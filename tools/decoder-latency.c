/* Hardware decoder latency on an Annex B camera stream (each frame starts with
   its SPS): push one frame every 40 ms, as the camera does, and time when each
   decoded frame comes out. Tells a decoder that holds a picture until the next
   one arrives apart from one that outputs as soon as it has decoded it.
   Usage: decoder-latency stream.h264 [decoder]   (default v4l2h264dec) */
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) { fprintf(stderr, "Usage: %s stream.h264 [decoder]\n", argv[0]); return 2; }
    gst_init(&argc, &argv);
    gchar *data; gsize size;
    if (!g_file_get_contents(argv[1], &data, &size, NULL)) { perror(argv[1]); return 1; }
    /* Frame boundaries: every frame begins with 00 00 00 01 67 (SPS). */
    size_t starts[4096], count = 0;
    for (size_t i = 0; i + 5 <= size && count < 4096; ++i)
        if (!memcmp(data + i, "\0\0\0\1\x67", 5)) starts[count++] = i;
    if (count < 10) { fprintf(stderr, "Too few frames\n"); return 1; }
    gchar *description = g_strdup_printf("appsrc name=src is-live=true format=time "
        "caps=video/x-h264,stream-format=byte-stream,alignment=au ! h264parse ! %s ! "
        "appsink name=sink sync=false max-buffers=64", argc == 3 ? argv[2] : "v4l2h264dec");
    GError *error = NULL;
    GstElement *pipeline = gst_parse_launch(description, &error);
    g_free(description);
    if (!pipeline) { fprintf(stderr, "%s\n", error->message); return 1; }
    GstElement *src = gst_bin_get_by_name(GST_BIN(pipeline), "src");
    GstElement *sink = gst_bin_get_by_name(GST_BIN(pipeline), "sink");
    gst_element_set_state(pipeline, GST_STATE_PLAYING);
    gint64 *pushed = g_new0(gint64, count), *latency = g_new0(gint64, count);
    size_t outputs = 0, held_after_next = 0;
    gint64 next = g_get_monotonic_time();
    for (size_t i = 0; i + 1 < count; ++i) {
        size_t n = starts[i + 1] - starts[i];
        GstBuffer *b = gst_buffer_new_memdup(data + starts[i], n);
        GST_BUFFER_PTS(b) = (GstClockTime)i * 40 * GST_MSECOND;
        pushed[i] = g_get_monotonic_time();
        gst_app_src_push_buffer(GST_APP_SRC(src), b);
        next += 40000;
        /* Collect whatever comes out until the next frame is due. */
        for (;;) {
            gint64 wait = next - g_get_monotonic_time();
            if (wait <= 0) break;
            GstSample *s = gst_app_sink_try_pull_sample(GST_APP_SINK(sink), (GstClockTime)wait * 1000);
            if (!s) continue;
            size_t k = GST_BUFFER_PTS(gst_sample_get_buffer(s)) / (40 * GST_MSECOND);
            if (k < count && !latency[k]) {
                latency[k] = g_get_monotonic_time() - pushed[k];
                outputs++;
                if (k < i) held_after_next++;
            }
            gst_sample_unref(s);
        }
    }
    gst_app_src_end_of_stream(GST_APP_SRC(src));
    gst_element_set_state(pipeline, GST_STATE_NULL);
    /* Skip the first 5 frames (decoder start-up). */
    gint64 sum = 0, max = 0; size_t used = 0;
    for (size_t i = 5; i + 1 < count; ++i) if (latency[i]) { sum += latency[i]; max = MAX(max, latency[i]); used++; }
    printf("{\"frames\":%zu,\"decoded\":%zu,\"mean_ms\":%.2f,\"max_ms\":%.2f,"
           "\"output_only_after_next_frame\":%zu}\n", count - 1, outputs,
           used ? sum / 1000.0 / used : 0, max / 1000.0, held_after_next);
    gst_object_unref(src); gst_object_unref(sink); gst_object_unref(pipeline);
    g_free(pushed); g_free(latency); g_free(data);
    return 0;
}
