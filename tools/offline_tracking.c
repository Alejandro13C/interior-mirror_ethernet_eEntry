/* Reuse the live C tracker and prediction in the PC video diagnostic. */
#include "vehicle_tracking.h"
#include "vehicle_marker.h"
#include <stdlib.h>
void *offline_tracks_new(void) { return calloc(1,sizeof(CameraTracks)); }
void offline_tracks_free(void *tracks) { free(tracks); }
void offline_tracks_update(void *tracks,CameraVehicle *vehicles,size_t count,uint64_t ns) {
    camera_track(tracks,vehicles,count,ns);
}
void offline_anchor(const CameraVehicle *vehicle,uint64_t age,float *x,float *y) {
    camera_vehicle_anchor(vehicle,age,x,y);
}
void offline_marker(const CameraVehicle *vehicle,float far_width,float near_width,CameraMarker *out) {
    *out=camera_vehicle_marker(vehicle,far_width,near_width);
}
