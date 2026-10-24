#include "gst_panini.h"
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>
#include <assert.h>
#include <stdio.h>
#include <stdint.h>

/* Deterministic textured I420 input: compare old/new output byte for byte,
   and time warmed remapping independently of camera/network variability. */
int main(int argc,char **argv) {
    if(argc!=2){fprintf(stderr,"Usage: benchmark-panini output.i420\n");return 2;}
    gst_init(NULL,NULL);assert(camera_panini_register());
    GError *error=NULL;
    GstElement *p=gst_parse_launch("appsrc name=input format=time ! "
        "video/x-raw,format=I420,width=1344,height=968,framerate=25/1 ! "
        "camerapanini name=warp ! appsink name=output sync=false",&error);
    assert(p && !error);
    GstElement *source=gst_bin_get_by_name(GST_BIN(p),"input");
    GstElement *sink=gst_bin_get_by_name(GST_BIN(p),"output");
    GstElement *warp=gst_bin_get_by_name(GST_BIN(p),"warp");
    PaniniConfig config={1,180,130,0};camera_panini_configure(warp,&config);
    assert(gst_element_set_state(p,GST_STATE_PLAYING)!=GST_STATE_CHANGE_FAILURE);
    const size_t bytes=1344u*968u*3u/2u;guint8 *pixels=g_malloc(bytes);
    uint32_t seed=42;
    for(size_t i=0;i<bytes;++i){seed=seed*1664525u+1013904223u;pixels[i]=seed>>24;}
    gint64 elapsed=0;
    for(int i=0;i<62;++i) {
        GstBuffer *b=gst_buffer_new_allocate(NULL,bytes,NULL);gst_buffer_fill(b,0,pixels,bytes);
        GST_BUFFER_PTS(b)=(uint64_t)i*GST_SECOND/25;GST_BUFFER_DURATION(b)=GST_SECOND/25;
        gint64 start=g_get_monotonic_time();
        assert(gst_app_src_push_buffer(GST_APP_SRC(source),b)==GST_FLOW_OK);
        GstSample *sample=gst_app_sink_try_pull_sample(GST_APP_SINK(sink),15*GST_SECOND);assert(sample);
        if(i>=2)elapsed+=g_get_monotonic_time()-start;
        if(i==0) {
            GstVideoInfo info;GstVideoFrame f;
            assert(gst_video_info_from_caps(&info,gst_sample_get_caps(sample)));
            assert(gst_video_frame_map(&f,&info,gst_sample_get_buffer(sample),GST_MAP_READ));
            FILE *file=fopen(argv[1],"wb");assert(file);
            for(int plane=0;plane<3;++plane)for(int y=0;y<GST_VIDEO_FRAME_COMP_HEIGHT(&f,plane);++y)
                assert(fwrite((guint8 *)GST_VIDEO_FRAME_PLANE_DATA(&f,plane)+y*GST_VIDEO_FRAME_PLANE_STRIDE(&f,plane),
                    1,GST_VIDEO_FRAME_COMP_WIDTH(&f,plane),file)==(size_t)GST_VIDEO_FRAME_COMP_WIDTH(&f,plane));
            assert(!fclose(file));gst_video_frame_unmap(&f);
        }
        gst_sample_unref(sample);
    }
    printf("{\"frames\":60,\"mean_remap_ms\":%.4f}\n",elapsed/60000.0);
    gst_element_set_state(p,GST_STATE_NULL);g_free(pixels);
    gst_object_unref(source);gst_object_unref(sink);gst_object_unref(warp);gst_object_unref(p);
    return 0;
}
