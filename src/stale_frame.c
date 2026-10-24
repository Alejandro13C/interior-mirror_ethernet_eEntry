#include "stale_frame.h"
#include <gst/video/video.h>
#include <stdbool.h>
#include <string.h>

/* 5x7 glyphs for the only message drawn; rows top to bottom, bit 4 = left. */
static const struct { char c; guint8 rows[7]; } font[] = {
    {'S', {0x0f, 0x10, 0x10, 0x0e, 0x01, 0x01, 0x1e}},
    {'I', {0x0e, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0e}},
    {'N', {0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11}},
    {'V', {0x11, 0x11, 0x11, 0x11, 0x11, 0x0a, 0x04}},
    {'D', {0x1e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1e}},
    {'E', {0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x1f}},
    {'O', {0x0e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e}},
};

static const guint8 *glyph(char c) {
    for (size_t i = 0; i < G_N_ELEMENTS(font); ++i)
        if (font[i].c == c) return font[i].rows;
    return NULL;
}

GstBuffer *camera_stale_frame(GstCaps *caps, bool mirror, bool rotate180) {
    GstVideoInfo info;
    if (!caps || !gst_video_info_from_caps(&info, caps) || !GST_VIDEO_INFO_IS_YUV(&info))
        return NULL;
    GstBuffer *buffer = gst_buffer_new_allocate(NULL, GST_VIDEO_INFO_SIZE(&info), NULL);
    GstVideoFrame frame;
    if (!buffer || !gst_video_frame_map(&frame, &info, buffer, GST_MAP_WRITE)) {
        if (buffer) gst_buffer_unref(buffer);
        return NULL;
    }
    bool full = info.colorimetry.range == GST_VIDEO_COLOR_RANGE_0_255;
    guint8 black = full ? 0 : 16, white = full ? 255 : 235;
    /* Planar and semi-planar YUV: luma in plane 0, neutral chroma elsewhere. */
    for (guint plane = 0; plane < GST_VIDEO_FRAME_N_PLANES(&frame); ++plane) {
        guint8 *data = GST_VIDEO_FRAME_PLANE_DATA(&frame, plane);
        int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, plane);
        int rows = GST_VIDEO_FRAME_COMP_HEIGHT(&frame, plane ? 1 : 0);
        for (int y = 0; y < rows; ++y) memset(data + y * stride, plane ? 128 : black, (size_t)stride);
    }
    const char *text = "SIN VIDEO";
    int width = GST_VIDEO_FRAME_WIDTH(&frame), height = GST_VIDEO_FRAME_HEIGHT(&frame);
    int columns = (int)strlen(text) * 6 - 1;
    int scale = MAX(1, MIN(width * 2 / 3 / columns, height / 8 / 7));
    int left = (width - columns * scale) / 2, top = (height - 7 * scale) / 2;
    guint8 *luma = GST_VIDEO_FRAME_PLANE_DATA(&frame, 0);
    int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, 0);
    /* Reflect-x and rotate-180 are involutions: apply the same flips here. */
    bool flip_x = mirror != rotate180, flip_y = rotate180;
    for (int i = 0; text[i]; ++i) {
        const guint8 *rows = glyph(text[i]);
        if (!rows) continue;
        for (int gy = 0; gy < 7 * scale; ++gy)
            for (int gx = 0; gx < 5 * scale; ++gx)
                if (rows[gy / scale] & (0x10 >> (gx / scale))) {
                    int x = left + i * 6 * scale + gx, y = top + gy;
                    if (flip_x) x = width - 1 - x;
                    if (flip_y) y = height - 1 - y;
                    luma[y * stride + x] = white;
                }
    }
    gst_video_frame_unmap(&frame);
    return buffer;
}
