#include "gst_panini.h"
#include <gst/video/gstvideofilter.h>
#include <math.h>
#include <string.h>

/* One precomputed inverse map, sampled directly from decoded I420 into an
   800x480 I420 frame. No intermediate RGB conversion. Decoder memory is
   copied sequentially into cached RAM before the random-access remap. */
typedef struct { guint32 offset; guint8 fx, fy, valid; } Sample;
typedef struct {
    GstVideoFilter parent;
    PaniniConfig config;
    Sample *maps[3];
    guint8 *cached[3];
    int source_width, source_height;
} CameraPanini;
typedef struct { GstVideoFilterClass parent; } CameraPaniniClass;
G_DEFINE_TYPE(CameraPanini, camera_panini, GST_TYPE_VIDEO_FILTER)

static GstCaps *transform_caps(GstBaseTransform *base, GstPadDirection direction,
                               GstCaps *caps, GstCaps *filter) {
    (void)base;
    GstCaps *out = gst_caps_copy(caps);
    for (guint i = 0; i < gst_caps_get_size(out); ++i) {
        GstStructure *s = gst_caps_get_structure(out, i);
        if (direction == GST_PAD_SINK)
            gst_structure_set(s, "width", G_TYPE_INT, 800, "height", G_TYPE_INT, 480, NULL);
        else
            gst_structure_set(s, "width", GST_TYPE_INT_RANGE, 2, 8192,
                              "height", GST_TYPE_INT_RANGE, 2, 8192, NULL);
        gst_structure_remove_field(s, "pixel-aspect-ratio");
    }
    if (filter) {
        GstCaps *intersect = gst_caps_intersect_full(filter, out, GST_CAPS_INTERSECT_FIRST);
        gst_caps_unref(out); out = intersect;
    }
    return out;
}

static gboolean set_info(GstVideoFilter *filter, GstCaps *incaps, GstVideoInfo *in,
                         GstCaps *outcaps, GstVideoInfo *out) {
    (void)incaps; (void)outcaps;
    CameraPanini *self = (CameraPanini *)filter;
    if (!panini_valid(&self->config) || GST_VIDEO_INFO_WIDTH(out) != 800 ||
        GST_VIDEO_INFO_HEIGHT(out) != 480) return FALSE;
    if (self->maps[0] && self->maps[1] && self->maps[2] &&
        self->source_width == GST_VIDEO_INFO_WIDTH(in) &&
        self->source_height == GST_VIDEO_INFO_HEIGHT(in)) return TRUE;
    gint64 started = g_get_monotonic_time();
    for (int plane = 0; plane < 3; ++plane) {
        int w = plane ? 400 : 800, h = plane ? 240 : 480;
        int sw = GST_VIDEO_INFO_COMP_WIDTH(in, plane), sh = GST_VIDEO_INFO_COMP_HEIGHT(in, plane);
        g_free(self->maps[plane]);
        g_free(self->cached[plane]);
        self->cached[plane] = g_try_malloc((size_t)sw * sh);
        self->maps[plane] = g_try_new0(Sample, (size_t)w * h);
        if (!self->maps[plane] || !self->cached[plane]) return FALSE;
        for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
            double sx = 0, sy = 0;
            /* Use pixel centers at each plane's scale. */
            bool valid = panini_source(&self->config, GST_VIDEO_INFO_WIDTH(in),
                GST_VIDEO_INFO_HEIGHT(in), 800, 480,
                plane ? 2*x + .5 : x, plane ? 2*y + .5 : y, &sx, &sy);
            Sample *m = &self->maps[plane][y*w+x];
            if (plane) { sx = (sx - .5) / 2; sy = (sy - .5) / 2; }
            if (!valid || sx < 0 || sy < 0 || sx >= sw - 1 || sy >= sh - 1) continue;
            unsigned ix=(unsigned)sx,iy=(unsigned)sy;
            m->offset=iy*(unsigned)sw+ix;
            m->fx = (guint8)((sx - ix) * 256);
            m->fy = (guint8)((sy - iy) * 256); m->valid = 1;
        }
    }
    self->source_width = GST_VIDEO_INFO_WIDTH(in);
    self->source_height = GST_VIDEO_INFO_HEIGHT(in);
    g_printerr("Panini map: %dx%d -> 800x480, %.3f s\n", self->source_width,
        self->source_height, (g_get_monotonic_time()-started)/1e6);
    return TRUE;
}

static GstFlowReturn transform_frame(GstVideoFilter *filter, GstVideoFrame *in, GstVideoFrame *out) {
    CameraPanini *self = (CameraPanini *)filter;
    for (int plane = 0; plane < 3; ++plane) {
        int w = plane ? 400 : 800, h = plane ? 240 : 480;
        int source_stride = GST_VIDEO_FRAME_PLANE_STRIDE(in, plane);
        int si = GST_VIDEO_FRAME_COMP_WIDTH(in, plane);
        int input_height = GST_VIDEO_FRAME_COMP_HEIGHT(in, plane);
        int so = GST_VIDEO_FRAME_PLANE_STRIDE(out, plane);
        const guint8 *input = GST_VIDEO_FRAME_PLANE_DATA(in, plane);
        guint8 *src = self->cached[plane];
        for (int r = 0; r < input_height; ++r)
            memcpy(src + r*si, input + r*source_stride, (size_t)si);
        guint8 *dst = GST_VIDEO_FRAME_PLANE_DATA(out, plane);
        const Sample *map = self->maps[plane];
        guint8 black = plane ? 128 : (in->info.colorimetry.range == GST_VIDEO_COLOR_RANGE_0_255 ? 0 : 16);
        for (int y = 0; y < h; ++y) {
            guint8 *row = dst + y*so;
            for (int x = 0; x < w; ++x) {
                const Sample *m = &map[y*w+x];
                if (!m->valid) { row[x] = black; continue; }
                const guint8 *p = src + m->offset;
                /* Same integer bilinear interpolation, three multiplies
                   instead of six; all intermediates fit signed 32 bits. */
                int top = (p[0]<<8) + ((int)p[1]-p[0])*m->fx;
                int bottom = (p[si]<<8) + ((int)p[si+1]-p[si])*m->fx;
                row[x] = (guint8)(((top<<8)+(bottom-top)*m->fy+32768)>>16);
            }
        }
    }
    return GST_FLOW_OK;
}

static void finalize(GObject *object) {
    CameraPanini *self = (CameraPanini *)object;
    for (int p = 0; p < 3; ++p) { g_free(self->maps[p]); g_free(self->cached[p]); }
    G_OBJECT_CLASS(camera_panini_parent_class)->finalize(object);
}

static void camera_panini_class_init(CameraPaniniClass *klass) {
    GstElementClass *element = GST_ELEMENT_CLASS(klass);
    GstCaps *sink = gst_caps_from_string("video/x-raw,format=I420,width=[2,8192],height=[2,8192]");
    GstCaps *src = gst_caps_from_string("video/x-raw,format=I420,width=800,height=480,pixel-aspect-ratio=1/1");
    gst_element_class_add_pad_template(element, gst_pad_template_new("sink", GST_PAD_SINK, GST_PAD_ALWAYS, sink));
    gst_element_class_add_pad_template(element, gst_pad_template_new("src", GST_PAD_SRC, GST_PAD_ALWAYS, src));
    gst_caps_unref(sink); gst_caps_unref(src);
    gst_element_class_set_static_metadata(element, "Camera Panini preview", "Filter/Effect/Video",
        "Approximate equidistant lens to Panini, fixed 800x480 output", "Rear Camera");
    GST_BASE_TRANSFORM_CLASS(klass)->transform_caps = transform_caps;
    GST_VIDEO_FILTER_CLASS(klass)->set_info = set_info;
    GST_VIDEO_FILTER_CLASS(klass)->transform_frame = transform_frame;
    G_OBJECT_CLASS(klass)->finalize = finalize;
}
static void camera_panini_init(CameraPanini *self) {
    self->config = (PaniniConfig){1, 180, 120, 0};
}
bool camera_panini_register(void) {
    return gst_element_register(NULL, "camerapanini", GST_RANK_NONE, camera_panini_get_type());
}
void camera_panini_configure(GstElement *element, const PaniniConfig *config) {
    ((CameraPanini *)element)->config = *config;
}
