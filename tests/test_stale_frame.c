#include "stale_frame.h"
#include <gst/video/video.h>
#include <assert.h>
#include <stdio.h>

static void check(const char *description, guint8 black, guint8 white) {
    GstCaps *caps = gst_caps_from_string(description);
    GstVideoInfo info;
    assert(gst_video_info_from_caps(&info, caps));
    GstBuffer *buffer = camera_stale_frame(caps, false, false);
    assert(buffer && gst_buffer_get_size(buffer) == GST_VIDEO_INFO_SIZE(&info));
    GstVideoFrame frame;
    assert(gst_video_frame_map(&frame, &info, buffer, GST_MAP_READ));
    const guint8 *y = GST_VIDEO_FRAME_PLANE_DATA(&frame, 0);
    int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, 0);
    int width = GST_VIDEO_FRAME_WIDTH(&frame), height = GST_VIDEO_FRAME_HEIGHT(&frame);
    size_t lit = 0, other = 0;
    for (int row = 0; row < height; ++row)
        for (int x = 0; x < width; ++x) {
            guint8 v = y[row * stride + x];
            if (v == white) {
                lit++;
                // Text stays in the middle band, away from any old triangle position.
                assert(row > height / 3 && row < height * 2 / 3);
            } else if (v != black) other++;
        }
    assert(!other && lit > (size_t)width * height / 200);
    for (guint plane = 1; plane < GST_VIDEO_FRAME_N_PLANES(&frame); ++plane) {
        const guint8 *c = GST_VIDEO_FRAME_PLANE_DATA(&frame, plane);
        int cs = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, plane);
        for (int row = 0; row < GST_VIDEO_FRAME_COMP_HEIGHT(&frame, 1); ++row)
            for (int x = 0; x < GST_VIDEO_FRAME_COMP_WIDTH(&frame, 1); ++x)
                assert(c[row * cs + x] == 128);
    }
    gst_video_frame_unmap(&frame);
    gst_buffer_unref(buffer);
    gst_caps_unref(caps);
}

/* Luma of the mirrored / rotated variants must be the plain text flipped,
   so that the display plane's transform shows it upright. */
static void check_transforms(void) {
    GstCaps *caps = gst_caps_from_string("video/x-raw,format=I420,width=800,height=480");
    GstVideoInfo info;
    assert(gst_video_info_from_caps(&info, caps));
    GstBuffer *plain = camera_stale_frame(caps, false, false);
    GstBuffer *variants[3] = {camera_stale_frame(caps, true, false), camera_stale_frame(caps, false, true),
                              camera_stale_frame(caps, true, true)};
    const bool fx[3] = {true, true, false}, fy[3] = {false, true, true};
    GstVideoFrame a, b;
    assert(plain && gst_video_frame_map(&a, &info, plain, GST_MAP_READ));
    const guint8 *pa = GST_VIDEO_FRAME_PLANE_DATA(&a, 0);
    int sa = GST_VIDEO_FRAME_PLANE_STRIDE(&a, 0);
    for (int k = 0; k < 3; ++k) {
        assert(variants[k] && gst_video_frame_map(&b, &info, variants[k], GST_MAP_READ));
        const guint8 *pb = GST_VIDEO_FRAME_PLANE_DATA(&b, 0);
        int sb = GST_VIDEO_FRAME_PLANE_STRIDE(&b, 0);
        for (int y = 0; y < 480; ++y) for (int x = 0; x < 800; ++x)
            assert(pb[y * sb + x] == pa[(fy[k] ? 479 - y : y) * sa + (fx[k] ? 799 - x : x)]);
        gst_video_frame_unmap(&b);
        gst_buffer_unref(variants[k]);
    }
    gst_video_frame_unmap(&a);
    gst_buffer_unref(plain);
    gst_caps_unref(caps);
}

int main(int argc, char **argv) {
    gst_init(&argc, &argv);
    check_transforms();
    check("video/x-raw,format=I420,width=800,height=480", 16, 235);
    check("video/x-raw,format=NV12,width=1344,height=968", 16, 235);
    check("video/x-raw,format=I420,width=800,height=480,colorimetry=1:4:0:0", 0, 255);
    GstCaps *rgb = gst_caps_from_string("video/x-raw,format=RGB,width=800,height=480");
    assert(!camera_stale_frame(rgb, false, false) && !camera_stale_frame(NULL, false, false));
    gst_caps_unref(rgb);
    puts("Stale frame: black SIN VIDEO in I420/NV12, limited and full range passed");
}
