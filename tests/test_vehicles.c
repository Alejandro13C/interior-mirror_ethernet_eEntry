#include "vehicle_tracking.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static const uint64_t T0=10000000000ull,MS=1000000ull;
static CameraVehicle car(float x,float y,float w,float h) {
    return (CameraVehicle){.x=x,.y=y,.w=w,.h=h,.confidence=.9f,.category=2};
}
static uint32_t track_one(CameraTracks *t,CameraVehicle v,uint64_t ns,uint32_t *hits) {
    camera_track(t,&v,1,ns);
    if(hits)*hits=v.hits;
    return v.track;
}
/* Cases from the offline evaluation (tools/track-eval.py, variant b+c+e);
   each fails with plain IoU against the last box in detection order. */
static void association_tests(void) {
    /* Predicted box: a car moving ~0.28 widths/s keeps its id at 360 ms cadence. */
    CameraTracks t={0};uint32_t hits;
    uint32_t id=track_one(&t,car(.10f,.2f,.2f,.2f),T0,NULL);
    assert(track_one(&t,car(.20f,.2f,.2f,.2f),T0+360*MS,NULL)==id);
    assert(track_one(&t,car(.33f,.2f,.2f,.2f),T0+720*MS,&hits)==id && hits==3);
    /* Prediction is limited to one box of displacement. */
    memset(&t,0,sizeof t);
    id=track_one(&t,car(.10f,.2f,.2f,.2f),T0,NULL);
    for(unsigned k=0;k<CAMERA_AI_MAX;++k)if(t.last_ns[k] && t.vehicles[k].track==id)t.vehicles[k].vx=2;
    CameraTracks copy=t;
    assert(track_one(&t,car(.30f,.2f,.2f,.2f),T0+700*MS,NULL)==id);
    assert(track_one(&copy,car(.45f,.2f,.2f,.2f),T0+700*MS,NULL)!=id);
    /* Centre gate: IoU 0.22 but centres 0.57 mean boxes apart. */
    memset(&t,0,sizeof t);
    id=track_one(&t,car(.1f,.3f,.2f,.2f),T0,NULL);
    copy=t;
    CameraVehicle moved=car(.18f,.38f,.2f,.2f);
    camera_track(&t,&moved,1,T0+360*MS);
    assert(moved.track==id && moved.hits==2 && fabsf(moved.vx-.08f/.36f)<1e-4f && fabsf(moved.vy-.08f/.36f)<1e-4f);
    CameraTracks other=copy;
    assert(track_one(&other,car(.2f,.4f,.2f,.2f),T0+360*MS,NULL)!=id);  /* 0.71 apart */
    other=copy;
    assert(track_one(&other,car(.18f,.38f,.5f,.5f),T0+360*MS,NULL)!=id); /* 2.5x larger */
    /* Global assignment: the closer detection keeps the id even if it comes second. */
    memset(&t,0,sizeof t);
    id=track_one(&t,car(.30f,.2f,.2f,.2f),T0,NULL);
    CameraVehicle two[2]={car(.40f,.2f,.2f,.2f),car(.31f,.2f,.2f,.2f)};
    camera_track(&t,two,2,T0+100*MS);
    assert(two[1].track==id && two[1].hits==2 && two[0].track!=id && two[0].hits==1);
    /* Any IoU match ranks before a centre-gate match. */
    memset(&t,0,sizeof t);
    id=track_one(&t,car(.1f,.3f,.2f,.2f),T0,NULL);
    CameraVehicle rivals[2]={car(.18f,.38f,.2f,.2f),car(.12f,.30f,.2f,.2f)};
    camera_track(&t,rivals,2,T0+360*MS);
    assert(rivals[1].track==id && rivals[0].track!=id);
    /* Full table: updates go before births, so a birth cannot evict a track
       that is matched in the same frame. */
    memset(&t,0,sizeof t);
    uint32_t oldest=track_one(&t,car(0,0,.1f,.1f),T0,NULL);
    for(unsigned k=1;k<CAMERA_AI_MAX;++k)track_one(&t,car((k%4)*.25f,(k/4)*.25f,.1f,.1f),T0+10*MS,NULL);
    CameraVehicle crowd[2]={car(.88f,.88f,.1f,.1f),car(0,0,.1f,.1f)};
    camera_track(&t,crowd,2,T0+100*MS);
    assert(crowd[1].track==oldest && crowd[1].hits==2 && crowd[0].hits==1);
    /* Different categories never associate. */
    memset(&t,0,sizeof t);
    id=track_one(&t,car(.3f,.3f,.2f,.2f),T0,NULL);
    CameraVehicle bike=car(.3f,.3f,.2f,.2f);bike.category=3;
    camera_track(&t,&bike,1,T0+100*MS);
    assert(bike.track!=id && bike.hits==1);
}

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
    association_tests();
    puts("Vehicle protocol, expiration and tracking tests passed");
}
