<p align="center">
  <img src="static/img/printhive_logo.svg" alt="PrintHive Logo" width="300">
</p>

<h1 align="center">PrintHive</h1>

<p align="center">
  <strong>Universal Multi-Vendor 3D Printer Command Center & Companion</strong><br>
  Self-hosted control for Bambu Lab, Elegoo, Flashforge, and multi-vendor print farms. No cloud. Your rules.
</p>

<p align="center">
  <a href="#-about-printhive">About</a> •
  <a href="#-key-features--multi-brand-extensions">Features</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-upstream-bambuddy-foundation">Upstream Base</a>
</p>

---

## 🐝 About PrintHive

**PrintHive** is an open-source, multi-vendor 3D printing ecosystem forked from the excellent upstream [Bambuddy](https://github.com/maziggy/bambuddy) project. 

While Bambuddy focuses primarily on Bambu Lab machines, **PrintHive**'s mission is to be the ultimate **universal print farm companion** across all major 3D printer manufacturers and companion ecosystems:

- 🚀 **Bambu Lab Fleet**: Full MQTT, AMS/AMS-HT multi-slot management, FTPS, and live chamber camera streaming.
- 🐊 **Elegoo Centauri Carbon & CC Series**: Native SDCP WebSocket status telemetry, 2MB optimized HTTP chunk file transfers, and web camera integration (powered by [pycentauri](https://github.com/bjan/pycentauri)).
- ⚡ **Flashforge Creator 5**: Multi-toolhead dual-nozzle (T0/T1) telemetry, REST API dispatch (Port 8898), and direct MJPEG camera streaming.
- 🏷️ **Phomemo Bluetooth Thermal QR & Spool Ecosystem**: 203 DPI 30mm round sticker generation with ESC/POS raster drivers in [BuddyDash](https://github.com/ChronosWing/BuddyDash), automated spool tare weight subtraction, and QR-to-NFC backend translation.

---

## ⚡ Key Features & Multi-Brand Extensions

### 1. Multi-Vendor Farm Telemetry & Control
- Unified dashboard for Bambu Lab, Elegoo, and Flashforge machines.
- Consolidated queue scheduling, smart plug power management, print history analytics, and file archives.

### 2. Phomemo 30mm Thermal QR Sticker System
- Print 30mm round circular QR stickers directly over Bluetooth from **BuddyDash** without using Phomemo vendor apps.
- Automatic Spool Tare Weight subtraction referencing PrintHive's pre-loaded database of 90+ empty spool weights.
- Backend server QR-to-NFC translation layer.

### 3. Integrated Slicing & Pipelines
- Drop STLs or 3MFs into PrintHive to slice server-side via sidecar integration without desktop slicers.
- Save reusable slicer recipes (printer + process + filament) into **Slicer Pipelines** for one-click multi-copy fanout dispatches.

---

## 🚀 Quick Start (Docker)

Launch PrintHive in one command using Docker:

```bash
docker run -d \
  --name printhive \
  --network host \
  -v printhive_data:/app/data \
  -v printhive_logs:/app/logs \
  --restart unless-stopped \
  ghcr.io/anuragdeshpande/printhive:latest
```

Open `http://localhost:8000` (or your server's IP address) in any browser!

---

## 🔗 Upstream Bambuddy Foundation

PrintHive builds on top of upstream **Bambuddy**. For core Bambu-specific features, historical context, or original documentation reference, please visit:
- **Upstream Repository**: [github.com/maziggy/bambuddy](https://github.com/maziggy/bambuddy)
- **Upstream License**: [GNU Affero General Public License v3.0](LICENSE)

---

## 📄 License

PrintHive is licensed under the [GNU Affero General Public License v3.0](LICENSE).
