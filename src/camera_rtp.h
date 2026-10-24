#ifndef CAMERA_RTP_H
#define CAMERA_RTP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define CAMERA_FRAME_LIMIT (4u * 1024u * 1024u)

typedef struct {
    const uint8_t *payload;
    size_t size;
    uint16_t seq;
    uint32_t timestamp, ssrc;
} CameraPacket;

/* Ethernet (with optional VLAN tags), IPv6, UDP 42800, RTP PT 127. */
bool camera_packet(const uint8_t *data, size_t size, CameraPacket *out);

/* The callback owns data, including when it returns false. */
typedef bool (*CameraFrameFn)(void *ctx, uint8_t *data, size_t size,
                              uint32_t timestamp, uint64_t first_arrival_ns);
typedef struct {
    uint8_t *data;
    size_t size, capacity;
    uint16_t seq;
    uint32_t timestamp, ssrc;
    bool have_frame, valid;
    uint64_t first_arrival_ns;
    uint64_t packets, complete, damaged, sequence_errors, oversized;
    uint64_t duplicates, late_packets, resyncs;
    unsigned behind; /* consecutive packets older than the stream */
} CameraAssembler;
bool camera_assemble(CameraAssembler *a, const CameraPacket *packet,
                     uint64_t arrival_ns, CameraFrameFn emit, void *ctx);
/* Never emit the final timestamp: without the next boundary it may be partial. */
void camera_assembler_clear(CameraAssembler *a);

typedef struct { FILE *file; bool little, nano; } CameraPcap;
bool camera_pcap_open(CameraPcap *r, FILE *file);
/* 1=packet, 0=EOF, -1=malformed/truncated. */
int camera_pcap_next(CameraPcap *r, uint8_t *data, size_t capacity,
                     size_t *size, uint64_t *capture_ns);
#endif
