#ifndef CAMERA_VEHICLE_MARKER_H
#define CAMERA_VEHICLE_MARKER_H
#include "vehicle_protocol.h"

typedef struct { int height; unsigned char r,g,b; } CameraMarker;

/* Experimental apparent-width scale, NOT metres or collision risk:
   green far, yellow, red near. Endpoints must be established for the
   installed camera/projection first. White when the width is not
   comparable: motorcycles, or boxes cut by the left/right edge. A top or
   bottom cut (frequent with CAMERA_AI_BAND) leaves the width intact. */
static inline CameraMarker camera_vehicle_marker(const CameraVehicle *v,float far_width,float near_width) {
    CameraMarker neutral={20,255,255,255};
    bool car_like=v->category==2 || v->category==5 || v->category==7; /* car, bus, truck */
    if(!isfinite(far_width) || !isfinite(near_width) || far_width<=0 ||
       near_width<=far_width || near_width>=1 || !camera_vehicle_valid(v) ||
       !car_like || v->x<=0 || v->x+v->w>=1)
        return neutral;
    float t=fmaxf(0,fminf(1,(v->w-far_width)/(near_width-far_width)));
    CameraMarker marker={16+(int)lroundf(20*t),
        (unsigned char)lroundf(255*fminf(1,2*t)),
        (unsigned char)lroundf(255*fminf(1,2*(1-t))),0};
    return marker;
}
#endif
