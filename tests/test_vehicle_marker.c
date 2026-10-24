#include "vehicle_marker.h"
#include <assert.h>
#include <stdio.h>

static void neutral(CameraMarker m) {
    assert(m.height==20 && m.r==255 && m.g==255 && m.b==255);
}
int main(void) {
    /* Synthetic endpoints exercise rendering policy, not a distance calibration. */
    CameraVehicle v={.x=.1f,.y=.2f,.w=.1f,.h=.2f,.confidence=.8f,.category=2};
    neutral(camera_vehicle_marker(&v,0,0));
    neutral(camera_vehicle_marker(&v,.4f,.1f));
    neutral(camera_vehicle_marker(&v,NAN,.4f));
    neutral(camera_vehicle_marker(&v,.1f,INFINITY));
    CameraMarker far=camera_vehicle_marker(&v,.1f,.4f);
    assert(far.height==16 && far.r==0 && far.g==255 && far.b==0);
    v.w=.25f;
    CameraMarker middle=camera_vehicle_marker(&v,.1f,.4f);
    assert(middle.height==26 && middle.r==255 && middle.g==255 && middle.b==0);
    v.w=.5f;
    CameraMarker near=camera_vehicle_marker(&v,.1f,.4f);
    assert(near.height==36 && near.r==255 && near.g==0 && near.b==0);
    v.category=7;neutral(camera_vehicle_marker(&v,.1f,.4f));
    v.category=2;v.x=0;neutral(camera_vehicle_marker(&v,.1f,.4f));
    v.x=.5f;neutral(camera_vehicle_marker(&v,.1f,.4f));
    v.x=.1f;v.y=.8f;neutral(camera_vehicle_marker(&v,.1f,.4f));
    puts("Marker scale: neutral fallback, size/color progression and clipped boxes passed");
}
