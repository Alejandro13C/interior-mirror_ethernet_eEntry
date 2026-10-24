#!/usr/bin/env python3
"""Rewrite the camera's H.264 SPS with VUI bitstream_restriction
(max_num_reorder_frames=0, max_dec_frame_buffering=max_num_ref_frames).

Without it a conforming decoder may hold pictures for reordering. The camera
stream is Baseline, all IDR, so declaring no reordering is truthful. The
encoder is checked first: re-emitting the parsed SPS unchanged must give the
original bytes. Usage: sps-restrict.py in.h264 out.h264
"""
import sys


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def u(self, n):
        v = 0
        for _ in range(n):
            v = v << 1 | (self.data[self.pos >> 3] >> (7 - (self.pos & 7)) & 1)
            self.pos += 1
        return v

    def ue(self):
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + self.u(zeros)


class Writer:
    def __init__(self):
        self.bits = []

    def u(self, n, v):
        self.bits += [(v >> (n - 1 - i)) & 1 for i in range(n)]

    def ue(self, v):
        n = (v + 1).bit_length()
        self.u(n - 1, 0)
        self.u(n, v + 1)

    def rbsp(self):
        self.u(1, 1)  # rbsp_stop_one_bit
        while len(self.bits) % 8:
            self.bits.append(0)
        return bytes(int(''.join(map(str, self.bits[i:i + 8])), 2) for i in range(0, len(self.bits), 8))


def unescape(nal):
    out, zeros = bytearray(), 0
    for b in nal:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def escape(rbsp):
    out, zeros = bytearray(), 0
    for b in rbsp:
        if zeros >= 2 and b <= 3:
            out.append(3)
            zeros = 0
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


# Fields in bitstream order; only the subset this camera uses (Baseline,
# POC type 0, frame_mbs_only, no HRD) is supported, and anything else aborts.
def parse(rbsp):
    r, f = Reader(rbsp), {}
    f['profile'], f['constraints'], f['level'] = r.u(8), r.u(8), r.u(8)
    if f['profile'] not in (66, 77, 88):
        raise SystemExit('only Baseline/Main/Extended SPS supported')
    f['sps_id'] = r.ue()
    f['log2_max_frame_num'] = r.ue()
    f['poc_type'] = r.ue()
    if f['poc_type'] != 0:
        raise SystemExit('only POC type 0 supported')
    f['log2_max_poc_lsb'] = r.ue()
    f['max_num_ref_frames'], f['gaps'] = r.ue(), r.u(1)
    f['width_mbs'], f['height_units'] = r.ue(), r.ue()
    f['frame_mbs_only'] = r.u(1)
    if not f['frame_mbs_only']:
        raise SystemExit('interlaced SPS not supported')
    f['direct_8x8'] = r.u(1)
    f['crop'] = [r.ue() for _ in range(4)] if r.u(1) else None
    v = f['vui'] = {} if r.u(1) else None
    if v is not None:
        v['aspect'] = (r.u(8),) if r.u(1) else None
        if v['aspect'] and v['aspect'][0] == 255:
            v['aspect'] += (r.u(16), r.u(16))
        v['overscan'] = (r.u(1),) if r.u(1) else None
        v['signal'] = None
        if r.u(1):
            v['signal'] = [r.u(3), r.u(1), None]
            if r.u(1):
                v['signal'][2] = (r.u(8), r.u(8), r.u(8))
        v['chroma_loc'] = (r.ue(), r.ue()) if r.u(1) else None
        v['timing'] = (r.u(32), r.u(32), r.u(1)) if r.u(1) else None
        if r.u(1) or r.u(1):
            raise SystemExit('HRD parameters not supported')
        v['pic_struct'] = r.u(1)
        v['restriction'] = [r.u(1)] + [r.ue() for _ in range(6)] if r.u(1) else None
    return f


def emit(f):
    w = Writer()
    w.u(8, f['profile']); w.u(8, f['constraints']); w.u(8, f['level'])
    w.ue(f['sps_id']); w.ue(f['log2_max_frame_num']); w.ue(f['poc_type']); w.ue(f['log2_max_poc_lsb'])
    w.ue(f['max_num_ref_frames']); w.u(1, f['gaps'])
    w.ue(f['width_mbs']); w.ue(f['height_units']); w.u(1, f['frame_mbs_only']); w.u(1, f['direct_8x8'])
    w.u(1, f['crop'] is not None)
    for c in f['crop'] or []:
        w.ue(c)
    v = f['vui']
    w.u(1, v is not None)
    if v is not None:
        w.u(1, v['aspect'] is not None)
        if v['aspect']:
            w.u(8, v['aspect'][0])
            if v['aspect'][0] == 255:
                w.u(16, v['aspect'][1]); w.u(16, v['aspect'][2])
        w.u(1, v['overscan'] is not None)
        if v['overscan']:
            w.u(1, v['overscan'][0])
        w.u(1, v['signal'] is not None)
        if v['signal']:
            w.u(3, v['signal'][0]); w.u(1, v['signal'][1]); w.u(1, v['signal'][2] is not None)
            if v['signal'][2]:
                for x in v['signal'][2]:
                    w.u(8, x)
        w.u(1, v['chroma_loc'] is not None)
        if v['chroma_loc']:
            w.ue(v['chroma_loc'][0]); w.ue(v['chroma_loc'][1])
        w.u(1, v['timing'] is not None)
        if v['timing']:
            w.u(32, v['timing'][0]); w.u(32, v['timing'][1]); w.u(1, v['timing'][2])
        w.u(1, 0); w.u(1, 0)  # no NAL/VCL HRD
        w.u(1, v['pic_struct'])
        w.u(1, v['restriction'] is not None)
        if v['restriction']:
            w.u(1, v['restriction'][0])
            for x in v['restriction'][1:]:
                w.ue(x)
    return w.rbsp()


def restrict(sps_nal):
    header, rbsp = sps_nal[:1], unescape(sps_nal[1:])
    f = parse(rbsp)
    if escape(emit(f)) != sps_nal[1:].rstrip(b'\0'):
        raise SystemExit('SPS re-encoding does not round-trip; refusing to rewrite')
    if f['vui'] is None:
        raise SystemExit('SPS without VUI not supported')
    # motion vectors over boundaries, default denominators and MV lengths, then no reordering.
    f['vui']['restriction'] = [1, 2, 1, 16, 16, 0, max(1, f['max_num_ref_frames'])]
    return header + escape(emit(f))


def main():
    data = open(sys.argv[1], 'rb').read()
    marker = b'\0\0\0\1\x67'
    start = data.find(marker)
    if start < 0:
        raise SystemExit('no SPS found')
    end = data.find(b'\0\0\0\1', start + 4)
    old = data[start + 4:end]
    new = restrict(old)
    out = data.replace(b'\0\0\0\1' + old + b'\0\0\0\1', b'\0\0\0\1' + new + b'\0\0\0\1')
    open(sys.argv[2], 'wb').write(out)
    print(f'SPS {old.hex()} -> {new.hex()}; replaced {data.count(old)} copies')


if __name__ == '__main__':
    main()
