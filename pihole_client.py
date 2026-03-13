"""
pihole_client.py — Pi-hole v6 API wrapper with session-token auth.
"""
import time
import requests


class PiholeClient:
    def __init__(self, base_url, password):
        self.base_url = base_url.rstrip('/')
        self.password = password
        self._sid = None
        self._sid_expires = 0

    def _get_sid(self):
        now = time.time()
        if self._sid and now < self._sid_expires:
            return self._sid
        try:
            r = requests.post(
                f"{self.base_url}/auth",
                json={'password': self.password},
                timeout=8
            )
            r.raise_for_status()
            sid = r.json().get('session', {}).get('sid')
            if sid:
                self._sid = sid
                self._sid_expires = now + 1500  # refresh before 30-min expiry
                print("[PiholeClient] Auth OK — new session token")
            return sid
        except Exception as e:
            print(f"[PiholeClient] Auth error: {e}")
            return None

    def get(self, endpoint, params=None):
        """GET an API endpoint, handling token expiry with one retry."""
        sid = self._get_sid()
        if not sid:
            return None
        try:
            url = f"{self.base_url}/{endpoint.lstrip('/')}"
            r = requests.get(url, headers={'sid': sid}, params=params, timeout=10)
            if r.status_code == 401:
                self._sid = None
                self._sid_expires = 0
                sid = self._get_sid()
                if not sid:
                    return None
                r = requests.get(url, headers={'sid': sid}, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[PiholeClient] GET {endpoint} error: {e}")
            return None

    def get_summary(self):
        return self.get('stats/summary')

    def get_queries(self, count=200):
        return self.get('queries', params={'count': count})

    def get_network_devices(self):
        return self.get('network/devices')

    def get_top_domains(self, blocked=True, count=5):
        return self.get('stats/top_domains', params={
            'blocked': str(blocked).lower(), 'count': count
        })

    def get_top_clients(self, count=10):
        return self.get('stats/top_clients', params={'count': count})
