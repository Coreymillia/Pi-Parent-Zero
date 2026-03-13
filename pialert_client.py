"""
pialert_client.py — HTTP client for Pi.Alert PHP API.

Pi.Alert endpoints (all POST):
  POST /pialert/api/  body: api-key=KEY&get=ENDPOINT
  Endpoints: system-status, all-online, all-offline,
             all-new, all-down, recent-events, ip-changes
"""
import requests


class PiAlertClient:
    def __init__(self, base_url, api_key, timeout=8):
        self.api_url = base_url.rstrip('/') + '/pialert/api/'
        self.api_key = api_key
        self.timeout = timeout

    def _post(self, endpoint):
        try:
            r = requests.post(
                self.api_url,
                data={'api-key': self.api_key, 'get': endpoint},
                timeout=self.timeout
            )
            if r.status_code == 200 and r.text.strip():
                return r.json()
        except Exception as e:
            print(f"[PiAlertClient] {endpoint}: {e}")
        return None

    def get_status(self):
        """Returns summary dict: Total, Connected, Disconnected, NewDevices, DownDevices, LastScan."""
        data = self._post('system-status')
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}

    def get_online(self):
        return self._post('all-online') or []

    def get_offline(self):
        return self._post('all-offline') or []

    def get_all_devices(self):
        """Combined online + offline, each entry has _online bool."""
        online  = self.get_online()
        offline = self.get_offline()
        for d in online:
            d['_online'] = True
        for d in offline:
            d['_online'] = False
        return online + offline

    def get_recent_events(self):
        return self._post('recent-events') or []
