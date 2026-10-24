#include "camera_rtp.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

static void put16(uint8_t *b, unsigned v) { b[0] = v >> 8; b[1] = v; }
static void put32(uint8_t *b, uint32_t v) {
    b[0] = v >> 24; b[1] = v >> 16; b[2] = v >> 8; b[3] = v;
}
static size_t ethernet(uint8_t *b, unsigned tags, const uint8_t *rtp, size_t n) {
    memset(b, 0, 512);
    memcpy(b + 6, "\x44\x3c\x88\x24\x70\x22", 6);
    size_t offset = 12;
    for (unsigned i = 0; i < tags; ++i) {
        put16(b + offset, 0x8100); put16(b + offset + 2, 2); offset += 4;
    }
    put16(b + offset, 0x86dd); offset += 2;
    b[offset] = 0x60; b[offset + 6] = 17;
    put16(b + offset + 4, 8 + n);
    memcpy(b + offset + 24, "\xff\x14\0\0\0\0\0\0\0\0\0\0\0\2\0\x0d", 16);
    offset += 40;
    put16(b + offset, 42993); put16(b + offset + 2, 42800);
    put16(b + offset + 4, 8 + n); offset += 8;
    memcpy(b + offset, rtp, n);
    return offset + n;
}
static void parser_tests(void) {
    uint8_t r[64] = {0x80, 127};
    put16(r + 2, 65535); put32(r + 4, 0xfffffff0); put32(r + 8, 1);
    memcpy(r + 12, "\0\0\0\1\x67\x42", 6);
    uint8_t storage[513];
    uint8_t *b = storage + 1; /* Deliberately unaligned. */
    CameraPacket p;
    for (unsigned tags = 0; tags <= 2; ++tags) {
        size_t size = ethernet(b, tags, r, 18);
        assert(camera_packet(b, size, &p));
        assert(p.seq == 65535 && p.timestamp == 0xfffffff0 && p.ssrc == 1 && p.size == 6);
        assert(!memcmp(p.payload, r + 12, 6));
        for (size_t prefix = 0; prefix < size; ++prefix)
            assert(!camera_packet(b, prefix, &p));
        b[6] ^= 1; assert(!camera_packet(b, size, &p)); b[6] ^= 1;
        b[14 + tags * 4 + 6] = 44; /* IPv6 fragment. */
        assert(!camera_packet(b, size, &p));
    }
    /* CSRC + extension + padding; payload must exclude all three. */
    memset(r, 0, sizeof r); r[0] = 0xb1; r[1] = 127;
    put16(r + 18, 1); /* Extension length in 32-bit words. */
    memcpy(r + 24, "\0\0\1\x67", 4); r[31] = 4;
    size_t size = ethernet(b, 1, r, 32);
    assert(camera_packet(b, size, &p) && p.size == 4 && p.payload[3] == 0x67);
    r[31] = 0; size = ethernet(b, 1, r, 32);
    assert(!camera_packet(b, size, &p));
    r[31] = 20; size = ethernet(b, 1, r, 32);
    assert(!camera_packet(b, size, &p));
    r[31] = 4; put16(r + 18, 0xffff); size = ethernet(b, 1, r, 32);
    assert(!camera_packet(b, size, &p));
}

typedef struct { unsigned count; size_t bytes; uint64_t hash; } Output;
static bool collect(void *ctx, uint8_t *data, size_t n, uint32_t ts, uint64_t arrival) {
    (void)ts; (void)arrival;
    Output *out = ctx;
    assert(n >= 5 && data[0] == 0 && data[1] == 0);
    out->count++; out->bytes += n;
    for (size_t i = 0; i < n; ++i) { out->hash ^= data[i]; out->hash *= 1099511628211ull; }
    free(data);
    return true;
}
static const uint8_t sps[] = {0, 0, 0, 1, 0x67, 0x42};
static bool feed(CameraAssembler *a, Output *o, unsigned seq, uint32_t ts, uint32_t ssrc) {
    CameraPacket p = {.payload = sps, .size = sizeof sps,
        .seq = seq, .timestamp = ts, .ssrc = ssrc};
    return camera_assemble(a, &p, 0, collect, o);
}
static void assembler_tests(void) {
    CameraAssembler a = {0}; Output o = {0};
    assert(feed(&a, &o, 65535, 0xffffff00, 1));
    assert(feed(&a, &o, 0, 0xffffff00, 1)); /* Sequence wraps within frame. */
    assert(feed(&a, &o, 1, 0x100, 1)); /* Timestamp wraps on boundary. */
    assert(o.count == 1 && o.bytes == 12 && !a.sequence_errors);
    assert(feed(&a, &o, 3, 0x100, 1)); /* Lost packet inside frame. */
    assert(feed(&a, &o, 4, 0x200, 1));
    assert(o.count == 1 && a.sequence_errors == 1);
    assert(feed(&a, &o, 6, 0x300, 1)); /* Lost tail at boundary. */
    assert(o.count == 1 && a.sequence_errors == 2);
    assert(feed(&a, &o, 7, 0x400, 1));
    assert(o.count == 2);
    size_t before = o.bytes;
    /* Duplicate: skipped, neither appended (corruption) nor fatal to the frame. */
    assert(feed(&a, &o, 7, 0x400, 1));
    assert(feed(&a, &o, 8, 0x500, 1));
    assert(o.count == 3 && o.bytes == before + sizeof sps && a.duplicates == 1 && a.sequence_errors == 2);
    /* A late packet from an earlier frame is dropped without damaging this one. */
    assert(feed(&a, &o, 5, 0x500, 1));
    assert(feed(&a, &o, 9, 0x600, 1));
    assert(o.count == 4 && a.late_packets == 1 && a.sequence_errors == 2);
    assert(feed(&a, &o, 0, 0x700, 2)); /* New source: discard previous frame. */
    assert(o.count == 4);
    assert(feed(&a, &o, 1, 0x800, 2));
    assert(o.count == 5);
    CameraPacket huge = {.payload = sps, .size = CAMERA_FRAME_LIMIT + 1,
        .seq = 2, .timestamp = 0x900, .ssrc = 2};
    assert(camera_assemble(&a, &huge, 0, collect, &o));
    assert(a.oversized == 1 && !a.valid);
    camera_assembler_clear(&a);
    assert(o.count == 6); /* Last timestamp deliberately not flushed. */

    /* Camera restart keeping its SSRC with a lower sequence: resynchronize
       after 32 packets, not after the 16-bit counter catches up. */
    CameraAssembler r = {0}; Output q = {0};
    for (unsigned i = 0; i < 100; ++i) assert(feed(&r, &q, 30000 + i, 0x1000 + i, 7));
    unsigned emitted = q.count;
    for (unsigned i = 0; i < 40; ++i) assert(feed(&r, &q, 10 + i, 0x5000 + i, 7));
    assert(r.resyncs == 1 && r.late_packets == 31 && q.count > emitted);
    camera_assembler_clear(&r);
}

/* Deterministic fuzzing: mutated and truncated packets must never be parsed
   out of bounds (run under ASan with `make test-asan`), and any accepted
   payload must lie inside the buffer and feed the assembler safely. */
static bool discard(void *ctx, uint8_t *data, size_t n, uint32_t ts, uint64_t arrival) {
    (void)ctx; (void)ts; (void)arrival;
    assert(n >= 4 && !data[0] && !data[1]); /* a frame starts with its SPS start code */
    free(data);
    return true;
}
static void fuzz_tests(void) {
    uint8_t r[64] = {0x80, 127};
    put16(r + 2, 100); put32(r + 4, 9000); put32(r + 8, 1);
    memcpy(r + 12, "\0\0\0\1\x67\x42\0\0\0\1\x65\x88", 12);
    uint8_t valid[512];
    size_t n = ethernet(valid, 1, r, 24);
    CameraAssembler a = {0};
    uint32_t seed = 12345;
    for (unsigned i = 0; i < 200000; ++i) {
        size_t size = n;
        uint8_t *b = malloc(size); /* exact size: ASan sees any over-read */
        assert(b);
        memcpy(b, valid, size);
        for (unsigned k = 0, flips = 1 + i % 8; k < flips; ++k) {
            seed = seed * 1664525u + 1013904223u;
            b[(seed >> 8) % size] ^= (uint8_t)(seed >> 24);
        }
        seed = seed * 1664525u + 1013904223u;
        if (seed % 4 == 0) size = (seed >> 8) % (n + 1);
        CameraPacket p;
        if (camera_packet(b, size, &p)) {
            assert(p.payload >= b && p.size && p.payload + p.size <= b + size);
            assert(camera_assemble(&a, &p, i, discard, NULL));
        }
        free(b);
    }
    camera_assembler_clear(&a);
}

static void capture_test(const char *path) {
    FILE *file = fopen(path, "rb"); assert(file);
    CameraPcap reader; assert(camera_pcap_open(&reader, file));
    uint8_t *b = malloc(65536); assert(b);
    CameraAssembler a = {0}; Output out = {.hash = 14695981039346656037ull};
    size_t size; uint64_t arrival; int status;
    while ((status = camera_pcap_next(&reader, b, 65536, &size, &arrival)) == 1) {
        CameraPacket p;
        if (camera_packet(b, size, &p)) assert(camera_assemble(&a, &p, arrival, collect, &out));
    }
    assert(status == 0);
    printf("capture: packets=%llu frames=%u bytes=%zu fnv64=%016llx\n",
        (unsigned long long)a.packets, out.count, out.bytes, (unsigned long long)out.hash);
    assert(a.packets == 11262 && out.count == 124 && a.sequence_errors == 0);
    assert(out.bytes == 14839708 && out.hash == 0x8b09be07a4bfce96ull);
    camera_assembler_clear(&a); free(b); fclose(file);
}
int main(int argc, char **argv) {
    parser_tests(); assembler_tests(); fuzz_tests();
    if (argc == 2) capture_test(argv[1]);
    puts("RTP tests passed");
    return 0;
}
