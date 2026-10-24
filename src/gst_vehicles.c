#define _GNU_SOURCE
#include "gst_vehicles.h"
#include "vehicle_protocol.h"
#include "vehicle_marker.h"
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
    /* Worker health: time of its last reply, and how long the indicator is allowed to wait. */
    uint64_t started_ns,last_reply_ns,reply_gap_max,ai_stale_ns,ai_stale_events;
    uint64_t stats_ns,stats_at,stats_sent,stats_drawn;
    uint64_t period_ns; /* minimum time between exported images (CAMERA_AI_PERIOD_MS) */
    uint64_t hold_ns;   /* how long a result stays drawn (CAMERA_AI_HOLD_MS) */
    bool ai_stale;
    CameraAIFrame *frame;
    CameraAIResults result;
    float far_width,near_width;
    float marker_scale; /* CAMERA_MARKER_SIZE / 20: triangle size relative to the default */
    /* Exported image: whole picture by default, or the band [band_y0, band_y0+band_h)
       of it (CAMERA_AI_BAND, a detection-only concession on vertical field). */
    float band_y0,band_h;
    unsigned export_width,export_height;
    guint16 mx[3][CAMERA_AI_MAX_WIDTH],my[3][CAMERA_AI_HEIGHT];
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
static uint64_t env_ms(const char *name,uint64_t fallback) {
    const char *value=g_getenv(name);
    if(!value || !*value)return fallback;
    char *end;unsigned long long ms=strtoull(value,&end,10);
    if(*end || ms>3600000) {g_printerr("Ignoring invalid %s=%s\n",name,value);return fallback;}
    return ms;
}
/* "y0,y1" as fractions of the picture height, 0 <= y0 < y1 <= 1. */
static bool parse_band(const char *value,float *y0,float *h) {
    if(!value || !*value)return false;
    char *end;float a=strtof(value,&end);
    if(*end!=',') {g_printerr("Ignoring invalid CAMERA_AI_BAND=%s\n",value);return false;}
    float b=strtof(end+1,&end);
    if(*end || !isfinite(a) || !isfinite(b) || a<0 || b>1 || b-a<.05f) {
        g_printerr("Ignoring invalid CAMERA_AI_BAND=%s\n",value);return false;
    }
    *y0=a;*h=b-a;return true;
}
static gboolean start(GstBaseTransform *base) {
    CameraVehicles *s=(CameraVehicles *)base;
    const char *far=g_getenv("CAMERA_MARKER_FAR_WIDTH");
    const char *near=g_getenv("CAMERA_MARKER_NEAR_WIDTH");
    s->far_width=s->near_width=0;
    /* Visibility: default 20 px (about 4 mm on the 7-inch panel). */
    s->marker_scale=(float)CLAMP(env_ms("CAMERA_MARKER_SIZE",20),12,80)/20.f;
    if(far || near) {
        char *end_far=NULL,*end_near=NULL;
        float f=far?strtof(far,&end_far):0,n=near?strtof(near,&end_near):0;
        /* A cosmetic setting must never take the rear view down: fall back to white. */
        if(!far || !near || end_far==far || end_near==near || *end_far || *end_near ||
           !isfinite(f) || !isfinite(n) || f<=0 || n<=f || n>=1)
            g_printerr("Invalid marker width calibration (require 0 < FAR < NEAR < 1); using white markers\n");
        else {
            s->far_width=f;s->near_width=n;
            g_printerr("Experimental apparent-width markers enabled; no distance or risk measurement\n");
        }
    }
    s->band_y0=0;s->band_h=1;
    if(parse_band(g_getenv("CAMERA_AI_BAND"),&s->band_y0,&s->band_h))
        g_printerr("Detection limited to the band y=%.3f..%.3f of the picture (concession: vertical field)\n",
            s->band_y0,s->band_y0+s->band_h);
    /* 1 s: replies arrive every <=555 ms in 30 min of live use (p95 441 ms). */
    s->ai_stale_ns=env_ms("CAMERA_AI_STALE_MS",1000)*GST_MSECOND;
    s->period_ns=env_ms("CAMERA_AI_PERIOD_MS",333)*GST_MSECOND;
    /* Concession: holding marks longer than the 750 ms TTL shows older positions. */
    s->hold_ns=MIN(env_ms("CAMERA_AI_HOLD_MS",750),1500)*GST_MSECOND;
    s->stats_ns=env_ms("CAMERA_STATS_MS",10000)*GST_MSECOND;
    s->started_ns=s->stats_at=monotonic_ns();
    s->last_reply_ns=s->reply_gap_max=s->ai_stale_events=s->stats_sent=s->stats_drawn=0;
    s->ai_stale=false;
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
        " marked_frames=%" G_GUINT64_FORMAT " rejected_results=%" G_GUINT64_FORMAT
        " ai_stale_events=%" G_GUINT64_FORMAT "\n",
        s->sent,s->dropped,s->drawn,s->rejected,s->ai_stale_events);
    return TRUE;
}
static gboolean set_info(GstVideoFilter *filter,GstCaps *ic,GstVideoInfo *in,GstCaps *oc,GstVideoInfo *out) {
    (void)ic;(void)oc;(void)out;
    CameraVehicles *s=(CameraVehicles *)filter;
    if(s->band_h<1) {
        /* Band: full width at 512 px, same scale vertically, even sizes. */
        double band_rows=s->band_h*GST_VIDEO_INFO_HEIGHT(in);
        s->export_width=CAMERA_AI_MAX_WIDTH;
        s->export_height=2*(unsigned)lround(CAMERA_AI_MAX_WIDTH*band_rows/GST_VIDEO_INFO_WIDTH(in)/2);
        if(!camera_frame_size_valid(s->export_width,s->export_height)) {
            g_printerr("CAMERA_AI_BAND gives an invalid %ux%u export; using the whole picture\n",
                s->export_width,s->export_height);
            s->band_y0=0;s->band_h=1;
        }
    }
    if(s->band_h>=1) {s->export_width=CAMERA_AI_WIDTH;s->export_height=CAMERA_AI_HEIGHT;}
    for(int plane=0;plane<3;++plane) {
        unsigned w=plane?s->export_width/2:s->export_width;
        unsigned h=plane?s->export_height/2:s->export_height;
        int source_height=GST_VIDEO_INFO_COMP_HEIGHT(in,plane);
        int first=(int)(s->band_y0*source_height),rows=(int)(s->band_h*source_height);
        for(unsigned x=0;x<w;++x)s->mx[plane][x]=(guint16)(x*GST_VIDEO_INFO_COMP_WIDTH(in,plane)/w);
        for(unsigned y=0;y<h;++y)s->my[plane][y]=(guint16)MIN(source_height-1,first+(int)(y*rows/h));
    }
    return TRUE;
}
static void pixel(GstVideoFrame *f,int x,int y,const guint8 yuv[3]) {
    if(x<0 || y<0 || x>=GST_VIDEO_FRAME_WIDTH(f) || y>=GST_VIDEO_FRAME_HEIGHT(f))return;
    guint8 *p=GST_VIDEO_FRAME_PLANE_DATA(f,0);
    p[y*GST_VIDEO_FRAME_PLANE_STRIDE(f,0)+x]=yuv[0];
    for(int plane=1;plane<3;++plane) {
        p=GST_VIDEO_FRAME_PLANE_DATA(f,plane);p[(y/2)*GST_VIDEO_FRAME_PLANE_STRIDE(f,plane)+x/2]=yuv[plane];
    }
}
static void yuv(const GstVideoFrame *f,unsigned char red,unsigned char green,unsigned char blue,guint8 out[3]) {
    bool full=f->info.colorimetry.range==GST_VIDEO_COLOR_RANGE_0_255;
    bool bt709=f->info.colorimetry.matrix==GST_VIDEO_COLOR_MATRIX_BT709;
    float kr=bt709?.2126f:.299f,kb=bt709?.0722f:.114f;
    float r=red/255.f,g=green/255.f,b=blue/255.f;
    float luma=kr*r+(1-kr-kb)*g+kb*b;
    out[0]=(guint8)lroundf((full?0:16)+(full?255:219)*luma);
    out[1]=(guint8)CLAMP(lroundf(128+(full?255:224)*(b-luma)/(2*(1-kb))),0,255);
    out[2]=(guint8)CLAMP(lroundf(128+(full?255:224)*(r-luma)/(2*(1-kr))),0,255);
}
static void triangle(GstVideoFrame *f,int cx,int tip,CameraMarker marker) {
    bool full=f->info.colorimetry.range==GST_VIDEO_COLOR_RANGE_0_255;
    guint8 fill[3];yuv(f,marker.r,marker.g,marker.b,fill);
    const guint8 outline[3]={full?0:16,128,128};
    /* Dark outline 2 px at the default size, thicker on larger markers. */
    int edge=MAX(2,marker.height/10);
    for(int y=0;y<=marker.height;++y) {
        int half=(marker.height-y)/2;
        for(int x=-half;x<=half;++x)pixel(f,cx+x,tip-marker.height+y,
            (y<edge || abs(x)>half-edge)?outline:fill);
    }
}
/* Detection unavailable: amber square in the top-right corner. Without it,
   "no triangle" would look the same as "no vehicle behind". */
#define AI_INDICATOR 14
static void ai_indicator(GstVideoFrame *f) {
    bool full=f->info.colorimetry.range==GST_VIDEO_COLOR_RANGE_0_255;
    guint8 amber[3];yuv(f,255,176,0,amber);
    const guint8 outline[3]={full?0:16,128,128};
    int left=GST_VIDEO_FRAME_WIDTH(f)-AI_INDICATOR-8,top=8;
    for(int y=0;y<AI_INDICATOR;++y)for(int x=0;x<AI_INDICATOR;++x)
        pixel(f,left+x,top+y,(x<2 || y<2 || x>=AI_INDICATOR-2 || y>=AI_INDICATOR-2)?outline:amber);
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
           r.sequence==s->frame->sequence && r.captured_ns==s->frame->captured_ns) {
            s->awaiting_result=false;
            if(s->last_reply_ns && now-s->last_reply_ns>s->reply_gap_max)s->reply_gap_max=now-s->last_reply_ns;
            s->last_reply_ns=now;
        }
        if(camera_results_valid(&r,(size_t)n,now) && r.captured_ns>s->last_result_ns) {
            s->last_result_ns=r.captured_ns;
            /* A single missed observation must not erase a still-fresh mark.
               Keep its original timestamp, so misses never extend its TTL. */
            if(r.count || !s->result.count)s->result=r;
        } else s->rejected++;
    }
    /* At most one image in flight. Export only when the worker has replied,
       or after a bounded retry if it died. No stale images accumulate. */
    if(s->awaiting_result && now-s->last_send>CAMERA_AI_TTL_NS) {
        s->awaiting_result=false;
        /* A fast detector stop/start recreates its runtime directory: sends
           still work, but replies have no path back to us. Rebind. */
        if(s->fd>=0 && access(s->local,F_OK)<0){close(s->fd);s->fd=-1;s->next_try=0;}
    }
    /* An old frame is skipped without using up the turn: the next fresh one goes. */
    if(s->fd>=0 && !s->awaiting_result && now-s->last_send>=s->period_ns &&
       now-captured<=150*GST_MSECOND) {
        s->last_send=now;
        {
            CameraAIFrame *out=s->frame;
            out->magic=CAMERA_AI_MAGIC;out->version=CAMERA_AI_VERSION;
            out->width=s->export_width;out->height=s->export_height;
            out->sequence=++s->sequence;out->captured_ns=captured;
            out->flags=(frame->info.colorimetry.matrix==GST_VIDEO_COLOR_MATRIX_BT709?CAMERA_AI_709:0) |
                (frame->info.colorimetry.range==GST_VIDEO_COLOR_RANGE_0_255?CAMERA_AI_FULL_RANGE:0);
            size_t offset=0;
            for(int plane=0;plane<3;++plane) {
                unsigned w=plane?s->export_width/2:s->export_width;
                unsigned h=plane?s->export_height/2:s->export_height;
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
        now-s->result.captured_ns<=s->hold_ns) {
        for(uint32_t i=0;i<s->result.count;++i) {
            CameraVehicle *v=&s->result.vehicles[i];
            if(v->hits<2)continue;
            /* Results are normalized to the exported image: map a band back
               to the picture (width is unchanged, so the marker uses v). */
            CameraVehicle placed=*v;
            placed.y=s->band_y0+v->y*s->band_h;placed.h=v->h*s->band_h;placed.vy=v->vy*s->band_h;
            float x,y;
            camera_vehicle_anchor(&placed,captured>s->result.captured_ns?captured-s->result.captured_ns:0,&x,&y);
            int cx=(int)(x*GST_VIDEO_FRAME_WIDTH(frame));
            int tip=(int)(y*GST_VIDEO_FRAME_HEIGHT(frame))-4;
            CameraMarker marker=camera_vehicle_marker(v,s->far_width,s->near_width);
            marker.height=(int)lroundf(marker.height*s->marker_scale);
            marker.height=MIN(marker.height,MIN(GST_VIDEO_FRAME_WIDTH(frame)-6,GST_VIDEO_FRAME_HEIGHT(frame)-3));
            int margin=marker.height/2+2;
            triangle(frame,CLAMP(cx,margin,GST_VIDEO_FRAME_WIDTH(frame)-margin-1),
                     CLAMP(tip,marker.height+2,GST_VIDEO_FRAME_HEIGHT(frame)-1),marker);
        }
        if(s->result.count)s->drawn++;
    }
    uint64_t heard=s->last_reply_ns?s->last_reply_ns:s->started_ns;
    bool stale=s->ai_stale_ns && now-heard>s->ai_stale_ns;
    if(stale && !s->ai_stale) {
        s->ai_stale_events++;
        g_printerr("Detection unavailable: no worker reply for %.0f ms\n",(double)(now-heard)/GST_MSECOND);
    } else if(!stale && s->ai_stale) g_printerr("Detection available again\n");
    s->ai_stale=stale;
    if(stale)ai_indicator(frame);
    if(s->stats_ns && now-s->stats_at>=s->stats_ns) {
        g_printerr("overlay: sent=%" G_GUINT64_FORMAT " marked_frames=%" G_GUINT64_FORMAT
            " max_reply_gap_ms=%.0f ai_stale=%d ai_stale_events=%" G_GUINT64_FORMAT "\n",
            s->sent-s->stats_sent,s->drawn-s->stats_drawn,(double)s->reply_gap_max/GST_MSECOND,
            stale,s->ai_stale_events);
        s->stats_at=now;s->stats_sent=s->sent;s->stats_drawn=s->drawn;s->reply_gap_max=0;
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
        "Nonblocking image export and expiring vehicle triangles with optional apparent-width styling","Rear Camera");
    GST_BASE_TRANSFORM_CLASS(klass)->start=start;GST_BASE_TRANSFORM_CLASS(klass)->stop=stop;
    GST_VIDEO_FILTER_CLASS(klass)->set_info=set_info;
    GST_VIDEO_FILTER_CLASS(klass)->transform_frame_ip=transform;
}
static void camera_vehicles_init(CameraVehicles *s) {
    s->fd=-1;s->band_h=1;gst_base_transform_set_in_place(GST_BASE_TRANSFORM(s),TRUE);
}
bool camera_vehicles_register(void) {
    return gst_element_register(NULL,"cameravehicles",GST_RANK_NONE,camera_vehicles_get_type());
}
