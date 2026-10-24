#!/usr/bin/env python3
"""Evaluación offline, por fotograma y sin seguimiento, del perfil EXACTO de detección de la Pi.

Reproduce la cadena del coche a partir de src/:
  lienzo 800x480 (PNG de manual-reference) -> I420 BT.601 rango limitado (suposición, ver SUPUESTOS)
  -> miniatura 416x250 por vecino más próximo con las tablas mx/my de src/gst_vehicles.c
  -> decode_frame de src/vehicle_detector.cpp (I420 -> BGR entero)
  -> YoloV5::detect de src/yolov5.hpp (escala float, entrada rectangular múltiplo de 32,
     INTER_LINEAR, borde 114, BGR2RGB, /255, cabezas NCNN, decodificación, clases 2/3/5/7, NMS 0,45, máx. 16)
con el MISMO modelo NCNN que la Pi (models/yolov5n.param/.bin, paquete pip `ncnn`).

Puntuación: regla de anclaje de tools/evaluate-all-frames.py (se importa y se comprueba) y,
como métrica secundaria, IoU >= 0,5 uno a uno. Genera solo JSON/CSV.

Uso (venv con ncnn, numpy, opencv-python-headless, onnx y onnxruntime):
  python tools/evaluate-pi-profile.py [--output comparisons/pi-profile-evaluation]
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

import cv2
import ncnn
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'comparisons/new-models_all-frames_6-videos/manual-reference'
OLD_STUDY = ROOT / 'comparisons/old/all-models_all-frames_6-videos'
NAMES = ('01_winchester', '02_unity', '03_hornet', '04_dwarf', '05_texana', '06_x5m')
CANVAS_W, CANVAS_H = 800, 480
AI_W, AI_H = 416, 250                     # src/vehicle_protocol.h CAMERA_AI_WIDTH/HEIGHT
THRESHOLDS = (.15, .25)                   # vehicle_detector.cpp: umbral de observación y de alta
WIDTH_BUCKETS = (('<40', 0, 40), ('40-80', 40, 80), ('>=80', 80, 10 ** 9))
ANCHORS = np.array([[10, 13, 16, 30, 33, 23], [30, 61, 62, 45, 59, 119], [116, 90, 156, 198, 373, 326]], np.float32)
AUDIT = {  # cifras de auditoria/hallazgos-2026-10-24.json (ORT FP32 en PC, scratchpad/yolo_res_eval.py)
    'a_pi_320x192': {'0.15': 58, '0.25': 48, '<40@0.15': '4/27', '40-80@0.15': '18/24'},
    'b_thumb_416x256': {'0.15': 70, '0.25': 68},
    'c_640_square': {'0.15': 82, '0.25': 76, '<40@0.15': '14/27', '40-80@0.15': '24/24'},
    'd_area_320x192': {'0.15': 60, '0.25': 54, 'note': 'auditor: INTER_AREA del lienzo BGR directo a 320x192; verificador con ruta I420: 61/53'},
    'e_band_512x128': {'0.15': 77, '0.25': 64, 'note': 'auditor: franja y=144-336 elegida mirando la referencia; variante NN 512x128: 80/69'},
}


# ---------------------------------------------------------------- utilidades
def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def round_half_away(x):  # std::lround para valores positivos
    return int(math.floor(float(x) + .5))


def load_score_rule():
    spec = importlib.util.spec_from_file_location('evaluate_all_frames', ROOT / 'tools/evaluate-all-frames.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.score


def anchor_match(truth, found):
    """Copia de score() de tools/evaluate-all-frames.py que además devuelve los índices emparejados."""
    pairs = []
    for i, (x, y, w, h) in enumerate(truth):
        for j, b in enumerate(found):
            xx, yy, ww, hh = b['box']; ax = (xx + ww / 2) * 800; ay = yy * 480
            if x - 2 <= ax <= x + w + 2 and abs(ay - y) <= max(4, h * .5):
                pairs.append((abs(ax - (x + w / 2)) / w + abs(ay - y) / h, i, j))
    used_t, used_p, match = set(), set(), {}
    for _, i, j in sorted(pairs):
        if i not in used_t and j not in used_p:
            used_t.add(i); used_p.add(j); match[i] = j
    duplicates = len({j for _, i, j in pairs if j not in used_p})
    return {'matched': len(used_t), 'missed': len(truth) - len(used_t), 'duplicate_anchors': duplicates,
            'unassociated_predictions_not_verified_false_positives': len(found) - len(used_p) - duplicates}, match


def iou(a, b):
    ix = max(0., min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])); iy = max(0., min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / (a[2] * a[3] + b[2] * b[3] - inter) if inter > 0 else 0.


def iou_match(truth, found, minimum=.5):
    boxes = [[b['box'][0] * 800, b['box'][1] * 480, b['box'][2] * 800, b['box'][3] * 480] for b in found]
    pairs = sorted(((iou(t, p), i, j) for i, t in enumerate(truth) for j, p in enumerate(boxes)), reverse=True)
    used_t, used_p, match = set(), set(), {}
    for value, i, j in pairs:
        if value < minimum: break
        if i not in used_t and j not in used_p:
            used_t.add(i); used_p.add(j); match[i] = (j, value)
    return match


def wilson(k, n, z=1.959963984540054):
    if not n: return [None, None]
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def mcnemar_exact(b, c):
    n = b + c
    if not n: return 1.
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
    return min(1., 2 * tail)


# ---------------------------------------------------------------- cadena de la Pi
def rgb_to_i420(rgb, matrix='601', chroma='average'):
    """PNG RGB -> I420 rango limitado. Croma 4:2:0 por media 2x2 (como un codificador) o esquina superior izquierda."""
    kr, kb = (.299, .114) if matrix == '601' else (.2126, .0722)
    f = rgb.astype(np.float64) / 255.
    luma = kr * f[..., 0] + (1 - kr - kb) * f[..., 1] + kb * f[..., 2]
    y = np.clip(np.floor(16 + 219 * luma + .5), 0, 255).astype(np.uint8)
    h, w = luma.shape
    sub = f.reshape(h // 2, 2, w // 2, 2, 3).mean(axis=(1, 3)) if chroma == 'average' else f[::2, ::2]
    sl = kr * sub[..., 0] + (1 - kr - kb) * sub[..., 1] + kb * sub[..., 2]
    u = np.clip(np.floor(128 + 224 * (sub[..., 2] - sl) / (2 * (1 - kb)) + .5), 0, 255).astype(np.uint8)
    v = np.clip(np.floor(128 + 224 * (sub[..., 0] - sl) / (2 * (1 - kr)) + .5), 0, 255).astype(np.uint8)
    return y, u, v


def export_nn(planes, out_w, out_h, row0=0, rows=CANVAS_H):
    """src/gst_vehicles.c set_info + transform: mx[x]=x*W_in/w, my[y]=y*H_in/h (enteros), sin filtrar.
    row0/rows permiten exportar una franja horizontal del lienzo con las mismas tablas."""
    out = []
    for p, plane in enumerate(planes):
        div = 2 if p else 1; w, h = out_w // div, out_h // div
        mx = (np.arange(w, dtype=np.int64) * (CANVAS_W // div)) // w
        my = row0 // div + (np.arange(h, dtype=np.int64) * (rows // div)) // h
        out.append(plane[my][:, mx])
    return out


def export_area(planes, out_w, out_h):
    return [cv2.resize(p, ((out_w // 2) if i else out_w, (out_h // 2) if i else out_h), interpolation=cv2.INTER_AREA)
            for i, p in enumerate(planes)]


def decode_frame(planes, full=False, hd=False):
    """src/vehicle_detector.cpp decode_frame, aritmética entera idéntica."""
    yy, uu, vv = planes
    lum = yy.astype(np.int32) - (0 if full else 16)
    u = uu.astype(np.int32).repeat(2, 0).repeat(2, 1) - 128
    v = vv.astype(np.int32).repeat(2, 0).repeat(2, 1) - 128
    a = 256 if full else 298
    r = (a * lum + ((403 if full else 459) if hd else (359 if full else 409)) * v + 128) >> 8
    g = (a * lum - ((48 if full else 55) if hd else (88 if full else 100)) * u - ((120 if full else 136) if hd else (183 if full else 208)) * v + 128) >> 8
    b = (a * lum + ((475 if full else 541) if hd else (454 if full else 516)) * u + 128) >> 8
    return np.clip(np.stack((b, g, r), axis=2), 0, 255).astype(np.uint8)


def audit_thumbnail(bgr):
    """Miniatura usada por la auditoría (copiada de tools/analyze-video.py thumbnail): OpenCV BGR2YUV_I420 + INTER_NEAREST."""
    H, W = bgr.shape[:2]
    flat = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420).ravel(); pixels = W * H
    yy = cv2.resize(flat[:pixels].reshape(H, W), (416, 250), interpolation=cv2.INTER_NEAREST).astype(np.int32) - 16
    u = cv2.resize(flat[pixels:pixels + pixels // 4].reshape(H // 2, W // 2), (208, 125), interpolation=cv2.INTER_NEAREST).astype(np.int32) - 128
    v = cv2.resize(flat[pixels + pixels // 4:].reshape(H // 2, W // 2), (208, 125), interpolation=cv2.INTER_NEAREST).astype(np.int32) - 128
    u = u.repeat(2, 0).repeat(2, 1); v = v.repeat(2, 0).repeat(2, 1)
    return np.stack((np.clip((298 * yy + 516 * u + 128) >> 8, 0, 255), np.clip((298 * yy - 100 * u - 208 * v + 128) >> 8, 0, 255),
                     np.clip((298 * yy + 409 * v + 128) >> 8, 0, 255)), axis=2).astype(np.uint8)


def letterbox(image, size, rect, interp=cv2.INTER_LINEAR):
    """Parte geométrica de YoloV5::detect (src/yolov5.hpp), con la escala en float32 como en C++."""
    rows, cols = image.shape[:2]
    scale = np.float32(size) / np.float32(max(cols, rows))
    w = max(1, round_half_away(np.float32(cols) * scale)); h = max(1, round_half_away(np.float32(rows) * scale))
    input_w = (w + 31) // 32 * 32 if rect else size; input_h = (h + 31) // 32 * 32 if rect else size
    left, top = (input_w - w) // 2, (input_h - h) // 2
    resized = cv2.resize(image, (w, h), interpolation=interp)
    padded = cv2.copyMakeBorder(resized, top, input_h - h - top, left, input_w - w - left, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return padded, dict(scale=scale, left=left, top=top, input_w=input_w, input_h=input_h, cols=cols, rows=rows)


def sigmoid(x):
    return (np.float32(1) / (np.float32(1) + np.exp(-x))).astype(np.float32)


def decode(heads, geo, threshold):
    """Decodificación y NMS de YoloV5::detect en float32, mismo orden de candidatos (nivel, ancla, y, x)."""
    thr = np.float32(threshold); scale = geo['scale']; cand = []
    for level, head in enumerate(heads):
        stride = np.float32(8 << level)
        for a in range(3):
            ch = head[a * 85:(a + 1) * 85]
            obj = sigmoid(ch[4]); cls = ch[5:]; category = cls.argmax(0); peak = np.take_along_axis(cls, category[None], 0)[0]
            keep = (obj >= thr) & np.isin(category, (2, 3, 5, 7))
            score = obj * sigmoid(peak); keep &= score >= thr
            for y, x in zip(*np.nonzero(keep)):
                s = sigmoid(ch[:4, y, x])
                cx = (s[0] * np.float32(2) - np.float32(.5) + np.float32(x)) * stride
                cy = (s[1] * np.float32(2) - np.float32(.5) + np.float32(y)) * stride
                bw = (s[2] * np.float32(2)) ** 2 * ANCHORS[level, a * 2]; bh = (s[3] * np.float32(2)) ** 2 * ANCHORS[level, a * 2 + 1]
                half = np.float32(.5)
                x1 = np.clip((cx - bw * half - geo['left']) / scale / np.float32(geo['cols']), 0, 1)
                y1 = np.clip((cy - bh * half - geo['top']) / scale / np.float32(geo['rows']), 0, 1)
                x2 = np.clip((cx + bw * half - geo['left']) / scale / np.float32(geo['cols']), 0, 1)
                y2 = np.clip((cy + bh * half - geo['top']) / scale / np.float32(geo['rows']), 0, 1)
                if x2 > x1 and y2 > y1:
                    cand.append((np.float32(score[y, x]), np.array([x1, y1, x2 - x1, y2 - y1], np.float32), int(category[y, x])))
    cand.sort(key=lambda c: -c[0])  # estable, como std::stable_sort
    result = []
    for score, box, category in cand:
        keep = True
        for _, prior, _ in result:
            ix = max(box[0], prior[0]); iy = max(box[1], prior[1])
            iw = min(box[0] + box[2], prior[0] + prior[2]) - ix; ih = min(box[1] + box[3], prior[1] + prior[3]) - iy
            inter = np.float32(iw * ih) if iw > 0 and ih > 0 else np.float32(0)
            if inter / (box[2] * box[3] + prior[2] * prior[3] - inter) > np.float32(.45): keep = False; break
        if keep: result.append((score, box, category))
        if len(result) == 16: break
    return [{'box': [float(v) for v in box], 'score': float(score), 'class': category} for score, box, category in result]


class NcnnYolo:
    def __init__(self, prefix, threads):
        self.net = ncnn.Net(); o = self.net.opt
        o.num_threads = threads; o.use_vulkan_compute = False
        o.use_fp16_storage = False; o.use_fp16_arithmetic = False; o.use_bf16_storage = False
        if self.net.load_param(str(prefix) + '.param') or self.net.load_model(str(prefix) + '.bin'):
            raise RuntimeError('No se puede cargar el modelo NCNN')
        if list(self.net.input_names()) != ['in0']: raise RuntimeError('Entrada NCNN inesperada')

    def heads(self, padded):
        h, w = padded.shape[:2]
        mat = ncnn.Mat.from_pixels(np.ascontiguousarray(padded), ncnn.Mat.PixelType.PIXEL_BGR2RGB, w, h)
        mat.substract_mean_normalize([], [1 / 255., 1 / 255., 1 / 255.])
        ex = self.net.create_extractor()
        if ex.input('in0', mat): raise RuntimeError('Fallo de entrada NCNN')
        out = []
        for level, name in enumerate(('out0', 'out1', 'out2')):
            ret, m = ex.extract(name); stride = 8 << level
            if ret or m.dims != 3 or m.c != 255 or m.w != w // stride or m.h != h // stride: raise RuntimeError('Cabeza NCNN inesperada')
            out.append(np.array(m, dtype=np.float32).copy())
        return out


# ---------------------------------------------------------------- perfiles
PROFILES = {
    'a_pi_320x192': dict(main=True, source='i420', export='nn', size=320, rect=True,
                         description='Pi actual: I420 800x480 -> miniatura NN 416x250 (tablas mx/my) -> decode_frame -> 320x192 rectangular'),
    'b_thumb_416x256': dict(main=True, source='i420', export='nn', size=416, rect=True,
                            description='Misma miniatura NN 416x250 -> entrada rectangular 416x256 (sin reescalado, borde 114 de 3 px arriba/abajo)'),
    'c_640_square': dict(main=True, source='i420', export='full', size=640, rect=False,
                         description='Referencia: lienzo I420 800x480 completo -> decode_frame -> 640x640 cuadrado (640x384 + borde 114)'),
    'd_area_320x192': dict(main=True, source='i420', export='area', size=320, rect=True,
                           description='Miniatura 416x250 con INTER_AREA por plano I420 desde 800x480 -> decode_frame -> 320x192'),
    'e_band_512x128': dict(main=True, source='i420', export='band', size=512, rect=True,
                           description='Franja a priori 800x200 del lienzo -> exportación NN 512x128 (tablas mx/my) -> decode_frame -> 512x128 sin reescalado'),
    'x_audit_thumb_320x192': dict(main=False, source='bgr', export='audit_thumb', size=320, rect=True,
                                  description='Ablación: miniatura de la auditoría (cv2 BGR2YUV_I420 con croma de esquina + INTER_NEAREST) -> 320x192'),
    'x_png_640_square': dict(main=False, source='bgr', export='png', size=640, rect=False,
                             description='Ablación: PNG BGR sin ida y vuelta I420 -> 640x640 (como el estudio antiguo y la auditoría)'),
    'x_audit_area_bgr_320x192': dict(main=False, source='bgr', export='png', size=320, rect=True, interp='area',
                                     description='Ablación: perfil de la auditoría, PNG BGR -> INTER_AREA directo a 320x192'),
    'x_a_chroma_topleft': dict(main=False, source='i420', export='nn', size=320, rect=True, chroma='topleft',
                               description='Sensibilidad de (a): conversión PNG->I420 con croma de la muestra superior izquierda (como cv2 BGR2YUV_I420) en vez de media 2x2'),
    'x_a_bt709': dict(main=False, source='i420', export='nn', size=320, rect=True, matrix='709',
                      description='Sensibilidad de (a): conversión PNG->I420 BT.709 y decode_frame con el flag CAMERA_AI_709 (coherentes entre sí)'),
    'x_audit_band144_336_bgr': dict(main=False, source='bgr', export='png_band', band=(144, 336), size=512, rect=True,
                                    description='Ablación: franja de la auditoría y=144-336 (elegida a posteriori) recortada del PNG BGR -> 512x123+borde=512x128 INTER_LINEAR'),
}


def prepare(profile, bgr, planes, band, hd=False):
    """Devuelve (imagen BGR que recibe YoloV5::detect, función que lleva cajas normalizadas al lienzo)."""
    identity = lambda b: b
    kind = profile['export']
    if kind == 'nn': return decode_frame(export_nn(planes, AI_W, AI_H), hd=hd), identity
    if kind == 'area': return decode_frame(export_area(planes, AI_W, AI_H), hd=hd), identity
    if kind == 'full': return decode_frame(planes, hd=hd), identity
    if kind == 'png': return bgr, identity
    if kind == 'audit_thumb': return audit_thumbnail(bgr), identity
    if kind in ('band', 'png_band'):
        y0, y1 = band if kind == 'band' else profile['band']
        image = decode_frame(export_nn(planes, 512, 128, y0, y1 - y0), hd=hd) if kind == 'band' else bgr[y0:y1]
        k = (y1 - y0) / CANVAS_H
        return image, lambda b: [b[0], y0 / CANVAS_H + b[1] * k, b[2], b[3] * k]
    raise ValueError(kind)


# ---------------------------------------------------------------- franja a priori
def choose_band(samples, height, step, exclude, min_score, max_width):
    """Elige y0 SIN mirar las anotaciones: detecciones YOLOv5n-640 guardadas de los vídeos completos,
    excluyendo +-exclude fotogramas alrededor de cada fotograma de la referencia (las 72 muestras)."""
    tops = []; used = 0; skipped = 0; files = []
    for n, name in enumerate(NAMES):
        path = OLD_STUDY / name / 'yolov5n/detections.jsonl'; files.append({'path': str(path.relative_to(ROOT)), 'sha256': sha256(path)})
        refs = [s['frame_index'] for s in samples if s['clip'] == n]
        with path.open() as f:
            for line in f:
                row = json.loads(line); i = row['frame_index']
                if any(abs(i - r) <= exclude for r in refs): skipped += 1; continue
                used += 1
                for b in row['raw']:
                    if b['score'] >= min_score and b['box'][2] * CANVAS_W < max_width:
                        tops.append((b['box'][1] * CANVAS_H, (b['box'][1] + b['box'][3]) * CANVAS_H))
    tops = np.array(tops)
    candidates = []
    for y0 in range(0, CANVAS_H - height + 1, step):
        inside = int(np.sum((tops[:, 0] >= y0) & (tops[:, 1] <= y0 + height)))
        candidates.append((inside, -abs(y0 + height / 2 - CANVAS_H / 2), y0))
    best = max(candidates)[2]
    hist, edges = np.histogram(tops[:, 0], bins=np.arange(0, CANVAS_H + 1, 16))
    return best, {
        'rule': f'y0 que maximiza el número de cajas YOLOv5n-640 (score>={min_score}, ancho<{max_width} px en 800) completamente dentro de [y0, y0+{height}); '
                f'paso {step} px; empate -> más centrada. Fuente: detecciones guardadas de los 6 vídeos completos, excluidos +-{exclude} fotogramas '
                'alrededor de cada una de las 72 muestras de referencia. No usa las cajas manuales; los clips sí son los mismos (no es un conjunto independiente).',
        'band_y': [best, best + height], 'height': height, 'frames_used': used, 'frames_excluded': skipped, 'boxes_used': int(len(tops)),
        'boxes_inside_best': int(max(candidates)[0]),
        'coverage_by_y0': {str(c[2]): c[0] for c in sorted(candidates, key=lambda c: c[2])},
        'histogram_box_top_16px': {f'{int(edges[i])}-{int(edges[i + 1])}': int(hist[i]) for i in range(len(hist))},
        'sources': files,
    }


# ---------------------------------------------------------------- paridad
def fp32_heads_model(work):
    """models/yolov5n.onnx (FP16) -> FP32 sin pérdidas -> subgrafo hasta las 3 cabezas, entrada dinámica."""
    import onnx
    from onnx import numpy_helper as nh, TensorProto as T
    m = onnx.load(str(ROOT / 'models/yolov5n.onnx'))
    for i, t in enumerate(m.graph.initializer):
        if t.data_type == T.FLOAT16: m.graph.initializer[i].CopyFrom(nh.from_array(nh.to_array(t).astype(np.float32), t.name))
    for node in m.graph.node:
        for a in node.attribute:
            if a.type == onnx.AttributeProto.TENSOR and a.t.data_type == T.FLOAT16: a.t.CopyFrom(nh.from_array(nh.to_array(a.t).astype(np.float32), a.t.name))
            if node.op_type == 'Cast' and a.name == 'to' and a.i == T.FLOAT16: a.i = T.FLOAT
    for v in list(m.graph.input) + list(m.graph.output) + list(m.graph.value_info):
        if v.type.tensor_type.elem_type == T.FLOAT16: v.type.tensor_type.elem_type = T.FLOAT
    full = work / 'yolov5n_fp32.onnx'; heads = work / 'heads.onnx'; onnx.save(m, str(full))
    onnx.utils.extract_model(str(full), str(heads), ['images'], ['/model.24/m.%d/Conv_output_0' % i for i in range(3)])
    h = onnx.load(str(heads)); d = h.graph.input[0].type.tensor_type.shape.dim; d[2].dim_param = 'H'; d[3].dim_param = 'W'
    for o in h.graph.output:
        for k in (2, 3): o.type.tensor_type.shape.dim[k].dim_param = f'{o.name}_{k}'
    del h.graph.value_info[:]
    onnx.save(h, str(heads))
    return heads


def ort_original_640(sess, padded, geo, threshold):
    """Modelo original FP16 (output0 ya decodificado por Detect) con el filtrado/NMS de yolov5.hpp."""
    blob = (padded[:, :, ::-1].astype(np.float32) / 255.).transpose(2, 0, 1)[None].astype(np.float16)
    data = sess.run(None, {'images': blob})[0][0].astype(np.float32)
    cand = []
    data = data[data[:, 4] >= threshold]
    for row in data:
        obj = row[4]; c = int(row[5:].argmax()); sc = obj * row[5 + c]
        if c not in (2, 3, 5, 7) or sc < threshold: continue
        cx, cy, bw, bh = row[:4]; s = float(geo['scale'])
        x1 = np.clip((cx - bw / 2 - geo['left']) / s / geo['cols'], 0, 1); y1 = np.clip((cy - bh / 2 - geo['top']) / s / geo['rows'], 0, 1)
        x2 = np.clip((cx + bw / 2 - geo['left']) / s / geo['cols'], 0, 1); y2 = np.clip((cy + bh / 2 - geo['top']) / s / geo['rows'], 0, 1)
        if x2 > x1 and y2 > y1: cand.append((float(sc), [x1, y1, x2 - x1, y2 - y1], c))
    cand.sort(key=lambda c: -c[0]); res = []
    for sc, box, c in cand:
        if all(iou(box, r['box']) <= .45 for r in res): res.append({'box': [float(v) for v in box], 'score': sc, 'class': c})
        if len(res) == 16: break
    return res


def compare_lists(a, b):
    """Empareja dos listas de detecciones por IoU>=0,9 y devuelve diferencias."""
    m = iou_match([[x['box'][0] * 800, x['box'][1] * 480, x['box'][2] * 800, x['box'][3] * 480] for x in a], b, .9)
    box = [max(abs(p - q) * s for p, q, s in zip(a[i]['box'], b[j]['box'], (800, 480, 800, 480))) for i, (j, _) in m.items()]
    score = [abs(a[i]['score'] - b[j]['score']) for i, (j, _) in m.items()]
    return len(a), len(b), len(m), (max(box) if box else 0.), (max(score) if score else 0.)


# ---------------------------------------------------------------- principal
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--reference', type=Path, default=REFERENCE)
    p.add_argument('--output', type=Path, default=ROOT / 'comparisons/pi-profile-evaluation')
    p.add_argument('--model', type=Path, default=ROOT / 'models/yolov5n', help='prefijo NCNN (.param/.bin)')
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--matrix', choices=('601', '709'), default='601', help='matriz de la conversión PNG->I420 y de decode_frame')
    p.add_argument('--chroma', choices=('average', 'topleft'), default='average', help='submuestreo 4:2:0 de la conversión PNG->I420')
    p.add_argument('--bootstrap', type=int, default=2000)
    p.add_argument('--seed', type=int, default=20261024)
    p.add_argument('--band-height', type=int, default=200, help='800x200 -> 512x128 con la misma escala 0,64 en ambos ejes')
    p.add_argument('--band-exclude', type=int, default=150, help='fotogramas excluidos a cada lado de cada muestra de referencia')
    p.add_argument('--band-y', type=int, default=160, help='borde superior de la franja (elegido a priori el 24/10/2026)')
    p.add_argument('--choose-band', action='store_true', help='recalcular la franja con las detecciones del estudio antiguo (si existen)')
    p.add_argument('--no-parity', action='store_true', help='omite la paridad con onnxruntime y con las detecciones guardadas')
    a = p.parse_args()
    started = time.time()
    ref_path = a.reference / 'reference.json'; reference = json.loads(ref_path.read_text())
    expected = json.loads((a.reference / 'reference-sha256.json').read_text())['sha256']
    if sha256(ref_path) != expected: raise RuntimeError('reference.json no coincide con reference-sha256.json')
    if reference.get('canvas') != [CANVAS_W, CANVAS_H]: raise RuntimeError('Lienzo inesperado')
    samples = [s for s in reference['samples'] if not s.get('exclude_from_scoring')]
    targets = sum(len(s['boxes']) for s in samples)
    if targets != reference['scored_target_observations']: raise RuntimeError('Recuento de objetivos distinto de scored_target_observations')
    rule = load_score_rule()

    if a.choose_band:
        band, band_info = choose_band(reference['samples'], a.band_height, 8, a.band_exclude, .25, 160)
    else:
        # The study detections used for the a-priori choice are no longer kept in the repository.
        band_info = {'rule': 'fijada con --band-y; elegida a priori el 24/10/2026 con choose_band() sobre las detecciones '
                             'YOLOv5n-640 de los vídeos completos, sin mirar las anotaciones', 'band_y': [a.band_y, a.band_y + a.band_height],
                     'height': a.band_height}
    print('Franja a priori:', band_info['band_y'], flush=True)
    band = tuple(band_info['band_y'])
    ref_tops = [b[1] for s in samples for b in s['boxes']]
    band_info['post_hoc_reference_boxes_with_top_in_band'] = f'{sum(band[0] <= t < band[1] for t in ref_tops)}/{targets} (solo informativo, no se usó para elegir)'

    yolo = NcnnYolo(a.model, a.threads)
    hd = a.matrix == '709'
    images = {}
    for s in samples:
        bgr = cv2.imread(str(a.reference / s['image']), cv2.IMREAD_COLOR)
        if bgr is None or bgr.shape != (CANVAS_H, CANVAS_W, 3): raise RuntimeError('Imagen inválida ' + s['image'])
        images[s['image']] = (bgr, {(m, c): rgb_to_i420(bgr[:, :, ::-1], m, c) for m, c in
                                    {(a.matrix, a.chroma), *((p.get('matrix', a.matrix), p.get('chroma', a.chroma)) for p in PROFILES.values())}})

    # ---- inferencia: una pasada por perfil e imagen; se decodifica a cada umbral desde las mismas cabezas
    detections = {}; timing = {}; inputs = {}
    for name, prof in PROFILES.items():
        ms = []
        for s in samples:
            bgr, conversions = images[s['image']]
            matrix = prof.get('matrix', a.matrix)
            planes = conversions[(matrix, prof.get('chroma', a.chroma))]
            image, to_canvas = prepare(prof, bgr, planes, band, matrix == '709')
            padded, geo = letterbox(image, prof['size'], prof['rect'], cv2.INTER_AREA if prof.get('interp') == 'area' else cv2.INTER_LINEAR)
            t0 = time.perf_counter(); heads = yolo.heads(padded); ms.append((time.perf_counter() - t0) * 1000)
            inputs[(name, s['image'])] = (padded, geo, heads)
            for thr in THRESHOLDS:
                found = decode(heads, geo, thr)
                for f in found: f['box'] = [float(v) for v in to_canvas(f['box'])]
                detections[(name, s['image'], thr)] = found
        shape = [geo['input_w'], geo['input_h']]
        timing[name] = {'input_wxh': shape, 'input_pixels': shape[0] * shape[1], 'pc_ncnn_median_ms': round(float(np.median(ms)), 2)}
        print(name, timing[name], flush=True)

    # ---- puntuación
    per_target = []; per_frame = []; summary = {}
    for name, prof in PROFILES.items():
        summary[name] = {'description': prof['description'], 'main_profile': prof['main'], **timing[name], 'thresholds': {}}
        for thr in THRESHOLDS:
            tot = dict(matched=0, missed=0, duplicate_anchors=0, unassociated_predictions_not_verified_false_positives=0)
            iou_matched = iou_unmatched = clean = clean_iou = predictions = 0
            bucket = {k: [0, 0] for k, _, _ in WIDTH_BUCKETS}; bucket_iou = {k: [0, 0] for k, _, _ in WIDTH_BUCKETS}
            for s in samples:
                found = detections[(name, s['image'], thr)]
                sc, match = anchor_match(s['boxes'], found)
                if sc != rule(s['boxes'], found): raise RuntimeError('La regla copiada difiere de tools/evaluate-all-frames.py')
                im = iou_match(s['boxes'], found)
                for k in tot: tot[k] += sc[k]
                iou_matched += len(im); iou_unmatched += len(found) - len(im); predictions += len(found)
                is_clean = sc['matched'] == len(s['boxes']) and not sc['duplicate_anchors'] and not sc['unassociated_predictions_not_verified_false_positives']
                clean += is_clean; clean_iou += len(im) == len(s['boxes']) == len(found)
                per_frame.append({'profile': name, 'threshold': thr, 'clip': NAMES[s['clip']], 'sample': s['sample'], 'frame_index': s['frame_index'], 'image': s['image'],
                                  'targets': len(s['boxes']), 'predictions': len(found), 'anchor_matched': sc['matched'], 'duplicate_anchors': sc['duplicate_anchors'],
                                  'unassociated': sc['unassociated_predictions_not_verified_false_positives'], 'clean_anchor': int(is_clean),
                                  'iou50_matched': len(im), 'iou50_unmatched_predictions': len(found) - len(im)})
                for i, box in enumerate(s['boxes']):
                    label = next(k for k, lo, hi in WIDTH_BUCKETS if lo <= box[2] < hi)
                    bucket[label][1] += 1; bucket[label][0] += i in match
                    bucket_iou[label][1] += 1; bucket_iou[label][0] += i in im
                    j = match.get(i)
                    per_target.append({'profile': name, 'threshold': thr, 'clip': NAMES[s['clip']], 'clip_index': s['clip'], 'sample': s['sample'],
                                       'frame_index': s['frame_index'], 'image': s['image'], 'target': i, 'x': box[0], 'y': box[1], 'w': box[2], 'h': box[3],
                                       'width_bucket': label, 'anchor_hit': int(j is not None), 'anchor_score': round(found[j]['score'], 4) if j is not None else '',
                                       'iou50_hit': int(i in im), 'iou': round(im[i][1], 3) if i in im else ''})
            n = len(samples)
            summary[name]['thresholds'][str(thr)] = {
                'anchor': {**tot, 'targets': targets, 'recall': round(tot['matched'] / targets, 4), 'wilson95': wilson(tot['matched'], targets),
                           'by_width': {k: {'hits': v[0], 'targets': v[1], 'recall': round(v[0] / v[1], 4), 'wilson95': wilson(*v)} for k, v in bucket.items()},
                           'unmatched_predictions_per_frame': round((tot['duplicate_anchors'] + tot['unassociated_predictions_not_verified_false_positives']) / n, 4),
                           'fully_clean_frames': clean, 'frames': n},
                'iou50': {'matched': iou_matched, 'targets': targets, 'recall': round(iou_matched / targets, 4), 'wilson95': wilson(iou_matched, targets),
                          'by_width': {k: {'hits': v[0], 'targets': v[1]} for k, v in bucket_iou.items()},
                          'unmatched_predictions_per_frame': round(iou_unmatched / n, 4), 'fully_clean_frames': clean_iou},
                'predictions_per_frame': round(predictions / n, 4),
            }

    # ---- bootstrap por clip y McNemar frente a (a)
    rng = np.random.default_rng(a.seed); draws = rng.integers(0, 6, size=(a.bootstrap, 6))
    def hits(name, thr, key='anchor_hit'):
        return np.array([r[key] for r in per_target if r['profile'] == name and r['threshold'] == thr])
    clip_of = np.array([r['clip_index'] for r in per_target if r['profile'] == 'a_pi_320x192' and r['threshold'] == .15])
    tcount = np.array([np.sum(clip_of == c) for c in range(6)])
    for thr in THRESHOLDS:
        base = hits('a_pi_320x192', thr)
        for name in PROFILES:
            h = hits(name, thr); per_clip = np.array([h[clip_of == c].sum() for c in range(6)]); base_clip = np.array([base[clip_of == c].sum() for c in range(6)])
            rec = per_clip[draws].sum(1) / tcount[draws].sum(1); diff = rec - base_clip[draws].sum(1) / tcount[draws].sum(1)
            entry = summary[name]['thresholds'][str(thr)]['anchor']
            entry['bootstrap_clip95'] = [round(float(np.percentile(rec, 2.5)), 4), round(float(np.percentile(rec, 97.5)), 4)]
            entry['hits_per_clip'] = {NAMES[c]: f'{int(per_clip[c])}/{int(tcount[c])}' for c in range(6)}
            if name != 'a_pi_320x192':
                b = int(np.sum((h == 1) & (base == 0))); c = int(np.sum((h == 0) & (base == 1)))
                entry['vs_a_pi_320x192'] = {'only_this': b, 'only_a': c, 'mcnemar_exact_p': float(f'{mcnemar_exact(b, c):.3g}'),
                                            'recall_difference': round(float((h.sum() - base.sum()) / targets), 4),
                                            'bootstrap_clip95_difference': [round(float(np.percentile(diff, 2.5)), 4), round(float(np.percentile(diff, 97.5)), 4)]}
                hi = hits(name, thr, 'iou50_hit'); bi = hits('a_pi_320x192', thr, 'iou50_hit')
                b2 = int(np.sum((hi == 1) & (bi == 0))); c2 = int(np.sum((hi == 0) & (bi == 1)))
                summary[name]['thresholds'][str(thr)]['iou50']['vs_a_pi_320x192'] = {'only_this': b2, 'only_a': c2, 'mcnemar_exact_p': float(f'{mcnemar_exact(b2, c2):.3g}')}

    # ---- paridad
    parity = {}
    if not a.no_parity:
        import onnxruntime as ort
        work = Path(tempfile.mkdtemp(prefix='f4-parity-'))
        try:
            so = ort.SessionOptions(); so.intra_op_num_threads = a.threads; so.log_severity_level = 3
            heads_sess = ort.InferenceSession(str(fp32_heads_model(work)), so, providers=['CPUExecutionProvider'])
            for name in ('a_pi_320x192', 'b_thumb_416x256', 'c_640_square', 'd_area_320x192', 'e_band_512x128'):
                max_head = 0.; same = 0; total_n = [0, 0]; box_d = score_d = 0.
                for s in samples:
                    padded, geo, heads = inputs[(name, s['image'])]
                    blob = (padded[:, :, ::-1].astype(np.float32) / 255.).transpose(2, 0, 1)[None].copy()
                    ref = [o[0] for o in heads_sess.run(None, {'images': blob})]
                    max_head = max(max_head, max(float(np.abs(x - y).max()) for x, y in zip(heads, ref)))
                    d1, d2 = decode(heads, geo, .15), decode(ref, geo, .15)
                    n1, n2, m, bd, sd = compare_lists(d1, d2); total_n[0] += n1; total_n[1] += n2
                    same += n1 == n2 == m; box_d = max(box_d, bd); score_d = max(score_d, sd)
                parity[name + '_ncnn_vs_ort_fp32_heads'] = {'max_abs_head_logit_diff': max_head, 'frames_identical_detection_sets@0.15': f'{same}/{len(samples)}',
                                                             'detections_ncnn_vs_ort': total_n, 'max_box_diff_px_canvas': round(box_d, 4), 'max_score_diff': round(score_d, 6)}
            orig = ort.InferenceSession(str(ROOT / 'models/yolov5n.onnx'), so, providers=['CPUExecutionProvider'])
            tot_o = tot_n = 0; same = 0; box_d = score_d = 0.
            for s in samples:
                padded, geo, heads = inputs[('x_png_640_square', s['image'])]
                d_o = ort_original_640(orig, padded, geo, .15); d_n = decode(heads, geo, .15)
                tot_o += rule(s['boxes'], d_o)['matched']; tot_n += rule(s['boxes'], d_n)['matched']
                n1, n2, m, bd, sd = compare_lists(d_n, d_o); same += n1 == n2 == m; box_d = max(box_d, bd); score_d = max(score_d, sd)
            parity['x_png_640_square_ncnn_vs_original_onnx_fp16_ort'] = {'anchor_matched@0.15': {'ncnn': tot_n, 'onnx_fp16': tot_o},
                                                                         'frames_identical_detection_sets': f'{same}/{len(samples)}', 'max_box_diff_px_canvas': round(box_d, 3), 'max_score_diff': round(score_d, 4)}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if not a.no_parity and not OLD_STUDY.exists():
        parity['x_png_640_square_vs_saved_old_study_yolov5n'] = 'omitida: las detecciones del estudio antiguo ya no están en el repositorio (resultado del 24/10/2026: 82 = 82 y 76 = 76)'
    elif not a.no_parity:
        saved = {}
        for n, name in enumerate(NAMES):
            want = {s['frame_index'] for s in samples if s['clip'] == n}
            with (OLD_STUDY / name / 'yolov5n/detections.jsonl').open() as f:
                for line in f:
                    row = json.loads(line)
                    if row['frame_index'] in want: saved[(n, row['frame_index'])] = row['raw']
        res = {}
        for thr in THRESHOLDS:
            tot_s = tot_n = same = 0; box_d = score_d = 0.
            for s in samples:
                sv = [b for b in saved[(s['clip'], s['frame_index'])] if b['score'] >= thr]; nv = detections[('x_png_640_square', s['image'], thr)]
                tot_s += rule(s['boxes'], sv)['matched']; tot_n += rule(s['boxes'], nv)['matched']
                n1, n2, m, bd, sd = compare_lists(nv, sv); same += n1 == n2 == m; box_d = max(box_d, bd); score_d = max(score_d, sd)
            res[str(thr)] = {'anchor_matched': {'ncnn_this_tool': tot_n, 'saved_openvino_640': tot_s}, 'frames_identical_detection_sets': f'{same}/{len(samples)}',
                             'max_box_diff_px_canvas_matched': round(box_d, 3), 'max_score_diff_matched': round(score_d, 4)}
        parity['x_png_640_square_vs_saved_old_study_yolov5n'] = {'source': str((OLD_STUDY / '*/yolov5n/detections.jsonl').relative_to(ROOT)), **res,
                                                                 'note': 'estudio antiguo: OpenVINO con salida en FP16 aparente (puntuaciones cuantizadas) y NMS de cv2.dnn.NMSBoxes'}
        print(json.dumps(parity, indent=1), flush=True)

    # ---- contraste con la auditoría
    audit_check = {}
    for name, ref in AUDIT.items():
        mine = summary[name]['thresholds']
        audit_check[name] = {'audit': ref, 'this_tool': {'0.15': mine['0.15']['anchor']['matched'], '0.25': mine['0.25']['anchor']['matched'],
                                                         '<40@0.15': f"{mine['0.15']['anchor']['by_width']['<40']['hits']}/{mine['0.15']['anchor']['by_width']['<40']['targets']}",
                                                         '40-80@0.15': f"{mine['0.15']['anchor']['by_width']['40-80']['hits']}/{mine['0.15']['anchor']['by_width']['40-80']['targets']}"}}
    for name in [n for n in PROFILES if n.startswith('x_')]:
        mine = summary[name]['thresholds']; audit_check[name] = {'this_tool': {'0.15': mine['0.15']['anchor']['matched'], '0.25': mine['0.25']['anchor']['matched']}}
    m = lambda n, t: summary[n]['thresholds'][t]['anchor']['matched']
    audit_check['explanation'] = {
        'engine': 'NCNN (este script) y ORT FP32 dan cabezas casi idénticas (ver parity) y las ablaciones con la cadena de la auditoría reproducen sus cifras: '
                  f"miniatura de la auditoría {m('x_audit_thumb_320x192', '0.15')}/{m('x_audit_thumb_320x192', '0.25')}, PNG a 640 {m('x_png_640_square', '0.15')}/{m('x_png_640_square', '0.25')}, "
                  f"área BGR {m('x_audit_area_bgr_320x192', '0.15')}/{m('x_audit_area_bgr_320x192', '0.25')}, franja 144-336 {m('x_audit_band144_336_bgr', '0.15')}/{m('x_audit_band144_336_bgr', '0.25')}. "
                  'Las diferencias de los perfiles principales vienen de la entrada, no del motor.',
        'a_pi_320x192': f"{m('a_pi_320x192', '0.15')}/{m('a_pi_320x192', '0.25')} frente a 58/48: la auditoría convierte con cv2 BGR2YUV_I420 (croma de la muestra superior izquierda, "
                        f"redondeo de OpenCV) y muestrea con INTER_NEAREST; aquí croma por media 2x2 y tablas enteras de gst_vehicles.c. Con croma de esquina: "
                        f"{m('x_a_chroma_topleft', '0.15')}/{m('x_a_chroma_topleft', '0.25')}; con BT.709 coherente: {m('x_a_bt709', '0.15')}/{m('x_a_bt709', '0.25')}. "
                        'Es sensibilidad a +-1-2 objetivos del color de la miniatura, dentro del ruido (McNemar en vs_a_pi_320x192 de las ablaciones).',
        'b_thumb_416x256': f"{m('b_thumb_416x256', '0.15')}/{m('b_thumb_416x256', '0.25')} frente a 70/68: misma causa (ruta de color de la miniatura).",
        'c_640_square': f"{m('c_640_square', '0.15')}/{m('c_640_square', '0.25')} frente a 82/76: aquí 640 recibe el lienzo tras la ida y vuelta I420 4:2:0 + decode_frame "
                        f"(misma ruta de color que la Pi); la auditoría usó el PNG BGR directo (= x_png_640_square, {m('x_png_640_square', '0.15')}/{m('x_png_640_square', '0.25')}).",
        'd_area_320x192': f"{m('d_area_320x192', '0.15')}/{m('d_area_320x192', '0.25')} frente a 60/54: aquí INTER_AREA por plano I420 a 416x250 y luego el 320x192 lineal de la Pi "
                          '(implementable en cameravehicles); la auditoría aplicó INTER_AREA al PNG BGR directo a 320x192 (= x_audit_area_bgr_320x192). El verificador obtuvo 61/53 con ruta I420.',
        'e_band_512x128': f"{m('e_band_512x128', '0.15')}/{m('e_band_512x128', '0.25')} frente a 77/64: franja distinta (elegida a priori, y={band[0]}-{band[1]}, 800x200 -> 512x128 sin "
                          'borde) y exportada por vecino más próximo desde I420 como haría cameravehicles; la auditoría eligió 144-336 mirando la referencia, recortó el PNG BGR y '
                          'reescaló con INTER_LINEAR a 512x123 + borde. Su variante NN 512x128 dio 80/69.',
    }

    a.output.mkdir(parents=True, exist_ok=True)
    pngs = {s['image']: sha256(a.reference / s['image']) for s in samples}
    result = {
        'tool': 'tools/evaluate-pi-profile.py',
        'command': ' '.join([Path(sys.executable).name] + [str(Path(sys.argv[0]).as_posix())] + sys.argv[1:]),
        'arguments': {k: str(v) for k, v in vars(a).items()},
        'generated_unix_s': round(started),
        'runtime_s': round(time.time() - started, 1),
        'versions': {'python': platform.python_version(), 'platform': platform.platform(), 'numpy': np.__version__, 'opencv': cv2.__version__,
                     'ncnn': getattr(ncnn, '__version__', '?'), 'onnxruntime': (__import__('onnxruntime').__version__ if not a.no_parity else None),
                     'onnx': (__import__('onnx').__version__ if not a.no_parity else None)},
        'hashes': {'yolov5n.param': sha256(str(a.model) + '.param'), 'yolov5n.bin': sha256(str(a.model) + '.bin'), 'yolov5n.onnx': sha256(ROOT / 'models/yolov5n.onnx'),
                   'reference.json': sha256(ref_path), 'evaluate-all-frames.py': sha256(ROOT / 'tools/evaluate-all-frames.py'),
                   'src/gst_vehicles.c': sha256(ROOT / 'src/gst_vehicles.c'), 'src/vehicle_detector.cpp': sha256(ROOT / 'src/vehicle_detector.cpp'),
                   'src/yolov5.hpp': sha256(ROOT / 'src/yolov5.hpp'), 'png_sha256': pngs,
                   'png_set_sha256': hashlib.sha256(''.join(f'{k}:{v}\n' for k, v in sorted(pngs.items())).encode()).hexdigest()},
        'reference': {'path': str(ref_path.relative_to(ROOT)), 'version': reference.get('reference_version'), 'scored_frames': len(samples), 'targets': targets,
                      'excluded_frames': [s['image'] for s in reference['samples'] if s.get('exclude_from_scoring')],
                      'targets_by_width': {k: sum(lo <= b[2] < hi for s in samples for b in s['boxes']) for k, lo, hi in WIDTH_BUCKETS},
                      'limits': reference['limits']},
        'assumptions': [
            f'PNG RGB -> I420 rango limitado BT.{a.matrix}, croma 4:2:0 por {"media 2x2" if a.chroma == "average" else "muestra superior izquierda"}; '
            'decode_frame con flags full=0 y 709=' + str(int(hd)) + '. En la Pi la colorimetría la fijan las caps del decodificador y de camerapanini: no verificada aquí.',
            'Los PNG ya son el lienzo 800x480 (vídeo de YouTube con INTER_AREA), no la salida Panini de la cámara.',
            'Evaluación por fotograma sin seguimiento: 0,25 es el umbral de alta aplicado a las detecciones en bruto, no a triángulos confirmados (hits>=2, TTL, 2,78 Hz).',
            'ncnn pip 1.0.20261020 en x86 (la wheel 1.0.20250916, misma revisión que la Pi, falla al importarse en Python 3.14 de Windows); FP32 sin fp16/bf16, como YoloV5.',
            'cv::resize INTER_LINEAR/INTER_AREA de OpenCV 4.13 de PC; en la Pi se usa la libopencv de Raspbian (redimensionado de 8 bits en coma fija, se espera bit a bit igual, no verificado).',
        ],
        'matching': {'anchor': 'regla score() de tools/evaluate-all-frames.py importada y comprobada en cada fotograma: uno a uno; ancla = (centro x, borde superior) de la predicción; '
                               'x dentro de la caja +-2 px y |y_ancla - y_sup| <= max(4, h/2). Duplicados = predicciones extra que encajan con un objetivo.',
                     'iou50': 'secundaria: uno a uno voraz por IoU descendente con IoU>=0,5 en píxeles del lienzo 800x480.',
                     'clean_frame': 'anchor: todos los objetivos emparejados y 0 duplicados y 0 no asociadas; iou50: todos emparejados y 0 predicciones sin pareja.',
                     'unmatched_predictions_per_frame': '(duplicados + no asociadas) / fotogramas; la referencia no es exhaustiva: no son falsos positivos certificados.',
                     'width_buckets_px_canvas': [[k, lo, hi] for k, lo, hi in WIDTH_BUCKETS],
                     'bootstrap': f'{a.bootstrap} remuestreos de los 6 clips con reemplazo (semilla {a.seed}); percentiles 2,5-97,5 del recall agregado.',
                     'mcnemar': 'exacto bilateral (binomial) sobre objetivos discordantes frente a a_pi_320x192.',
                     'pc_ncnn_median_ms': f'solo inferencia NCNN en este PC x86 con {a.threads} hilos; orientativo del coste relativo, NO es latencia de la Pi.'},
        'band_selection': band_info,
        'profiles': summary,
        'parity': parity,
        'audit_comparison': audit_check,
        'outputs': ['results.json', 'per_target.csv', 'per_frame.csv'],
    }
    (a.output / 'results.json').write_text(json.dumps(result, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    for fname, rows in (('per_target.csv', per_target), ('per_frame.csv', per_frame)):
        with (a.output / fname).open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    for name, prof in summary.items():
        t = prof['thresholds']
        print(f"{name:28s} 0.15: {t['0.15']['anchor']['matched']:3d}/{targets} iou {t['0.15']['iou50']['matched']:3d}  "
              f"0.25: {t['0.25']['anchor']['matched']:3d}/{targets} iou {t['0.25']['iou50']['matched']:3d}  <40@.15 {t['0.15']['anchor']['by_width']['<40']['hits']}/27", flush=True)


if __name__ == '__main__':
    main()
