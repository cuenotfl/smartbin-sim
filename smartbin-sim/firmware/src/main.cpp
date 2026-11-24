// Smart Bin module firmware (ESP32-CAM, AI-Thinker).
//
// Board glue only: pins, camera, servo, LED, UART. All behaviour lives in
// lib/smartbin (ModuleFsm), which is unit tested on the host.
//
// Build variants (platformio.ini):
//   esp32cam  real module, OV2640 camera, protocol on UART0 (921600 baud, USB-UART to the Jetson)
//   wokwi     ESP32 DevKit in the Wokwi simulator: fake camera, buttons for items,
//             UART0 exposed as RFC2217 port for the Jetson software (see firmware/README.md)
//
// UART0 carries ONLY protocol frames. No Serial.print debugging: the Jetson's
// decoder skips garbage, but every stray byte costs a resync.
#include <Arduino.h>

#include "module_fsm.h"

#ifndef MODULE_ID
#define MODULE_ID 1
#endif
#ifndef SERIAL_BAUD
#define SERIAL_BAUD 921600
#endif

using namespace smartbin;

// ------------------------------------------------------------------ pins
#ifdef SMARTBIN_WOKWI
static const int PIN_PIR = 13;        // wokwi-pir-motion-sensor
static const int PIN_ITEM = 15;       // button "Einwurf": drops an item on the flap
static const int PIN_TAKE = 4;        // button "Entnehmen": user takes a held item back
static const int PIN_JAM = 26;        // slide switch: simulate a blocked flap
static const int PIN_SERVO = 14;
static const int PIN_LED = 2;         // one WS2812
#else
// ESP32-CAM: with the camera running only GPIO 2, 4, 12, 13, 14, 15 are free
// (no SD card). GPIO 4 drives the on-board flash LED, used as photo light.
static const int PIN_PIR = 13;        // PIR output, active HIGH
static const int PIN_ITEM = 15;       // item sensor on the flap (e.g. IR reflex), active LOW
static const int PIN_END = 12;        // end switch at full tilt, active HIGH, external pull-down!
                                      // (GPIO 12 is a strapping pin: must be LOW at boot)
static const int PIN_SERVO = 14;
static const int PIN_LED = 2;         // one WS2812 (status LED in the opening)
static const int PIN_FLASH = 4;
#endif

// Servo angles per target. PLACEHOLDER: the mechanics (neighbour_bin vs.
// via_flaps, one or two axes) are still open; adjust once the flap exists.
static const int ANGLE_REST = 90;
static const int ANGLE_DIREKT = 0;
static const int ANGLE_LINKS = 45;
static const int ANGLE_RECHTS = 135;

static const int SERVO_CHANNEL = 2;   // LEDC channel 2 = timer 1; the camera uses timer 0
static const uint32_t ITEM_DEBOUNCE_MS = 30;

#ifndef SMARTBIN_WOKWI
#include "esp_camera.h"

static bool cameraInit() {
    camera_config_t c = {};
    c.pin_pwdn = 32;
    c.pin_reset = -1;
    c.pin_xclk = 0;
    c.pin_sccb_sda = 26;
    c.pin_sccb_scl = 27;
    c.pin_d7 = 35;
    c.pin_d6 = 34;
    c.pin_d5 = 39;
    c.pin_d4 = 36;
    c.pin_d3 = 21;
    c.pin_d2 = 19;
    c.pin_d1 = 18;
    c.pin_d0 = 5;
    c.pin_vsync = 25;
    c.pin_href = 23;
    c.pin_pclk = 22;
    c.xclk_freq_hz = 20000000;
    c.ledc_timer = LEDC_TIMER_0;
    c.ledc_channel = LEDC_CHANNEL_0;
    c.pixel_format = PIXFORMAT_JPEG;
    c.frame_size = FRAMESIZE_QVGA;    // 320x240, ~10-20 KB (README Erkenntnis 2: <= 30 KB)
    c.jpeg_quality = 12;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    return esp_camera_init(&c) == ESP_OK;
}
#endif

// ------------------------------------------------------------------ board
class BoardHal : public Hal {
public:
    void begin() {
        pinMode(PIN_PIR, INPUT);
        pinMode(PIN_ITEM, INPUT_PULLUP);
#ifdef SMARTBIN_WOKWI
        pinMode(PIN_TAKE, INPUT_PULLUP);
        pinMode(PIN_JAM, INPUT_PULLUP);
#else
        pinMode(PIN_END, INPUT);
        pinMode(PIN_FLASH, OUTPUT);
        digitalWrite(PIN_FLASH, LOW);
        cameraOk_ = cameraInit();
#endif
        ledcSetup(SERVO_CHANNEL, 50, 16);
        ledcAttachPin(PIN_SERVO, SERVO_CHANNEL);
        writeAngle(ANGLE_REST);
        rawItem_ = itemStable_ = readItemPin();
        itemChanged_ = millis();
    }

    // called every loop: debounce, LED animation, Wokwi item physics
    void update() {
        uint32_t now = millis();
        bool raw = readItemPin();
        if (raw != rawItem_) {
            rawItem_ = raw;
            itemChanged_ = now;
        } else if (raw != itemStable_ && now - itemChanged_ >= ITEM_DEBOUNCE_MS) {
            itemStable_ = raw;
        }
        renderLed(now);
    }

    uint32_t nowMs() override { return millis(); }
    bool handInOpening() override { return digitalRead(PIN_PIR) == HIGH; }
    bool itemOnFlap() override { return itemStable_; }

    bool flapJammed() override {
#ifdef SMARTBIN_WOKWI
        return digitalRead(PIN_JAM) == LOW;
#else
#ifdef HAS_END_SWITCH
        delay(50);  // let the servo settle at the end position
        return digitalRead(PIN_END) == LOW;
#else
        return false;  // no end switch fitted: blockade cannot be detected
#endif
#endif
    }

    void setFlap(FlapTarget target, float progress) override {
        int to = ANGLE_REST;
        switch (target) {
            case FlapTarget::DIREKT: to = ANGLE_DIREKT; break;
            case FlapTarget::LINKS: to = ANGLE_LINKS; break;
            case FlapTarget::RECHTS: to = ANGLE_RECHTS; break;
            default: break;
        }
        writeAngle(ANGLE_REST + (int)((to - ANGLE_REST) * progress));
#ifdef SMARTBIN_WOKWI
        // simulated physics: at full tilt the item drops off the flap
        if (progress >= 1.0f && target != FlapTarget::REST && !flapJammed()) {
            wokwiItem_ = false;
        }
#endif
    }

    void setLed(uint8_t signal) override {
        signal_ = signal;
        signalSince_ = millis();
    }

    bool captureImage(const uint8_t** data, size_t* len) override {
#ifdef SMARTBIN_WOKWI
        // no camera in Wokwi: a JPEG-framed dummy of realistic size
        static uint8_t fake[15000];
        fake[0] = 0xFF; fake[1] = 0xD8;
        for (size_t i = 2; i < sizeof(fake) - 2; ++i) fake[i] = (uint8_t)(i * 31 + counter_);
        fake[sizeof(fake) - 2] = 0xFF; fake[sizeof(fake) - 1] = 0xD9;
        ++counter_;
        *data = fake;
        *len = sizeof(fake);
        return true;
#else
        if (!cameraOk_) return false;
        digitalWrite(PIN_FLASH, HIGH);
        camera_fb_t* stale = esp_camera_fb_get();  // frame buffered before the item landed
        if (stale) esp_camera_fb_return(stale);
        fb_ = esp_camera_fb_get();
        digitalWrite(PIN_FLASH, LOW);
        if (!fb_) return false;
        *data = fb_->buf;
        *len = fb_->len;
        return true;
#endif
    }

    void releaseImage() override {
#ifndef SMARTBIN_WOKWI
        if (fb_) esp_camera_fb_return(fb_);
        fb_ = nullptr;
#endif
    }

    void write(const uint8_t* data, size_t len) override { Serial.write(data, len); }

private:
    bool readItemPin() {
#ifdef SMARTBIN_WOKWI
        if (digitalRead(PIN_ITEM) == LOW) wokwiItem_ = true;    // "Einwurf" pressed
        if (digitalRead(PIN_TAKE) == LOW) wokwiItem_ = false;   // "Entnehmen" pressed
        return wokwiItem_;
#else
        return digitalRead(PIN_ITEM) == LOW;
#endif
    }

    void writeAngle(int deg) {
        if (deg < 0) deg = 0;
        if (deg > 180) deg = 180;
        uint32_t us = 500 + (uint32_t)deg * 2000 / 180;   // 0.5..2.5 ms pulse
        ledcWrite(SERVO_CHANNEL, us * 65536UL / 20000UL);
    }

    void renderLed(uint32_t now) {
        uint8_t r = 0, g = 0, b = 0;
        uint32_t t = now - signalSince_;
        switch (signal_) {
            case Signal::OK: if (t < 2000) g = 60; break;                       // green, short
            case Signal::FEHLWURF: if ((t / 250) % 2 == 0) r = 80; break;       // red, blinking
            case Signal::UNBEKANNT: r = 60; g = 40; break;                      // yellow
            case Signal::STOERUNG: r = 80; break;                               // red, steady
            default: break;
        }
        uint32_t rgb = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
        if (rgb != shownRgb_) {
            neopixelWrite(PIN_LED, r, g, b);
            shownRgb_ = rgb;
        }
    }

    bool rawItem_ = false, itemStable_ = false;
    uint32_t itemChanged_ = 0;
    uint8_t signal_ = Signal::AUS;
    uint32_t signalSince_ = 0;
    uint32_t shownRgb_ = 0xFFFFFFFF;
#ifdef SMARTBIN_WOKWI
    bool wokwiItem_ = false;
    uint8_t counter_ = 0;
#else
    bool cameraOk_ = false;
    camera_fb_t* fb_ = nullptr;
#endif
};

static BoardHal board;
static ModuleFsm fsm(board, MODULE_ID);

void setup() {
    Serial.setRxBufferSize(1024);
    Serial.begin(SERIAL_BAUD);
    board.begin();
    fsm.begin();
}

void loop() {
    uint8_t buf[128];
    int n;
    while ((n = Serial.available()) > 0) {
        n = Serial.readBytes(buf, n < (int)sizeof(buf) ? n : (int)sizeof(buf));
        fsm.onBytes(buf, (size_t)n);
    }
    board.update();
    fsm.poll();
}
