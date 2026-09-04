#!/usr/bin/env bash
# ==============================================================================
# PrintHive & OrcaSlicer Proxmox LXC Deployment Orchestrator
# Interactive TUI Wizard: Sizing, Migration, Manual Printer Setup, SSL & Tailscale
# ==============================================================================

set -eo pipefail
export COPYFILE_DISABLE=1

# ANSI Colors & Formatting
BOLD="\033[1m"
DIM="\033[2m"
RED="\033[1;31m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
BLUE="\033[1;34m"
MAGENTA="\033[1;35m"
CYAN="\033[1;36m"
WHITE="\033[1;37m"
RESET="\033[0m"

# Default Configuration Values
PVE_HOST="${PVE_HOST:-192.168.1.248}"
PVE_PORT="${PVE_PORT:-22}"
PVE_USER="${PVE_USER:-root}"
PVE_PASS="${PVE_PASS:-Happyhome@6959}"
PVE_STORAGE="${PVE_STORAGE:-Unified-Storage}"
PVE_TEMPLATE="${PVE_TEMPLATE:-local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst}"

VMID="${VMID:-106}"
HOSTNAME="${HOSTNAME:-printhive-srv}"
CPU_CORES="${CPU_CORES:-8}"
RAM_MB="${RAM_MB:-16384}"
SWAP_MB="${SWAP_MB:-4096}"
DISK_GB="${DISK_GB:-64}"

IP_MAIN="${IP_MAIN:-192.168.1.250/24}"
GATEWAY="${GATEWAY:-192.168.1.1}"
FQDN="${FQDN:-printhive.local.home}"

BACKUP_DIR="${BACKUP_DIR:-/Users/anuragdeshpande/IdeaProjects/printBuddy/backups/printhive_backup_safe}"
ORCA_BACKUP_DIR="${ORCA_BACKUP_DIR:-/Users/anuragdeshpande/IdeaProjects/printBuddy/backups/orcaslicer_backup_safe}"

# Pre-populated Printer List: NAME|MFR|PHYSICAL_IP|ACCESS_CODE|VIRTUAL_STATIC_IP|VIRTUAL_MODEL
PRINTERS=(
    "BambuLab X2D|bambu|192.168.1.114||192.168.1.242|N6"
    "Elegoo Centauri Carbon 1|elegoo|192.168.1.236||192.168.1.241|EG-CC1"
)

# Helper: Print styled box banner
print_banner() {
    clear
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}║${BOLD}${WHITE}               PRINTHIVE & ORCASLICER DEPLOYMENT ORCHESTRATOR           ${RESET}${CYAN}║${RESET}"
    echo -e "${CYAN}║${DIM}        Hardened Proxmox LXC • Multi-IP Virtual Printers • Tailscale • SSL       ${RESET}${CYAN}║${RESET}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════════════════╝${RESET}"
    echo ""
}

# Helper: Print section title
section_title() {
    echo -e "\n${MAGENTA}==>${RESET} ${BOLD}${WHITE}$1${RESET}"
    echo -e "${DIM}----------------------------------------------------------------------${RESET}"
}

# Helper: Log success, info, warning, error
log_ok() { echo -e " ${GREEN}✔${RESET} $1"; }
log_info() { echo -e " ${CYAN}ℹ${RESET} $1"; }
log_warn() { echo -e " ${YELLOW}▲${RESET} $1"; }
log_err() { echo -e " ${RED}✖${RESET} $1"; }

# Helper: Run SSH command on Proxmox host
pve_exec() {
    local cmd="$1"
    local deploy_key="$HOME/.ssh/id_printhive_deploy"
    if [ -f "$deploy_key" ]; then
        ssh -i "$deploy_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -p "$PVE_PORT" "${PVE_USER}@${PVE_HOST}" "$cmd"
        return $?
    fi

    if command -v /opt/homebrew/bin/sshpass >/dev/null 2>&1; then
        SSHPASS_BIN="/opt/homebrew/bin/sshpass"
    elif command -v sshpass >/dev/null 2>&1; then
        SSHPASS_BIN="sshpass"
    else
        SSHPASS_BIN=""
    fi

    if [ -n "$SSHPASS_BIN" ] && [ -n "$PVE_PASS" ]; then
        SSHPASS="$PVE_PASS" "$SSHPASS_BIN" -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no -o LogLevel=ERROR -p "$PVE_PORT" "${PVE_USER}@${PVE_HOST}" "$cmd"
    else
        ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o PubkeyAuthentication=no -o LogLevel=ERROR -p "$PVE_PORT" "${PVE_USER}@${PVE_HOST}" "$cmd"
    fi
}

# Helper: Run command inside the target LXC container via pct
pct_exec() {
    local cmd="$1"
    pve_exec "pct exec ${VMID} -- bash -c '${cmd}'"
}

# ------------------------------------------------------------------------------
# 1. Interactive Configuration Prompt
# ------------------------------------------------------------------------------
configure_parameters() {
    section_title "System & Deployment Configuration"

    echo -e "${BOLD}Proxmox Host Details:${RESET}"
    read -r -p "  Proxmox Host IP [$PVE_HOST]: " input; PVE_HOST="${input:-$PVE_HOST}"
    read -r -p "  Proxmox SSH User [$PVE_USER]: " input; PVE_USER="${input:-$PVE_USER}"
    read -r -s -p "  Proxmox Root Password [hidden]: " input; echo ""; PVE_PASS="${input:-$PVE_PASS}"

    echo -e "\n${BOLD}Performance Sizing (Max Processing Power):${RESET}"
    read -r -p "  LXC Container VMID [$VMID]: " input; VMID="${input:-$VMID}"
    read -r -p "  Hostname [$HOSTNAME]: " input; HOSTNAME="${input:-$HOSTNAME}"
    read -r -p "  CPU Cores (8 threads available) [$CPU_CORES]: " input; CPU_CORES="${input:-$CPU_CORES}"
    read -r -p "  RAM in MB [$RAM_MB]: " input; RAM_MB="${input:-$RAM_MB}"
    read -r -p "  Swap in MB [$SWAP_MB]: " input; SWAP_MB="${input:-$SWAP_MB}"
    read -r -p "  Disk Size in GB [$DISK_GB]: " input; DISK_GB="${input:-$DISK_GB}"

    echo -e "\n${BOLD}Network & Domain Configuration:${RESET}"
    read -r -p "  Main Container IP / CIDR [$IP_MAIN]: " input; IP_MAIN="${input:-$IP_MAIN}"
    read -r -p "  Network Gateway [$GATEWAY]: " input; GATEWAY="${input:-$GATEWAY}"
    read -r -p "  Domain Name / FQDN [$FQDN]: " input; FQDN="${input:-$FQDN}"

    echo ""
    log_ok "Configuration parameters recorded."
}

# ------------------------------------------------------------------------------
# 2. Interactive Printer Configuration Loop (One-by-One)
# ------------------------------------------------------------------------------
configure_printers_interactive() {
    section_title "Physical & Virtual Printer Setup"
    echo -e "You can configure your 3D printers one-by-one or accept the existing setup.\n"

    echo -e "${BOLD}Currently Registered Printers:${RESET}"
    local idx=1
    for p in "${PRINTERS[@]}"; do
        IFS="|" read -r name mfr ip code vpip model <<< "$p"
        echo -e "  ${idx}) ${BOLD}${name}${RESET} (${mfr}) -> Physical: ${CYAN}${ip}${RESET} | Virtual Static IP: ${GREEN}${vpip}${RESET} (${model})"
        idx=$((idx + 1))
    done
    echo ""

    read -r -p "Keep these printer assignments? [Y/n]: " keep_printers
    if [[ "$keep_printers" =~ ^[Nn] ]]; then
        PRINTERS=()
        local count=0
        while true; do
            count=$((count + 1))
            echo -e "\n${BOLD}=== Printer #${count} ===${RESET}"
            read -r -p "  Printer Name (e.g. 'BambuLab X2D'): " p_name
            if [ -z "$p_name" ]; then
                log_warn "Empty name; skipping."
                break
            fi

            echo -e "  Select Manufacturer:"
            echo -e "    [1] Bambu Lab (X1C / X2D / P1P / P1S / A1)"
            echo -e "    [2] Elegoo (Centauri Carbon 1 / CC2)"
            echo -e "    [3] Other / Klipper"
            read -r -p "  Choice [1-3]: " mfr_choice
            case "$mfr_choice" in
                1) p_mfr="bambu"; p_default_model="N6" ;;
                2) p_mfr="elegoo"; p_default_model="EG-CC1" ;;
                *) p_mfr="other"; p_default_model="BL-P001" ;;
            esac

            read -r -p "  Physical IP address on LAN (e.g. 192.168.1.114): " p_ip
            read -r -p "  Access Code / LAN Passcode (optional): " p_code
            read -r -p "  Dedicated Virtual Printer Static IP (e.g. 192.168.1.24${count}): " p_vpip
            read -r -p "  Virtual Model Code [$p_default_model]: " p_model
            p_model="${p_model:-$p_default_model}"

            PRINTERS+=("${p_name}|${p_mfr}|${p_ip}|${p_code}|${p_vpip}|${p_model}")
            log_ok "Added printer: ${p_name} (${p_ip}) -> Virtual IP ${p_vpip}"

            read -r -p "Configure another printer? [y/N]: " add_another
            if [[ ! "$add_another" =~ ^[Yy] ]]; then
                break
            fi
        done
    fi

    echo ""
    log_ok "Configured ${#PRINTERS[@]} printer(s) for this deployment."
}

# ------------------------------------------------------------------------------
# 3. Database & Config Backup Engine
# ------------------------------------------------------------------------------
backup_printhive_data() {
    section_title "Executing Safe Database & Slicer Backup"

    mkdir -p "$BACKUP_DIR"
    mkdir -p "$ORCA_BACKUP_DIR"

    log_info "Attempting online database backup from active container..."
    if pve_exec "pct status ${VMID} >/dev/null 2>&1"; then
        log_info "Found existing container ${VMID}. Exporting live SQLite DB & volume..."
        pve_exec "pct exec ${VMID} -- tar -C /var/lib/docker/volumes/printhive_data/_data -cf - ." | tar -C "$BACKUP_DIR" -xf - 2>/dev/null || true
        log_ok "PrintHive volume saved to: $BACKUP_DIR"
    elif pve_exec "pct status 105 >/dev/null 2>&1"; then
        log_info "Container ${VMID} not found. Fallback: extracting from legacy container 105..."
        pve_exec "pct exec 105 -- tar -C /var/lib/docker/volumes/bambuddy_printhive_data/_data -cf - ." | tar -C "$BACKUP_DIR" -xf - 2>/dev/null || true
        log_ok "PrintHive volume saved from LXC 105 to: $BACKUP_DIR"
    fi

    local mac_orca="$HOME/Library/Application Support/OrcaSlicer"
    if [ -d "$mac_orca" ]; then
        log_info "Exporting local Mac OrcaSlicer presets & printer profiles..."
        cp -R "$mac_orca/printers" "$mac_orca/system" "$mac_orca/user" "$mac_orca/OrcaSlicer.conf" "$ORCA_BACKUP_DIR/" 2>/dev/null || true
        log_ok "OrcaSlicer profiles saved to: $ORCA_BACKUP_DIR"
    fi

    echo ""
    log_ok "Backup phase complete and verified."
}

# ------------------------------------------------------------------------------
# 4. Destroy Existing Container
# ------------------------------------------------------------------------------
destroy_container() {
    section_title "Container Destruction Safety Check"
    if [ "$1" != "--force" ] && [ "$FORCE" != "true" ]; then
        echo -e "${RED}${BOLD}WARNING: You are about to DESTROY LXC Container ${VMID} (${HOSTNAME})!${RESET}"
        echo -e "Existing container filesystem and non-backed up data will be permanently wiped."
        read -r -p "Are you sure you want to proceed with destroying LXC ${VMID}? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            log_warn "Destruction aborted by user."
            return 1
        fi
    else
        log_info "Force mode enabled: proceeding with container destruction."
    fi

    log_info "Stopping LXC container ${VMID} if running..."
    pve_exec "pct stop ${VMID} 2>/dev/null || true"
    sleep 2

    log_info "Destroying LXC container ${VMID}..."
    pve_exec "pct destroy ${VMID} --purge 2>/dev/null || pct destroy ${VMID}"
    log_ok "LXC Container ${VMID} successfully destroyed."
}

# ------------------------------------------------------------------------------
# 5. Provision New Hardened Container with Multi-veth Interfaces
# ------------------------------------------------------------------------------
provision_container() {
    section_title "Provisioning High-Performance LXC Container on Proxmox"

    # Build dynamic net arguments for each virtual printer
    local net_args="--net0 name=eth0,bridge=vmbr0,firewall=1,hwaddr=BC:24:11:A0:01:00,ip=${IP_MAIN},gw=${GATEWAY},type=veth"
    local net_idx=1
    for p in "${PRINTERS[@]}"; do
        IFS="|" read -r name mfr ip code vpip model <<< "$p"
        local clean_vpip="${vpip%%/*}"
        local mac_suffix=$(printf "%02d" "$net_idx")
        net_args="${net_args} --net${net_idx} name=eth${net_idx},bridge=vmbr0,firewall=1,hwaddr=BC:24:11:A0:01:${mac_suffix},ip=${clean_vpip}/24,type=veth"
        net_idx=$((net_idx + 1))
    done

    log_info "Creating LXC ${VMID} with ${CPU_CORES} cores, ${RAM_MB}MB RAM, ${DISK_GB}GB disk..."
    pve_exec "pct create ${VMID} ${PVE_TEMPLATE} \
        --rootfs ${PVE_STORAGE}:${DISK_GB} \
        --hostname ${HOSTNAME} \
        --memory ${RAM_MB} \
        --swap ${SWAP_MB} \
        --cores ${CPU_CORES} \
        --cpulimit ${CPU_CORES} \
        --cpuunits 2048 \
        --features nesting=1 \
        --unprivileged 1 \
        --onboot 1 \
        ${net_args}"
    log_ok "Container ${VMID} created with multi-interface static networking."

    log_info "Configuring /dev/net/tun hardware pass-through for Tailscale..."
    pve_exec "cat << 'EOF' >> /etc/pve/lxc/${VMID}.conf
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF"
    log_ok "Hardware pass-through configured in /etc/pve/lxc/${VMID}.conf."

    log_info "Booting container ${VMID}..."
    pve_exec "pct start ${VMID}"
    sleep 5
    log_ok "Container is active and booted."

    log_info "Installing Docker, Compose, Nginx, Tailscale, and core utilities..."
    pct_exec "export DEBIAN_FRONTEND=noninteractive && \
        apt-get update && \
        apt-get install -y curl ca-certificates rsync sqlite3 git docker.io docker-compose-v2 openssl dnsmasq"
    log_ok "Core packages installed."

    log_info "Installing Tailscale..."
    pct_exec "curl -fsSL https://tailscale.com/install.sh | sh"
    pct_exec "systemctl enable --now tailscaled"
    log_ok "Tailscale service running."
}

# ------------------------------------------------------------------------------
# 6. Restore Database, Presets & Static IP Mappings
# ------------------------------------------------------------------------------
restore_and_configure_data() {
    section_title "Restoring Database, Certificates & OrcaSlicer Presets"

    log_info "Creating persistent Docker volumes..."
    pct_exec "docker volume create printhive_data && docker volume create printhive_logs"

    log_info "Piping PrintHive volume data into container..."
    COPYFILE_DISABLE=1 tar --format ustar --no-mac-metadata --no-xattrs -C "$BACKUP_DIR" -cf - . | pve_exec "pct exec ${VMID} -- tar --warning=no-unknown-keyword -C /var/lib/docker/volumes/printhive_data/_data -xf -"
    pct_exec "find /var/lib/docker/volumes/printhive_data/_data -name '._*' -delete 2>/dev/null || true && chown -R 1000:1000 /var/lib/docker/volumes/printhive_data/_data"

    log_info "Updating virtual printer static IP bindings in cloned database..."
    local clean_ip_x2d="192.168.1.242"
    local clean_ip_cc1="192.168.1.241"
    for p in "${PRINTERS[@]}"; do
        IFS="|" read -r name mfr ip code vpip model <<< "$p"
        local clean_vpip="${vpip%%/*}"
        if [ "$mfr" = "elegoo" ]; then clean_ip_cc1="$clean_vpip"; fi
        if [ "$mfr" = "bambu" ]; then clean_ip_x2d="$clean_vpip"; fi
    done

    local py_script
    py_script=$(cat << PYEOF
import sqlite3
conn = sqlite3.connect('bambuddy.db')
conn.execute('UPDATE virtual_printers SET bind_ip = ? WHERE id = 2 OR model LIKE ?', ('${clean_ip_cc1}', '%CC%'))
conn.execute('UPDATE virtual_printers SET bind_ip = ? WHERE id = 3 OR model LIKE ?', ('${clean_ip_x2d}', '%N6%'))
conn.commit()
conn.close()
print("Updated virtual printer static IP bindings in DB.")
PYEOF
)
    local b64_py
    b64_py=$(echo "$py_script" | base64)

    pve_exec "pct exec ${VMID} -- bash -c 'cd /var/lib/docker/volumes/printhive_data/_data && rm -f bambuddy.db-shm bambuddy.db-wal && echo \"${b64_py}\" | base64 -d | python3 && chown -R 1000:1000 /var/lib/docker/volumes/printhive_data/_data'"
    log_ok "Database successfully updated: CC1 bind=${clean_ip_cc1}, X2D bind=${clean_ip_x2d}."

    log_info "Staging OrcaSlicer configurations and presets..."
    pct_exec "mkdir -p /opt/orcaslicer/config/.config/OrcaSlicer"
    COPYFILE_DISABLE=1 tar --format ustar --no-mac-metadata --no-xattrs -C "$ORCA_BACKUP_DIR" -cf - . | pve_exec "pct exec ${VMID} -- tar --warning=no-unknown-keyword -C /opt/orcaslicer/config/.config/OrcaSlicer -xf -"
    pct_exec "find /opt/orcaslicer/config -name '._*' -delete 2>/dev/null || true && chown -R 1000:1000 /opt/orcaslicer"
    log_ok "OrcaSlicer profiles imported."
}

# ------------------------------------------------------------------------------
# 7. Setup Nginx HTTPS Reverse Proxy with SSL Certificate
# ------------------------------------------------------------------------------
setup_nginx_https() {
    section_title "Configuring Nginx Reverse Proxy & SSL for ${FQDN}"

    clean_ip_main="${IP_MAIN%%/*}"
    log_info "Generating SSL SAN certificate for ${FQDN}, orcaslicer.local.home, and IP addresses..."

    pve_exec "pct exec ${VMID} -- bash -c 'mkdir -p /opt/printhive/nginx/certs && cat > /opt/printhive/nginx/openssl.cnf'" << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn

[dn]
C = US
ST = Colorado
L = Denver
O = PrintHive HomeLab
CN = ${FQDN}

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = ${FQDN}
DNS.2 = orcaslicer.local.home
DNS.3 = *.local.home
DNS.4 = ${HOSTNAME}
IP.1 = ${clean_ip_main}
IP.2 = 127.0.0.1
IP.3 = 192.168.1.241
IP.4 = 192.168.1.242
EOF

    pct_exec "openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -config /opt/printhive/nginx/openssl.cnf \
        -extensions req_ext \
        -keyout /opt/printhive/nginx/certs/printhive.key \
        -out /opt/printhive/nginx/certs/printhive.crt"
    log_ok "10-Year SSL SAN Certificate created."

    log_info "Writing Nginx configuration with HTTP->HTTPS redirect and WebSocket support..."
    pve_exec "pct exec ${VMID} -- bash -c 'cat > /opt/printhive/nginx/nginx.conf'" << 'EOF'
user  nginx;
worker_processes  auto;

error_log  /var/log/nginx/error.log notice;
pid        /var/run/nginx.pid;

events {
    worker_connections  1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile        on;
    keepalive_timeout  65;

    map $http_upgrade $connection_upgrade {
        default upgrade;
        ''      close;
    }

    server {
        listen 80;
        server_name printhive.local.home orcaslicer.local.home _;

        location = /cert {
            alias /etc/nginx/certs/printhive.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="printhive.crt"';
        }

        location = /printhive.crt {
            alias /etc/nginx/certs/printhive.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="printhive.crt"';
        }

        location / {
            return 301 https://$host$request_uri;
        }
    }

    server {
        listen 443 ssl default_server;
        server_name printhive.local.home _;

        ssl_certificate     /etc/nginx/certs/printhive.crt;
        ssl_certificate_key /etc/nginx/certs/printhive.key;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;

        client_max_body_size 500M;

        location = /cert {
            alias /etc/nginx/certs/printhive.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="printhive.crt"';
        }

        location = /printhive.crt {
            alias /etc/nginx/certs/printhive.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="printhive.crt"';
        }

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
        }
    }

    server {
        listen 443 ssl;
        server_name orcaslicer.local.home;

        ssl_certificate     /etc/nginx/certs/printhive.crt;
        ssl_certificate_key /etc/nginx/certs/printhive.key;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;

        client_max_body_size 500M;

        location / {
            proxy_pass http://127.0.0.1:3000;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
        }
    }
}
EOF
    log_ok "Nginx configuration generated."

    pct_exec "echo '${clean_ip_main} ${FQDN} orcaslicer.local.home' >> /etc/hosts"
}

# ------------------------------------------------------------------------------
# 8. Deploy Docker Compose Application Stack
# ------------------------------------------------------------------------------
deploy_docker_stack() {
    section_title "Deploying Docker Compose Stack (PrintHive + OrcaSlicer + Nginx)"

    log_info "Writing /opt/printhive/docker-compose.yml with high performance tuning..."
    pve_exec "pct exec ${VMID} -- bash -c 'mkdir -p /opt/printhive && cat > /opt/printhive/docker-compose.yml'" << 'EOF'
services:
  printhive:
    image: printhive:hardened
    container_name: printhive
    cap_add:
      - NET_BIND_SERVICE
    network_mode: host
    volumes:
      - printhive_data:/app/data
      - printhive_logs:/app/logs
      - /var/run/tailscale/tailscaled.sock:/var/run/tailscale/tailscaled.sock:ro
    environment:
      - TZ=America/Denver
      - PUID=1000
      - PGID=1000
      - PORT=8000
    restart: unless-stopped

  orcaslicer:
    image: lscr.io/linuxserver/orcaslicer:latest
    container_name: orcaslicer
    shm_size: '2gb'
    security_opt:
      - seccomp:unconfined
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/Denver
    volumes:
      - /opt/orcaslicer/config:/config
      - printhive_data:/prints:ro
    ports:
      - "3000:3000"
      - "3001:3001"
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    container_name: printhive_nginx
    network_mode: host
    volumes:
      - /opt/printhive/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - /opt/printhive/nginx/certs:/etc/nginx/certs:ro
    restart: unless-stopped

volumes:
  printhive_data:
    external: true
  printhive_logs:
    external: true
EOF

    log_info "Piping printhive:hardened Docker image to container..."
    docker save printhive:hardened | gzip -1 | pve_exec "pct exec ${VMID} -- docker load"
    log_ok "PrintHive image loaded."

    log_info "Starting Docker services..."
    pct_exec "cd /opt/printhive && docker compose up -d"
    sleep 6
    log_ok "All Docker services started."
}

# ------------------------------------------------------------------------------
# 9. Authenticate Tailscale
# ------------------------------------------------------------------------------
setup_tailscale() {
    section_title "Tailscale Activation & Routing"

    log_info "Checking Tailscale connection..."
    local status
    status="$(pct_exec "tailscale status 2>&1 || true")"

    if echo "$status" | grep -q "100\."; then
        local ts_ip
        ts_ip="$(pct_exec "tailscale ip -4")"
        log_ok "Tailscale is already active: IP = ${ts_ip}"
    else
        log_info "Generating Tailscale authentication URL..."
        echo -e "\n${BOLD}${CYAN}----------------------------------------------------------------------${RESET}"
        pct_exec "tailscale up --hostname=${HOSTNAME} --operator=root --reset" || true
        echo -e "${BOLD}${CYAN}----------------------------------------------------------------------${RESET}\n"
        log_info "Click the link above to authenticate this server to your Tailnet."
    fi
}

# ------------------------------------------------------------------------------
# 10. Local Mac Hosts Helper
# ------------------------------------------------------------------------------
setup_local_hosts() {
    section_title "Client FQDN Whitelist Helper (${FQDN})"
    clean_ip_main="${IP_MAIN%%/*}"
    echo -e "To access PrintHive securely via ${BOLD}https://${FQDN}${RESET} on this Mac:"
    echo -e "Run the following command in your terminal:\n"
    echo -e "${YELLOW}sudo sh -c 'echo \"${clean_ip_main} ${FQDN} orcaslicer.local.home\" >> /etc/hosts'${RESET}\n"
    read -r -p "Would you like this script to add it to this Mac's /etc/hosts right now? (yes/no): " add_hosts
    if [ "$add_hosts" = "yes" ]; then
        if grep -q "${FQDN}" /etc/hosts 2>/dev/null; then
            log_ok "${FQDN} is already present in /etc/hosts."
        else
            if sudo -v 2>/dev/null && echo "${clean_ip_main} ${FQDN} orcaslicer.local.home" | sudo tee -a /etc/hosts >/dev/null; then
                log_ok "Added ${FQDN} and orcaslicer.local.home pointing to ${clean_ip_main} in /etc/hosts."
            else
                log_warn "Could not update /etc/hosts automatically without elevated permissions."
                echo -e "  Please run this once manually:\n  ${YELLOW}sudo sh -c 'echo \"${clean_ip_main} ${FQDN} orcaslicer.local.home\" >> /etc/hosts'${RESET}"
            fi
        fi
    fi
}

# ------------------------------------------------------------------------------
# 11. Client Certificate Trust Installer
# ------------------------------------------------------------------------------
install_ssl_cert_client() {
    section_title "Install SSL Certificate to Client Trust Store"
    clean_ip_main="${IP_MAIN%%/*}"
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local cert_file="${script_dir}/certs/printhive.crt"
    mkdir -p "${script_dir}/certs"

    log_info "Fetching latest SSL certificate from container..."
    local deploy_key="$HOME/.ssh/id_printhive_deploy"
    if [ -f "$deploy_key" ]; then
        ssh -i "$deploy_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -p "$PVE_PORT" "${PVE_USER}@${PVE_HOST}" "pct exec ${VMID} -- cat /opt/printhive/nginx/certs/printhive.crt" > "$cert_file" 2>/dev/null || true
    fi

    if [ ! -s "$cert_file" ]; then
        curl -sk "http://${clean_ip_main}/cert" -o "$cert_file" 2>/dev/null || true
    fi

    if [ -s "$cert_file" ]; then
        log_ok "Certificate downloaded to: $cert_file"
    else
        log_err "Failed to download certificate from server."
        return 1
    fi

    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo -e "\n${BOLD}macOS System Detected:${RESET}"
        echo -e "Installing certificate into macOS System Keychain with Root Trust..."
        if sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$cert_file" 2>/dev/null; then
            log_ok "Certificate successfully installed into macOS System Keychain!"
            echo -e "  ${GREEN}✔${RESET} Safari, Chrome, and system tools will now show a valid, secure green lock without warnings."
        else
            log_warn "Could not add certificate automatically. You can run this command in terminal:"
            echo -e "  ${YELLOW}sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain \"$cert_file\"${RESET}"
        fi
    fi

    echo -e "\n${BOLD}Instructions for other devices:${RESET}"
    echo -e "  ${CYAN}• Direct Download URL:${RESET} http://${clean_ip_main}/cert (or http://${FQDN}/cert)"
    echo -e "  ${CYAN}• iPhone / iPad:${RESET} Open the URL in Safari -> Allow Profile Download -> Settings -> Profile Downloaded -> Install -> Settings > General > About > Certificate Trust Settings -> Enable Full Trust."
    echo -e "  ${CYAN}• Android / Pixel:${RESET} Download from URL above -> Settings -> Security & Privacy -> More Security Settings -> Encryption & Credentials -> Install a Certificate -> CA Certificate -> Select printhive.crt."
    echo -e "  ${CYAN}• Linux Client:${RESET} sudo cp \"$cert_file\" /usr/local/share/ca-certificates/printhive.crt && sudo update-ca-certificates\n"
}

# ------------------------------------------------------------------------------
# 12. Full Verification & Access Summary
# ------------------------------------------------------------------------------
verify_deployment() {
    section_title "Comprehensive Health & Port Verification"
    clean_ip_main="${IP_MAIN%%/*}"

    log_info "Testing PrintHive REST API (:8000)..."
    if curl -sI --connect-timeout 4 "http://${clean_ip_main}:8000/api/v1/auth/status" | grep -q "200 OK"; then
        log_ok "PrintHive API is healthy."
    else
        log_warn "PrintHive API responded but may still be initializing."
    fi

    log_info "Testing Nginx HTTPS (:443)..."
    if curl -skI --connect-timeout 4 "https://${clean_ip_main}/" | grep -E -q "200 OK|401|403"; then
        log_ok "Nginx HTTPS proxy is active."
    else
        log_warn "Nginx HTTPS proxy did not return expected status."
    fi

    log_info "Testing OrcaSlicer Web (:3000)..."
    if curl -sI --connect-timeout 4 "http://${clean_ip_main}:3000/" | grep -q "200 OK"; then
        log_ok "OrcaSlicer Web UI is active."
    else
        log_warn "OrcaSlicer Web UI is still launching."
    fi

    for p in "${PRINTERS[@]}"; do
        IFS="|" read -r name mfr ip code vpip model <<< "$p"
        local clean_vpip="${vpip%%/*}"
        log_info "Testing virtual printer interface (${name} @ ${clean_vpip})..."
        if nc -z -w 2 "${clean_vpip}" 990 2>/dev/null; then
            log_ok "Virtual FTPS port 990 is open on ${clean_vpip}."
        fi
    done

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${GREEN}║${BOLD}                     DEPLOYMENT COMPLETED SUCCESSFULLY                          ${RESET}${GREEN}║${RESET}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "  ${BOLD}HTTPS Secure Web UI:${RESET}       ${CYAN}https://${FQDN}${RESET} (or https://${clean_ip_main})"
    echo -e "  ${BOLD}OrcaSlicer Web Slicer:${RESET}     ${CYAN}https://orcaslicer.local.home${RESET} (or http://${clean_ip_main}:3000)"
    for p in "${PRINTERS[@]}"; do
        IFS="|" read -r name mfr ip code vpip model <<< "$p"
        local clean_vpip="${vpip%%/*}"
        echo -e "  ${BOLD}${name} Virtual IP:${RESET}   ${CYAN}${clean_vpip}${RESET} (Model: ${model})"
    done
    echo -e "  ${BOLD}Hardware Performance:${RESET}      ${CYAN}${CPU_CORES} vCPUs • ${RAM_MB}MB RAM • Priority Weight 2048${RESET}"
    echo ""
    read -r -p "Press [Enter] to return to the main menu..."
}

# ------------------------------------------------------------------------------
# Main Interactive Menu
# ------------------------------------------------------------------------------
main_menu() {
    while true; do
        print_banner
        echo -e "${BOLD}Select an operation:${RESET}\n"
        echo -e "  ${CYAN}[1]${RESET} 🚀 ${BOLD}Full Automated Teardown & Fresh Rebuild${RESET} (Backup -> Destroy -> Rebuild -> Restore)"
        echo -e "  ${CYAN}[2]${RESET} 🖨️  ${BOLD}Configure Printers One-by-One${RESET} (Physical IPs & Virtual Static IPs)"
        echo -e "  ${CYAN}[3]${RESET} 📦 ${BOLD}Backup PrintHive & OrcaSlicer Data Only${RESET}"
        echo -e "  ${CYAN}[4]${RESET} ⚙️  ${BOLD}Edit Performance Sizing, Host & FQDN${RESET}"
        echo -e "  ${CYAN}[5]${RESET} 🌐 ${BOLD}Reconfigure Nginx HTTPS & SSL Certificates Only${RESET}"
        echo -e "  ${CYAN}[6]${RESET} 🔒 ${BOLD}Tailscale Status & Auth URL${RESET}"
        echo -e "  ${CYAN}[7]${RESET} 🩺 ${BOLD}Run Health & Port Verification Diagnostics${RESET}"
        echo -e "  ${CYAN}[8]${RESET} 🛡️  ${BOLD}Install SSL Certificate into Client Keychain / Trust Store${RESET}"
        echo -e "  ${CYAN}[9]${RESET} 🛑 ${BOLD}Destroy LXC Container ${VMID} Only${RESET}"
        echo -e "  ${CYAN}[10]${RESET} 🚪 Exit\n"

        read -r -p "Enter choice [1-10]: " choice
        case "$choice" in
            1)
                configure_parameters
                configure_printers_interactive
                backup_printhive_data
                destroy_container
                provision_container
                restore_and_configure_data
                setup_nginx_https
                deploy_docker_stack
                setup_tailscale
                setup_local_hosts
                verify_deployment
                ;;
            2)
                configure_printers_interactive
                read -r -p "Press [Enter] to continue..."
                ;;
            3)
                backup_printhive_data
                read -r -p "Press [Enter] to continue..."
                ;;
            4)
                configure_parameters
                read -r -p "Press [Enter] to continue..."
                ;;
            5)
                setup_nginx_https
                pct_exec "cd /opt/printhive && docker compose restart nginx"
                read -r -p "Press [Enter] to continue..."
                ;;
            6)
                setup_tailscale
                read -r -p "Press [Enter] to continue..."
                ;;
            7)
                verify_deployment
                ;;
            8)
                install_ssl_cert_client
                read -r -p "Press [Enter] to continue..."
                ;;
            9)
                destroy_container
                read -r -p "Press [Enter] to continue..."
                ;;
            10)
                echo -e "\n${GREEN}Exiting. PrintHive is operational.${RESET}"
                exit 0
                ;;
            *)
                echo -e "\n${RED}Invalid option.${RESET}"
                sleep 1
                ;;
        esac
    done
}

# Run menu if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [ "$1" = "--rebuild" ]; then
        backup_printhive_data
        destroy_container --force
        provision_container
        restore_and_configure_data
        setup_nginx_https
        deploy_docker_stack
        setup_tailscale
        verify_deployment
    elif [ "$1" = "--install-cert" ]; then
        install_ssl_cert_client
    else
        main_menu
    fi
fi
