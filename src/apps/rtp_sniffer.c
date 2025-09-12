#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "rtp/rtp.h"
#include "rtp/rtp_stats.h"

static inline uint64_t now_ns(void){
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec*1000000000ull + (uint64_t)ts.tv_nsec;
}

static void print_packet(const rtp_packet_t *p){
    printf("RTP v%u PT=%u M=%u Seq=%u TS=%u SSRC=0x%08x payload=%zu bytes\n",
           p->version, p->payload_type, p->marker, p->seq, p->timestamp, p->ssrc, p->payload_len);
}

static void print_stats(const rtp_stats_t *st){
    printf("\n=== Stats SSRC=0x%08x ===\n", st->ssrc);
    printf("Recibidos: %u  Perdidos(est): %u  Reorden: %u\n", st->received, st->lost, st->out_of_order);
    printf("Seq base: %u  Seq max ext: %u\n", st->base_seq, st->max_seq_ext);
    printf("Jitter: %.3f ms\n\n", rtp_stats_jitter_ms(st));
}

int main(int argc, char **argv){
    if(argc < 3){
        fprintf(stderr, "Uso: %s <puerto UDP> <clockrate>\n", argv[0]);
        return 1;
    }
    int port = atoi(argv[1]);
    uint32_t clockrate = (uint32_t)strtoul(argv[2], NULL, 10);

    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if(fd < 0){ perror("socket"); return 1; }

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET; addr.sin_addr.s_addr = htonl(INADDR_ANY); addr.sin_port = htons((uint16_t)port);
    if(bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0){ perror("bind"); return 1; }

    printf("Escuchando RTP en UDP:%d con clockrate=%u Hz\n", port, clockrate);

    uint8_t buf[2048];
    rtp_stats_t stats = {0};

    for(;;){
        struct sockaddr_in src; socklen_t slen = sizeof(src);
        ssize_t n = recvfrom(fd, buf, sizeof(buf), 0, (struct sockaddr*)&src, &slen);
        if(n < 0){ if(errno==EINTR) continue; perror("recvfrom"); break; }
        uint64_t t_ns = now_ns();

        rtp_packet_t p;
        if(!rtp_parse(buf, (size_t)n, &p)){
            fprintf(stderr, "Paquete no RTP o malformado (%zd bytes)\n", n);
            continue;
        }

        if(!stats.inited){
            rtp_stats_init(&stats, p.ssrc, p.seq, p.timestamp, clockrate);
        } else if(p.ssrc != stats.ssrc){
            print_stats(&stats);
            rtp_stats_init(&stats, p.ssrc, p.seq, p.timestamp, clockrate);
        }

        rtp_stats_update(&stats, &p, t_ns);
        print_packet(&p);
    }

    if(stats.inited) print_stats(&stats);
    close(fd);
    return 0;
}
