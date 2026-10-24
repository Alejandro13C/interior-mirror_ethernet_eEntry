#!/usr/bin/env python3
"""Evaluación offline del seguimiento y de los triángulos a la cadencia real de la Pi (plan 7.1, 7.2 y 7.4).

Port fiel a Python de:
  - camera_track() de src/vehicle_tracking.c (variante «a»), con las mejoras candidatas como opciones;
  - la confirmación de src/vehicle_detector.cpp: umbral 0,15, máximo 16, bus/camión -> coche antes de
    seguir, hits>=2 y puntuación máxima de la pista >=0,25, picos olvidados tras el TTL;
  - la regla del overlay de src/gst_vehicles.c: un resultado vacío no borra marcas vigentes, TTL de 750 ms
    con el reloj del overlay, ancla de camera_vehicle_anchor() (src/vehicle_protocol.h).

Simulación temporal (supuestos; ver «method» en results.json):
  - una sola imagen en vuelo: se analiza el primer fotograma con t >= max(turno, respuesta recibida);
    turnos cada P=360 ms de media (acumulador sin ráfagas);
  - el resultado se usa cuando el reloj del overlay llega a captura+L (L=365 ms medido; también 250 y 450);
  - el overlay procesa cada fotograma D=72 ms después de su captura (auditoría 2.1: decodificador 49 ms
    + Panini/cola 23 ms), así que la edad del ancla al aparecer es L-D;
  - un triángulo por vehículo del resultado vigente en cada fotograma del vídeo.

Pseudo-verdad (build_pseudo_truth): pistas de TODAS las detecciones >=0,25 a 30 Hz, asociación voraz por
IoU>=0,3 contra la caja predicha, sin clase, huecos de hasta 8 fotogramas interpolados y pistas con >=8
detecciones. Es aproximada y procede del mismo detector (YOLOv5n-640 en PC, NO el perfil 320x192 de la Pi).

Uso:
  python tools/track-eval.py --data <carpeta o .zip con <clip>/yolov5n/detections.jsonl>
         [--output comparisons/tracking-evaluation]
  python tools/track-eval.py --self-test
Genera solo JSON/CSV.
"""
import argparse
import copy
import csv
import io
import itertools
import json
import math
import time
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ZIP_PREFIX = 'comparisons/old/all-models_all-frames_6-videos/'
NAMES = ('01_winchester', '02_unity', '03_hornet', '04_dwarf', '05_texana', '06_x5m')
FIT = ('01_winchester', '03_hornet', '05_texana')     # ajuste: clips impares, fijado antes de ver resultados
VAL = ('02_unity', '04_dwarf', '06_x5m')              # validación
W_PX, H_PX = 800, 480                                 # lienzo del overlay (regla de anclaje de evaluate-all-frames.py)
WIDTH_BUCKETS = (('<40', 0, 40), ('40-80', 40, 80), ('>=80', 80, 10 ** 9))

# Constantes del directo (src/)
MAX_TRACKS = 16                      # CAMERA_AI_MAX
TTL_NS = 750_000_000                 # CAMERA_AI_TTL_NS
IOU_GATE = .25                       # vehicle_tracking.c: score=.25f, value>score
THRESHOLD, START_THRESHOLD, CONFIRM_HITS = .15, .25, 2
VMAX = 2.0                           # |vx|,|vy| <= 2 (protocolo)
CLOCK_OFFSET_NS = 10_000_000_000

# Parámetros de las variantes
PRED_CLAMP_BOXES = 1.0               # (b) desplazamiento predicho limitado a +-1 caja: fijado a priori, sin ajustar
SIZE_RATIO = 2.0                     # (c) w y h de detección/pista dentro de [1/2, 2]: fijado a priori
EMA_ALPHA = .5                       # (d) indicado en el plan
TAU_GRID = (.4, .6, .8, 1.0, 1.2, 1.5)  # (c) rejilla de la puerta de centros (en tamaños medios de caja)
FLAGS = 'bcdefg'
FLAG_TEXT = {
    'b': 'IoU contra la caja predicha con la velocidad de la pista y dt real',
    'c': 'puerta alternativa por distancia de centros normalizada cuando el IoU no alcanza',
    'd': 'velocidad suavizada (EMA alfa 0,5) y limitada en cajas/s',
    'e': 'asociación global voraz por coste ordenado',
    'f': 'con la tabla llena, expulsar la pista no confirmada (hits<2) más antigua',
    'g': 'asociación entre clases (extra del plan 7.2; no pedida en la lista a-f)',
}
SCENARIOS = {  # nombre: (P periodo ms, L edad del resultado ms, D retraso hasta el overlay ms)
    'L365': (360, 365, 72),
    'L250': (360, 250, 72),
    'L450': (360, 450, 72),
}
PRIMARY = 'L365'
# Estudio de perfiles (--profiles): detector más rápido desplegado, edad mediana ~295 ms y periodo 333-360 ms
PROFILE_SCENARIOS = {'L295': (345, 295, 72), 'L365': (360, 365, 72)}
PROFILE_PRIMARY = 'L295'
ALL_SCENARIOS = {**SCENARIOS, **PROFILE_SCENARIOS}


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def f32(v):
    return float(np.float32(v))


# ------------------------------------------------------------------ port del directo
class Vehicle:
    """CameraVehicle (src/vehicle_protocol.h)."""
    __slots__ = ('x', 'y', 'w', 'h', 'confidence', 'category', 'track', 'hits', 'vx', 'vy')

    def __init__(self, x, y, w, h, confidence, category):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.confidence, self.category = confidence, category
        self.track = self.hits = 0
        self.vx = self.vy = 0.

    def box(self):
        return (self.x, self.y, self.w, self.h)

    def clone(self):
        return copy.copy(self)


F32 = np.float32
F32_ZERO = F32(0)


def overlap(a, b):
    """overlap() de vehicle_tracking.c (IoU) en float32, operación a operación como en C: con doble
    precisión hay pares en el límite 0,25 que cambian de lado."""
    ax, ay, aw, ah = F32(a[0]), F32(a[1]), F32(a[2]), F32(a[3])
    bx, by, bw, bh = F32(b[0]), F32(b[1]), F32(b[2]), F32(b[3])
    x, y = max(ax, bx), max(ay, by)
    w = max(F32_ZERO, min(ax + aw, bx + bw) - x)
    h = max(F32_ZERO, min(ay + ah, by + bh) - y)
    area = w * h
    return float(area / (aw * ah + bw * bh - area))


def camera_vehicle_anchor(v, age_ns):
    """camera_vehicle_anchor() de vehicle_protocol.h: devuelve (x, y) normalizados."""
    dt = min(age_ns / 1e9, .5)
    dx = max(-v.w * .5, min(v.vx * dt, v.w * .5))
    dy = max(-v.h * .5, min(v.vy * dt, v.h * .5))
    return max(0., min(1., v.x + v.w * .5 + dx)), max(0., min(1., v.y + dy))


class CameraTracks:
    """camera_track(); sin opciones es el código actual línea a línea."""

    def __init__(self, opt=None):
        self.opt = dict(opt or {})
        self.vehicles = [None] * MAX_TRACKS
        self.last_ns = [0] * MAX_TRACKS
        self.next_id = 0
        self.evictions = 0  # solo diagnóstico: altas con la tabla llena

    def reference(self, k, ns):
        t = self.vehicles[k]
        if not self.opt.get('b'):
            return t.box()
        dt = (ns - self.last_ns[k]) / 1e9
        dx = clamp(t.vx * dt, -PRED_CLAMP_BOXES * t.w, PRED_CLAMP_BOXES * t.w)
        dy = clamp(t.vy * dt, -PRED_CLAMP_BOXES * t.h, PRED_CLAMP_BOXES * t.h)
        return (t.x + dx, t.y + dy, t.w, t.h)

    def center_distance(self, r, d):
        """(c) distancia de centros en tamaños medios de caja; None si no pasa la puerta."""
        if not (1 / SIZE_RATIO <= d.w / r[2] <= SIZE_RATIO and 1 / SIZE_RATIO <= d.h / r[3] <= SIZE_RATIO):
            return None
        mw, mh = (r[2] + d.w) * .5, (r[3] + d.h) * .5
        dist = math.hypot((d.x + d.w * .5 - r[0] - r[2] * .5) / mw, (d.y + d.h * .5 - r[1] - r[3] * .5) / mh)
        return dist if dist < self.opt['tau'] else None

    def compatible(self, k, d):
        return self.opt.get('g') or self.vehicles[k].category == d.category

    def create(self, d, ns, used):
        best = -1
        for k in range(MAX_TRACKS):
            if not used[k] and not self.last_ns[k]:
                best = k
                break
        if best < 0:
            self.evictions += 1
        if best < 0 and self.opt.get('f'):
            for k in range(MAX_TRACKS):
                if not used[k] and self.vehicles[k].hits < CONFIRM_HITS and (best < 0 or self.last_ns[k] < self.last_ns[best]):
                    best = k
        if best < 0:
            for k in range(MAX_TRACKS):
                if not used[k] and (best < 0 or self.last_ns[k] < self.last_ns[best]):
                    best = k
        self.next_id = (self.next_id + 1) & 0xffffffff
        if self.next_id == 0:
            self.next_id = 1
        d.track, d.hits, d.vx, d.vy = self.next_id, 1, 0., 0.
        if best >= 0:
            used[best] = True
            self.vehicles[best] = d.clone()
            self.last_ns[best] = ns

    def update(self, k, d, ns, used):
        old = self.vehicles[k]
        d.track = old.track
        d.hits = old.hits + 1 if old.hits < 1000 else 1000
        dt = (ns - self.last_ns[k]) / 1e9
        vx = vy = 0.
        if dt > .001:
            vx = ((d.x + d.w * .5) - (old.x + old.w * .5)) / dt
            vy = ((d.y + d.h * .5) - (old.y + old.h * .5)) / dt
        if self.opt.get('d'):
            lx, ly = self.opt['vlim_x'] * d.w, self.opt['vlim_y'] * d.h
            vx, vy = clamp(vx, -lx, lx), clamp(vy, -ly, ly)
            if old.hits >= 2:  # ya había una medida de velocidad
                vx, vy = EMA_ALPHA * vx + (1 - EMA_ALPHA) * old.vx, EMA_ALPHA * vy + (1 - EMA_ALPHA) * old.vy
        d.vx, d.vy = clamp(vx, -VMAX, VMAX), clamp(vy, -VMAX, VMAX)
        used[k] = True
        self.vehicles[k] = d.clone()
        self.last_ns[k] = ns

    def track(self, vehicles, ns):
        o = self.opt
        used = [False] * MAX_TRACKS
        vehicles = vehicles[:MAX_TRACKS]
        for k in range(MAX_TRACKS):
            if self.last_ns[k] > ns or ns - self.last_ns[k] > TTL_NS:
                self.last_ns[k] = 0
        live = [k for k in range(MAX_TRACKS) if self.last_ns[k]]
        refs = {k: self.reference(k, ns) for k in live}
        if o.get('e'):  # coste: 1-IoU; la puerta de centros siempre después de cualquier IoU
            pairs = []
            for i, d in enumerate(vehicles):
                for k in live:
                    if not self.compatible(k, d):
                        continue
                    value = overlap(refs[k], d.box())
                    if value > IOU_GATE:
                        pairs.append((1 - value, i, k))
                    elif o.get('c'):
                        dist = self.center_distance(refs[k], d)
                        if dist is not None:
                            pairs.append((1 + dist / o['tau'], i, k))
            pairs.sort()
            match = [-1] * len(vehicles)
            taken = [False] * MAX_TRACKS
            for _, i, k in pairs:
                if match[i] < 0 and not taken[k]:
                    match[i], taken[k] = k, True
            for i, d in enumerate(vehicles):
                if match[i] >= 0:
                    self.update(match[i], d, ns, used)
            for i, d in enumerate(vehicles):
                if match[i] < 0:
                    self.create(d, ns, used)
            return
        for d in vehicles:  # orden de detección, como en C
            best, score = -1, IOU_GATE
            for k in live:
                if used[k] or not self.compatible(k, d):
                    continue
                value = overlap(refs[k], d.box())
                if value > score:
                    score, best = value, k
            if best < 0 and o.get('c'):
                nearest = o['tau']
                for k in live:
                    if used[k] or not self.compatible(k, d):
                        continue
                    dist = self.center_distance(refs[k], d)
                    if dist is not None and dist < nearest:
                        nearest, best = dist, k
            if best < 0:
                self.create(d, ns, used)
            else:
                self.update(best, d, ns, used)


class Detector:
    """Parte de seguimiento y confirmación de vehicle_detector.cpp (líneas 142-164)."""

    def __init__(self, opt=None):
        opt = opt or {}
        self.tracks = CameraTracks(opt)
        self.peaks = {}
        self.start = opt.get('start', START_THRESHOLD)  # CAMERA_DETECTOR_START_THRESHOLD
        self.fast = opt.get('fast')                       # CAMERA_DETECTOR_FAST_CONFIRM (None: desactivada)

    def step(self, raw, ns):
        vehicles, categories = [], []
        for x, y, w, h, score, cls in raw:  # raw ya viene ordenado (stable_sort) y con NMS, como yolov5.hpp
            if score < THRESHOLD:
                continue
            vehicles.append(Vehicle(x, y, w, h, score, 2 if cls in (5, 7) else cls))
            categories.append(cls)
            if len(vehicles) == MAX_TRACKS:
                break
        self.tracks.track(vehicles, ns)
        for tid in [t for t, (_, seen) in self.peaks.items() if ns - seen > TTL_NS]:
            del self.peaks[tid]
        confirmed, self.observed = [], []
        for v, cls in zip(vehicles, categories):
            v.category = cls
            peak = max(self.peaks.get(v.track, (0., 0))[0], v.confidence)
            self.peaks[v.track] = (peak, ns)
            self.observed.append((v, peak))  # solo diagnóstico: todas las observaciones seguidas
            if v.hits >= CONFIRM_HITS and peak >= self.start:
                confirmed.append(v)
            elif self.fast is not None and v.confidence >= self.fast:
                # confirmación rápida: la pista conserva hits=1; solo la respuesta lleva hits=2
                quick = v.clone()
                quick.hits = CONFIRM_HITS
                confirmed.append(quick)
        return confirmed


def schedule(frames, period_ns, age_ns, overlay_ns):
    """Fotogramas analizados: una imagen en vuelo, turno medio period_ns, respuesta visible en captura+L-D."""
    out, due, ready = [], frames[0][0], -1
    for fi, (ns, _) in enumerate(frames):
        if ns >= due and ns >= ready:
            out.append(fi)
            due = due + period_ns if due + period_ns > ns else ns + period_ns
            ready = ns + age_ns - overlay_ns
    return out


# ------------------------------------------------------------------ datos
def load_clips(source):
    source = Path(source)
    clips = {}
    archive = zipfile.ZipFile(source) if source.suffix.lower() == '.zip' else None
    for name in NAMES:
        rel = f'{name}/yolov5n/detections.jsonl'
        text = io.TextIOWrapper(archive.open(ZIP_PREFIX + rel), encoding='utf-8') if archive else (source / rel).open(encoding='utf-8')
        frames, confirmed = [], []
        with text:
            for index, line in enumerate(text):
                row = json.loads(line)
                if row['frame_index'] != index:
                    raise RuntimeError(f'{name}: fotogramas desordenados')
                raw = [tuple(f32(v) for v in d['box']) + (f32(d['score']), int(d['class'])) for d in row['raw']]
                # +10 s: en C last_ns==0 significa hueco libre; el reloj monótono real nunca vale 0
                frames.append((CLOCK_OFFSET_NS + int(round(row['time_s'] * 1e9)), raw))
                confirmed.append(row.get('confirmed'))
        clips[name] = {'frames': frames, 'study_confirmed': confirmed}
    return clips


# ------------------------------------------------------------------ pseudo-verdad
def build_pseudo_truth(frames, threshold=.25, iou_min=.3, max_gap=8, min_hits=8):
    """Pistas a 30 Hz de todas las detecciones >=threshold, sin clase: IoU>=iou_min contra la caja predicha
    (velocidad por fotograma suavizada), voraz por IoU descendente; huecos <=max_gap interpolados linealmente;
    se descartan pistas con menos de min_hits detecciones."""
    active, finished = [], []
    for fi, (_, raw) in enumerate(frames):
        dets = [r[:4] for r in raw if r[4] >= threshold]
        preds = []
        for t in active:
            lf, lb = t['obs'][-1]
            g = fi - lf
            preds.append((lb[0] + t['vx'] * g, lb[1] + t['vy'] * g, lb[2], lb[3]))
        pairs = sorted(((overlap(p, d), ti, di) for ti, p in enumerate(preds) for di, d in enumerate(dets)), reverse=True)
        mt, md = set(), set()
        for value, ti, di in pairs:
            if value < iou_min:
                break
            if ti in mt or di in md:
                continue
            mt.add(ti); md.add(di)
            t, d = active[ti], dets[di]
            lf, lb = t['obs'][-1]
            g = fi - lf
            mvx = ((d[0] + d[2] / 2) - (lb[0] + lb[2] / 2)) / g
            mvy = ((d[1] + d[3] / 2) - (lb[1] + lb[3] / 2)) / g
            if len(t['obs']) >= 2:
                mvx, mvy = .5 * mvx + .5 * t['vx'], .5 * mvy + .5 * t['vy']
            t['vx'], t['vy'] = mvx, mvy
            t['obs'].append((fi, d))
        for di, d in enumerate(dets):
            if di not in md:
                active.append({'obs': [(fi, d)], 'vx': 0., 'vy': 0.})
        keep = []
        for t in active:
            (finished if fi - t['obs'][-1][0] > max_gap else keep).append(t)
        active = keep
    finished += active
    kept = sorted((t for t in finished if len(t['obs']) >= min_hits), key=lambda t: t['obs'][0][0])
    per_frame = [dict() for _ in frames]
    meta = []
    for gid, t in enumerate(kept):
        obs = t['obs']
        for (f0, b0), (f1, b1) in zip(obs, obs[1:] + [obs[-1]]):
            span = max(1, f1 - f0)
            for f in range(f0, f1 if f1 > f0 else f0 + 1):
                a = (f - f0) / span
                per_frame[f][gid] = tuple(b0[i] + (b1[i] - b0[i]) * a for i in range(4))
        widths = sorted(b[2] * W_PX for _, b in obs)
        start, end = obs[0][0], obs[-1][0]
        meta.append({'start': start, 'end': end, 'detections': len(obs), 'median_width_px': widths[len(widths) // 2]})
    return per_frame, meta


def bucket(width_px):
    for name, lo, hi in WIDTH_BUCKETS:
        if lo <= width_px < hi:
            return name
    return WIDTH_BUCKETS[-1][0]


# ------------------------------------------------------------------ simulación y métricas de un clip
def simulate_clip(clip, gt, opt, scenario):
    period, age, delay = (v * 1_000_000 for v in ALL_SCENARIOS[scenario])
    hold = int(opt.get('hold_ms', TTL_NS // 1_000_000)) * 1_000_000  # CAMERA_AI_HOLD_MS: solo el dibujo
    frames = clip['frames']
    per_frame, meta = gt
    fps = (len(frames) - 1) / ((frames[-1][0] - frames[0][0]) / 1e9)
    frame_ns = 1e9 / fps
    detector = Detector(opt)
    analysed = schedule(frames, period, age, delay)
    # Por análisis: estado de cada vehículo de pseudo-verdad (observación seguida con IoU>0,3 en ese fotograma)
    identities = [[] for _ in meta]
    breaks = continued = 0
    results, seen_before = [], {}
    for fi in analysed:
        ns = frames[fi][0]
        confirmed = detector.step(frames[fi][1], ns)
        status = {}
        for gid, g in per_frame[fi].items():
            value, v, peak = max(((overlap(o.box(), g), o, p) for o, p in detector.observed), default=(0., None, 0.),
                                 key=lambda item: item[0])
            if value <= .3:
                status[gid] = 'detector_miss'
                continue
            identities[gid].append(v.track)
            if seen_before.get(gid):
                continued += 1
                breaks += v.hits == 1
            if v.hits >= CONFIRM_HITS:
                status[gid] = 'confirmed' if peak >= detector.start else 'low_peak_score'
            elif detector.fast is not None and v.confidence >= detector.fast:
                status[gid] = 'confirmed'
            else:
                status[gid] = 'association_break' if seen_before.get(gid) else 'first_sighting'
        seen_before = {gid: s != 'detector_miss' for gid, s in status.items()}
        results.append((ns, ns + age - delay, confirmed, fi, status))
    present = {b[0]: 0 for b in WIDTH_BUCKETS}; covered = dict(present)
    tracks = [{'first': None} for _ in meta]
    loss = {k: 0 for k in LOSS_KEYS}
    false_frames = false_episodes = triangle_frames = false_stale = 0
    false_prev = set()
    errors, errors_raw = [], []
    current, latest, ri = None, None, 0
    for fi, (ns, _) in enumerate(frames):
        now = ns + delay
        while ri < len(results) and results[ri][1] <= ns:
            captured, _, vehicles, cfi, _ = latest = results[ri]
            ri += 1
            # camera_results_valid + «if(r.count || !s->result.count)s->result=r;»
            if now - captured <= TTL_NS and (vehicles or current is None or not current[1]):
                current = (captured, vehicles, cfi)
        tris = []
        if current is not None and now >= current[0] and now - current[0] <= hold:
            age_frame = ns - current[0] if ns > current[0] else 0
            for v in current[1]:
                if v.hits < CONFIRM_HITS:
                    continue
                ax, ay = camera_vehicle_anchor(v, age_frame)
                tris.append((v.track, ax * W_PX, ay * H_PX, (v.x + v.w * .5) * W_PX, v.y * H_PX, v.box()))
        gts = [(gid, b[0] * W_PX, b[1] * H_PX, b[2] * W_PX, b[3] * H_PX) for gid, b in per_frame[fi].items()]
        triangle_frames += len(tris)
        # regla de anclaje estricta de tools/evaluate-all-frames.py, uno a uno
        pairs = []
        for i, (_, x, y, w, h) in enumerate(gts):
            for j, t in enumerate(tris):
                if x - 2 <= t[1] <= x + w + 2 and abs(t[2] - y) <= max(4., h * .5):
                    pairs.append((abs(t[1] - (x + w / 2)) / w + abs(t[2] - y) / h, i, j))
        pairs.sort()
        used_g, used_t = set(), set()
        for _, i, j in pairs:
            if i in used_g or j in used_t:
                continue
            used_g.add(i); used_t.add(j)
            info = tracks[gts[i][0]]
            if info['first'] is None:
                info['first'] = ns
        for i, g in enumerate(gts):
            b = bucket(g[3])
            present[b] += 1
            covered[b] += i in used_g
            if i not in used_g:  # causa, según el último análisis recibido
                loss['before_first_result' if latest is None else latest[4].get(g[0], 'appeared_after_capture')] += 1
        # «cerca»: ancla a menos de un ancho de caja del centro y de max(8 px, alto) del borde superior
        near = []
        for i, (_, x, y, w, h) in enumerate(gts):
            cx = x + w / 2
            for j, t in enumerate(tris):
                if abs(t[1] - cx) <= w and abs(t[2] - y) <= max(8., h):
                    near.append((math.hypot(t[1] - cx, t[2] - y), i, j))
        near.sort()
        near_t = {j for _, _, j in near}
        used_g, used_t = set(), set()
        for dist, i, j in near:
            if i in used_g or j in used_t:
                continue
            used_g.add(i); used_t.add(j)
            _, x, y, w, h = gts[i]
            errors.append(dist)
            errors_raw.append(math.hypot(tris[j][3] - (x + w / 2), tris[j][4] - y))
        false_now = set()
        for j, t in enumerate(tris):
            if j not in near_t:
                false_frames += 1
                # «caducada»: en el fotograma analizado sí había un vehículo de pseudo-verdad bajo la caja
                false_stale += any(overlap(t[5], b) > .3 for b in per_frame[current[2]].values())
                false_now.add(t[0])
                if t[0] not in false_prev:
                    false_episodes += 1
        false_prev = false_now
    long_tracks = [(m, t) for m, t in zip(meta, tracks) if (m['end'] - m['start'] + 1) / fps >= 1.0]
    never = {b[0]: [0, 0] for b in WIDTH_BUCKETS}
    for m, t in long_tracks:
        b = bucket(m['median_width_px'])
        never[b][0] += 1
        never[b][1] += t['first'] is None
    marked = [(m, t) for m, t in long_tracks if t['first'] is not None]
    delays = [(t['first'] - frames[m['start']][0]) / 1e6 for m, t in marked]
    # identidad en el seguidor: cambios del id de pista a lo largo de los análisis en que se observa el vehículo
    followed = [ids for m, ids in zip(meta, identities) if (m['end'] - m['start'] + 1) / fps >= 1.0 and len(ids) >= 2]
    switches = sum(sum(a != b for a, b in zip(ids, ids[1:])) for ids in followed)
    minutes = len(frames) * frame_ns / 6e10
    return {
        'frames': len(frames), 'minutes': minutes, 'fps': fps,
        'analyses': len(analysed), 'mean_period_ms': (frames[analysed[-1]][0] - frames[analysed[0]][0]) / 1e6 / max(1, len(analysed) - 1),
        'present': present, 'covered': covered,
        'tracks_1s': len(long_tracks), 'never_1s': sum(v[1] for v in never.values()), 'never_by_bucket': never,
        'marked_1s': len(marked), 'delays_ms': delays, 'switches': switches, 'followed_1s': len(followed),
        'association_breaks': breaks, 'association_continued': continued, 'loss': loss,
        'evictions': detector.tracks.evictions,
        'false_frames': false_frames, 'false_seconds': false_frames * frame_ns / 1e9, 'false_episodes': false_episodes,
        'false_stale_seconds': false_stale * frame_ns / 1e9, 'false_unsupported_seconds': (false_frames - false_stale) * frame_ns / 1e9,
        'triangle_frames': triangle_frames, 'errors': errors, 'errors_raw': errors_raw,
    }


LOSS_KEYS = ('appeared_after_capture', 'detector_miss', 'first_sighting', 'association_break', 'low_peak_score',
             'confirmed', 'before_first_result')


# ------------------------------------------------------------------ agregación y bootstrap
METRICS = (  # nombre, numerador(clip), denominador(clip), escala
    ('coverage_pct', lambda c: sum(c['covered'].values()), lambda c: sum(c['present'].values()), 100),
    ('coverage_lt40_pct', lambda c: c['covered']['<40'], lambda c: c['present']['<40'], 100),
    ('coverage_40_80_pct', lambda c: c['covered']['40-80'], lambda c: c['present']['40-80'], 100),
    ('coverage_ge80_pct', lambda c: c['covered']['>=80'], lambda c: c['present']['>=80'], 100),
    ('never_marked_1s_pct', lambda c: c['never_1s'], lambda c: c['tracks_1s'], 100),
    ('first_triangle_delay_ms', lambda c: sum(c['delays_ms']), lambda c: len(c['delays_ms']), 1),
    ('id_switches_per_track', lambda c: c['switches'], lambda c: c['followed_1s'], 1),
    ('association_break_pct', lambda c: c['association_breaks'], lambda c: c['association_continued'], 100),
    ('false_episodes_per_min', lambda c: c['false_episodes'], lambda c: c['minutes'], 1),
    ('false_seconds_per_min', lambda c: c['false_seconds'], lambda c: c['minutes'], 1),
    ('false_stale_seconds_per_min', lambda c: c['false_stale_seconds'], lambda c: c['minutes'], 1),
    ('false_unsupported_seconds_per_min', lambda c: c['false_unsupported_seconds'], lambda c: c['minutes'], 1),
    ('anchor_error_px', lambda c: sum(c['errors']), lambda c: len(c['errors']), 1),
    ('anchor_error_no_prediction_px', lambda c: sum(c['errors_raw']), lambda c: len(c['errors_raw']), 1),
)


def ratio(num, den):
    return float(num / den) if den else None


def summarize(per_clip, base_clip, subset, weights):
    """Valor agregado (razón de sumas), IC bootstrap por clip y diferencia emparejada frente a «a»."""
    names = [n for n in NAMES if n in subset]
    out = {}
    w = weights[len(names)]
    for metric, num, den, scale in METRICS:
        n = np.array([num(per_clip[c]) for c in names], float); d = np.array([den(per_clip[c]) for c in names], float)
        bn = np.array([num(base_clip[c]) for c in names], float); bd = np.array([den(base_clip[c]) for c in names], float)
        value = ratio(n.sum(), d.sum())
        with np.errstate(invalid='ignore', divide='ignore'):
            draws = (w @ n) / (w @ d) * scale
            base = (w @ bn) / (w @ bd) * scale
        diff = draws - base
        entry = {'value': None if value is None else round(value * scale, 3),
                 'ci95': [round(float(np.nanpercentile(draws, 2.5)), 3), round(float(np.nanpercentile(draws, 97.5)), 3)],
                 'diff_vs_a': None if value is None else round(value * scale - ratio(bn.sum(), bd.sum()) * scale, 3),
                 'diff_ci95': [round(float(np.nanpercentile(diff, 2.5)), 3), round(float(np.nanpercentile(diff, 97.5)), 3)]}
        out[metric] = entry
    errors = np.concatenate([per_clip[c]['errors'] for c in names]) if names else np.array([])
    delays = np.concatenate([per_clip[c]['delays_ms'] for c in names]) if names else np.array([])
    out['anchor_error_px_median'] = round(float(np.median(errors)), 2) if len(errors) else None
    out['anchor_error_px_p90'] = round(float(np.percentile(errors, 90)), 2) if len(errors) else None
    out['first_triangle_delay_ms_median'] = round(float(np.median(delays)), 1) if len(delays) else None
    return out


def loss_budget(per_clip):
    present = sum(sum(c['present'].values()) for c in per_clip.values())
    covered = sum(sum(c['covered'].values()) for c in per_clip.values())
    out = {'covered': round(100 * covered / present, 2)}
    for k in LOSS_KEYS:
        out[k] = round(100 * sum(c['loss'][k] for c in per_clip.values()) / present, 2)
    return out


def variant_name(flags):
    return 'a' if not flags else '+'.join(flags)


def all_variants():
    out = []
    for r in range(len(FLAGS) + 1):
        out += [''.join(c) for c in itertools.combinations(FLAGS, r)]
    return out


# ------------------------------------------------------------------ ajuste de parámetros (solo clips FIT)
def fit_kinematics(clips, gts):
    """Cinemática de la pseudo-verdad muestreada a la cadencia de la Pi, solo en los clips FIT: límites de
    velocidad de (d) y, como diagnóstico, la separación de la puerta de centros (Youden)."""
    period, age, delay = (v * 1_000_000 for v in SCENARIOS[PRIMARY])
    speeds_x, speeds_y = [], []
    positives, negatives = [], []
    same_total = same_iou_fail_last = same_iou_fail_pred = 0
    for name in FIT:
        frames = clips[name]['frames']
        per_frame = gts[name][0]
        idx = schedule(frames, period, age, delay)
        for a in range(1, len(idx)):
            f0, f1 = idx[a - 1], idx[a]
            dt = (frames[f1][0] - frames[f0][0]) / 1e9
            for gid, b1 in per_frame[f1].items():
                b0 = per_frame[f0].get(gid)
                if b0 is None:
                    continue
                mw, mh = (b0[2] + b1[2]) / 2, (b0[3] + b1[3]) / 2
                speeds_x.append(abs((b1[0] + b1[2] / 2) - (b0[0] + b0[2] / 2)) / mw / dt)
                speeds_y.append(abs((b1[1] + b1[3] / 2) - (b0[1] + b0[3] / 2)) / mh / dt)
        for a in range(1, len(idx) - 2):
            fp, fk = idx[a - 1], idx[a]
            for j in (1, 2):  # siguiente análisis o el posterior (un fallo dentro del TTL)
                fn = idx[a + j]
                if (frames[fn][0] - frames[fk][0]) > TTL_NS:
                    continue
                dtp = (frames[fk][0] - frames[fp][0]) / 1e9
                dtn = (frames[fn][0] - frames[fk][0]) / 1e9
                for gid, bk in per_frame[fk].items():
                    bp = per_frame[fp].get(gid)
                    if bp is None:
                        continue
                    vx = clamp(((bk[0] + bk[2] / 2) - (bp[0] + bp[2] / 2)) / dtp, -VMAX, VMAX)
                    vy = clamp(((bk[1] + bk[3] / 2) - (bp[1] + bp[3] / 2)) / dtp, -VMAX, VMAX)
                    ref = (bk[0] + clamp(vx * dtn, -bk[2], bk[2]), bk[1] + clamp(vy * dtn, -bk[3], bk[3]), bk[2], bk[3])
                    probe = CameraTracks({'tau': 1e9})
                    for gid2, bn in per_frame[fn].items():
                        det = Vehicle(*bn, 1., 2)
                        if gid2 == gid:
                            same_total += 1
                            same_iou_fail_last += overlap(bk, bn) <= IOU_GATE
                        value = overlap(ref, bn)
                        if value > IOU_GATE:
                            continue
                        if gid2 == gid:
                            same_iou_fail_pred += 1
                        dist = probe.center_distance(ref, det)
                        if dist is None:
                            continue
                        (positives if gid2 == gid else negatives).append(dist)
    pos, neg = np.array(positives), np.array(negatives)
    grid = np.round(np.arange(.10, 3.001, .05), 2)
    best = max(grid, key=lambda t: (float((pos < t).mean() - (neg < t).mean()), -t))
    def tpr_fpr(t):
        return {'tpr': round(float((pos < t).mean()), 4), 'fpr_other_vehicles': round(float((neg < t).mean()), 4)}
    return {
        'clips': list(FIT),
        'vlim_x_boxes_per_s': math.ceil(np.percentile(speeds_x, 99) * 4) / 4,
        'vlim_y_boxes_per_s': math.ceil(np.percentile(speeds_y, 99) * 4) / 4,
        'vlim_rule': 'percentil 99 de |velocidad del centro| en cajas/s por eje entre análisis consecutivos (clips FIT), '
                     'redondeado hacia arriba a 0,25',
        'center_gate_youden_diagnostic': {
            'tau_youden': float(best), **tpr_fpr(best),
            'by_tau': {str(t): tpr_fpr(t) for t in TAU_GRID},
            'definition': 'entre pares que no pasan IoU>0,25 contra la caja predicha: mismo vehículo de pseudo-verdad '
                          '(positivos) frente a otros vehículos del mismo fotograma con tamaño compatible (negativos), al '
                          'siguiente análisis o al posterior dentro del TTL. Solo diagnóstico: tau se elige con tau_sweep',
            'positives': int(len(pos)), 'negatives': int(len(neg)),
            'positive_distance_percentiles_50_90_99': [round(float(np.percentile(pos, q)), 3) for q in (50, 90, 99)] if len(pos) else None,
        },
        'same_vehicle_pairs': same_total,
        'same_vehicle_iou_fail_last_box_pct': round(100 * same_iou_fail_last / same_total, 2),
        'same_vehicle_iou_fail_predicted_box_pct': round(100 * same_iou_fail_pred / same_total, 2),
        'speed_percentiles_x_50_90_99': [round(float(np.percentile(speeds_x, q)), 3) for q in (50, 90, 99)],
        'speed_percentiles_y_50_90_99': [round(float(np.percentile(speeds_y, q)), 3) for q in (50, 90, 99)],
        'velocity_samples': len(speeds_x),
    }


FALSE_METRICS = ('false_episodes_per_min', 'false_seconds_per_min')


def admissible(fit_summary, base_summary):
    """Sin aumento de falsas marcas en FIT: ni más de un 10 % sobre «a» ni un aumento consistente (IC 95 %
    bootstrap emparejado de la diferencia entero por encima de 0), en episodios/min y en segundos/min."""
    return all(fit_summary[m]['value'] <= 1.10 * base_summary[m]['value'] and fit_summary[m]['diff_ci95'][0] <= 0
               for m in FALSE_METRICS)


def tau_sweep(clips, gts, base_params, weights):
    """tau de la puerta de centros, por separado para asociación secuencial (b+c) y global (b+c+e), en los clips
    FIT del escenario principal: la de mayor cobertura entre las admisibles (ver admissible); entre las que
    quedan a menos de 0,25 puntos de la mejor, la menor tau. Los clips VAL no intervienen."""
    base = {n: simulate_clip(clips[n], gts[n], {}, PRIMARY) for n in FIT}
    base_summary = summarize(base, base, FIT, weights)
    out = {'rule': tau_sweep.__doc__.strip(), 'admissible_rule': admissible.__doc__.strip()}
    for mode, flags in (('sequential', 'bc'), ('global', 'bce')):
        rows = []
        for tau in TAU_GRID:
            opt = dict(base_params, tau=tau, **{f: 1 for f in flags})
            summary = summarize({n: simulate_clip(clips[n], gts[n], opt, PRIMARY) for n in FIT}, base, FIT, weights)
            rows.append({'tau': tau, 'admissible': admissible(summary, base_summary),
                         **{m: {k: summary[m][k] for k in ('value', 'diff_vs_a', 'diff_ci95')}
                            for m in ('coverage_pct',) + FALSE_METRICS}})
        ok = [r for r in rows if r['admissible']]
        if ok:
            top = max(r['coverage_pct']['value'] for r in ok)
            tau = min(r['tau'] for r in ok if r['coverage_pct']['value'] >= top - .25)
        else:
            tau = min(TAU_GRID)
        out[mode] = {'variant': variant_name(flags), 'tau': tau, 'grid': rows}
    return out


# ------------------------------------------------------------------ fidelidad
def check_study_fidelity(clips):
    """Seguidor «a» a TODOS los fotogramas frente al campo 'confirmed' del estudio antiguo
    (generado en PC a 30 Hz con el seguidor del directo, umbrales 0,15/0,25)."""
    out = {}
    for name in NAMES:
        frames, study = clips[name]['frames'], clips[name]['study_confirmed']
        detector = Detector()
        same = differ = 0
        first = None
        for fi, (ns, raw) in enumerate(frames):
            mine = detector.step(raw, ns)
            ref = study[fi] or []
            ok = len(mine) == len(ref) and all(
                v.track == r['id'] and all(abs(a - b) < 1e-5 for a, b in zip(v.box(), r['box'])) and
                abs(v.vx - r['vx']) < 1e-3 and abs(v.vy - r['vy']) < 1e-3 for v, r in zip(mine, ref))
            same += ok
            differ += not ok
            if not ok and first is None:
                first = fi
        out[name] = {'frames_identical': same, 'frames_different': differ, 'first_difference_frame': first}
    return out


def self_test():
    """Mismas comprobaciones que tests/test_vehicles.c sobre el port, más casos de las variantes."""
    ns = 10_000_000_000
    t = CameraTracks()
    cars = [Vehicle(.1, .2, .2, .2, .9, 2), Vehicle(.6, .2, .2, .2, .8, 2)]
    t.track(cars, ns)
    assert cars[0].hits == 1 and cars[1].hits == 1 and cars[0].vx == 0
    first, second = cars[0].track, cars[1].track
    assert first and second and first != second
    cars[0].x += .01; cars[1].x -= .01
    t.track(cars, ns + 200_000_000)
    assert cars[0].track == first and cars[1].track == second and cars[0].hits == 2 and cars[1].hits == 2
    assert abs(cars[0].vx - .05) < 1e-4 and abs(cars[1].vx + .05) < 1e-4
    x, y = camera_vehicle_anchor(cars[0], 200_000_000)
    assert abs(x - .22) < 1e-4 and abs(y - .2) < 1e-4
    fast = cars[0].clone(); fast.vx, fast.vy = 2, -2
    x, y = camera_vehicle_anchor(fast, 700_000_000)
    assert abs(x - .31) < 1e-4 and abs(y - .1) < 1e-4
    t.track(cars, ns + 2_000_000_000)
    assert cars[0].track != first and cars[1].track != second and cars[0].track != cars[1].track
    assert cars[0].hits == 1 and cars[0].vx == 0
    # (b) un coche que avanza 0,5 y luego 0,65 cajas por análisis: el segundo paso tiene IoU 0,21 contra la
    #     última caja (falla) y 0,74 contra la predicha
    for opt, keeps in (({}, False), ({'b': 1}, True)):
        t = CameraTracks(opt); ids = []
        for k, x in enumerate((.1, .2, .33)):
            v = Vehicle(x, .3, .2, .2, .9, 2); t.track([v], ns + k * 360_000_000); ids.append(v.track)
        assert ids[0] == ids[1] and (ids[1] == ids[2]) == keeps, (opt, ids)
    # (c) salto de centro de 0,8 cajas sin velocidad previa: solo la puerta de centros lo asocia
    t = CameraTracks({'c': 1, 'tau': 1.0})
    a = Vehicle(.1, .3, .2, .2, .9, 2); t.track([a], ns)
    b = Vehicle(.26, .3, .2, .2, .9, 2); t.track([b], ns + 360_000_000)
    assert b.track == a.track and b.hits == 2
    # (e) la detección peor ordenada no roba la pista: global por coste
    for opt, expect in (({}, False), ({'e': 1}, True)):
        t = CameraTracks(opt)
        p = Vehicle(.30, .3, .2, .2, .9, 2); t.track([p], ns)
        weak = Vehicle(.40, .3, .2, .2, .9, 2); good = Vehicle(.31, .3, .2, .2, .9, 2)
        t.track([weak, good], ns + 100_000_000)
        assert (good.track == p.track) == expect, opt
    # (f) con la tabla llena se conserva la pista confirmada más antigua
    for opt, kept in (({}, False), ({'f': 1}, True)):
        t = CameraTracks(opt)
        old = Vehicle(.0, .0, .05, .05, .9, 2); t.track([old], ns); t.track([old], ns + 1)
        oldest_id = old.track
        others = [Vehicle(.06 * (k % 15) + .06, .5 + .06 * (k // 15), .05, .05, .9, 2) for k in range(15)]
        t.track(others, ns + 2)
        t.track([Vehicle(.9, .9, .05, .05, .9, 2)], ns + 3)
        assert (oldest_id in [v.track for v in t.vehicles if v is not None and t.last_ns[t.vehicles.index(v)]]) == kept, opt
    # (d) EMA: segunda medida promedia con la primera
    t = CameraTracks({'d': 1, 'vlim_x': 100, 'vlim_y': 100})
    v = Vehicle(.1, .3, .2, .2, .9, 2); t.track([v], ns)
    v = Vehicle(.15, .3, .2, .2, .9, 2); t.track([v], ns + 500_000_000); assert abs(v.vx - .1) < 1e-6
    v = Vehicle(.25, .3, .2, .2, .9, 2); t.track([v], ns + 1_000_000_000); assert abs(v.vx - .15) < 1e-6
    # (d) límite: 1 caja/s con w=0,2 -> |vx|<=0,2
    t = CameraTracks({'d': 1, 'vlim_x': 1.0, 'vlim_y': 1.0})
    v = Vehicle(.1, .3, .2, .2, .9, 2); t.track([v], ns)
    v = Vehicle(.2, .3, .2, .2, .9, 2); t.track([v], ns + 100_000_000); assert abs(v.vx - .2) < 1e-6
    # Casos propuestos para tests/test_vehicles.c con la variante recomendada b+c+e (tau 0,6)
    rec = {'b': 1, 'c': 1, 'e': 1, 'tau': .6}
    for opt, same in (({}, False), ({'b': 1}, False), (rec, True)):  # diagonal de 0,4 cajas: IoU 0,22, d 0,566
        t = CameraTracks(opt)
        a = Vehicle(.1, .3, .2, .2, .9, 2); t.track([a], ns)
        b = Vehicle(.18, .38, .2, .2, .9, 2); t.track([b], ns + 360_000_000)
        assert (b.track == a.track) == same and b.hits == (2 if same else 1), opt
        if same:
            assert abs(b.vx - .08 / .36) < 1e-5 and abs(b.vy - .08 / .36) < 1e-5
    t = CameraTracks(rec)  # diagonal de 0,5 cajas: d 0,707 >= 0,6, pista nueva
    a = Vehicle(.1, .3, .2, .2, .9, 2); t.track([a], ns)
    b = Vehicle(.2, .4, .2, .2, .9, 2); t.track([b], ns + 360_000_000); assert b.track != a.track
    t = CameraTracks(rec)  # tamaño x2,5: la puerta de centros lo rechaza
    a = Vehicle(.1, .3, .1, .1, .9, 2); t.track([a], ns)
    b = Vehicle(.05, .25, .25, .25, .9, 2); t.track([b], ns + 360_000_000); assert overlap(a.box(), b.box()) <= .25 and b.track != a.track
    t = CameraTracks(rec)  # un par por IoU gana a uno por centros aunque la detección vaya después
    a = Vehicle(.1, .3, .2, .2, .9, 2); t.track([a], ns)
    near_c = Vehicle(.18, .38, .2, .2, .9, 2); good = Vehicle(.12, .3, .2, .2, .9, 2)
    t.track([near_c, good], ns + 100_000_000); assert good.track == a.track and near_c.track != a.track and near_c.hits == 1
    for x, same in ((.3, True), (.45, False)):  # predicción limitada a 1 caja: vx=2 y dt=0,7 s -> x+0,2
        t = CameraTracks(rec)
        a = Vehicle(.1, .3, .2, .2, .9, 2); t.track([a], ns)
        t.vehicles[0].vx = 2.
        b = Vehicle(x, .3, .2, .2, .9, 2); t.track([b], ns + 700_000_000)
        assert (b.track == a.track) == same, x
    for opt, kept in (({}, False), (rec, True)):  # tabla llena: una alta anterior en la lista no roba la pista más antigua
        t = CameraTracks(opt); ids = []
        for k in range(16):
            v = Vehicle(.06 * (k % 8), .1 + .3 * (k // 8), .05, .05, .9, 2); t.track([v], ns + k * 1_000_000); ids.append(v.track)
        far, again = Vehicle(.9, .9, .05, .05, .9, 2), Vehicle(0., .1, .05, .05, .9, 2)
        t.track([far, again], ns + 100_000_000)
        assert (again.track == ids[0] and again.hits == 2) == kept and far.track != ids[0], opt
    t = CameraTracks(rec)  # coche y moto no se asocian
    a = Vehicle(.1, .3, .2, .2, .9, 2); t.track([a], ns)
    b = Vehicle(.1, .3, .2, .2, .9, 3); t.track([b], ns + 100_000_000); assert b.track != a.track
    # Detector: bus/camión siguen como coche, pero se publica la clase original; pico >=0,25 y hits>=2
    d = Detector()
    assert d.step([(.1, .1, .2, .2, .3, 7)], ns) == []
    out = d.step([(.1, .1, .2, .2, .2, 5)], ns + 300_000_000)
    assert len(out) == 1 and out[0].category == 5 and out[0].hits == 2
    d = Detector()
    d.step([(.1, .1, .2, .2, .2, 2)], ns)
    assert d.step([(.1, .1, .2, .2, .2, 2)], ns + 300_000_000) == []
    print('track-eval: autotest correcto')


# ------------------------------------------------------------------ ejecución
GT_ALTERNATIVES = {'strict': (('max_gap', 4), ('min_hits', 15)), 'loose': (('max_gap', 12), ('min_hits', 5))}


def run_variant(clips, gts, flags, params, scenario):
    opt = dict(params, **{f: 1 for f in flags})
    opt['tau'] = params['tau_global'] if 'e' in flags else params['tau_sequential']
    return {n: simulate_clip(clips[n], gts[n], opt, scenario) for n in NAMES}


def choose(rows, base, statistical=True):
    """En los clips FIT del escenario principal y solo con opciones b-f (lista pedida): entre las variantes
    admisibles (ver admissible), máxima cobertura; entre las que quedan a menos de 0,5 puntos de la mejor, la
    de menos opciones (y luego más cobertura). statistical=False reproduce la regla de la primera pasada
    (solo el límite del 10 %)."""
    def ok_row(r):
        if statistical:
            return admissible(r['fit'], base['fit'])
        return all(r['fit'][m]['value'] <= 1.10 * base['fit'][m]['value'] for m in FALSE_METRICS)
    ok = [r for r in rows if ok_row(r)]
    top = max(r['fit']['coverage_pct']['value'] for r in ok)
    near = [r for r in ok if r['fit']['coverage_pct']['value'] >= top - .5]
    return min(near, key=lambda r: (len(r['flags']), -r['fit']['coverage_pct']['value'])), len(ok)


PROFILE_FAST = (None, .5, .6, .7)          # CAMERA_DETECTOR_FAST_CONFIRM
PROFILE_HOLD = (750, 1000, 1250)           # CAMERA_AI_HOLD_MS
PROFILE_LIMIT = 1.25                       # «sin disparar»: como mucho +25 % de falsas marcas en FIT


def profile_name(fast, hold):
    return f"F={'off' if fast is None else fast}|H={hold}"


def median_boot(per_clip, base_clip, names, w, key='delays_ms'):
    """Mediana agregada con bootstrap por clip y diferencia emparejada frente a la base."""
    arrays = [np.asarray(per_clip[c][key], float) for c in names]
    barrays = [np.asarray(base_clip[c][key], float) for c in names]
    draws, diffs = [], []
    for row in w.astype(int):
        a = np.concatenate([np.tile(x, k) for x, k in zip(arrays, row) if k]) if row.any() else np.array([])
        b = np.concatenate([np.tile(x, k) for x, k in zip(barrays, row) if k]) if row.any() else np.array([])
        if len(a) and len(b):
            draws.append(np.median(a)); diffs.append(np.median(a) - np.median(b))
    value = float(np.median(np.concatenate(arrays))); base = float(np.median(np.concatenate(barrays)))
    return {'value': round(value, 1), 'ci95': [round(float(np.percentile(draws, q)), 1) for q in (2.5, 97.5)],
            'diff_vs_a': round(value - base, 1), 'diff_ci95': [round(float(np.percentile(diffs, q)), 1) for q in (2.5, 97.5)]}


def run_profiles(a, clips, gts, weights):
    """Sobre b+c+e (tau 0,6, alta 0,25): confirmación rápida F y permanencia de dibujo H, por separado y combinadas."""
    base_params = json.loads((a.output / 'results.json').read_text(encoding='utf-8'))['fitted_parameters']['used']
    opt0 = {'b': 1, 'c': 1, 'e': 1, 'tau': base_params['tau_global'], 'start': START_THRESHOLD}
    subsets = {'all': NAMES, 'fit': FIT, 'val': VAL}
    raw, table = {}, {}
    for scenario in PROFILE_SCENARIOS:
        for fast in PROFILE_FAST:
            for hold in PROFILE_HOLD:
                opt = dict(opt0, fast=fast, hold_ms=hold)
                raw[(scenario, fast, hold)] = {n: simulate_clip(clips[n], gts[n], opt, scenario) for n in NAMES}
        for fast in PROFILE_FAST:
            for hold in PROFILE_HOLD:
                res, base = raw[(scenario, fast, hold)], raw[(scenario, None, 750)]
                table[(scenario, fast, hold)] = {
                    sub: summarize(res, base, members, weights) | {
                        'first_triangle_delay_median_ms': median_boot(res, base, members, weights[len(members)])}
                    for sub, members in subsets.items()}
    base_fit = table[(PROFILE_PRIMARY, None, 750)]['fit']
    def ok(t):
        return all(t[m]['value'] <= PROFILE_LIMIT * base_fit[m]['value'] for m in FALSE_METRICS)
    pairs = [(f, h) for f in PROFILE_FAST[1:] for h in PROFILE_HOLD[1:]]
    admissible = [k for k in pairs if ok(table[(PROFILE_PRIMARY,) + k]['fit'])]
    chosen = max(admissible, key=lambda k: table[(PROFILE_PRIMARY,) + k]['fit']['coverage_pct']['value']) if admissible else None
    def worst_increase(k):  # mayor aumento relativo de falsas marcas en FIT
        t = table[(PROFILE_PRIMARY,) + k]['fit']
        return max(t[m]['value'] / base_fit[m]['value'] - 1 for m in FALSE_METRICS)
    least = min(pairs, key=worst_increase)
    metrics = ('coverage_pct', 'coverage_lt40_pct', 'coverage_40_80_pct', 'coverage_ge80_pct', 'never_marked_1s_pct',
               'first_triangle_delay_ms', 'first_triangle_delay_median_ms', 'false_episodes_per_min',
               'false_seconds_per_min', 'false_stale_seconds_per_min', 'anchor_error_px')
    out = a.output
    with (out / 'profiles.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scenario', 'fast_confirm', 'hold_ms', 'subset', 'metric', 'value', 'ci95_low', 'ci95_high',
                    'diff_vs_bce', 'diff_ci95_low', 'diff_ci95_high'])
        for (scenario, fast, hold), subs in table.items():
            for sub, t in subs.items():
                for m in metrics:
                    e = t[m]
                    w.writerow([scenario, 'off' if fast is None else fast, hold, sub, m, e['value'], *e['ci95'],
                                e['diff_vs_a'], *e['diff_ci95']])
    with (out / 'profiles_tradeoff.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scenario', 'fast_confirm', 'hold_ms', 'subset', 'coverage_pct', 'd_coverage_pp', 'd_coverage_ci95',
                    'false_episodes_per_min', 'd_false_episodes', 'false_seconds_per_min', 'd_false_seconds',
                    'd_false_seconds_ci95', 'delay_mean_ms', 'delay_median_ms', 'never_marked_1s_pct', 'anchor_error_px',
                    'pp_per_false_second'])
        for (scenario, fast, hold), subs in table.items():
            for sub in ('all', 'fit', 'val'):
                t = subs[sub]
                dfs = t['false_seconds_per_min']['diff_vs_a']
                w.writerow([scenario, 'off' if fast is None else fast, hold, sub, t['coverage_pct']['value'],
                            t['coverage_pct']['diff_vs_a'], t['coverage_pct']['diff_ci95'],
                            t['false_episodes_per_min']['value'], t['false_episodes_per_min']['diff_vs_a'],
                            t['false_seconds_per_min']['value'], dfs, t['false_seconds_per_min']['diff_ci95'],
                            t['first_triangle_delay_ms']['value'], t['first_triangle_delay_median_ms']['value'],
                            t['never_marked_1s_pct']['value'], t['anchor_error_px']['value'],
                            round(t['coverage_pct']['diff_vs_a'] / dfs, 2) if dfs and dfs > 0 else None])
    summary = {
        'tool': 'tools/track-eval.py --profiles',
        'base': 'b+c+e desplegado (tau %.1f), alta 0,25, sin confirmación rápida, permanencia 750 ms' % opt0['tau'],
        'scenarios': {k: {'period_ms': v[0], 'result_age_ms': v[1], 'overlay_delay_ms': v[2]} for k, v in PROFILE_SCENARIOS.items()},
        'primary_scenario': PROFILE_PRIMARY,
        'fast_confirm_rule': 'además de hits>=2 y pico>=0,25, se publica en la primera observación una detección con '
                             'score>=F; la pista conserva hits=1 y solo la respuesta lleva hits=2',
        'hold_rule': 'el overlay dibuja el resultado vigente mientras ahora-captura <= H; TTL del seguidor y validación '
                     'de recepción siguen en 750 ms; el ancla sigue limitada a 0,5 s y media caja',
        'selection_rule': f'entre F en {{0,5, 0,6, 0,7}} y H en {{1000, 1250}}, en los clips FIT del escenario '
                          f'{PROFILE_PRIMARY}: máxima cobertura sin superar en más de un {round((PROFILE_LIMIT - 1) * 100)} % '
                          'los episodios ni los segundos de falsa marca de la base; VAL solo para comprobar',
        'admissible_pairs': [profile_name(*k) for k in admissible],
        'chosen_pair': profile_name(*chosen) if chosen else None,
        'least_false_increase_pair': {'pair': profile_name(*least), 'fit_worst_relative_increase_pct':
                                      round(100 * worst_increase(least), 1)},
        'results': {s_: {profile_name(f, h): {sub: {m: table[(s_, f, h)][sub][m] for m in metrics}
                                              for sub in ('all', 'fit', 'val')}
                         for f in PROFILE_FAST for h in PROFILE_HOLD} for s_ in PROFILE_SCENARIOS},
        'limitations': [
            'detecciones YOLOv5n-640 en PC, no el perfil de la Pi: con 320x192 (o con la franja ROI) las puntuaciones son '
            'otras, así que F=0,5-0,7 no equivale a la misma proporción de detecciones; la franja ROI no se puede simular '
            'con estos datos',
            'pseudo-verdad del mismo detector (>=0,25 a 30 Hz); las falsas marcas son aproximadas',
            'periodo fijo de 345 ms con L=295 y 360 ms con L=365, D=72 ms supuesto, vídeo a 30 fps',
        ],
    }
    (out / 'profiles.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'admissible': summary['admissible_pairs'], 'chosen': summary['chosen_pair']}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data', type=Path)
    p.add_argument('--output', type=Path, default=ROOT / 'comparisons/tracking-evaluation')
    p.add_argument('--bootstrap', type=int, default=2000)
    p.add_argument('--seed', type=int, default=20261024)
    p.add_argument('--self-test', action='store_true')
    p.add_argument('--profiles', action='store_true', help='estudio de confirmación rápida y permanencia sobre b+c+e')
    a = p.parse_args()
    self_test()
    if a.self_test:
        return
    if a.data is None:
        p.error('--data es obligatorio')
    started = time.time()
    clips = load_clips(a.data)
    gts = {n: build_pseudo_truth(clips[n]['frames']) for n in NAMES}
    if a.profiles:
        rng = np.random.default_rng(a.seed)
        weights = {k: np.stack([np.bincount(rng.integers(0, k, k), minlength=k) for _ in range(a.bootstrap)]).astype(float)
                   for k in (3, 6)}
        run_profiles(a, clips, gts, weights)
        return
    fidelity = check_study_fidelity(clips)
    print('fidelidad frente al estudio a 30 Hz:', json.dumps(fidelity), flush=True)
    kinematics = fit_kinematics(clips, gts)
    base_params = {'vlim_x': kinematics['vlim_x_boxes_per_s'], 'vlim_y': kinematics['vlim_y_boxes_per_s']}
    rng = np.random.default_rng(a.seed)
    weights = {k: np.stack([np.bincount(rng.integers(0, k, k), minlength=k) for _ in range(a.bootstrap)]).astype(float)
               for k in (3, 6)}
    sweep = tau_sweep(clips, gts, base_params, weights)
    run_params = dict(base_params, tau_sequential=sweep['sequential']['tau'], tau_global=sweep['global']['tau'])
    print('parámetros (solo FIT):', json.dumps(run_params), flush=True)
    raw = {}
    for scenario in SCENARIOS:
        for flags in all_variants():
            raw[(scenario, flags, 'main')] = run_variant(clips, gts, flags, run_params, scenario)
        print(f'{scenario}: {len(all_variants())} variantes ({time.time() - started:.0f} s)', flush=True)
    subsets = {'all': NAMES, 'fit': FIT, 'val': VAL}
    table = {}

    def tabulate():
        for key_, res in raw.items():
            if key_ not in table:
                table[key_] = {s_: summarize(res, raw[(key_[0], '', key_[2])], members, weights) for s_, members in subsets.items()}
    tabulate()
    primary = [{'flags': f, 'variant': variant_name(f), **table[(PRIMARY, f, 'main')]} for f in all_variants() if 'g' not in f]
    chosen, n_admissible = choose(primary, table[(PRIMARY, '', 'main')])
    first_rule, _ = choose(primary, table[(PRIMARY, '', 'main')], statistical=False)
    gt_checked = list(dict.fromkeys(['', 'b', 'bd', 'bcd', 'bcde', chosen['flags']]))
    for k, alt in GT_ALTERNATIVES.items():
        alt_gts = {n: build_pseudo_truth(clips[n]['frames'], **dict(alt)) for n in NAMES}
        for flags in gt_checked:
            raw[(PRIMARY, flags, k)] = run_variant(clips, alt_gts, flags, run_params, PRIMARY)
    tabulate()
    out = a.output
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'variants.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scenario', 'pseudo_truth', 'variant', 'subset', 'metric', 'value', 'ci95_low', 'ci95_high',
                    'diff_vs_a', 'diff_ci95_low', 'diff_ci95_high'])
        for (scenario, flags, gt_key), subs in sorted(table.items(), key=lambda kv: (kv[0][2], kv[0][0], len(kv[0][1]), kv[0][1])):
            for s, metrics in subs.items():
                for m, _, _, _ in METRICS:
                    e = metrics[m]
                    w.writerow([scenario, gt_key, variant_name(flags), s, m, e['value'], *e['ci95'], e['diff_vs_a'], *e['diff_ci95']])
                for m in ('anchor_error_px_median', 'anchor_error_px_p90', 'first_triangle_delay_ms_median'):
                    w.writerow([scenario, gt_key, variant_name(flags), s, m, metrics[m], '', '', '', '', ''])
    with (out / 'per_clip.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scenario', 'pseudo_truth', 'variant', 'clip', 'subset', 'analyses', 'mean_period_ms', 'minutes']
                   + [m for m, _, _, _ in METRICS])
        for (scenario, flags, gt_key), res in sorted(raw.items(), key=lambda kv: (kv[0][2], kv[0][0], len(kv[0][1]), kv[0][1])):
            for n in NAMES:
                c = res[n]
                w.writerow([scenario, gt_key, variant_name(flags), n, 'fit' if n in FIT else 'val', c['analyses'],
                            round(c['mean_period_ms'], 1), round(c['minutes'], 3)]
                           + [None if ratio(num(c), den(c)) is None else round(ratio(num(c), den(c)) * sc, 3) for _, num, den, sc in METRICS])
    gt_stats = {n: {'tracks': len(gts[n][1]), 'tracks_1s': sum((m['end'] - m['start'] + 1) >= 30 for m in gts[n][1]),
                    'vehicle_frames': sum(len(f) for f in gts[n][0])} for n in NAMES}
    key = ('coverage_pct', 'coverage_lt40_pct', 'coverage_40_80_pct', 'coverage_ge80_pct', 'never_marked_1s_pct',
           'first_triangle_delay_ms', 'id_switches_per_track', 'association_break_pct', 'false_episodes_per_min', 'false_seconds_per_min',
           'false_stale_seconds_per_min', 'false_unsupported_seconds_per_min', 'anchor_error_px', 'anchor_error_no_prediction_px')
    def brief(flags, scenario=PRIMARY, gt_key='main'):
        t = table[(scenario, flags, gt_key)]
        return {s: {m: t[s][m] for m in key} | {m: t[s][m] for m in ('anchor_error_px_median', 'anchor_error_px_p90', 'first_triangle_delay_ms_median')}
                for s in ('all', 'val', 'fit')}
    singles = [''] + list(FLAGS)
    combos = ['bc', 'bd', 'bcd', 'bcde', 'bcdef', 'bcdefg', 'bde', 'be', chosen['flags']]
    results = {
        'tool': 'tools/track-eval.py',
        'generated_s': round(time.time() - started, 1),
        'data': {'source': str(a.data), 'origin': 'rear_view-respaldo-2026-10-24.zip: ' + ZIP_PREFIX + '<clip>/yolov5n/detections.jsonl', 'model': 'YOLOv5n-640 en PC (estudio antiguo comparisons/old/all-models_all-frames_6-videos)',
                 'clips': {n: {'frames': len(clips[n]['frames']), 'subset': 'fit' if n in FIT else 'val'} for n in NAMES}},
        'method': {
            'port': 'camera_track() línea a línea (variante a), confirmación del detector y regla del overlay; autotest con las '
                    'aserciones de tests/test_vehicles.c',
            'timing': {s: {'period_ms': v[0], 'result_age_ms': v[1], 'overlay_delay_ms': v[2]} for s, v in SCENARIOS.items()},
            'timing_rule': 'una imagen en vuelo; se analiza el primer fotograma con t >= max(turno, respuesta recibida); el '
                           'resultado se ve en los fotogramas capturados desde captura+L-D (reloj del overlay = captura+D); '
                           'TTL 750 ms con el reloj del overlay; edad del ancla = diferencia de capturas',
            'pseudo_truth': build_pseudo_truth.__doc__.strip(),
            'pseudo_truth_stats': gt_stats,
            'on_vehicle': 'regla de anclaje de tools/evaluate-all-frames.py, uno a uno: ancla x dentro de la caja +-2 px y '
                          '|y - borde superior| <= max(4 px, alto/2), en 800x480',
            'false_mark': 'triángulo sin vehículo de pseudo-verdad cerca: ninguna caja con |ancla x - centro| <= ancho y '
                          '|ancla y - borde superior| <= max(8 px, alto); episodio = racha de fotogramas de la misma pista',
            'anchor_error': 'distancia en px (800x480) entre el ancla dibujada y (centro x, borde superior) de la caja de '
                            'pseudo-verdad en el fotograma mostrado; pares «cerca» uno a uno por distancia. '
                            '_no_prediction: misma pareja sin desplazamiento predicho',
            'tracks_1s': 'pistas de pseudo-verdad que abarcan >= 1 s; retraso = primer triángulo sobre ella - inicio de la pista',
            'identity': 'en cada análisis, la observación seguida (confirmada o no) con mayor IoU>0,3 con cada vehículo de '
                        'pseudo-verdad; id_switches_per_track = cambios de id a lo largo de los análisis, por pista >= 1 s '
                        'observada en >= 2 análisis; association_break_pct = % de observaciones de un vehículo ya observado '
                        'en el análisis anterior que salen con hits=1 (pista nueva)',
            'loss_budget': 'cada fotograma de vehículo de pseudo-verdad sin triángulo encima se atribuye al estado del vehículo '
                           'en el último análisis recibido: no presente al capturar, no detectado, primera observación (hits=1), '
                           'ruptura de asociación (hits=1 aunque se observó en el análisis anterior), pico <0,25, o confirmado '
                           '(ancla fuera de la regla por movimiento o latencia)',
            'bootstrap': f'{a.bootstrap} remuestreos de clips con reemplazo (semilla {a.seed}); razón de sumas; '
                         'percentiles 2,5-97,5; diferencia emparejada con los mismos remuestreos. Con 3 clips (fit/val) '
                         'solo hay 10 remuestreos distintos: IC orientativos',
            'fit_validation': {'fit': list(FIT), 'val': list(VAL), 'note': 'parámetros nuevos (tau, vlim_x, vlim_y) y elección de la '
                               'variante solo con FIT; VAL solo para comprobar'},
            'fixed_a_priori': {'pred_clamp_boxes': PRED_CLAMP_BOXES, 'size_ratio': SIZE_RATIO, 'ema_alpha': EMA_ALPHA,
                               'iou_gate': IOU_GATE},
            'flags': FLAG_TEXT,
        },
        'fidelity_vs_study_30hz': fidelity,
        'fitted_parameters': {'used': run_params, 'kinematics': kinematics, 'tau_sweep': sweep},
        'selection_rule': choose.__doc__.strip(),
        'selection': {'chosen': chosen['variant'], 'admissible_variants': n_admissible,
                      'first_pass_rule_choice': first_rule['variant'],
                      'history': 'la primera pasada usaba solo el límite del 10 % en FIT y una única tau ajustada con b+c '
                                 '(0,6), y elegía b+c; en FIT ese aumento de segundos de falsa marca ya era consistente '
                                 '(IC por encima de 0), así que se añadió la condición estadística en FIT y una tau por modo '
                                 'de asociación. first_pass_rule_choice aplica la regla antigua a esta pasada'},
        'primary_scenario': PRIMARY,
        'singles': {variant_name(f): brief(f) for f in singles},
        'combinations': {variant_name(f): brief(f) for f in dict.fromkeys(combos)},
        'latency_sensitivity': {s: {variant_name(f): brief(f, s) for f in dict.fromkeys(['', 'b', 'be', 'bce', 'bcde', chosen['flags']])}
                                for s in SCENARIOS},
        'pseudo_truth_sensitivity': {k: {variant_name(f): brief(f, PRIMARY, k) for f in gt_checked}
                                     for k in GT_ALTERNATIVES} | {'definitions': {k: dict(v) for k, v in GT_ALTERNATIVES.items()}},
        'loss_budget_pct_of_vehicle_frames': {variant_name(f): loss_budget(raw[(PRIMARY, f, 'main')])
                                              for f in dict.fromkeys(['', 'b', 'bc', chosen['flags']])},
        'table_full_evictions': {variant_name(f): {n: raw[(PRIMARY, f, 'main')][n]['evictions'] for n in NAMES}
                                 for f in dict.fromkeys(['', 'f', chosen['flags']])},
        'never_marked_1s_by_median_width': {variant_name(f): {b: dict(zip(('tracks', 'never_marked'), [
            sum(raw[(PRIMARY, f, 'main')][n]['never_by_bucket'][b][i] for n in NAMES) for i in (0, 1)]))
            for b, _, _ in WIDTH_BUCKETS} for f in dict.fromkeys(['', chosen['flags']])},
        'limitations': [
            'detecciones de YOLOv5n-640 en PC (estudio antiguo), no del perfil 320x192 de la Pi: con 320 hay menos '
            'detecciones de coches pequeños y otras puntuaciones; las cifras absolutas no son las del coche y los umbrales '
            '0,15/0,25 y las confirmaciones (7.1) no se reajustan con estos datos',
            'pseudo-verdad construida con el mismo detector (>=0,25 a 30 Hz): mide continuidad frente a lo que el detector '
            've a cadencia completa, no exactitud frente a vehículos reales; sus falsos positivos persistentes cuentan como '
            'vehículos y los coches que nunca supera 0,25 no existen',
            'cadencia y edad simuladas con valores medios (P=360 ms, L=365 ms, D=72 ms supuesto de la auditoría 2.1) y '
            'vídeo a 30 fps; en la Pi hay ~23,4 fps, fluctuación de edad y fotogramas dañados',
            'una sola ejecución determinista por variante; IC bootstrap por clip con solo 6 clips (3 en fit/val)',
            'vídeos de carreras de internet con otra cámara, otro montaje y otra óptica',
        ],
        'effective_period_ms': {s: round(float(np.mean([raw[(s, '', 'main')][n]['mean_period_ms'] for n in NAMES])), 1) for s in SCENARIOS},
        'files': {'variants.csv': 'todas las variantes (64 por escenario) x subconjunto x métrica, con IC y diferencia frente a a',
                  'per_clip.csv': 'métricas por clip'},
    }
    (out / 'results.json').write_text(json.dumps(results, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'chosen': chosen['variant'], 'admissible': n_admissible, 'first_pass_rule': first_rule['variant'],
                      'seconds': round(time.time() - started)}), flush=True)


if __name__ == '__main__':
    main()
