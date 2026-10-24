CC ?= cc
CXX ?= c++
NCNN ?= build/ncnn-install
CV_FLAGS = -I/usr/include/opencv4
CV_LIBS = -lopencv_imgcodecs -lopencv_imgproc -lopencv_core
NCNN_FLAGS = -I$(NCNN)/include/ncnn
NCNN_LIBS = $(NCNN)/lib/libncnn.a -fopenmp -pthread
CFLAGS ?= -O2 -g
WARN = -std=c11 -Wall -Wextra -Wpedantic
GST = gstreamer-1.0 gstreamer-app-1.0 gstreamer-video-1.0
CAPTURE ?= captures/camera.pcap

.PHONY: all test test-capture test-video test-detector clean
all: build/rear-camera

build:
	mkdir -p $@

build/rear-camera: src/rear_camera.c src/camera_rtp.c src/camera_rtp.h src/panini.c src/panini.h src/gst_panini.c src/gst_panini.h src/gst_vehicles.c src/gst_vehicles.h src/vehicle_protocol.h src/vehicle_marker.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc src/rear_camera.c src/camera_rtp.c src/panini.c src/gst_panini.c src/gst_vehicles.c -o $@ $$(pkg-config --libs $(GST)) -lm

build/vehicle_tracking.o: src/vehicle_tracking.c src/vehicle_tracking.h src/vehicle_protocol.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc -c src/vehicle_tracking.c -o $@

build/liboffline-tracking.so: tools/offline_tracking.c src/vehicle_tracking.c src/vehicle_tracking.h src/vehicle_protocol.h src/vehicle_marker.h | build
	$(CC) $(CFLAGS) $(WARN) -fPIC -shared -Isrc tools/offline_tracking.c src/vehicle_tracking.c -o $@ -lm

build/vehicle-detector: src/vehicle_detector.cpp src/nanodet.hpp src/yolov5.hpp src/vehicle_protocol.h build/vehicle_tracking.o $(NCNN)/lib/libncnn.a | build
	$(CXX) -O3 -std=c++17 -Wall -Wextra $(CV_FLAGS) $(NCNN_FLAGS) -Isrc src/vehicle_detector.cpp build/vehicle_tracking.o -o $@ $(NCNN_LIBS) $(CV_LIBS) -lm

build/benchmark-detector: tools/benchmark-detector.cpp src/nanodet.hpp $(NCNN)/lib/libncnn.a | build
	$(CXX) -O3 -std=c++17 -Wall -Wextra $(CV_FLAGS) $(NCNN_FLAGS) -Isrc tools/benchmark-detector.cpp -o $@ $(NCNN_LIBS) $(CV_LIBS) -lm

build/benchmark-yolov5: tools/benchmark-yolov5.cpp src/yolov5.hpp src/nanodet.hpp $(NCNN)/lib/libncnn.a | build
	$(CXX) -O3 -std=c++17 -Wall -Wextra $(CV_FLAGS) $(NCNN_FLAGS) -Isrc tools/benchmark-yolov5.cpp -o $@ $(NCNN_LIBS) $(CV_LIBS) -lm

build/test-detector-boxes: tests/test_detector_boxes.cpp src/nanodet.hpp $(NCNN)/lib/libncnn.a | build
	$(CXX) -O3 -std=c++17 -Wall -Wextra $(CV_FLAGS) $(NCNN_FLAGS) -Isrc tests/test_detector_boxes.cpp -o $@ $(NCNN_LIBS) $(CV_LIBS) -lm

test-detector: build/test-detector-boxes
	./build/test-detector-boxes "$(IMAGE)"

build/render-views: tools/render-views.c src/camera_rtp.c src/camera_rtp.h src/gst_panini.c src/gst_panini.h src/panini.c src/panini.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc tools/render-views.c src/camera_rtp.c src/gst_panini.c src/panini.c -o $@ $$(pkg-config --libs $(GST)) -lm

build/benchmark-panini: tools/benchmark-panini.c src/gst_panini.c src/gst_panini.h src/panini.c src/panini.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc tools/benchmark-panini.c src/gst_panini.c src/panini.c -o $@ $$(pkg-config --libs $(GST)) -lm

build/test-rtp: tests/test_rtp.c src/camera_rtp.c src/camera_rtp.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_rtp.c src/camera_rtp.c -o $@

build/test-panini: tests/test_panini.c src/panini.c src/panini.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_panini.c src/panini.c -o $@ -lm

build/test-video-panini: tests/test_video_panini.c src/gst_panini.c src/gst_panini.h src/panini.c src/panini.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc tests/test_video_panini.c src/gst_panini.c src/panini.c -o $@ $$(pkg-config --libs $(GST)) -lm

test-capture: build/test-rtp
	./build/test-rtp "$(CAPTURE)"

test-video: build/test-video-panini build/test-vehicle-overlay
	./build/test-video-panini
	./build/test-vehicle-overlay
	./build/test-vehicle-overlay --colored

build/test-vehicles: tests/test_vehicles.c src/vehicle_tracking.c src/vehicle_tracking.h src/vehicle_protocol.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_vehicles.c src/vehicle_tracking.c -o $@ -lm

build/test-vehicle-overlay: tests/test_vehicle_overlay.c src/gst_vehicles.c src/gst_vehicles.h src/vehicle_protocol.h src/vehicle_marker.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc tests/test_vehicle_overlay.c src/gst_vehicles.c -o $@ $$(pkg-config --libs $(GST)) -lm

build/test-vehicle-marker: tests/test_vehicle_marker.c src/vehicle_marker.h src/vehicle_protocol.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_vehicle_marker.c -o $@ -lm

test: build/test-rtp build/test-panini build/test-vehicles build/test-vehicle-marker
	./build/test-rtp
	./build/test-panini
	./build/test-vehicles
	./build/test-vehicle-marker

clean:
	rm -f build/test-vehicle-marker
	rm -f build/rear-camera build/test-rtp build/test-panini build/test-video-panini build/render-views build/benchmark-panini build/vehicle-detector build/benchmark-detector build/benchmark-yolov5 build/vehicle_tracking.o build/test-vehicles build/test-vehicle-overlay build/test-detector-boxes build/liboffline-tracking.so
