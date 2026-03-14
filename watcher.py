"""
watcher.py — MAC bypass detection and MAC/IP change alerting.

Polls Pi-hole v6 API every poll_interval_s seconds.
Detects:
  - Watched device making allowed queries to social domains (bypass alert)
  - Watched device IP address change
"""
import json
import os
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from pihole_client import PiholeClient
from pialert_client import PiAlertClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHED_MACS_FILE   = os.path.join(BASE_DIR, 'watched_macs.json')
SOCIAL_DOMAINS_FILE = os.path.join(BASE_DIR, 'social_domains.json')
ALERTS_FILE         = os.path.join(BASE_DIR, 'alerts.json')

# Known DNS-over-HTTPS provider domains.
# A watched device querying these (and NOT being blocked) means it may be
# routing DNS outside Pi-hole entirely.
DOH_PROVIDERS = {
    'dns.google', 'dns64.dns.google',
    'cloudflare-dns.com', '1dot1dot1dot1.cloudflare.com',
    'mozilla.cloudflare-dns.com',
    'dns.quad9.net', 'dns9.quad9.net', 'dns11.quad9.net',
    'dns.nextdns.io',
    'dns.adguard.com', 'dns.adguard-dns.com',
    'doh.opendns.com',
    'doh.xfinity.com',
    'doh.mullvad.net',
    'doh.dns.apple.com',
    'doh.cleanbrowsing.org',
    'doh.li',
    'dns.alternate-dns.com',
}


class Watcher:
    def __init__(self, config):
        self.client = PiholeClient(
            config['pihole_base_url'],
            config['pihole_password']
        )
        self.poll_interval       = config.get('poll_interval_s', 30)
        self.query_refresh       = config.get('query_refresh_interval_s', 5)
        self.bypass_threshold    = config.get('bypass_threshold', 10)
        self.query_window_min    = config.get('query_window_min', 5)

        pa_url = config.get('pialert_url', '')
        pa_key = config.get('pialert_api_key', '')
        self.pialert = PiAlertClient(pa_url, pa_key) if (pa_url and pa_key) else None

        self._lock = threading.Lock()
        self._running = False
        self._thread = None

        self.alerts = []            # list of alert dicts
        self.recent_queries = []    # last 50 queries for live feed
        self.summary = {}           # Pi-hole summary stats
        self.ip_to_mac = {}         # IP -> MAC from network device list
        self.device_history = {}    # mac -> {'ips': set, 'last_seen': float}

        self.pialert_status  = {}   # Pi.Alert dashboard summary
        self.pialert_devices = []   # all online+offline devices from Pi.Alert
        self.pialert_events  = []   # recent events from Pi.Alert

        self.social_bypass_times = {}  # mac -> datetime of last unblocked social query

        self.monitoring_enabled = True   # global alert toggle (pauses alert generation when False)
        self.watched_hits = []           # recent allowed social/DoH query hits for CYD feed

    # ── Watched devices ───────────────────────────────────────────────────────

    def load_watched(self):
        try:
            with open(WATCHED_MACS_FILE) as f:
                return json.load(f)
        except Exception:
            return []

    def save_watched(self, devices):
        with open(WATCHED_MACS_FILE, 'w') as f:
            json.dump(devices, f, indent=2)

    # ── Social domains ────────────────────────────────────────────────────────

    def load_social_domains(self):
        try:
            with open(SOCIAL_DOMAINS_FILE) as f:
                return set(json.load(f).get('domains', []))
        except Exception:
            return set()

    # ── Monitoring toggle ─────────────────────────────────────────────────────

    def toggle_monitoring(self):
        self.monitoring_enabled = not self.monitoring_enabled
        state = 'ON' if self.monitoring_enabled else 'OFF'
        print(f"[Watcher] Monitoring turned {state}")
        return self.monitoring_enabled

    # ── Alerts ────────────────────────────────────────────────────────────────

    def _add_alert(self, mac, name, alert_type, message):
        """Add alert, suppressing duplicates within 10 minutes. Skipped when monitoring is off."""
        if not self.monitoring_enabled:
            return
        alert = {
            'mac': mac,
            'name': name,
            'type': alert_type,
            'message': message,
            'time': datetime.now().isoformat(),
            'cleared': False,
        }
        cutoff = datetime.now() - timedelta(minutes=10)
        with self._lock:
            recent = [
                a for a in self.alerts
                if a['mac'] == mac
                and a['type'] == alert_type
                and not a['cleared']
                and datetime.fromisoformat(a['time']) > cutoff
            ]
            if not recent:
                self.alerts.insert(0, alert)
                self.alerts = self.alerts[:50]
        self._save_alerts()
        print(f"[Watcher] ALERT {alert_type} — {name} ({mac}): {message}")

    def _save_alerts(self):
        try:
            with self._lock:
                data = list(self.alerts)
            with open(ALERTS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load_alerts(self):
        try:
            with open(ALERTS_FILE) as f:
                with self._lock:
                    self.alerts = json.load(f)
        except Exception:
            pass

    def clear_alert(self, index):
        with self._lock:
            if 0 <= index < len(self.alerts):
                self.alerts[index]['cleared'] = True
        self._save_alerts()

    def clear_all_alerts(self):
        with self._lock:
            for a in self.alerts:
                a['cleared'] = True
        self._save_alerts()

    def has_active_alerts(self):
        with self._lock:
            return any(not a['cleared'] for a in self.alerts)

    def get_active_alerts(self):
        with self._lock:
            return [a for a in self.alerts if not a['cleared']]

    def get_social_bypass_recent(self, mac, window_hours=1):
        """True if a watched social domain query slipped through for this MAC in the last hour."""
        with self._lock:
            t = self.social_bypass_times.get(mac.lower())
        if t is None:
            return False
        return datetime.now() - t < timedelta(hours=window_hours)

    # ── Fast poll — queries only (every query_refresh_interval_s) ─────────────

    def _fast_poll(self):
        social_domains = self.load_social_domains()
        watched        = self.load_watched()
        watched_macs   = {d['mac'].lower(): d for d in watched}

        queries_data = self.client.get_queries(count=200)
        if not queries_data:
            return
        queries = queries_data.get('queries', [])
        with self._lock:
            self.recent_queries = queries[:50]
            ip_to_mac = dict(self.ip_to_mac)

        if not watched_macs or not social_domains:
            return

        window_start      = time.time() - (self.query_window_min * 60)
        mac_allowed_social = defaultdict(int)
        now               = datetime.now()

        for q in queries:
            blocked = 'BLOCKED' in (q.get('status') or '').upper()
            domain  = (q.get('domain') or '').lower()
            client  = q.get('client', {})
            qip     = client.get('ip', '') if isinstance(client, dict) else str(client)
            mac     = ip_to_mac.get(qip, '').lower()
            if not mac or mac not in watched_macs:
                continue
            is_social = any(domain == sd or domain.endswith('.' + sd) for sd in social_domains)
            if not is_social:
                continue
            if not blocked:
                # Record that a social query slipped through right now
                with self._lock:
                    self.social_bypass_times[mac] = now
                if (q.get('time') or 0) >= window_start:
                    mac_allowed_social[mac] += 1
                    name = watched_macs[mac]['name']
                    hit = {'name': name, 'domain': domain, 'type': 'social', 'ts': now.strftime('%H:%M:%S')}
                    with self._lock:
                        self.watched_hits.insert(0, hit)
                        self.watched_hits = self.watched_hits[:30]

        for mac, count in mac_allowed_social.items():
            if count >= self.bypass_threshold:
                name = watched_macs[mac]['name']
                self._add_alert(
                    mac, name, 'bypass',
                    f"{count} allowed social media queries in {self.query_window_min}min window"
                )

        # DoH detection — any unblocked query to a known DoH provider
        for q in queries:
            if 'BLOCKED' in (q.get('status') or '').upper():
                continue
            domain = (q.get('domain') or '').lower()
            is_doh = any(domain == d or domain.endswith('.' + d) for d in DOH_PROVIDERS)
            if not is_doh:
                continue
            client = q.get('client', {})
            qip    = client.get('ip', '') if isinstance(client, dict) else str(client)
            mac    = ip_to_mac.get(qip, '').lower()
            if mac and mac in watched_macs:
                name = watched_macs[mac]['name']
                hit = {'name': name, 'domain': domain, 'type': 'doh', 'ts': datetime.now().strftime('%H:%M:%S')}
                with self._lock:
                    self.watched_hits.insert(0, hit)
                    self.watched_hits = self.watched_hits[:30]
                self._add_alert(
                    mac, name, 'doh_attempt',
                    f"Unblocked DoH query to {domain} — DNS may be bypassing Pi-hole"
                )

    # ── Slow poll — network, Pi.Alert, summary (every poll_interval_s) ────────

    def _poll(self):
        """Slow poll — network mapping, summary stats, Pi.Alert data."""
        watched      = self.load_watched()
        watched_macs = {d['mac'].lower(): d for d in watched}

        # Update MAC→IP mapping and detect IP changes
        net_data = self.client.get_network_devices()
        new_ip_to_mac = {}
        if net_data:
            for dev in net_data.get('devices', []):
                mac = (dev.get('hwaddr') or dev.get('mac') or '').lower()
                ips = dev.get('ip', [])
                if isinstance(ips, str):
                    ips = [ips]
                ips = [ip for ip in ips if ip]
                for ip in ips:
                    if mac:
                        new_ip_to_mac[ip] = mac
                if mac and mac in watched_macs:
                    name = watched_macs[mac]['name']
                    current_ips = set(ips)
                    if mac in self.device_history:
                        prev_ips = self.device_history[mac].get('ips', set())
                        if current_ips and prev_ips and current_ips != prev_ips:
                            self._add_alert(
                                mac, name, 'ip_change',
                                f"IP changed from {', '.join(prev_ips)} to {', '.join(current_ips)}"
                            )
                    self.device_history[mac] = {
                        'ips': current_ips,
                        'last_seen': time.time()
                    }

        # Supplement with Pi.Alert IP→MAC (Pi-hole network API is often empty)
        for dev in (self.pialert_devices or []):
            m  = (dev.get('dev_MAC') or '').lower()
            ip = dev.get('dev_LastIP', '')
            if m and ip and ip not in new_ip_to_mac:
                new_ip_to_mac[ip] = m

        self.ip_to_mac = new_ip_to_mac

        # Summary stats
        summary = self.client.get_summary()
        if summary:
            with self._lock:
                self.summary = summary

        # Pi.Alert
        if self.pialert:
            try:
                status = self.pialert.get_status()
                if status:
                    with self._lock:
                        self.pialert_status = status
                devices = self.pialert.get_all_devices()
                with self._lock:
                    self.pialert_devices = devices
                events = self.pialert.get_recent_events()
                with self._lock:
                    self.pialert_events = events
            except Exception as e:
                print(f"[Watcher] Pi.Alert poll error: {e}")

    def _run_slow(self):
        self.load_alerts()
        print("[Watcher] Slow poll started (every {}s)".format(self.poll_interval))
        while self._running:
            try:
                self._poll()
            except Exception as e:
                print(f"[Watcher] Slow poll error: {e}")
            time.sleep(self.poll_interval)

    def _run_fast(self):
        print("[Watcher] Fast query poll started (every {}s)".format(self.query_refresh))
        time.sleep(2)  # let slow poll build ip_to_mac first
        while self._running:
            try:
                self._fast_poll()
            except Exception as e:
                print(f"[Watcher] Fast poll error: {e}")
            time.sleep(self.query_refresh)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run_slow, daemon=True)
        self._thread.start()
        threading.Thread(target=self._run_fast, daemon=True).start()

    def stop(self):
        self._running = False
