#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <sched.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <errno.h>

static void pin_to_cpu(int cpu){
  cpu_set_t set; CPU_ZERO(&set); CPU_SET(cpu, &set);
  if (sched_setaffinity(0, sizeof(set), &set) != 0)
    perror("sched_setaffinity");
}

int main(void){
  if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
    perror("mlockall");

  struct sched_param sp; memset(&sp, 0, sizeof(sp)); sp.sched_priority = 90;
  if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0)
    perror("sched_setscheduler");

  pin_to_cpu(2); // ajusta si quieres otro core

  // Pipeline GStreamer (sin X): filesrc .sdp -> sdpdemux -> jitter -> depay -> parse -> dec -> sink
  const char *gst = "/usr/bin/gst-launch-1.0";
  const char *sdp = "/home/pi/interior-mirror/config/stream.sdp";

  // Decodificador: v4l2h264dec (si disponible) o avdec_h264 como fallback
  // Sink: kmssink (DRM/KMS, pantalla completa sin X). Si no te vale, usa autovideosink.
  execlp(gst, gst,
    "filesrc", "location", sdp, "!",
    "sdpdemux", "!",
    "rtpjitterbuffer", "latency=50", "!",   // prueba 30..100 para latencia/estabilidad
    "rtph264depay", "!", "h264parse", "!",
    "v4l2h264dec", "!", "kmssink", "sync=false", (char*)NULL
  );

  // Si fallara v4l2h264dec/kmssink, prueba esta alternativa:
  // execlp(gst, gst, "filesrc", "location", sdp, "!", "sdpdemux", "!",
  //   "rtpjitterbuffer", "latency=50", "!", "rtph264depay", "!", "h264parse", "!",
  //   "avdec_h264", "!", "autovideosink", "sync=false", (char*)NULL);

  perror("exec gst-launch-1.0");
  return 1;
}
