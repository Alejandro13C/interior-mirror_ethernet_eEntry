#include "yolov5.hpp"
#include <opencv2/imgcodecs.hpp>
#include <chrono>
#include <iostream>
#include <sys/resource.h>
int main(int argc,char **argv) {
    if(argc<3 || argc>10){std::cerr<<"Usage: benchmark-yolov5 prefix image [size threads repeats rectangular(0|1) threshold all_classes(0|1) diagnostics(0|1)]\n";return 2;}
    try {
        int size=argc>3?std::stoi(argv[3]):640,threads=argc>4?std::stoi(argv[4]):2,repeats=argc>5?std::stoi(argv[5]):3;
        if(repeats<1 || repeats>1000)throw std::runtime_error("Invalid repeats");
        int rect=argc>6?std::stoi(argv[6]):0;if(rect!=0 && rect!=1)throw std::runtime_error("rectangular must be 0 or 1");
        float threshold=argc>7?std::stof(argv[7]):.15f;
        int all=argc>8?std::stoi(argv[8]):0;if(all!=0 && all!=1)throw std::runtime_error("all_classes must be 0 or 1");
        int diagnostics=argc>9?std::stoi(argv[9]):0;if(diagnostics!=0 && diagnostics!=1)throw std::runtime_error("diagnostics must be 0 or 1");
        cv::setNumThreads(1);cv::Mat image=cv::imread(argv[2]);YoloV5 detector(argv[1],size,threads,rect);
        detector.detect(image,threshold,!all);double ms=0;std::vector<Vehicle> found;YoloDiagnostics stats;
        for(int i=0;i<repeats;++i){auto start=std::chrono::steady_clock::now();found=detector.detect(image,threshold,!all,diagnostics?&stats:nullptr);ms+=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();}
        rusage usage{};getrusage(RUSAGE_SELF,&usage);
        std::cout<<"{\"size\":"<<size<<",\"threads\":"<<threads<<",\"rectangular\":"<<rect<<",\"threshold\":"<<threshold<<",\"all_classes\":"<<all<<",\"mean_ms\":"<<ms/repeats<<",\"rss_peak_mib\":"<<usage.ru_maxrss/1024.0<<",\"detections\":[";
        bool first=true;for(const auto &v:found){if(!first)std::cout<<",";first=false;std::cout<<"{\"box\":["<<v.box.x<<","<<v.box.y<<","<<v.box.width<<","<<v.box.height<<"],\"score\":"<<v.score<<",\"class\":"<<v.category<<"}";}
        std::cout<<"]";
        if(diagnostics)std::cout<<",\"diagnostics\":{\"anchors\":"<<stats.anchors
            <<",\"objectness_rejected\":"<<stats.objectness_rejected<<",\"class_rejected\":"<<stats.class_rejected
            <<",\"score_rejected\":"<<stats.score_rejected<<",\"peak_vehicle_score\":"<<stats.peak_vehicle_score
            <<",\"peak_vehicle_argmax_score\":"<<stats.peak_vehicle_argmax_score<<"}";
        std::cout<<"}\n";
    }catch(const std::exception &e){std::cerr<<e.what()<<"\n";return 1;}
}
