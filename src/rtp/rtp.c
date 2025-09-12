#include "rtp/rtp.h"
#include <arpa/inet.h>
#include <string.h>

bool rtp_parse(const uint8_t *buf, size_t len, rtp_packet_t *out){
    if(!buf || !out || len < RTP_MIN_HEADER) return false;
    uint8_t b0 = buf[0], b1 = buf[1];
    out->version      = (b0 >> 6) & 0x03;
    out->padding      = (b0 >> 5) & 0x01;
    out->extension    = (b0 >> 4) & 0x01;
    out->csrc_count   =  b0       & 0x0F;
    out->marker       = (b1 >> 7) & 0x01;
    out->payload_type =  b1       & 0x7F;
    out->seq       = ntohs(*(const uint16_t*)(buf+2));
    out->timestamp = ntohl(*(const uint32_t*)(buf+4));
    out->ssrc      = ntohl(*(const uint32_t*)(buf+8));

    size_t off = RTP_MIN_HEADER;
    size_t csrc_bytes = 4u * out->csrc_count;
    if(len < off + csrc_bytes) return false;
    off += csrc_bytes;

    if(out->extension){
        if(len < off + 4) return false;
        uint16_t ext_len_words = ntohs(*(const uint16_t*)(buf+off+2));
        off += 4;
        size_t ext_len_bytes = (size_t)ext_len_words * 4u;
        if(len < off + ext_len_bytes) return false;
        off += ext_len_bytes;
    }

    size_t payload_len = (len > off) ? (len - off) : 0;
    if(out->padding){
        if(payload_len == 0) return false;
        uint8_t pad = buf[len-1];
        if(pad > payload_len) return false;
        payload_len -= pad;
    }
    out->payload = buf + off;
    out->payload_len = payload_len;
    return out->version == 2;
}
