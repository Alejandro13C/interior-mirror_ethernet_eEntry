#include "yolov5.hpp"
#include <opencv2/imgcodecs.hpp>
#include <chrono>
#include <iostream>
#include <sys/resource.h>
int main(int argc,char **argv) {
    if(argc<3 || argc>7){std::cerr<<"Usage: benchmark-yolov5 prefix image [size threads repeats rectangular(0|1)]\n";return 2;}
    try {
        int size=argc>3?std::stoi(argv[3]):640,threads=argc>4?std::stoi(argv[4]):2,repeats=argc>5?std::stoi(argv[5]):3;
        if(repeats<1 || repeats>1000)throw std::runtime_error("Invalid repeats");
        int rect=argc>6?std::stoi(argv[6]):0;if(rect!=0 && rect!=1)throw std::runtime_error("rectangular must be 0 or 1");
        cv::setNumThreads(1);cv::Mat image=cv::imread(argv[2]);YoloV5 detector(argv[1],size,threads,rect);
        detector.detect(image);double ms=0;std::vector<Vehicle> found;std::vector<double> each;
        for(int i=0;i<repeats;++i){auto start=std::chrono::steady_clock::now();found=detector.detect(image);each.push_back(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count());ms+=each.back();}
        std::sort(each.begin(),each.end());
        auto rank=[&](double q){return each[std::min(each.size()-1,size_t(q*(each.size()-1)+.5))];};
        rusage usage{};getrusage(RUSAGE_SELF,&usage);
        std::cout<<"{\"size\":"<<size<<",\"threads\":"<<threads<<",\"rectangular\":"<<rect<<",\"repeats\":"<<repeats
            <<",\"mean_ms\":"<<ms/repeats<<",\"p50_ms\":"<<rank(.5)<<",\"p95_ms\":"<<rank(.95)
            <<",\"raw_peak\":"<<detector.last_peak<<",\"rss_peak_mib\":"<<usage.ru_maxrss/1024.0<<",\"detections\":[";
        bool first=true;for(const auto &v:found){if(!first)std::cout<<",";first=false;std::cout<<"{\"box\":["<<v.box.x<<","<<v.box.y<<","<<v.box.width<<","<<v.box.height<<"],\"score\":"<<v.score<<",\"class\":"<<v.category<<"}";}
        std::cout<<"]}\n";
    }catch(const std::exception &e){std::cerr<<e.what()<<"\n";return 1;}
}
