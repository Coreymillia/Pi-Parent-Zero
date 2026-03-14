/**
 * PiParent CYD Display — Split-screen dashboard + per-device row pages (INVERTED)
 *
 * Hardware: ESP32-2432S028 "Cheap Yellow Display"
 *   - ILI9341 320x240 TFT (landscape, INVERTED)
 *   - XPT2046 resistive touch (polling, no IRQ)
 *
 * Page 1: DASHBOARD — split-screen Pi-hole stats (left) + Pi.Alert stats (right)
 *                     + monitoring status bar at bottom
 * Page 2+: Per-device alert/hit rows, one page per watched device
 *
 * Touch left half ← → right half to navigate pages.
 * Polls GET /messages from PiParent every 5 seconds.
 */

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <XPT2046_Touchscreen.h>
#include <SPI.h>
#include <WiFi.h>

// ---- Display (CYD inverted — software SPI bus, display inverted) ----
#define GFX_BL 21
Arduino_DataBus *bus = new Arduino_ESP32SPI(2, 15, 14, 13, 12);
Arduino_GFX *gfx = new Arduino_ILI9341(bus, GFX_NOT_DEFINED, 1);

// ---- Touch (CYD inverted — polling mode, no IRQ) ----
#define XPT2046_CS   33
#define XPT2046_CLK  25
#define XPT2046_MOSI 32
#define XPT2046_MISO 39
SPIClass touchSPI(VSPI);
XPT2046_Touchscreen ts(XPT2046_CS);

#include "Portal.h"
#include "BotMessages.h"

// ---- Colors (RGB565) ----
#define COL_BG       0x0000
#define COL_HEADER   0x0210
#define COL_HDR_TXT  0x07E0
#define COL_DIM      0x2104
#define COL_WHITE    0xFFFF
#define COL_GREEN    0x07E0
#define COL_CYAN     0x07FF
#define COL_ORANGE   0xFC60
#define COL_YELLOW   0xFFE0
#define COL_GREY     0x6B4D
#define COL_RED      0xF800

// ---- Layout ----
#define SCREEN_W    320
#define SCREEN_H    240
#define HEADER_H    28
#define CONTENT_Y   (HEADER_H + 1)
#define CONTENT_H   (SCREEN_H - CONTENT_Y)
#define MARGIN_X    4
#define ROW_H       16
#define MAX_ROWS    (CONTENT_H / ROW_H)

// ---- Page grouping ----
#define MAX_PAGES     12
#define MAX_ROWS_PAGE MAX_ROWS

struct Page {
    char title[16];
    int  indices[MAX_ROWS_PAGE];
    int  count;
};

static Page pages[MAX_PAGES];
static int  page_count = 0;
static int  cur_page   = 0;

// Dashboard source message indices
static int g_status_idx  = -1;
static int g_pihole_idx  = -1;
static int g_pialert_idx = -1;

// ---- State ----
static bool          needs_redraw  = true;
static unsigned long last_fetch_ms = 0;
#define FETCH_INTERVAL_MS 5000

// ---- Helpers ----
static uint16_t msgColor(const char* type) {
    if (strncmp(type, "dm",     2) == 0) return COL_GREEN;
    if (strncmp(type, "system", 6) == 0) return COL_ORANGE;
    if (strncmp(type, "sensor", 6) == 0) return COL_YELLOW;
    return COL_WHITE;
}

static const char* msgLabel(const char* type) {
    if (strncmp(type, "dm",     2) == 0) return "HIT";
    if (strncmp(type, "system", 6) == 0) return "ALT";
    if (strncmp(type, "sensor", 6) == 0) return "INF";
    return "MSG";
}

// Split a " | " delimited string into parts
static void splitParts(const char* text, char parts[][48], int maxParts, int& count) {
    count = 0;
    const char* p = text;
    while (count < maxParts) {
        const char* found = strstr(p, " | ");
        if (!found) {
            int len = strlen(p); if (len > 47) len = 47;
            strncpy(parts[count], p, len); parts[count][len] = '\0';
            count++; break;
        }
        int len = (int)(found - p); if (len > 47) len = 47;
        strncpy(parts[count], p, len); parts[count][len] = '\0';
        count++;
        p = found + 3;
    }
}

// ---- Page building ----
static void buildPages() {
    char prev_title[16] = "";
    if (page_count > 0 && cur_page < page_count)
        strlcpy(prev_title, pages[cur_page].title, sizeof(prev_title));

    page_count = 0;
    memset(pages, 0, sizeof(pages));
    g_status_idx = g_pihole_idx = g_pialert_idx = -1;

    // Pass 1 — find dashboard source messages
    for (int i = 0; i < bm_count; i++) {
        const char* to = bm_msgs[i].to;
        if (strcmp(to, "STATUS")  == 0) { g_status_idx  = i; continue; }
        if (strcmp(to, "PI-HOLE") == 0) { g_pihole_idx  = i; continue; }
        if (strcmp(to, "PIALERT") == 0) { g_pialert_idx = i; continue; }
    }

    // Always create DASHBOARD as page 0
    strlcpy(pages[page_count].title, "DASHBOARD", sizeof(pages[page_count].title));
    pages[page_count].count = 0;
    page_count++;

    // Pass 2 — group remaining messages by device
    for (int i = 0; i < bm_count; i++) {
        const char* to = bm_msgs[i].to;
        if (strcmp(to, "STATUS")  == 0) continue;
        if (strcmp(to, "PI-HOLE") == 0) continue;
        if (strcmp(to, "PIALERT") == 0) continue;

        int pi = -1;
        for (int j = 1; j < page_count; j++) {
            if (strncmp(pages[j].title, to, sizeof(pages[j].title) - 1) == 0) {
                pi = j; break;
            }
        }
        if (pi < 0) {
            if (page_count >= MAX_PAGES) continue;
            pi = page_count++;
            strlcpy(pages[pi].title, to, sizeof(pages[pi].title));
            pages[pi].count = 0;
        }
        if (pages[pi].count < MAX_ROWS_PAGE)
            pages[pi].indices[pages[pi].count++] = i;
    }

    // Stay on same device page after refresh
    cur_page = 0;
    if (prev_title[0]) {
        for (int j = 0; j < page_count; j++) {
            if (strncmp(pages[j].title, prev_title, sizeof(pages[j].title) - 1) == 0) {
                cur_page = j; break;
            }
        }
    }
}

// ---- Draw header ----
static void drawHeader() {
    gfx->fillRect(0, 0, SCREEN_W, HEADER_H, COL_HEADER);
    gfx->setTextSize(2);
    gfx->setTextColor(COL_HDR_TXT, COL_HEADER);
    gfx->setCursor(6, 6);
    gfx->print(page_count > 0 ? pages[cur_page].title : "PIPARENT");

    char nav[10];
    snprintf(nav, sizeof(nav), "%d/%d", cur_page + 1, page_count);
    gfx->setTextSize(1);
    gfx->setTextColor(COL_WHITE, COL_HEADER);
    gfx->setCursor(SCREEN_W - (int)strlen(nav) * 6 - MARGIN_X, 11);
    gfx->print(nav);
}

// ---- Draw split-screen dashboard ----
static void drawDashboard() {
    gfx->fillRect(0, CONTENT_Y, SCREEN_W, CONTENT_H, COL_BG);

    const int MID_X = SCREEN_W / 2;       // 160
    const int R_X   = MID_X + MARGIN_X;   // 164

    // Vertical divider
    gfx->drawFastVLine(MID_X, CONTENT_Y, CONTENT_H - 32, COL_DIM);

    // ── Column headers ────────────────────────────────
    int cy = CONTENT_Y + 4;
    gfx->setTextSize(1);
    gfx->setTextColor(COL_GREEN, COL_BG);
    gfx->setCursor(MARGIN_X, cy);
    gfx->print("PI-HOLE");

    gfx->setTextColor(COL_CYAN, COL_BG);
    gfx->setCursor(R_X, cy);
    gfx->print("PI.ALERT");

    cy += 10;
    gfx->drawFastHLine(MARGIN_X, cy, SCREEN_W - MARGIN_X * 2, COL_DIM);
    cy += 6;

    // ── PI-HOLE column ────────────────────────────────
    if (g_pihole_idx >= 0) {
        char parts[3][48]; int np = 0;
        splitParts(bm_msgs[g_pihole_idx].text, parts, 3, np);

        // parts[0] = "Blocked 29.7%"  → extract "29.7%"
        if (np > 0) {
            const char* pct = strstr(parts[0], " ");
            if (pct) pct++; else pct = parts[0];
            gfx->setTextSize(2);
            gfx->setTextColor(COL_ORANGE, COL_BG);
            gfx->setCursor(MARGIN_X, cy);
            gfx->print(pct);
        }
        int left_y = cy + 22;
        // parts[1] = "11,508/38,760 queries" — truncate to fit
        if (np > 1) {
            char tmp[26]; strncpy(tmp, parts[1], 25); tmp[25] = '\0';
            gfx->setTextSize(1);
            gfx->setTextColor(COL_WHITE, COL_BG);
            gfx->setCursor(MARGIN_X, left_y);
            gfx->print(tmp);
            left_y += ROW_H;
        }
        // parts[2] = "0.1/min"
        if (np > 2) {
            gfx->setTextSize(1);
            gfx->setTextColor(COL_YELLOW, COL_BG);
            gfx->setCursor(MARGIN_X, left_y);
            gfx->print(parts[2]);
        }
    } else {
        gfx->setTextSize(1);
        gfx->setTextColor(COL_GREY, COL_BG);
        gfx->setCursor(MARGIN_X, cy);
        gfx->print("No data");
    }

    // ── PI.ALERT column ───────────────────────────────
    if (g_pialert_idx >= 0) {
        char parts[4][48]; int np = 0;
        splitParts(bm_msgs[g_pialert_idx].text, parts, 4, np);
        // parts: "24 online", "25 offline", "39 new", "21:45:00"

        int right_y = cy;
        for (int i = 0; i < np && i < 3; i++) {
            uint16_t col = COL_WHITE;
            if (strstr(parts[i], "online"))  col = COL_GREEN;
            if (strstr(parts[i], "offline")) col = COL_GREY;
            if (strstr(parts[i], "new")) {
                col = (atoi(parts[i]) > 0) ? COL_ORANGE : COL_GREY;
            }

            // Big number for online count, smaller for rest
            if (i == 0) {
                char num[8] = ""; int n = atoi(parts[i]);
                snprintf(num, sizeof(num), "%d", n);
                gfx->setTextSize(2);
                gfx->setTextColor(col, COL_BG);
                gfx->setCursor(R_X, right_y);
                gfx->print(num);
                gfx->setTextSize(1);
                gfx->setTextColor(COL_GREY, COL_BG);
                gfx->setCursor(R_X + strlen(num) * 12 + 3, right_y + 6);
                gfx->print("online");
                right_y += 22;
            } else {
                char tmp[26]; strncpy(tmp, parts[i], 25); tmp[25] = '\0';
                gfx->setTextSize(1);
                gfx->setTextColor(col, COL_BG);
                gfx->setCursor(R_X, right_y);
                gfx->print(tmp);
                right_y += ROW_H;
            }
        }
        // Last scan time (parts[3])
        if (np >= 4) {
            gfx->setTextSize(1);
            gfx->setTextColor(COL_GREY, COL_BG);
            gfx->setCursor(R_X, right_y);
            char scan[16];
            snprintf(scan, sizeof(scan), "scan %s", parts[3]);
            gfx->print(scan);
        }
    } else {
        gfx->setTextSize(1);
        gfx->setTextColor(COL_GREY, COL_BG);
        gfx->setCursor(R_X, cy);
        gfx->print("No data");
    }

    // ── Monitoring status bar (full width, bottom) ────
    int bar_y = SCREEN_H - 28;
    gfx->drawFastHLine(MARGIN_X, bar_y, SCREEN_W - MARGIN_X * 2, COL_DIM);
    bar_y += 7;

    if (g_status_idx >= 0) {
        bool on = strstr(bm_msgs[g_status_idx].text, "ON") != nullptr
               && strstr(bm_msgs[g_status_idx].text, "PAUSED") == nullptr;
        gfx->setTextSize(1);
        gfx->setTextColor(on ? COL_GREEN : COL_ORANGE, COL_BG);
        gfx->setCursor(MARGIN_X, bar_y);
        gfx->print(on ? "# MONITORING ON" : "! MONITORING PAUSED");
    }
}

// ---- Draw device row page ----
static void drawRows() {
    gfx->fillRect(0, CONTENT_Y, SCREEN_W, CONTENT_H, COL_BG);

    if (page_count <= 1) {
        gfx->setTextSize(1);
        gfx->setTextColor(COL_GREY, COL_BG);
        gfx->setCursor(MARGIN_X, CONTENT_Y + 20);
        gfx->print("No device alerts yet.");
        return;
    }

    const int BADGE_W = 5 * 6 + 4;
    const int TS_W    = 8 * 6 + MARGIN_X;
    const int TXT_MAX = (SCREEN_W - MARGIN_X - BADGE_W - TS_W) / 6;

    Page& p = pages[cur_page];
    int cy = CONTENT_Y + 2;

    for (int i = 0; i < p.count; i++) {
        if (cy + ROW_H > SCREEN_H - 1) break;
        BotMsg& m    = bm_msgs[p.indices[i]];
        uint16_t col = msgColor(m.type);

        char badge[8];
        snprintf(badge, sizeof(badge), "[%s]", msgLabel(m.type));
        gfx->setTextSize(1);
        gfx->setTextColor(col, COL_BG);
        gfx->setCursor(MARGIN_X, cy);
        gfx->print(badge);

        char txt[64] = "";
        int copy = strlen(m.text); if (copy > TXT_MAX) copy = TXT_MAX;
        strncpy(txt, m.text, copy); txt[copy] = '\0';
        gfx->setTextColor(COL_WHITE, COL_BG);
        gfx->setCursor(MARGIN_X + BADGE_W, cy);
        gfx->print(txt);

        if (m.ts[0]) {
            gfx->setTextColor(COL_GREY, COL_BG);
            gfx->setCursor(SCREEN_W - TS_W + MARGIN_X, cy);
            gfx->print(m.ts);
        }
        cy += ROW_H;
        if (i < p.count - 1)
            gfx->drawFastHLine(MARGIN_X, cy - 1, SCREEN_W - MARGIN_X * 2, COL_DIM);
    }
}

// ---- Full redraw ----
static void fullRedraw() {
    drawHeader();
    if (page_count > 0 && strcmp(pages[cur_page].title, "DASHBOARD") == 0)
        drawDashboard();
    else
        drawRows();
    needs_redraw = false;
}

static void showStatus(const char* line1, const char* line2 = nullptr) {
    gfx->fillScreen(COL_BG);
    gfx->setTextColor(COL_GREY); gfx->setTextSize(1);
    gfx->setCursor(10, 110); gfx->print(line1);
    if (line2) { gfx->setCursor(10, 122); gfx->print(line2); }
}

// ---- Touch ----
static unsigned long last_touch_ms = 0;
#define TOUCH_DEBOUNCE_MS 450

static void handleTouch() {
    if (!ts.touched()) return;
    unsigned long now = millis();
    if (now - last_touch_ms < TOUCH_DEBOUNCE_MS) return;
    last_touch_ms = now;

    TS_Point p = ts.getPoint();
    int tx = map(p.x, 200, 3800, 0, SCREEN_W);

    if (tx < SCREEN_W / 2) {
        if (cur_page > 0) { cur_page--; needs_redraw = true; }
    } else {
        if (cur_page < page_count - 1) { cur_page++; needs_redraw = true; }
    }
}

// ---- Setup ----
void setup() {
    Serial.begin(115200);
    if (!gfx->begin()) Serial.println("gfx->begin() failed!");
    gfx->invertDisplay(true);
    gfx->fillScreen(COL_BG);

    // PWM backlight
    const int PWM_CH = 0, PWM_FREQ = 5000, PWM_BITS = 8;
    ledcSetup(PWM_CH, PWM_FREQ, PWM_BITS);
    ledcAttachPin(GFX_BL, PWM_CH);
    ledcWrite(PWM_CH, 255);

    pinMode(0, INPUT_PULLUP);

    touchSPI.begin(XPT2046_CLK, XPT2046_MISO, XPT2046_MOSI, XPT2046_CS);
    ts.begin(touchSPI); ts.setRotation(1);

    brLoadSettings();

    bool showPortal = !br_has_settings;
    showStatus("Hold BOOT to change settings...");
    for (int i = 0; i < 30 && !showPortal; i++) {
        if (digitalRead(0) == LOW) showPortal = true;
        delay(100);
    }
    if (showPortal) brRunPortal(gfx);

    if (!brConnectWiFi(gfx)) {
        showStatus("WiFi failed.", "Hold BOOT on reboot to reconfigure.");
        delay(5000); ESP.restart();
    }

    Serial.printf("WiFi OK — polling http://%s:%u/messages\n", br_bot_ip, br_bot_port);
    showStatus("Connected.", "Fetching from PiParent...");
    bmFetch(br_bot_ip, br_bot_port);
    buildPages();
    fullRedraw();
}

// ---- Loop ----
void loop() {
    handleTouch();

    unsigned long now = millis();
    if (now - last_fetch_ms >= FETCH_INTERVAL_MS) {
        last_fetch_ms = now;
        if (bmFetch(br_bot_ip, br_bot_port)) {
            buildPages();
            needs_redraw = true;
        }
    }

    if (needs_redraw) fullRedraw();
    delay(20);
}
