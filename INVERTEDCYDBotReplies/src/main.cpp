/**
 * PiParent CYD Display — Per-device row pages
 *
 * Hardware: ESP32-2432S028 "Cheap Yellow Display"
 *   - ILI9341 320x240 TFT (landscape)
 *   - XPT2046 resistive touch
 *
 * Display: One page per device/category, messages listed as rows.
 *   Touch left half  → previous device page
 *   Touch right half → next device page
 *
 * Polls GET /messages from PiParent every 5 seconds.
 * Groups messages by the `to` field — one page per unique device name.
 */

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <XPT2046_Touchscreen.h>
#include <SPI.h>
#include <WiFi.h>

// ---- Display pins (CYD standard) ----
#define GFX_BL 21
Arduino_DataBus *bus = new Arduino_ESP32SPI(2, 15, 14, 13, 12);
Arduino_GFX *gfx = new Arduino_ILI9341(bus, GFX_NOT_DEFINED, 1 /* landscape */);

// ---- Touch pins (CYD standard) ----
#define XPT2046_CS   33
#define XPT2046_CLK  25
#define XPT2046_MOSI 32
#define XPT2046_MISO 39
SPIClass touchSPI(VSPI);
XPT2046_Touchscreen ts(XPT2046_CS);

#include "Portal.h"
#include "BotMessages.h"

// ---- Colors (RGB565) ----
#define COL_BG         0x0000
#define COL_HEADER     0x0210   // dark green
#define COL_HEADER_TXT 0x07E0   // bright green
#define COL_DIM        0x2104   // very dark grey — row separator
#define COL_WHITE      0xFFFF
#define COL_DM         0x07E0   // green  — social/DoH hit
#define COL_SYSTEM     0xFC60   // orange — bypass alert
#define COL_SENSOR     0xFFE0   // yellow — status/stats
#define COL_GREY       0x6B4D   // dim grey

// ---- Layout ----
#define SCREEN_W   320
#define SCREEN_H   240
#define HEADER_H   28
#define CONTENT_Y  (HEADER_H + 1)
#define CONTENT_H  (SCREEN_H - CONTENT_Y)
#define MARGIN_X   4
#define ROW_H      16                        // px per row at textSize=1
#define MAX_ROWS   (CONTENT_H / ROW_H)       // 13 rows

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

// ---- State ----
static bool          needs_redraw  = true;
static unsigned long last_fetch_ms = 0;
#define FETCH_INTERVAL_MS 5000

// ---- Helpers ----
static uint16_t msgColor(const char* type) {
    if (strncmp(type, "dm",     2) == 0) return COL_DM;
    if (strncmp(type, "system", 6) == 0) return COL_SYSTEM;
    if (strncmp(type, "sensor", 6) == 0) return COL_SENSOR;
    return COL_WHITE;
}

static const char* msgLabel(const char* type) {
    if (strncmp(type, "dm",     2) == 0) return "HIT";
    if (strncmp(type, "system", 6) == 0) return "ALT";
    if (strncmp(type, "sensor", 6) == 0) return "INF";
    return "MSG";
}

// Build per-device pages from fetched messages.
// Tries to keep cur_page on the same device title after a refresh.
static void buildPages() {
    char prev_title[16] = "";
    if (page_count > 0 && cur_page < page_count)
        strlcpy(prev_title, pages[cur_page].title, sizeof(prev_title));

    page_count = 0;
    memset(pages, 0, sizeof(pages));

    for (int i = 0; i < bm_count; i++) {
        const char* to = bm_msgs[i].to;

        // Find existing page for this device
        int pi = -1;
        for (int j = 0; j < page_count; j++) {
            if (strncmp(pages[j].title, to, sizeof(pages[j].title) - 1) == 0) {
                pi = j;
                break;
            }
        }
        // Create a new page if needed
        if (pi < 0) {
            if (page_count >= MAX_PAGES) continue;
            pi = page_count++;
            strlcpy(pages[pi].title, to, sizeof(pages[pi].title));
            pages[pi].count = 0;
        }
        if (pages[pi].count < MAX_ROWS_PAGE)
            pages[pi].indices[pages[pi].count++] = i;
    }

    // Stay on the same device page if it still exists, else go to 0
    cur_page = 0;
    if (prev_title[0]) {
        for (int j = 0; j < page_count; j++) {
            if (strncmp(pages[j].title, prev_title, sizeof(pages[j].title) - 1) == 0) {
                cur_page = j;
                break;
            }
        }
    }
}

// Draw header: device name (large) + "cur/total" page counter (small, right)
static void drawHeader() {
    gfx->fillRect(0, 0, SCREEN_W, HEADER_H, COL_HEADER);

    gfx->setTextSize(2);
    gfx->setTextColor(COL_HEADER_TXT, COL_HEADER);
    gfx->setCursor(6, 6);
    if (page_count > 0)
        gfx->print(pages[cur_page].title);
    else
        gfx->print("PIPARENT");

    char nav[12];
    snprintf(nav, sizeof(nav), page_count > 0 ? "%d/%d" : "--",
             cur_page + 1, page_count);
    gfx->setTextSize(1);
    gfx->setTextColor(COL_WHITE, COL_HEADER);
    gfx->setCursor(SCREEN_W - (int)strlen(nav) * 6 - MARGIN_X, 11);
    gfx->print(nav);
}

// Draw all message rows for the current device page
static void drawRows() {
    gfx->fillRect(0, CONTENT_Y, SCREEN_W, CONTENT_H, COL_BG);

    if (page_count == 0) {
        gfx->setTextColor(COL_GREY, COL_BG);
        gfx->setTextSize(1);
        gfx->setCursor(MARGIN_X, CONTENT_Y + 20);
        gfx->print("No messages yet.");
        gfx->setCursor(MARGIN_X, CONTENT_Y + 34);
        gfx->print("Waiting for PiParent...");
        return;
    }

    // Column widths at textSize=1 (6px per char):
    //   badge "[ALT]" = 5 chars × 6 + 4 gap = 34px
    //   timestamp "HH:MM:SS" = 8 chars × 6 + MARGIN_X = 52px
    //   text area = remaining
    const int BADGE_W = 5 * 6 + 4;
    const int TS_W    = 8 * 6 + MARGIN_X;
    const int TXT_W   = SCREEN_W - MARGIN_X - BADGE_W - TS_W;
    const int TXT_MAX = TXT_W / 6;

    Page& p = pages[cur_page];
    int cy = CONTENT_Y + 2;

    for (int i = 0; i < p.count; i++) {
        if (cy + ROW_H > SCREEN_H - 1) break;

        BotMsg&  m   = bm_msgs[p.indices[i]];
        uint16_t col = msgColor(m.type);

        // Type badge — color coded
        char badge[8];
        snprintf(badge, sizeof(badge), "[%s]", msgLabel(m.type));
        gfx->setTextSize(1);
        gfx->setTextColor(col, COL_BG);
        gfx->setCursor(MARGIN_X, cy);
        gfx->print(badge);

        // Message text truncated to fit
        char txt[64] = "";
        int tlen = strlen(m.text);
        int copy = tlen < TXT_MAX ? tlen : TXT_MAX;
        strncpy(txt, m.text, copy);
        txt[copy] = '\0';
        gfx->setTextColor(COL_WHITE, COL_BG);
        gfx->setCursor(MARGIN_X + BADGE_W, cy);
        gfx->print(txt);

        // Timestamp right-aligned
        if (m.ts[0]) {
            gfx->setTextColor(COL_GREY, COL_BG);
            gfx->setCursor(SCREEN_W - TS_W + MARGIN_X, cy);
            gfx->print(m.ts);
        }

        cy += ROW_H;

        // Thin separator between rows
        if (i < p.count - 1)
            gfx->drawFastHLine(MARGIN_X, cy - 1, SCREEN_W - MARGIN_X * 2, COL_DIM);
    }
}

static void fullRedraw() {
    drawHeader();
    drawRows();
    needs_redraw = false;
}

static void showStatus(const char* line1, const char* line2 = nullptr) {
    gfx->fillScreen(COL_BG);
    gfx->setTextColor(COL_GREY);
    gfx->setTextSize(1);
    gfx->setCursor(10, 110);
    gfx->print(line1);
    if (line2) { gfx->setCursor(10, 122); gfx->print(line2); }
}

// ---- Touch handling ----
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
        // Left → previous page
        if (cur_page > 0) { cur_page--; needs_redraw = true; }
    } else {
        // Right → next page
        if (cur_page < page_count - 1) { cur_page++; needs_redraw = true; }
    }
}

// ---- Setup ----
void setup() {
    Serial.begin(115200);

    if (!gfx->begin()) Serial.println("gfx->begin() failed!");
    gfx->invertDisplay(true);
    gfx->fillScreen(COL_BG);

    pinMode(GFX_BL, OUTPUT);
    ledcSetup(0, 5000, 8);
    ledcAttachPin(GFX_BL, 0);
    ledcWrite(0, 255);

    pinMode(0, INPUT_PULLUP);

    touchSPI.begin(XPT2046_CLK, XPT2046_MISO, XPT2046_MOSI, XPT2046_CS);
    ts.begin(touchSPI);
    ts.setRotation(1);

    brLoadSettings();
    ledcWrite(0, br_brightness);

    bool showPortal = !br_has_settings;
    showStatus("Hold BOOT to change settings...");
    for (int i = 0; i < 30 && !showPortal; i++) {
        if (digitalRead(0) == LOW) showPortal = true;
        delay(100);
    }
    if (showPortal) brRunPortal(gfx);

    if (!brConnectWiFi(gfx)) {
        showStatus("WiFi failed.", "Hold BOOT on reboot to reconfigure.");
        delay(5000);
        ESP.restart();
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
