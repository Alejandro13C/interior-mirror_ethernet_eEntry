#include "vehicle_tracking.h"
#include <string.h>

/* Association, evaluated offline at the Pi's cadence (tools/track-eval.py,
   variant b+c+e): compare against the box PREDICTED from the track's
   velocity (at most one box of displacement), accept a nearby centre when
   IoU fails (sizes within 2x), and assign globally by cost, IoU before
   centre distance, instead of in detection order. */
#define TRACK_IOU .25f
#define PREDICT_BOXES 1.f
#define CENTRE_GATE .6f /* centre distance in mean box sizes */
#define SIZE_RATIO 2.f

static float overlap(const CameraVehicle *a,const CameraVehicle *b) {
    float x=fmaxf(a->x,b->x),y=fmaxf(a->y,b->y);
    float w=fmaxf(0,fminf(a->x+a->w,b->x+b->w)-x);
    float h=fmaxf(0,fminf(a->y+a->h,b->y+b->h)-y);
    float area=w*h;return area/(a->w*a->h+b->w*b->h-area);
}
static CameraVehicle predict(const CameraVehicle *t,uint64_t last,uint64_t ns) {
    float dt=(float)(ns-last)/1e9f;
    CameraVehicle r=*t;
    r.x=t->x+fmaxf(-PREDICT_BOXES*t->w,fminf(t->vx*dt,PREDICT_BOXES*t->w));
    r.y=t->y+fmaxf(-PREDICT_BOXES*t->h,fminf(t->vy*dt,PREDICT_BOXES*t->h));
    return r;
}
static bool centre_gate(const CameraVehicle *r,const CameraVehicle *d,float *distance) {
    float rw=d->w/r->w,rh=d->h/r->h;
    if(rw<1/SIZE_RATIO || rw>SIZE_RATIO || rh<1/SIZE_RATIO || rh>SIZE_RATIO)return false;
    float mw=(r->w+d->w)*.5f,mh=(r->h+d->h)*.5f;
    float dx=(d->x+d->w*.5f-r->x-r->w*.5f)/mw,dy=(d->y+d->h*.5f-r->y-r->h*.5f)/mh;
    *distance=sqrtf(dx*dx+dy*dy);
    return *distance<CENTRE_GATE;
}
typedef struct {float cost;uint8_t i,k;} Pair;
static bool before(const Pair *a,const Pair *b) {
    if(a->cost!=b->cost)return a->cost<b->cost;
    if(a->i!=b->i)return a->i<b->i;
    return a->k<b->k;
}
void camera_track(CameraTracks *t,CameraVehicle *vehicles,size_t count,uint64_t ns) {
    bool used[CAMERA_AI_MAX]={0};
    int match[CAMERA_AI_MAX];
    CameraVehicle ref[CAMERA_AI_MAX];
    Pair pairs[CAMERA_AI_MAX*CAMERA_AI_MAX];
    size_t n=0;
    if(count>CAMERA_AI_MAX)count=CAMERA_AI_MAX;
    for(size_t k=0;k<CAMERA_AI_MAX;++k) {
        if(t->last_ns[k]>ns || ns-t->last_ns[k]>CAMERA_AI_TTL_NS)t->last_ns[k]=0;
        if(t->last_ns[k])ref[k]=predict(&t->vehicles[k],t->last_ns[k],ns);
    }
    for(size_t i=0;i<count;++i) {
        match[i]=-1;
        for(size_t k=0;k<CAMERA_AI_MAX;++k) {
            if(!t->last_ns[k] || t->vehicles[k].category!=vehicles[i].category)continue;
            float iou=overlap(&ref[k],&vehicles[i]),distance;
            /* Centre matches always rank after any IoU match. */
            if(iou>TRACK_IOU)pairs[n++]=(Pair){1-iou,(uint8_t)i,(uint8_t)k};
            else if(centre_gate(&ref[k],&vehicles[i],&distance))
                pairs[n++]=(Pair){1+distance/CENTRE_GATE,(uint8_t)i,(uint8_t)k};
        }
    }
    for(size_t a=1;a<n;++a) { /* insertion sort: at most 256 pairs */
        Pair p=pairs[a];size_t b=a;
        while(b && before(&p,&pairs[b-1])){pairs[b]=pairs[b-1];--b;}
        pairs[b]=p;
    }
    for(size_t a=0;a<n;++a)
        if(match[pairs[a].i]<0 && !used[pairs[a].k]){match[pairs[a].i]=pairs[a].k;used[pairs[a].k]=true;}
    /* Updates first, so births never evict a track matched in this frame. */
    for(size_t i=0;i<count;++i) {
        if(match[i]<0)continue;
        int k=match[i];
        const CameraVehicle *old=&t->vehicles[k];
        vehicles[i].track=old->track;
        vehicles[i].hits=old->hits<1000?old->hits+1:1000;
        float dt=(float)(ns-t->last_ns[k])/1e9f;
        float vx=0,vy=0;
        if(dt>.001f) { /* from the stored observation, not the prediction */
            vx=((vehicles[i].x+vehicles[i].w*.5f)-(old->x+old->w*.5f))/dt;
            vy=((vehicles[i].y+vehicles[i].h*.5f)-(old->y+old->h*.5f))/dt;
        }
        vehicles[i].vx=fmaxf(-2,fminf(2,vx));
        vehicles[i].vy=fmaxf(-2,fminf(2,vy));
        t->vehicles[k]=vehicles[i];t->last_ns[k]=ns;
    }
    for(size_t i=0;i<count;++i) {
        if(match[i]>=0)continue;
        int best=-1;
        for(size_t k=0;k<CAMERA_AI_MAX;++k)if(!used[k] && !t->last_ns[k]){best=(int)k;break;}
        if(best<0)for(size_t k=0;k<CAMERA_AI_MAX;++k)if(!used[k] &&
            (best<0 || t->last_ns[k]<t->last_ns[best]))best=(int)k;
        if(++t->next_id==0)++t->next_id;
        vehicles[i].track=t->next_id;
        vehicles[i].hits=1;vehicles[i].vx=vehicles[i].vy=0;
        if(best>=0){used[best]=true;t->vehicles[best]=vehicles[i];t->last_ns[best]=ns;}
    }
}
