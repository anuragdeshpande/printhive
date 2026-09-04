<p align="center">
  <img src="static/img/printhive_logo.svg" alt="PrintHive Logo" width="300">
</p>

<h1 align="center">PrintHive</h1>

<p align="center">
  <strong>Universal Multi-Vendor 3D Printer Command Center, Virtual Printer Farm & Remote Slicing Engine</strong><br>
  Self-hosted, cloud-free fleet orchestration for Bambu Lab, Elegoo, and multi-brand 3D printers with native slicer bridge, containerized OrcaSlicer, Tailscale, and hardened SSL.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License: AGPL v3">
  <img src="https://img.shields.io/badge/Python-3.12-blue.svg" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Proxmox-LXC%20Hardened-orange.svg" alt="Proxmox LXC">
  <img src="https://img.shields.io/badge/Docker-Compose%20v2-2496ED.svg" alt="Docker">
  <img src="https://img.shields.io/badge/Tailscale-Native%20VPN-100.65.78.92-brightgreen.svg" alt="Tailscale">
  <img src="https://img.shields.io/badge/SSL-10--Year%20SAN-success.svg" alt="SSL">
</p>

<p align="center">
  <a href="#-about-printhive">About</a> •
  <a href="#-system-architecture">Architecture</a> •
  <a href="#-key-features">Features</a> •
  <a href="#-deployment-orchestrator">Orchestrator</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-client-setup--trusted-https">Client SSL Setup</a> •
  <a href="#-documentation">Docs</a>
</p>

---

## 🐝 About PrintHive

**PrintHive** is a modern, privacy-first, universal 3D printer management platform forked from the upstream [Bambuddy](https://github.com/maziggy/bambuddy) foundation and radically overhauled to serve as an enterprise-grade print farm command center.

Modern 3D printing is plagued by proprietary walled gardens, vendor-specific slicer locks, and unreliable cloud brokers. PrintHive solves this by presenting a **cohesive, modular abstraction layer**:

1. **Every 3D printer is unified into a standard Feature Pipeline**: Whether telemetry arrives over Bambu MQTT, Elegoo SDCP WebSockets, or Klipper REST, PrintHive normalizes the metrics, job state, file transfer, and controls into a clean interface.
2. **Virtual Printers with Dedicated Multi-Static IPs**: PrintHive acts as a local proxy on your LAN. Slicers (OrcaSlicer, Bambu Studio) discover virtual printers as physical machines, routing slicing jobs directly to automated print queues and physical machines with zero cloud interaction.
3. **Containerized Server-Side Slicing**: Run a full instance of OrcaSlicer directly on your homelab server with hardware memory backing, synced desktop presets, and KasmVNC browser streaming.
4. **Hardened Homelab Deployment**: Automated Proxmox LXC orchestration with 10-year SSL SAN encryption, Tailscale hardware pass-through, and one-click root certificate installation across all clients.

---

## 🏛️ System Architecture

PrintHive is designed for high-density homelab virtualization (e.g., Proxmox VE) and Docker containerization:

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

### 1. Modular Printer Pipeline (`PrinterPipeline`)
Printers are no longer hardcoded into disparate services. Instead, each manufacturer implements a standardized `IPrinterAdapter`:
* **Telemetry**: Unified temperature, fan, chamber, nozzle, and layer stats.
* **Job Control**: Universal `start_print`, `pause`, `resume`, and `cancel` verbs.
* **File Upload**: Direct FTPS (Bambu) or multi-chunk tokenized HTTP uploads (Elegoo).
* **Live Camera**: Chamber RTSP and MJPEG broadcast streaming.

### 2. Multi-IP Virtual Printer Network
To eliminate port collisions on standard ports (Port 990 for FTPS, Port 8883 for MQTT, Port 3030 for SDCP, Port 1900/2021 for SSDP), each virtual printer binds to its own dedicated static IP interface on the local subnet (`vmbr0`). Your local router/gateway (e.g., Google Fiber `192.168.1.1`) sees them as separate physical devices.

### 3. Containerized OrcaSlicer (KasmVNC & Drag-and-Drop Auto-Load)
OrcaSlicer runs natively inside the stack (`lscr.io/linuxserver/orcaslicer`) with 2GB shared memory (`/dev/shm`), dark mode enabled by default, and a built-in drag-and-drop watcher:
* **Drag-and-Drop File Loading**: Drag any `.3mf`, `.stl`, `.step`, or `.obj` file directly from your desktop into the browser window — the integrated `orca_drop_watcher` daemon detects the upload and opens it on the build plate automatically.
* **Direct Print Archive Access**: Mounts PrintHive's data archive directly into `/prints/archive` so you can open historical prints with `Ctrl+O` without re-uploading.
* **Synced Slicing Presets**: Custom filament profiles, printer settings, and process parameters from your desktop are synchronized automatically.

### 4. Nginx Reverse Proxy & SSL Termination
* **Automatic Redirect**: Port 80 redirects to HTTPS on Port 443.
* **Subject Alternative Name (SAN)**: 10-year TLS 1.2/1.3 certificate covers `printhive.local.home`, `orcaslicer.local.home`, `*.local.home`, LAN IPs, and Tailscale endpoints.
* **Universal Cert Distribution**: Nginx exposes `/cert` and `/printhive.crt` over plain HTTP so mobile phones and laptops can download the root certificate before completing an SSL handshake.

---

## ⚡ Key Features

### 🚀 Bambu Lab Fleet Integration
* **MQTT Telemetry**: Real-time state subscription with session deduplication to prevent connection drops.
* **AMS & AMS-HT Control**: Multi-slot filament mapping, slot switching, and runout handling.
* **Implicit FTPS Server**: Virtual Bambu printer FTPS allows direct "Send" and "Print" from OrcaSlicer/Bambu Studio.
* **Camera-322 Proxy**: Relays low-latency chamber video directly through the virtual printer IP.

### 🐊 Elegoo Centauri Carbon & CC Series Integration (SDCP v3.0.0)
* **Elegoo Link Feature Parity**: Powered by an overhauled `pycentauri` engine matching official Elegoo Link specifications.
* **SDCP WebSocket Server**: Native discovery and control over port 3030.
* **Multi-Chunk Tokenized HTTP Upload**: High-speed, buffered multi-part file transfers with automated MD5 verification.
* **Live Camera Stream**: MJPEG broadcast server (`/mjpeg`) with auto-reconnect and frame distribution.
* **Smart Board ID Pairing**: Seamless discovery and pairing using board IDs (`107319580103...`).

### 🏷️ SpoolBuddy & Phomemo Bluetooth QR Ecosystem
* **30mm Circular Thermal QR Printing**: ESC/POS raster print drivers communicate with Phomemo thermal printers over Bluetooth via [BuddyDash](https://github.com/ChronosWing/BuddyDash).
* **Automated Spool Tare Subtraction**: Integrates a pre-loaded catalog of 90+ empty spool weights for precision filament estimation.
* **Backend QR-to-NFC Translation**: Bridges thermal labels directly to digital filament inventories.

---

## 🛠️ Deployment Orchestrator (`deploy/printhive_orchestrator.sh`)

PrintHive includes an interactive terminal UI (TUI) orchestrator for zero-effort homelab deployments:

```bash
chmod +x deploy/printhive_orchestrator.sh
./deploy/printhive_orchestrator.sh
```

```
╔════════════════════════════════════════════════════════════════════════════════╗
║               PRINTHIVE & ORCASLICER DEPLOYMENT ORCHESTRATOR                   ║
║        Hardened Proxmox LXC • Multi-IP Virtual Printers • Tailscale • SSL      ║
╚════════════════════════════════════════════════════════════════════════════════╝

Select an operation:

  [1] 🚀 Full Automated Teardown & Fresh Rebuild (Backup -> Destroy -> Rebuild -> Restore)
  [2] 🖨️  Configure Printers One-by-One (Physical IPs & Virtual Static IPs)
  [3] 📦 Backup PrintHive & OrcaSlicer Data Only
  [4] ⚙️  Edit Performance Sizing, Host & FQDN
  [5] 🌐 Reconfigure Nginx HTTPS & SSL Certificates Only
  [6] 🔒 Tailscale Status & Auth URL
  [7] 🩺 Run Health & Port Verification Diagnostics
  [8] 🛡️  Install SSL Certificate into Client Keychain / Trust Store
  [9] 🛑 Destroy LXC Container 106 Only
  [10] 🚪 Exit
```

### Unattended Headless Flags
* **Full Rebuild**: `./deploy/printhive_orchestrator.sh --rebuild`
* **Install Local SSL Trust**: `./deploy/printhive_orchestrator.sh --install-cert`

---

## 🚀 Quick Start (Proxmox LXC)

For complete zero-to-production manual setup or custom configurations, see the comprehensive [**Proxmox Deployment Guide**](docs/PROXMOX_DEPLOYMENT_GUIDE.md).

### One-Line Automated Provisioning:
1. Ensure your Proxmox server is accessible at `192.168.1.248`.
2. Generate and authorize a deployment key:
   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_printhive_deploy -C "printhive-deploy"
   ssh-copy-id -i ~/.ssh/id_printhive_deploy.pub root@192.168.1.248
   ```
3. Run the automated rebuild:
   ```bash
   ./deploy/printhive_orchestrator.sh --rebuild
   ```

---

## 🚀 Quick Start (Standard Docker)

If you prefer running standalone Docker on a single host without multi-IP virtual interfaces:

```bash
docker run -d \
  --name printhive \
  --network host \
  -v printhive_data:/app/data \
  -v printhive_logs:/app/logs \
  -e TZ=America/Denver \
  -e PUID=1000 \
  -e PGID=1000 \
  -e PORT=8000 \
  --restart unless-stopped \
  ghcr.io/anuragdeshpande/printhive:latest
```

---

## 🛡️ Client Setup & Trusted HTTPS

Because PrintHive creates a private, self-hosted root certificate covering your LAN and Tailscale domain, client devices must install root trust once to eliminate browser "Not Secure" warnings:

### 1. Add Domain to `/etc/hosts` (Mac / Linux)
```bash
sudo sh -c 'echo "192.168.1.250 printhive.local.home orcaslicer.local.home" >> /etc/hosts'
```

### 2. Install Root Certificate

* **macOS (Automated)**:
  ```bash
  ./deploy/printhive_orchestrator.sh --install-cert
  ```
* **iPhone / iPad (iOS)**:
  1. Open Safari and navigate to `http://192.168.1.250/cert`.
  2. Tap **Allow** to download profile $\rightarrow$ **Settings** $\rightarrow$ **Profile Downloaded** $\rightarrow$ **Install**.
  3. Go to **Settings** $\rightarrow$ **General** $\rightarrow$ **About** $\rightarrow$ **Certificate Trust Settings** and toggle full trust **ON**.
* **Android / Pixel**:
  1. Download certificate from `http://192.168.1.250/cert`.
  2. Go to **Settings** $\rightarrow$ **Security & Privacy** $\rightarrow$ **Encryption & Credentials** $\rightarrow$ **Install a Certificate** $\rightarrow$ **CA Certificate**.
* **Linux Client**:
  ```bash
  curl -sk http://192.168.1.250/cert | sudo tee /usr/local/share/ca-certificates/printhive.crt >/dev/null
  sudo update-ca-certificates
  ```

---

## 📖 Documentation

* [**Proxmox LXC Zero-to-Production Guide**](docs/PROXMOX_DEPLOYMENT_GUIDE.md): Full walkthrough on network bridge configuration, `/dev/net/tun` pass-through, and container sizing.
* [**Upstream Bambuddy Base**](https://github.com/maziggy/bambuddy): Core historical Bambu Lab features and original upstream project documentation.

---

## 📄 License

PrintHive is licensed under the [GNU Affero General Public License v3.0 (AGPLv3)](LICENSE).
