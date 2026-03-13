# PiParent — Parental Control Monitor

A Pi Zero W + Adafruit 1.14" MiniPiTFT parental control display that monitors your Pi-hole DNS traffic and alerts you when watched devices are bypassing the blocklist.

## Hardware

- **Pi Zero W** (Raspberry Pi OS Lite 32-bit, Bookworm)
- **Adafruit 1.14" MiniPiTFT** — plugs directly onto GPIO header
  - ST7789 display, 240×135 pixels
  - Button A: GPIO 23 | Button B: GPIO 24
  - Backlight: GPIO 26

## Features

- **Stats mode** — block rate, total/blocked query counts, query rate, active alert count
- **Live DNS Feed** — scrolling real-time DNS query log, color-coded (green=allowed, red=blocked)
- **Watched Devices** — list of monitored devices with live alert status indicators
- **Alerts mode** — bypass and IP-change alerts with timestamps
- **Flashing alert banner** — blinks red when active alerts exist (visible in all modes)
- **Web UI** (port 5000) — add/remove watched devices, manage social domain blocklist, clear alerts

## Alert Types

| Type | Trigger |
|------|---------|
| `BYPASS` | Watched device made ≥ N allowed queries to social domains within poll window |
| `IP_CHANGE` | Watched device's IP address changed (possible MAC randomization) |

## File Overview

| File | Purpose |
|------|---------|
| `piparent.py` | Main daemon — display loop, button handling, mode switching |
| `watcher.py` | Pi-hole polling, bypass detection, alert management |
| `pihole_client.py` | Pi-hole v6 API wrapper with session auth |
| `web_ui.py` | Flask web UI (port 5000) |
| `config.json` | Your config (gitignored — copy from config.example.json) |
| `watched_macs.json` | Persistent watched device list |
| `social_domains.json` | Domains to watch for bypass detection |
| `alerts.json` | Persisted alert history |
| `systemd/piparent.service` | Systemd unit for auto-start |

## Setup

### 1. Flash the Pi Zero

Flash **Raspberry Pi OS Lite 32-bit (Bookworm)** using Raspberry Pi Imager.
Enable SSH and set hostname/user in the imager advanced settings.

### 2. First boot — install dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-pil fonts-dejavu git

# Enable SPI
sudo raspi-config nonint do_spi 0

# Install Python deps
pip3 install -r requirements.txt --break-system-packages
```

### 3. Deploy project

```bash
mkdir ~/piparent
# Copy all files from this folder to ~/piparent on the Pi
scp -r /path/to/PiParent/* pi@<PI-IP>:~/piparent/
```

### 4. Configure

```bash
cp ~/piparent/config.example.json ~/piparent/config.json
nano ~/piparent/config.json
```

Fill in:
- `pihole_base_url` — e.g. `http://192.168.0.103:8080/api`
- `pihole_password` — your Pi-hole admin password
- `display_ip` — the Pi Zero's IP address (shown in web UI links on display)

### 5. Install systemd service

```bash
sudo cp ~/piparent/systemd/piparent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable piparent
sudo systemctl start piparent
```

### 6. Check logs

```bash
tail -f ~/piparent/piparent.log
```

## Web UI

Open `http://<PI-ZERO-IP>:5000` in a browser on your network.

| Page | URL | Purpose |
|------|-----|---------|
| Dashboard | `/` | Stats + active alerts + watched device status |
| Devices | `/devices` | Add/remove watched MAC addresses |
| Blocklist | `/blocklist` | Manage social domains + set bypass threshold |
| Alerts | `/alerts` | Full alert history, clear alerts |

## Button Controls

| Button | Action |
|--------|--------|
| A (GPIO 23) | Next display mode |
| B (GPIO 24) | Previous display mode |

## Notes on MAC Randomization

Modern iOS and Android devices randomize their MAC address per WiFi network. If a device changes MAC, you'll get an `IP_CHANGE` alert. You may need to:
1. Disable MAC randomization on the device (Settings → WiFi → your network → Private Address → Off)
2. Update the MAC in the watched devices list via the web UI
