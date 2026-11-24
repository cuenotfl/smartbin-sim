// Protocol contract on the host: pio test -e native
// The vectors are copied from CONTRACT_VECTORS in tests/test_protocol.py;
// tests/test_firmware_contract.py checks that both lists stay identical.
#include <stdio.h>
#include <string.h>
#include <unity.h>

#include <vector>

#include "protocol.h"

using namespace smartbin;

struct Vector {
    uint8_t type;
    uint16_t seq;
    const char* payload_hex;
    const char* frame_hex;
};

// CONTRACT_VECTORS BEGIN
static const Vector CONTRACT_VECTORS[] = {
    {0x06, 0x0000, "", "a506000000000000cc35a9"},
    {0x02, 0x0007, "00", "a5020700010000007000f454"},
    {0x11, 0x1234, "0202", "a511341202000000d70202d206"},
    {0x12, 0x0001, "02", "a5120100010000005502905e"},
};
// CONTRACT_VECTORS END

static std::vector<uint8_t> unhex(const char* s) {
    std::vector<uint8_t> out;
    for (size_t i = 0; s[i] && s[i + 1]; i += 2) {
        unsigned v;
        sscanf(s + i, "%2x", &v);
        out.push_back((uint8_t)v);
    }
    return out;
}

struct Collect : FrameHandler {
    struct Got {
        uint8_t type;
        uint16_t seq;
        std::vector<uint8_t> payload;
    };
    std::vector<Got> frames;
    void onFrame(const Frame& f) override {
        frames.push_back({f.type, f.seq, std::vector<uint8_t>(f.payload, f.payload + f.length)});
    }
};

static std::vector<uint8_t> encode(uint8_t type, uint16_t seq, const std::vector<uint8_t>& p) {
    std::vector<uint8_t> buf(p.size() + HEADER_SIZE + CRC_SIZE);
    size_t n = encodeFrame(buf.data(), buf.size(), type, seq, p.data(), (uint32_t)p.size());
    TEST_ASSERT_EQUAL(buf.size(), n);
    return buf;
}

void setUp() {}
void tearDown() {}

void test_crc_check_values() {
    const uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    TEST_ASSERT_EQUAL_HEX16(0x29B1, crc16(check, sizeof(check)));  // CRC-16/CCITT-FALSE
    TEST_ASSERT_EQUAL_HEX8(0xF4, crc8(check, sizeof(check)));       // CRC-8/SMBUS
    // chaining = one pass
    TEST_ASSERT_EQUAL_HEX16(0x29B1, crc16(check + 4, 5, crc16(check, 4)));
}

void test_contract_vectors_encode_and_decode() {
    for (const Vector& v : CONTRACT_VECTORS) {
        std::vector<uint8_t> payload = unhex(v.payload_hex);
        std::vector<uint8_t> expected = unhex(v.frame_hex);
        std::vector<uint8_t> raw = encode(v.type, v.seq, payload);
        TEST_ASSERT_EQUAL(expected.size(), raw.size());
        TEST_ASSERT_EQUAL_HEX8_ARRAY(expected.data(), raw.data(), raw.size());

        FrameDecoder dec;
        Collect c;
        dec.feed(raw.data(), raw.size(), c);
        TEST_ASSERT_EQUAL(1, c.frames.size());
        TEST_ASSERT_EQUAL_HEX8(v.type, c.frames[0].type);
        TEST_ASSERT_EQUAL(v.seq, c.frames[0].seq);
        TEST_ASSERT_TRUE(c.frames[0].payload == payload);
    }
}

void test_streaming_in_small_chunks() {
    std::vector<uint8_t> stream;
    for (int i = 0; i < 3; ++i) {
        std::vector<uint8_t> f = encode(Msg::SORT, (uint16_t)(100 + i), {(uint8_t)i, 2});
        stream.insert(stream.end(), f.begin(), f.end());
    }
    FrameDecoder dec;
    Collect c;
    for (size_t i = 0; i < stream.size(); i += 7) {
        size_t n = stream.size() - i < 7 ? stream.size() - i : 7;
        dec.feed(stream.data() + i, n, c);
    }
    TEST_ASSERT_EQUAL(3, c.frames.size());
    TEST_ASSERT_EQUAL(102, c.frames[2].seq);
    TEST_ASSERT_EQUAL(2, c.frames[2].payload[0]);
}

void test_garbage_before_frame_is_skipped() {
    std::vector<uint8_t> s = {0x00, 0x13, 'g', 'a', 'r', 0xA5};
    std::vector<uint8_t> f = encode(Msg::HEARTBEAT, 0, {});
    s.insert(s.end(), f.begin(), f.end());
    FrameDecoder dec;
    Collect c;
    dec.feed(s.data(), s.size(), c);
    TEST_ASSERT_EQUAL(1, c.frames.size());
    TEST_ASSERT_EQUAL_HEX8(Msg::HEARTBEAT, c.frames[0].type);
}

void test_corrupted_frame_is_dropped_and_decoder_resyncs() {
    std::vector<uint8_t> bad = encode(Msg::SORT, 9, {1});
    bad[bad.size() - 3] ^= 0xFF;
    std::vector<uint8_t> good = encode(Msg::SORT_DONE, 9, {0});
    bad.insert(bad.end(), good.begin(), good.end());
    FrameDecoder dec;
    Collect c;
    dec.feed(bad.data(), bad.size(), c);
    TEST_ASSERT_EQUAL(1, c.frames.size());
    TEST_ASSERT_EQUAL_HEX8(Msg::SORT_DONE, c.frames[0].type);
    TEST_ASSERT_EQUAL(1, dec.crcErrorCount());
}

void test_unknown_type_is_delivered_for_caller_to_ignore() {
    std::vector<uint8_t> f = encode(0x7F, 1, {'f', 'u', 't', 'u', 'r', 'e'});
    FrameDecoder dec;
    Collect c;
    dec.feed(f.data(), f.size(), c);
    TEST_ASSERT_EQUAL(1, c.frames.size());
    TEST_ASSERT_EQUAL_HEX8(0x7F, c.frames[0].type);
}

void test_oversized_frame_is_skipped_and_next_frame_decoded() {
    std::vector<uint8_t> big(5000, 0xA5);  // payload full of start bytes, worst case
    std::vector<uint8_t> s = encode(0x7E, 1, big);
    std::vector<uint8_t> good = encode(Msg::RESET_FAULT, 2, {});
    s.insert(s.end(), good.begin(), good.end());
    FrameDecoder dec;
    Collect c;
    for (size_t i = 0; i < s.size(); i += 100) dec.feed(s.data() + i, s.size() - i < 100 ? s.size() - i : 100, c);
    TEST_ASSERT_EQUAL(1, c.frames.size());
    TEST_ASSERT_EQUAL_HEX8(Msg::RESET_FAULT, c.frames[0].type);
    TEST_ASSERT_EQUAL(1, dec.oversizedCount());
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_crc_check_values);
    RUN_TEST(test_contract_vectors_encode_and_decode);
    RUN_TEST(test_streaming_in_small_chunks);
    RUN_TEST(test_garbage_before_frame_is_skipped);
    RUN_TEST(test_corrupted_frame_is_dropped_and_decoder_resyncs);
    RUN_TEST(test_unknown_type_is_delivered_for_caller_to_ignore);
    RUN_TEST(test_oversized_frame_is_skipped_and_next_frame_decoded);
    return UNITY_END();
}
