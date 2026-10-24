#include "h264_sps.h"
#include <stdbool.h>
#include <string.h>

#define MAX_RBSP 256

typedef struct { const uint8_t *data; size_t bits, pos; bool error; } Reader;
typedef struct { uint8_t data[MAX_RBSP]; size_t pos; bool error; } Writer;

static uint32_t u(Reader *r, unsigned n) {
    uint32_t v = 0;
    for (unsigned i = 0; i < n; ++i) {
        if (r->pos >= r->bits) { r->error = true; return 0; }
        v = v << 1 | ((r->data[r->pos >> 3] >> (7 - (r->pos & 7))) & 1);
        r->pos++;
    }
    return v;
}
static uint32_t ue(Reader *r) {
    unsigned zeros = 0;
    while (!r->error && !u(r, 1)) if (++zeros > 31) { r->error = true; return 0; }
    return zeros ? (((uint32_t)1 << zeros) - 1 + u(r, zeros)) : 0;
}
static void put(Writer *w, unsigned n, uint32_t v) {
    for (unsigned i = n; i-- > 0;) {
        if (w->pos >= 8 * MAX_RBSP) { w->error = true; return; }
        if (v >> i & 1) w->data[w->pos >> 3] |= (uint8_t)(0x80 >> (w->pos & 7));
        w->pos++;
    }
}
static void put_ue(Writer *w, uint32_t v) {
    unsigned bits = 0;
    for (uint64_t x = (uint64_t)v + 1; x; x >>= 1) bits++;
    put(w, bits - 1, 0);
    put(w, bits, v + 1);
}

/* Fields in bitstream order (subset used by the camera). */
typedef struct {
    uint32_t profile, constraints, level, id, log2_frame, poc_type, log2_poc, refs, gaps;
    uint32_t width, height, frame_mbs, direct, crop, crop_values[4];
    uint32_t vui, aspect, aspect_idc, sar[2], overscan, overscan_ok, signal, signal_fields[3];
    uint32_t colour, colour_fields[3], chroma_loc, chroma_fields[2];
    uint32_t timing, timing_fields[3], pic_struct, restriction, restriction_fields[7];
} Sps;

static bool parse(Reader *r, Sps *s) {
    s->profile = u(r, 8); s->constraints = u(r, 8); s->level = u(r, 8);
    if (s->profile != 66 && s->profile != 77 && s->profile != 88) return false;
    s->id = ue(r); s->log2_frame = ue(r); s->poc_type = ue(r);
    if (s->poc_type != 0) return false;
    s->log2_poc = ue(r); s->refs = ue(r); s->gaps = u(r, 1);
    s->width = ue(r); s->height = ue(r); s->frame_mbs = u(r, 1);
    if (!s->frame_mbs) return false;
    s->direct = u(r, 1);
    if ((s->crop = u(r, 1))) for (int i = 0; i < 4; ++i) s->crop_values[i] = ue(r);
    if (!(s->vui = u(r, 1))) return false;
    if ((s->aspect = u(r, 1)) && (s->aspect_idc = u(r, 8)) == 255) { s->sar[0] = u(r, 16); s->sar[1] = u(r, 16); }
    if ((s->overscan = u(r, 1))) s->overscan_ok = u(r, 1);
    if ((s->signal = u(r, 1))) {
        s->signal_fields[0] = u(r, 3); s->signal_fields[1] = u(r, 1);
        if ((s->colour = u(r, 1))) for (int i = 0; i < 3; ++i) s->colour_fields[i] = u(r, 8);
    }
    if ((s->chroma_loc = u(r, 1))) { s->chroma_fields[0] = ue(r); s->chroma_fields[1] = ue(r); }
    if ((s->timing = u(r, 1))) { s->timing_fields[0] = u(r, 32); s->timing_fields[1] = u(r, 32); s->timing_fields[2] = u(r, 1); }
    if (u(r, 1) || u(r, 1)) return false; /* HRD parameters: not handled */
    s->pic_struct = u(r, 1);
    if ((s->restriction = u(r, 1))) {
        s->restriction_fields[0] = u(r, 1);
        for (int i = 1; i < 7; ++i) s->restriction_fields[i] = ue(r);
    }
    /* rbsp_stop_one_bit, then zero alignment. */
    if (r->error || !u(r, 1)) return false;
    while (r->pos < r->bits) if (u(r, 1)) return false;
    return !r->error;
}

static void emit(Writer *w, const Sps *s) {
    put(w, 8, s->profile); put(w, 8, s->constraints); put(w, 8, s->level);
    put_ue(w, s->id); put_ue(w, s->log2_frame); put_ue(w, s->poc_type); put_ue(w, s->log2_poc);
    put_ue(w, s->refs); put(w, 1, s->gaps); put_ue(w, s->width); put_ue(w, s->height);
    put(w, 1, s->frame_mbs); put(w, 1, s->direct); put(w, 1, s->crop);
    if (s->crop) for (int i = 0; i < 4; ++i) put_ue(w, s->crop_values[i]);
    put(w, 1, s->vui);
    put(w, 1, s->aspect);
    if (s->aspect) { put(w, 8, s->aspect_idc); if (s->aspect_idc == 255) { put(w, 16, s->sar[0]); put(w, 16, s->sar[1]); } }
    put(w, 1, s->overscan);
    if (s->overscan) put(w, 1, s->overscan_ok);
    put(w, 1, s->signal);
    if (s->signal) {
        put(w, 3, s->signal_fields[0]); put(w, 1, s->signal_fields[1]); put(w, 1, s->colour);
        if (s->colour) for (int i = 0; i < 3; ++i) put(w, 8, s->colour_fields[i]);
    }
    put(w, 1, s->chroma_loc);
    if (s->chroma_loc) { put_ue(w, s->chroma_fields[0]); put_ue(w, s->chroma_fields[1]); }
    put(w, 1, s->timing);
    if (s->timing) { put(w, 32, s->timing_fields[0]); put(w, 32, s->timing_fields[1]); put(w, 1, s->timing_fields[2]); }
    put(w, 1, 0); put(w, 1, 0);
    put(w, 1, s->pic_struct);
    put(w, 1, s->restriction);
    if (s->restriction) {
        put(w, 1, s->restriction_fields[0]);
        for (int i = 1; i < 7; ++i) put_ue(w, s->restriction_fields[i]);
    }
    put(w, 1, 1);
    while (w->pos & 7) put(w, 1, 0);
}

static size_t unescape(const uint8_t *in, size_t n, uint8_t *out) {
    size_t o = 0; unsigned zeros = 0;
    for (size_t i = 0; i < n; ++i) {
        if (zeros >= 2 && in[i] == 3) { zeros = 0; continue; }
        out[o++] = in[i];
        zeros = in[i] ? 0 : zeros + 1;
    }
    return o;
}
static size_t escape(const uint8_t *in, size_t n, uint8_t *out, size_t capacity) {
    size_t o = 0; unsigned zeros = 0;
    for (size_t i = 0; i < n; ++i) {
        if (zeros >= 2 && in[i] <= 3) { if (o >= capacity) return 0; out[o++] = 3; zeros = 0; }
        if (o >= capacity) return 0;
        out[o++] = in[i];
        zeros = in[i] ? 0 : zeros + 1;
    }
    return o;
}

size_t h264_sps_restrict(const uint8_t *nal, size_t size, uint8_t *out, size_t capacity) {
    if (!nal || size < 4 || size > MAX_RBSP || (nal[0] & 31) != 7 || capacity < 2) return 0;
    uint8_t rbsp[MAX_RBSP];
    size_t n = unescape(nal + 1, size - 1, rbsp);
    Reader r = {.data = rbsp, .bits = 8 * n};
    Sps s = {0};
    if (!parse(&r, &s)) return 0;
    if (s.restriction && s.restriction_fields[5] == 0) return 0; /* already declares no reordering */
    /* Refuse unless the parser understood every bit: re-encoding must round-trip. */
    Writer same = {0};
    emit(&same, &s);
    if (same.error || same.pos / 8 != n || memcmp(same.data, rbsp, n)) return 0;
    /* Motion vectors may cross boundaries, default denominators and MV lengths,
       no reordering, a buffer of max_num_ref_frames pictures. */
    s.restriction = 1;
    uint32_t fields[7] = {1, 2, 1, 16, 16, 0, s.refs ? s.refs : 1};
    memcpy(s.restriction_fields, fields, sizeof fields);
    Writer w = {0};
    emit(&w, &s);
    if (w.error) return 0;
    out[0] = nal[0];
    size_t written = escape(w.data, w.pos / 8, out + 1, capacity - 1);
    return written ? written + 1 : 0;
}
