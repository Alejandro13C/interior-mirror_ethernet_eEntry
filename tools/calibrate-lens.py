#!/usr/bin/env python3
"""Intrinsic calibration of the rear camera lens (plan F10.1).

Subcommands:
  board      printable chessboard or ChArUco PNG, with its physical size.
  calibrate  folder of PNG/JPG -> JSON: Kannala-Brandt (cv2.fisheye) K, D,
             reprojection RMS, valid views, image coverage, hold-out RMS and
             the comparison with the equidistant 180 deg lens that
             src/panini.c assumes (f = W/pi px/rad, centre at the image
             centre). Also fits the unified EUCM model, and cv2.omnidir
             (Mei) when the OpenCV build has the contrib modules.
  circle     image circle (vignetting edge) of one or more frames.
  synth      rendered views of a board through a known fisheye camera, to
             check that `calibrate` recovers K and D end to end.
  selftest   quick points-only check (cv2.fisheye.projectPoints + noise).

Examples:
  calibrate-lens.py board --pattern charuco --out board.png
  calibrate-lens.py calibrate frames/ --pattern charuco --square-mm 50.2 \
      --marker-mm 37.1 --out lens.json
"""
import argparse
import glob
import json
import math
import os
import re
import struct
import zlib

import cv2
import numpy as np

DICTS = {n: getattr(cv2.aruco, n) for n in dir(cv2.aruco) if n.startswith('DICT_')}
THETA_CANVAS = 68.1  # max ray angle sampled by the current 800x480/130 deg Panini canvas


# ---------------------------------------------------------------- boards
def charuco(a):
    d = cv2.aruco.getPredefinedDictionary(DICTS[a.dict])
    return cv2.aruco.CharucoBoard((a.cols, a.rows), a.square_mm, a.marker_mm, d)


def board_texture(a, px_per_mm):
    """Board image and the object point (mm) of its top-left pixel corner."""
    w, h = int(round(a.cols * a.square_mm * px_per_mm)), int(round(a.rows * a.square_mm * px_per_mm))
    if a.pattern == 'charuco':
        img = charuco(a).generateImage((w, h), marginSize=0, borderBits=1)
    else:
        yy, xx = np.mgrid[0:h, 0:w]
        sq = a.square_mm * px_per_mm
        img = np.where(((xx // sq).astype(int) + (yy // sq).astype(int)) % 2 == 0, 0, 255).astype(np.uint8)
    return img


def object_points(a):
    """All inner corners in mm, z = 0, origin at the board's top-left corner."""
    if a.pattern == 'charuco':
        return charuco(a).getChessboardCorners().astype(np.float64)
    ys, xs = np.mgrid[1:a.rows, 1:a.cols]
    return np.c_[xs.ravel(), ys.ravel(), np.zeros(xs.size)] * a.square_mm


def cmd_board(a):
    margin = 10.0  # mm of white border so the outer squares can be detected
    s = a.dpi / 25.4
    img = board_texture(a, s)
    m = int(round(margin * s))
    img = cv2.copyMakeBorder(img, m, m, m, m, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode('.png', img)
    if not ok:
        raise SystemExit('cannot encode PNG')
    # pHYs chunk after IHDR, so viewers print at the intended physical size.
    ppm = int(round(a.dpi / 0.0254))
    body = b'pHYs' + struct.pack('>IIB', ppm, ppm, 1)
    chunk = struct.pack('>I', 9) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)
    png = png.tobytes()
    with open(a.out, 'wb') as f:
        f.write(png[:33] + chunk + png[33:])
    w_mm, h_mm = a.cols * a.square_mm + 2 * margin, a.rows * a.square_mm + 2 * margin
    info = {'file': a.out, 'pattern': a.pattern, 'squares': [a.cols, a.rows],
            'square_mm': a.square_mm, 'pixels': [img.shape[1], img.shape[0]], 'dpi': a.dpi,
            'print_size_mm': [round(w_mm, 1), round(h_mm, 1)],
            'note': 'Print at 100 % (no fit-to-page) on rigid flat board; measure a square '
                    'with a caliper and pass the measured value as --square-mm.'}
    if a.pattern == 'charuco':
        info.update(marker_mm=a.marker_mm, dict=a.dict)
    print(json.dumps(info, indent=1))


# ---------------------------------------------------------------- detection
def detect(a, gray):
    """(object points, image points) of one view, or (None, reason)."""
    if a.pattern == 'charuco':
        det = cv2.aruco.CharucoDetector(charuco(a))
        corners, ids, _, _ = det.detectBoard(gray)
        if ids is None or len(ids) < a.min_corners:
            return None, 'charuco: %d corners' % (0 if ids is None else len(ids))
        obj = charuco(a).getChessboardCorners()[ids.ravel()].astype(np.float64)
        # OpenCV 4.13 CharucoDetector returns corners +0.5 px off the
        # pixel-centre convention of projectPoints (measured on a flat board:
        # +0.51 px in x and y). cornerSubPix uses the right convention.
        corners = corners.astype(np.float32).reshape(-1, 1, 2)
        # Window inside the white gap between the corner and the markers.
        c2 = corners.reshape(-1, 2)
        dist = np.sqrt(((c2[:, None] - c2[None]) ** 2).sum(2)) + np.eye(len(c2)) * 1e9
        px_per_mm = float(np.median(dist.min(1))) / a.square_mm
        win = int(np.clip(0.8 * px_per_mm * (a.square_mm - a.marker_mm) / 2, 2, 6))
        cv2.cornerSubPix(gray, corners, (win, win), (-1, -1),
                         (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 50, 1e-3))
        img = corners.reshape(-1, 2).astype(np.float64)
        # Collinear corners (one row) do not constrain a homography.
        if np.linalg.matrix_rank(np.c_[obj[:, :2], np.ones(len(obj))], tol=1e-6) < 3:
            return None, 'collinear corners'
        return (obj, img), None
    size = (a.cols - 1, a.rows - 1)
    ok, corners = cv2.findChessboardCornersSB(
        gray, size, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    if not ok:
        return None, 'chessboard not found'
    return (object_points(a), corners.reshape(-1, 2).astype(np.float64)), None


# ---------------------------------------------------------------- KB model
def kb_rays(pts, K, D):
    """Unit rays for pixels under Kannala-Brandt (valid beyond 90 deg too)."""
    x = (pts[:, 0] - K[0, 2]) / K[0, 0]
    y = (pts[:, 1] - K[1, 2]) / K[1, 1]
    td = np.hypot(x, y)
    th = td.copy()
    k = np.ravel(D)
    for _ in range(30):
        t2 = th * th
        f = th * (1 + t2 * (k[0] + t2 * (k[1] + t2 * (k[2] + t2 * k[3])))) - td
        df = 1 + t2 * (3 * k[0] + t2 * (5 * k[1] + t2 * (7 * k[2] + t2 * 9 * k[3])))
        th = th - f / np.where(np.abs(df) < 1e-9, 1e-9, df)
    s = np.where(td > 1e-12, np.sin(th) / np.maximum(td, 1e-12), 1.0)
    return np.c_[x * s, y * s, np.cos(th)], th


def kb_radius(theta, K, D):
    k = np.ravel(D)
    t2 = theta * theta
    return K[0, 0] * theta * (1 + t2 * (k[0] + t2 * (k[1] + t2 * (k[2] + t2 * k[3]))))


def fisheye_calibrate(objs, imgs, size, guess_f):
    """cv2.fisheye.calibrate dropping views that make it ill-conditioned."""
    keep = list(range(len(objs)))
    rejected = []
    flags = (cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC | cv2.fisheye.CALIB_CHECK_COND |
             cv2.fisheye.CALIB_FIX_SKEW | cv2.fisheye.CALIB_USE_INTRINSIC_GUESS)
    crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 200, 1e-10)
    while len(keep) >= 3:
        K = np.array([[guess_f, 0, (size[0] - 1) / 2], [0, guess_f, (size[1] - 1) / 2], [0, 0, 1]])
        D = np.zeros((4, 1))
        try:
            rms, K, D, rv, tv = cv2.fisheye.calibrate(
                [objs[i].reshape(-1, 1, 3) for i in keep], [imgs[i].reshape(-1, 1, 2) for i in keep],
                size, K, D, flags=flags, criteria=crit)
            return rms, K, D, rv, tv, keep, rejected
        except cv2.error as e:
            m = re.search(r'input array (\d+)', str(e))
            if not m:
                raise
            rejected.append(keep.pop(int(m.group(1))))
    raise SystemExit('fewer than 3 usable views')


def view_rms(obj, img, rv, tv, K, D):
    p, _ = cv2.fisheye.projectPoints(obj.reshape(-1, 1, 3), rv, tv, K, D)
    e = p.reshape(-1, 2) - img
    return float(np.sqrt((e ** 2).sum(1).mean())), e


def holdout_rms(objs, imgs, size, guess_f, every=5):
    """Calibrate without every 5th view, then solve only the pose of those."""
    test = list(range(0, len(objs), every))
    train = [i for i in range(len(objs)) if i not in test]
    if len(train) < 8 or not test:
        return None
    _, K, D, _, _, _, _ = fisheye_calibrate([objs[i] for i in train], [imgs[i] for i in train], size, guess_f)
    errs = []
    for i in test:
        ok, rv, tv = cv2.fisheye.solvePnP(objs[i].reshape(-1, 1, 3), imgs[i].reshape(-1, 1, 2), K, D)
        if ok:
            errs.append(view_rms(objs[i], imgs[i], rv, tv, K, D)[1])
    if not errs:
        return None
    e = np.vstack(errs)
    return {'views': len(errs), 'rms_px': round(float(np.sqrt((e ** 2).sum(1).mean())), 4)}


# ---------------------------------------------------------------- EUCM
def eucm_project(P, p):
    fx, fy, cx, cy, al, be = p
    d = np.sqrt(be * (P[:, 0] ** 2 + P[:, 1] ** 2) + P[:, 2] ** 2)
    den = al * d + (1 - al) * P[:, 2]
    return np.c_[fx * P[:, 0] / den + cx, fy * P[:, 1] / den + cy]


def eucm_fit(objs, imgs, K, rvecs, tvecs):
    """Extended unified camera model (Khomenko 2016), initialised from KB."""
    try:
        from scipy.optimize import least_squares
        from scipy.sparse import lil_matrix
    except ImportError:
        return {'skipped': 'scipy not available'}
    n = len(objs)
    x0 = np.r_[K[0, 0], K[1, 1], K[0, 2], K[1, 2], 0.6, 1.0,
               np.ravel([np.r_[np.ravel(r), np.ravel(t)] for r, t in zip(rvecs, tvecs)])]
    # Near the axis EUCM and KB share the focal, so K is a good start.
    sizes = [len(o) for o in objs]

    def residual(x):
        out = []
        for i in range(n):
            R, _ = cv2.Rodrigues(x[6 + 6 * i:9 + 6 * i])
            P = objs[i] @ R.T + x[9 + 6 * i:12 + 6 * i]
            out.append((eucm_project(P, x[:6]) - imgs[i]).ravel())
        return np.concatenate(out)

    sp = lil_matrix((2 * sum(sizes), len(x0)), dtype=int)
    row = 0
    for i, s in enumerate(sizes):
        sp[row:row + 2 * s, :6] = 1
        sp[row:row + 2 * s, 6 + 6 * i:12 + 6 * i] = 1
        row += 2 * s
    lo = np.full(len(x0), -np.inf)
    hi = np.full(len(x0), np.inf)
    lo[4], hi[4], lo[5], hi[5] = 0.0, 1.0, 0.01, 10.0
    r = least_squares(residual, x0, jac_sparsity=sp, bounds=(lo, hi), x_scale='jac', max_nfev=1000)
    e = r.fun.reshape(-1, 2)
    fx, fy, cx, cy, al, be = r.x[:6]
    return {'fx': fx, 'fy': fy, 'cx': cx, 'cy': cy, 'alpha': al, 'beta': be,
            'rms_px': float(np.sqrt((e ** 2).sum(1).mean())), 'converged': bool(r.success)}


def omnidir_fit(objs, imgs, size):
    if not hasattr(cv2, 'omnidir'):
        return {'skipped': 'cv2.omnidir not in this OpenCV build (needs opencv-contrib-python)'}
    crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 300, 1e-8)
    try:
        rms, K, xi, D, _, _, idx = cv2.omnidir.calibrate(
            [o.reshape(-1, 1, 3) for o in objs], [i.reshape(-1, 1, 2) for i in imgs], size,
            None, None, None, cv2.omnidir.CALIB_FIX_SKEW, crit)
    except cv2.error as e:
        return {'failed': str(e).strip().splitlines()[-1]}
    return {'note': 'Mei model: K holds gamma = f*(1+xi), not comparable with the KB fx',
            'K': K.tolist(), 'xi': float(np.ravel(xi)[0]), 'D': np.ravel(D).tolist(),
            'rms_px': float(rms), 'views_used': 0 if idx is None else int(np.size(idx))}


# ---------------------------------------------------------------- calibrate
def coverage(imgs, size, K, D, circle):
    pts = np.vstack(imgs)
    gx, gy = 16, 12
    cx = np.clip((pts[:, 0] * gx / size[0]).astype(int), 0, gx - 1)
    cy = np.clip((pts[:, 1] * gy / size[1]).astype(int), 0, gy - 1)
    hit = np.zeros((gy, gx), bool)
    hit[cy, cx] = True
    # Only cells whose centre lies inside the image circle can be covered.
    yy, xx = np.mgrid[0:gy, 0:gx]
    ccx, ccy, cr = circle
    inside = np.hypot((xx + .5) * size[0] / gx - ccx, (yy + .5) * size[1] / gy - ccy) < cr
    _, th = kb_rays(pts, K, D)
    r = np.hypot(pts[:, 0] - ccx, pts[:, 1] - ccy)
    return {'grid': '%dx%d' % (gx, gy), 'cells_covered_in_circle': round(float(hit[inside].mean()), 3),
            'theta_max_deg': round(float(np.degrees(th.max())), 1),
            'corners_beyond_75pct_radius': int((r > 0.75 * cr).sum()),
            'corners_beyond_canvas_theta': int((np.degrees(th) > THETA_CANVAS).sum())}


def compare_assumed(K, D, size, lens_fov):
    """Calibrated KB lens against the equidistant model in src/panini.c."""
    W, H = size
    f_assumed = W / math.radians(lens_fov)
    th = np.radians(np.arange(1, THETA_CANVAS + 0.01, 0.5))
    r = kb_radius(th, K, D)
    f_fit = float((r * th).sum() / (th * th).sum())  # least squares r = f*theta
    table = {str(t): {'calibrated_px': round(float(kb_radius(math.radians(t), K, D)), 1),
                      'assumed_px': round(f_assumed * math.radians(t), 1)}
             for t in (10, 30, 45, 60, 68, 80, 90)}
    off = (K[0, 2] - (W - 1) / 2, K[1, 2] - (H - 1) / 2)
    return {'assumed_lens_fov_deg': lens_fov, 'f_assumed_px_per_rad': round(f_assumed, 2),
            'fx_px_per_rad': round(float(K[0, 0]), 2),
            'fx_vs_assumed_pct': round(100 * (K[0, 0] / f_assumed - 1), 2),
            'f_equidistant_fit_to_68deg': round(f_fit, 2),
            'equivalent_CAMERA_LENS_FOV_deg': round(math.degrees(W / f_fit), 1),
            'max_error_equidistant_fit_px': round(float(np.abs(r - f_fit * th).max()), 2),
            'radius_vs_theta_deg': table,
            'principal_point_offset_px': [round(off[0], 2), round(off[1], 2)],
            'principal_point_offset_deg': [round(math.degrees(off[0] / K[0, 0]), 2),
                                           round(math.degrees(off[1] / K[1, 1]), 2)]}


def cmd_calibrate(a):
    files = sorted(f for e in ('png', 'jpg', 'jpeg', 'bmp') for f in glob.glob(os.path.join(a.folder, '*.' + e)))
    if not files:
        raise SystemExit('no images in ' + a.folder)
    objs, imgs, names, failed, size = [], [], [], {}, None
    for f in files:
        gray = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            failed[os.path.basename(f)] = 'unreadable'
            continue
        if size is None:
            size = (gray.shape[1], gray.shape[0])
        elif size != (gray.shape[1], gray.shape[0]):
            failed[os.path.basename(f)] = 'different size'
            continue
        res, why = detect(a, gray)
        if res is None:
            failed[os.path.basename(f)] = why
            continue
        objs.append(res[0])
        imgs.append(res[1])
        names.append(os.path.basename(f))
    if len(objs) < 3:
        raise SystemExit('only %d views with a detected board' % len(objs))
    guess_f = size[0] / math.radians(a.lens_fov)
    rms, K, D, rv, tv, keep, rejected = fisheye_calibrate(objs, imgs, size, guess_f)
    for i in rejected:
        failed[names[i]] = 'ill-conditioned (CALIB_CHECK_COND)'
    per_view = {names[i]: round(view_rms(objs[i], imgs[i], rv[j], tv[j], K, D)[0], 4)
                for j, i in enumerate(keep)}
    ko = [objs[i] for i in keep]
    ki = [imgs[i] for i in keep]
    circle = a.circle or ((size[0] - 1) / 2, (size[1] - 1) / 2, math.hypot(size[0], size[1]) / 2)
    out = {
        'model': 'kannala-brandt (cv2.fisheye)', 'image_size': list(size),
        'K': K.tolist(), 'D': np.ravel(D).tolist(), 'rms_px': round(float(rms), 4),
        'views_total': len(files), 'views_valid': len(keep), 'corners': int(sum(len(o) for o in ko)),
        'per_view_rms_px': per_view, 'views_failed': failed,
        'holdout': holdout_rms(ko, ki, size, guess_f),
        'coverage': coverage(ki, size, K, D, circle),
        'vs_panini_assumption': compare_assumed(K, D, size, a.lens_fov),
        'eucm': eucm_fit(ko, ki, K, rv, tv) if a.eucm else {'skipped': '--no-eucm'},
        'omnidir_mei': omnidir_fit(ko, ki, size),
    }
    out['acceptance'] = {'rms_le_0.5px': out['rms_px'] <= 0.5, 'views_ge_20': len(keep) >= 20,
                         'cells_ge_0.6': out['coverage']['cells_covered_in_circle'] >= 0.6}
    if a.truth:
        t = json.load(open(a.truth))
        Kt, Dt = np.array(t['K']), np.ravel(t['D'])
        tmax = math.radians(out['coverage']['theta_max_deg'])
        th = np.linspace(0, tmax, 200)
        dr = kb_radius(th, K, D) - kb_radius(th, Kt, Dt)
        out['vs_truth'] = {'K_error': (K - Kt).round(3).tolist(), 'D_error': (np.ravel(D) - Dt).round(5).tolist(),
                           'radius_error_max_px_observed_theta': round(float(np.abs(dr).max()), 3)}
    text = json.dumps(out, indent=1)
    if a.out:
        open(a.out, 'w').write(text + '\n')
    print(text)


# ---------------------------------------------------------------- image circle
def circle_edges(g, rmin=560, half=12, min_drop=0.3, win=4):
    """Per 0.25 deg ray from the image centre: radius of the steepest drop of
    log luminance (the vignetting edge), if the drop is strong enough."""
    h, w = g.shape
    cx, cy = (w - 1) / 2, (h - 1) / 2
    L = np.log(cv2.GaussianBlur(g.astype(np.float32), (0, 0), 1.5) + 4).astype(np.float32)
    pts = []
    for k in range(1440):
        a = math.radians(k / 4)
        dx, dy = math.cos(a), math.sin(a)
        tx = (w - 1 - cx) / dx if dx > 1e-9 else (-cx / dx if dx < -1e-9 else 1e9)
        ty = (h - 1 - cy) / dy if dy > 1e-9 else (-cy / dy if dy < -1e-9 else 1e9)
        rmax = min(tx, ty) - 1
        if rmax < rmin + 3 * half:
            continue
        r = np.arange(rmin, rmax, 1.0)
        prof = np.zeros_like(r)
        for o in range(-win, win + 1):
            px = (cx + r * dx - o * dy).astype(np.float32)[None]
            py = (cy + r * dy + o * dx).astype(np.float32)[None]
            prof += cv2.remap(L, px, py, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[0]
        prof = np.convolve(prof / (2 * win + 1), np.ones(5) / 5, 'valid')
        r = r[2:-2]
        drop = np.full_like(prof, -9)
        drop[half:-half] = prof[:-2 * half] - prof[2 * half:]
        i = int(np.argmax(drop))
        if drop[i] < min_drop:
            continue
        j = slice(max(i - 3, 0), i + 4)
        wt = np.clip(drop[j] - drop[i] * 0.5, 0, None)
        ri = float((r[j] * wt).sum() / wt.sum())
        pts.append((cx + ri * dx, cy + ri * dy, k / 4))
    return np.array(pts).reshape(-1, 3)


def fit_circle(p):
    x, y = p[:, 0], p[:, 1]
    s = np.linalg.lstsq(np.c_[2 * x, 2 * y, np.ones_like(x)], x * x + y * y, rcond=None)[0]
    R = math.sqrt(s[2] + s[0] ** 2 + s[1] ** 2)
    return s[0], s[1], R, np.hypot(x - s[0], y - s[1]) - R


def cmd_circle(a):
    results = []
    for f in a.images:
        g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        if g is None:
            raise SystemExit('cannot read ' + f)
        h, w = g.shape
        c0 = ((w - 1) / 2, (h - 1) / 2)
        p = circle_edges(g)
        res = {'image': f, 'edge_points': len(p)}
        best = None
        rng = np.random.default_rng(0)
        for _ in range(3000 if len(p) >= 3 else 0):  # RANSAC with a plausibility prior
            cx, cy, R, _ = fit_circle(p[rng.choice(len(p), 3, replace=False), :2])
            if not (a.rmin < R < a.rmax) or math.hypot(cx - c0[0], cy - c0[1]) > a.max_offset:
                continue
            inl = np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cy) - R) < 3
            if best is None or inl.sum() > best.sum():
                best = inl
        if best is None or best.sum() < 20:
            res['error'] = 'no consistent circle'
            results.append(res)
            continue
        for _ in range(3):
            cx, cy, R, e = fit_circle(p[best, :2])
            best = np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cy) - R) < 3
        q = p[best]
        # Corner of each inlier, by its ray angle from the image centre (y down).
        names = ('bottom_right', 'bottom_left', 'top_left', 'top_right')
        corners = {n: int((q[:, 2] // 90 == i).sum()) for i, n in enumerate(names)}
        # Support: inliers over the rays where this circle lies inside the image.
        ang = np.radians(np.arange(0, 360, 0.25))
        x, y = cx + R * np.cos(ang), cy + R * np.sin(ang)
        visible = int(((x > 14) & (x < w - 15) & (y > 14) & (y < h - 15)).sum())
        support = round(len(q) / max(visible, 1), 2)
        res.update(inliers=int(best.sum()), inliers_per_corner=corners, support=support,
                   centre=[round(cx, 1), round(cy, 1)], radius=round(R, 1),
                   rms_px=round(float(np.sqrt((e ** 2).mean())), 2),
                   offset_from_image_centre=[round(cx - c0[0], 1), round(cy - c0[1], 1)])
        if sum(v >= 10 for v in corners.values()) < 3 or support < 0.3:
            res['warning'] = ('edge not clearly seen in 3 corners (dark scene or objects at '
                              'the corners): free centre not reliable, use --centre')
        if a.centre:
            # Radius with the centre fixed (e.g. from a better-lit capture): the
            # densest 6 px band of edge distances, refined on +-3 px.
            d = np.hypot(p[:, 0] - a.centre[0], p[:, 1] - a.centre[1])
            d = d[(d > a.rmin) & (d < a.rmax)]
            if len(d):
                counts = [(np.abs(d - r0) < 3).sum() for r0 in np.arange(a.rmin, a.rmax, 1.0)]
                r0 = a.rmin + float(np.argmax(counts))
                d = d[np.abs(d - r0) < 3]
                d = d[np.abs(d - d.mean()) < 3]
                res['fixed_centre'] = {'centre': a.centre, 'radius': round(float(d.mean()), 1),
                                       'sd_px': round(float(d.std()), 2), 'points': int(len(d))}
        if a.debug:
            v = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            for x, y, _ in p:
                cv2.circle(v, (int(x), int(y)), 2, (0, 0, 255), -1)
            for x, y, _ in q:
                cv2.circle(v, (int(x), int(y)), 3, (0, 255, 0), -1)
            cv2.circle(v, (int(round(cx)), int(round(cy))), int(round(R)), (0, 255, 0), 1)
            cv2.imwrite(os.path.join(a.debug, os.path.splitext(os.path.basename(f))[0] + '-circle.png'), v)
        results.append(res)
    print(json.dumps(results, indent=1))


# ---------------------------------------------------------------- synthetic
def look_at(direction, up_hint):
    z = direction / np.linalg.norm(direction)
    x = np.cross(up_hint, z)
    x /= np.linalg.norm(x)
    return np.c_[x, np.cross(z, x), z]


def random_pose(rng, a, obj):
    """Board somewhere in the field, roughly facing the camera."""
    th = math.radians(rng.uniform(0, a.max_theta))
    ph = rng.uniform(0, 2 * math.pi)
    ray = np.array([math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)])
    dist = rng.uniform(a.dmin, a.dmax)
    # Board normal points back to the camera, tilted by up to 40 deg.
    tilt = np.radians(rng.uniform(-40, 40, 2))
    R = look_at(ray, np.array([0.0, 1.0, 0.0]) if abs(ray[1]) < 0.9 else np.array([1.0, 0, 0]))
    Rt, _ = cv2.Rodrigues(np.array([tilt[0], tilt[1], rng.uniform(-0.4, 0.4)]))
    R = R @ Rt
    centre = obj.mean(0)
    t = dist * ray - R @ centre
    return cv2.Rodrigues(R)[0], t.reshape(3, 1)


def pixel_rays(K, D, size, ss=2):
    """Rays of a supersampled pixel grid; computed once per synthetic camera."""
    W, H = size
    ys, xs = np.mgrid[0:H * ss, 0:W * ss].astype(np.float64)
    pix = np.c_[(xs.ravel() + .5) / ss - .5, (ys.ravel() + .5) / ss - .5]
    ray, th = kb_rays(pix, K, D)
    ray[th > math.radians(95)] = 0  # never intersects anything
    return ray


def render(tex, px_per_mm, rvec, tvec, rays, size, circle, ss=2):
    W, H = size
    R, _ = cv2.Rodrigues(rvec)
    uvw = rays @ np.linalg.inv(np.c_[R[:, 0], R[:, 1], tvec.ravel()]).T
    w = uvw[:, 2]
    ok = w > 1e-9
    w = np.where(ok, w, 1.0)
    u = np.where(ok, uvw[:, 0] / w * px_per_mm - .5, -1e6).astype(np.float32)
    v = np.where(ok, uvw[:, 1] / w * px_per_mm - .5, -1e6).astype(np.float32)
    img = cv2.remap(tex.astype(np.float32), u.reshape(H * ss, W * ss), v.reshape(H * ss, W * ss),
                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=150)
    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[0:H, 0:W]
    rr = np.hypot(xx - circle[0], yy - circle[1])
    img *= np.clip(1 - (rr - circle[2] + 15) / 30, 0.12, 1.0)  # soft vignetting edge
    return img


def cmd_synth(a):
    os.makedirs(a.out, exist_ok=True)
    size = (a.width, a.height)
    K = np.array([[a.fx, 0, a.cx], [0, a.fy, a.cy], [0, 0, 1]], float)
    D = np.array(a.d, float).reshape(4, 1)
    circle = (a.cx, a.cy, a.circle_radius)
    rng = np.random.default_rng(a.seed)
    obj = object_points(a)
    px_per_mm = 4.0
    tex = board_texture(a, px_per_mm)
    rays = pixel_rays(K, D, size)
    rendered, attempts, det_err = 0, 0, []
    while rendered < a.views and attempts < 50 * a.views:
        attempts += 1
        rv, tv = random_pose(rng, a, obj)
        Pc = obj @ cv2.Rodrigues(rv)[0].T + tv.ravel()
        theta = np.degrees(np.arctan2(np.hypot(Pc[:, 0], Pc[:, 1]), Pc[:, 2]))
        p, _ = cv2.fisheye.projectPoints(obj.reshape(-1, 1, 3), rv, tv, K, D)
        p = p.reshape(-1, 2)
        vis = (theta < 85) & (p[:, 0] > 8) & (p[:, 0] < size[0] - 9) & (p[:, 1] > 8) & \
              (p[:, 1] < size[1] - 9) & (np.hypot(p[:, 0] - a.cx, p[:, 1] - a.cy) < a.circle_radius - 20)
        need = vis.all() if a.pattern == 'chessboard' else vis.mean() > 0.5
        if not need:
            continue
        img = render(tex, px_per_mm, rv, tv, rays, size, circle)
        img = cv2.GaussianBlur(img, (0, 0), a.blur) + rng.normal(0, a.noise, img.shape)
        img = np.clip(img, 0, 255).astype(np.uint8)
        name = os.path.join(a.out, 'view_%02d.png' % rendered)
        cv2.imwrite(name, img)
        res, _ = detect(a, img)
        if res is not None:
            # Detected corners against cv2.fisheye.projectPoints of the truth.
            # Nearest projected corner: a chessboard may come back rotated 180 deg.
            q = res[1]
            idx = np.argmin(((q[:, None, :] - p[None]) ** 2).sum(2), 1)
            det_err.append(q - p[idx])
        rendered += 1
    truth = {'K': K.tolist(), 'D': D.ravel().tolist(), 'image_size': list(size), 'views': rendered,
             'pattern': a.pattern, 'squares': [a.cols, a.rows], 'square_mm': a.square_mm}
    if det_err:
        e = np.vstack(det_err)
        truth['detection_vs_projectPoints'] = {'views_detected': len(det_err),
                                               'rms_px': round(float(np.sqrt((e ** 2).sum(1).mean())), 4),
                                               'max_px': round(float(np.hypot(e[:, 0], e[:, 1]).max()), 4)}
    json.dump(truth, open(os.path.join(a.out, 'truth.json'), 'w'), indent=1)
    print(json.dumps(truth, indent=1))


def cmd_selftest(a):
    """Points only (no rendering or detection): the solver over several seeds."""
    size = (1344, 968)
    K = np.array([[a.fx, 0, a.cx], [0, a.fy, a.cy], [0, 0, 1]], float)
    D = np.array(a.d, float).reshape(4, 1)
    a.pattern, a.cols, a.rows, a.square_mm = 'chessboard', 10, 7, 50.0
    obj = object_points(a)
    rows = []
    for trial in range(a.trials):
        rng = np.random.default_rng(a.seed + trial)
        objs, imgs = [], []
        while len(objs) < a.views:
            rv, tv = random_pose(rng, a, obj)
            p, _ = cv2.fisheye.projectPoints(obj.reshape(-1, 1, 3), rv, tv, K, D)
            p = p.reshape(-1, 2)
            Pc = obj @ cv2.Rodrigues(rv)[0].T + tv.ravel()
            if (np.arctan2(np.hypot(Pc[:, 0], Pc[:, 1]), Pc[:, 2]) > math.radians(85)).any():
                continue
            if (p < 0).any() or (p[:, 0] > size[0] - 1).any() or (p[:, 1] > size[1] - 1).any():
                continue
            objs.append(obj)
            imgs.append(p + rng.normal(0, a.noise, p.shape))
        rms, Ke, De, _, _, _, _ = fisheye_calibrate(objs, imgs, size, size[0] / math.pi)
        # D alone is not identifiable (its terms trade off): compare r(theta)
        # inside the angles the views covered.
        tmax = max(float(kb_rays(i, K, D)[1].max()) for i in imgs)
        th = np.linspace(0, tmax, 200)
        dr = float(np.abs(kb_radius(th, Ke, De) - kb_radius(th, K, D)).max())
        th = np.radians(np.linspace(0, THETA_CANVAS, 200))
        dc = float(np.abs(kb_radius(th, Ke, De) - kb_radius(th, K, D)).max())
        rows.append([rms, Ke[0, 0] - K[0, 0], Ke[1, 1] - K[1, 1], Ke[0, 2] - K[0, 2],
                     Ke[1, 2] - K[1, 2], dr, dc, math.degrees(tmax)])
    r = np.array(rows)
    names = ['rms_px', 'fx_err', 'fy_err', 'cx_err', 'cy_err', 'radius_err_max_px_observed',
             'radius_err_max_px_canvas_68deg', 'theta_max_deg']
    out = {'trials': a.trials, 'views': a.views, 'noise_px_per_axis': a.noise,
           'K_true': K.tolist(), 'D_true': D.ravel().tolist(),
           'mean': dict(zip(names, r.mean(0).round(3).tolist())),
           'sd': dict(zip(names, r.std(0).round(3).tolist())),
           'max_abs': dict(zip(names, np.abs(r).max(0).round(3).tolist()))}
    print(json.dumps(out, indent=1))
    worst = out['max_abs']
    # About 3 sd of the solver scatter at 0.3 px noise: catches bias, not noise.
    if max(worst['fx_err'], worst['fy_err']) > 1.5 or max(worst['cx_err'], worst['cy_err']) > 2 or \
            worst['radius_err_max_px_canvas_68deg'] > 1.5:
        raise SystemExit('selftest FAILED')


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def board_args(p, square=50.0):
        p.add_argument('--pattern', choices=('charuco', 'chessboard'), default='charuco')
        p.add_argument('--cols', type=int, default=11, help='squares across')
        p.add_argument('--rows', type=int, default=8, help='squares down')
        p.add_argument('--square-mm', type=float, default=square)
        p.add_argument('--marker-mm', type=float, default=37.0, help='ChArUco marker side')
        p.add_argument('--dict', choices=sorted(DICTS), default='DICT_5X5_100')
        p.add_argument('--min-corners', type=int, default=12)

    def camera_args(p):
        p.add_argument('--fx', type=float, default=430.0)
        p.add_argument('--fy', type=float, default=430.0)
        p.add_argument('--cx', type=float, default=661.1)
        p.add_argument('--cy', type=float, default=478.8)
        p.add_argument('--d', type=float, nargs=4, default=[0.02, -0.012, 0.004, -0.0006],
                       metavar=('K1', 'K2', 'K3', 'K4'))
        p.add_argument('--views', type=int, default=25)
        p.add_argument('--noise', type=float, default=2.0)
        p.add_argument('--seed', type=int, default=1)
        p.add_argument('--max-theta', type=float, default=60.0, help='max board direction, deg')
        p.add_argument('--dmin', type=float, default=450.0, help='board distance, mm')
        p.add_argument('--dmax', type=float, default=900.0)

    p = sub.add_parser('board', help='printable board PNG')
    board_args(p)
    p.add_argument('--dpi', type=float, default=150.0)
    p.add_argument('--out', required=True)
    p.set_defaults(fn=cmd_board)

    p = sub.add_parser('calibrate', help='calibrate from a folder of images')
    p.add_argument('folder')
    board_args(p)
    p.add_argument('--lens-fov', type=float, default=180.0, help='CAMERA_LENS_FOV to compare with')
    p.add_argument('--circle', type=float, nargs=3, metavar=('CX', 'CY', 'R'),
                   help='image circle from the circle command (for coverage)')
    p.add_argument('--no-eucm', dest='eucm', action='store_false')
    p.add_argument('--truth', help='truth.json from synth, to report the errors')
    p.add_argument('--out', help='JSON output file')
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser('circle', help='image circle from the vignetting edge')
    p.add_argument('images', nargs='+')
    p.add_argument('--rmin', type=float, default=600.0)
    p.add_argument('--rmax', type=float, default=850.0)
    p.add_argument('--max-offset', type=float, default=40.0, help='max centre offset from image centre, px')
    p.add_argument('--centre', type=float, nargs=2, help='also fit the radius with this centre fixed')
    p.add_argument('--debug', help='folder for overlay PNGs')
    p.set_defaults(fn=cmd_circle)

    p = sub.add_parser('synth', help='render synthetic board views through a known fisheye')
    board_args(p)
    camera_args(p)
    p.add_argument('--width', type=int, default=1344)
    p.add_argument('--height', type=int, default=968)
    p.add_argument('--circle-radius', type=float, default=733.0)
    p.add_argument('--blur', type=float, default=0.8)
    p.add_argument('--out', required=True)
    p.set_defaults(fn=cmd_synth)

    p = sub.add_parser('selftest', help='points-only solver check')
    camera_args(p)
    p.add_argument('--trials', type=int, default=20)
    p.set_defaults(fn=cmd_selftest, noise=0.3)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
