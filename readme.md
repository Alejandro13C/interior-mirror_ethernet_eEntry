# Rear Camera

Raspberry Pi rear-view video prototype for a racing car. A native C receiver reconstructs the Ficosa camera stream and uses GStreamer hardware decoding and display scaling. Once configured, video starts at boot without a PC or Wi-Fi.

## Hardware

- **Computer:** Raspberry Pi 3 Model A+, Raspberry Pi OS Lite (32-bit).
- **Display:** 7-inch IPS DSI touchscreen, 800 × 480. Exact manufacturer/model unconfirmed; see [display references](documentation_pantalla/referencias.md).
- **Camera:** Ficosa fisheye, H.264, 1344 × 968, approximately 25 fps.
- **USB Ethernet:** ASIX AX88772B, using `eth0` on the Pi.

The receiver supports the observed camera encapsulation: VLAN 2, IPv6 multicast `ff14::2:d`, UDP port 42800, RTP payload type 127, with Annex B SPS/PPS/IDR data grouped by RTP timestamp. Camera-specific filtering is defined in [src](src/).

## Build and install

On the Pi:

```bash
sudo apt install build-essential pkg-config kbd \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav
make
make test
```

Edit the user, group and project paths in [rear-camera.service](systemd/rear-camera.service) to match your installation, then:

```bash
sudo cp systemd/rear-camera.service /etc/systemd/system/
sudo cp systemd/rear-camera.defaults /etc/default/rear-camera
sudo systemctl daemon-reload
sudo systemctl enable --now rear-camera
```

The service supplies the network capabilities needed for capture. Run one hardware video player at a time.

## Configuration and checks

Current Pi settings in `/etc/default/rear-camera`:

```ini
CAMERA_INTERFACE=eth0
CAMERA_MIRROR=0
CAMERA_ROTATION=0
CAMERA_PANINI=1
CAMERA_COMPRESSION=1
CAMERA_LENS_FOV=180
CAMERA_VIEW_FOV=130
CAMERA_PITCH=0
```

Mirror accepts `0` or `1`; rotation accepts `0` or `180`. Experimental [Panini settings](calibration/panini.md) use an approximate equidistant lens model and a selectable field of view. Restart after changes:

```bash
sudo systemctl restart rear-camera
journalctl -u rear-camera -n 20 --no-pager
python3 tools/check-pi.py --seconds 300
```

The diagnostic requires Python 3 and checks service stability and resources. Image proportions are preserved. Mirror and rotation use the display hardware.

## Status

Boot startup, hardware video, mirror/rotation and recovery after a process failure have been tested on the Pi. The dedicated black background on `tty7` is deployed and visually confirmed. The Pi Wi-Fi profile has autoconnect enabled with unlimited retries; recovery and reboot checks passed.

Experimental Panini at 130° and optimized YOLOv5n are active on the Pi 3A+. Lens calibration, detection accuracy, automotive power and track validation remain pending. See the [plan](plan.md), [projection model](calibration/panini.md) and [test results](validation.md). Reported timing excludes camera encoding and panel refresh.

## Optional vehicle detection

A separate C++/NCNN worker runs YOLOv5n with two CPU threads and rectangular letterboxing (320×192 for the 416×250 landscape input). It removes unused square padding without reducing the resized scene. At most one image is in flight, up to three per second, preventing a backlog. Video uses CPU cores 0–1; inference uses 2–3. The Pi 3A+ profile uses less detail than the 640-pixel PC comparison. Video never waits for inference. White triangles require two matching observations within 750 ms, expire after 750 ms and use bounded motion prediction to compensate analysis delay. An empty observation retains existing marks only until their original expiry. There is no distance measurement or collision warning. This is an unvalidated prototype.

```bash
sudo apt install cmake libopencv-imgproc-dev libopencv-imgcodecs-dev
python3 tools/fetch-detector.py
python3 tools/build-ncnn.py
make build/vehicle-detector
```

Adapt the user, group and project paths in `systemd/rear-camera-detector.service`, then:

```bash
sudo cp systemd/rear-camera-detector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rear-camera-detector
```

Export the studied YOLOv5n ONNX on the PC using `tools/export-yolov5-ncnn.py`, then copy `models/yolov5n.param` and `models/yolov5n.bin` to the Pi. The exporter preserves the original weights. Current detector settings in `/etc/default/rear-camera` (`CAMERA_DETECTION=1`, detector service enabled):

```ini
CAMERA_DETECTOR_MODEL=yolov5n
CAMERA_DETECTOR_SIZE=320
CAMERA_DETECTOR_THREADS=2
CAMERA_DETECTOR_RECT=1
CAMERA_DETECTOR_THRESHOLD=0.15
CAMERA_DETECTOR_START_THRESHOLD=0.25
```

Rebuild after source changes. To restore the NanoDet fallback, set model `nanodet`, size `256`, threads `1`, and both thresholds to `0.4`.

Set `CAMERA_DETECTION=1` in `/etc/default/rear-camera` and restart `rear-camera`. Set it to `0` to remove detection from video; the base installation defaults to off. The worker restarts independently after failure. CPU use is capped at two cores; a 256 MiB address-space limit also applies when the kernel lacks memory cgroups. The 192 MiB cgroup memory cap requires that controller.

`make test-video` checks the Panini filter and marker rendering/expiration. `make test-capture CAPTURE=/path/to/reference.pcap` additionally checks the original known capture fixture.

`make test-detector IMAGE=/path/to/street.png` checks both car boxes in the [OpenCV reference image](https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/dnn/street.png). This optional NanoDet fallback test uses the official 416-trained weights at reduced input size; it does not test YOLOv5n quality.

## PC video diagnostic

Using Python 3.12:

```bash
python3.12 -m venv build/video-env
build/video-env/bin/pip install -r tools/video-requirements.txt
make build/liboffline-tracking.so
build/video-env/bin/python tools/analyze-video.py /path/to/video.mp4 comparisons/video-test --size 256
```

Outputs an annotated H.264 video with audio, snapshots and PC resource metrics. Uses the same weights and C tracker; defaults to 3 Hz and a simulated 200 ms result delay. Video is fitted to 800×480 without Panini. `--size 416` compares higher-resolution inference; its PC speed does not predict Raspberry Pi performance.

PC racing diagnostic, processing every frame at 640 pixels (the Pi uses a separate 320-pixel profile):

```bash
python3 tools/fetch-video-model.py yolov5n
build/video-env/bin/python tools/analyze-video.py /path/to/video.mp4 comparisons/video-yolo --detector yolov5n --hz 30 --threshold .15 --start-threshold .25 --group-vehicles --hold-ms 100 --delay-ms 0 --display-age-ms 0 --threads 4
```

This is an offline PC quality test with four OpenCV CPU threads, not the Pi timing profile. Weak observations maintain existing tracks; a track requires a score of .25 at least once and two observations before display. Car/bus/truck labels share association; missed observations retain markers briefly, bounded by the original 750 ms capture TTL. `tools/evaluate-video.py` compares models on approximate labelled frames in `tests/fixtures/texana_boxes.json`, keeping threshold tuning and check frames separate.

Detector quality is evaluated on real racing videos; the selected 320-pixel Pi profile still needs a separate quality evaluation. See the [CPU/model study](detector-study.md) for measured timings, RAM, limitations and triangle-only comparisons.

[Full-video NanoDet416 evaluation](full-video-results.md): all 38,850 frames from six racing clips, with triangle-only outputs and reviewed failure examples.
