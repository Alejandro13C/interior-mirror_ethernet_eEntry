#include "gst_panini.h"
#include <gst/video/gstvideofilter.h>
#include <math.h>
#include <string.h>

/* One precomputed inverse map, sampled directly from decoded I420 into an
   800x480 I420 frame. No intermediate RGB conversion. Decoder memory is
   copied sequentially into cached RAM before the random-access remap; only
   the rows and columns the map reads are copied. Samples outside the lens
   point at black padding after the image, so the inner loop has no branch. */
typedef struct {
    guint32 *offset;
    guint8 *fx, *fy;
    guint8 *cached;
    int first_row, last_row, first_column, last_column;
} PlaneMap;
typedef struct {
    const PlaneMap *map;
    guint8 *dst;
    int dst_stride, width, height, source_width;
} PlaneTask;
typedef struct {
    GstVideoFilter parent;
    PaniniConfig config;
    PlaneMap maps[3];
    int source_width, source_height;
    /* A worker remaps the lower half of each plane (CAMERA_PANINI_THREADS=1: no worker). */
    int threads;
    GThread *worker;
    GMutex lock;
    GCond cond;
    bool pending, quit;
    PlaneTask tasks[3];
} CameraPanini;

static void free_map(PlaneMap *m) {
    g_free(m->offset); g_free(m->fx); g_free(m->fy); g_free(m->cached);
    memset(m, 0, sizeof *m);
}
typedef struct { GstVideoFilterClass parent; } CameraPaniniClass;
G_DEFINE_TYPE(CameraPanini, camera_panini, GST_TYPE_VIDEO_FILTER)

static GstCaps *transform_caps(GstBaseTransform *base, GstPadDirection direction,
                               GstCaps *caps, GstCaps *filter) {
    const CameraPanini *self = (const CameraPanini *)base;
    /* Declare the display's pixel shape: the map already accounts for it, so
       kmssink shows all 800x480 pixels instead of "fixing" the aspect itself
       (on this panel it cropped the picture to 750 columns). */
    int par_n = 1, par_d = 1;
    if (self->config.pixel_aspect > 0) gst_util_double_to_fraction(self->config.pixel_aspect, &par_n, &par_d);
    GstCaps *out = gst_caps_copy(caps);
    for (guint i = 0; i < gst_caps_get_size(out); ++i) {
        GstStructure *s = gst_caps_get_structure(out, i);
        gst_structure_remove_field(s, "pixel-aspect-ratio");
        if (direction == GST_PAD_SINK)
            gst_structure_set(s, "width", G_TYPE_INT, 800, "height", G_TYPE_INT, 480,
                              "pixel-aspect-ratio", GST_TYPE_FRACTION, par_n, par_d, NULL);
        else
            gst_structure_set(s, "width", GST_TYPE_INT_RANGE, 2, 8192,
                              "height", GST_TYPE_INT_RANGE, 2, 8192, NULL);
    }
    if (filter) {
        GstCaps *intersect = gst_caps_intersect_full(filter, out, GST_CAPS_INTERSECT_FIRST);
        gst_caps_unref(out); out = intersect;
    }
    return out;
}

/* Map entry for output pixel (x, y) of one plane; false outside the lens,
   where the entry points at the black padding with fx = fy = 0. */
static bool map_entry(const PaniniConfig *c, const GstVideoInfo *in, int plane, int x, int y,
                      guint32 *offset, guint8 *fx, guint8 *fy) {
    int sw = GST_VIDEO_INFO_COMP_WIDTH(in, plane), sh = GST_VIDEO_INFO_COMP_HEIGHT(in, plane);
    double sx = 0, sy = 0;
    /* Use pixel centers at each plane's scale. */
    bool valid = panini_source(c, GST_VIDEO_INFO_WIDTH(in), GST_VIDEO_INFO_HEIGHT(in), 800, 480,
        plane ? 2*x + .5 : x, plane ? 2*y + .5 : y, &sx, &sy);
    if (plane) { sx = (sx - .5) / 2; sy = (sy - .5) / 2; }
    if (!valid || sx < 0 || sy < 0 || sx >= sw - 1 || sy >= sh - 1) {
        *offset = (guint32)((size_t)sw * sh); *fx = *fy = 0;
        return false;
    }
    unsigned ix = (unsigned)sx, iy = (unsigned)sy;
    *offset = iy * (unsigned)sw + ix;
    *fx = (guint8)((sx - ix) * 256);
    *fy = (guint8)((sy - iy) * 256);
    return true;
}

static void compute_plane(const PaniniConfig *c, const GstVideoInfo *in, int plane, PlaneMap *m) {
    int w = plane ? 400 : 800, h = plane ? 240 : 480, sw = GST_VIDEO_INFO_COMP_WIDTH(in, plane);
    m->first_row = GST_VIDEO_INFO_COMP_HEIGHT(in, plane); m->first_column = sw;
    m->last_row = m->last_column = -1;
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
        size_t i = (size_t)y * w + x;
        if (!map_entry(c, in, plane, x, y, &m->offset[i], &m->fx[i], &m->fy[i])) continue;
        int ix = (int)(m->offset[i] % (unsigned)sw), iy = (int)(m->offset[i] / (unsigned)sw);
        m->first_row = MIN(m->first_row, iy); m->last_row = MAX(m->last_row, iy + 1);
        m->first_column = MIN(m->first_column, ix); m->last_column = MAX(m->last_column, ix + 1);
    }
}

/* Building the map takes over a second on the Pi, on every (re)start. With
   systemd's CacheDirectory= it is stored once per configuration and reused.
   Bump MAP_VERSION whenever panini.c or map_entry() change: a localized
   change (e.g. border rules) can escape the 64 recomputed sample entries. */
#define MAP_VERSION "3" /* 3: display pixel aspect */
#define MAP_MAGIC "PANMAP" MAP_VERSION
static guint64 fnv1a(const gchar *data, gsize size) {
    guint64 hash = 14695981039346656037ull;
    for (gsize i = 0; i < size; ++i) { hash ^= (guchar)data[i]; hash *= 1099511628211ull; }
    return hash;
}
static gchar *cache_path(const PaniniConfig *c, const GstVideoInfo *in) {
    const char *dir = g_getenv("CACHE_DIRECTORY");
    if (!dir || !*dir || strchr(dir, ':')) return NULL;
    return g_strdup_printf("%s/panini-v" MAP_VERSION "-%dx%d-%.6g-%.6g-%.6g-%.6g-%.6g.map", dir, GST_VIDEO_INFO_WIDTH(in),
        GST_VIDEO_INFO_HEIGHT(in), c->compression, c->lens_fov, c->view_fov, c->pitch,
        c->pixel_aspect > 0 ? c->pixel_aspect : 1);
}
static gsize plane_bytes(int plane) {
    gsize n = plane ? 400 * 240 : 800 * 480;
    return 4 * sizeof(gint32) + n * (sizeof(guint32) + 2);
}
static bool load_maps(CameraPanini *self, const GstVideoInfo *in, const char *path) {
    gchar *data = NULL;
    gsize size = 0, expected = sizeof MAP_MAGIC + sizeof(guint64);
    for (int plane = 0; plane < 3; ++plane) expected += plane_bytes(plane);
    if (!path || !g_file_get_contents(path, &data, &size, NULL)) return false;
    guint64 stored = 0;
    if (size == expected) memcpy(&stored, data + size - sizeof stored, sizeof stored);
    /* Checksum: corruption anywhere; sampled entries: a different algorithm. */
    bool ok = size == expected && !memcmp(data, MAP_MAGIC, sizeof MAP_MAGIC) &&
        stored == fnv1a(data, size - sizeof stored);
    const gchar *p = data + sizeof MAP_MAGIC;
    for (int plane = 0; ok && plane < 3; ++plane) {
        PlaneMap *m = &self->maps[plane];
        gsize n = plane ? 400 * 240 : 800 * 480;
        gint32 bounds[4];
        memcpy(bounds, p, sizeof bounds); p += sizeof bounds;
        m->first_row = bounds[0]; m->last_row = bounds[1];
        m->first_column = bounds[2]; m->last_column = bounds[3];
        memcpy(m->offset, p, n * sizeof(guint32)); p += n * sizeof(guint32);
        memcpy(m->fx, p, n); p += n;
        memcpy(m->fy, p, n); p += n;
        int sw = GST_VIDEO_INFO_COMP_WIDTH(in, plane), sh = GST_VIDEO_INFO_COMP_HEIGHT(in, plane);
        ok = m->last_row < sh && m->last_column < sw && m->first_row >= 0 && m->first_column >= 0;
        for (gsize i = 0; ok && i < n; ++i)
            ok = m->offset[i] <= (guint32)((size_t)sw * sh); /* never read past the padding */
        for (int k = 0; ok && k < 64; ++k) {
            gsize i = (gsize)k * 104729u % n;
            int w = plane ? 400 : 800;
            guint32 offset; guint8 fx, fy;
            map_entry(&self->config, in, plane, (int)(i % w), (int)(i / w), &offset, &fx, &fy);
            ok = offset == m->offset[i] && fx == m->fx[i] && fy == m->fy[i];
        }
    }
    g_free(data);
    return ok;
}
static void save_maps(const CameraPanini *self, const char *path) {
    gsize size = sizeof MAP_MAGIC + sizeof(guint64);
    for (int plane = 0; plane < 3; ++plane) size += plane_bytes(plane);
    gchar *data = g_try_malloc(size), *p = data;
    if (!path || !data) { g_free(data); return; }
    memcpy(p, MAP_MAGIC, sizeof MAP_MAGIC); p += sizeof MAP_MAGIC;
    for (int plane = 0; plane < 3; ++plane) {
        const PlaneMap *m = &self->maps[plane];
        gsize n = plane ? 400 * 240 : 800 * 480;
        gint32 bounds[4] = {m->first_row, m->last_row, m->first_column, m->last_column};
        memcpy(p, bounds, sizeof bounds); p += sizeof bounds;
        memcpy(p, m->offset, n * sizeof(guint32)); p += n * sizeof(guint32);
        memcpy(p, m->fx, n); p += n;
        memcpy(p, m->fy, n); p += n;
    }
    guint64 hash = fnv1a(data, (gsize)(p - data));
    memcpy(p, &hash, sizeof hash);
    GError *error = NULL;
    if (!g_file_set_contents(path, data, (gssize)size, &error)) {
        g_printerr("Panini map cache not written: %s\n", error->message);
        g_clear_error(&error);
    }
    g_free(data);
}

static gboolean set_info(GstVideoFilter *filter, GstCaps *incaps, GstVideoInfo *in,
                         GstCaps *outcaps, GstVideoInfo *out) {
    (void)incaps; (void)outcaps;
    CameraPanini *self = (CameraPanini *)filter;
    if (!panini_valid(&self->config) || GST_VIDEO_INFO_WIDTH(out) != 800 ||
        GST_VIDEO_INFO_HEIGHT(out) != 480) return FALSE;
    if (self->maps[0].offset && self->maps[1].offset && self->maps[2].offset &&
        self->source_width == GST_VIDEO_INFO_WIDTH(in) &&
        self->source_height == GST_VIDEO_INFO_HEIGHT(in)) return TRUE;
    gint64 started = g_get_monotonic_time();
    guint8 luma_black = in->colorimetry.range == GST_VIDEO_COLOR_RANGE_0_255 ? 0 : 16;
    for (int plane = 0; plane < 3; ++plane) {
        int w = plane ? 400 : 800, h = plane ? 240 : 480;
        int sw = GST_VIDEO_INFO_COMP_WIDTH(in, plane), sh = GST_VIDEO_INFO_COMP_HEIGHT(in, plane);
        PlaneMap *m = &self->maps[plane];
        free_map(m);
        /* Two padding rows: the black sample and its bilinear neighbours. */
        size_t image = (size_t)sw * sh, padding = 2 * (size_t)sw + 2;
        m->cached = g_try_malloc(image + padding);
        m->offset = g_try_new(guint32, (size_t)w * h);
        m->fx = g_try_new0(guint8, (size_t)w * h);
        m->fy = g_try_new0(guint8, (size_t)w * h);
        if (!m->cached || !m->offset || !m->fx || !m->fy) { free_map(m); return FALSE; }
        memset(m->cached + image, plane ? 128 : luma_black, padding);
    }
    gchar *path = cache_path(&self->config, in);
    bool cached = load_maps(self, in, path);
    if (!cached) {
        for (int plane = 0; plane < 3; ++plane) compute_plane(&self->config, in, plane, &self->maps[plane]);
        save_maps(self, path);
    }
    g_free(path);
    self->source_width = GST_VIDEO_INFO_WIDTH(in);
    self->source_height = GST_VIDEO_INFO_HEIGHT(in);
    g_printerr("Panini map: %dx%d -> 800x480, %.3f s%s\n", self->source_width,
        self->source_height, (g_get_monotonic_time()-started)/1e6, cached ? " (cached)" : "");
    return TRUE;
}

/* Rows [y0, y1) of one output plane. */
static void remap_rows(const PlaneTask *t, int y0, int y1) {
    const PlaneMap *m = t->map;
    const guint8 *src = m->cached;
    int w = t->width, si = t->source_width;
    for (int y = y0; y < y1; ++y) {
        guint8 *row = t->dst + y * t->dst_stride;
        size_t i = (size_t)y * w;
        for (int x = 0; x < w; ++x, ++i) {
            const guint8 *p = src + m->offset[i];
            /* Same integer bilinear interpolation, three multiplies
               instead of six; all intermediates fit signed 32 bits. */
            int top = (p[0]<<8) + ((int)p[1]-p[0])*m->fx[i];
            int bottom = (p[si]<<8) + ((int)p[si+1]-p[si])*m->fx[i];
            row[x] = (guint8)(((top<<8)+(bottom-top)*m->fy[i]+32768)>>16);
        }
    }
}

/* Optional second thread for the lower half of every plane. The remap is
   memory-bound: this lowers latency, not CPU time, on the video cores. */
static gpointer worker_main(gpointer data) {
    CameraPanini *self = data;
    g_mutex_lock(&self->lock);
    for (;;) {
        while (!self->pending && !self->quit) g_cond_wait(&self->cond, &self->lock);
        if (self->quit) break;
        g_mutex_unlock(&self->lock);
        for (int plane = 0; plane < 3; ++plane)
            remap_rows(&self->tasks[plane], self->tasks[plane].height / 2, self->tasks[plane].height);
        g_mutex_lock(&self->lock);
        self->pending = false;
        g_cond_broadcast(&self->cond);
    }
    g_mutex_unlock(&self->lock);
    return NULL;
}

static GstFlowReturn transform_frame(GstVideoFilter *filter, GstVideoFrame *in, GstVideoFrame *out) {
    CameraPanini *self = (CameraPanini *)filter;
    for (int plane = 0; plane < 3; ++plane) {
        int source_stride = GST_VIDEO_FRAME_PLANE_STRIDE(in, plane);
        int si = GST_VIDEO_FRAME_COMP_WIDTH(in, plane);
        const guint8 *input = GST_VIDEO_FRAME_PLANE_DATA(in, plane);
        const PlaneMap *m = &self->maps[plane];
        if (m->last_row >= 0) {
            size_t columns = (size_t)(m->last_column - m->first_column + 1);
            for (int r = m->first_row; r <= m->last_row; ++r)
                memcpy(m->cached + r*si + m->first_column,
                       input + r*source_stride + m->first_column, columns);
        }
        self->tasks[plane] = (PlaneTask){m, GST_VIDEO_FRAME_PLANE_DATA(out, plane),
            GST_VIDEO_FRAME_PLANE_STRIDE(out, plane), plane ? 400 : 800, plane ? 240 : 480, si};
    }
    if (self->threads == 2 && !self->worker)
        self->worker = g_thread_try_new("panini", worker_main, self, NULL);
    bool split = self->worker != NULL;
    if (split) {
        g_mutex_lock(&self->lock);
        self->pending = true;
        g_cond_signal(&self->cond);
        g_mutex_unlock(&self->lock);
    }
    for (int plane = 0; plane < 3; ++plane)
        remap_rows(&self->tasks[plane], 0, split ? self->tasks[plane].height / 2 : self->tasks[plane].height);
    if (split) {
        g_mutex_lock(&self->lock);
        while (self->pending) g_cond_wait(&self->cond, &self->lock);
        g_mutex_unlock(&self->lock);
    }
    return GST_FLOW_OK;
}

static void finalize(GObject *object) {
    CameraPanini *self = (CameraPanini *)object;
    if (self->worker) {
        g_mutex_lock(&self->lock);
        self->quit = true;
        g_cond_signal(&self->cond);
        g_mutex_unlock(&self->lock);
        g_thread_join(self->worker);
    }
    g_mutex_clear(&self->lock);
    g_cond_clear(&self->cond);
    for (int p = 0; p < 3; ++p) free_map(&self->maps[p]);
    G_OBJECT_CLASS(camera_panini_parent_class)->finalize(object);
}

static void camera_panini_class_init(CameraPaniniClass *klass) {
    GstElementClass *element = GST_ELEMENT_CLASS(klass);
    GstCaps *sink = gst_caps_from_string("video/x-raw,format=I420,width=[2,8192],height=[2,8192]");
    GstCaps *src = gst_caps_from_string("video/x-raw,format=I420,width=800,height=480");
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
    self->config = (PaniniConfig){1, 180, 130, 0, 0};
    /* Two threads by default: measured -7 ms latency, same CPU (CAMERA_PANINI_THREADS=1 disables). */
    const char *threads = g_getenv("CAMERA_PANINI_THREADS");
    self->threads = threads && !strcmp(threads, "1") ? 1 : 2;
    g_mutex_init(&self->lock);
    g_cond_init(&self->cond);
}
bool camera_panini_register(void) {
    return gst_element_register(NULL, "camerapanini", GST_RANK_NONE, camera_panini_get_type());
}
/* Call before caps negotiation; a new configuration rebuilds the map there. */
void camera_panini_configure(GstElement *element, const PaniniConfig *config) {
    CameraPanini *self = (CameraPanini *)element;
    self->config = *config;
    self->source_width = self->source_height = 0;
}
