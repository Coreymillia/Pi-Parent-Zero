// BotMessages.h — Fetch and store bot DM/system messages from Pi HTTP server
// GET http://<bot_ip>:<bot_port>/messages
// JSON: {"count": N, "messages": [{"type":"dm","to":"!abcd1234","text":"...","ts":"HH:MM:SS"}]}

#pragma once
#include <Arduino.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

#define BM_MAX_MSGS     20
#define BM_TEXT_LEN     1500
#define BM_TYPE_LEN     8
#define BM_TO_LEN       16
#define BM_TS_LEN       9

struct BotMsg {
    char type[BM_TYPE_LEN];   // "dm", "system", "sensor"
    char to[BM_TO_LEN];       // recipient node id or "ALERT"
    char text[BM_TEXT_LEN];   // message content
    char ts[BM_TS_LEN];       // "HH:MM:SS"
};

static BotMsg bm_msgs[BM_MAX_MSGS];
static int    bm_count     = 0;
static int    bm_last_count = -1;  // detect new messages
static bool   bm_new_msg   = false;

static bool bmFetch(const char* host, uint16_t port) {
    char url[128];
    snprintf(url, sizeof(url), "http://%s:%u/messages", host, port);

    HTTPClient http;
    http.begin(url);
    http.setTimeout(4000);
    int code = http.GET();
    if (code != 200) {
        http.end();
        return false;
    }

    String body = http.getString();
    http.end();

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, body);
    if (err) return false;

    int new_count = doc["count"] | 0;
    JsonArray arr = doc["messages"].as<JsonArray>();
    if (arr.isNull()) return false;

    int n = 0;
    for (JsonObject obj : arr) {
        if (n >= BM_MAX_MSGS) break;
        strlcpy(bm_msgs[n].type, obj["type"] | "?",  BM_TYPE_LEN);
        strlcpy(bm_msgs[n].to,   obj["to"]   | "",   BM_TO_LEN);
        strlcpy(bm_msgs[n].text, obj["text"]  | "",  BM_TEXT_LEN);
        strlcpy(bm_msgs[n].ts,   obj["ts"]    | "",  BM_TS_LEN);
        n++;
    }
    bm_count = n;

    // Index 0 = newest — server already returns newest-first, no reversal needed.

    if (new_count != bm_last_count) {
        bm_new_msg   = true;
        bm_last_count = new_count;
    } else {
        bm_new_msg = false;
    }
    return true;
}
