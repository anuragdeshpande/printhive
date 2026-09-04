# PrintHive & OrcaSlicer: Proxmox LXC Zero-to-Production Deployment Guide

## 1. Architectural Overview

This system provisions a dedicated, high-performance homelab printing and slicing server running on **Proxmox VE (Debian bookworm)**. It unifies multi-manufacturer 3D printer fleet management (Bambu Lab and Elegoo) and remote slicing into a single resilient infrastructure.

```
                              ┌─────────────────────────────────────────────────────────┐
                              │                    Proxmox VE Host                      │
                              │                    (192.168.1.248)                      │
                              └───────────────────────────┬─────────────────────────────┘
                                                          │
                                         ┌────────────────┴───────────────┐
                                         │  LXC Container 106             │
                                         │  (8 vCPUs, 16GB RAM, Nesting)  │
                                         └────────────────┬───────────────┘
                                                          │
          ┌───────────────────────────────────────────────┼───────────────────────────────────────────────┐
          │                                               │                                               │
   [eth0: 192.168.1.250]                           [eth1: 192.168.1.241]                           [eth2: 192.168.1.242]
   Main Dashboard & Slicer                         Virtual Elegoo Centauri Carbon 1                Virtual BambuLab X2D
          │                                               │                                               │
  ┌───────┴────────────────────────┐              ┌───────┴────────────────────────┐              ┌───────┴────────────────────────┐
  │ Nginx Reverse Proxy (SSL 443)  │              │ SDCP WS Server (Port 3030)     │              │ Implicit FTPS (Port 990)       │
  │ • printhive.local.home         │              │ SSDP Discovery (Port 2021)     │              │ MQTT Bridge (Port 8883)        │
  │ • orcaslicer.local.home        │              │ Auto-Dispatch to Physical CC1  │              │ Camera-322 Proxy (Port 322)    │
  │ Containerized OrcaSlicer (3000)│              │ (192.168.1.236)                │              │ Auto-Dispatch to Physical X2D  │
  │ PrintHive Backend (Port 8000)  │              └────────────────────────────────┘              │ (192.168.1.114)                │
  │ Tailscale (100.65.78.92)       │                                                              └────────────────────────────────┘
  └────────────────────────────────┘
```

### Key Highlights
1. **Multi-IP Dedicated Static Interfaces**: Distinct virtual ethernet adapters (`eth0`, `eth1`, `eth2`) with unique MAC addresses prevent port collisions on ports 990 (FTPS), 8883 (MQTT), 3030 (SDCP), and 1900/2021 (SSDP).
2. **Dedicated Tailscale Hardware Pass-through**: `/dev/net/tun` is mounted into the unprivileged LXC container so Tailscale connects natively without userspace performance penalties.
3. **Hardened Nginx SSL Termination**: A 10-year Subject Alternative Name (SAN) certificate covers `printhive.local.home`, `orcaslicer.local.home`, LAN IPs, and the assigned Tailscale IP (`100.65.78.92`).
4. **Universal Client Trust**: Nginx serves `/cert` and `/printhive.crt` over plain HTTP (port 80) and HTTPS (port 443) so macOS, iOS/iPadOS, and Android clients can install Root CA trust with one click.
5. **Headless & Interactive Orchestrator**: [`deploy/printhive_orchestrator.sh`](file:///Users/anuragdeshpande/IdeaProjects/printBuddy/deploy/printhive_orchestrator.sh) can perform complete teardowns, fresh rebuilds, backups, and restores either via an interactive TUI or unattended CLI flags.

---

## 2. Hardware & Network Prerequisites

* **Proxmox VE Host**: Proxmox 8.x+ on `192.168.1.248`.
* **Proxmox Storage Pool**: `Unified-Storage` (or `local-lvm` / `local-zfs`).
* **Root Access**: SSH access as `root`.
* **Network Gateway**: Google Fiber Network Box / router at `192.168.1.1`.
* **Allocated Static IPs & MAC Assignments**:
  - `eth0`: `192.168.1.250/24` (MAC: `BC:24:11:A0:01:00`) $\rightarrow$ Primary Server & OrcaSlicer
  - `eth1`: `192.168.1.241/24` (MAC: `BC:24:11:A0:01:01`) $\rightarrow$ Virtual Elegoo CC1
  - `eth2`: `192.168.1.242/24` (MAC: `BC:24:11:A0:01:02`) $\rightarrow$ Virtual Bambu X2D
* **Physical Printer Targets**:
  - Elegoo Centauri Carbon 1: `192.168.1.236`
  - Bambu Lab X2D: `192.168.1.114`

---

## 3. Quickstart: Automated Setup via Orchestrator

The fastest way to deploy the entire stack from scratch or rebuild an existing setup is using the orchestrator script on your macOS client:

```bash
# Make executable
chmod +x deploy/printhive_orchestrator.sh

# Option A: Run interactive TUI menu
./deploy/printhive_orchestrator.sh

# Option B: Run unattended end-to-end fresh rebuild
./deploy/printhive_orchestrator.sh --rebuild

# Option C: Install SSL certificate into local macOS System Keychain
./deploy/printhive_orchestrator.sh --install-cert
```

---

## 4. Complete Setup from Scratch (Manual Step-by-Step)

If setting up on a brand new Proxmox server with no prior configuration, follow these steps:

### Step 1: Proxmox Passwordless SSH Deployment Key

On your management Mac/workstation, generate and install a dedicated deployment key:

```bash
# Generate dedicated deploy key (empty passphrase for automation)
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_printhive_deploy -C "printhive-deploy"

# Copy key to Proxmox host
ssh-copy-id -i ~/.ssh/id_printhive_deploy.pub root@192.168.1.248
```

Verify you can connect without passwords:
```bash
ssh -i ~/.ssh/id_printhive_deploy -o IdentitiesOnly=yes root@192.168.1.248 "pveversion"
```

---

### Step 2: Provision LXC Container 106

Run this command on the Proxmox host to create container `106` with maximum processing power (8 cores, 16GB RAM, 2048 CPU units):

```bash
pct create 106 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
    --hostname printhive-srv \
    --cores 8 \
    --cpuunits 2048 \
    --memory 16384 \
    --swap 4096 \
    --rootfs Unified-Storage:64 \
    --features nesting=1,keyctl=1 \
    --unprivileged 1 \
    --onboot 1 \
    --ostype ubuntu \
    --net0 name=eth0,bridge=vmbr0,firewall=0,hwaddr=BC:24:11:A0:01:00,ip=192.168.1.250/24,gw=192.168.1.1 \
    --net1 name=eth1,bridge=vmbr0,firewall=0,hwaddr=BC:24:11:A0:01:01,ip=192.168.1.241/24 \
    --net2 name=eth2,bridge=vmbr0,firewall=0,hwaddr=BC:24:11:A0:01:02,ip=192.168.1.242/24 \
    --nameserver "1.1.1.1 8.8.8.8"
```

---

### Step 3: Configure Hardware Pass-Through for Tailscale

Tailscale in an unprivileged container requires `/dev/net/tun`. Append the device mapping to `/etc/pve/lxc/106.conf` on the Proxmox host:

```bash
cat >> /etc/pve/lxc/106.conf << 'EOF'
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF
```

Start the container:
```bash
pct start 106
sleep 5
```

---

### Step 4: Install Docker Engine, Tailscale, and Utilities

Execute inside container `106`:

```bash
pct exec 106 -- bash -c '
set -e
apt-get update && apt-get install -y curl wget git ca-certificates openssl iproute2 net-tools nano jq python3 python3-pip

# Install official Docker Engine + Docker Compose v2
curl -fsSL https://get.docker.com | sh

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled

# Setup deployment directories
mkdir -p /opt/printhive/nginx/certs
mkdir -p /opt/orcaslicer/config/.config/OrcaSlicer
'
```

---

### Step 5: Build & Stream Hardened PrintHive Docker Image

On your development Mac, build the `linux/amd64` container image using Docker/OrbStack and pipe it into container `106`:

```bash
# Build multi-architecture amd64 image
docker buildx build --platform linux/amd64 -t printhive:hardened .

# Stream compressed image directly into container Docker daemon
docker save printhive:hardened | gzip -1 | ssh -i ~/.ssh/id_printhive_deploy -o IdentitiesOnly=yes root@192.168.1.248 "pct exec 106 -- docker load"
```

---

### Step 6: Initialize Persistent Storage & Database

Inside container `106`, create the Docker named volumes:

```bash
pct exec 106 -- docker volume create printhive_data
pct exec 106 -- docker volume create printhive_logs
```

#### Option A: Fresh Initialization
If starting with zero existing database, PrintHive will automatically create the tables on first boot. You can then add the printers and virtual printers via the Web UI at `https://printhive.local.home`.

#### Option B: Restore from Existing Backup
To restore print histories, plates, and virtual printer configurations without macOS tar metadata warnings:

```bash
# 1. Pipe backup safely using POSIX ustar format and stripping macOS xattrs
COPYFILE_DISABLE=1 tar --format ustar --no-mac-metadata --no-xattrs -C backups/printhive_backup_safe -cf - . | \
    ssh -i ~/.ssh/id_printhive_deploy root@192.168.1.248 "pct exec 106 -- tar --warning=no-unknown-keyword -C /var/lib/docker/volumes/printhive_data/_data -xf -"

# 2. Clean up any AppleDouble metadata and fix permissions to container user (UID 1000)
pct exec 106 -- bash -c '
    find /var/lib/docker/volumes/printhive_data/_data -name "._*" -delete 2>/dev/null || true
    chown -R 1000:1000 /var/lib/docker/volumes/printhive_data/_data
'

# 3. Import Mac OrcaSlicer slicing presets
COPYFILE_DISABLE=1 tar --format ustar --no-mac-metadata --no-xattrs -C backups/orcaslicer_backup_safe -cf - . | \
    ssh -i ~/.ssh/id_printhive_deploy root@192.168.1.248 "pct exec 106 -- tar --warning=no-unknown-keyword -C /opt/orcaslicer/config/.config/OrcaSlicer -xf -"

pct exec 106 -- bash -c '
    find /opt/orcaslicer -name "._*" -delete 2>/dev/null || true
    chown -R 1000:1000 /opt/orcaslicer
'
```

---

### Step 7: Issue 10-Year SSL SAN Certificate

Generate a self-signed SAN certificate covering all local FQDNs, LAN IPs, and Tailscale endpoints:

```bash
pct exec 106 -- bash -c '
cat > /opt/printhive/nginx/openssl.cnf << "EOF"
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
CN = printhive.local.home

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = printhive.local.home
DNS.2 = orcaslicer.local.home
DNS.3 = *.local.home
DNS.4 = printhive-srv
DNS.5 = printhive-srv-1
IP.1 = 192.168.1.250
IP.2 = 127.0.0.1
IP.3 = 192.168.1.241
IP.4 = 192.168.1.242
IP.5 = 100.65.78.92
EOF

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -config /opt/printhive/nginx/openssl.cnf \
    -extensions req_ext \
    -keyout /opt/printhive/nginx/certs/printhive.key \
    -out /opt/printhive/nginx/certs/printhive.crt
'
```

---

### Step 8: Configure Nginx Reverse Proxy with `/cert` Export

Create `/opt/printhive/nginx/nginx.conf` inside container `106`:

```nginx
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

    # Port 80: Download certificate or redirect to HTTPS
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

    # Port 443: PrintHive Dashboard & API
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

    # Port 443: Containerized OrcaSlicer Web UI
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
```

---

### Step 9: Launch Docker Compose Stack

Write `/opt/printhive/docker-compose.yml` inside container `106`:

```yaml
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
```

Start the stack:
```bash
pct exec 106 -- bash -c 'cd /opt/printhive && docker compose up -d'
```

---

### Step 10: Authenticate Tailscale

Run inside container `106` to obtain your unique authorization URL:

```bash
pct exec 106 -- tailscale up --hostname=printhive-srv --operator=root --reset
```

Open the printed URL in your browser and authorize the node to join your Tailnet. Once authorized, verify the assigned IP:
```bash
pct exec 106 -- tailscale ip -4
```

---

## 5. Client Setup & Trusted HTTPS (Eliminating "Insecure" Warnings)

Because this setup uses a custom self-signed 10-year root certificate, client devices must install trust for `printhive.crt` to eliminate SSL browser warnings.

### A. macOS Client Setup

1. **Map the Local FQDNs** in `/etc/hosts`:
   ```bash
   sudo sh -c 'echo "192.168.1.250 printhive.local.home orcaslicer.local.home" >> /etc/hosts'
   ```

2. **Install Certificate into System Keychain** (Automatic via Orchestrator):
   ```bash
   ./deploy/printhive_orchestrator.sh --install-cert
   ```

   *Or manually via Terminal:*
   ```bash
   curl -sk http://192.168.1.250/cert -o /tmp/printhive.crt
   sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain /tmp/printhive.crt
   ```

3. **Verify**: Open [`https://printhive.local.home`](https://printhive.local.home) in Safari or Chrome. The lock icon will be solid green/secure without warnings.

---

### B. iPhone / iPad (iOS) Setup

1. Open **Safari** on your iOS device and navigate to:
   ```
   http://192.168.1.250/cert
   ```
2. Tap **Allow** when prompted to download the configuration profile.
3. Open **Settings** $\rightarrow$ tap **Profile Downloaded** (near the top) $\rightarrow$ tap **Install** (enter your passcode).
4. Navigate to **Settings** $\rightarrow$ **General** $\rightarrow$ **About** $\rightarrow$ **Certificate Trust Settings** (at the bottom).
5. Under *"Enable full trust for root certificates"*, toggle **ON** the switch for `PrintHive HomeLab`.

---

### C. Android / Pixel Phone Setup

1. Open **Chrome** on your Android device and visit:
   ```
   http://192.168.1.250/cert
   ```
2. The `printhive.crt` certificate will download to your device.
3. Open device **Settings** $\rightarrow$ **Security & Privacy** $\rightarrow$ **More Security Settings** $\rightarrow$ **Encryption & Credentials**.
4. Tap **Install a Certificate** $\rightarrow$ choose **CA Certificate** (tap *"Install anyway"* if prompted).
5. Select the downloaded `printhive.crt` file.

---

### D. Linux Desktop Client Setup

```bash
# Add hosts entry
sudo sh -c 'echo "192.168.1.250 printhive.local.home orcaslicer.local.home" >> /etc/hosts'

# Install trusted CA
curl -sk http://192.168.1.250/cert | sudo tee /usr/local/share/ca-certificates/printhive.crt >/dev/null
sudo update-ca-certificates
```

---

## 6. Troubleshooting & Operational Runbook

### Issue 1: Bambu Lab Flapping / Constant Reconnects
* **Symptom:** PrintHive UI shows Bambu printer reconnecting every other second; logs show `Request topic subscription accepted` repeatedly.
* **Root Cause:** Multiple containers (e.g., an old container LXC `105` and new LXC `106`) are running simultaneously and connecting to the same Bambu printer with identical client credentials. The Bambu broker only permits one active session and terminates the conflicting client.
* **Fix:** Stop legacy containers:
  ```bash
  pct stop 105
  ```

### Issue 2: Elegoo Centauri Carbon 1 Shows Offline on Port 3030
* **Symptom:** Port 3030 TCP connects but WebSocket handshake times out or is rejected.
* **Root Cause:** The embedded daemon on the Elegoo mainboard can freeze if an interrupted multi-chunk upload occurred.
* **Fix:** Power cycle the physical Elegoo printer (turn switch OFF, wait 10 seconds, turn ON). Once booted, PrintHive auto-reconnects within 3 seconds.

### Issue 3: macOS Tar Extended Header Warnings
* **Symptom:** `tar: Ignoring unknown extended header keyword 'LIBARCHIVE.xattr.com.apple.provenance'`
* **Root Cause:** macOS `tar` emits PAX headers for system quarantine attributes.
* **Fix:** Always create archives with `--format ustar --no-mac-metadata --no-xattrs` and unpack with `--warning=no-unknown-keyword`.

### Issue 4: SSH "Too many authentication failures"
* **Symptom:** Proxmox rejects SSH connection with `Permission denied (publickey,password)`.
* **Root Cause:** Client `ssh-agent` offers multiple local keys and exceeds `MaxAuthTries` before reaching password/deploy key.
* **Fix:** Use `-o IdentitiesOnly=yes -i ~/.ssh/id_printhive_deploy`.

---

## 7. Service Reference Cheat Sheet

| Action | Command |
| :--- | :--- |
| **Inspect Live Container Logs** | `ssh -i ~/.ssh/id_printhive_deploy root@192.168.1.248 "pct exec 106 -- docker logs -f printhive"` |
| **Restart Application Stack** | `ssh -i ~/.ssh/id_printhive_deploy root@192.168.1.248 "pct exec 106 -- bash -c 'cd /opt/printhive && docker compose restart'"` |
| **Check Tailscale Status** | `ssh -i ~/.ssh/id_printhive_deploy root@192.168.1.248 "pct exec 106 -- tailscale status"` |
| **Check Network Interfaces** | `ssh -i ~/.ssh/id_printhive_deploy root@192.168.1.248 "pct exec 106 -- ip -br a"` |
| **Download Fresh SSL Cert** | `curl -sk http://192.168.1.250/cert -o printhive.crt` |
| **Re-run Orchestrator TUI** | `./deploy/printhive_orchestrator.sh` |
