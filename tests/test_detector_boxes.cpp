// Optional real-image regression; image is not bundled.
// https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/dnn/street.png
#include "nanodet.hpp"
#include <opencv2/imgcodecs.hpp>
#include <iostream>
int main(int argc,char **argv) {
    if(argc!=2){std::cerr<<"Usage: test-detector-boxes street.png\n";return 2;}
    try {
        cv::setNumThreads(1);
        cv::Mat image=cv::imread(argv[1]);
        if(image.cols!=512 || image.rows!=512)throw std::runtime_error("Expected the 512x512 street reference");
        NanoDet detector("models/nanodet_plus_m",256);
        auto found=detector.detect(image);
        // Approximate boxes annotated on the source image: approaching and receding car.
        const cv::Rect2f truth[]={{334/512.f,236/512.f,82/512.f,100/512.f},
                                  {231/512.f,240/512.f,23/512.f,26/512.f}};
        for(const auto &box:truth) {
            float best=0;
            for(const auto &v:found)if(v.category==2) {
                float intersection=(box & v.box).area();
                best=std::max(best,intersection/(box.area()+v.box.area()-intersection));
            }
            std::cout<<"Reference car box IoU="<<best<<"\n";
            if(best<.5f)throw std::runtime_error("Missed or misplaced reference car");
        }
    } catch(const std::exception &e){std::cerr<<e.what()<<"\n";return 1;}
}
