#include "gst_panini.h"
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>
#include <glib/gstdio.h>
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

/* Runs two frames through the filter; returns an FNV-1a hash of the first. */
static guint64 run(const char *pattern, PaniniConfig c, bool check_white) {
    gchar *description = g_strdup_printf("videotestsrc num-buffers=2 pattern=%s ! "
        "video/x-raw,format=I420,width=1344,height=968 ! "
        "camerapanini name=warp ! appsink name=output sync=false", pattern);
    GError *error = NULL;
    GstElement *pipeline = gst_parse_launch(description, &error);
    g_free(description);
    assert(pipeline && !error);
    GstElement *warp = gst_bin_get_by_name(GST_BIN(pipeline), "warp");
    camera_panini_configure(warp, &c);
    GstElement *sink = gst_bin_get_by_name(GST_BIN(pipeline), "output");
    assert(gst_element_set_state(pipeline, GST_STATE_PLAYING) != GST_STATE_CHANGE_FAILURE);
    guint64 hash = 14695981039346656037ull;
    for (int frame = 0; frame < 2; ++frame) {
        GstSample *sample = gst_app_sink_try_pull_sample(GST_APP_SINK(sink), 15*GST_SECOND);
        assert(sample);
        GstVideoInfo info;
        assert(gst_video_info_from_caps(&info, gst_sample_get_caps(sample)));
        assert(info.width == 800 && info.height == 480 && info.finfo->format == GST_VIDEO_FORMAT_I420);
        GstVideoFrame mapped;
        assert(gst_video_frame_map(&mapped, &info, gst_sample_get_buffer(sample), GST_MAP_READ));
        for (int plane = 0; plane < 3; ++plane) {
            const guint8 *pixels = GST_VIDEO_FRAME_PLANE_DATA(&mapped, plane);
            int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&mapped, plane);
            int w = GST_VIDEO_FRAME_COMP_WIDTH(&mapped, plane);
            int h = GST_VIDEO_FRAME_COMP_HEIGHT(&mapped, plane);
            for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
                guint8 value = pixels[y*stride+x];
                if (!frame) { hash ^= value; hash *= 1099511628211ull; }
                if (!check_white) continue;
                if (plane) assert(value == 128);
                else assert(value == 16 || value == 235);
            }
        }
        /* Upper-right corner is outside the assumed narrow lens; render black. */
        if (check_white) assert(((const guint8 *)GST_VIDEO_FRAME_PLANE_DATA(&mapped, 0))[799] == 16);
        gst_video_frame_unmap(&mapped);
        gst_sample_unref(sample);
    }
    gst_element_set_state(pipeline, GST_STATE_NULL);
    gst_object_unref(sink); gst_object_unref(warp); gst_object_unref(pipeline);
    return hash;
}

int main(int argc, char **argv) {
    gst_init(&argc, &argv);
    assert(camera_panini_register());
    g_unsetenv("CACHE_DIRECTORY");
    g_setenv("CAMERA_PANINI_THREADS", "1", TRUE); /* single-thread reference */
    run("white", (PaniniConfig){1,90,160,45,0}, true); /* Intentionally exposes out-of-source corners. */
    PaniniConfig live = {1,180,130,0,0};
    guint64 computed = run("smpte", live, false);
    /* Map cache: written once, reused bit-exactly, corruption rejected. */
    gchar *dir = g_dir_make_tmp("panini-cache-XXXXXX", NULL);
    assert(dir);
    g_setenv("CACHE_DIRECTORY", dir, TRUE);
    assert(run("smpte", live, false) == computed);
    gchar *path = g_strdup_printf("%s/panini-v3-1344x968-1-180-130-0-1.map", dir);
    gchar *data; gsize size;
    assert(g_file_get_contents(path, &data, &size, NULL) && size > 1000000);
    assert(run("smpte", live, false) == computed);
    data[size / 2] ^= 0x55; /* An offset or weight in the middle, rarely a sampled entry. */
    assert(g_file_set_contents(path, data, (gssize)size, NULL));
    assert(run("smpte", live, false) == computed);
    gchar *rewritten; gsize rewritten_size;
    assert(g_file_get_contents(path, &rewritten, &rewritten_size, NULL));
    assert(rewritten_size == size && memcmp(rewritten, data, size)); /* rebuilt after rejection */
    g_free(rewritten); g_free(data);
    g_unlink(path); g_rmdir(dir); g_free(path); g_free(dir);
    g_unsetenv("CACHE_DIRECTORY");
    /* Two remap threads (the default) split rows only: identical output. */
    g_unsetenv("CAMERA_PANINI_THREADS");
    assert(run("smpte", live, false) == computed);
    puts("Panini I420 integration and map cache tests passed");
}
