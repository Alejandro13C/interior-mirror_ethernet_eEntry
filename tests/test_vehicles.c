#include "vehicle_tracking.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    const uint64_t ns=10000000000ull;
    CameraAIResults r={.magic=CAMERA_AI_MAGIC,.version=CAMERA_AI_VERSION,.count=1,
        .captured_ns=ns,.completed_ns=ns+100000000};
    r.vehicles[0]=(CameraVehicle){.x=.1f,.y=.2f,.w=.3f,.h=.4f,.confidence=.9f,.category=2};
    assert(camera_results_valid(&r,sizeof r,ns+200000000));
    assert(!camera_results_valid(&r,sizeof r-1,ns+200000000));
    assert(!camera_results_valid(&r,sizeof r,ns+CAMERA_AI_TTL_NS+1));
    assert(!camera_results_valid(&r,sizeof r,ns+1)); // completion in future
    r.count=CAMERA_AI_MAX+1;assert(!camera_results_valid(&r,sizeof r,ns+200000000));r.count=1;
    r.vehicles[0].x=NAN;assert(!camera_results_valid(&r,sizeof r,ns+200000000));r.vehicles[0].x=.1f;
    r.vehicles[0].w=2;assert(!camera_results_valid(&r,sizeof r,ns+200000000));r.vehicles[0].w=.3f;
    r.vehicles[0].category=0;assert(!camera_results_valid(&r,sizeof r,ns+200000000));r.vehicles[0].category=2;
    r.count=0;assert(camera_results_valid(&r,sizeof r,ns+200000000));
    r.count=1;r.vehicles[0].vx=NAN;assert(!camera_results_valid(&r,sizeof r,ns+200000000));
    r.vehicles[0].vx=3;assert(!camera_results_valid(&r,sizeof r,ns+200000000));r.vehicles[0].vx=0;
    CameraTracks tracks={0};
    CameraVehicle cars[2]={
        {.x=.1f,.y=.2f,.w=.2f,.h=.2f,.confidence=.9f,.category=2},
        {.x=.6f,.y=.2f,.w=.2f,.h=.2f,.confidence=.8f,.category=2}};
    camera_track(&tracks,cars,2,ns);
    assert(cars[0].hits==1 && cars[1].hits==1 && cars[0].vx==0);
    uint32_t first=cars[0].track,second=cars[1].track;
    assert(first && second && first!=second);
    cars[0].x+=.01f;cars[1].x-=.01f;
    camera_track(&tracks,cars,2,ns+200000000);
    assert(cars[0].track==first && cars[1].track==second);
    assert(cars[0].hits==2 && cars[1].hits==2);
    assert(fabsf(cars[0].vx-.05f)<.0001f && fabsf(cars[1].vx+.05f)<.0001f);
    float x,y;camera_vehicle_anchor(&cars[0],200000000,&x,&y);
    assert(fabsf(x-.22f)<.0001f && fabsf(y-.2f)<.0001f);
    CameraVehicle fast=cars[0];fast.vx=2;fast.vy=-2;
    camera_vehicle_anchor(&fast,700000000,&x,&y);
    assert(fabsf(x-.31f)<.0001f && fabsf(y-.1f)<.0001f);
    camera_track(&tracks,cars,2,ns+2000000000);
    assert(cars[0].track!=first && cars[1].track!=second && cars[0].track!=cars[1].track);
    assert(cars[0].hits==1 && cars[0].vx==0);
    puts("Vehicle protocol, expiration and tracking tests passed");
}
