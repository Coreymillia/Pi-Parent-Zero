"""
web_ui.py — Flask web UI for PiParent.
Routes: / (dashboard), /devices, /blocklist, /alerts
Mobile-friendly dark theme. All templates inline.
"""
import json
import os
import re
import requests as _requests
from datetime import datetime
from flask import Flask, request, redirect, render_template_string, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHED_MACS_FILE  = os.path.join(BASE_DIR, 'watched_macs.json')
SOCIAL_DOMAINS_FILE = os.path.join(BASE_DIR, 'social_domains.json')

# ── Shared layout ─────────────────────────────────────────────────────────────
_BASE_STYLE = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: sans-serif; background: #0a0a1a; color: #eee; font-size: 15px; }
header { background: #1e3c8f; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; }
header h1 { color: #fff; font-size: 18px; }
nav a { color: #90c8ff; margin-left: 14px; text-decoration: none; font-size: 14px; }
.container { max-width: 600px; margin: 0 auto; padding: 16px; }
.card { background: #121228; border-radius: 8px; padding: 14px; margin-bottom: 14px; }
.card h2 { font-size: 13px; color: #80c8ff; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 1px; }
.row2 { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #1e1e3a; }
.val { color: #fff; font-weight: bold; }
.alert-box { padding: 9px; border-radius: 5px; margin-bottom: 7px; background: #1e0808; border-left: 3px solid #d04040; }
.alert-box.cleared { opacity: 0.35; border-left-color: #444; }
.alert-box.doh { background: #1a1a06; border-left-color: #c8b400; }
.atype { color: #ff6868; font-size: 12px; font-weight: bold; }
.atype.doh { color: #e0cc00; }
.amsg  { color: #ccc; font-size: 13px; margin: 2px 0; }
.atime { color: #888; font-size: 11px; }
.dev-row { display: flex; align-items: center; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid #1e1e3a; }
.dot { width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; display: inline-block; flex-shrink: 0; }
.ok   { background: #40d040; }
.warn { background: #e05050; }
input[type=text] { background: #1a1a32; border: 1px solid #3a3a60; color: #eee; padding: 7px 10px; border-radius: 5px; width: 100%; margin-bottom: 8px; font-size: 14px; }
.btn  { background: #1e4aaa; color: #fff; border: none; padding: 8px 14px; border-radius: 5px; cursor: pointer; font-size: 14px; }
.btn.danger  { background: #7a1a1a; }
.btn.success { background: #1a6020; }
.row { display: flex; gap: 8px; align-items: center; }
.tag { background: #1a2e58; color: #90c8ff; padding: 3px 9px; border-radius: 12px; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; margin: 2px; }
.tag .x { background: none; border: none; color: #f88; cursor: pointer; font-size: 12px; padding: 0; }
.flash { animation: fl 1s ease-in-out infinite; }
@keyframes fl { 0%,100% { opacity:1; } 50% { opacity:0.25; } }
.toggle-on  { background: #145214; border: 2px solid #40d040; color: #40d040; border-radius: 8px; padding: 12px 24px; font-size: 16px; font-weight: bold; cursor: pointer; width: 100%; }
.toggle-off { background: #521414; border: 2px solid #d04040; color: #d04040; border-radius: 8px; padding: 12px 24px; font-size: 16px; font-weight: bold; cursor: pointer; width: 100%; }
small { color: #666; }
</style>
"""

_NAV = """
<header>
  <h1>🛡 PiParent</h1>
  <nav>
    <a href="/">Stats</a>
    <a href="/devices">Devices</a>
    <a href="/blocklist">Blocklist</a>
    <a href="/alerts">Alerts</a>
  </nav>
</header>
"""

_DASHBOARD_T = """<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PiParent</title>""" + _BASE_STYLE + """</head><body>""" + _NAV + """
<div class="container">
  <div class="card">
    <h2>Monitoring</h2>
    <form method="post" action="/monitoring/toggle">
      {% if monitoring_on %}
      <button class="toggle-on" type="submit">🟢 MONITORING ON — tap to pause</button>
      {% else %}
      <button class="toggle-off" type="submit">🔴 MONITORING PAUSED — tap to resume</button>
      {% endif %}
    </form>
  </div>

  <div class="card">
    <h2>Pi-hole Stats</h2>
    {% for label, val, col in stats %}
    <div class="row2"><span>{{ label }}</span><span class="val" style="color:{{ col }}">{{ val }}</span></div>
    {% endfor %}
  </div>

  {% if active_alerts %}
  <div class="card">
    <h2 class="flash" style="color:#ff6868">⚠ Active Alerts ({{ active_alerts|length }})</h2>
    {% for a in active_alerts %}
    <div class="alert-box {{ 'doh' if a.type == 'doh_attempt' else '' }}">
      <div class="atype {{ 'doh' if a.type == 'doh_attempt' else '' }}">{{ a.type|upper }} — {{ a.name }}</div>
      <div class="amsg">{{ a.message }}</div>
      <div class="atime">{{ a.time[:16] }}</div>
    </div>
    {% endfor %}
    <form method="post" action="/alerts/clear_all">
      <button class="btn success" type="submit">Clear All Alerts</button>
    </form>
  </div>
  {% endif %}

  <div class="card">
    <h2>Watched Devices ({{ watched|length }})</h2>
    {% for d in watched %}
    {% set alerted = d.mac|lower in alert_macs %}
    <div class="dev-row">
      <span>
        <span class="dot {{ 'warn' if alerted else 'ok' }}"></span>
        {{ d.name }} <small>{{ d.mac }}</small>
      </span>
      {% if alerted %}<span style="color:#ff6868;font-size:12px">⚠ bypass</span>{% endif %}
    </div>
    {% else %}
    <p style="color:#666">No devices watched. <a href="/devices" style="color:#90c8ff">Add one →</a></p>
    {% endfor %}
  </div>
</div></body></html>"""

_DEVICES_T = """<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Devices — PiParent</title>""" + _BASE_STYLE + """</head><body>""" + _NAV + """
<div class="container">
  <div class="card">
    <h2>Watched Devices</h2>
    {% for d in watched %}
    <div class="dev-row">
      <span>{{ d.name }} <small>{{ d.mac }}</small></span>
      <form method="post" action="/devices/remove">
        <input type="hidden" name="mac" value="{{ d.mac }}">
        <button class="btn danger" type="submit">Remove</button>
      </form>
    </div>
    {% else %}
    <p style="color:#666;margin-bottom:10px">No devices yet.</p>
    {% endfor %}
  </div>
  <div class="card">
    <h2>Add Device Manually</h2>
    <form method="post" action="/devices/add">
      <input type="text" name="name" placeholder="Friendly name  (e.g. Kid's iPad)" required>
      <input type="text" name="mac"  placeholder="MAC address  (e.g. aa:bb:cc:dd:ee:ff)" required>
      <button class="btn" type="submit">Add Device</button>
    </form>
  </div>
  {% if pialert_devices %}
  <div class="card">
    <h2>Add from Network Scan (Pi.Alert)</h2>
    <p style="color:#888;font-size:13px;margin-bottom:10px">
      Devices currently seen on your network. Tap <strong style="color:#eee">Watch</strong> to add.
    </p>
    {% for d in pialert_devices %}
    {% set mac_lower = d.dev_MAC | lower %}
    {% set already = mac_lower in watched_macs %}
    <div class="dev-row">
      <span>
        <span class="dot" style="background:{{ '#40d040' if d._online else '#666' }}"></span>
        {{ d.dev_Name or '(unknown)' }}
        <small>{{ d.dev_LastIP }} &nbsp;·&nbsp; {{ d.dev_MAC }}</small>
        {% if d.dev_Vendor %}<small style="color:#555"> · {{ d.dev_Vendor[:20] }}</small>{% endif %}
      </span>
      {% if already %}
        <span style="color:#40d040;font-size:12px">✓ Watched</span>
      {% else %}
        <form method="post" action="/devices/add" style="flex-shrink:0">
          <input type="hidden" name="name" value="{{ d.dev_Name or d.dev_MAC }}">
          <input type="hidden" name="mac"  value="{{ d.dev_MAC }}">
          <button class="btn" type="submit" style="padding:5px 10px;font-size:13px">Watch</button>
        </form>
      {% endif %}
    </div>
    {% endfor %}
  </div>
  {% elif pialert_configured %}
  <div class="card">
    <h2>Network Scan (Pi.Alert)</h2>
    <p style="color:#888;font-size:13px">No devices returned yet — waiting for first poll.</p>
  </div>
  {% endif %}
</div></body></html>"""

_BLOCKLIST_T = """<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blocklist — PiParent</title>""" + _BASE_STYLE + """</head><body>""" + _NAV + """
<div class="container">

  {% if msg %}
  <div class="card" style="border-left:3px solid {{ '#40d040' if msg_ok else '#e05050' }}">
    <p style="color:{{ '#40d040' if msg_ok else '#ff8888' }};margin:0">{{ msg }}</p>
  </div>
  {% endif %}

  <div class="card">
    <h2>Watched Social Domains</h2>
    <p style="color:#888;font-size:13px;margin-bottom:10px">
      Alert fires when a watched device makes more than <strong style="color:#eee">{{ threshold }}</strong>
      allowed queries to these domains within the poll window.
      Remove a domain with ✕. <span style="color:#aaa">Tip: remove YouTube if you allow it.</span>
    </p>
    <div style="margin-bottom:12px">
      {% for d in domains %}
      <span class="tag">{{ d }}
        <form method="post" action="/blocklist/remove" style="display:inline;margin:0">
          <input type="hidden" name="domain" value="{{ d }}">
          <button class="x" type="submit">✕</button>
        </form>
      </span>
      {% endfor %}
    </div>
    <form method="post" action="/blocklist/add">
      <div class="row">
        <input type="text" name="domain" placeholder="e.g. tiktok.com" style="margin:0" required>
        <button class="btn" type="submit">Add</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2>Import from URL</h2>
    <p style="color:#888;font-size:13px;margin-bottom:10px">
      Paste a blocklist URL (plain domains or hosts-file format). All unique domains will be added.
      Works with raw GitHub blocklists, StevenBlack/hosts, jmdugan lists, etc.
    </p>
    <form method="post" action="/blocklist/import_url">
      <input type="text" name="url" placeholder="https://raw.githubusercontent.com/jmdugan/blocklists/master/corporations/facebook/all" required>
      <div class="row" style="margin-top:6px">
        <button class="btn success" type="submit">Import Domains</button>
      </div>
    </form>
    <p style="color:#555;font-size:12px;margin-top:8px">
      Supports: one domain per line · <code style="color:#aaa">0.0.0.0 domain.com</code> ·
      <code style="color:#aaa">127.0.0.1 domain.com</code> · lines starting with # are skipped.
    </p>
  </div>

  <div class="card">
    <h2>Bypass Alert Threshold</h2>
    <p style="color:#888;font-size:13px;margin-bottom:8px">
      Number of allowed social queries in the window before alerting.
    </p>
    <form method="post" action="/config/threshold">
      <div class="row">
        <input type="text" name="threshold" value="{{ threshold }}" style="margin:0;width:80px">
        <button class="btn" type="submit">Update</button>
      </div>
    </form>
  </div>
</div></body></html>"""

_ALERTS_T = """<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alerts — PiParent</title>""" + _BASE_STYLE + """</head><body>""" + _NAV + """
<div class="container">
  <div class="card">
    <h2>Alert History</h2>
    {% if alerts %}
    <form method="post" action="/alerts/clear_all" style="margin-bottom:10px">
      <button class="btn success" type="submit">Clear All</button>
    </form>
    {% for a in alerts %}
    <div class="alert-box {{ 'cleared' if a.cleared else ('doh' if a.type == 'doh_attempt' else '') }}">
      <div class="atype {{ 'doh' if a.type == 'doh_attempt' else '' }}">{{ a.type|upper }} — {{ a.name }}{% if a.cleared %} (cleared){% endif %}</div>
      <div class="amsg">{{ a.message }}</div>
      <div class="atime">{{ a.time[:16] }}</div>
    </div>
    {% endfor %}
    {% else %}
    <p style="color:#40d040">✓ No alerts — all clear!</p>
    {% endif %}
  </div>
</div></body></html>"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_watched():
    try:
        with open(WATCHED_MACS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_watched(devices):
    with open(WATCHED_MACS_FILE, 'w') as f:
        json.dump(devices, f, indent=2)


def _load_social():
    try:
        with open(SOCIAL_DOMAINS_FILE) as f:
            return json.load(f)
    except Exception:
        return {'domains': []}


def _save_social(data):
    with open(SOCIAL_DOMAINS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(watcher, config):
    app = Flask(__name__)

    @app.route('/')
    def dashboard():
        with watcher._lock:
            q = watcher.summary.get('queries', {})
        stats = [
            ('Block rate',   f"{q.get('percent_blocked', 0):.1f}%",  '#e05050'),
            ('Total queries', f"{q.get('total', 0):,}",              '#ffffff'),
            ('Blocked',       f"{q.get('blocked', 0):,}",            '#e05050'),
            ('Query rate',    f"{q.get('frequency', 0):.1f}/min",    '#80d0d0'),
        ]
        watched       = _load_watched()
        active_alerts = watcher.get_active_alerts()
        alert_macs    = {a['mac'].lower() for a in active_alerts}
        return render_template_string(_DASHBOARD_T,
            stats=stats, watched=watched,
            active_alerts=active_alerts, alert_macs=alert_macs,
            monitoring_on=watcher.monitoring_enabled)

    @app.route('/monitoring/toggle', methods=['POST'])
    def monitoring_toggle():
        watcher.toggle_monitoring()
        return redirect('/')

    @app.route('/devices')
    def devices():
        watched = _load_watched()
        watched_macs = {d['mac'].lower() for d in watched}
        with watcher._lock:
            pialert_devices = list(watcher.pialert_devices)
        pialert_devices.sort(key=lambda d: (not d.get('_online', False),
                                            d.get('dev_Name') or 'zzz'))
        return render_template_string(_DEVICES_T,
            watched=watched,
            watched_macs=watched_macs,
            pialert_devices=pialert_devices,
            pialert_configured=watcher.pialert is not None)

    @app.route('/devices/add', methods=['POST'])
    def devices_add():
        name = request.form.get('name', '').strip()
        mac  = request.form.get('mac',  '').strip().lower()
        if name and mac:
            watched = _load_watched()
            if not any(d['mac'].lower() == mac for d in watched):
                watched.append({'name': name, 'mac': mac})
                _save_watched(watched)
        return redirect('/devices')

    @app.route('/devices/remove', methods=['POST'])
    def devices_remove():
        mac = request.form.get('mac', '').strip().lower()
        _save_watched([d for d in _load_watched() if d['mac'].lower() != mac])
        return redirect('/devices')

    @app.route('/blocklist')
    def blocklist():
        social = _load_social()
        msg    = request.args.get('msg', '')
        msg_ok = request.args.get('ok', '1') == '1'
        return render_template_string(_BLOCKLIST_T,
            domains=sorted(social.get('domains', [])),
            threshold=config.get('bypass_threshold', 10),
            msg=msg, msg_ok=msg_ok)

    @app.route('/blocklist/import_url', methods=['POST'])
    def blocklist_import_url():
        url = request.form.get('url', '').strip()
        if not url:
            return redirect('/blocklist?msg=No+URL+provided&ok=0')
        try:
            resp = _requests.get(url, timeout=15, stream=True)
            resp.raise_for_status()
            # Read up to 4 MB
            chunks = []
            size = 0
            for chunk in resp.iter_content(chunk_size=8192):
                chunks.append(chunk)
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    break
            raw = b''.join(chunks).decode('utf-8', errors='ignore')
        except Exception as e:
            return redirect(f'/blocklist?msg=Fetch+failed:+{str(e)[:60]}&ok=0')

        # Parse: hosts-file lines (0.0.0.0 domain / 127.0.0.1 domain) or plain domains
        domain_re = re.compile(
            r'^(?:0\.0\.0\.0|127\.0\.0\.1)\s+(\S+)'   # hosts format
            r'|^([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+)$'
        )
        parsed = set()
        for line in raw.splitlines():
            line = line.strip().lower()
            if not line or line.startswith('#'):
                continue
            m = domain_re.match(line)
            if m:
                domain = m.group(1) or m.group(2)
                # Skip localhost, broadcast, invalid entries
                if domain and domain not in ('localhost', 'broadcasthost', '0.0.0.0'):
                    parsed.add(domain)

        if not parsed:
            return redirect('/blocklist?msg=No+valid+domains+found+in+that+URL&ok=0')

        social  = _load_social()
        existing = set(social.get('domains', []))
        new_domains = parsed - existing
        social['domains'] = sorted(existing | parsed)
        _save_social(social)

        msg = f"Imported+{len(new_domains)}+new+domains+({len(parsed)}+in+list,+{len(existing & parsed)}+already+present)"
        return redirect(f'/blocklist?msg={msg}&ok=1')

    @app.route('/blocklist/add', methods=['POST'])
    def blocklist_add():
        domain = request.form.get('domain', '').strip().lower()
        if domain:
            social = _load_social()
            if domain not in social.get('domains', []):
                social.setdefault('domains', []).append(domain)
                _save_social(social)
        return redirect('/blocklist')

    @app.route('/blocklist/remove', methods=['POST'])
    def blocklist_remove():
        domain = request.form.get('domain', '').strip().lower()
        social = _load_social()
        social['domains'] = [d for d in social.get('domains', []) if d != domain]
        _save_social(social)
        return redirect('/blocklist')

    @app.route('/config/threshold', methods=['POST'])
    def config_threshold():
        try:
            threshold = int(request.form.get('threshold', 10))
            config['bypass_threshold'] = threshold
            watcher.bypass_threshold = threshold
            cfg_path = os.path.join(BASE_DIR, 'config.json')
            with open(cfg_path) as f:
                cfg = json.load(f)
            cfg['bypass_threshold'] = threshold
            with open(cfg_path, 'w') as f:
                json.dump(cfg, f, indent=4)
        except ValueError:
            pass
        return redirect('/blocklist')

    @app.route('/alerts')
    def alerts():
        with watcher._lock:
            all_alerts = list(watcher.alerts)
        return render_template_string(_ALERTS_T, alerts=all_alerts)

    @app.route('/alerts/clear_all', methods=['POST'])
    def alerts_clear_all():
        watcher.clear_all_alerts()
        return redirect('/alerts')

    @app.route('/messages')
    def messages():
        """CYD endpoint — returns monitoring state, stats, hits, and alerts. Newest first."""
        msgs = []
        now_ts = datetime.now().strftime('%H:%M:%S')

        # 1. Monitoring state — always first so CYD always knows current mode
        state_text = 'MONITORING ON' if watcher.monitoring_enabled else 'MONITORING PAUSED'
        msgs.append({
            'type': 'sensor',
            'to':   'STATUS',
            'text': state_text,
            'ts':   now_ts,
        })

        # 2. Pi-hole stats summary
        with watcher._lock:
            q = watcher.summary.get('queries', {})
        if q:
            pct  = q.get('percent_blocked', 0)
            tot  = q.get('total', 0)
            blk  = q.get('blocked', 0)
            freq = q.get('frequency', 0)
            msgs.append({
                'type': 'sensor',
                'to':   'PI-HOLE',
                'text': f"Blocked {pct:.1f}% | {blk:,}/{tot:,} queries | {freq:.1f}/min",
                'ts':   now_ts,
            })

        # 3. Recent watched-device hits (social/DoH queries that slipped through)
        with watcher._lock:
            hits = list(watcher.watched_hits[:10])
        for h in hits:
            label = 'DoH' if h['type'] == 'doh' else 'SOCIAL'
            msgs.append({
                'type': 'dm',
                'to':   h['name'][:15],
                'text': f"[{label}] {h['domain']}",
                'ts':   h['ts'],
            })

        # 4. Alerts — newest first
        with watcher._lock:
            all_alerts = list(watcher.alerts)

        for a in all_alerts:
            atype = a.get('type', '')
            if atype == 'bypass':
                msg_type = 'system'
            elif atype == 'doh_attempt':
                msg_type = 'dm'
            else:
                msg_type = 'sensor'
            try:
                ts = datetime.fromisoformat(a['time']).strftime('%H:%M:%S')
            except Exception:
                ts = a.get('time', '')[:8]
            cleared_tag = ' [cleared]' if a.get('cleared') else ''
            msgs.append({
                'type': msg_type,
                'to':   a.get('name', 'ALERT')[:15],
                'text': f"[{atype.upper()}] {a.get('message', '')}{cleared_tag}",
                'ts':   ts,
            })

        return jsonify({'count': len(msgs), 'messages': msgs})

    return app
