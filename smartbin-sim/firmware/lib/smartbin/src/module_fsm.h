// Module state machine of the ESP32 firmware (arc42 5.3, 8.1, ADR-003).
//
// C++ implementation of sim/module.py, which is the executable spec:
//
//   INIT -> BEREIT -> ERKANNT -> WARTEN -> SORTIEREN -> BEREIT
//   ERKANNT / WARTEN / SORTIEREN --fault--> STOERUNG --RESET_FAULT--> BEREIT
//
// Safety (ADR-003), enforced here and nowhere else:
//   * the flap tilts only in SORTIEREN and only while the PIR sees no hand;
//     the PIR is checked on every poll() during the movement, so a hand
//     stops the flap at once (the spec checks at start and half way);
//   * a hand longer than pir_max_wait_ms -> STOERUNG (PIR_BLOCKED);
//   * any fault -> STOERUNG: the item stays on the flap, the flap goes back
//     to rest (again only while the PIR is clear); leaves only via RESET_FAULT.
//
// Hardware independent (no Arduino includes). All timing uses Hal::nowMs()
// and is non-blocking: call poll() from loop() as often as possible.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "protocol.h"

namespace smartbin {

enum class FlapTarget : uint8_t { REST = 0, DIREKT, LINKS, RECHTS };

// Everything the state machine needs from the board.
class Hal : public ByteSink {
public:
    virtual uint32_t nowMs() = 0;
    virtual bool handInOpening() = 0;   // PIR (true = hand / motion in the opening)
    virtual bool itemOnFlap() = 0;      // item sensor on the flap, debounced
    virtual bool flapJammed() = 0;      // end position not reached after a tilt
    // Flap position: `progress` 0.0 = rest, 1.0 = fully tilted towards `target`.
    virtual void setFlap(FlapTarget target, float progress) = 0;
    virtual void setLed(uint8_t signal) = 0;  // Signal::*; pattern/blinking is up to the board
    // Takes a photo. Blocking is fine (the flap is at rest meanwhile).
    // The buffer stays valid until releaseImage().
    virtual bool captureImage(const uint8_t** data, size_t* len) = 0;
    virtual void releaseImage() = 0;
};

// Defaults = ModuleParams in sim/module.py (seconds there, milliseconds here).
struct Params {
    uint32_t boot_ms = 500;
    uint32_t settle_ms = 100;            // item lands -> photo (capture itself ~50 ms)
    uint32_t tilt_ms = 600;
    uint32_t return_ms = 400;
    uint32_t pir_max_wait_ms = 4000;     // hand in opening longer -> FAULT
    uint32_t retry_ms = 600;             // no SORT yet -> resend ITEM_DETECTED + IMAGE
    uint32_t sort_cmd_timeout_ms = 4000; // no SORT after ITEM_DETECTED -> FAULT
    uint32_t heartbeat_interval_ms = 1000;
    uint32_t heartbeat_timeout_ms = 3000;
};

const uint8_t FW_MAJOR = 1;
const uint8_t FW_MINOR = 0;

class ModuleFsm : public FrameHandler {
public:
    ModuleFsm(Hal& hal, uint8_t moduleId, const Params& params = Params());

    void begin();                               // once at power-up
    void poll();                                // from loop(): timers, sensors, motion
    void onBytes(const uint8_t* data, size_t n);  // bytes received from the Jetson
    void onFrame(const Frame& f) override;

    uint8_t state() const { return state_; }    // StateCode::*
    uint16_t seq() const { return seq_; }
    uint8_t led() const { return led_; }
    uint8_t fraction() const { return fraction_; }
    uint8_t position() const { return position_; }
    uint8_t routing() const { return routing_; }
    float flapProgress() const { return progress_; }
    uint8_t lastFault() const { return lastFault_; }
    uint32_t retries() const { return retries_; }
    const FrameDecoder& decoder() const { return decoder_; }

private:
    enum class Motion : uint8_t { NONE, TILT, RETURN };

    void send(uint8_t type, uint16_t seq, const uint8_t* payload, uint32_t len);
    void sendHello();
    void sendHeartbeat();
    void sendItemAndImage(bool withItem);
    void setState(uint8_t s);
    void setLed(uint8_t s);
    void detect();
    void showSignal(uint16_t seq, uint8_t code);
    void startSort(uint8_t direction);
    void finishSort(uint8_t result);
    void fault(uint8_t code);
    void updateItemSensor();
    void updateMotion(uint32_t dt);
    void becomeReady();

    Hal& hal_;
    FrameDecoder decoder_;
    Params p_;
    uint8_t moduleId_;

    uint8_t state_ = StateCode::INIT;
    uint8_t fraction_ = 0xFF;
    uint8_t position_ = 0xFF;
    uint8_t routing_ = 0xFF;
    uint16_t seq_ = 0;
    uint8_t led_ = Signal::AUS;
    uint16_t lastDoneSeq_ = 0;
    uint8_t lastDoneResult_ = 0;
    uint8_t lastFault_ = 0;
    uint32_t retries_ = 0;

    // item tracking (see updateItemSensor)
    bool itemPrev_ = false;
    bool itemPending_ = false;    // an item waits on the flap to be reported
    bool handover_ = false;       // it arrived while the PIR saw no hand
    bool awaitRemoval_ = false;   // HALTEN: item stays until the user takes it

    // flap motion
    Motion motion_ = Motion::NONE;
    FlapTarget target_ = FlapTarget::REST;
    float progress_ = 0.0f;
    bool blocked_ = false;
    uint32_t blockedSince_ = 0;

    // timers (absolute ms, compared wrap-safe)
    uint32_t started_ = 0;
    uint32_t lastPoll_ = 0;
    uint32_t nextHeartbeat_ = 0;
    uint32_t lastHbRx_ = 0;
    uint32_t captureAt_ = 0;
    uint32_t nextRetry_ = 0;
    uint32_t sortDeadline_ = 0;
    bool helloSent_ = false;
};

}  // namespace smartbin
