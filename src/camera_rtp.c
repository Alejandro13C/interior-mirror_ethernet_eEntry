#include "camera_rtp.h"
#include <stdlib.h>
#include <string.h>

static uint16_t be16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] << 8 | p[1]);
}
static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 |
           (uint32_t)p[2] << 8 | p[3];
}

bool camera_packet(const uint8_t *b, size_t n, CameraPacket *out) {
    static const uint8_t mac[] = {0x44, 0x3c, 0x88, 0x24, 0x70, 0x22};
    static const uint8_t group[] = {0xff, 0x14, 0, 0, 0, 0, 0, 0,
                                   0, 0, 0, 0, 0, 2, 0, 0x0d};
    if (!b || !out || n < 14 || memcmp(b + 6, mac, 6)) return false;
    size_t ip = 14;
    uint16_t type = be16(b + 12);
    unsigned tags = 0;
    while (type == 0x8100 || type == 0x88a8) {
        if (++tags > 2 || n - ip < 4) return false;
        type = be16(b + ip + 2);
        ip += 4;
    }
    if (type != 0x86dd || n - ip < 40 || b[ip] >> 4 != 6 ||
        memcmp(b + ip + 24, group, 16)) return false;
    size_t end = ip + 40 + be16(b + ip + 4);
    if (end > n) return false;
    uint8_t next = b[ip + 6];
    size_t udp = ip + 40;
    /* Accept bounded IPv6 options; fragments are deliberately unsupported. */
    for (unsigned i = 0; next != 17 && i < 8; ++i) {
        if (next != 0 && next != 43 && next != 60) return false;
        if (end - udp < 8) return false;
        size_t extra = ((size_t)b[udp + 1] + 1) * 8;
        if (extra > end - udp) return false;
        next = b[udp];
        udp += extra;
    }
    if (next != 17 || end - udp < 20 || be16(b + udp + 2) != 42800)
        return false;
    size_t length = be16(b + udp + 4);
    if (length < 20 || length > end - udp) return false;
    const uint8_t *r = b + udp + 8;
    size_t rn = length - 8;
    if (r[0] >> 6 != 2 || (r[1] & 127) != 127) return false;
    size_t offset = 12 + 4 * (r[0] & 15);
    if (offset > rn) return false;
    if (r[0] & 16) {
        if (rn - offset < 4) return false;
        size_t extension = 4 + 4 * (size_t)be16(r + offset + 2);
        if (extension > rn - offset) return false;
        offset += extension;
    }
    if (r[0] & 32) {
        uint8_t padding = r[rn - 1];
        if (!padding || padding > rn - offset) return false;
        rn -= padding;
    }
    if (offset >= rn) return false;
    *out = (CameraPacket){.payload = r + offset, .size = rn - offset,
        .seq = be16(r + 2), .timestamp = be32(r + 4), .ssrc = be32(r + 8)};
    return true;
}

static bool starts_sps(const uint8_t *p, size_t n) {
    return (n >= 5 && !memcmp(p, "\0\0\0\1", 4) && (p[4] & 31) == 7) ||
           (n >= 4 && !memcmp(p, "\0\0\1", 3) && (p[3] & 31) == 7);
}

bool camera_assemble(CameraAssembler *a, const CameraPacket *p,
                     uint64_t arrival, CameraFrameFn emit, void *ctx) {
    a->packets++;
    if (a->have_frame && p->ssrc != a->ssrc) {
        free(a->data);
        a->data = NULL; a->size = a->capacity = 0;
        a->have_frame = false;
    }
    if (a->have_frame && p->seq != (uint16_t)(a->seq + 1)) {
        a->sequence_errors++;
        a->valid = false; /* A gap on a boundary can lose the previous tail. */
        uint16_t delta = (uint16_t)(p->seq - a->seq);
        if (!delta || delta > 0x8000u) return true; /* Duplicate or late packet. */
    }
    if (!a->have_frame || a->timestamp != p->timestamp) {
        if (a->have_frame) {
            if (a->valid && a->size) {
                uint8_t *data = a->data;
                size_t size = a->size;
                a->data = NULL; a->size = a->capacity = 0;
                a->complete++;
                if (!emit(ctx, data, size, a->timestamp, a->first_arrival_ns))
                    return false;
            } else {
                a->damaged++;
            }
        }
        a->size = 0;
        a->timestamp = p->timestamp;
        a->ssrc = p->ssrc;
        a->first_arrival_ns = arrival;
        a->have_frame = true;
        a->valid = starts_sps(p->payload, p->size);
    }
    a->seq = p->seq;
    if (!a->valid) return true;
    if (p->size > CAMERA_FRAME_LIMIT - a->size) {
        a->valid = false; a->oversized++;
        return true;
    }
    size_t needed = a->size + p->size;
    if (needed > a->capacity) {
        size_t capacity = a->capacity ? a->capacity : 128 * 1024;
        while (capacity < needed) capacity *= 2;
        uint8_t *data = realloc(a->data, capacity);
        if (!data) return false;
        a->data = data; a->capacity = capacity;
    }
    memcpy(a->data + a->size, p->payload, p->size);
    a->size = needed;
    return true;
}

void camera_assembler_clear(CameraAssembler *a) {
    free(a->data);
    a->data = NULL; a->size = a->capacity = 0;
    a->have_frame = a->valid = false;
}

static uint32_t read32(const uint8_t *b, bool little) {
    if (!little) return be32(b);
    return (uint32_t)b[0] | (uint32_t)b[1] << 8 |
           (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
}
bool camera_pcap_open(CameraPcap *r, FILE *file) {
    uint8_t b[24];
    if (fread(b, 1, sizeof b, file) != sizeof b) return false;
    uint32_t magic = be32(b);
    if (magic != 0xd4c3b2a1 && magic != 0xa1b2c3d4 &&
        magic != 0x4d3cb2a1 && magic != 0xa1b23c4d) return false;
    r->little = magic == 0xd4c3b2a1 || magic == 0x4d3cb2a1;
    r->nano = magic == 0x4d3cb2a1 || magic == 0xa1b23c4d;
    r->file = file;
    return read32(b + 20, r->little) == 1; /* Ethernet only. */
}
int camera_pcap_next(CameraPcap *r, uint8_t *b, size_t capacity,
                     size_t *size, uint64_t *ns) {
    uint8_t h[16];
    size_t n = fread(h, 1, sizeof h, r->file);
    if (!n && feof(r->file)) return 0;
    if (n != sizeof h) return -1;
    uint32_t len = read32(h + 8, r->little);
    uint32_t original = read32(h + 12, r->little);
    if (len > capacity || len > original) return -1;
    if (fread(b, 1, len, r->file) != len) return -1;
    if (len != original) return -1; /* Do not decode a truncated capture. */
    *size = len;
    *ns = (uint64_t)read32(h, r->little) * 1000000000ull +
          (uint64_t)read32(h + 4, r->little) * (r->nano ? 1 : 1000);
    return 1;
}
