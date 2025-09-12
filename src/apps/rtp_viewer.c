// rtp_viewer.c — Recibe RTP (UDP), muestra con GStreamer y permite “procesar” paquetes RTP.
// Uso:
//   ./rtp_viewer <puerto> <encoding> <pt> <clockrate> [latency_ms] [--probe]
// Ejemplos:
//   ./rtp_viewer 5004 H264 96 90000 100 --probe
//   ./rtp_viewer 5004 H265 96 90000 200
#define _POSIX_C_SOURCE 200809L
#include <gst/gst.h>
#include <gst/app/app.h>
#include <gst/rtp/rtp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
  gboolean probe_enabled;
} AppCtx;

static GstFlowReturn on_new_sample(GstAppSink *appsink, gpointer user_data) {
  // Punto para procesar frames (opcional). Ahora mismo solo consume.
  GstSample *s = gst_app_sink_pull_sample(appsink);
  if (s) gst_sample_unref(s);
  return GST_FLOW_OK;
}

static GstPadProbeReturn rtp_probe(GstPad *pad, GstPadProbeInfo *info, gpointer user_data) {
  if ((GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER) == 0) return GST_PAD_PROBE_OK;
  GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
  if (!buf) return GST_PAD_PROBE_OK;

  GstRTPBuffer rtp = GST_RTP_BUFFER_INIT;
  if (gst_rtp_buffer_map(buf, GST_MAP_READ, &rtp)) {
    guint16 seq = gst_rtp_buffer_get_seq(&rtp);
    guint32 ts  = gst_rtp_buffer_get_timestamp(&rtp);
    guint32 ssrc= gst_rtp_buffer_get_ssrc(&rtp);
    gboolean m  = gst_rtp_buffer_get_marker(&rtp);
    g_print("RTP seq=%u ts=%u ssrc=0x%08x M=%d\n", seq, ts, ssrc, m);
    gst_rtp_buffer_unmap(&rtp);
  }
  return GST_PAD_PROBE_OK;
}

int main(int argc, char **argv) {
  if (argc < 5) {
    g_printerr("Uso: %s <puerto> <encoding(H264|H265|OPUS|PCMU|...)> <pt> <clockrate> [latency_ms=100] [--probe]\n", argv[0]);
    return 1;
  }
  int port = atoi(argv[1]);
  const char *encoding = argv[2];
  int payload = atoi(argv[3]);
  int clockrate = atoi(argv[4]);
  int latency = (argc >= 6 && argv[5][0] != '-') ? atoi(argv[5]) : 100;
  gboolean want_probe = (argc >= 6 && strcmp(argv[argc-1], "--probe") == 0);

  gst_init(&argc, &argv);

  // Seleccionar depay/decoder según encoding
  const char *depay = NULL;
  const char *decoder = NULL;
  gboolean is_video = FALSE;

  if (g_ascii_strcasecmp(encoding, "H264") == 0) {
    depay = "rtph264depay"; decoder = "avdec_h264"; is_video = TRUE;
  } else if (g_ascii_strcasecmp(encoding, "H265") == 0 || g_ascii_strcasecmp(encoding, "HEVC") == 0) {
    depay = "rtph265depay"; decoder = "avdec_h265"; is_video = TRUE;
  } else if (g_ascii_strcasecmp(encoding, "OPUS") == 0) {
    depay = "rtpopusdepay"; decoder = "opusdec";
  } else if (g_ascii_strcasecmp(encoding, "PCMU") == 0 || g_ascii_strcasecmp(encoding, "PCMA") == 0) {
    depay = (g_ascii_strcasecmp(encoding, "PCMU")==0) ? "rtppcmudepay" : "rtppcmadepay";
    decoder = "avdec_pcm_alaw"; // para PCMA; para PCMU: avdec_pcm_mulaw. (autoplug con decodebin también vale)
  } else {
    g_printerr("Encoding no soportado directamente: %s\n", encoding);
    g_printerr("Prueba H264/H265/OPUS o usa decodebin en tu propia variante.\n");
    return 1;
  }

  // Construcción de caps de RTP
  gchar *caps_str = g_strdup_printf(
    "application/x-rtp, media=%s, encoding-name=%s, payload=%d, clock-rate=%d",
    is_video ? "video" : "audio", encoding, payload, clockrate);

  // Pipeline con tee: rama A → pantalla; rama B → appsink (procesamiento de frames)
  // Nombramos elementos para enlazar y para el probe.
  gchar *pipeline_desc = g_strdup_printf(
    "udpsrc name=src port=%d caps=\"%s\" ! "
    "rtpjitterbuffer name=jbuf latency=%d ! %s name=depay ! tee name=t "
    "t. ! queue ! %s ! %s ! %s sync=false "
    "t. ! queue ! appsink name=proc sync=false drop=true max-buffers=8",
    port, caps_str, latency,
    depay,
    (is_video ? "h264parse" : "queue"),   // parse para H264/H265; para audio basta queue
    decoder,
    (is_video ? "videoconvert ! autovideosink" : "autoaudiosink")
  );

  GError *err = NULL;
  GstElement *pipeline = gst_parse_launch(pipeline_desc, &err);
  if (!pipeline) {
    g_printerr("Error al crear pipeline: %s\n", err ? err->message : "desconocido");
    if (err) g_error_free(err);
    g_free(caps_str); g_free(pipeline_desc);
    return 1;
  }

  // Config appsink (callback de frames)
  GstElement *appsink = gst_bin_get_by_name(GST_BIN(pipeline), "proc");
  gst_app_sink_set_emit_signals((GstAppSink*)appsink, TRUE);
  g_signal_connect(appsink, "new-sample", G_CALLBACK(on_new_sample), NULL);

  // (Opcional) pad probe para inspeccionar paquetes RTP antes del depay
  if (want_probe) {
    GstElement *depay_el = gst_bin_get_by_name(GST_BIN(pipeline), "depay");
    GstPad *sinkpad = gst_element_get_static_pad(depay_el, "sink");
    gst_pad_add_probe(sinkpad, GST_PAD_PROBE_TYPE_BUFFER, rtp_probe, NULL, NULL);
    gst_object_unref(sinkpad);
    gst_object_unref(depay_el);
  }

  // Play
  gst_element_set_state(pipeline, GST_STATE_PLAYING);

  // Bucle de bus (CTRL+C para salir)
  GstBus *bus = gst_element_get_bus(pipeline);
  while (1) {
    GstMessage *msg = gst_bus_timed_pop_filtered(bus, GST_CLOCK_TIME_NONE,
        GST_MESSAGE_ERROR | GST_MESSAGE_EOS | GST_MESSAGE_STATE_CHANGED);
    if (!msg) continue;
    switch (GST_MESSAGE_TYPE(msg)) {
      case GST_MESSAGE_ERROR: {
        GError *e=NULL; gchar *dbg=NULL; gst_message_parse_error(msg, &e, &
