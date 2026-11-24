#include "protocol.h"

#include <string.h>

namespace smartbin {

uint8_t crc8(const uint8_t* data, size_t len, uint8_t crc) {
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int b = 0; b < 8; ++b) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

uint16_t crc16(const uint8_t* data, size_t len, uint16_t crc) {
    for (size_t i = 0; i < len; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; ++b) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static void buildHeader(uint8_t h[HEADER_SIZE], uint8_t type, uint16_t seq, uint32_t len) {
    h[0] = START;
    h[1] = type;
    h[2] = (uint8_t)(seq & 0xFF);
    h[3] = (uint8_t)(seq >> 8);
    h[4] = (uint8_t)(len & 0xFF);
    h[5] = (uint8_t)((len >> 8) & 0xFF);
    h[6] = (uint8_t)((len >> 16) & 0xFF);
    h[7] = (uint8_t)((len >> 24) & 0xFF);
    h[8] = crc8(h + 1, 7);
}

void writeFrame(ByteSink& out, uint8_t type, uint16_t seq, const uint8_t* payload, uint32_t len) {
    uint8_t h[HEADER_SIZE];
    buildHeader(h, type, seq, len);
    uint16_t crc = crc16(h + 1, HEADER_SIZE - 1);
    if (len) crc = crc16(payload, len, crc);
    uint8_t c[CRC_SIZE] = {(uint8_t)(crc & 0xFF), (uint8_t)(crc >> 8)};
    out.write(h, HEADER_SIZE);
    if (len) out.write(payload, len);
    out.write(c, CRC_SIZE);
}

namespace {
class BufferSink : public ByteSink {
public:
    BufferSink(uint8_t* buf, size_t cap) : buf_(buf), cap_(cap), len_(0) {}
    void write(const uint8_t* data, size_t n) override {
        if (len_ + n <= cap_) memcpy(buf_ + len_, data, n);
        len_ += n;
    }
    size_t size() const { return len_ <= cap_ ? len_ : 0; }

private:
    uint8_t* buf_;
    size_t cap_;
    size_t len_;
};
}  // namespace

size_t encodeFrame(uint8_t* out, size_t cap, uint8_t type, uint16_t seq,
                   const uint8_t* payload, uint32_t len) {
    BufferSink sink(out, cap);
    writeFrame(sink, type, seq, payload, len);
    return sink.size();
}

// ------------------------------------------------------------------ decoder
void FrameDecoder::drop(size_t n) {
    if (n >= len_) {
        len_ = 0;
        return;
    }
    memmove(buf_, buf_ + n, len_ - n);
    len_ -= n;
}

void FrameDecoder::feed(const uint8_t* data, size_t n, FrameHandler& handler) {
    while (n > 0) {
        if (skip_ > 0) {  // rest of an oversized frame
            size_t k = n < skip_ ? n : (size_t)skip_;
            skip_ -= (uint32_t)k;
            data += k;
            n -= k;
            continue;
        }
        size_t room = sizeof(buf_) - len_;
        size_t k = n < room ? n : room;
        memcpy(buf_ + len_, data, k);
        len_ += k;
        data += k;
        n -= k;
        process(handler);
    }
}

void FrameDecoder::process(FrameHandler& handler) {
    for (;;) {
        const uint8_t* start = (const uint8_t*)memchr(buf_, START, len_);
        if (!start) {
            len_ = 0;
            return;
        }
        drop((size_t)(start - buf_));
        if (len_ < HEADER_SIZE) return;

        uint32_t length = (uint32_t)buf_[4] | ((uint32_t)buf_[5] << 8) |
                          ((uint32_t)buf_[6] << 16) | ((uint32_t)buf_[7] << 24);
        if (length > MAX_PAYLOAD || buf_[8] != crc8(buf_ + 1, 7)) {
            ++crcErrors;
            drop(1);  // bogus start byte or broken header: resync
            continue;
        }
        if (length > MAX_RX_PAYLOAD) {
            // valid header of a frame we cannot buffer (Postel: ignore it)
            ++oversized;
            uint32_t total = (uint32_t)(HEADER_SIZE + CRC_SIZE) + length;
            skip_ = total - (uint32_t)len_;
            len_ = 0;
            return;
        }
        size_t total = HEADER_SIZE + length + CRC_SIZE;
        if (len_ < total) return;
        uint16_t crc = crc16(buf_ + 1, HEADER_SIZE - 1 + length);
        uint16_t got = (uint16_t)(buf_[total - 2] | (buf_[total - 1] << 8));
        if (crc != got) {
            ++crcErrors;
            drop(1);
            continue;
        }
        Frame f;
        f.type = buf_[1];
        f.seq = (uint16_t)(buf_[2] | (buf_[3] << 8));
        f.payload = buf_ + HEADER_SIZE;
        f.length = length;
        handler.onFrame(f);
        drop(total);
    }
}

}  // namespace smartbin
