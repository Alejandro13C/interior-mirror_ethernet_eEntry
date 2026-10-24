#ifndef CAMERA_VEHICLE_MARKER_H
#define CAMERA_VEHICLE_MARKER_H
#include "vehicle_protocol.h"

typedef struct { int height; unsigned char r,g,b; } CameraMarker;

/* Experimental apparent-width scale, NOT metres or collision risk.
   Endpoints must be established for the installed camera/projection first.
   Other classes and edge-clipped boxes have no comparable car width. */
static inline CameraMarker camera_vehicle_marker(const CameraVehicle *v,float far_width,float near_width) {
    CameraMarker neutral={20,255,255,255};
    if(!isfinite(far_width) || !isfinite(near_width) || far_width<=0 ||
       near_width<=far_width || near_width>=1 || !camera_vehicle_valid(v) ||
       v->category!=2 || v->x<=0 || v->y<=0 || v->x+v->w>=1 || v->y+v->h>=1)
        return neutral;
    float t=fmaxf(0,fminf(1,(v->w-far_width)/(near_width-far_width)));
    CameraMarker marker={16+(int)lroundf(20*t),
        (unsigned char)lroundf(255*fminf(1,2*t)),
        (unsigned char)lroundf(255*fminf(1,2*(1-t))),0};
    return marker;
}
#endif
