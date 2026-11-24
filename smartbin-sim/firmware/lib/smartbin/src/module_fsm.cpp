#include "module_fsm.h"

namespace smartbin {

namespace {
// wrap-safe "now >= t" for millis()
inline bool reached(uint32_t now, uint32_t t) { return (int32_t)(now - t) >= 0; }

FlapTarget targetFor(uint8_t direction) {
    switch (direction) {
        case Direction::LINKS: return FlapTarget::LINKS;
        case Direction::RECHTS: return FlapTarget::RECHTS;
        default: return FlapTarget::DIREKT;
    }
}

const uint32_t MAX_STEP_MS = 50;  // a stalled loop slows the flap down, never makes it jump
}  // namespace

ModuleFsm::ModuleFsm(Hal& hal, uint8_t moduleId, const Params& params)
    : hal_(hal), p_(params), moduleId_(moduleId) {}

void ModuleFsm::begin() {
    uint32_t now = hal_.nowMs();
    started_ = lastPoll_ = lastHbRx_ = now;
    nextHeartbeat_ = now + p_.heartbeat_interval_ms;
    hal_.setFlap(FlapTarget::REST, 0.0f);
    hal_.setLed(Signal::AUS);
    itemPrev_ = hal_.itemOnFlap();
    itemPending_ = itemPrev_;       // something already lies there at power-up
    handover_ = false;
}

// ------------------------------------------------------------------ output
void ModuleFsm::send(uint8_t type, uint16_t seq, const uint8_t* payload, uint32_t len) {
    writeFrame(hal_, type, seq, payload, len);
}

void ModuleFsm::sendHello() {
    const uint8_t p[4] = {moduleId_, FW_MAJOR, FW_MINOR, PROTOCOL_VERSION};
    send(Msg::HELLO, 0, p, sizeof(p));
}

void ModuleFsm::sendHeartbeat() {
    const uint8_t p[4] = {state_, (uint8_t)(lastDoneSeq_ & 0xFF), (uint8_t)(lastDoneSeq_ >> 8),
                          lastDoneResult_};
    send(Msg::HEARTBEAT, 0, p, sizeof(p));
}

void ModuleFsm::sendItemAndImage(bool withItem) {
    if (withItem) {
        const uint8_t src = handover_ ? 1 : 0;
        send(Msg::ITEM_DETECTED, seq_, &src, 1);
    }
    const uint8_t* img = nullptr;
    size_t len = 0;
    if (!hal_.captureImage(&img, &len)) {
        fault(FaultCode::CAMERA);
        return;
    }
    send(Msg::IMAGE, seq_, img, (uint32_t)len);
    hal_.releaseImage();
}

void ModuleFsm::setState(uint8_t s) { state_ = s; }

void ModuleFsm::setLed(uint8_t s) {
    if (s != led_) {
        led_ = s;
        hal_.setLed(s);
    }
}

// ------------------------------------------------------------------ input
void ModuleFsm::onBytes(const uint8_t* data, size_t n) { decoder_.feed(data, n, *this); }

void ModuleFsm::onFrame(const Frame& f) {
    const uint8_t* p = f.payload;
    switch (f.type) {
        case Msg::HEARTBEAT:
            lastHbRx_ = hal_.nowMs();
            break;
        case Msg::CONFIG:
            if (f.length < 2) break;
            fraction_ = p[0];
            position_ = p[1];
            if (f.length >= 3) routing_ = p[2];
            lastHbRx_ = hal_.nowMs();
            if (state_ == StateCode::INIT) becomeReady();
            break;
        case Msg::SIGNAL:
            if (f.length >= 1) showSignal(f.seq, p[0]);
            break;
        case Msg::SORT:
            if (f.length >= 2 && p[1]) showSignal(f.seq, p[1]);
            // idempotent per seq: a repeated SORT finds us in SORTIEREN/BEREIT and is ignored
            if (f.length >= 1 && p[0] <= Direction::HALTEN && f.seq == seq_ &&
                (state_ == StateCode::ERKANNT || state_ == StateCode::WARTEN)) {
                startSort(p[0]);
            }
            break;
        case Msg::RESET_FAULT:
            if (state_ == StateCode::STOERUNG) {
                setLed(Signal::AUS);
                lastHbRx_ = hal_.nowMs();
                becomeReady();
            }
            break;
        default:
            break;  // unknown types: ignore (Postel)
    }
}

void ModuleFsm::showSignal(uint16_t seq, uint8_t code) {
    if (code > Signal::STOERUNG) return;
    if (seq == seq_ && (state_ == StateCode::ERKANNT || state_ == StateCode::WARTEN)) setLed(code);
}

// ------------------------------------------------------------------ flow
void ModuleFsm::becomeReady() {
    setState(StateCode::BEREIT);
    // an item still on the flap (e.g. after a fault) is reported again with a new seq
    itemPending_ = hal_.itemOnFlap() && !awaitRemoval_;
}

void ModuleFsm::detect() {
    itemPending_ = false;
    seq_ = (uint16_t)(seq_ + 1);
    setState(StateCode::ERKANNT);
    setLed(Signal::AUS);
    const uint8_t src = handover_ ? 1 : 0;
    send(Msg::ITEM_DETECTED, seq_, &src, 1);
    uint32_t now = hal_.nowMs();
    captureAt_ = now + p_.settle_ms;
    sortDeadline_ = now + p_.sort_cmd_timeout_ms;
}

void ModuleFsm::startSort(uint8_t direction) {
    if (direction == Direction::HALTEN) {
        finishSort(1);
        awaitRemoval_ = hal_.itemOnFlap();  // user takes it back; no new Vorgang for it
        itemPending_ = false;
        return;
    }
    setState(StateCode::SORTIEREN);
    target_ = targetFor(direction);
    motion_ = Motion::TILT;
    blocked_ = false;
}

void ModuleFsm::finishSort(uint8_t result) {
    lastDoneSeq_ = seq_;
    lastDoneResult_ = result;
    const uint8_t r = result;
    send(Msg::SORT_DONE, seq_, &r, 1);
    setState(StateCode::BEREIT);
    if (result == 0 && hal_.itemOnFlap()) {
        // the flap tilted but the sensor still sees something: another item
        // landed meanwhile (no edge in between), report it as new
        itemPending_ = true;
        handover_ = !hal_.handInOpening();
    }
}

void ModuleFsm::fault(uint8_t code) {
    lastFault_ = code;
    setState(StateCode::STOERUNG);
    setLed(Signal::STOERUNG);
    motion_ = progress_ > 0.0f ? Motion::RETURN : Motion::NONE;  // safe state: back to rest
    blocked_ = false;
    const uint8_t p[2] = {code, StateCode::STOERUNG};
    send(Msg::FAULT, seq_, p, sizeof(p));
}

void ModuleFsm::updateItemSensor() {
    bool present = hal_.itemOnFlap();
    if (present && !itemPrev_) {
        // Firmware rule (spec): an item that arrives while the PIR sees no hand
        // came from the neighbour's flap (handover), not through the opening.
        handover_ = !hal_.handInOpening();
        itemPending_ = true;
    } else if (!present && itemPrev_) {
        awaitRemoval_ = false;
        if (state_ == StateCode::BEREIT || state_ == StateCode::STOERUNG ||
            state_ == StateCode::INIT) {
            itemPending_ = false;  // taken out before it was reported
        }
    }
    itemPrev_ = present;
}

void ModuleFsm::updateMotion(uint32_t dt) {
    if (motion_ == Motion::NONE) return;
    if (motion_ == Motion::TILT && state_ != StateCode::SORTIEREN) motion_ = Motion::RETURN;

    // ADR-003: the flap moves only while no hand is in the opening
    if (hal_.handInOpening()) {
        uint32_t now = hal_.nowMs();
        if (!blocked_) {
            blocked_ = true;
            blockedSince_ = now;
        } else if (state_ == StateCode::SORTIEREN && now - blockedSince_ >= p_.pir_max_wait_ms) {
            fault(FaultCode::PIR_BLOCKED);
        }
        return;  // hold position
    }
    blocked_ = false;
    if (dt > MAX_STEP_MS) dt = MAX_STEP_MS;

    if (motion_ == Motion::TILT) {
        progress_ += p_.tilt_ms ? (float)dt / (float)p_.tilt_ms : 1.0f;
        if (progress_ < 1.0f) {
            hal_.setFlap(target_, progress_);
            return;
        }
        progress_ = 1.0f;
        hal_.setFlap(target_, progress_);
        if (hal_.flapJammed()) {
            fault(FaultCode::BLOCKADE);
            return;
        }
        motion_ = Motion::RETURN;  // item has dropped, flap goes back
        return;
    }
    // RETURN
    progress_ -= p_.return_ms ? (float)dt / (float)p_.return_ms : 1.0f;
    if (progress_ > 0.0f) {
        hal_.setFlap(target_, progress_);
        return;
    }
    progress_ = 0.0f;
    hal_.setFlap(FlapTarget::REST, 0.0f);
    motion_ = Motion::NONE;
    if (state_ == StateCode::SORTIEREN) finishSort(0);
}

void ModuleFsm::poll() {
    uint32_t now = hal_.nowMs();
    uint32_t dt = now - lastPoll_;
    lastPoll_ = now;

    if (!helloSent_ && reached(now, started_ + p_.boot_ms)) {
        sendHello();
        helloSent_ = true;
    }
    if (reached(now, nextHeartbeat_)) {
        nextHeartbeat_ = now + p_.heartbeat_interval_ms;
        sendHeartbeat();
        // Not in the spec's first version: repeat HELLO until CONFIG arrives,
        // so a Jetson that starts after the module (or a lost HELLO/CONFIG)
        // does not leave the module in INIT forever. sim/module.py does the same.
        if (state_ == StateCode::INIT && helloSent_) sendHello();
        bool silent = now - lastHbRx_ > p_.heartbeat_timeout_ms;
        if (state_ != StateCode::INIT && state_ != StateCode::STOERUNG && silent) {
            fault(FaultCode::HEARTBEAT_LOST);
        }
    }

    updateItemSensor();

    switch (state_) {
        case StateCode::BEREIT:
            if (itemPending_ && motion_ == Motion::NONE) detect();
            break;
        case StateCode::ERKANNT:
            if (reached(now, sortDeadline_)) {
                fault(FaultCode::TIMEOUT_SORT_CMD);
            } else if (reached(now, captureAt_)) {
                sendItemAndImage(false);
                if (state_ == StateCode::ERKANNT) {
                    setState(StateCode::WARTEN);
                    nextRetry_ = hal_.nowMs() + p_.retry_ms;
                }
            }
            break;
        case StateCode::WARTEN:
            if (reached(now, sortDeadline_)) {
                fault(FaultCode::TIMEOUT_SORT_CMD);
            } else if (reached(now, nextRetry_)) {
                // no SORT yet: a frame got lost somewhere, report the item again
                ++retries_;
                sendItemAndImage(true);
                nextRetry_ = hal_.nowMs() + p_.retry_ms;
            }
            break;
        default:
            break;
    }

    updateMotion(dt);
}

}  // namespace smartbin
