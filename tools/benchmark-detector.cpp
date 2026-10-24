#include "nanodet.hpp"
#include <opencv2/imgcodecs.hpp>
#include <chrono>
#include <iostream>
#include <sys/resource.h>
int main(int argc,char **argv) {
    if(argc<3 || argc>6) {std::cerr<<"Usage: benchmark-detector model-prefix image [output.png [threshold [size]]]\n";return 2;}
    try {
        float threshold=.4f;
        if(argc>=5) {
            std::string value=argv[4];size_t used=0;
            threshold=std::stof(value,&used);
            if(used!=value.size() || !std::isfinite(threshold) || threshold<=0 || threshold>1)
                throw std::runtime_error("Threshold must be in (0,1]");
        }
        cv::setNumThreads(1);
        NanoDet detector(argv[1],argc==6?std::stoi(argv[5]):320);
        cv::Mat image=cv::imread(argv[2]);
        if(image.empty())throw std::runtime_error("Cannot load image");
        std::vector<Vehicle> detections;
        for(int run=0;run<3;++run) {
            auto start=std::chrono::steady_clock::now();detections=detector.detect(image,threshold);
            double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
            std::cout<<"run="<<run<<" inference_ms="<<ms<<" vehicles="<<detections.size()<<"\n";
        }
        for(auto &d:detections) {
            cv::Rect rect(int(d.box.x*image.cols),int(d.box.y*image.rows),int(d.box.width*image.cols),int(d.box.height*image.rows));
            std::cout<<"class="<<d.category<<" confidence="<<d.score<<" box="<<rect<<"\n";
            cv::rectangle(image,rect,cv::Scalar(255,255,255),1);
            int cx=rect.x+rect.width/2,tip=std::max(20,rect.y);
            std::vector<cv::Point> triangle={{cx,tip},{cx-9,tip-17},{cx+9,tip-17}};
            cv::fillConvexPoly(image,triangle,cv::Scalar(255,255,255));
            cv::polylines(image,triangle,true,cv::Scalar(0,0,0),2);
        }
        if(argc>=4 && !cv::imwrite(argv[3],image))throw std::runtime_error("Cannot write output");
        rusage usage{};getrusage(RUSAGE_SELF,&usage);std::cout<<"max_rss_kb="<<usage.ru_maxrss<<"\n";
    } catch(const std::exception &e) {std::cerr<<e.what()<<"\n";return 1;}
}
