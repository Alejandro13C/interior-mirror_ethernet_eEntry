#ifndef CAMERA_VEHICLE_PROTOCOL_H
#define CAMERA_VEHICLE_PROTOCOL_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <math.h>
#define CAMERA_AI_MAGIC 0x56454831u
#define CAMERA_AI_VERSION 2u
#define CAMERA_AI_WIDTH 416u
#define CAMERA_AI_HEIGHT 250u
#define CAMERA_AI_BYTES (CAMERA_AI_WIDTH*CAMERA_AI_HEIGHT*3u/2u)
#define CAMERA_AI_MAX 16u
#define CAMERA_AI_TTL_NS 750000000ull
#define CAMERA_AI_709 1u
#define CAMERA_AI_FULL_RANGE 2u

typedef struct {
    uint32_t magic,version,width,height;
    uint64_t sequence,captured_ns;
    uint32_t flags,reserved;
    uint8_t pixels[CAMERA_AI_BYTES];
} CameraAIFrame;
typedef struct {
    float x,y,w,h,confidence;
    uint32_t category,track,hits;
    float vx,vy; // normalized centre velocity per second
} CameraVehicle;
typedef struct {
    uint32_t magic,version,count,reserved;
    uint64_t sequence,captured_ns,completed_ns;
    CameraVehicle vehicles[CAMERA_AI_MAX];
} CameraAIResults;

static inline bool camera_vehicle_class(uint32_t c) {return c==2 || c==3 || c==5 || c==7;}
static inline bool camera_vehicle_valid(const CameraVehicle *v) {
    return isfinite(v->x) && isfinite(v->y) && isfinite(v->w) && isfinite(v->h) &&
        isfinite(v->confidence) && isfinite(v->vx) && isfinite(v->vy) &&
        fabsf(v->vx)<=2 && fabsf(v->vy)<=2 && v->x>=0 && v->y>=0 && v->w>0 && v->h>0 &&
        v->x+v->w<=1.00001f && v->y+v->h<=1.00001f &&
        v->confidence>=0 && v->confidence<=1 && camera_vehicle_class(v->category);
}
/* Predict only a short, bounded displacement; never hold a result past its TTL. */
static inline void camera_vehicle_anchor(const CameraVehicle *v,uint64_t age_ns,float *x,float *y) {
    float dt=fminf((float)age_ns/1e9f,.5f);
    float dx=fmaxf(-v->w*.5f,fminf(v->vx*dt,v->w*.5f));
    float dy=fmaxf(-v->h*.5f,fminf(v->vy*dt,v->h*.5f));
    *x=fmaxf(0,fminf(1,v->x+v->w*.5f+dx));
    *y=fmaxf(0,fminf(1,v->y+dy));
}
static inline bool camera_results_valid(const CameraAIResults *r,size_t bytes,uint64_t now) {
    if(bytes!=sizeof(*r) || r->magic!=CAMERA_AI_MAGIC || r->version!=CAMERA_AI_VERSION ||
       r->count>CAMERA_AI_MAX || !r->captured_ns || r->completed_ns<r->captured_ns ||
       r->completed_ns>now || now<r->captured_ns || now-r->captured_ns>CAMERA_AI_TTL_NS) return false;
    for(uint32_t i=0;i<r->count;++i)if(!camera_vehicle_valid(&r->vehicles[i]))return false;
    return true;
}
#ifdef __cplusplus
static_assert(sizeof(CameraVehicle)==40 && sizeof(CameraAIResults)==680,"IPC layout");
#else
_Static_assert(sizeof(CameraVehicle)==40 && sizeof(CameraAIResults)==680,"IPC layout");
#endif
#endif
