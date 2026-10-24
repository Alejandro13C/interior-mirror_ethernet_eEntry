#ifndef REAR_CAMERA_YOLOV5_HPP
#define REAR_CAMERA_YOLOV5_HPP
#include "nanodet.hpp"

// Original YOLOv5 v7.0 heads; decode matches models/yolo.py Detect.forward.
class YoloV5 {
    ncnn::Net net;
    int size;
    bool rectangular;
    static float sigmoid(float x) {return 1.f/(1.f+std::exp(-x));}
public:
    // Best vehicle score of the last image, even below the threshold: tells
    // "nothing resembles a vehicle" apart from "almost passed the threshold".
    float last_peak=0;
    explicit YoloV5(const std::string &prefix,int input=640,int threads=2,bool rect=false):size(input),rectangular(rect) {
        if(input<256 || input>640 || input%32 || threads<1 || threads>4)
            throw std::runtime_error("YOLOv5 input must be 256..640, multiple of 32; threads 1..4");
        net.opt.num_threads=threads;
        net.opt.use_vulkan_compute=false;
        net.opt.use_fp16_storage=false;
        net.opt.use_fp16_arithmetic=false;
        net.opt.use_bf16_storage=false;
        if(net.load_param((prefix+".param").c_str()) || net.load_model((prefix+".bin").c_str()))
            throw std::runtime_error("Cannot load YOLOv5n NCNN model");
        if(net.input_names().size()!=1 || std::strcmp(net.input_names()[0],"in0"))
            throw std::runtime_error("Unsupported YOLOv5 input tensor");
    }
    std::vector<Vehicle> detect(const cv::Mat &image,float threshold=.15f) {
        if(image.empty() || image.type()!=CV_8UC3 || threshold<=0 || threshold>=1)
            throw std::runtime_error("Invalid YOLOv5 image/threshold");
        float scale=float(size)/std::max(image.cols,image.rows);
        int w=std::max(1,int(std::lround(image.cols*scale))),h=std::max(1,int(std::lround(image.rows*scale)));
        int input_w=rectangular?(w+31)/32*32:size;
        int input_h=rectangular?(h+31)/32*32:size;
        int left=(input_w-w)/2,top=(input_h-h)/2;
        cv::Mat resized,padded;
        cv::resize(image,resized,cv::Size(w,h),0,0,cv::INTER_LINEAR);
        cv::copyMakeBorder(resized,padded,top,input_h-h-top,left,input_w-w-left,cv::BORDER_CONSTANT,cv::Scalar(114,114,114));
        ncnn::Mat input=ncnn::Mat::from_pixels(padded.data,ncnn::Mat::PIXEL_BGR2RGB,input_w,input_h);
        const float norm[]={1.f/255,1.f/255,1.f/255};input.substract_mean_normalize(nullptr,norm);
        auto ex=net.create_extractor();
        if(ex.input("in0",input))throw std::runtime_error("YOLOv5 input failed");
        static const float anchors[3][6]={{10,13,16,30,33,23},{30,61,62,45,59,119},{116,90,156,198,373,326}};
        static const char *names[]={"out0","out1","out2"};
        std::vector<Vehicle> candidates;
        last_peak=0;
        for(int level=0;level<3;++level) {
            int stride=8<<level,grid_w=input_w/stride,grid_h=input_h/stride;ncnn::Mat head;
            if(ex.extract(names[level],head) || head.dims!=3 || head.c!=255 || head.w!=grid_w || head.h!=grid_h)
                throw std::runtime_error("Unexpected YOLOv5 raw head shape");
            for(int a=0;a<3;++a)for(int y=0;y<grid_h;++y)for(int x=0;x<grid_w;++x) {
                auto at=[&](int channel){return head.channel(a*85+channel).row(y)[x];};
                float objectness=sigmoid(at(4));
                if(objectness>last_peak) {
                    float best=std::max({at(7),at(8),at(10),at(12)}); // car, motorcycle, bus, truck
                    last_peak=std::max(last_peak,objectness*sigmoid(best));
                }
                if(objectness<threshold)continue;
                int category=0;float peak=at(5);
                for(int c=1;c<80;++c){float value=at(c+5);if(value>peak){category=c;peak=value;}}
                if(category!=2 && category!=3 && category!=5 && category!=7)continue;
                float score=objectness*sigmoid(peak);if(score<threshold)continue;
                float cx=(sigmoid(at(0))*2-.5f+x)*stride,cy=(sigmoid(at(1))*2-.5f+y)*stride;
                float bw=std::pow(sigmoid(at(2))*2,2)*anchors[level][a*2];
                float bh=std::pow(sigmoid(at(3))*2,2)*anchors[level][a*2+1];
                float x1=std::clamp((cx-bw*.5f-left)/scale/image.cols,0.f,1.f);
                float y1=std::clamp((cy-bh*.5f-top)/scale/image.rows,0.f,1.f);
                float x2=std::clamp((cx+bw*.5f-left)/scale/image.cols,0.f,1.f);
                float y2=std::clamp((cy+bh*.5f-top)/scale/image.rows,0.f,1.f);
                if(x2>x1 && y2>y1)candidates.push_back({{x1,y1,x2-x1,y2-y1},score,category,0});
            }
        }
        std::stable_sort(candidates.begin(),candidates.end(),[](const auto &a,const auto &b){return a.score>b.score;});
        std::vector<Vehicle> result;
        for(const auto &v:candidates) {
            bool keep=true;
            for(const auto &prior:result) {
                float inter=(v.box & prior.box).area();
                if(inter/(v.box.area()+prior.box.area()-inter)>.45f){keep=false;break;}
            }
            if(keep)result.push_back(v);
            if(result.size()==16)break;
        }
        return result;
    }
};
#endif
