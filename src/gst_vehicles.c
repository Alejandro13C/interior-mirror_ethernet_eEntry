#define _GNU_SOURCE
#include "gst_vehicles.h"
#include "vehicle_protocol.h"
#include <gst/video/gstvideofilter.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    GstVideoFilter parent;
    int fd;
    bool awaiting_result;
    char local[108],peer[108];
    uint64_t next_try,last_send,sequence,sent,dropped,drawn,rejected,last_result_ns;
    CameraAIFrame *frame;
    CameraAIResults result;
    guint16 mx[3][CAMERA_AI_WIDTH],my[3][CAMERA_AI_HEIGHT];
} CameraVehicles;
typedef struct {GstVideoFilterClass parent;} CameraVehiclesClass;
G_DEFINE_TYPE(CameraVehicles,camera_vehicles,GST_TYPE_VIDEO_FILTER)
static uint64_t monotonic_ns(void){return (uint64_t)g_get_monotonic_time()*1000;}
static void open_socket(CameraVehicles *self,uint64_t now) {
    if(self->fd>=0 || now<self->next_try)return;
    self->next_try=now+GST_SECOND;
    int fd=socket(AF_UNIX,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    if(fd<0)return;
    int size=256*1024;setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&size,sizeof size);
    struct sockaddr_un addr={.sun_family=AF_UNIX};
    memcpy(addr.sun_path,self->local,strlen(self->local)+1);
    unlink(self->local);
    if(bind(fd,(struct sockaddr *)&addr,sizeof addr)<0){close(fd);return;}
    self->fd=fd;
}
static gboolean start(GstBaseTransform *base) {
    CameraVehicles *s=(CameraVehicles *)base;
    const char *dir=g_getenv("CAMERA_DETECTOR_DIR");if(!dir)dir="/run/rear-camera-detector";
    if(strlen(dir)+16>=sizeof s->local)return FALSE;
    snprintf(s->local,sizeof s->local,"%s/video.sock",dir);
    snprintf(s->peer,sizeof s->peer,"%s/worker.sock",dir);
    s->frame=g_try_new0(CameraAIFrame,1);
    return s->frame!=NULL;
}
static gboolean stop(GstBaseTransform *base) {
    CameraVehicles *s=(CameraVehicles *)base;
    if(s->fd>=0){close(s->fd);unlink(s->local);s->fd=-1;}
    g_free(s->frame);s->frame=NULL;
    g_printerr("Vehicle overlay: sent=%" G_GUINT64_FORMAT " ipc_dropped=%" G_GUINT64_FORMAT
        " marked_frames=%" G_GUINT64_FORMAT " rejected_results=%" G_GUINT64_FORMAT "\n",
        s->sent,s->dropped,s->drawn,s->rejected);
    return TRUE;
}
static gboolean set_info(GstVideoFilter *filter,GstCaps *ic,GstVideoInfo *in,GstCaps *oc,GstVideoInfo *out) {
    (void)ic;(void)oc;(void)out;
    CameraVehicles *s=(CameraVehicles *)filter;
    for(int plane=0;plane<3;++plane) {
        unsigned w=plane?CAMERA_AI_WIDTH/2:CAMERA_AI_WIDTH;
        unsigned h=plane?CAMERA_AI_HEIGHT/2:CAMERA_AI_HEIGHT;
        for(unsigned x=0;x<w;++x)s->mx[plane][x]=(guint16)(x*GST_VIDEO_INFO_COMP_WIDTH(in,plane)/w);
        for(unsigned y=0;y<h;++y)s->my[plane][y]=(guint16)(y*GST_VIDEO_INFO_COMP_HEIGHT(in,plane)/h);
    }
    return TRUE;
}
static void pixel(GstVideoFrame *f,int x,int y,guint8 luma) {
    if(x<0 || y<0 || x>=GST_VIDEO_FRAME_WIDTH(f) || y>=GST_VIDEO_FRAME_HEIGHT(f))return;
    guint8 *p=GST_VIDEO_FRAME_PLANE_DATA(f,0);
    p[y*GST_VIDEO_FRAME_PLANE_STRIDE(f,0)+x]=luma;
    for(int plane=1;plane<3;++plane) {
        p=GST_VIDEO_FRAME_PLANE_DATA(f,plane);p[(y/2)*GST_VIDEO_FRAME_PLANE_STRIDE(f,plane)+x/2]=128;
    }
}
static void triangle(GstVideoFrame *f,int cx,int tip) {
    /* Downward white triangle, black outline. No distance/severity colour. */
    for(int y=0;y<=20;++y) {
        int half=(20-y)/2;
        for(int x=-half;x<=half;++x)pixel(f,cx+x,tip-20+y,
            (y<2 || abs(x)>=half-1)?16:235);
    }
}
static GstFlowReturn transform(GstVideoFilter *filter,GstVideoFrame *frame) {
    CameraVehicles *s=(CameraVehicles *)filter;
    uint64_t now=monotonic_ns();open_socket(s,now);
    uint64_t captured=now;
    GstBuffer *buffer=frame->buffer;
    GstClock *clock=gst_element_get_clock(GST_ELEMENT(s));
    if(clock && GST_BUFFER_PTS_IS_VALID(buffer)) {
        uint64_t running=gst_clock_get_time(clock)-gst_element_get_base_time(GST_ELEMENT(s));
        if(running>=GST_BUFFER_PTS(buffer) && running-GST_BUFFER_PTS(buffer)<now)
            captured=now-(running-GST_BUFFER_PTS(buffer));
    }
    if(clock)gst_object_unref(clock);
    if(s->fd>=0)for(int i=0;i<8;++i) {
        CameraAIResults r;
        ssize_t n=recv(s->fd,&r,sizeof r,MSG_DONTWAIT|MSG_TRUNC);
        if(n<0)break;
        if(n==(ssize_t)sizeof r && r.magic==CAMERA_AI_MAGIC && r.version==CAMERA_AI_VERSION &&
           r.sequence==s->frame->sequence && r.captured_ns==s->frame->captured_ns)
            s->awaiting_result=false;
        if(camera_results_valid(&r,(size_t)n,now) && r.captured_ns>s->last_result_ns) {
            s->last_result_ns=r.captured_ns;
            /* A single missed observation must not erase a still-fresh mark.
               Keep its original timestamp, so misses never extend its TTL. */
            if(r.count || !s->result.count)s->result=r;
        } else s->rejected++;
    }
    /* At most one image in flight. Export only when the worker has replied,
       or after a bounded retry if it died. No stale images accumulate. */
    if(s->awaiting_result && now-s->last_send>CAMERA_AI_TTL_NS)s->awaiting_result=false;
    if(s->fd>=0 && !s->awaiting_result && now-s->last_send>=GST_SECOND/3) {
        s->last_send=now;
        if(now-captured<=150*GST_MSECOND) {
            CameraAIFrame *out=s->frame;
            out->magic=CAMERA_AI_MAGIC;out->version=CAMERA_AI_VERSION;
            out->width=CAMERA_AI_WIDTH;out->height=CAMERA_AI_HEIGHT;
            out->sequence=++s->sequence;out->captured_ns=captured;
            out->flags=(frame->info.colorimetry.matrix==GST_VIDEO_COLOR_MATRIX_BT709?CAMERA_AI_709:0) |
                (frame->info.colorimetry.range==GST_VIDEO_COLOR_RANGE_0_255?CAMERA_AI_FULL_RANGE:0);
            size_t offset=0;
            for(int plane=0;plane<3;++plane) {
                unsigned w=plane?CAMERA_AI_WIDTH/2:CAMERA_AI_WIDTH;
                unsigned h=plane?CAMERA_AI_HEIGHT/2:CAMERA_AI_HEIGHT;
                const guint8 *in=GST_VIDEO_FRAME_PLANE_DATA(frame,plane);
                int stride=GST_VIDEO_FRAME_PLANE_STRIDE(frame,plane);
                for(unsigned y=0;y<h;++y) {
                    const guint8 *row=in+s->my[plane][y]*stride;
                    for(unsigned x=0;x<w;++x)out->pixels[offset++]=row[s->mx[plane][x]];
                }
            }
            struct sockaddr_un peer={.sun_family=AF_UNIX};memcpy(peer.sun_path,s->peer,strlen(s->peer)+1);
            ssize_t n=sendto(s->fd,out,sizeof *out,MSG_DONTWAIT|MSG_NOSIGNAL,(struct sockaddr *)&peer,sizeof peer);
            if(n==(ssize_t)sizeof *out){s->sent++;s->awaiting_result=true;}
            else {
                s->dropped++;
                if(access(s->local,F_OK)<0) { close(s->fd);s->fd=-1; }
            }
        }
    }
    if(s->result.captured_ns && now>=s->result.captured_ns &&
        now-s->result.captured_ns<=CAMERA_AI_TTL_NS) {
        for(uint32_t i=0;i<s->result.count;++i) {
            CameraVehicle *v=&s->result.vehicles[i];
            if(v->hits<2)continue;
            float x,y;
            camera_vehicle_anchor(v,captured>s->result.captured_ns?captured-s->result.captured_ns:0,&x,&y);
            int cx=(int)(x*GST_VIDEO_FRAME_WIDTH(frame));
            int tip=(int)(y*GST_VIDEO_FRAME_HEIGHT(frame))-4;
            triangle(frame,CLAMP(cx,12,GST_VIDEO_FRAME_WIDTH(frame)-13),
                     CLAMP(tip,22,GST_VIDEO_FRAME_HEIGHT(frame)-1));
        }
        if(s->result.count)s->drawn++;
    }
    return GST_FLOW_OK;
}
static void camera_vehicles_class_init(CameraVehiclesClass *klass) {
    GstElementClass *element=GST_ELEMENT_CLASS(klass);
    GstCaps *caps=gst_caps_from_string("video/x-raw,format=I420,width=[24,8192],height=[24,8192]");
    gst_element_class_add_pad_template(element,gst_pad_template_new("sink",GST_PAD_SINK,GST_PAD_ALWAYS,caps));
    gst_element_class_add_pad_template(element,gst_pad_template_new("src",GST_PAD_SRC,GST_PAD_ALWAYS,caps));
    gst_caps_unref(caps);
    gst_element_class_set_static_metadata(element,"Vehicle overlay","Filter/Video",
        "Nonblocking latest-image export and expiring neutral vehicle triangles","Rear Camera");
    GST_BASE_TRANSFORM_CLASS(klass)->start=start;GST_BASE_TRANSFORM_CLASS(klass)->stop=stop;
    GST_VIDEO_FILTER_CLASS(klass)->set_info=set_info;
    GST_VIDEO_FILTER_CLASS(klass)->transform_frame_ip=transform;
}
static void camera_vehicles_init(CameraVehicles *s) {
    s->fd=-1;gst_base_transform_set_in_place(GST_BASE_TRANSFORM(s),TRUE);
}
bool camera_vehicles_register(void) {
    return gst_element_register(NULL,"cameravehicles",GST_RANK_NONE,camera_vehicles_get_type());
}
