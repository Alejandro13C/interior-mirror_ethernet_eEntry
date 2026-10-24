#define _GNU_SOURCE
#include "camera_rtp.h"
#include "gst_panini.h"
#include "gst_vehicles.h"
#include "stale_frame.h"
#include "h264_sps.h"
#include <math.h>
#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include <arpa/inet.h>
#include <errno.h>
#include <getopt.h>
#include <linux/filter.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopping, input_paused;
static void stop_signal(int signum) { (void)signum; stopping = 1; }
/* Fault injection: SIGUSR1 toggles discarding camera packets, which behaves
   like a cut cable without touching the link (a link down closes the socket). */
static void pause_signal(int signum) { (void)signum; input_paused = !input_paused; }
/* Invalid configuration: systemd must not retry (RestartPreventExitStatus). */
#define EXIT_CONFIG 78
static uint64_t now_ns(clockid_t clock) {
    struct timespec t;
    clock_gettime(clock, &t);
    return (uint64_t)t.tv_sec * GST_SECOND + (uint64_t)t.tv_nsec;
}

typedef struct {
    GstElement *pipeline, *source, *display;
    GstBus *bus;
    bool file, first_ts_set, mirror, rotate180, sps_restrict;
    /* Last camera SPS seen and its rewrite (0 length: left unchanged). */
    uint8_t sps_in[256], sps_out[256];
    size_t sps_in_len, sps_out_len;
    uint32_t first_ts;
    uint64_t submitted, too_old, unsupported, late_display;
    uint64_t latency_count, latency_sum, latency_max;
    uint64_t histogram[501];
    /* Every frame reaching the sink, including late drops: total and per interval. */
    uint64_t age_total[501], age_interval[501];
    uint64_t presented, last_presented_ns, gap_max;
    /* Mean frame age per stage over the stats interval (assembled, decoded, projected). */
    uint64_t stage_sum[3], stage_count[3];
    GMutex lock;
} Player;

enum { STAGE_ASSEMBLED, STAGE_DECODED, STAGE_PROJECTED };
static void stage_add(Player *p, int stage, uint64_t age) {
    g_mutex_lock(&p->lock);
    p->stage_sum[stage] += age;
    p->stage_count[stage]++;
    g_mutex_unlock(&p->lock);
}
static double stage_ms(Player *p, int stage) {
    return p->stage_count[stage] ? (double)p->stage_sum[stage] / p->stage_count[stage] / GST_MSECOND : 0;
}

/* Age of a live buffer (since its first packet) when it leaves a stage. */
static GstPadProbeReturn stage_probe(GstPad *pad, GstPadProbeInfo *info, gpointer ctx) {
    Player *p = ctx;
    GstBuffer *b = GST_PAD_PROBE_INFO_BUFFER(info);
    if (!b || !GST_BUFFER_PTS_IS_VALID(b)) return GST_PAD_PROBE_OK;
    GstClock *clock = gst_element_get_clock(p->pipeline);
    if (!clock) return GST_PAD_PROBE_OK;
    uint64_t running = gst_clock_get_time(clock) - gst_element_get_base_time(p->pipeline);
    gst_object_unref(clock);
    if (running >= GST_BUFFER_PTS(b))
        stage_add(p, GPOINTER_TO_INT(g_object_get_data(G_OBJECT(pad), "stage")), running - GST_BUFFER_PTS(b));
    return GST_PAD_PROBE_OK;
}
static void add_stage_probe(Player *p, const char *element, int stage) {
    GstElement *e = gst_bin_get_by_name(GST_BIN(p->pipeline), element);
    if (!e) return;
    GstPad *pad = gst_element_get_static_pad(e, "src");
    if (pad) {
        g_object_set_data(G_OBJECT(pad), "stage", GINT_TO_POINTER(stage));
        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, stage_probe, p, NULL);
        gst_object_unref(pad);
    }
    gst_object_unref(e);
}

static uint64_t env_ms(const char *name, uint64_t fallback) {
    const char *value = getenv(name);
    if (!value || !*value) return fallback;
    char *end;
    unsigned long long ms = strtoull(value, &end, 10);
    if (*end || ms > 3600000) {
        fprintf(stderr, "Ignoring invalid %s=%s\n", name, value);
        return fallback;
    }
    return ms;
}

/* Upper bound in ms of the bucket holding the given percentile. */
static size_t percentile_ms(const uint64_t *histogram, unsigned percent) {
    uint64_t count = 0, accumulated = 0;
    for (size_t i = 0; i <= 500; ++i) count += histogram[i];
    if (!count) return 0;
    size_t i = 0;
    for (; i < 500; ++i) {
        accumulated += histogram[i];
        if (accumulated * 100 >= count * percent) break;
    }
    return i + 1;
}

static double cpu_seconds(void) {
    struct rusage usage;
    getrusage(RUSAGE_SELF, &usage);
    return usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1e6 +
           usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1e6;
}

/* Replace the picture (and any triangles in it) once frames stop being shown.
   Never blocks: if the sink is busy (or hung) the main loop retries later and
   keeps serving the bus watchdog and the restart deadline. */
typedef enum { STALE_SHOWN, STALE_BUSY, STALE_FAILED } StaleResult;
static StaleResult show_stale_frame(Player *p) {
    GstPad *pad = gst_element_get_static_pad(p->display, "sink");
    if (!pad) return STALE_FAILED;
    if (!GST_PAD_STREAM_TRYLOCK(pad)) { gst_object_unref(pad); return STALE_BUSY; }
    GstCaps *caps = gst_pad_get_current_caps(pad);
    GstBuffer *buffer = camera_stale_frame(caps, p->mirror, p->rotate180);
    if (caps) gst_caps_unref(caps);
    /* The stream lock is recursive: gst_pad_chain takes it again. */
    GstFlowReturn flow = buffer ? gst_pad_chain(pad, buffer) : GST_FLOW_ERROR;
    GST_PAD_STREAM_UNLOCK(pad);
    gst_object_unref(pad);
    return flow == GST_FLOW_OK ? STALE_SHOWN : STALE_FAILED;
}

static bool has_idr(const uint8_t *b, size_t n) {
    for (size_t i = 0; i + 3 < n; ++i)
        if (!b[i] && !b[i + 1] && b[i + 2] == 1 && (b[i + 3] & 31) == 5)
            return true;
    return false;
}

/* Replace the frame's leading SPS by one declaring no reordering, so the
   hardware decoder outputs each picture at once instead of holding it until
   the next frame (measured: 56 -> 27 ms). Returns the (possibly new) buffer. */
static uint8_t *restrict_sps(Player *p, uint8_t *b, size_t *n) {
    size_t start = 0;
    while (start + 3 < *n && start < 8 && !(b[start] == 0 && b[start + 1] == 0 && b[start + 2] == 1)) start++;
    start += 3;
    if (start >= *n || (b[start] & 31) != 7) return b;
    size_t end = start + 1;
    while (end + 2 < *n && !(b[end] == 0 && b[end + 1] == 0 && b[end + 2] == 1)) end++;
    if (end + 2 >= *n) return b;
    while (end > start && b[end - 1] == 0) end--; /* zeros belong to the next start code */
    size_t length = end - start;
    if (length > sizeof p->sps_in) return b;
    if (length != p->sps_in_len || memcmp(p->sps_in, b + start, length)) {
        memcpy(p->sps_in, b + start, length);
        p->sps_in_len = length;
        p->sps_out_len = h264_sps_restrict(b + start, length, p->sps_out, sizeof p->sps_out);
        fprintf(stderr, "Camera SPS %s\n", p->sps_out_len ?
            "rewritten with bitstream_restriction (no reordering): decoder outputs each frame at once" :
            "left unchanged (unsupported layout or already restricted)");
    }
    if (!p->sps_out_len) return b;
    size_t grown = *n - length + p->sps_out_len;
    uint8_t *out = malloc(grown);
    if (!out) return b;
    memcpy(out, b, start);
    memcpy(out + start, p->sps_out, p->sps_out_len);
    memcpy(out + start + p->sps_out_len, b + end, *n - end);
    free(b);
    *n = grown;
    return out;
}

static bool emit_frame(void *ctx, uint8_t *b, size_t n, uint32_t ts,
                       uint64_t first_arrival) {
    Player *p = ctx;
    /* The observed camera repeats SPS/PPS/IDR on every timestamp. This makes
       skipping a complete old frame safe without retaining reference frames. */
    if (!has_idr(b, n)) {
        /* Only all-intra streams are supported; P-frames would freeze the
           picture at the last IDR, so say it once instead of failing silently. */
        if (!p->unsupported++)
            fprintf(stderr, "Camera sends non-IDR frames: only intra frames are shown; check the camera's GOP\n");
        free(b); return true;
    }
    uint64_t now = now_ns(CLOCK_MONOTONIC);
    if (!p->file && now - first_arrival > 150 * GST_MSECOND) {
        p->too_old++; free(b); return true;
    }
    if (p->sps_restrict) b = restrict_sps(p, b, &n);
    GstBuffer *buffer = gst_buffer_new_wrapped(b, n); /* Takes ownership. */
    if (!buffer) { free(b); return false; }
    if (p->file) {
        if (!p->first_ts_set) { p->first_ts = ts; p->first_ts_set = true; }
        GST_BUFFER_PTS(buffer) = (uint64_t)(uint32_t)(ts - p->first_ts) * GST_SECOND / 90000;
    } else {
        GstClock *clock = gst_element_get_clock(p->pipeline);
        GstClockTime running = gst_clock_get_time(clock) - gst_element_get_base_time(p->pipeline);
        gst_object_unref(clock);
        uint64_t age = now - first_arrival;
        GST_BUFFER_PTS(buffer) = running > age ? running - age : 0;
    }
    GST_BUFFER_DURATION(buffer) = GST_CLOCK_TIME_NONE;
    if (!p->file) stage_add(p, STAGE_ASSEMBLED, now - first_arrival);
    GstFlowReturn flow = gst_app_src_push_buffer(GST_APP_SRC(p->source), buffer);
    if (flow != GST_FLOW_OK) {
        fprintf(stderr, "push-buffer: %s\n", gst_flow_get_name(flow));
        return false;
    }
    p->submitted++;
    return true;
}

/* Measures first packet received -> decoded buffer reaching the video sink.
   It excludes camera encoding and panel scanout: NOT glass-to-glass latency. */
static GstPadProbeReturn decoded_probe(GstPad *pad, GstPadProbeInfo *info, gpointer ctx) {
    (void)pad;
    Player *p = ctx;
    GstBuffer *b = GST_PAD_PROBE_INFO_BUFFER(info);
    if (!b || p->file || !GST_BUFFER_PTS_IS_VALID(b)) return GST_PAD_PROBE_OK;
    GstClock *clock = gst_element_get_clock(p->pipeline);
    uint64_t running = gst_clock_get_time(clock) - gst_element_get_base_time(p->pipeline);
    gst_object_unref(clock);
    uint64_t pts = GST_BUFFER_PTS(b);
    if (running < pts) return GST_PAD_PROBE_OK;
    uint64_t age = running - pts;
    size_t bucket = age / GST_MSECOND;
    if (bucket > 500) bucket = 500;
    g_mutex_lock(&p->lock);
    p->age_total[bucket]++;
    p->age_interval[bucket]++;
    /* Map preparation or overload must not put an old live frame on screen. */
    if (age > 150 * GST_MSECOND) {
        p->late_display++;
        g_mutex_unlock(&p->lock);
        return GST_PAD_PROBE_DROP;
    }
    uint64_t now = now_ns(CLOCK_MONOTONIC);
    if (p->last_presented_ns && now - p->last_presented_ns > p->gap_max)
        p->gap_max = now - p->last_presented_ns;
    p->last_presented_ns = now;
    p->presented++;
    p->latency_count++;
    p->latency_sum += age;
    if (age > p->latency_max) p->latency_max = age;
    p->histogram[bucket]++;
    g_mutex_unlock(&p->lock);
    return GST_PAD_PROBE_OK;
}

static bool player_error(Player *p) {
    GstMessage *m = gst_bus_pop_filtered(p->bus, GST_MESSAGE_ERROR);
    if (!m) return false;
    GError *error = NULL;
    gchar *details = NULL;
    gst_message_parse_error(m, &error, &details);
    fprintf(stderr, "%s: %s\n", GST_OBJECT_NAME(m->src), error->message);
    if (details) fprintf(stderr, "%s\n", details);
    g_clear_error(&error); g_free(details); gst_message_unref(m);
    return true;
}

static void sink_stats(GstElement *display, uint64_t *rendered, uint64_t *dropped) {
    *rendered = *dropped = 0;
    if (!g_object_class_find_property(G_OBJECT_GET_CLASS(display), "stats")) return;
    GstStructure *stats = NULL;
    g_object_get(display, "stats", &stats, NULL);
    if (!stats) return;
    gst_structure_get_uint64(stats, "rendered", rendered);
    gst_structure_get_uint64(stats, "dropped", dropped);
    gst_structure_free(stats);
}

/* PACKET_STATISTICS resets on every read, so callers accumulate it. */
static void socket_stats(int fd, uint64_t *packets, uint64_t *drops) {
    struct tpacket_stats stats;
    socklen_t length = sizeof stats;
    if (fd >= 0 && !getsockopt(fd, SOL_PACKET, PACKET_STATISTICS, &stats, &length)) {
        *packets += stats.tp_packets;
        *drops += stats.tp_drops;
    }
}

static int packet_socket(const char *interface) {
    unsigned index = if_nametoindex(interface);
    if (!index) { perror("interface"); return -1; }
    int fd = socket(AF_PACKET, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, htons(ETH_P_ALL));
    if (fd < 0) { perror("socket AF_PACKET (requires CAP_NET_RAW)"); return -1; }
    struct sockaddr_ll address = {.sll_family = AF_PACKET,
        .sll_protocol = htons(ETH_P_ALL), .sll_ifindex = (int)index};
    if (bind(fd, (struct sockaddr *)&address, sizeof address) < 0) goto fail;
    /* Source MAC filter in kernel, independently of IPv6/VLAN offload layout. */
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, 6),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0x443c8824, 0, 3),
        BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 10),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0x7022, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, 65536), BPF_STMT(BPF_RET | BPF_K, 0)
    };
    struct sock_fprog program = {.len = sizeof filter / sizeof filter[0], .filter = filter};
    if (setsockopt(fd, SOL_SOCKET, SO_ATTACH_FILTER, &program, sizeof program) < 0) goto fail;
    struct packet_mreq membership = {.mr_ifindex = (int)index, .mr_type = PACKET_MR_PROMISC};
    if (setsockopt(fd, SOL_PACKET, PACKET_ADD_MEMBERSHIP, &membership, sizeof membership) < 0) goto fail;
    int size = 256 * 1024, yes = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &size, sizeof size) < 0) goto fail;
    if (setsockopt(fd, SOL_SOCKET, SO_TIMESTAMPNS, &yes, sizeof yes) < 0) goto fail;
    return fd;
fail:
    perror("packet socket setup"); close(fd); return -1;
}

static ssize_t receive_packet(int fd, uint8_t *b, size_t size, uint64_t *arrival) {
    struct iovec iov = {.iov_base = b, .iov_len = size};
    union { struct cmsghdr align; uint8_t bytes[CMSG_SPACE(sizeof(struct timespec))]; } control;
    struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = sizeof control.bytes};
    ssize_t n = recvmsg(fd, &msg, MSG_DONTWAIT | MSG_TRUNC);
    *arrival = now_ns(CLOCK_MONOTONIC);
    if (n < 0 || (msg.msg_flags & MSG_TRUNC)) return n < 0 ? n : 0;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_TIMESTAMPNS &&
            c->cmsg_len >= CMSG_LEN(sizeof(struct timespec))) {
            struct timespec stamp;
            memcpy(&stamp, CMSG_DATA(c), sizeof stamp);
            uint64_t kernel = (uint64_t)stamp.tv_sec * GST_SECOND + (uint64_t)stamp.tv_nsec;
            uint64_t real = now_ns(CLOCK_REALTIME);
            /* Ignore impossible ages following a wall-clock adjustment. */
            if (real >= kernel && real - kernel < 5 * GST_SECOND) {
                uint64_t age = real - kernel;
                *arrival = *arrival > age ? *arrival - age : 0;
            }
        }
    }
    return n;
}

static void usage(const char *name) {
    fprintf(stderr, "Usage: %s [--interface eth0 | --file capture.pcap] [--hardware] "
            "[--kms | --headless] [--mirror] [--rotate 0|180] [--duration seconds] [--detect]\n"
            "       [--panini [--compression 0..2] [--lens-fov 90..220] "
            "[--view-fov 40..160] [--pitch -45..45]]\n", name);
}

int main(int argc, char **argv) {
    const char *interface = "eth0", *filename = NULL;
    bool hardware = false, kms = false, headless = false, mirror = false;
    unsigned rotation = 0;
    bool panini = false, panini_options = false, detect = false;
    PaniniConfig projection = {1, 180, 130, 0, 0}; /* deployed view: 130 degrees */
    double duration = 0;
    static const struct option options[] = {
        {"interface", required_argument, NULL, 'i'}, {"file", required_argument, NULL, 'f'},
        {"hardware", no_argument, NULL, 'H'}, {"kms", no_argument, NULL, 'k'},
        {"headless", no_argument, NULL, 'n'}, {"duration", required_argument, NULL, 'd'},
        {"mirror", no_argument, NULL, 'm'}, {"rotate", required_argument, NULL, 'r'},
        {"panini", no_argument, NULL, 'P'}, {"detect", no_argument, NULL, 'D'},
        {"compression", required_argument, NULL, 'C'}, {"lens-fov", required_argument, NULL, 'L'},
        {"view-fov", required_argument, NULL, 'V'}, {"pitch", required_argument, NULL, 'T'},
        {"help", no_argument, NULL, 'h'}, {NULL, 0, NULL, 0}
    };
    int c;
    while ((c = getopt_long(argc, argv, "", options, NULL)) != -1) {
        switch (c) {
        case 'i': interface = optarg; break;
        case 'f': filename = optarg; break;
        case 'H': hardware = true; break;
        case 'k': kms = true; break;
        case 'n': headless = true; break;
        case 'm': mirror = true; break;
        case 'r':
            if (!strcmp(optarg, "0")) rotation = 0;
            else if (!strcmp(optarg, "180")) rotation = 180;
            else { usage(argv[0]); return EXIT_CONFIG; }
            break;
        case 'd': {
            char *end;
            duration = strtod(optarg, &end);
            if (*end || !(duration > 0) || duration > 86400) { usage(argv[0]); return EXIT_CONFIG; }
            break;
        }
        case 'P': panini = true; break;
        case 'D': detect = true; break;
        case 'C': case 'L': case 'V': case 'T': {
            char *end;
            double value = strtod(optarg, &end);
            if (!*optarg || *end || !isfinite(value)) { usage(argv[0]); return EXIT_CONFIG; }
            if (c == 'C') projection.compression = value;
            else if (c == 'L') projection.lens_fov = value;
            else if (c == 'V') projection.view_fov = value;
            else projection.pitch = value;
            panini_options = true;
            break;
        }
        case 'h': usage(argv[0]); return 0;
        default: usage(argv[0]); return EXIT_CONFIG;
        }
    }
    /* Display pixel shape, "16/15" or decimal (width/height of one pixel). */
    if (getenv("CAMERA_DISPLAY_PAR") && *getenv("CAMERA_DISPLAY_PAR")) {
        const char *value = getenv("CAMERA_DISPLAY_PAR");
        char *end;
        double ratio = strtod(value, &end);
        if (*end == '/') { double den = strtod(end + 1, &end); ratio = den > 0 ? ratio / den : NAN; }
        if (*end || !isfinite(ratio)) { fprintf(stderr, "Invalid CAMERA_DISPLAY_PAR=%s\n", value); return EXIT_CONFIG; }
        projection.pixel_aspect = ratio;
    }
    if (optind != argc || (kms && headless) || ((mirror || rotation) && !kms) ||
        !panini_valid(&projection) || (panini_options && !panini)) { usage(argv[0]); return EXIT_CONFIG; }
    struct sigaction sa = {.sa_handler = stop_signal};
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL); sigaction(SIGTERM, &sa, NULL);
    struct sigaction pause_action = {.sa_handler = pause_signal};
    sigemptyset(&pause_action.sa_mask);
    sigaction(SIGUSR1, &pause_action, NULL);
    gst_init(NULL, NULL);
    if (panini && !camera_panini_register()) {
        fprintf(stderr, "Cannot register Panini filter\n"); return 1;
    }
    if (detect && !camera_vehicles_register()) {
        fprintf(stderr, "Cannot register vehicle overlay\n"); return 1;
    }
    int result = 0, fd = -1;
    FILE *file = NULL;
    CameraPcap reader = {0};
    if (filename) {
        file = fopen(filename, "rb");
        if (!file || !camera_pcap_open(&reader, file)) {
            fprintf(stderr, "Invalid or unreadable Ethernet PCAP\n");
            if (file) fclose(file);
            return 1;
        }
    }

    const char *sps = getenv("CAMERA_SPS_RESTRICT");
    Player p = {.file = filename != NULL, .mirror = mirror, .rotate180 = rotation == 180,
                .sps_restrict = !sps || strcmp(sps, "0")};
    g_mutex_init(&p.lock);
    const char *decoder = hardware ? "h264parse ! v4l2h264dec name=decoder" : "avdec_h264 name=decoder";
    const char *sink = headless ? "fakesink name=display sync=false" :
        kms ? (filename ? "kmssink name=display driver-name=vc4 sync=true" :
                          "kmssink name=display driver-name=vc4 sync=false") :
              "videoconvert ! autovideosink name=display sync=true";
    gchar *description = g_strdup_printf(
        "appsrc name=input format=time is-live=true block=%s emit-signals=false "
        "max-buffers=2 max-bytes=0 leaky-type=%s "
        "caps=\"video/x-h264,stream-format=byte-stream,alignment=au\" ! %s ! %s %s %s %s",
        filename ? "true" : "false", filename ? "none" : "downstream",
        decoder, panini ? (filename ? "video/x-raw,format=I420 ! camerapanini name=projection !" :
            "queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
            "video/x-raw,format=I420 ! camerapanini name=projection ! "
            "queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream !") : "",
        detect ? "video/x-raw,format=I420 ! cameravehicles !" : "",
        filename ? "" : "watchdog timeout=3000 !", sink);
    GError *error = NULL;
    p.pipeline = gst_parse_launch(description, &error);
    g_free(description);
    if (error || !p.pipeline) {
        fprintf(stderr, "Pipeline: %s\n", error ? error->message : "unknown error");
        g_clear_error(&error); result = 1; goto cleanup;
    }
    if (panini) {
        GstElement *warp = gst_bin_get_by_name(GST_BIN(p.pipeline), "projection");
        camera_panini_configure(warp, &projection);
        gst_object_unref(warp);
        fprintf(stderr, "Panini APPROXIMATE lens: compression=%.3f lens_hfov=%.1f "
            "view_hfov=%.1f pitch=%.1f output=800x480 display_pixel_aspect=%.4f\n", projection.compression,
            projection.lens_fov, projection.view_fov, projection.pitch,
            projection.pixel_aspect > 0 ? projection.pixel_aspect : 1.0);
    }
    p.source = gst_bin_get_by_name(GST_BIN(p.pipeline), "input");
    p.display = gst_bin_get_by_name(GST_BIN(p.pipeline), "display");
    /* DRM rotation/reflection operates in the display plane, without CPU copies.
       Set identity too: a preceding preview may have left the plane reflected. */
    if (kms) {
        guint64 transform = (rotation == 180 ? 4 : 1) | (mirror ? 16 : 0);
        GstStructure *properties = gst_structure_new("plane-properties",
            "rotation", G_TYPE_UINT64, transform, NULL);
        g_object_set(p.display, "plane-properties", properties, NULL);
        gst_structure_free(properties);
        fprintf(stderr, "Display: mirror=%s rotation=%u\n", mirror ? "yes" : "no", rotation);
    }
    p.bus = gst_element_get_bus(p.pipeline);
    if (!filename) {
        add_stage_probe(&p, "decoder", STAGE_DECODED);
        add_stage_probe(&p, "projection", STAGE_PROJECTED);
    }
    GstPad *pad = gst_element_get_static_pad(p.display, "sink");
    if (pad) {
        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, decoded_probe, &p, NULL);
        gst_object_unref(pad);
    }
    if (gst_element_set_state(p.pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) {
        player_error(&p); result = 1; goto cleanup;
    }
    /* Open capture after plugin/device initialization, so startup work cannot
       fill the receive queue with old packets before we begin draining it. */
    if (!filename && (fd = packet_socket(interface)) < 0) {
        result = 1; goto cleanup;
    }
    uint64_t started = now_ns(CLOCK_MONOTONIC);
    CameraAssembler assembler = {0};
    uint8_t *packet = malloc(65536);
    if (!packet) { result = 1; goto cleanup; }
    fprintf(stderr, "Receiver C: %s, %s\n", filename ? filename : interface,
            hardware ? "hardware H.264" : "software H.264");
    /* A frozen picture looks live. Blank it shortly after frames stop being
       shown, even if buffers still flow; restart if that persists. */
    uint64_t stale_limit = filename ? 0 : env_ms("CAMERA_STALE_MS", 300) * GST_MSECOND;
    uint64_t stale_restart = env_ms("CAMERA_STALE_RESTART_MS", 5000) * GST_MSECOND;
    uint64_t stats_interval = filename ? 0 : env_ms("CAMERA_STATS_MS", 10000) * GST_MSECOND;
    uint64_t stale_since = 0, stale_submitted = 0, stale_events = 0;
    bool blanked = false, blank_failed = false, paused = false;
    uint64_t socket_packets = 0, socket_drops = 0;
    struct {
        uint64_t at, packets, damaged, sequence_errors, submitted, too_old, unsupported;
        uint64_t late, presented, rendered, socket_drops, stale_events;
        double cpu;
    } mark = {.at = started, .cpu = cpu_seconds()};
    while (!stopping) {
        uint64_t now = now_ns(CLOCK_MONOTONIC);
        if (duration && (double)(now - started) / GST_SECOND >= duration) break;
        if (player_error(&p)) { result = 1; break; }
        if (stale_limit) {
            g_mutex_lock(&p.lock);
            uint64_t last = p.last_presented_ns;
            g_mutex_unlock(&p.lock);
            if (last && now > last && now - last > stale_limit) {
                if (!stale_since) {
                    stale_since = last; stale_submitted = p.submitted; stale_events++;
                    blanked = blank_failed = false;
                }
                if (!blanked && !blank_failed) {
                    StaleResult shown = show_stale_frame(&p);
                    blanked = shown == STALE_SHOWN;
                    blank_failed = shown == STALE_FAILED;
                    /* A busy sink is retried on the next pass (at most 50 ms later). */
                    if (shown != STALE_BUSY)
                        fprintf(stderr, "Video stale: no fresh frame for %.0f ms; %s\n",
                                (double)(now - last) / GST_MSECOND,
                                blanked ? "display blanked" : "blanking failed");
                }
                /* The bus watchdog (3 s) covers a silent camera; this covers frames
                   that keep arriving but never reach the screen. */
                if (stale_restart && now - last > stale_restart && p.submitted > stale_submitted) {
                    fprintf(stderr, "Frames arrive but none displayed for %.0f ms; restarting\n",
                            (double)(now - last) / GST_MSECOND);
                    result = 1; break;
                }
            } else if (stale_since && last > stale_since) {
                fprintf(stderr, "Video resumed after %.0f ms without a fresh frame\n",
                        (double)(last - stale_since) / GST_MSECOND);
                stale_since = 0;
            }
        }
        if (stats_interval && now - mark.at >= stats_interval) {
            uint64_t rendered, sink_dropped;
            sink_stats(p.display, &rendered, &sink_dropped);
            socket_stats(fd, &socket_packets, &socket_drops);
            g_mutex_lock(&p.lock);
            size_t p50 = percentile_ms(p.age_interval, 50), p95 = percentile_ms(p.age_interval, 95);
            size_t p99 = percentile_ms(p.age_interval, 99);
            uint64_t late = p.late_display, presented = p.presented, gap = p.gap_max;
            double assembled = stage_ms(&p, STAGE_ASSEMBLED), decoded = stage_ms(&p, STAGE_DECODED);
            double projected = stage_ms(&p, STAGE_PROJECTED);
            memset(p.age_interval, 0, sizeof p.age_interval);
            memset(p.stage_sum, 0, sizeof p.stage_sum);
            memset(p.stage_count, 0, sizeof p.stage_count);
            p.gap_max = 0;
            g_mutex_unlock(&p.lock);
            double seconds = (double)(now - mark.at) / GST_SECOND, cpu = cpu_seconds();
            fprintf(stderr, "stats: t=%.1f fps=%.2f presented=%" G_GUINT64_FORMAT
                " rendered=%" G_GUINT64_FORMAT " late_display=%" G_GUINT64_FORMAT
                " damaged=%" G_GUINT64_FORMAT " sequence_errors=%" G_GUINT64_FORMAT
                " stale=%" G_GUINT64_FORMAT " non_idr=%" G_GUINT64_FORMAT
                " submitted=%" G_GUINT64_FORMAT " packets=%" G_GUINT64_FORMAT
                " socket_dropped=%" G_GUINT64_FORMAT " stale_events=%" G_GUINT64_FORMAT
                " age_all_ms p50_lt=%zu p95_lt=%zu p99_lt=%zu max_gap_ms=%.1f cpu_core=%.1f%%"
                " stage_mean_ms assembled=%.1f decoded=%.1f projected=%.1f\n",
                (double)(now - started) / GST_SECOND, (presented - mark.presented) / seconds,
                presented - mark.presented, rendered - mark.rendered, late - mark.late,
                assembler.damaged - mark.damaged, assembler.sequence_errors - mark.sequence_errors,
                p.too_old - mark.too_old, p.unsupported - mark.unsupported,
                p.submitted - mark.submitted, assembler.packets - mark.packets,
                socket_drops - mark.socket_drops, stale_events - mark.stale_events,
                p50, p95, p99, (double)gap / GST_MSECOND, 100 * (cpu - mark.cpu) / seconds,
                assembled, decoded, projected);
            mark.at = now; mark.packets = assembler.packets; mark.damaged = assembler.damaged;
            mark.sequence_errors = assembler.sequence_errors; mark.submitted = p.submitted;
            mark.too_old = p.too_old; mark.unsupported = p.unsupported; mark.late = late;
            mark.presented = presented; mark.rendered = rendered; mark.socket_drops = socket_drops;
            mark.stale_events = stale_events; mark.cpu = cpu;
        }
        size_t size = 0;
        uint64_t arrival = 0;
        if (file) {
            int status = camera_pcap_next(&reader, packet, 65536, &size, &arrival);
            if (status <= 0) {
                if (status < 0) { fprintf(stderr, "Truncated PCAP\n"); result = 1; }
                break;
            }
        } else {
            struct pollfd pollfd = {.fd = fd, .events = POLLIN};
            int ready = poll(&pollfd, 1, 50);
            if (ready < 0) { if (errno == EINTR) continue; perror("poll"); result = 1; break; }
            if (!ready) continue;
            if (pollfd.revents & (POLLERR | POLLHUP | POLLNVAL)) { result = 1; break; }
            ssize_t n = receive_packet(fd, packet, 65536, &arrival);
            if (n < 0) {
                if (errno == EAGAIN || errno == EINTR) continue;
                perror("recvmsg"); result = 1; break;
            }
            if (!n) continue;
            size = (size_t)n;
            if (input_paused != paused) {
                paused = input_paused;
                fprintf(stderr, "Camera input %s (SIGUSR1)\n", paused ? "paused" : "resumed");
            }
            if (paused) continue;
        }
        CameraPacket rtp;
        if (camera_packet(packet, size, &rtp) &&
            !camera_assemble(&assembler, &rtp, arrival, emit_frame, &p)) {
            result = 1; break;
        }
    }
    free(packet);
    camera_assembler_clear(&assembler);
    if (!result) {
        gst_app_src_end_of_stream(GST_APP_SRC(p.source));
        GstMessage *m = gst_bus_timed_pop_filtered(p.bus, 3 * GST_SECOND,
                                                  GST_MESSAGE_EOS | GST_MESSAGE_ERROR);
        if (!m || GST_MESSAGE_TYPE(m) == GST_MESSAGE_ERROR) {
            fprintf(stderr, "Decoder failed to drain\n"); result = 1;
        }
        if (m) gst_message_unref(m);
    }
    uint64_t rendered, dropped;
    sink_stats(p.display, &rendered, &dropped);
    g_mutex_lock(&p.lock);
    uint64_t late_display = p.late_display;
    g_mutex_unlock(&p.lock);
    struct rusage usage_stats;
    getrusage(RUSAGE_SELF, &usage_stats);
    double cpu = cpu_seconds();
    fprintf(stderr, "packets=%" G_GUINT64_FORMAT " submitted=%" G_GUINT64_FORMAT
        " rendered=%" G_GUINT64_FORMAT " sink_dropped=%" G_GUINT64_FORMAT
        " damaged=%" G_GUINT64_FORMAT " sequence_errors=%" G_GUINT64_FORMAT
        " stale=%" G_GUINT64_FORMAT " non_idr=%" G_GUINT64_FORMAT " late_display=%" G_GUINT64_FORMAT
        " wall_s=%.3f cpu_s=%.3f max_rss_kb=%ld\n",
        assembler.packets, p.submitted, rendered, dropped, assembler.damaged,
        assembler.sequence_errors, p.too_old, p.unsupported, late_display,
        (double)(now_ns(CLOCK_MONOTONIC) - started) / GST_SECOND, cpu, usage_stats.ru_maxrss);
    fprintf(stderr, "rtp: duplicates=%" G_GUINT64_FORMAT " late_packets=%" G_GUINT64_FORMAT
        " resyncs=%" G_GUINT64_FORMAT " complete=%" G_GUINT64_FORMAT "\n",
        assembler.duplicates, assembler.late_packets, assembler.resyncs, assembler.complete);
    g_mutex_lock(&p.lock);
    if (!file && p.latency_count) {
        uint64_t accumulated = 0;
        size_t p95 = 0;
        for (; p95 < 500; ++p95) {
            accumulated += p.histogram[p95];
            if (accumulated * 100 >= p.latency_count * 95) break;
        }
        fprintf(stderr, "local_first_packet_to_sink_ms: mean=%.2f p95_lt=%zu max=%.2f (excludes camera/panel)\n",
            (double)p.latency_sum / p.latency_count / GST_MSECOND, p95 + 1,
            (double)p.latency_max / GST_MSECOND);
        /* Same clock, but counting the frames dropped as late as well. */
        fprintf(stderr, "all_frames_age_ms: p50_lt=%zu p95_lt=%zu p99_lt=%zu presented=%" G_GUINT64_FORMAT
            " stale_events=%" G_GUINT64_FORMAT "\n", percentile_ms(p.age_total, 50),
            percentile_ms(p.age_total, 95), percentile_ms(p.age_total, 99), p.presented, stale_events);
    }
    g_mutex_unlock(&p.lock);
    if (fd >= 0) {
        socket_stats(fd, &socket_packets, &socket_drops);
        fprintf(stderr, "socket_packets=%" G_GUINT64_FORMAT " socket_dropped=%" G_GUINT64_FORMAT "\n",
                socket_packets, socket_drops);
    }
cleanup:
    if (p.pipeline) gst_element_set_state(p.pipeline, GST_STATE_NULL);
    if (p.bus) gst_object_unref(p.bus);
    if (p.source) gst_object_unref(p.source);
    if (p.display) gst_object_unref(p.display);
    if (p.pipeline) gst_object_unref(p.pipeline);
    g_mutex_clear(&p.lock);
    if (file) fclose(file);
    if (fd >= 0) close(fd);
    return result;
}
