#include "vehicle_tracking.h"
#include <string.h>
static float overlap(const CameraVehicle *a,const CameraVehicle *b) {
    float x=fmaxf(a->x,b->x),y=fmaxf(a->y,b->y);
    float w=fmaxf(0,fminf(a->x+a->w,b->x+b->w)-x);
    float h=fmaxf(0,fminf(a->y+a->h,b->y+b->h)-y);
    float area=w*h;return area/(a->w*a->h+b->w*b->h-area);
}
void camera_track(CameraTracks *t,CameraVehicle *vehicles,size_t count,uint64_t ns) {
    bool used[CAMERA_AI_MAX]={0};
    if(count>CAMERA_AI_MAX)count=CAMERA_AI_MAX;
    for(size_t k=0;k<CAMERA_AI_MAX;++k)
        if(t->last_ns[k]>ns || ns-t->last_ns[k]>CAMERA_AI_TTL_NS)t->last_ns[k]=0;
    for(size_t i=0;i<count;++i) {
        int best=-1;float score=.25f;
        for(size_t k=0;k<CAMERA_AI_MAX;++k) {
            if(used[k] || !t->last_ns[k] || t->vehicles[k].category!=vehicles[i].category)continue;
            float value=overlap(&t->vehicles[k],&vehicles[i]);
            if(value>score){score=value;best=(int)k;}
        }
        if(best<0) {
            for(size_t k=0;k<CAMERA_AI_MAX;++k)if(!used[k] && !t->last_ns[k]){best=(int)k;break;}
            if(best<0)for(size_t k=0;k<CAMERA_AI_MAX;++k)if(!used[k] &&
                (best<0 || t->last_ns[k]<t->last_ns[best]))best=(int)k;
            if(++t->next_id==0)++t->next_id;
            vehicles[i].track=t->next_id;
            vehicles[i].hits=1;vehicles[i].vx=vehicles[i].vy=0;
        } else {
            const CameraVehicle *old=&t->vehicles[best];
            vehicles[i].track=old->track;
            vehicles[i].hits=old->hits<1000?old->hits+1:1000;
            float dt=(float)(ns-t->last_ns[best])/1e9f;
            float vx=0,vy=0;
            if(dt>.001f) {
                vx=((vehicles[i].x+vehicles[i].w*.5f)-(old->x+old->w*.5f))/dt;
                vy=((vehicles[i].y+vehicles[i].h*.5f)-(old->y+old->h*.5f))/dt;
            }
            vehicles[i].vx=fmaxf(-2,fminf(2,vx));
            vehicles[i].vy=fmaxf(-2,fminf(2,vy));
        }
        if(best>=0) {
            used[best]=true;t->vehicles[best]=vehicles[i];t->last_ns[best]=ns;
        }
    }
}
