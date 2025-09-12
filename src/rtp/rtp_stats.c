#define _POSIX_C_SOURCE 200809L
#include "rtp/rtp_stats.h"
#include <string.h>
#include <time.h>

static inline uint64_t now_ns(void){
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec*1000000000ull + (uint64_t)ts.tv_nsec;
}
static inline double ns_to_seconds(uint64_t ns){ return (double)ns/1e9; }

void rtp_stats_init(rtp_stats_t *st, uint32_t ssrc, uint16_t seq, uint32_t ts, uint32_t clockrate){
    memset(st, 0, sizeof(*st));
    st->inited = true; st->ssrc = ssrc; st->base_seq = seq; st->max_seq_ext = seq; st->clockrate = clockrate;
    st->first_arrival_ns = now_ns(); st->first_ts = ts;
}

void rtp_stats_update(rtp_stats_t *st, const rtp_packet_t *p, uint64_t arrival_ns){
    static int32_t prev_transit = 0; // simple (un SSRC)

    uint32_t seq_ext = p->seq | st->cycles;
    if(p->seq < (st->max_seq_ext & 0xFFFF) && ((st->max_seq_ext & 0xFFFF) - p->seq) > 0x8000){
        st->cycles += 0x10000; seq_ext = p->seq | st->cycles;
    }
    if(seq_ext < st->max_seq_ext) st->out_of_order++;
    if(seq_ext > st->max_seq_ext) st->max_seq_ext = seq_ext;

    st->received++;
    uint32_t expected = (st->max_seq_ext - st->base_seq) + 1u;
    st->lost = (expected > st->received) ? (expected - st->received) : 0u;

    double arrival_s = ns_to_seconds(arrival_ns - st->first_arrival_ns);
    double arrival_rtp_units = arrival_s * (double)st->clockrate;
    int32_t transit = (int32_t)(arrival_rtp_units + (double)st->first_ts) - (int32_t)p->timestamp;
    int32_t d = transit - prev_transit; if(d < 0) d = -d;
    st->jitter += ( (double)d - st->jitter ) / 16.0;
    prev_transit = transit;
}

double rtp_stats_jitter_ms(const rtp_stats_t *st){
    return st->clockrate ? (st->jitter * 1000.0 / (double)st->clockrate) : 0.0;
}
