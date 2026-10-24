/* Reuse the live C tracker and prediction in the PC video diagnostic. */
#include "vehicle_tracking.h"
#include <stdlib.h>
void *offline_tracks_new(void) { return calloc(1,sizeof(CameraTracks)); }
void offline_tracks_free(void *tracks) { free(tracks); }
void offline_tracks_update(void *tracks,CameraVehicle *vehicles,size_t count,uint64_t ns) {
    camera_track(tracks,vehicles,count,ns);
}
void offline_anchor(const CameraVehicle *vehicle,uint64_t age,float *x,float *y) {
    camera_vehicle_anchor(vehicle,age,x,y);
}
