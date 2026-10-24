#ifndef REAR_CAMERA_NANODET_HPP
#define REAR_CAMERA_NANODET_HPP
#include <net.h>
#include <opencv2/imgproc.hpp>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>

// RangiLyu/NanoDet adapter: official Plus combined output and legacy split heads.
// References: RangiLyu/nanodet demo_ncnn and Tencent/ncnn examples/nanodet.cpp.
struct Vehicle {
    cv::Rect2f box; // normalized to the input image before padding
    float score;
    int category;
    unsigned track = 0;
};
class NanoDet {
    ncnn::Net net;
    bool plus=false;
    int target_size;
public:
    explicit NanoDet(const std::string &prefix,int size=320):target_size(size) {
        if(size!=256 && size!=320 && size!=416)throw std::runtime_error("Input size must be 256, 320 or 416");
        net.opt.num_threads=1;
        net.opt.use_vulkan_compute=false;
        net.opt.use_fp16_storage=false;
        net.opt.use_bf16_storage=false;
        if(net.load_param((prefix+".param").c_str()) || net.load_model((prefix+".bin").c_str()))
            throw std::runtime_error("Cannot load NanoDet NCNN model");
        if(net.input_names().size()!=1)throw std::runtime_error("Unexpected model inputs");
        plus=!std::strcmp(net.input_names()[0],"data");
        if(!plus && std::strcmp(net.input_names()[0],"input.1"))
            throw std::runtime_error("Unsupported NanoDet model");
    }
    std::vector<Vehicle> detect(const cv::Mat &image,float threshold=.4f) {
        if(image.empty() || image.type()!=CV_8UC3)throw std::runtime_error("Expected BGR image");
        cv::Mat packed=image.isContinuous()?image:image.clone();
        float scale=float(target_size)/std::max(image.cols,image.rows);
        int width=std::max(1,int(image.cols*scale));
        int height=std::max(1,int(image.rows*scale));
        ncnn::Mat resized=ncnn::Mat::from_pixels_resize(packed.data,ncnn::Mat::PIXEL_BGR,
            image.cols,image.rows,width,height);
        int align=plus?64:32;
        int padded_w=(width+align-1)/align*align,padded_h=(height+align-1)/align*align;
        int left=(padded_w-width)/2,top=(padded_h-height)/2;
        ncnn::Mat input;
        ncnn::copy_make_border(resized,input,top,padded_h-height-top,left,padded_w-width-left,
                              ncnn::BORDER_CONSTANT,0.f);
        const float mean[]={103.53f,116.28f,123.675f};
        const float norm[]={1.f/57.375f,1.f/57.12f,1.f/58.395f};
        input.substract_mean_normalize(mean,norm);
        auto extractor=net.create_extractor();
        if(extractor.input(plus?"data":"input.1",input))throw std::runtime_error("NCNN input failed");
        const char *names[][2]={{"792","795"},{"814","817"},{"836","839"}};
        const int strides[]={8,16,32,64};
        std::vector<Vehicle> candidates;
        ncnn::Mat combined;
        if(plus && (extractor.extract("output",combined) || combined.w!=112))
            throw std::runtime_error("Unexpected NanoDet-Plus output");
        int offset=0;
        for(int level=0;level<(plus?4:3);++level) {
            ncnn::Mat cls,reg;
            if(!plus && (extractor.extract(names[level][0],cls) || extractor.extract(names[level][1],reg)))
                throw std::runtime_error("NCNN output failed");
            int stride=strides[level],grid=(padded_w+stride-1)/stride;
            int rows=grid*((padded_h+stride-1)/stride);
            if((plus && offset+rows>combined.h) || (!plus &&
                (cls.w!=80 || reg.w!=32 || cls.h!=reg.h || cls.h!=rows)))
                throw std::runtime_error("Unexpected NCNN tensor shape");
            for(int row=0;row<rows;++row) {
                const float *p=plus?combined.row(offset+row):cls.row(row);
                int category=int(std::max_element(p,p+80)-p);
                if((category!=2 && category!=3 && category!=5 && category!=7) || p[category]<threshold)continue;
                const float *r=plus?p+80:reg.row(row);float distances[4];
                for(int edge=0;edge<4;++edge) {
                    const float *logits=r+8*edge;
                    float peak=*std::max_element(logits,logits+8),sum=0,weighted=0;
                    for(int bin=0;bin<8;++bin) {
                        float v=std::exp(logits[bin]-peak);sum+=v;weighted+=bin*v;
                    }
                    distances[edge]=weighted/sum*stride;
                }
                // Plus uses integer grid centres; the legacy split-head model uses +0.5.
                float center=plus?0.f:.5f;
                float cx=(row%grid+center)*stride,cy=(row/grid+center)*stride;
                float x1=std::clamp((cx-distances[0]-left)/scale/image.cols,0.f,1.f);
                float y1=std::clamp((cy-distances[1]-top)/scale/image.rows,0.f,1.f);
                float x2=std::clamp((cx+distances[2]-left)/scale/image.cols,0.f,1.f);
                float y2=std::clamp((cy+distances[3]-top)/scale/image.rows,0.f,1.f);
                if(x2>x1 && y2>y1)candidates.push_back({{x1,y1,x2-x1,y2-y1},p[category],category,0});
            }
            offset+=rows;
        }
        if(plus && offset!=combined.h)throw std::runtime_error("Unexpected NanoDet-Plus grid");
        std::sort(candidates.begin(),candidates.end(),[](const auto &a,const auto &b){return a.score>b.score;});
        std::vector<Vehicle> result;
        for(const auto &v:candidates) {
            bool keep=true;
            for(const auto &prior:result) {
                float inter=(v.box & prior.box).area();
                if(inter/(v.box.area()+prior.box.area()-inter)>.5f){keep=false;break;}
            }
            if(keep)result.push_back(v);
            if(result.size()==16)break;
        }
        return result;
    }
};
#endif
