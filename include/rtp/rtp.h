#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RTP_MIN_HEADER 12

typedef struct {
    uint8_t  version;
    bool     padding;
    bool     extension;
    uint8_t  csrc_count;
    bool     marker;
    uint8_t  payload_type;
    uint16_t seq;
    uint32_t timestamp;
    uint32_t ssrc;
    const uint8_t *payload;
    size_t   payload_len;
} rtp_packet_t;

bool rtp_parse(const uint8_t *buf, size_t len, rtp_packet_t *out);
