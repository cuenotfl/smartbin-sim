// Binary frame protocol Jetson <-> ESP32 (arc42 8.7, ADR-004).
//
// C++ twin of smartbin/adapters/protocol.py. The Python file is the
// reference; tests/test_protocol.py (CONTRACT_VECTORS) is the contract and
// firmware/test/test_protocol checks the same vectors.
// tests/test_firmware_contract.py makes sure codes and vectors stay in sync.
//
//   0xA5 | type u8 | seq u16 | length u32 | hcrc u8 | payload[length] | crc16 u16
//
// little endian; hcrc = CRC-8 (poly 0x07, init 0) over type..length;
// crc16 = CRC-16/CCITT-FALSE over type..payload (header crc included).
//
// Hardware independent: no Arduino includes, builds on the host for unit tests.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace smartbin {

const uint8_t START = 0xA5;
const uint8_t PROTOCOL_VERSION = 1;
const size_t HEADER_SIZE = 9;        // start, type, seq(2), length(4), hcrc
const size_t CRC_SIZE = 2;
const uint32_t MAX_PAYLOAD = 512UL * 1024UL;

// Wire codes: stable, never renumber, only append (same as protocol.py).
namespace Msg {
enum : uint8_t {
    // ESP32 -> Jetson
    HELLO = 0x01,          // module_id u8, fw_major u8, fw_minor u8, protocol u8
    ITEM_DETECTED = 0x02,  // source u8 (0 = opening, 1 = handover from neighbour)
    IMAGE = 0x03,          // jpeg bytes
    SORT_DONE = 0x04,      // result u8 (0 ok, 1 held, >=2 error)
    FAULT = 0x05,          // code u8, state u8
    HEARTBEAT = 0x06,      // ESP32->Jetson: state u8, last_done_seq u16, last_done_result u8
    // Jetson -> ESP32
    CONFIG = 0x10,         // fraction u8, position u8, routing u8
    SORT = 0x11,           // direction u8, signal u8 (0 = AUS/no change)
    SIGNAL = 0x12,         // pattern u8
    RESET_FAULT = 0x13,    // -
};
}  // namespace Msg

namespace StateCode {
enum : uint8_t { INIT = 0, BEREIT = 1, ERKANNT = 2, WARTEN = 3, SORTIEREN = 4, STOERUNG = 5 };
}

namespace FaultCode {
enum : uint8_t {
    TIMEOUT_SORT_CMD = 1,  // no SORT received in time
    PIR_BLOCKED = 2,       // hand stayed in opening too long
    BLOCKADE = 3,          // servo did not reach end position
    HEARTBEAT_LOST = 4,    // Jetson silent
    CAMERA = 5,
};
}

namespace Fraction { enum : uint8_t { PET = 0, ALU = 1, REST = 2 }; }
namespace Direction { enum : uint8_t { DIREKT = 0, LINKS = 1, RECHTS = 2, HALTEN = 3 }; }
namespace Signal { enum : uint8_t { AUS = 0, OK = 1, FEHLWURF = 2, UNBEKANNT = 3, STOERUNG = 4 }; }
namespace Routing { enum : uint8_t { NEIGHBOUR_BIN = 0, VIA_FLAPS = 1 }; }

// Chainable: pass the previous result as `crc` to continue over more data.
uint8_t crc8(const uint8_t* data, size_t len, uint8_t crc = 0x00);
uint16_t crc16(const uint8_t* data, size_t len, uint16_t crc = 0xFFFF);

class ByteSink {
public:
    virtual ~ByteSink() {}
    virtual void write(const uint8_t* data, size_t len) = 0;
};

// Writes one frame without copying the payload (images go straight from the
// camera buffer to the UART).
void writeFrame(ByteSink& out, uint8_t type, uint16_t seq, const uint8_t* payload, uint32_t len);

// Encodes into a buffer. Returns the frame size, 0 if `cap` is too small.
size_t encodeFrame(uint8_t* out, size_t cap, uint8_t type, uint16_t seq,
                   const uint8_t* payload, uint32_t len);

struct Frame {
    uint8_t type;
    uint16_t seq;
    const uint8_t* payload;  // valid only during the onFrame() call
    uint32_t length;
};

class FrameHandler {
public:
    virtual ~FrameHandler() {}
    virtual void onFrame(const Frame& f) = 0;
};

// Streaming decoder, same behaviour as protocol.FrameDecoder: resync after
// garbage, drop frames with a wrong header or frame CRC, deliver unknown
// types to the handler (which ignores them).
// The ESP32 only receives short commands, so payloads above MAX_RX_PAYLOAD
// are skipped (counted in `oversized`) instead of buffered.
class FrameDecoder {
public:
    static const size_t MAX_RX_PAYLOAD = 64;

    FrameDecoder() : len_(0), skip_(0), crcErrors(0), oversized(0) {}
    void feed(const uint8_t* data, size_t n, FrameHandler& handler);

    uint32_t crcErrorCount() const { return crcErrors; }
    uint32_t oversizedCount() const { return oversized; }

private:
    void process(FrameHandler& handler);
    void drop(size_t n);

    uint8_t buf_[HEADER_SIZE + MAX_RX_PAYLOAD + CRC_SIZE];
    size_t len_;
    uint32_t skip_;
    uint32_t crcErrors;
    uint32_t oversized;
};

}  // namespace smartbin
