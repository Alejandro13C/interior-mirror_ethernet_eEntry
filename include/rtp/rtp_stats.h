#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "rtp/rtp.h"

typedef struct {
    bool     inited;
    uint32_t ssrc;
    uint16_t base_seq;
    uint32_t cycles;
    uint32_t max_seq_ext;
    uint32_t received;
    uint32_t lost;
    uint32_t out_of_order;
    double   jitter;     // unidades de timestamp RTP
    uint32_t clockrate;  // Hz
    uint64_t first_arrival_ns;
    uint32_t first_ts;
} rtp_stats_t;

void   rtp_stats_init(rtp_stats_t *st, uint32_t ssrc, uint16_t seq, uint32_t ts, uint32_t clockrate);
void   rtp_stats_update(rtp_stats_t *st, const rtp_packet_t *p, uint64_t arrival_ns);
double rtp_stats_jitter_ms(const rtp_stats_t *st);
