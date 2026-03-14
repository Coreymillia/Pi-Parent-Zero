// Portal.h — Captive portal + NVS settings for CYDBotReplies
// Settings: WiFi SSID/pass, bot IP, bot port (default 8766)

#pragma once
#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

// ---- Persisted settings ----
static char   br_wifi_ssid[64]  = "";
static char   br_wifi_pass[64]  = "";
static char   br_bot_ip[64]     = "192.168.0.111";
static uint16_t br_bot_port     = 8766;
static uint8_t  br_brightness   = 200;   // backlight 10–255

static bool   br_has_settings   = false;
static bool   br_force_portal   = false;

static Preferences _prefs;

static void brLoadSettings() {
    _prefs.begin("cydbotreply", true);
    br_has_settings = _prefs.getBool("configured", false);
    strlcpy(br_wifi_ssid, _prefs.getString("ssid",   "").c_str(),   sizeof(br_wifi_ssid));
    strlcpy(br_wifi_pass, _prefs.getString("wpass",  "").c_str(),   sizeof(br_wifi_pass));
    strlcpy(br_bot_ip,    _prefs.getString("botip",  "192.168.0.111").c_str(), sizeof(br_bot_ip));
    br_bot_port   = (uint16_t)_prefs.getUInt("botport", 8766);
    br_brightness = (uint8_t) _prefs.getUChar("bright", 200);
    if (br_brightness < 10) br_brightness = 10;
    _prefs.end();
}

static void brSaveSettings(const char* ssid, const char* wpass,
                            const char* botip, uint16_t botport, uint8_t brightness) {
    _prefs.begin("cydbotreply", false);
    _prefs.putBool("configured", true);
    _prefs.putString("ssid",    ssid);
    _prefs.putString("wpass",   wpass);
    _prefs.putString("botip",   botip);
    _prefs.putUInt("botport",   botport);
    _prefs.putUChar("bright",   brightness);
    _prefs.end();
    strlcpy(br_wifi_ssid, ssid,  sizeof(br_wifi_ssid));
    strlcpy(br_wifi_pass, wpass, sizeof(br_wifi_pass));
    strlcpy(br_bot_ip,    botip, sizeof(br_bot_ip));
    br_bot_port   = botport;
    br_brightness = brightness;
    br_has_settings = true;
}

// ---- Captive portal ----
static WebServer  _portal_server(80);
static DNSServer  _dns_server;

static const char PORTAL_HTML[] PROGMEM = R"rawhtml(
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:sans-serif;background:#111;color:#eee;padding:20px}
 h2{color:#0f0}
 input,button{width:100%;padding:8px;margin:6px 0;box-sizing:border-box;
              background:#222;color:#eee;border:1px solid #555;border-radius:4px;font-size:15px}
 input[type=range]{padding:4px 0;background:none;border:none}
 button{background:#1a6;color:#000;font-weight:bold;border:none;cursor:pointer}
 .row{display:flex;align-items:center;gap:8px}
 .row input[type=range]{flex:1}
 .row output{min-width:32px;text-align:right;color:#0f0}
</style></head><body>
<h2>&#x2B22; BOT REPLIES Setup</h2>
<form method="POST" action="/save">
 <label>WiFi SSID</label>
 <input name="ssid"    value="{SSID}">
 <label>WiFi Password</label>
 <input name="wpass"   type="password" value="">
 <label>Bot IP (Pi)</label>
 <input name="botip"   value="{BOTIP}">
 <label>Bot Port</label>
 <input name="botport" type="number" value="{BOTPORT}">
 <label>Brightness</label>
 <div class="row">
  <input name="bright" type="range" min="10" max="255" value="{BRIGHT}"
         oninput="this.nextElementSibling.value=this.value">
  <output>{BRIGHT}</output>
 </div>
 <button type="submit">Save &amp; Connect</button>
</form>
</body></html>
)rawhtml";

static void _portalHandleRoot() {
    String page = PORTAL_HTML;
    page.replace("{SSID}",    br_wifi_ssid);
    page.replace("{BOTIP}",   br_bot_ip);
    page.replace("{BOTPORT}", String(br_bot_port));
    page.replace("{BRIGHT}",  String(br_brightness));
    _portal_server.send(200, "text/html", page);
}

static void _portalHandleSave() {
    String ssid    = _portal_server.arg("ssid");
    String wpass   = _portal_server.arg("wpass");
    String botip   = _portal_server.arg("botip");
    uint16_t port  = (uint16_t)_portal_server.arg("botport").toInt();
    if (port == 0) port = 8766;
    uint8_t bright = (uint8_t)constrain(_portal_server.arg("bright").toInt(), 10, 255);

    brSaveSettings(ssid.c_str(), wpass.c_str(), botip.c_str(), port, bright);
    _portal_server.send(200, "text/html",
        "<html><body style='background:#111;color:#0f0;font-family:sans-serif;padding:20px'>"
        "<h2>Saved! Rebooting...</h2></body></html>");
    delay(1500);
    ESP.restart();
}

// Run the captive portal until settings are saved
static void brRunPortal(Arduino_GFX* gfx) {
    WiFi.mode(WIFI_AP);
    WiFi.softAP("BotReplies-Setup", "");

    _dns_server.start(53, "*", WiFi.softAPIP());
    _portal_server.on("/",            HTTP_GET,  _portalHandleRoot);
    _portal_server.on("/save",        HTTP_POST, _portalHandleSave);
    _portal_server.onNotFound([]() {
        _portal_server.sendHeader("Location", "http://192.168.4.1/", true);
        _portal_server.send(302, "text/plain", "");
    });
    _portal_server.begin();

    // Draw portal screen
    gfx->fillScreen(0x0000);
    gfx->setTextColor(0x07E0);   // green
    gfx->setTextSize(2);
    gfx->setCursor(10, 10);
    gfx->print("BOT REPLIES Setup");
    gfx->setTextColor(0xFFFF);
    gfx->setTextSize(1);
    gfx->setCursor(10, 50);
    gfx->print("Connect to WiFi:");
    gfx->setCursor(10, 66);
    gfx->setTextColor(0xFD20);   // orange
    gfx->print("BotReplies-Setup");
    gfx->setTextColor(0xFFFF);
    gfx->setCursor(10, 90);
    gfx->print("Then open:");
    gfx->setCursor(10, 106);
    gfx->setTextColor(0x07FF);   // cyan
    gfx->print("http://192.168.4.1");

    while (true) {
        _dns_server.processNextRequest();
        _portal_server.handleClient();
        delay(2);
    }
}

static bool brConnectWiFi(Arduino_GFX* gfx) {
    gfx->fillScreen(0x0000);
    gfx->setTextColor(0x07E0);
    gfx->setTextSize(2);
    gfx->setCursor(10, 10);
    gfx->print("Connecting...");
    gfx->setTextColor(0xAD55);
    gfx->setTextSize(1);
    gfx->setCursor(10, 40);
    gfx->print(br_wifi_ssid);

    WiFi.mode(WIFI_STA);
    WiFi.begin(br_wifi_ssid, br_wifi_pass);
    for (int i = 0; i < 20; i++) {
        if (WiFi.status() == WL_CONNECTED) return true;
        delay(500);
        gfx->print(".");
    }
    return false;
}
