#include "gst_panini.h"
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>
#include <assert.h>
#include <stdio.h>

int main(int argc, char **argv) {
    gst_init(&argc, &argv);
    assert(camera_panini_register());
    GError *error = NULL;
    GstElement *pipeline = gst_parse_launch(
        "videotestsrc num-buffers=2 pattern=white ! "
        "video/x-raw,format=I420,width=1344,height=968 ! "
        "camerapanini name=warp ! appsink name=output sync=false", &error);
    assert(pipeline && !error);
    GstElement *warp = gst_bin_get_by_name(GST_BIN(pipeline), "warp");
    PaniniConfig c = {1,90,160,45}; /* Intentionally exposes out-of-source corners. */
    camera_panini_configure(warp, &c);
    GstElement *sink = gst_bin_get_by_name(GST_BIN(pipeline), "output");
    assert(gst_element_set_state(pipeline, GST_STATE_PLAYING) != GST_STATE_CHANGE_FAILURE);
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
                if (plane) assert(value == 128);
                else assert(value == 16 || value == 235);
            }
        }
        /* Upper-right corner is outside the assumed narrow lens; render black. */
        const guint8 *y = GST_VIDEO_FRAME_PLANE_DATA(&mapped, 0);
        assert(y[799] == 16);
        gst_video_frame_unmap(&mapped);
        gst_sample_unref(sample);
    }
    gst_element_set_state(pipeline, GST_STATE_NULL);
    gst_object_unref(sink); gst_object_unref(warp); gst_object_unref(pipeline);
    puts("Panini I420 integration tests passed");
}
