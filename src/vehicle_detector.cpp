#include "nanodet.hpp"
#include "yolov5.hpp"
#include "vehicle_tracking.h"
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <poll.h>
#include <csignal>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <ctime>
#include <cerrno>
#include <unordered_map>

static volatile sig_atomic_t running=1;
static void stop(int){running=0;}
static uint64_t now_ns(){timespec t{};clock_gettime(CLOCK_MONOTONIC,&t);return uint64_t(t.tv_sec)*1000000000+t.tv_nsec;}
static cv::Mat decode_frame(const CameraAIFrame &frame) {
    const int w=CAMERA_AI_WIDTH,h=CAMERA_AI_HEIGHT;
    cv::Mat bgr(h,w,CV_8UC3);
    const uint8_t *yy=frame.pixels,*uu=yy+w*h,*vv=uu+w*h/4;
    bool full=frame.flags&CAMERA_AI_FULL_RANGE,hd=frame.flags&CAMERA_AI_709;
    for(int y=0;y<h;++y) {
        cv::Vec3b *row=bgr.ptr<cv::Vec3b>(y);
        for(int x=0;x<w;++x) {
            int lum=yy[y*w+x]-(full?0:16),u=uu[(y/2)*(w/2)+x/2]-128,v=vv[(y/2)*(w/2)+x/2]-128;
            int a=full?256:298;
            int r=(a*lum+(hd?(full?403:459):(full?359:409))*v+128)>>8;
            int g=(a*lum-(hd?(full?48:55):(full?88:100))*u-(hd?(full?120:136):(full?183:208))*v+128)>>8;
            int b=(a*lum+(hd?(full?475:541):(full?454:516))*u+128)>>8;
            row[x]=cv::Vec3b(cv::saturate_cast<uchar>(b),cv::saturate_cast<uchar>(g),cv::saturate_cast<uchar>(r));
        }
    }
    return bgr;
}
int main(int argc,char **argv) {
    const char *kind=std::getenv("CAMERA_DETECTOR_MODEL");if(!kind)kind="nanodet";
    bool use_yolo=!std::strcmp(kind,"yolov5n");
    if(!use_yolo && std::strcmp(kind,"nanodet")){std::cerr<<"CAMERA_DETECTOR_MODEL must be nanodet or yolov5n\n";return 2;}
    const char *model=use_yolo?"models/yolov5n":"models/nanodet_plus_m";
    if(argc==3 && !std::strcmp(argv[1],"--model"))model=argv[2];
    else if(argc!=1){std::cerr<<"Usage: vehicle-detector [--model prefix]\n";return 2;}
    int input_size=use_yolo?640:256,threads=use_yolo?2:1;
    bool rectangular=false;
    if(const char *value=std::getenv("CAMERA_DETECTOR_RECT")) {
        if(std::strcmp(value,"0") && std::strcmp(value,"1")){std::cerr<<"CAMERA_DETECTOR_RECT must be 0 or 1\n";return 2;}
        rectangular=!std::strcmp(value,"1");
    }
    if(const char *value=std::getenv("CAMERA_DETECTOR_SIZE")) {
        if(!std::strcmp(value,"256"))input_size=256;
        else if(!std::strcmp(value,"320"))input_size=320;
        else if(!std::strcmp(value,"416"))input_size=416;
        else if(use_yolo && !std::strcmp(value,"640"))input_size=640;
        else {std::cerr<<"Invalid CAMERA_DETECTOR_SIZE\n";return 2;}
    }
    if(const char *value=std::getenv("CAMERA_DETECTOR_THREADS")) {
        if(std::strlen(value)!=1 || value[0]<'1' || value[0]>'4'){std::cerr<<"CAMERA_DETECTOR_THREADS must be 1..4\n";return 2;}
        threads=value[0]-'0';
        if(!use_yolo && threads!=1){std::cerr<<"NanoDet worker uses one thread\n";return 2;}
    }
    auto threshold_from_env=[](const char *name,float fallback) {
        const char *value=std::getenv(name);if(!value)return fallback;
        char *end=nullptr;float result=std::strtof(value,&end);
        if(!end || *end || !std::isfinite(result) || result<=0 || result>=1)
            throw std::runtime_error(std::string("Invalid ")+name);
        return result;
    };
    float threshold,start_threshold;
    try {
        threshold=threshold_from_env("CAMERA_DETECTOR_THRESHOLD",use_yolo?.15f:.4f);
        start_threshold=threshold_from_env("CAMERA_DETECTOR_START_THRESHOLD",use_yolo?.25f:.4f);
        if(start_threshold<threshold)throw std::runtime_error("Start threshold must be >= detection threshold");
    }catch(const std::exception &e){std::cerr<<e.what()<<"\n";return 2;}
    signal(SIGINT,stop);signal(SIGTERM,stop);
    const char *dir=std::getenv("CAMERA_DETECTOR_DIR");if(!dir)dir="/run/rear-camera-detector";
    std::string path=std::string(dir)+"/worker.sock";
    sockaddr_un address{};address.sun_family=AF_UNIX;
    if(path.size()>=sizeof address.sun_path){std::cerr<<"IPC path too long\n";return 1;}
    std::memcpy(address.sun_path,path.c_str(),path.size()+1);
    int fd=socket(AF_UNIX,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    if(fd<0){perror("socket");return 1;}
    int size=256*1024;setsockopt(fd,SOL_SOCKET,SO_RCVBUF,&size,sizeof size);
    unlink(path.c_str());
    if(bind(fd,reinterpret_cast<sockaddr *>(&address),sizeof address)<0){perror("bind");close(fd);return 1;}
    int result=0;
    try {
        cv::setNumThreads(1);
        std::unique_ptr<NanoDet> nano;
        std::unique_ptr<YoloV5> yolo;
        if(use_yolo)yolo=std::make_unique<YoloV5>(model,input_size,threads,rectangular);
        else nano=std::make_unique<NanoDet>(model,input_size);
        auto incoming=std::make_unique<CameraAIFrame>();auto latest=std::make_unique<CameraAIFrame>();
        CameraTracks tracks{};
        struct Peak {float score;uint64_t ns;};
        std::unordered_map<uint32_t,Peak> peaks;
        uint64_t processed=0,expired=0,replaced=0,found=0,sum_ns=0,last_log=now_ns(),previous=0;
        uint64_t late_results=0,confirmed_frames=0,result_age_ns=0,max_result_age_ns=0;
        std::cout<<"Vehicle detector ready: NCNN "<<(use_yolo?"YOLOv5n":"NanoDet-Plus")<<", input="<<input_size
            <<", threads="<<threads<<", rectangular="<<rectangular<<", threshold="<<threshold<<", birth="<<start_threshold<<", confirm=2, TTL=750ms\n"<<std::flush;
        while(running) {
            pollfd pfd{fd,POLLIN,0};
            int ready=poll(&pfd,1,250);
            if(ready<0){if(errno==EINTR)continue;throw std::runtime_error("IPC poll failed");}
            if(!ready)continue;
            bool have=false;sockaddr_un peer{};socklen_t peer_length=sizeof peer;
            for(int attempts=0;attempts<32;++attempts) {
                sockaddr_un from{};socklen_t from_length=sizeof from;
                ssize_t n=recvfrom(fd,incoming.get(),sizeof *incoming,MSG_DONTWAIT|MSG_TRUNC,
                                   reinterpret_cast<sockaddr *>(&from),&from_length);
                if(n<0)break;
                uint64_t now=now_ns();
                if(n!=ssize_t(sizeof *incoming) || incoming->magic!=CAMERA_AI_MAGIC ||
                    incoming->version!=CAMERA_AI_VERSION || incoming->width!=CAMERA_AI_WIDTH ||
                    incoming->height!=CAMERA_AI_HEIGHT || incoming->captured_ns>now ||
                    now-incoming->captured_ns>300000000 || incoming->captured_ns<=previous ||
                    from.sun_path[0]==0 || from_length>sizeof from || from_length<=sizeof(sa_family_t)){expired++;continue;}
                if(have)replaced++;
                *latest=*incoming;peer=from;peer_length=from_length;have=true;
            }
            if(!have)continue;
            previous=latest->captured_ns;
            auto bgr=decode_frame(*latest);
            uint64_t started=now_ns();auto detections=use_yolo?yolo->detect(bgr,threshold):nano->detect(bgr,threshold);uint64_t done=now_ns();
            processed++;sum_ns+=done-started;
            CameraAIResults reply{};reply.magic=CAMERA_AI_MAGIC;reply.version=CAMERA_AI_VERSION;
            reply.sequence=latest->sequence;reply.captured_ns=latest->captured_ns;reply.completed_ns=done;
            if(done-latest->captured_ns<=CAMERA_AI_TTL_NS) {
                uint64_t age=done-latest->captured_ns;
                result_age_ns+=age;max_result_age_ns=std::max(max_result_age_ns,age);
                for(const auto &v:detections) {
                    if(reply.count==CAMERA_AI_MAX)break;
                    CameraVehicle &out=reply.vehicles[reply.count++];
                    out.x=v.box.x;out.y=v.box.y;out.w=v.box.width;out.h=v.box.height;
                    out.confidence=v.score;out.category=uint32_t(v.category);
                }
                uint32_t categories[CAMERA_AI_MAX]{};
                for(uint32_t i=0;i<reply.count;++i) {
                    categories[i]=reply.vehicles[i].category;
                    if(use_yolo && (categories[i]==5 || categories[i]==7))reply.vehicles[i].category=2;
                }
                camera_track(&tracks,reply.vehicles,reply.count,reply.captured_ns);
                for(auto it=peaks.begin();it!=peaks.end();) {
                    if(reply.captured_ns-it->second.ns>CAMERA_AI_TTL_NS)it=peaks.erase(it);
                    else ++it;
                }
                uint32_t confirmed=0;
                for(uint32_t i=0;i<reply.count;++i) {
                    CameraVehicle &vehicle=reply.vehicles[i];vehicle.category=categories[i];
                    auto &peak=peaks[vehicle.track];peak.score=std::max(peak.score,vehicle.confidence);peak.ns=reply.captured_ns;
                    if(vehicle.hits>=2 && peak.score>=start_threshold)reply.vehicles[confirmed++]=vehicle;
                }
                reply.count=confirmed;
                found+=reply.count;
                if(reply.count)confirmed_frames++;
                sendto(fd,&reply,sizeof reply,MSG_DONTWAIT|MSG_NOSIGNAL,
                       reinterpret_cast<sockaddr *>(&peer),peer_length);
            } else {expired++;late_results++;}
            if(done-last_log>=10000000000ull) {
                std::cout<<"detector: processed="<<processed<<" mean_inference_ms="<<(sum_ns/1e6/processed)
                    <<" vehicles="<<found<<" expired="<<expired<<" replaced="<<replaced
                    <<" late_results="<<late_results<<" confirmed_frames="<<confirmed_frames
                    <<" mean_result_age_ms="<<(processed>late_results?result_age_ns/1e6/(processed-late_results):0)
                    <<" max_result_age_ms="<<(max_result_age_ns/1e6)<<"\n"<<std::flush;
                last_log=done;
            }
        }
    }catch(const std::exception &e){std::cerr<<e.what()<<"\n";result=1;}
    close(fd);unlink(path.c_str());return result;
}
