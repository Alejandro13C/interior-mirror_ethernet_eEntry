# Rear Camera

Raspberry Pi rear-view video prototype for a racing car. A native C receiver reconstructs the camera stream and uses GStreamer hardware decoding and display scaling. Once configured, video starts at boot without a PC or Wi-Fi.

## Hardware

- **Computer:** Raspberry Pi 3 Model A+, Raspberry Pi OS Lite (32-bit).
- **Display:** 7-inch IPS DSI touchscreen, 800 × 480. Exact manufacturer/model unconfirmed; see [display references](documentation_pantalla/referencias.md).
- **Camera:** Fisheye, H.264, 1344 × 968, approximately 25 fps.
- **USB Ethernet:** ASIX AX88772B, using `eth0` on the Pi.

The receiver supports the observed camera encapsulation: VLAN 2, IPv6 multicast `ff14::2:d`, UDP port 42800, RTP payload type 127, with Annex B SPS/PPS/IDR data grouped by RTP timestamp. Packets are accepted only from source MAC `44:3c:88:24:70:22` (kernel BPF filter in `src/rear_camera.c` and parser in `src/camera_rtp.c`; change both for another camera); up to two VLAN tags with any VID are accepted. The camera sends every frame as SPS+PPS+IDR without bitstream_restriction; the receiver rewrites the SPS so the hardware decoder outputs each frame at once (`CAMERA_SPS_RESTRICT=0` disables it).

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
sudo install -D -o root -g root -m 0755 display-console.sh /usr/local/lib/rear-camera/display-console.sh
sudo cp systemd/rear-camera.defaults /etc/default/rear-camera
sudo cp systemd/90-rear-camera-net.rules /etc/udev/rules.d/ && sudo udevadm trigger --sysname-match=eth0
sudo systemctl daemon-reload
sudo systemctl enable --now rear-camera
```

The service supplies the network capabilities needed for capture. Run one hardware video player at a time.

### Read-only microSD

The Pi runs with the root file system in a RAM overlay (`overlayroot=tmpfs`) and `/boot/firmware` mounted read-only, so cutting the power cannot corrupt the card. Every change, including the journal, is lost at the next boot, and `tools/deploy-pi.sh` refuses to run. To change anything:

```bash
sudo raspi-config nonint disable_overlayfs && sudo reboot
# deploy, edit /etc/default/rear-camera, ... (/boot/firmware/config.txt: sudo mount -o remount,rw /boot/firmware)
sudo raspi-config nonint enable_overlayfs && sudo reboot
```

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

Experimental Panini at 130° and optimized YOLOv5n are active on the Pi 3A+. Lens calibration, detection accuracy, automotive power and track validation remain pending. See the [plan](plan.md), [projection model](calibration/panini.md) and the [audit correction plan with current measurements](auditoria/07-plan-correcciones.md). Reported timing excludes camera encoding and panel refresh.

The provisional apparent-width color/size profile was deployed on 24 October 2026. Native rendering tests passed on the Pi; live measurements are in `comparisons/marker-calibration/pi-deployment/`. Since the RPS rule (24 October) video runs at the camera's 25 fps without damaged frames while detection runs; real-car accuracy and physical distance calibration remain pending.

## Monitoring and safety

- **Frozen picture:** if no fresh frame reaches the display for `CAMERA_STALE_MS` (default 300 ms), the picture and its triangles are replaced by a black "SIN VIDEO" frame, even while buffers keep arriving. If frames keep arriving but none is shown for `CAMERA_STALE_RESTART_MS` (5000 ms), the process exits and systemd restarts it. A silent camera also trips the 3 s GStreamer watchdog. `kill -USR1` toggles discarding camera packets, to test this without unplugging the cable (`tools/fault-injection.sh`).
- **Detection unavailable:** an amber square in the top-right corner appears after `CAMERA_AI_STALE_MS` (1000 ms) without a worker reply, so "no triangle" is not mistaken for "no vehicle".
- **Counters:** every `CAMERA_STATS_MS` (10000 ms) the journal gets fps, losses per stage, frame age p50/p95/p99 including dropped frames, the largest gap between shown frames, mean age after assembly, decoding and Panini, and CPU; the detector logs its best raw vehicle score (`raw_peak_*`).
- **Network receive:** [90-rear-camera-net.rules](systemd/90-rear-camera-net.rules) brings `eth0` up as soon as the adapter appears and steers its packet processing to cores 2–3 (RPS). Without RPS the USB adapter loses packets while the detector runs (about 8 damaged frames per 10 s); with it, none. Keep the rule's interface equal to `CAMERA_INTERFACE`.
- **Startup:** the units skip the default ordering after `basic.target` (`DefaultDependencies=no`), so video starts once the display exists instead of after cloud-init; `run-pi.sh` skips GStreamer's plugin rescan, which every boot otherwise triggers (new `/dev` nodes), while its cache is current. First frame 24.5 → 16.8 s after kernel start. The Panini map is cached in `/var/cache/rear-camera` (`CacheDirectory=`), so a restart skips its ~1.4 s rebuild.
- **Detection region and rate:** `CAMERA_AI_BAND=y0,y1` (fractions of the picture height) sends only that band to the detector, 512 px wide, with `CAMERA_DETECTOR_SIZE=512`; it is a concession on vertical field, off by default. `CAMERA_AI_PERIOD_MS` (333) is the minimum time between analysed images.
- **Display pixels:** `CAMERA_DISPLAY_PAR=16/15` makes the Panini output match the 7-inch panel's non-square pixels, so the whole 800×480 picture is shown with correct proportions (kmssink otherwise cropped it to 750 columns).
- **Marker size:** `CAMERA_MARKER_SIZE` (20 px, 12–80) sets the triangle height; the colour profile scales with it and the dark outline thickens on larger markers.
- **Other concessions, off by default:** `CAMERA_AI_HOLD_MS` (750, max 1500) keeps marks longer, showing older positions; `CAMERA_DETECTOR_FAST_CONFIRM=0.6` shows a detection scoring at least 0.6 after one observation instead of two (earlier triangles, more false marks).
- **Detector model:** `CAMERA_DETECTOR_PREFIX` selects another NCNN model prefix (for example the native-Swish `.param` from `tools/fold-swish.py`).

The unit runs `display-console.sh` as root from a root-owned copy in `/usr/local/lib/rear-camera/` (see Build and install). Invalid settings exit with status 78, which systemd does not retry. A/B measurements on the Pi: `sudo python3 tools/measure-ab.py --variant A=<binary> --variant B=<binary> --output <dir>`.

## Optional vehicle detection

A separate C++/NCNN worker runs YOLOv5n with two CPU threads and rectangular letterboxing (320×192 for the 416×250 landscape input). It removes unused square padding without reducing the resized scene. At most one image is in flight, up to three per second, preventing a backlog. Video uses CPU cores 0–1; inference uses 2–3. The Pi 3A+ profile uses less detail than the 640-pixel PC comparison. Video never waits for inference. White triangles require two matching observations within 750 ms and a track score of at least 0.25 once, expire after 750 ms and use bounded motion prediction to compensate analysis delay. Association compares each detection with the box predicted from the track's velocity (at most one box of displacement) and accepts IoU > 0.25, or else centres closer than 0.6 mean box sizes with sizes within 2×; pairs are assigned globally by cost, IoU before centre distance. An empty observation retains existing marks only until their original expiry. There is no distance measurement or collision warning. This is an unvalidated prototype.

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

Set `CAMERA_DETECTION=1` in `/etc/default/rear-camera` and restart `rear-camera`. Set it to `0` to remove detection from video (do so if the worker is not installed: otherwise the overlay shows the detection-unavailable indicator). `systemd/rear-camera.defaults` mirrors the deployed configuration: Panini 130°, YOLOv5n with two threads on the band y=0.333–0.75 at 512×128, and fast confirmation at 0.6. Without the band and fast-confirm lines the code defaults to the whole picture at 320×192 with two observations. The worker restarts independently after failure. CPU use is capped at two cores; a 256 MiB address-space limit also applies when the kernel lacks memory cgroups. The 192 MiB cgroup memory cap requires that controller.

`make test-video` checks the Panini filter and marker rendering/expiration. `make test-capture CAPTURE=/path/to/reference.pcap` additionally checks the original known capture fixture.

Experimental marker styling uses `CAMERA_MARKER_FAR_WIDTH` and `CAMERA_MARKER_NEAR_WIDTH`: car-box width / full image width, requiring `0 < FAR < NEAR < 1`. Apparent width continuously changes green/16 px → yellow/26 px → red/36 px; cars, buses and trucks are coloured; motorcycles and boxes cut by the left or right edge remain white (a top/bottom cut, frequent with the band, keeps the width). Unset endpoints preserve white 20-pixel triangles. This is an apparent-size cue, not metres, approach speed or collision risk. [Provisional values](calibration/marker-widths.defaults) come from 1,956 confirmed car boxes sampled at 1 Hz in six racing videos, using the median of their per-video 10th/90th percentiles. They have no measured distances or validated transfer to the installed camera/Panini. After final mounting, replace both values and check different cars, lanes and orientations.

After rebuilding and installing the updated video service, load the provisional profile on the Pi:

```bash
sudo cp calibration/marker-widths.defaults /etc/default/rear-camera-markers
sudo systemctl restart rear-camera
```

Edit those two values in `/etc/default/rear-camera-markers` and restart to recalibrate; remove that file (and any marker variables in `/etc/default/rear-camera`) to restore white markers. To refit, record detections from the installed camera and run `make build/liboffline-tracking.so` then `python3 tools/calibrate-markers.py --input <detections folder> --render` (FFmpeg via `--ffmpeg`; `--far-width` and `--near-width` override the fit). It writes verified previews, an `index.html` and JSON measurements. These are offline previews with saved detections, not Pi320 accuracy or live timing tests. `make test-video` checks neutral and colored drawing, confirmation and expiration using synthetic data.

`make test-detector IMAGE=/path/to/street.png` checks both car boxes in the [OpenCV reference image](https://raw.githubusercontent.com/opencv/opencv_extra/4.x/testdata/dnn/street.png). This optional NanoDet fallback test uses the official 416-trained weights at reduced input size; it does not test YOLOv5n quality.

## Offline evaluation

`tools/evaluate-pi-profile.py` reproduces the Pi detection chain frame by frame (I420 thumbnail, 320×192 input, same NCNN model) on the annotated reference frames in `comparisons/new-models_all-frames_6-videos/manual-reference/`, with Wilson and per-clip bootstrap intervals. Results: `comparisons/pi-profile-evaluation/results.json`. It needs a Python environment with `ncnn`, `opencv-python-headless`, `numpy`, `onnx` and `onnxruntime`; it does not simulate the 2.78 Hz cadence, tracking or TTL.

Closed PC studies (engine/model comparisons, full-video renders) and their large outputs were removed on 24 October 2026; they are kept in the backup archive `rear_view-respaldo-2026-10-24.zip`.
