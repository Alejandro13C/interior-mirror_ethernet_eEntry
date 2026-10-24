CC ?= cc
CXX ?= c++
NCNN ?= build/ncnn-install
CV_FLAGS = -I/usr/include/opencv4
CV_LIBS = -lopencv_imgcodecs -lopencv_imgproc -lopencv_core
NCNN_FLAGS = -I$(NCNN)/include/ncnn
NCNN_LIBS = $(NCNN)/lib/libncnn.a -fopenmp -pthread
# Raspbian's GCC targets ARMv6/VFPv2 by default; the Pi 3 is a Cortex-A53 with NEON.
MACHINE := $(shell $(CC) -dumpmachine 2>/dev/null | cut -d- -f1)
ARCH_FLAGS ?= $(if $(filter arm armv7l,$(MACHINE)),-mcpu=cortex-a53 -mfpu=neon-fp-armv8,$(if $(filter aarch64,$(MACHINE)),-mcpu=cortex-a53))
CFLAGS ?= -O2 -g $(ARCH_FLAGS)
WARN = -std=c11 -Wall -Wextra -Wpedantic
GST = gstreamer-1.0 gstreamer-app-1.0 gstreamer-video-1.0
CAPTURE ?= captures/camera.pcap

.PHONY: all test test-asan test-capture test-video test-detector test-ipc clean
all: build/rear-camera

build:
	mkdir -p $@

build/rear-camera: src/rear_camera.c src/camera_rtp.c src/camera_rtp.h src/panini.c src/panini.h src/gst_panini.c src/gst_panini.h src/gst_vehicles.c src/gst_vehicles.h src/vehicle_protocol.h src/vehicle_marker.h src/stale_frame.c src/stale_frame.h src/h264_sps.c src/h264_sps.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc src/rear_camera.c src/camera_rtp.c src/panini.c src/gst_panini.c src/gst_vehicles.c src/stale_frame.c src/h264_sps.c -o $@ $$(pkg-config --libs $(GST)) -lm

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

# Real worker over IPC: a racing frame (tests/fixtures/race-positive.i420, 416x250 I420
# thumbnail of a reference frame with 3 detectable cars) must give confirmed, stable tracks.
test-ipc: build/vehicle-detector
	python3 tests/test_detector_ipc.py tests/fixtures/race-positive.i420 --model yolov5n --flags 0

test-detector: build/test-detector-boxes
	./build/test-detector-boxes "$(IMAGE)"

build/render-views: tools/render-views.c src/camera_rtp.c src/camera_rtp.h src/gst_panini.c src/gst_panini.h src/panini.c src/panini.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc tools/render-views.c src/camera_rtp.c src/gst_panini.c src/panini.c -o $@ $$(pkg-config --libs $(GST)) -lm

build/decoder-latency: tools/decoder-latency.c | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) tools/decoder-latency.c -o $@ $$(pkg-config --libs $(GST))

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

test-video: build/test-video-panini build/test-vehicle-overlay build/test-stale-frame
	./build/test-video-panini
	./build/test-vehicle-overlay
	./build/test-vehicle-overlay --colored
	./build/test-vehicle-overlay --invalid-markers
	./build/test-vehicle-overlay --band
	./build/test-stale-frame

build/test-stale-frame: tests/test_stale_frame.c src/stale_frame.c src/stale_frame.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc tests/test_stale_frame.c src/stale_frame.c -o $@ $$(pkg-config --libs $(GST))

build/test-vehicles: tests/test_vehicles.c src/vehicle_tracking.c src/vehicle_tracking.h src/vehicle_protocol.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_vehicles.c src/vehicle_tracking.c -o $@ -lm

build/test-vehicle-overlay: tests/test_vehicle_overlay.c src/gst_vehicles.c src/gst_vehicles.h src/vehicle_protocol.h src/vehicle_marker.h | build
	$(CC) $(CFLAGS) $(WARN) $$(pkg-config --cflags $(GST)) -Isrc tests/test_vehicle_overlay.c src/gst_vehicles.c -o $@ $$(pkg-config --libs $(GST)) -lm

build/test-vehicle-marker: tests/test_vehicle_marker.c src/vehicle_marker.h src/vehicle_protocol.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_vehicle_marker.c -o $@ -lm

build/test-h264-sps: tests/test_h264_sps.c src/h264_sps.c src/h264_sps.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_h264_sps.c src/h264_sps.c -o $@

test: build/test-rtp build/test-panini build/test-vehicles build/test-vehicle-marker build/test-h264-sps
	./build/test-rtp
	./build/test-panini
	./build/test-vehicles
	./build/test-vehicle-marker
	./build/test-h264-sps

# Unit tests again with AddressSanitizer and UBSan (separate binaries; armhf libasan needs
# libatomic, and Raspberry Pi OS preloads libarmmem before the ASan runtime).
test-asan:
	$(MAKE) CFLAGS="-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer $(ARCH_FLAGS)" \
		build/test-rtp-asan build/test-panini-asan build/test-vehicles-asan build/test-vehicle-marker-asan build/test-h264-sps-asan
	ASAN_OPTIONS=verify_asan_link_order=0 UBSAN_OPTIONS=halt_on_error=1 ./build/test-rtp-asan
	ASAN_OPTIONS=verify_asan_link_order=0 UBSAN_OPTIONS=halt_on_error=1 ./build/test-panini-asan
	ASAN_OPTIONS=verify_asan_link_order=0 UBSAN_OPTIONS=halt_on_error=1 ./build/test-vehicles-asan
	ASAN_OPTIONS=verify_asan_link_order=0 UBSAN_OPTIONS=halt_on_error=1 ./build/test-vehicle-marker-asan
	ASAN_OPTIONS=verify_asan_link_order=0 UBSAN_OPTIONS=halt_on_error=1 ./build/test-h264-sps-asan

build/test-h264-sps-asan: tests/test_h264_sps.c src/h264_sps.c src/h264_sps.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_h264_sps.c src/h264_sps.c -o $@ -latomic

build/test-rtp-asan: tests/test_rtp.c src/camera_rtp.c src/camera_rtp.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_rtp.c src/camera_rtp.c -o $@ -latomic

build/test-panini-asan: tests/test_panini.c src/panini.c src/panini.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_panini.c src/panini.c -o $@ -lm -latomic

build/test-vehicles-asan: tests/test_vehicles.c src/vehicle_tracking.c src/vehicle_tracking.h src/vehicle_protocol.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_vehicles.c src/vehicle_tracking.c -o $@ -lm -latomic

build/test-vehicle-marker-asan: tests/test_vehicle_marker.c src/vehicle_marker.h src/vehicle_protocol.h | build
	$(CC) $(CFLAGS) $(WARN) -Isrc tests/test_vehicle_marker.c -o $@ -lm -latomic

clean:
	rm -f build/*-asan build/decoder-latency build/test-h264-sps
	rm -f build/test-vehicle-marker build/test-stale-frame
	rm -f build/rear-camera build/test-rtp build/test-panini build/test-video-panini build/render-views build/benchmark-panini build/vehicle-detector build/benchmark-detector build/benchmark-yolov5 build/vehicle_tracking.o build/test-vehicles build/test-vehicle-overlay build/test-detector-boxes build/liboffline-tracking.so
