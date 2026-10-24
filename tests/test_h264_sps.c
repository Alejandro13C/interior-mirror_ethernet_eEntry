#include "h264_sps.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static size_t hex(const char *text, uint8_t *out) {
    size_t n = strlen(text) / 2;
    for (size_t i = 0; i < n; ++i) sscanf(text + 2 * i, "%2hhx", &out[i]);
    return n;
}

int main(void) {
    /* The camera's SPS (Baseline 4.0, 1344x976 cropped to 968, VUI timing only)
       and its rewrite, cross-checked with tools/sps-restrict.py, which decodes
       pixel-identically with OpenCV. */
    uint8_t sps[64], expected[64], out[64];
    size_t n = hex("67424028f402a03dfcb0800001f4000061a842", sps);
    size_t m = hex("67424028f402a03dfcb0800001f4000061a846d0442350", expected);
    size_t written = h264_sps_restrict(sps, n, out, sizeof out);
    assert(written == m && !memcmp(out, expected, m));
    /* Already restricted: unchanged. Not an SPS, truncated, or too small an
       output buffer: unchanged. */
    assert(!h264_sps_restrict(expected, m, out, sizeof out));
    uint8_t pps[] = {0x68, 0xce, 0x38, 0x80};
    assert(!h264_sps_restrict(pps, sizeof pps, out, sizeof out));
    for (size_t k = 0; k < n; ++k) assert(!h264_sps_restrict(sps, k, out, sizeof out));
    assert(!h264_sps_restrict(sps, n, out, m - 1));
    /* Mutations never read or write out of bounds (run under ASan). */
    uint32_t seed = 7;
    for (unsigned i = 0; i < 100000; ++i) {
        uint8_t *mutated = malloc(n);
        assert(mutated);
        memcpy(mutated, sps, n);
        seed = seed * 1664525u + 1013904223u;
        mutated[1 + (seed >> 8) % (n - 1)] ^= (uint8_t)(seed >> 24);
        uint8_t *target = malloc(48);
        assert(target);
        size_t r = h264_sps_restrict(mutated, n, target, 48);
        assert(r <= 48 && (!r || target[0] == mutated[0]));
        free(target); free(mutated);
    }
    puts("H.264 SPS restriction: camera vector, rejections and fuzzing passed");
    return 0;
}
