#!/usr/bin/env python3
"""
piparent.py — Parental Control Monitor Display
Adafruit 1.14" MiniPiTFT (ST7789, 240x135) + 2 buttons

Modes (Button A = next, Button B = previous):
  0 - STATS       : Pi-hole block rate, totals, alert count
  1 - LIVE FEED   : Scrolling real-time DNS query log
  2 - WATCHED     : Watched device status
  3 - ALERTS      : Active bypass/IP-change alerts

MiniPiTFT 1.14" pinout:
  Display CS: BCM 7 (CE1)   DC: BCM 25   RST: BCM 27   BL: BCM 26
  Button A:   BCM 23        Button B:    BCM 24
"""

import json
import os
import sys
import time
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Config ────────────────────────────────────────────────────────────────────
with open(os.path.join(BASE_DIR, 'config.json')) as f:
    _config = json.load(f)

# ── Display ───────────────────────────────────────────────────────────────────
DISP_W, DISP_H = 240, 135
_display = None
_display_ok = False

try:
    import board
    import busio
    import digitalio
    from adafruit_rgb_display import st7789 as _st7789_lib
    from PIL import Image, ImageDraw, ImageFont

    _cs  = digitalio.DigitalInOut(board.CE1)   # BCM 7
    _dc  = digitalio.DigitalInOut(board.D25)
    _rst = digitalio.DigitalInOut(board.D27)
    _bl  = digitalio.DigitalInOut(board.D26)
    _bl.direction = digitalio.Direction.OUTPUT
    _bl.value = True

    _spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    _display = _st7789_lib.ST7789(
        _spi, cs=_cs, dc=_dc, rst=_rst,
        baudrate=24000000,
        width=135, height=240,
        x_offset=53, y_offset=40,
        rotation=90
    )
    _display_ok = True
    print("[Display] MiniPiTFT 1.14\" initialised (240x135)")
except Exception as _e:
    print(f"[Display] Init failed (headless mode): {_e}")

# ── Buttons ───────────────────────────────────────────────────────────────────
_buttons_ok = False
_BTN_A = 23
_BTN_B = 24

try:
    import RPi.GPIO as GPIO
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(_BTN_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(_BTN_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    _buttons_ok = True
    print("[Buttons] GPIO 23/24 ready")
except Exception as _e:
    print(f"[Buttons] Init failed: {_e}")

# ── Watcher ───────────────────────────────────────────────────────────────────
sys.path.insert(0, BASE_DIR)
from watcher import Watcher
_watcher = Watcher(_config)

# ── Fonts ─────────────────────────────────────────────────────────────────────
def _font(size, bold=False):
    try:
        base = '/usr/share/fonts/truetype/dejavu/'
        name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
        return ImageFont.truetype(base + name, size)
    except Exception:
        return ImageFont.load_default()

F16B = _font(16, bold=True)
F13B = _font(13, bold=True)
F11  = _font(11)
F10  = _font(10)
F9   = _font(9)

# ── Colors ────────────────────────────────────────────────────────────────────
BG             = (10,  10,  25)
WHITE          = (255, 255, 255)
GREEN          = (80,  220, 80)
RED            = (220, 60,  60)
YELLOW         = (255, 220, 0)
ORANGE         = (255, 140, 0)
CYAN           = (80,  220, 220)
GRAY           = (120, 120, 120)
HEADER_STATS   = (30,  80,  180)
HEADER_FEED    = (20,  120, 80)
HEADER_WATCHED = (100, 50,  180)
HEADER_ALERTS  = (180, 40,  40)
HEADER_PIALERT = (20,  110, 120)
HEADER_KIDS    = (20,  140, 60)

# ── Mode state ────────────────────────────────────────────────────────────────
MODES = ['STATS', 'PI.ALERT', 'LIVE FEED', 'WATCHED', 'KIDS LIVE', 'ALERTS']
_mode_idx        = 0
_kids_device_idx = 0
_mode_lock       = threading.Lock()
_last_btn_ms     = 0
DEBOUNCE_MS      = 300


def _set_mode(delta):
    global _mode_idx, _last_btn_ms, _kids_device_idx
    now_ms = int(time.time() * 1000)
    if now_ms - _last_btn_ms < DEBOUNCE_MS:
        return
    _last_btn_ms = now_ms
    with _mode_lock:
        _mode_idx = (_mode_idx + delta) % len(MODES)
    _kids_device_idx = 0   # reset device index when changing modes
    print(f"[Mode] → {MODES[_mode_idx]}")


def _next_kids_device(online_count):
    global _kids_device_idx, _last_btn_ms
    now_ms = int(time.time() * 1000)
    if now_ms - _last_btn_ms < DEBOUNCE_MS:
        return
    _last_btn_ms = now_ms
    _kids_device_idx = (_kids_device_idx + 1) % online_count
    print(f"[Kids] → device {_kids_device_idx + 1}/{online_count}")


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _header(draw, title, color):
    draw.rectangle([(0, 0), (DISP_W, 18)], fill=color)
    draw.text((6, 2), title, font=F13B, fill=WHITE)
    ts = datetime.now().strftime('%H:%M')
    draw.text((DISP_W - 38, 3), ts, font=F10, fill=(200, 200, 200))


def _mode_dots(draw, idx):
    """Small navigation dots at bottom right."""
    for i in range(len(MODES)):
        x = DISP_W - (len(MODES) - i) * 10 - 4
        y = DISP_H - 8
        col = WHITE if i == idx else GRAY
        draw.ellipse([(x, y), (x + 5, y + 5)], fill=col)


def _alert_banner(draw):
    """Flashing red banner if active alerts exist."""
    if _watcher.has_active_alerts() and int(time.time() * 2) % 2 == 0:
        draw.rectangle([(0, DISP_H - 14), (DISP_W, DISP_H)], fill=RED)
        n = len(_watcher.get_active_alerts())
        draw.text((4, DISP_H - 13), f"⚠  {n} ALERT(S) — CHECK ALERTS MODE", font=F9, fill=WHITE)


# ── Mode renderers ────────────────────────────────────────────────────────────

def _draw_stats(draw):
    _header(draw, "PI PARENT  STATS", HEADER_STATS)
    with _watcher._lock:
        q = _watcher.summary.get('queries', {})
    total   = q.get('total', 0)
    blocked = q.get('blocked', 0)
    pct     = q.get('percent_blocked', 0.0)
    freq    = q.get('frequency', 0.0)

    draw.text((8, 22), f"{pct:.1f}%", font=F16B, fill=RED)
    draw.text((82, 28), "blocked", font=F11, fill=GRAY)
    draw.text((8, 46), f"Total:    {total:,}", font=F10, fill=WHITE)
    draw.text((8, 59), f"Blocked:  {blocked:,}", font=F10, fill=RED)
    draw.text((8, 72), f"Rate:     {freq:.1f}/min", font=F10, fill=CYAN)

    active = len(_watcher.get_active_alerts())
    draw.text((8, 85), f"Alerts:   {active}", font=F10,
              fill=ORANGE if active > 0 else GREEN)
    watched = _watcher.load_watched()
    draw.text((8, 98), f"Watched:  {len(watched)} device(s)", font=F10, fill=CYAN)
    _alert_banner(draw)


def _draw_live_feed(draw):
    _header(draw, "LIVE DNS FEED", HEADER_FEED)
    with _watcher._lock:
        queries = list(_watcher.recent_queries[:11])
    y = 21
    for q in queries:
        if y > DISP_H - 10:
            break
        domain  = (q.get('domain') or '?')
        status  = (q.get('status') or '').upper()
        blocked = 'BLOCKED' in status
        col     = RED if blocked else GREEN
        marker  = '✗' if blocked else '✓'
        if len(domain) > 28:
            domain = domain[:27] + '…'
        draw.text((4, y), f"{marker} {domain}", font=F9, fill=col)
        y += 10


def _draw_watched(draw):
    _header(draw, "WATCHED DEVICES", HEADER_WATCHED)
    watched = _watcher.load_watched()
    alert_macs = {a['mac'].lower() for a in _watcher.get_active_alerts()}

    if not watched:
        draw.text((8, 32), "No watched devices.", font=F10, fill=GRAY)
        draw.text((8, 46), "Add via web UI:", font=F10, fill=GRAY)
        ip = _config.get('display_ip', 'Pi-IP')
        draw.text((8, 60), f"http://{ip}:5000", font=F10, fill=CYAN)
        return

    y = 22
    for dev in watched[:8]:
        if y > DISP_H - 12:
            break
        mac       = dev.get('mac', '').lower()
        name      = dev.get('name', mac)[:18]
        has_alert = mac in alert_macs
        dot_col   = RED if has_alert else GREEN
        # Blink dot on alert
        if not (has_alert and int(time.time() * 2) % 2 == 0):
            draw.ellipse([(4, y + 1), (11, y + 8)], fill=dot_col)
        draw.text((15, y), name, font=F9, fill=ORANGE if has_alert else WHITE)
        y += 12


def _draw_alerts(draw):
    active = _watcher.get_active_alerts()
    _header(draw, f"ALERTS  ({len(active)} active)", HEADER_ALERTS)

    if not active:
        draw.text((8, 32), "No active alerts.", font=F13B, fill=GREEN)
        draw.text((8, 52), "All devices clean ✓", font=F13B, fill=GREEN)
        return

    type_labels = {
        'bypass':           'BYPASS',
        'dns_bypass':       'DNS BYPASS',
        'vpn_or_encrypted': 'VPN/ENCRYPT',
        'ip_change':        'IP CHANGE',
        'mac_change':       'MAC CHANGE',
        'doh_attempt':      'DoH BYPASS',
    }
    y = 22
    for a in active[:4]:
        if y > DISP_H - 12:
            break
        label = type_labels.get(a['type'], a['type'].upper())
        name  = a.get('name', a.get('mac', '?'))[:16]
        t     = (a.get('time', ''))[11:16]
        if a['type'] in ('doh_attempt', 'vpn_or_encrypted'):
            col = YELLOW
        elif a['type'] in ('bypass', 'dns_bypass'):
            col = RED
        else:
            col = ORANGE
        draw.text((4, y),      f"[{label}] {name}", font=F13B, fill=col)
        msg = a.get('message', '')
        if len(msg) > 34:
            msg = msg[:33] + '…'
        draw.text((4, y + 16), msg, font=F11, fill=WHITE)
        draw.text((4, y + 29), t,   font=F9,  fill=GRAY)
        draw.line([(0, y + 37), (DISP_W, y + 37)], fill=(50, 50, 80), width=1)
        y += 39


def _get_online_watched():
    """Returns list of (name, mac, ip, queries, has_bypass) for watched+online devices."""
    watched = _watcher.load_watched()
    watched_by_mac = {d['mac'].lower(): d['name'] for d in watched}
    if not watched_by_mac:
        return []

    with _watcher._lock:
        pa_devices  = list(_watcher.pialert_devices)
        queries     = list(_watcher.recent_queries)
        ip_to_mac   = dict(_watcher.ip_to_mac)
        alert_macs  = {a['mac'].lower() for a in _watcher.alerts
                       if not a['cleared'] and a['type'] == 'bypass'}

    # Build IP→MAC from Pi.Alert data (Pi-hole network API is often empty for local devices)
    pa_ip_to_mac = {}
    for dev in pa_devices:
        m  = (dev.get('dev_MAC') or '').lower()
        ip = dev.get('dev_LastIP', '')
        if m and ip:
            pa_ip_to_mac[ip] = m

    online_watched = []
    for dev in pa_devices:
        if not dev.get('_online'):
            continue
        mac = (dev.get('dev_MAC') or '').lower()
        if mac not in watched_by_mac:
            continue
        ip   = dev.get('dev_LastIP', '')
        name = watched_by_mac[mac]

        dev_queries = []
        for q in queries:
            client = q.get('client', {})
            qip    = client.get('ip', '') if isinstance(client, dict) else str(client)
            # Match by direct IP (most reliable) OR via either MAC→IP table
            matched = (
                (ip and qip == ip)
                or ip_to_mac.get(qip, '').lower()    == mac
                or pa_ip_to_mac.get(qip, '').lower() == mac
            )
            if matched:
                domain  = (q.get('domain') or '?')
                blocked = 'BLOCKED' in (q.get('status') or '').upper()
                dev_queries.append((domain, blocked))
                if len(dev_queries) >= 5:
                    break

        online_watched.append((name, mac, ip, dev_queries, mac in alert_macs,
                                _watcher.get_social_bypass_recent(mac)))

    return online_watched


def _draw_device_panel(draw, x, y, w, h, name, ip, queries, has_bypass, bypass_recent=False):
    """Draw one device panel inside a bounding box."""
    dot_col  = RED if has_bypass else GREEN
    show_dot = not (has_bypass and int(time.time() * 2) % 2 == 0)
    if show_dot:
        draw.ellipse([(x + 2, y + 2), (x + 9, y + 9)], fill=dot_col)

    short_ip  = ('.' + ip.split('.')[-1]) if ip else '?'
    max_name  = (w - 32) // 7
    label     = f"{name[:max_name]}  {short_ip}"
    # CYAN = clean, RED = social bypass detected in last hour
    name_col  = RED if bypass_recent else CYAN
    draw.text((x + 13, y), label, font=F9, fill=name_col)

    if has_bypass:
        draw.text((x + w - 14, y), '⚠', font=F9, fill=RED)

    draw.line([(x, y + 11), (x + w, y + 11)], fill=(40, 40, 80), width=1)

    qy       = y + 14
    max_ch   = max(6, (w - 14) // 6)
    if not queries:
        draw.text((x + 2, qy), 'No queries yet', font=F9, fill=GRAY)
        return
    for domain, blocked in queries:
        if qy > y + h - 8:
            break
        col    = RED if blocked else WHITE
        marker = '✗' if blocked else '·'
        if len(domain) > max_ch:
            domain = domain[:max_ch - 1] + '…'
        draw.text((x + 2, qy), f"{marker} {domain}", font=F9, fill=col)
        qy += 10


def _draw_kids_live(draw):
    global _kids_device_idx
    online = _get_online_watched()
    n      = len(online)

    if n == 0:
        _header(draw, "KIDS LIVE  (0 online)", HEADER_KIDS)
        watched = _watcher.load_watched()
        if not watched:
            draw.text((8, 30), "No watched devices.", font=F11, fill=GRAY)
            ip = _config.get('display_ip', 'Pi-IP')
            draw.text((8, 48), f"http://{ip}:5000", font=F10, fill=CYAN)
        else:
            draw.text((8, 30), "All watched devices", font=F13B, fill=GREEN)
            draw.text((8, 50), "are offline  ✓",     font=F13B, fill=GREEN)
        return

    # Clamp index in case devices went offline
    _kids_device_idx = _kids_device_idx % n
    name, mac, ip, queries, has_bypass, bypass_recent = online[_kids_device_idx]

    # ── Header: dot + name + IP + device index ────────────────────────────────
    draw.rectangle([(0, 0), (DISP_W, 18)], fill=HEADER_KIDS)
    dot_col  = RED if has_bypass else GREEN
    show_dot = not (has_bypass and int(time.time() * 2) % 2 == 0)
    if show_dot:
        draw.ellipse([(4, 5), (12, 13)], fill=dot_col)
    short_ip = ('.' + ip.split('.')[-1]) if ip else '?'
    # Name in RED if bypass recent, else WHITE in header
    name_col = RED if bypass_recent else WHITE
    draw.text((16, 2), f"{name}  {short_ip}", font=F13B, fill=name_col)
    ts = datetime.now().strftime('%H:%M')
    if n > 1:
        indicator = f"{_kids_device_idx + 1}/{n}"
        draw.text((DISP_W - 52, 3), indicator, font=F10, fill=(200, 200, 200))
        draw.text((DISP_W - 32, 3), ts,        font=F10, fill=(180, 180, 180))
    else:
        draw.text((DISP_W - 38, 3), ts,        font=F10, fill=(180, 180, 180))

    # ── Query list ────────────────────────────────────────────────────────────
    y      = 22
    max_ch = 30  # ~30 chars fit in F11 across 240px
    if not queries:
        draw.text((8, y + 6), "No queries yet…", font=F11, fill=GRAY)
    else:
        for domain, blocked in queries:
            if y > DISP_H - 12:
                break
            col    = RED if blocked else WHITE
            marker = '✗' if blocked else '·'
            if len(domain) > max_ch:
                domain = domain[:max_ch - 1] + '…'
            draw.text((6, y), f"{marker} {domain}", font=F11, fill=col)
            y += 14

    # ── Hint when multiple devices ────────────────────────────────────────────
    if n > 1:
        draw.text((6, DISP_H - 11), "B: next device", font=F9, fill=(80, 80, 100))


def _draw_pialert(draw):
    _header(draw, "PI.ALERT  NETWORK", HEADER_PIALERT)
    with _watcher._lock:
        status = dict(_watcher.pialert_status)
        events = list(_watcher.pialert_events[:4])

    if not status:
        draw.text((8, 30), "Pi.Alert not reachable", font=F10, fill=GRAY)
        draw.text((8, 44), "Check pialert_url /", font=F9, fill=GRAY)
        draw.text((8, 55), "pialert_api_key in config", font=F9, fill=GRAY)
        return

    def _s(*keys):
        for k in keys:
            v = status.get(k)
            if v is not None:
                return v
        return 0

    total    = _s('All_Devices',    'Total', 'TOTAL_DEVICES', 'total')
    online   = _s('Online_Devices', 'Connected', 'CONNECTED_DEVICES', 'connected')
    offline  = _s('Offline_Devices','Disconnected', 'DISCONNECTED_DEVICES', 'disconnected')
    new_dev  = _s('New_Devices',    'NewDevices', 'NEW_DEVICES', 'new_devices')
    down_dev = _s('Down_Devices',   'DownDevices', 'DOWN_DEVICES', 'down_devices')
    last_scan = str(status.get('Last_Scan') or status.get('LastScan') or status.get('LAST_SCAN_DATE') or status.get('last_scan', ''))
    if len(last_scan) > 16:
        last_scan = last_scan[11:16]

    draw.text((8,  22), f"Online:  {online}", font=F13B, fill=GREEN)
    draw.text((130, 22), f"Off: {offline}", font=F13B, fill=GRAY)
    new_col  = ORANGE if new_dev  > 0 else GRAY
    down_col = RED    if down_dev > 0 else GRAY
    draw.text((8,  40), f"New: {new_dev}", font=F10, fill=new_col)
    draw.text((100, 40), f"Down: {down_dev}", font=F10, fill=down_col)
    draw.text((8,  54), f"Total: {total}   Scan: {last_scan}", font=F9, fill=GRAY)
    draw.line([(0, 65), (DISP_W, 65)], fill=(40, 40, 80), width=1)

    y = 69
    for ev in events:
        if y > DISP_H - 10:
            break
        etype = (ev.get('eve_EventType') or '?')
        name  = (ev.get('dev_Name') or ev.get('eve_IP') or '?')[:15]
        t     = str(ev.get('eve_DateTime') or '')
        if len(t) > 16:
            t = t[11:16]
        col = GREEN if 'Connect' in etype else (RED if 'Disconn' in etype else CYAN)
        draw.text((4, y), f"{t}  {name}", font=F9, fill=col)
        y += 10


# ── Display loop ──────────────────────────────────────────────────────────────

def _display_loop():
    from PIL import Image, ImageDraw
    if not _display_ok:
        print("[Display] Headless — display loop idle")
        while True:
            time.sleep(10)

    print("[Display] Loop started")
    while True:
        try:
            with _mode_lock:
                mode = MODES[_mode_idx]
                idx  = _mode_idx
            img  = Image.new('RGB', (DISP_W, DISP_H), BG)
            draw = ImageDraw.Draw(img)

            if mode == 'STATS':
                _draw_stats(draw)
            elif mode == 'PI.ALERT':
                _draw_pialert(draw)
            elif mode == 'LIVE FEED':
                _draw_live_feed(draw)
            elif mode == 'WATCHED':
                _draw_watched(draw)
            elif mode == 'KIDS LIVE':
                _draw_kids_live(draw)
            elif mode == 'ALERTS':
                _draw_alerts(draw)

            _mode_dots(draw, idx)
            _display.image(img)
        except Exception as e:
            print(f"[Display] Draw error: {e}")
        time.sleep(0.5)


# ── Button loop ───────────────────────────────────────────────────────────────

def _button_loop():
    prev_a = prev_b = True
    while True:
        a = GPIO.input(_BTN_A)
        b = GPIO.input(_BTN_B)
        if prev_a and not a:    # Button A — always next mode
            _set_mode(+1)
        if prev_b and not b:    # Button B — next device in KIDS LIVE, else next mode
            with _mode_lock:
                cur = MODES[_mode_idx]
            if cur == 'KIDS LIVE':
                online = _get_online_watched()
                if len(online) > 1:
                    _next_kids_device(len(online))
                else:
                    _set_mode(+1)
            else:
                _set_mode(+1)
        prev_a, prev_b = a, b
        time.sleep(0.05)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    from web_ui import create_app

    _watcher.start()

    app = create_app(_watcher, _config)
    flask_thread = threading.Thread(
        target=lambda: app.run(
            host='0.0.0.0',
            port=_config.get('web_port', 5000),
            debug=False,
            use_reloader=False
        ),
        daemon=True
    )
    flask_thread.start()
    print(f"[Web] Flask UI at http://{_config.get('display_ip', '?')}:{_config.get('web_port', 5000)}")

    if _buttons_ok:
        threading.Thread(target=_button_loop, daemon=True).start()

    _display_loop()
