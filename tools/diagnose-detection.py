#!/usr/bin/env python3
"""Diagnóstico de detección por etapas sobre capturas reales de la cámara (sin la Pi).

Reproduce en el PC la cadena de la Pi desde el H.264 de la cámara:
  decodificación -> Panini 800x480 I420 (port de src/panini.c panini_source + remap entero
  de src/gst_panini.c) -> miniatura NN 416x250 (src/gst_vehicles.c) -> decode_frame
  (src/vehicle_detector.cpp) -> YoloV5::detect 320x192 (src/yolov5.hpp, mismo modelo NCNN)
y, para cada etapa y variante (resolución, filtro, recorte, franja, original sin Panini, espejo,
brillo/contraste), mide en la zona del objeto (--roi, en el lienzo 800x480):
  - puntuación cruda objectness x clase por clase COCO (vehículos y las clases más altas),
  - la detección que saldría del detector a 0,15 / 0,25 (argmax de clase vehículo, NMS),
y simula el seguidor (src/vehicle_tracking.c + regla de alta de vehicle_detector.cpp) a la cadencia
de la Pi sobre todos los fotogramas.

Entrada: .pcap (como tools/extract-h264.py), .h264, PNG de la cámara o carpeta de PNG.
Luma exacta del decodificador FFmpeg (cv2, CONVERT_RGB=0); croma reconstruida del BGR (BT.601).

Uso (venv con ncnn, numpy y opencv):
  python tools/diagnose-detection.py captura.pcap --out DIR --roi 246,152,510,330 [--images 0,100]
(--roi: caja del objeto en el lienzo Panini 800x480; mírala en DIR/NNNN_canvas800x480.png con --images)
"""
import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault('OPENCV_LOG_LEVEL', 'ERROR')
import cv2  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PI = load_tool('evaluate-pi-profile')   # rgb_to_i420, export_nn, export_area, decode_frame, letterbox, decode, NcnnYolo
CANVAS_W, CANVAS_H, AI_W, AI_H = PI.CANVAS_W, PI.CANVAS_H, PI.AI_W, PI.AI_H
VEHICLES = (2, 3, 5, 7)
COCO = ('person bicycle car motorcycle airplane bus train truck boat traffic_light fire_hydrant stop_sign parking_meter '
        'bench bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag tie suitcase frisbee skis '
        'snowboard sports_ball kite baseball_bat baseball_glove skateboard surfboard tennis_racket bottle wine_glass cup fork '
        'knife spoon bowl banana apple sandwich orange broccoli carrot hot_dog pizza donut cake chair couch potted_plant bed '
        'dining_table toilet tv laptop mouse remote keyboard cell_phone microwave oven toaster sink refrigerator book clock '
        'vase scissors teddy_bear hair_drier toothbrush').split()
assert len(COCO) == 80


# ---------------------------------------------------------------- Panini (src/panini.c, src/gst_panini.c)
def panini_source(cfg, sw, sh, dw, dh, x, y):
    """Vectorizado de panini_source(): devuelve sx, sy, válido (mismas fórmulas, doble precisión)."""
    d, lens_fov, view_fov, pitch = cfg
    rad = math.pi / 180
    half = view_fov * rad / 2
    xmax = (d + 1) * math.sin(half) / (d + math.cos(half))
    scale = dw / (2 * xmax)
    u = (np.asarray(x, np.float64) - (dw - 1) / 2.0) / scale
    a = d + 1
    q = u * d / np.hypot(a, u)
    ok = np.abs(q) <= 1
    lon = np.arctan2(u, a) + np.arcsin(np.clip(q, -1, 1))
    s = (d + 1) / (d + np.cos(lon))
    lat = np.arctan(((dh - 1) / 2.0 - np.asarray(y, np.float64)) / (scale * s))
    rx = np.cos(lat) * np.sin(lon)
    ry, rz = np.sin(lat), np.cos(lat) * np.cos(lon)
    p = pitch * rad
    py = math.cos(p) * ry + math.sin(p) * rz
    pz = -math.sin(p) * ry + math.cos(p) * rz
    radial = np.hypot(rx, py)
    theta = np.arctan2(radial, pz)
    f = sw / (lens_fov * rad)
    factor = np.where(radial > 1e-12, f * theta / np.where(radial > 1e-12, radial, 1), f)
    sx = (sw - 1) / 2.0 + factor * rx
    sy = (sh - 1) / 2.0 - factor * py
    ok &= np.isfinite(sx) & np.isfinite(sy) & (sx >= 0) & (sx <= sw - 1) & (sy >= 0) & (sy <= sh - 1)
    return sx, sy, ok


def plane_map(cfg, width, height, plane):
    """map_entry()/compute_plane() de gst_panini.c para un plano: offset, fx, fy (uint8 truncados)."""
    w, h = (400, 240) if plane else (800, 480)
    sw, sh = ((width + 1) // 2, (height + 1) // 2) if plane else (width, height)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    sx, sy, valid = panini_source(cfg, width, height, 800, 480, 2 * xx + .5 if plane else xx, 2 * yy + .5 if plane else yy)
    if plane:
        sx, sy = (sx - .5) / 2, (sy - .5) / 2
    sx, sy = np.nan_to_num(sx, nan=-1.), np.nan_to_num(sy, nan=-1.)
    valid &= (sx >= 0) & (sy >= 0) & (sx < sw - 1) & (sy < sh - 1)
    ix, iy = np.where(valid, np.floor(sx), 0).astype(np.int64), np.where(valid, np.floor(sy), 0).astype(np.int64)
    offset = np.where(valid, iy * sw + ix, sw * sh)
    fx = np.where(valid, np.floor((sx - ix) * 256), 0).astype(np.int64)
    fy = np.where(valid, np.floor((sy - iy) * 256), 0).astype(np.int64)
    return dict(offset=offset, fx=fx, fy=fy, sw=sw, sh=sh, valid=valid)


def remap(plane_data, m, pad):
    """remap_rows(): bilineal entero con tres productos; fuera de la lente, relleno negro (16/128)."""
    sw, sh = m['sw'], m['sh']
    cached = np.full(sw * sh + 2 * sw + 2, pad, np.int64)
    cached[:sw * sh] = plane_data[:sh, :sw].ravel()
    o, fx, fy = m['offset'], m['fx'], m['fy']
    p0, p1, p2, p3 = cached[o], cached[o + 1], cached[o + sw], cached[o + sw + 1]
    top = (p0 << 8) + (p1 - p0) * fx
    bottom = (p2 << 8) + (p3 - p2) * fx
    return (((top << 8) + (bottom - top) * fy + 32768) >> 16).astype(np.uint8)


class Panini:
    def __init__(self, cfg, width, height, full_range=False):
        self.cfg, self.width, self.height = cfg, width, height
        self.maps = [plane_map(cfg, width, height, p) for p in range(3)]
        self.luma_black = 0 if full_range else 16

    def __call__(self, planes):
        return [remap(planes[p], self.maps[p], 128 if p else self.luma_black) for p in range(3)]


def check_panini(cfg, width=1344, height=968):
    """Comprobaciones del port: puntos calculados a mano y escalar (math) frente a vectorizado."""
    # Centro: (399.5, 239.5) -> centro óptico ((W-1)/2, (H-1)/2).
    sx, sy, ok = panini_source(cfg, width, height, 800, 480, 399.5, 239.5)
    assert ok and abs(sx - (width - 1) / 2) < 1e-9 and abs(sy - (height - 1) / 2) < 1e-9
    # Borde izquierdo, fila central, a mano (d=1, 130°, lente 180°): u=-1,272548, q=-0,536822,
    # lon=atan(-0,636274)+asin(-0,536822)=-1,133331 rad; sx=671,5-(1344/pi)*1,133331=186,65; sy=483,5.
    d, lens, view, pitch = cfg
    if (d, lens, view, pitch) == (1, 180, 130, 0) and (width, height) == (1344, 968):
        sx, sy, ok = panini_source(cfg, width, height, 800, 480, 0., 239.5)
        assert ok and abs(sx - 186.65) < .01 and abs(sy - 483.5) < 1e-9, (sx, sy)
    # Referencia escalar con math (copia literal del C) en puntos aleatorios.
    def scalar(x, y):
        rad = math.pi / 180; half = view * rad / 2
        xmax = (d + 1) * math.sin(half) / (d + math.cos(half)); scale = 800 / (2 * xmax)
        u = (x - 399.5) / scale; a = d + 1; q = u * d / math.hypot(a, u)
        if abs(q) > 1: return None
        lon = math.atan2(u, a) + math.asin(q); s = (d + 1) / (d + math.cos(lon))
        lat = math.atan((239.5 - y) / (scale * s))
        rx = math.cos(lat) * math.sin(lon); ry = math.sin(lat); rz = math.cos(lat) * math.cos(lon)
        p = pitch * rad; py = math.cos(p) * ry + math.sin(p) * rz; pz = -math.sin(p) * ry + math.cos(p) * rz
        radial = math.hypot(rx, py); theta = math.atan2(radial, pz); f = width / (lens * rad)
        factor = f * theta / radial if radial > 1e-12 else f
        return (width - 1) / 2 + factor * rx, (height - 1) / 2 - factor * py
    rng = np.random.default_rng(1)
    pts = rng.uniform([0, 0], [799, 479], (500, 2))
    vx, vy, _ = panini_source(cfg, width, height, 800, 480, pts[:, 0], pts[:, 1])
    worst = max(max(abs(r[0] - a), abs(r[1] - b)) for (x, y), a, b in zip(pts, vx, vy) if (r := scalar(x, y)))
    assert worst < 1e-9, worst
    return worst


# ---------------------------------------------------------------- entrada
def read_frames(path, every=1, limit=0, work=None):
    """Genera (índice, bgr, (y, u, v)) con la luma exacta del decodificador cuando la entrada es vídeo."""
    path = Path(path)
    if path.suffix.lower() == '.pcap':
        ext = load_tool('extract-h264')
        stats = dict.fromkeys(('packets', 'complete', 'damaged', 'sequence_errors', 'duplicates', 'late_packets',
                               'resyncs', 'oversized', 'unfinished_tail'), 0)
        out = Path(work or path.parent) / (path.stem + '.h264')
        with open(out, 'wb') as f:
            for frame in ext.frames(str(path), stats):
                f.write(frame)
        print('pcap:', json.dumps(stats), flush=True)
        path = out
    if path.is_dir() or path.suffix.lower() == '.png':
        files = sorted(path.glob('*.png')) if path.is_dir() else [path]
        for i, f in enumerate(files):
            bgr = cv2.imread(str(f), cv2.IMREAD_COLOR)
            h, w = bgr.shape[:2]
            bgr = bgr[:h - h % 2, :w - w % 2]
            yield i, bgr, PI.rgb_to_i420(bgr[:, :, ::-1]), f.name
        return
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    raw = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    raw.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    i = n = 0
    while limit <= 0 or n < limit:
        ok, bgr = cap.read()
        ok2, luma = raw.read()
        if not ok or not ok2:
            break
        if i % every == 0:
            y, u, v = PI.rgb_to_i420(bgr[:, :, ::-1])
            if luma.shape == y.shape:
                y = luma  # luma exacta del decodificador
            yield i, bgr, (y, u, v), f'frame_{i:04d}'
            n += 1
        i += 1


# ---------------------------------------------------------------- análisis de cabezas
def anchor_table(heads, geo):
    """Todas las anclas: caja normalizada en la imagen que recibe detect(), objectness y logits de clase."""
    boxes, obj, cls = [], [], []
    for level, head in enumerate(heads):
        stride = 8 << level
        gh, gw = head.shape[1:]
        hh = head.reshape(3, 85, gh, gw)
        s = PI.sigmoid(hh[:, :4])
        gy, gx = np.mgrid[0:gh, 0:gw].astype(np.float32)
        cx = (s[:, 0] * 2 - .5 + gx) * stride
        cy = (s[:, 1] * 2 - .5 + gy) * stride
        aw = PI.ANCHORS[level, 0::2][:, None, None]; ah = PI.ANCHORS[level, 1::2][:, None, None]
        bw = (s[:, 2] * 2) ** 2 * aw; bh = (s[:, 3] * 2) ** 2 * ah
        sc, cols, rows = geo['scale'], geo['cols'], geo['rows']
        x1 = np.clip((cx - bw / 2 - geo['left']) / sc / cols, 0, 1); x2 = np.clip((cx + bw / 2 - geo['left']) / sc / cols, 0, 1)
        y1 = np.clip((cy - bh / 2 - geo['top']) / sc / rows, 0, 1); y2 = np.clip((cy + bh / 2 - geo['top']) / sc / rows, 0, 1)
        boxes.append(np.stack([x1, y1, x2, y2], -1).reshape(-1, 4))
        obj.append(PI.sigmoid(hh[:, 4]).reshape(-1))
        cls.append(hh[:, 5:].transpose(0, 2, 3, 1).reshape(-1, 80))
    return np.concatenate(boxes), np.concatenate(obj), np.concatenate(cls)


def iou_xyxy(boxes, roi):
    ix = np.clip(np.minimum(boxes[:, 2], roi[2]) - np.maximum(boxes[:, 0], roi[0]), 0, None)
    iy = np.clip(np.minimum(boxes[:, 3], roi[3]) - np.maximum(boxes[:, 1], roi[1]), 0, None)
    inter = ix * iy
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return inter / (area + (roi[2] - roi[0]) * (roi[3] - roi[1]) - inter + 1e-12)


def roi_scores(heads, geo, roi, min_iou):
    """Puntuaciones en la zona: anclas con IoU >= min_iou con la caja del objeto (coordenadas normalizadas)."""
    boxes, obj, logits = anchor_table(heads, geo)
    iou = iou_xyxy(boxes, roi)
    cx = (boxes[:, 0] + boxes[:, 2]) / 2; cy = (boxes[:, 1] + boxes[:, 3]) / 2
    centre = (cx >= roi[0]) & (cx <= roi[2]) & (cy >= roi[1]) & (cy <= roi[3])
    conf = obj[:, None] * PI.sigmoid(logits)                 # objectness x clase, como YOLOv5
    sel = iou >= min_iou
    per_class = conf[sel].max(0) if sel.any() else np.zeros(80, np.float32)
    per_class_centre = conf[centre].max(0) if centre.any() else np.zeros(80, np.float32)
    argmax = logits.argmax(1)
    pipe = np.where(np.isin(argmax, VEHICLES), obj * PI.sigmoid(logits.max(1)), 0)   # lo que puede salir de detect()
    best = int(np.argmax(np.where(sel, pipe, -1))) if sel.any() else -1
    top = np.argsort(-per_class)[:5]
    i_obj = int(np.argmax(np.where(sel, obj, -1))) if sel.any() else -1
    return {
        'max_objectness': float(obj[sel].max()) if sel.any() else 0.,
        'argmax_at_max_objectness': COCO[int(argmax[i_obj])] if i_obj >= 0 else None,
        'vehicle': {COCO[c]: round(float(per_class[c]), 4) for c in VEHICLES},
        'vehicle_centre': {COCO[c]: round(float(per_class_centre[c]), 4) for c in VEHICLES},
        'top5': [(COCO[c], round(float(per_class[c]), 4)) for c in top],
        'pipeline_vehicle_score': round(float(pipe[best]), 4) if best >= 0 else 0.,
        'best_anchor_iou': round(float(iou.max()), 3),
    }


def roi_detections(found, roi, min_iou):
    """Detecciones finales (decode() = YoloV5::detect) que solapan la zona."""
    out = []
    for f in found:
        x, y, w, h = f['box']
        if iou_xyxy(np.array([[x, y, x + w, y + h]]), roi)[0] >= min_iou:
            out.append(f)
    return out


# ---------------------------------------------------------------- variantes
def to_bgr(planes, hd=False):
    return PI.decode_frame(planes, hd=hd)


def crop_planes(planes, x0, y0, w, h):
    """Recorte I420 con origen y tamaño pares."""
    x0, y0, w, h = x0 & ~1, y0 & ~1, w & ~1, h & ~1
    return [p[(y0 // 2 if i else y0):(y0 + h) // (2 if i else 1), (x0 // 2 if i else x0):(x0 + w) // (2 if i else 1)]
            for i, p in enumerate(planes)], (x0, y0, w, h)


def export_nn_window(planes, x0, y0, w, h, out_w, out_h):
    """Tablas mx/my de gst_vehicles.c aplicadas a una ventana (x0, y0, w, h) del lienzo."""
    out = []
    for p, plane in enumerate(planes):
        div = 2 if p else 1
        ow, oh = out_w // div, out_h // div
        mx = x0 // div + (np.arange(ow, dtype=np.int64) * (w // div)) // ow
        my = y0 // div + (np.arange(oh, dtype=np.int64) * (h // div)) // oh
        out.append(plane[my][:, mx])
    return out


def norm_roi(roi_px, x0, y0, w, h):
    """Zona del lienzo (px) -> coordenadas normalizadas de una ventana del lienzo."""
    return np.array([(roi_px[0] - x0) / w, (roi_px[1] - y0) / h, (roi_px[2] - x0) / w, (roi_px[3] - y0) / h], np.float32)


def adjust(bgr, kind):
    if kind == 'gain0.7':
        return np.clip(bgr.astype(np.float32) * .7, 0, 255).astype(np.uint8)
    if kind == 'gain1.3':
        return np.clip(bgr.astype(np.float32) * 1.3, 0, 255).astype(np.uint8)
    if kind == 'contrast1.5':
        return np.clip((bgr.astype(np.float32) - 128) * 1.5 + 128, 0, 255).astype(np.uint8)
    if kind == 'clahe':
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        lab[..., 0] = cv2.createCLAHE(2.0, (8, 8)).apply(lab[..., 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    raise ValueError(kind)


def build_variants(ctx, zoom_window):
    """Cada variante: (imagen BGR para detect(), tamaño, rect, zona normalizada, interp, nota de coste)."""
    canvas, source, roi, src_roi = ctx['canvas'], ctx['source'], ctx['roi'], ctx['src_roi']
    full = norm_roi(roi, 0, 0, CANVAS_W, CANVAS_H)
    thumb = to_bgr(PI.export_nn(canvas, AI_W, AI_H))
    v = {}
    v['pi_320x192'] = (thumb, 320, True, full, 'Pi actual: miniatura NN 416x250 -> 320x192')
    v['pi_320x192_th'] = None  # marcador: umbrales se leen de pi_320x192
    v['thumb_416x256'] = (thumb, 416, True, full, 'miniatura NN 416x250 -> 416x256 sin reescalar')
    v['thumb_640x416'] = (thumb, 640, True, full, 'miniatura NN ampliada a 640x385 (+borde)')
    v['area_320x192'] = (to_bgr(PI.export_area(canvas, AI_W, AI_H)), 320, True, full, 'miniatura INTER_AREA por plano -> 320x192')
    v['canvas_320x192'] = (to_bgr(canvas), 320, True, full, 'lienzo 800x480 completo -> 320x192 lineal (sin miniatura NN)')
    v['canvas_416x256'] = (to_bgr(canvas), 416, True, full, 'lienzo completo -> 416x250 lineal')
    v['canvas_640x384'] = (to_bgr(canvas), 640, True, full, 'lienzo completo -> 640x384 rect')
    v['canvas_640sq'] = (to_bgr(canvas), 640, False, full, 'lienzo completo -> 640x640 cuadrado (referencia PC)')
    band = PI.export_nn(canvas, 512, 128, 160, 200)
    v['band_512x128'] = (to_bgr(band), 512, True, norm_roi(roi, 0, 160, CANVAS_W, 200), 'franja y=160-360 NN 512x128')
    zx, zy, zw, zh = zoom_window
    zoom = export_nn_window(canvas, zx, zy, zw, zh, AI_W, AI_H)
    v['zoom_window_320x192'] = (to_bgr(zoom), 320, True, norm_roi(roi, zx, zy, zw, zh),
                                f'ventana fija {zw}x{zh} en ({zx},{zy}) -> NN 416x250 -> 320x192 (zoom digital)')
    # recorte ajustado alrededor del objeto (oráculo: muestra la capacidad del modelo con escala ideal)
    w, h = roi[2] - roi[0], roi[3] - roi[1]
    cx, cy = (roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2
    cw, ch = min(CANVAS_W, int(w * 1.6)), min(CANVAS_H, int(h * 1.6))
    x0, y0 = int(np.clip(cx - cw / 2, 0, CANVAS_W - cw)), int(np.clip(cy - ch / 2, 0, CANVAS_H - ch))
    crop, (x0, y0, cw, ch) = crop_planes(canvas, x0, y0, cw, ch)
    v['crop_object_320'] = (to_bgr(crop), 320, True, norm_roi(roi, x0, y0, cw, ch), f'recorte ajustado {cw}x{ch} alrededor del objeto -> 320 rect (oráculo)')
    sw, sh = source[0].shape[1], source[0].shape[0]
    src_bgr = to_bgr(source)
    sroi = np.array([src_roi[0] / sw, src_roi[1] / sh, src_roi[2] / sw, src_roi[3] / sh], np.float32)
    v['original_320'] = (src_bgr, 320, True, sroi, f'fotograma original {sw}x{sh} sin Panini -> 320 rect')
    v['original_640'] = (src_bgr, 640, True, sroi, f'fotograma original sin Panini -> 640 rect')
    mirror = full.copy(); mirror[0], mirror[2] = 1 - full[2], 1 - full[0]
    v['pi_mirror'] = (np.ascontiguousarray(thumb[:, ::-1]), 320, True, mirror, 'Pi actual con espejo horizontal')
    v['canvas_640sq_mirror'] = (np.ascontiguousarray(to_bgr(canvas)[:, ::-1]), 640, False, mirror, 'lienzo completo 640x640 con espejo')
    # contexto frente a escala: el objeto recortado (margen 6 px) sobre gris 114, a la escala de la Pi (0,4)
    m, k = 6, .4
    patch = to_bgr(canvas)[max(0, int(roi[1]) - m):int(roi[3]) + m, max(0, int(roi[0]) - m):int(roi[2]) + m]
    patch = cv2.resize(patch, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    ph, pw = patch.shape[:2]
    gray = np.full((192, 320, 3), 114, np.uint8)
    oy, ox = (192 - ph) // 2, (320 - pw) // 2
    gray[oy:oy + ph, ox:ox + pw] = patch
    v['context_gray_320'] = (gray, 320, True, np.array([(ox + m * k) / 320, (oy + m * k) / 192, (ox + pw - m * k) / 320, (oy + ph - m * k) / 192], np.float32),
                             'el objeto recortado sobre gris a la escala de la Pi (sin contexto)')
    for kind in ('gain0.7', 'gain1.3', 'contrast1.5', 'clahe'):
        v['pi_' + kind] = (adjust(thumb, kind), 320, True, full, f'Pi actual + {kind} en la miniatura')
    v['pi_bt709'] = (to_bgr(PI.export_nn(canvas, AI_W, AI_H), hd=True), 320, True, full, 'Pi actual con decode_frame BT.709')
    del v['pi_320x192_th']
    return v


# ---------------------------------------------------------------- seguidor (src/vehicle_tracking.c)
TTL = .75
TRACKED = ('pi_320x192', 'thumb_416x256', 'canvas_416x256', 'band_512x128', 'zoom_window_320x192')


class Tracks:
    def __init__(self):
        self.vehicles = [None] * 16; self.last = [0.] * 16; self.next_id = 0


def overlap(a, b):
    x = max(a['x'], b['x']); y = max(a['y'], b['y'])
    w = max(0., min(a['x'] + a['w'], b['x'] + b['w']) - x); h = max(0., min(a['y'] + a['h'], b['y'] + b['h']) - y)
    area = w * h
    return area / (a['w'] * a['h'] + b['w'] * b['h'] - area)


def camera_track(t, vehicles, ns):
    used = [False] * 16
    for k in range(16):
        if t.last[k] > ns or ns - t.last[k] > TTL: t.last[k] = 0.
    for v in vehicles[:16]:
        best, score = -1, .25
        for k in range(16):
            if used[k] or not t.last[k] or t.vehicles[k]['category'] != v['category']: continue
            value = overlap(t.vehicles[k], v)
            if value > score: score, best = value, k
        if best < 0:
            best = next((k for k in range(16) if not used[k] and not t.last[k]), -1)
            if best < 0:
                for k in range(16):
                    if not used[k] and (best < 0 or t.last[k] < t.last[best]): best = k
            t.next_id += 1; v['track'] = t.next_id; v['hits'] = 1
        else:
            v['track'] = t.vehicles[best]['track']; v['hits'] = min(t.vehicles[best]['hits'] + 1, 1000)
        if best >= 0:
            used[best] = True; t.vehicles[best] = dict(v); t.last[best] = ns


def simulate(found_per_frame, fps, period, phase, start, need_hits, roi, min_iou):
    """vehicle_detector.cpp: umbral ya aplicado en found; seguimiento con 5/7 -> 2; alta hits>=need y pico>=start."""
    t, peaks, analyses, confirmed = Tracks(), {}, 0, 0
    frames = sorted(found_per_frame)
    next_time = frames[0] / fps + phase
    for i in frames:
        ns = i / fps
        if ns + 1e-9 < next_time: continue
        next_time = ns + period
        vehicles = [dict(x=f['box'][0], y=f['box'][1], w=f['box'][2], h=f['box'][3], confidence=f['score'],
                         category=2 if f['class'] in (5, 7) else f['class'], orig=f['class']) for f in found_per_frame[i]]
        camera_track(t, vehicles, ns)
        for k in list(peaks):
            if ns - peaks[k][1] > TTL: del peaks[k]
        ok = []
        for v in vehicles:
            p = peaks.get(v['track'], (0., ns)); peaks[v['track']] = (max(p[0], v['confidence']), ns)
            if v['hits'] >= need_hits and peaks[v['track']][0] >= start: ok.append(v)
        analyses += 1
        confirmed += any(iou_xyxy(np.array([[v['x'], v['y'], v['x'] + v['w'], v['y'] + v['h']]]), roi)[0] >= min_iou for v in ok)
    return confirmed, analyses


# ---------------------------------------------------------------- principal
def parse_box(text):
    vals = [float(v) for v in text.split(',')]
    if len(vals) != 4: raise argparse.ArgumentTypeError('x0,y0,x1,y1')
    return vals


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='.pcap, .h264, PNG de la cámara o carpeta de PNG')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--roi', type=parse_box, required=True, help='caja del objeto en el lienzo 800x480: x0,y0,x1,y1')
    ap.add_argument('--zoom-window', type=parse_box, default=[200, 120, 400, 240], help='ventana fija del lienzo x,y,w,h para la variante de zoom')
    ap.add_argument('--panini', default='1,180,130,0', help='compresión,lente,campo,pitch (systemd/rear-camera.defaults)')
    ap.add_argument('--every', type=int, default=1)
    ap.add_argument('--frames', type=int, default=0)
    ap.add_argument('--min-iou', type=float, default=.3, help='IoU mínimo ancla/detección con la zona')
    ap.add_argument('--images', default='', help='índices de fotograma cuyas imágenes intermedias se guardan, p. ej. 0,100')
    ap.add_argument('--model', type=Path, default=ROOT / 'models/yolov5n')
    ap.add_argument('--threads', type=int, default=2)
    ap.add_argument('--fps', type=float, default=25.)
    ap.add_argument('--period', type=float, default=.36, help='s entre análisis (~2,8 Hz)')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    cfg = tuple(float(v) for v in a.panini.split(','))
    roi = np.array(a.roi, np.float64)
    keep_images = {int(v) for v in a.images.split(',') if v}
    yolo = PI.NcnnYolo(a.model, a.threads)
    panini = None; rows = []; found = {}; track_roi = {}; summary = {}
    for index, bgr, planes, name in read_frames(a.input, a.every, a.frames, a.out):
        h, w = planes[0].shape
        if (w, h) == (CANVAS_W, CANVAS_H):
            canvas, src_roi = planes, roi
        else:
            if panini is None:
                print('Panini: comprobación del port, error máximo frente a referencia escalar', check_panini(cfg, w, h), flush=True)
                panini = Panini(cfg, w, h)
                # zona del objeto en el fotograma original: caja de los puntos de la zona del lienzo
                xs, ys = np.meshgrid(np.linspace(roi[0], roi[2], 40), np.linspace(roi[1], roi[3], 40))
                sx, sy, ok = panini_source(cfg, w, h, 800, 480, xs, ys)
                src_roi = np.array([sx[ok].min(), sy[ok].min(), sx[ok].max(), sy[ok].max()])
                print('zona en el original:', np.round(src_roi, 1).tolist(), flush=True)
            canvas = panini(planes)
        ctx = dict(canvas=canvas, source=planes, roi=roi, src_roi=src_roi)
        variants = build_variants(ctx, [int(v) for v in a.zoom_window])
        for vname, (image, size, rect, nroi, note) in variants.items():
            padded, geo = PI.letterbox(image, size, rect)
            heads = yolo.heads(padded)
            sc = roi_scores(heads, geo, nroi, a.min_iou)
            row = dict(frame=index, name=name, variant=vname, input=f"{geo['input_w']}x{geo['input_h']}", **{
                'max_obj': round(sc['max_objectness'], 4), 'argmax_at_max_obj': sc['argmax_at_max_objectness'],
                **{f'{k}': v for k, v in sc['vehicle'].items()}, **{f'{k}_centre': v for k, v in sc['vehicle_centre'].items()},
                'pipeline_vehicle': sc['pipeline_vehicle_score'], 'top5': ' '.join(f'{c}:{s:.3f}' for c, s in sc['top5'])})
            for thr in (.15, .25):
                dets = roi_detections(PI.decode(heads, geo, thr), nroi, a.min_iou)
                row[f'det@{thr}'] = ';'.join(f"{COCO[d['class']]}:{d['score']:.3f}" for d in dets)
            rows.append(row)
            if vname in TRACKED:
                found.setdefault(vname, {})[index] = PI.decode(heads, geo, .15)
                track_roi[vname] = nroi
            summary.setdefault(vname, dict(note=note, input=row['input'], frames=0, sums={}, hits={'0.15': 0, '0.25': 0}))
            s = summary[vname]; s['frames'] += 1
            for k in ('max_obj', 'car', 'truck', 'bus', 'motorcycle', 'pipeline_vehicle'):
                s['sums'].setdefault(k, []).append(row[k])
            for c, val in sc['top5']:
                s.setdefault('top', {}).setdefault(c, []).append(val)
            s['hits']['0.15'] += bool(row['det@0.15']); s['hits']['0.25'] += bool(row['det@0.25'])
            if index in keep_images:
                img = padded.copy()
                x0 = geo['left'] + nroi[0] * geo['cols'] * geo['scale']; x1 = geo['left'] + nroi[2] * geo['cols'] * geo['scale']
                y0 = geo['top'] + nroi[1] * geo['rows'] * geo['scale']; y1 = geo['top'] + nroi[3] * geo['rows'] * geo['scale']
                cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), (0, 255, 255), 1)
                cv2.imwrite(str(a.out / f'{index:04d}_{vname}_input{geo["input_w"]}x{geo["input_h"]}.png'), img)
        if index in keep_images:
            cv2.imwrite(str(a.out / f'{index:04d}_canvas800x480.png'), to_bgr(canvas))
            cv2.imwrite(str(a.out / f'{index:04d}_thumb416x250.png'), to_bgr(PI.export_nn(canvas, AI_W, AI_H)))
        print(index, ' '.join(f"{r['variant']}={r['pipeline_vehicle']:.2f}" for r in rows if r['frame'] == index and r['variant'] in
                              ('pi_320x192', 'thumb_416x256', 'canvas_640sq', 'original_640', 'crop_object_320')), flush=True)

    with (a.out / 'per_frame.csv').open('w', newline='', encoding='utf-8') as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    out = {}
    for vname, s in summary.items():
        out[vname] = dict(note=s['note'], input=s['input'], frames=s['frames'],
                          detected_frames={k: f"{v}/{s['frames']}" for k, v in s['hits'].items()},
                          median={k: round(float(np.median(v)), 4) for k, v in s['sums'].items()},
                          max={k: round(float(np.max(v)), 4) for k, v in s['sums'].items()},
                          top_classes_median=sorted(((c, round(float(np.median(v + [0] * (s['frames'] - len(v)))), 4)) for c, v in s['top'].items()),
                                                    key=lambda x: -x[1])[:6])
    # Seguimiento a la cadencia de la Pi (tiene sentido con fotogramas consecutivos, --every 1)
    tracking = {}
    phases = np.arange(0, a.period, a.every / a.fps)
    is_video = Path(a.input).suffix.lower() in ('.pcap', '.h264')
    for vname, per_frame in found.items():
        if not is_video or len(per_frame) < 4: continue
        tracking[vname] = {}
        for label, need, start in (('actual: hits>=2, pico>=0,25 (umbral 0,15)', 2, .25), ('hits>=1, pico>=0,25', 1, .25),
                                   ('hits>=2, pico>=0,15', 2, .15), ('hits>=1, pico>=0,15', 1, .15)):
            res = [simulate(per_frame, a.fps, a.period, p, start, need, track_roi[vname], a.min_iou) for p in phases]
            c = sum(r[0] for r in res); n = sum(r[1] for r in res)
            tracking[vname][label] = f'{c}/{n}'
    result = dict(input=str(a.input), roi_canvas=a.roi, panini=cfg, min_iou=a.min_iou, variants=out,
                  tracking_note=f'análisis con vehículo confirmado en la zona / análisis, cadencia {a.period} s, {len(phases)} fases',
                  tracking=tracking)
    (a.out / 'summary.json').write_text(json.dumps(result, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    for vname, s in out.items():
        print(f"{vname:22s} {s['input']:>8s} det@.15 {s['detected_frames']['0.15']:>8s} det@.25 {s['detected_frames']['0.25']:>8s} "
              f"car med/max {s['median']['car']:.3f}/{s['max']['car']:.3f} truck {s['median']['truck']:.3f} obj {s['median']['max_obj']:.3f} "
              f"top {s['top_classes_median'][:3]}")
    for vname, labels in tracking.items():
        print('seguimiento', vname, labels)


if __name__ == '__main__':
    main()
