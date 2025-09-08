#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <sched.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <errno.h>
#include <time.h>

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

  pin_to_cpu(2); // ajusta core si quieres

  fprintf(stdout, "[interior-mirror] proceso RT iniciado.\n");
  fflush(stdout);

  for(;;){
    struct timespec ts = {0, 20*1000*1000}; // 20 ms
    nanosleep(&ts, NULL);
    // Aquí añadiremos el receptor (mpv/gstreamer)
  }
  return 0;
}
