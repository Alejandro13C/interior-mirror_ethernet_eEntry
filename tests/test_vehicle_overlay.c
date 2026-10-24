#define _GNU_SOURCE
#include "gst_vehicles.h"
#include "vehicle_protocol.h"
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <poll.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
static bool colored;
/* Triangle tip row: vehicle top y=.4 of the exported image, minus four pixels.
   In band mode the exported image is the band y=.25-.75 of the picture. */
static int tip=(int)(.4f*480)-4;
/* The detection-unavailable indicator occupies the top-right corner. */
static bool in_corner(int x,int row,int width) {return x>=width-30 && row<30;}
static bool indicator(GstSample *sample) {
    GstVideoInfo info;GstVideoFrame frame;
    assert(sample && gst_video_info_from_caps(&info,gst_sample_get_caps(sample)));
    assert(gst_video_frame_map(&frame,&info,gst_sample_get_buffer(sample),GST_MAP_READ));
    const guint8 *y=GST_VIDEO_FRAME_PLANE_DATA(&frame,0);
    const guint8 *u=GST_VIDEO_FRAME_PLANE_DATA(&frame,1);
    int stride=GST_VIDEO_FRAME_PLANE_STRIDE(&frame,0);
    // Centre of the 14-pixel amber square at x=778..791, y=8..21.
    bool yes=y[15*stride+785]>100 && u[7*GST_VIDEO_FRAME_PLANE_STRIDE(&frame,1)+392]<100;
    gst_video_frame_unmap(&frame);return yes;
}
static bool marked(GstSample *sample) {
    GstVideoInfo info;GstVideoFrame frame;
    assert(sample && gst_video_info_from_caps(&info,gst_sample_get_caps(sample)));
    assert(gst_video_frame_map(&frame,&info,gst_sample_get_buffer(sample),GST_MAP_READ));
    const guint8 *y=GST_VIDEO_FRAME_PLANE_DATA(&frame,0);
    int stride=GST_VIDEO_FRAME_PLANE_STRIDE(&frame,0);bool yes=false;
    for(int row=0;row<info.height;++row)for(int x=0;x<info.width;++x)
        if(y[row*stride+x]>32 && !in_corner(x,row,info.width)) {
        // Expected anchor: centre x=.45*800 and the tip row above.
        if(colored)assert(x>=349 && x<=371 && row>=tip-24 && row<=tip-2);
        else assert(x>=352 && x<=368 && row>=tip-18 && row<=tip-2);
        yes=true;
    }
    if(yes) {
        const guint8 *u=GST_VIDEO_FRAME_PLANE_DATA(&frame,1);
        const guint8 *v=GST_VIDEO_FRAME_PLANE_DATA(&frame,2);
        int uv=u[(tip-14)/2*GST_VIDEO_FRAME_PLANE_STRIDE(&frame,1)+180];
        int vv=v[(tip-14)/2*GST_VIDEO_FRAME_PLANE_STRIDE(&frame,2)+180];
        if(colored)assert(uv<128 && vv>128); // Yellow fill has real chroma.
        else assert(uv==128 && vv==128);
    }
    gst_video_frame_unmap(&frame);return yes;
}
int main(int argc,char **argv) {
    colored=argc==2 && !strcmp(argv[1],"--colored");
    bool invalid=argc==2 && !strcmp(argv[1],"--invalid-markers");
    bool band=argc==2 && !strcmp(argv[1],"--band");
    g_unsetenv("CAMERA_MARKER_FAR_WIDTH");g_unsetenv("CAMERA_MARKER_NEAR_WIDTH");g_unsetenv("CAMERA_AI_BAND");
    if(band) {
        g_setenv("CAMERA_AI_BAND","0.25,0.75",TRUE);
        tip=(int)((.25f+.4f*.5f)*480)-4; // band results map back to the picture
    }
    if(colored) {
        // Synthetic scale for rendering only; not a physical calibration.
        g_setenv("CAMERA_MARKER_FAR_WIDTH","0.1",TRUE);
        g_setenv("CAMERA_MARKER_NEAR_WIDTH","0.5",TRUE);
    }
    if(invalid) {
        // Reversed endpoints must keep video running with white markers.
        g_setenv("CAMERA_MARKER_FAR_WIDTH","0.5",TRUE);
        g_setenv("CAMERA_MARKER_NEAR_WIDTH","0.1",TRUE);
    }
    g_setenv("CAMERA_AI_STALE_MS","2500",TRUE);
    gst_init(&argc,&argv);assert(camera_vehicles_register());
    gchar *dir=g_dir_make_tmp("rear-camera-overlay-test-XXXXXX",NULL);assert(dir);
    g_setenv("CAMERA_DETECTOR_DIR",dir,TRUE);
    int fd=socket(AF_UNIX,SOCK_DGRAM|SOCK_NONBLOCK,0);assert(fd>=0);
    int size=256*1024;setsockopt(fd,SOL_SOCKET,SO_RCVBUF,&size,sizeof size);
    struct sockaddr_un addr={.sun_family=AF_UNIX};snprintf(addr.sun_path,sizeof addr.sun_path,"%s/worker.sock",dir);
    assert(!bind(fd,(struct sockaddr *)&addr,sizeof addr));
    GError *error=NULL;
    GstElement *p=gst_parse_launch("videotestsrc is-live=true pattern=black ! "
        "video/x-raw,format=I420,width=800,height=480,framerate=25/1 ! cameravehicles ! "
        "appsink name=output sync=false max-buffers=1 drop=true",&error);
    assert(p && !error);GstElement *sink=gst_bin_get_by_name(GST_BIN(p),"output");
    assert(gst_element_set_state(p,GST_STATE_PLAYING)!=GST_STATE_CHANGE_FAILURE);
    struct pollfd pollfd={.fd=fd,.events=POLLIN};assert(poll(&pollfd,1,3000)>0);
    CameraAIFrame *input=g_new0(CameraAIFrame,1);struct sockaddr_un peer;socklen_t length=sizeof peer;
    ssize_t n=recvfrom(fd,input,sizeof *input,0,(struct sockaddr *)&peer,&length);
    assert(n==(ssize_t)sizeof *input);
    // Whole picture 416x250, or the band 800x240 at 512 px wide: 512x154.
    if(band)assert(input->width==512 && input->height==154);
    else assert(input->width==CAMERA_AI_WIDTH && input->height==CAMERA_AI_HEIGHT);
    /* A slow worker must not receive a backlog while processing one image. */
    assert(poll(&pollfd,1,500)==0);
    CameraAIResults r={.magic=CAMERA_AI_MAGIC,.version=CAMERA_AI_VERSION,.count=1,
        .sequence=input->sequence,.captured_ns=input->captured_ns,
        .completed_ns=(uint64_t)g_get_monotonic_time()*1000};
    r.vehicles[0]=(CameraVehicle){.x=.3f,.y=.4f,.w=.3f,.h=.2f,.confidence=.9f,.category=2,.track=1,.hits=1};
    assert(sendto(fd,&r,sizeof r,0,(struct sockaddr *)&peer,length)==sizeof r);
    for(int i=0;i<4;++i) {
        GstSample *sample=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),GST_SECOND);
        // The worker has just replied: detection is available.
        assert(sample && !marked(sample) && !indicator(sample));gst_sample_unref(sample);
    }
    r.captured_ns=r.completed_ns=(uint64_t)g_get_monotonic_time()*1000;
    r.vehicles[0].hits=2;
    assert(sendto(fd,&r,sizeof r,0,(struct sockaddr *)&peer,length)==sizeof r);
    bool shown=false;
    for(int i=0;i<8 && !shown;++i) {
        GstSample *sample=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),GST_SECOND);
        assert(sample);shown=marked(sample);gst_sample_unref(sample);
    }
    assert(shown);
    /* A brief empty result must not flicker a confirmed mark, nor refresh it. */
    r.captured_ns=r.completed_ns=(uint64_t)g_get_monotonic_time()*1000;
    r.count=0;
    assert(sendto(fd,&r,sizeof r,0,(struct sockaddr *)&peer,length)==sizeof r);
    for(int i=0;i<4;++i) {
        GstSample *held=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),GST_SECOND);
        assert(held && marked(held));gst_sample_unref(held);
    }
    g_usleep(1000000); // Marker must disappear without fresh inference.
    GstSample *sample=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),GST_SECOND);
    assert(sample && !marked(sample));gst_sample_unref(sample);
    /* A result stamped in the future must be rejected: send a drawable one. */
    r.count=1;r.vehicles[0].hits=2;
    r.captured_ns=(uint64_t)g_get_monotonic_time()*1000+GST_SECOND;r.completed_ns=r.captured_ns;
    assert(sendto(fd,&r,sizeof r,0,(struct sockaddr *)&peer,length)==sizeof r);
    g_usleep(100000);
    sample=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),GST_SECOND);
    assert(sample && !marked(sample));gst_sample_unref(sample);
    /* Fast detector stop/start recreates the runtime directory, so the
       overlay's own socket path vanishes; it must rebind after a lost reply. */
    gchar *own=g_strdup_printf("%s/video.sock",dir);
    assert(!unlink(own));
    while(recv(fd,input,sizeof *input,MSG_DONTWAIT)>0){}
    bool rebound=false;
    for(int i=0;i<40 && !rebound;++i){g_usleep(100000);rebound=!access(own,F_OK);}
    assert(rebound);
    g_free(own);
    close(fd);unlink(addr.sun_path); // Worker loss must not stop video.
    g_usleep(100000);
    sample=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),GST_SECOND);
    assert(sample && !marked(sample));gst_sample_unref(sample);
    // Without replies the overlay must say so, instead of looking like an empty road.
    g_usleep(2600000);
    bool unavailable=false;
    for(int i=0;i<4 && !unavailable;++i) {
        sample=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),GST_SECOND);
        assert(sample);unavailable=indicator(sample);gst_sample_unref(sample);
    }
    assert(unavailable);
    gst_element_set_state(p,GST_STATE_NULL);gst_object_unref(sink);gst_object_unref(p);
    g_free(input);rmdir(dir);g_free(dir);
    puts("Vehicle overlay: rendering, expiration, future rejection, socket rebind, worker loss and indicator passed");
}
