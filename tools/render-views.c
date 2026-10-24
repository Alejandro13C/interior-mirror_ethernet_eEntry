/* Offline comparison: decode one complete IDR once, reuse the identical raw
   frame for every projection. Does not access the display or hardware decoder. */
#include "camera_rtp.h"
#include "gst_panini.h"
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>

typedef struct { uint8_t *data; size_t size; uint32_t timestamp; } Encoded;
static bool first_frame(void *ctx, uint8_t *data, size_t n, uint32_t ts, uint64_t arrival) {
    (void)arrival;
    Encoded *frame = ctx;
    frame->data = data; frame->size = n; frame->timestamp = ts;
    return false;
}
static GstSample *process(const char *description, GstCaps *caps, GstBuffer *buffer,
                          const PaniniConfig *projection) {
    GError *error = NULL;
    GstElement *pipeline = gst_parse_launch(description, &error);
    if (error || !pipeline) {
        fprintf(stderr,"Pipeline: %s\n", error ? error->message : "unknown");
        g_clear_error(&error);
        if (pipeline) gst_object_unref(pipeline);
        return NULL;
    }
    GstElement *input = gst_bin_get_by_name(GST_BIN(pipeline), "input");
    GstElement *output = gst_bin_get_by_name(GST_BIN(pipeline), "output");
    if (projection) {
        GstElement *warp = gst_bin_get_by_name(GST_BIN(pipeline), "warp");
        camera_panini_configure(warp, projection); gst_object_unref(warp);
    }
    gst_app_src_set_caps(GST_APP_SRC(input), caps);
    gst_element_set_state(pipeline, GST_STATE_PLAYING);
    GstBuffer *copy = gst_buffer_copy_deep(buffer);
    GST_BUFFER_PTS(copy) = 0; GST_BUFFER_DURATION(copy) = GST_SECOND/25;
    GstFlowReturn status = gst_app_src_push_buffer(GST_APP_SRC(input), copy);
    gst_app_src_end_of_stream(GST_APP_SRC(input));
    GstSample *sample = status == GST_FLOW_OK ?
        gst_app_sink_try_pull_sample(GST_APP_SINK(output), 20*GST_SECOND) : NULL;
    if (!sample) {
        GstBus *bus = gst_element_get_bus(pipeline);
        GstMessage *message = gst_bus_pop_filtered(bus, GST_MESSAGE_ERROR);
        if (message) {
            gchar *debug = NULL;
            gst_message_parse_error(message, &error, &debug);
            fprintf(stderr,"Render: %s\n%s\n",error->message,debug ? debug : "");
            g_clear_error(&error);g_free(debug);gst_message_unref(message);
        } else fprintf(stderr,"No output frame within timeout\n");
        gst_object_unref(bus);
    }
    gst_element_set_state(pipeline,GST_STATE_NULL);
    gst_object_unref(input);gst_object_unref(output);gst_object_unref(pipeline);
    return sample;
}
static bool save_ppm(GstSample *sample, const char *directory, const char *name) {
    GstVideoInfo info;GstVideoFrame frame;
    if (!gst_video_info_from_caps(&info,gst_sample_get_caps(sample)) ||
        info.finfo->format != GST_VIDEO_FORMAT_RGB ||
        !gst_video_frame_map(&frame,&info,gst_sample_get_buffer(sample),GST_MAP_READ)) return false;
    gchar *path = g_strdup_printf("%s/%s.ppm",directory,name);
    FILE *file = fopen(path,"wb");
    bool ok = file && fprintf(file,"P6\n%d %d\n255\n",info.width,info.height)>0;
    const uint8_t *data = GST_VIDEO_FRAME_PLANE_DATA(&frame,0);
    int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&frame,0);
    for (int y=0; ok && y<info.height; ++y)
        ok = fwrite(data+y*stride,3,(size_t)info.width,file)==(size_t)info.width;
    if (file && fclose(file)) ok=false;
    gst_video_frame_unmap(&frame);g_free(path);
    return ok;
}
int main(int argc,char **argv) {
    if (argc<3) { fprintf(stderr,"Usage: %s capture.pcap output-directory [--panini-only] [FOV ...]\n",argv[0]);return 2; }
    bool panini_only = argc>3 && !strcmp(argv[3],"--panini-only");
    int first_fov = panini_only ? 4 : 3;
    double fovs[32] = {110,120,130};
    size_t count = 3;
    if (argc>first_fov) {
        count = (size_t)(argc-first_fov);
        if (count>32) { fprintf(stderr,"Too many FOV values\n");return 2; }
        for (size_t j=0;j<count;++j) {
            char *end;
            long value = strtol(argv[first_fov+j],&end,10);
            if (!*argv[first_fov+j] || *end || value<40 || value>160) {
                fprintf(stderr,"FOV must be an integer from 40 to 160\n");return 2;
            }
            for (size_t k=0;k<j;++k) if (fovs[k]==value) {
                fprintf(stderr,"Repeated FOV\n");return 2;
            }
            fovs[j]=(double)value;
        }
    }
    gst_init(NULL,NULL);
    if (!camera_panini_register()) return 1;
    if (mkdir(argv[2],0755)<0 && errno!=EEXIST) { perror("mkdir");return 1; }
    FILE *file=fopen(argv[1],"rb");CameraPcap reader;CameraAssembler assembler={0};Encoded encoded={0};
    if (!file || !camera_pcap_open(&reader,file)) { fprintf(stderr,"Invalid capture\n");if(file)fclose(file);return 1; }
    uint8_t packet[65536];size_t size;uint64_t arrival;
    while (!encoded.data && camera_pcap_next(&reader,packet,sizeof packet,&size,&arrival)>0) {
        CameraPacket rtp;
        if (camera_packet(packet,size,&rtp)) camera_assemble(&assembler,&rtp,arrival,first_frame,&encoded);
    }
    fclose(file);camera_assembler_clear(&assembler);
    if (!encoded.data) { fprintf(stderr,"No complete frame\n");return 1; }
    printf("Shared source RTP timestamp=%u bytes=%zu\n",encoded.timestamp,encoded.size);
    GstCaps *caps=gst_caps_from_string("video/x-h264,stream-format=byte-stream,alignment=au");
    GstBuffer *buffer=gst_buffer_new_wrapped(encoded.data,encoded.size);
    GstSample *raw=process("appsrc name=input format=time ! h264parse ! avdec_h264 ! "
        "videoconvert ! video/x-raw,format=I420 ! appsink name=output sync=false",caps,buffer,NULL);
    gst_caps_unref(caps);gst_buffer_unref(buffer);
    if(!raw)return 1;
    GstSample *normal=process("appsrc name=input format=time ! videoconvert ! "
        "video/x-raw,format=RGB ! appsink name=output sync=false",
        gst_sample_get_caps(raw),gst_sample_get_buffer(raw),NULL);
    bool ok=normal && save_ppm(normal,argv[2],"original-native");
    if(normal)gst_sample_unref(normal);
    normal=process("appsrc name=input format=time ! videoscale add-borders=true ! videoconvert ! "
        "video/x-raw,format=RGB,width=800,height=480,pixel-aspect-ratio=1/1 ! "
        "appsink name=output sync=false",gst_sample_get_caps(raw),gst_sample_get_buffer(raw),NULL);
    ok=normal && save_ppm(normal,argv[2],"original-fit") && ok;
    if(normal)gst_sample_unref(normal);
    for(int kind=panini_only ? 1 : 0;kind<2;kind++)for(size_t j=0;j<count;j++) {
        PaniniConfig config={kind ? 1 : 0,180,fovs[j],0,0};
        gchar *name=g_strdup_printf("%s-fov%.0f",kind?"panini":"rectilinear",fovs[j]);
        GstSample *view=process("appsrc name=input format=time ! camerapanini name=warp ! "
            "videoconvert ! video/x-raw,format=RGB ! appsink name=output sync=false",
            gst_sample_get_caps(raw),gst_sample_get_buffer(raw),&config);
        ok=view && save_ppm(view,argv[2],name) && ok;
        if(view)gst_sample_unref(view);
        printf("%s\n",name);g_free(name);
    }
    gst_sample_unref(raw);
    return ok ? 0 : 1;
}
