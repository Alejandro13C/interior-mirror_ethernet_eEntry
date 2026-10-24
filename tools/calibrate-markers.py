#!/usr/bin/env python3
"""Fit a provisional apparent-width profile and replay saved YOLOv5n detections.

Standard library only; previews need FFmpeg and make build/liboffline-tracking.so.
No physical distances can be recovered from these uncalibrated racing videos.
"""
import argparse
import ctypes as C
import hashlib
import html
import json
import math
from pathlib import Path
import resource
import shutil
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


class Vehicle(C.Structure):
    _fields_ = [(k, C.c_float) for k in ('x', 'y', 'w', 'h', 'confidence')] + [
        (k, C.c_uint32) for k in ('category', 'track', 'hits')] + [
        (k, C.c_float) for k in ('vx', 'vy')]


class Marker(C.Structure):
    _fields_ = [('height', C.c_int), ('r', C.c_ubyte), ('g', C.c_ubyte), ('b', C.c_ubyte)]


def quantile(values, q):
    values = sorted(values)
    at = (len(values)-1)*q
    lo = int(at)
    return values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(at-lo)


def vehicle(row, box):
    # Older confirmed records omit category. Recover it from the exact raw box;
    # never assume an unmatched track is a car.
    raw = next((r for r in row['raw'] if r['box'] == box['box']), None)
    category = box.get('class', raw['class'] if raw else 0)
    return Vehicle(*box['box'], box['score'], category, box['id'], 2,
                   box.get('vx', 0), box.get('vy', 0))


def eligible(v):
    return (v.category == 2 and all(math.isfinite(x) for x in (v.x, v.y, v.w, v.h))
            and v.x > 0 and v.y > 0 and v.w > 0 and v.h > 0
            and v.x+v.w < 1 and v.y+v.h < 1)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def draw(frame, v, m):
    cx = min(800-m.height//2-3, max(m.height//2+2, int((v.x+v.w/2)*800)))
    tip = min(479, max(m.height+2, int(v.y*480)-4))
    fill = bytes((m.r, m.g, m.b))
    for y in range(m.height+1):
        half = (m.height-y)//2
        for x in range(-half, half+1):
            offset = ((tip-m.height+y)*800+cx+x)*3
            frame[offset:offset+3] = b'\0\0\0' if y < 2 or abs(x) >= half-1 else fill


def replay(ffmpeg, source, rows, fps, start, seconds, target, style, far, near):
    first = round(start*fps)
    count = min(round(seconds*fps), len(rows)-first)
    start = first/fps
    decode = [ffmpeg, '-v', 'error', '-ss', str(start), '-i', str(source),
              '-vf', r'scale=w=trunc(iw*min(800/iw\,480/ih)):h=trunc(ih*min(800/iw\,480/ih)),pad=800:480:(ow-iw)/2:(oh-ih)/2',
              '-frames:v', str(count), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1']
    encode = [ffmpeg, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
              '-s', '800x480', '-r', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
              '-threads', '2', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
              '-movflags', '+faststart', str(target)]
    began = time.monotonic()
    cpu = time.process_time()
    render_ns = []
    colors = {'neutral': 0, 'green_side': 0, 'red_side': 0}
    with (target.with_suffix('.log')).open('w') as log:
        decoder = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=log)
        encoder = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=log)
        try:
            for index in range(first, first+count):
                data = decoder.stdout.read(800*480*3)
                if len(data) != 800*480*3:
                    raise RuntimeError(f'Unexpected EOF: {source}, frame {index}')
                frame = bytearray(data)
                tick = time.perf_counter_ns()
                for box in rows[index]['confirmed']:
                    v = vehicle(rows[index], box)
                    # Unknown classes remain neutral while retaining their marker.
                    if v.category == 0:
                        v.category = 7
                    m = Marker()
                    style(C.byref(v), far, near, C.byref(m))
                    colors['neutral' if m.b else 'green_side' if m.g >= m.r else 'red_side'] += 1
                    draw(frame, v, m)
                render_ns.append(time.perf_counter_ns()-tick)
                encoder.stdin.write(frame)
            encoder.stdin.close()
            if decoder.wait(timeout=20) or encoder.wait(timeout=30):
                raise RuntimeError(f'FFmpeg failed; see {target.with_suffix(".log")}')
        finally:
            decoder.stdout.close()
            for proc in (decoder, encoder):
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)
    # Decode the entire preview and check its exact frame count, not just the file's existence.
    result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(target), '-map', '0:v:0',
                             '-f', 'framemd5', '-'], check=True, capture_output=True, text=True)
    decoded = sum(bool(line) and not line.startswith('#') for line in result.stdout.splitlines())
    if decoded != count:
        raise RuntimeError(f'Preview frame count mismatch: {decoded} != {count}')
    elapsed = time.monotonic()-began
    return {'path': str(target), 'start_s': start, 'frames': count,
            'source_fps': fps, 'processing_fps_including_verification': count/elapsed,
            'python_cpu_s': time.process_time()-cpu,
            'overlay_mean_ms': statistics.mean(render_ns)/1e6,
            'overlay_p95_ms': quantile(render_ns, .95)/1e6,
            'marker_observations': colors, 'all_frames_decoded': True,
            'audio': False, 'note': 'Saved detections; no inference, no simulated Pi delivery delay.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True, help='folder with manifest.json and */yolov5n/detections.jsonl')
    p.add_argument('--output', type=Path, default=ROOT/'comparisons/marker-calibration')
    p.add_argument('--profile', type=Path, default=ROOT/'calibration/marker-widths.defaults')
    p.add_argument('--render', action='store_true')
    p.add_argument('--seconds', type=float, default=12)
    p.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    p.add_argument('--far-width', type=float, help='Override the provisional far endpoint (fraction of image width)')
    p.add_argument('--near-width', type=float, help='Override the provisional near endpoint (fraction of image width)')
    a = p.parse_args()
    if not math.isfinite(a.seconds) or a.seconds <= 0:
        p.error('--seconds must be positive')
    custom = a.far_width is not None or a.near_width is not None
    if custom and (a.far_width is None or a.near_width is None or
                   not 0 < a.far_width < a.near_width < 1):
        p.error('Supply both endpoints with 0 < --far-width < --near-width < 1')
    if a.render and not a.ffmpeg:
        candidates = list((ROOT/'build/video-python/imageio_ffmpeg/binaries').glob('ffmpeg-*'))
        if not candidates:
            p.error('Pass --ffmpeg /path/to/ffmpeg')
        a.ffmpeg = str(candidates[0])
    a.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((a.input/'manifest.json').read_text())
    folders = sorted(a.input.glob('*/yolov5n/detections.jsonl'))
    if len(folders) != len(manifest['sources']):
        raise ValueError('Expected one YOLOv5n detection file per source')
    clips = []
    for folder, source in zip(folders, manifest['sources']):
        rows = [json.loads(line) for line in folder.open()]
        fps = source['fps']
        widths = []
        observations = []
        for index, row in enumerate(rows):
            if row.get('frame_index', index) != index or abs(row['time_s']-index/fps) > .001:
                raise ValueError(f'Detection timeline mismatch: {folder}:{index}')
            current = [v.w for b in row['confirmed'] if eligible(v := vehicle(row, b))]
            observations.append(current)
            # Sample once a second to avoid weighting higher frame rates more heavily.
            if index == round(round(index/fps)*fps):
                widths.extend(current)
        if not widths:
            raise ValueError(f'No eligible cars: {folder}')
        low, high = quantile(widths, .1), quantile(widths, .9)
        clips.append({'clip': folder.parents[1].name, 'source': source['path'],
                      'source_sha256': source['sha256'], 'detections_sha256': digest(folder),
                      'sampled_boxes': len(widths), 'p10_width': low, 'p90_width': high,
                      'frames': len(rows), '_rows': rows, '_observations': observations, 'fps': fps})
    # Median of per-clip endpoints gives each camera equal weight.
    far = statistics.median(c['p10_width'] for c in clips)
    near = statistics.median(c['p90_width'] for c in clips)
    if custom:
        far, near = a.far_width, a.near_width
    if not 0 < far < near < 1:
        raise ValueError('Degenerate apparent-width profile')
    far, near = float(f'{far:.8f}'), float(f'{near:.8f}')
    if not 0 < far < near < 1:
        raise ValueError('Endpoints are too small or too close for profile precision')
    a.profile.parent.mkdir(parents=True, exist_ok=True)
    a.profile.write_text('# PROVISIONAL: empirical apparent widths from six racing videos.\n'
                         '# Width = car bounding-box pixels / full image width.\n'
                         '# Not metres, approach speed or collision risk. Refit after final mounting.\n'
                         f'CAMERA_MARKER_FAR_WIDTH={far:.8f}\nCAMERA_MARKER_NEAR_WIDTH={near:.8f}\n')
    lib = None
    if a.render:
        lib = C.CDLL(str(ROOT/'build/liboffline-tracking.so'))
        lib.offline_marker.argtypes = [C.POINTER(Vehicle), C.c_float, C.c_float, C.POINTER(Marker)]
        lib.offline_marker.restype = None
    for clip in clips:
        rows = clip.pop('_rows')
        observations = clip.pop('_observations')
        if a.render:
            source = Path(clip['source'])
            if digest(source) != clip['source_sha256']:
                raise ValueError(f'Source changed: {source}')
            # Select a preview spanning the widest observed size range, in one-second steps.
            windows = []
            step = max(1, round(clip['fps']))
            span = round(a.seconds*clip['fps'])
            for first in range(0, max(1, len(rows)-span+1), step):
                widths = [w for current in observations[first:first+span:step] for w in current]
                windows.append((max(widths)-min(widths) if len(widths)>1 else 0, first))
            first = max(windows)[1]
            clip['preview'] = replay(a.ffmpeg, source, rows, clip['fps'], first/clip['fps'],
                                     a.seconds, a.output/f'{clip["clip"]}.mp4', lib.offline_marker, far, near)
        print(clip['clip'], clip['sampled_boxes'], 'sampled car boxes', flush=True)
    report = {'status': 'provisional_apparent_size_only', 'far_width': far, 'near_width': near,
              'method': 'Manual endpoint override.' if custom else 'Median of per-clip 10th/90th width percentiles; confirmed unclipped cars sampled at 1 Hz.',
              'clips': clips, 'python_peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
              'limits': ['No ground-truth distances or physical calibration.',
                         'Different cameras/projections; no validated transfer to the installed camera/Panini.',
                         'Saved YOLOv5n640 detections, not a Pi320 detection quality or performance test.',
                         'Width changes can reflect orientation, box jitter or identity changes, not approach.',
                         'False and missed detections remain uncorrected; color does not improve detection.']}
    (a.output/'calibration.json').write_text(json.dumps(report, indent=2)+'\n')
    if a.render:
        cards = ''.join(f'<article><h2>{html.escape(c["clip"])}</h2>'
                        f'<p>Desde {c["preview"]["start_s"]:.1f} s del original · detecciones YOLOv5n640 guardadas</p>'
                        f'<video controls preload="metadata" src="{html.escape(c["clip"])}.mp4"></video></article>' for c in clips)
        (a.output/'index.html').write_text('<!doctype html><html lang="es"><meta charset="utf-8">'
            '<title>Prueba de triángulos</title><style>body{background:#15171b;color:#eee;font:16px system-ui;'
            'max-width:1200px;margin:32px auto;padding:0 20px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:24px}'
            'video{width:100%}h2{font-size:18px}</style><h1>Color y tamaño por ancho aparente</h1>'
            f'<p>Perfil provisional: verde/16 px hasta {far*100:.2f}% del ancho; '
            f'amarillo/26 px en {(far+near)*50:.2f}%; rojo/36 px desde {near*100:.2f}%.</p>'
            '<p>No son metros ni una medida de riesgo. Blanco: clase no comparable o caja recortada. '
            'Sin inferencia nueva, sin calibración de la cámara instalada/Panini y sin simulación de los tiempos de la Pi. '
            'Los errores del detector se conservan. Previsualizaciones sin audio.</p><main>'+cards+'</main></html>')
    print(f'Profile: {a.profile}; FAR={far:.8f}, NEAR={near:.8f}', flush=True)


if __name__ == '__main__':
    main()
