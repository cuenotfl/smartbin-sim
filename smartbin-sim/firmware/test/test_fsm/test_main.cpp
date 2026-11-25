// Module state machine on the host: pio test -e native
// Each test mirrors a rule of sim/module.py (the executable spec).
#include <unity.h>

#include <vector>

#include "module_fsm.h"
#include "protocol.h"

using namespace smartbin;

// ------------------------------------------------------------------ fake board
struct Sent {
    uint8_t type;
    uint16_t seq;
    std::vector<uint8_t> payload;
};

// Jetson side of the line: decodes everything the module sends, any size.
// (FrameDecoder is the ESP32 receive path and skips payloads > 64 bytes, e.g.
// the IMAGE, by design; it must not be used to look at the module's output.)
struct JetsonRx {
    std::vector<uint8_t> buf;
    template <class F>
    void feed(const uint8_t* d, size_t n, F onFrame) {
        buf.insert(buf.end(), d, d + n);
        while (buf.size() >= HEADER_SIZE) {
            TEST_ASSERT_EQUAL_HEX8(START, buf[0]);  // the module never sends garbage
            TEST_ASSERT_EQUAL_HEX8(crc8(&buf[1], 7), buf[8]);
            uint32_t len = (uint32_t)buf[4] | ((uint32_t)buf[5] << 8) |
                           ((uint32_t)buf[6] << 16) | ((uint32_t)buf[7] << 24);
            size_t total = HEADER_SIZE + len + CRC_SIZE;
            if (buf.size() < total) return;
            uint16_t crc = (uint16_t)(buf[total - 2] | (buf[total - 1] << 8));
            TEST_ASSERT_EQUAL_HEX16(crc16(&buf[1], HEADER_SIZE - 1 + len), crc);
            Frame f;
            f.type = buf[1];
            f.seq = (uint16_t)(buf[2] | (buf[3] << 8));
            f.payload = &buf[HEADER_SIZE];
            f.length = len;
            onFrame(f);
            buf.erase(buf.begin(), buf.begin() + (long)total);
        }
    }
};

struct FakeHal : Hal {
    uint32_t now = 0;
    bool hand = false, item = false, jammed = false, cameraOk = true;
    FlapTarget flapTarget = FlapTarget::REST;
    float flap = 0.0f, maxFlap = 0.0f;
    bool movedWhileHand = false;
    uint8_t ledSignal = Signal::AUS;
    std::vector<uint8_t> image = std::vector<uint8_t>(300, 0x42);
    JetsonRx rx;  // decodes what the module sends
    std::vector<Sent> sent;

    uint32_t nowMs() override { return now; }
    bool handInOpening() override { return hand; }
    bool itemOnFlap() override { return item; }
    bool flapJammed() override { return jammed; }
    void setFlap(FlapTarget t, float progress) override {
        if (hand && progress != flap) movedWhileHand = true;
        flapTarget = t;
        flap = progress;
        if (progress > maxFlap) maxFlap = progress;
        if (progress >= 1.0f && t != FlapTarget::REST && !jammed) item = false;  // item drops
    }
    void setLed(uint8_t s) override { ledSignal = s; }
    bool captureImage(const uint8_t** d, size_t* n) override {
        *d = image.data();
        *n = image.size();
        return cameraOk;
    }
    void releaseImage() override {}
    void write(const uint8_t* d, size_t n) override {
        rx.feed(d, n, [this](const Frame& f) {
            sent.push_back({f.type, f.seq, std::vector<uint8_t>(f.payload, f.payload + f.length)});
        });
    }

    int count(uint8_t type) const {
        int n = 0;
        for (const Sent& s : sent) n += s.type == type;
        return n;
    }
    const Sent* last(uint8_t type) const {
        for (size_t i = sent.size(); i-- > 0;)
            if (sent[i].type == type) return &sent[i];
        return nullptr;
    }
};

static FakeHal* hal;
static ModuleFsm* fsm;

static void toModule(uint8_t type, uint16_t seq, std::vector<uint8_t> p) {
    uint8_t buf[64];
    size_t n = encodeFrame(buf, sizeof(buf), type, seq, p.data(), (uint32_t)p.size());
    fsm->onBytes(buf, n);
}

// advance time in 10 ms steps; the "Jetson" keeps sending heartbeats
static void run(uint32_t ms, bool jetsonAlive = true) {
    for (uint32_t t = 0; t < ms; t += 10) {
        hal->now += 10;
        if (jetsonAlive && hal->now % 1000 == 0) toModule(Msg::HEARTBEAT, 0, {});
        fsm->poll();
    }
}

static void configured() {
    run(600);
    toModule(Msg::CONFIG, 0, {Fraction::ALU, 1, Routing::VIA_FLAPS});
    run(20);
}

static void throwItem(bool withHand = true) {
    hal->hand = withHand;
    run(20);
    hal->item = true;
    run(200);  // settle + photo -> WARTEN
    hal->hand = false;
}

void setUp() {
    hal = new FakeHal();
    fsm = new ModuleFsm(*hal, 2);
    fsm->begin();
}

void tearDown() {
    delete fsm;
    delete hal;
}

// ------------------------------------------------------------------ tests
void test_boot_sends_hello_and_repeats_it_until_configured() {
    run(490);
    TEST_ASSERT_EQUAL(0, hal->count(Msg::HELLO));
    run(20);
    const Sent* h = hal->last(Msg::HELLO);
    TEST_ASSERT_NOT_NULL(h);
    TEST_ASSERT_EQUAL(4, h->payload.size());
    TEST_ASSERT_EQUAL(2, h->payload[0]);                 // module id
    TEST_ASSERT_EQUAL(PROTOCOL_VERSION, h->payload[3]);
    run(1000);
    TEST_ASSERT_EQUAL(2, hal->count(Msg::HELLO));        // repeated with the heartbeat
    TEST_ASSERT_EQUAL(StateCode::INIT, hal->last(Msg::HEARTBEAT)->payload[0]);
    toModule(Msg::CONFIG, 0, {Fraction::ALU, 1, Routing::VIA_FLAPS});
    TEST_ASSERT_EQUAL(StateCode::BEREIT, fsm->state());
    TEST_ASSERT_EQUAL(Fraction::ALU, fsm->fraction());
    run(2000);
    TEST_ASSERT_EQUAL(2, hal->count(Msg::HELLO));        // no more HELLO once configured
}

void test_item_through_opening_is_reported_with_image() {
    configured();
    throwItem(true);
    const Sent* d = hal->last(Msg::ITEM_DETECTED);
    TEST_ASSERT_NOT_NULL(d);
    TEST_ASSERT_EQUAL(1, d->seq);
    TEST_ASSERT_EQUAL(0, d->payload[0]);                 // source: opening
    const Sent* img = hal->last(Msg::IMAGE);
    TEST_ASSERT_NOT_NULL(img);
    TEST_ASSERT_EQUAL(1, img->seq);
    TEST_ASSERT_EQUAL(300, img->payload.size());
    TEST_ASSERT_EQUAL(StateCode::WARTEN, fsm->state());
}

void test_item_without_hand_is_a_handover() {
    configured();
    throwItem(false);
    TEST_ASSERT_EQUAL(1, hal->last(Msg::ITEM_DETECTED)->payload[0]);
}

void test_sort_tilts_reports_done_and_ignores_duplicates() {
    configured();
    throwItem();
    toModule(Msg::SORT, 1, {Direction::LINKS, Signal::FEHLWURF});
    TEST_ASSERT_EQUAL(Signal::FEHLWURF, hal->ledSignal);
    TEST_ASSERT_EQUAL(StateCode::SORTIEREN, fsm->state());
    run(1100);                                           // tilt 600 + return 400
    TEST_ASSERT_EQUAL(StateCode::BEREIT, fsm->state());
    TEST_ASSERT_TRUE(hal->maxFlap >= 1.0f);
    TEST_ASSERT_EQUAL(0.0f, hal->flap);
    TEST_ASSERT_FALSE(hal->item);
    const Sent* done = hal->last(Msg::SORT_DONE);
    TEST_ASSERT_EQUAL(1, done->seq);
    TEST_ASSERT_EQUAL(0, done->payload[0]);
    toModule(Msg::SORT, 1, {Direction::LINKS, Signal::FEHLWURF});   // resent by the core
    run(1100);
    TEST_ASSERT_EQUAL(1, hal->count(Msg::SORT_DONE));
    const Sent* hb = hal->last(Msg::HEARTBEAT);         // lost SORT_DONE is recoverable
    TEST_ASSERT_EQUAL(1, hb->payload[1] | (hb->payload[2] << 8));
    TEST_ASSERT_EQUAL(0, hb->payload[3]);
}

void test_sort_with_wrong_seq_is_ignored() {
    configured();
    throwItem();
    toModule(Msg::SORT, 7, {Direction::DIREKT, 0});
    TEST_ASSERT_EQUAL(StateCode::WARTEN, fsm->state());
}

void test_flap_never_moves_while_hand_in_opening() {
    configured();
    throwItem();
    hal->hand = true;
    toModule(Msg::SORT, 1, {Direction::DIREKT, Signal::OK});
    run(1500);
    TEST_ASSERT_EQUAL(0.0f, hal->maxFlap);               // waits before starting
    hal->hand = false;
    run(300);                                            // half way
    TEST_ASSERT_TRUE(hal->flap > 0.2f && hal->flap < 0.8f);
    hal->hand = true;                                    // safety stop in the middle
    float held = hal->flap;
    run(1000);
    TEST_ASSERT_EQUAL_FLOAT(held, hal->flap);
    TEST_ASSERT_FALSE(hal->movedWhileHand);
    hal->hand = false;
    run(1000);
    TEST_ASSERT_EQUAL(1, hal->count(Msg::SORT_DONE));
}

void test_hand_too_long_is_a_fault_and_flap_returns_only_when_clear() {
    configured();
    throwItem();
    toModule(Msg::SORT, 1, {Direction::DIREKT, Signal::OK});
    run(300);
    hal->hand = true;
    run(4100);
    TEST_ASSERT_EQUAL(StateCode::STOERUNG, fsm->state());
    const Sent* f = hal->last(Msg::FAULT);
    TEST_ASSERT_EQUAL(FaultCode::PIR_BLOCKED, f->payload[0]);
    TEST_ASSERT_EQUAL(StateCode::STOERUNG, f->payload[1]);
    TEST_ASSERT_EQUAL(Signal::STOERUNG, hal->ledSignal);
    TEST_ASSERT_TRUE(hal->flap > 0.0f);                  // still held: hand is there
    TEST_ASSERT_FALSE(hal->movedWhileHand);
    hal->hand = false;
    run(500);
    TEST_ASSERT_EQUAL(0.0f, hal->flap);                  // safe state: back to rest
    TEST_ASSERT_EQUAL(0, hal->count(Msg::SORT_DONE));
}

void test_missing_sort_is_retried_then_faults() {
    configured();
    throwItem();
    int images = hal->count(Msg::IMAGE);
    run(700);
    TEST_ASSERT_EQUAL(images + 1, hal->count(Msg::IMAGE));
    TEST_ASSERT_EQUAL(2, hal->count(Msg::ITEM_DETECTED));
    TEST_ASSERT_EQUAL(1u, fsm->retries());
    run(3500);
    TEST_ASSERT_EQUAL(StateCode::STOERUNG, fsm->state());
    TEST_ASSERT_EQUAL(FaultCode::TIMEOUT_SORT_CMD, fsm->lastFault());
}

void test_jetson_silent_goes_to_safe_state() {
    configured();
    run(2500, true);
    TEST_ASSERT_EQUAL(StateCode::BEREIT, fsm->state());
    run(4000, false);
    TEST_ASSERT_EQUAL(StateCode::STOERUNG, fsm->state());
    TEST_ASSERT_EQUAL(FaultCode::HEARTBEAT_LOST, fsm->lastFault());
}

void test_reset_fault_reports_item_still_on_flap_with_new_seq() {
    configured();
    throwItem();
    run(4000);                                           // no SORT -> fault, item stays
    TEST_ASSERT_EQUAL(StateCode::STOERUNG, fsm->state());
    toModule(Msg::SORT, 1, {Direction::DIREKT, 0});      // late SORT must not move the flap
    run(1000);
    TEST_ASSERT_EQUAL(0.0f, hal->maxFlap);
    toModule(Msg::RESET_FAULT, 0, {});
    run(20);
    TEST_ASSERT_EQUAL(StateCode::ERKANNT, fsm->state());
    TEST_ASSERT_EQUAL(2, hal->last(Msg::ITEM_DETECTED)->seq);
    TEST_ASSERT_EQUAL(Signal::AUS, hal->ledSignal);
}

void test_halten_keeps_item_until_taken_out() {
    configured();
    throwItem();
    toModule(Msg::SORT, 1, {Direction::HALTEN, Signal::UNBEKANNT});
    TEST_ASSERT_EQUAL(1, hal->last(Msg::SORT_DONE)->payload[0]);   // held
    run(2000);
    TEST_ASSERT_EQUAL(1, hal->count(Msg::ITEM_DETECTED));          // not reported again
    TEST_ASSERT_EQUAL(0.0f, hal->maxFlap);
    hal->item = false;                                             // user takes it back
    run(50);
    throwItem();
    TEST_ASSERT_EQUAL(2, hal->last(Msg::ITEM_DETECTED)->seq);
}

void test_jammed_flap_is_blockade_fault() {
    configured();
    throwItem();
    hal->jammed = true;
    toModule(Msg::SORT, 1, {Direction::RECHTS, Signal::FEHLWURF});
    run(700);
    TEST_ASSERT_EQUAL(StateCode::STOERUNG, fsm->state());
    TEST_ASSERT_EQUAL(FaultCode::BLOCKADE, fsm->lastFault());
}

void test_camera_failure_is_a_fault() {
    configured();
    hal->cameraOk = false;
    throwItem();
    TEST_ASSERT_EQUAL(StateCode::STOERUNG, fsm->state());
    TEST_ASSERT_EQUAL(FaultCode::CAMERA, fsm->lastFault());
}

void test_signal_only_for_current_item_and_unknown_types_ignored() {
    configured();
    toModule(Msg::SIGNAL, 0, {Signal::FEHLWURF});        // no item: ignore
    TEST_ASSERT_EQUAL(Signal::AUS, hal->ledSignal);
    throwItem();
    toModule(0x7F, 1, {1, 2, 3});                        // future message type
    toModule(Msg::SIGNAL, 1, {Signal::UNBEKANNT});
    TEST_ASSERT_EQUAL(Signal::UNBEKANNT, hal->ledSignal);
    TEST_ASSERT_EQUAL(StateCode::WARTEN, fsm->state());
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_boot_sends_hello_and_repeats_it_until_configured);
    RUN_TEST(test_item_through_opening_is_reported_with_image);
    RUN_TEST(test_item_without_hand_is_a_handover);
    RUN_TEST(test_sort_tilts_reports_done_and_ignores_duplicates);
    RUN_TEST(test_sort_with_wrong_seq_is_ignored);
    RUN_TEST(test_flap_never_moves_while_hand_in_opening);
    RUN_TEST(test_hand_too_long_is_a_fault_and_flap_returns_only_when_clear);
    RUN_TEST(test_missing_sort_is_retried_then_faults);
    RUN_TEST(test_jetson_silent_goes_to_safe_state);
    RUN_TEST(test_reset_fault_reports_item_still_on_flap_with_new_seq);
    RUN_TEST(test_halten_keeps_item_until_taken_out);
    RUN_TEST(test_jammed_flap_is_blockade_fault);
    RUN_TEST(test_camera_failure_is_a_fault);
    RUN_TEST(test_signal_only_for_current_item_and_unknown_types_ignored);
    return UNITY_END();
}
