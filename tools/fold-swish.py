#!/usr/bin/env python3
"""Fold the Split/Sigmoid/BinaryOp SiLU lowering back into native NCNN Swish.

export-yolov5-ncnn.py lowered every Swish because the pinned NCNN build left
that layer out. Swish has no weights, so the .bin file is unchanged; NCNN must
be built with the swish layer (tools/build-ncnn.py).
Usage: fold-swish.py models/yolov5n.param models/yolov5n-swish.param
"""
import sys
from pathlib import Path


def consumers(lines):
    count = {}
    for line in lines:
        f = line.split()
        for blob in f[4:4 + int(f[2])]:
            count[blob] = count.get(blob, 0) + 1
    return count


def fold(lines):
    """Split(x -> a, b), Sigmoid(one of them -> s), BinaryOp mul(other, s) == x*sigmoid(x)."""
    used = consumers(lines)
    out, i, folded = [], 0, 0
    while i < len(lines):
        f = lines[i].split()
        if f[0] == 'Split' and f[2:4] == ['1', '2'] and i + 2 < len(lines):
            sig, mul = lines[i + 1].split(), lines[i + 2].split()
            before, a, b = f[4:7]
            if (sig[0] == 'Sigmoid' and sig[2:4] == ['1', '1'] and sig[4] in (a, b)
                    and mul[0] == 'BinaryOp' and mul[2:4] == ['2', '1'] and mul[7:] == ['0=2']
                    and sorted(mul[4:6]) == sorted([b if sig[4] == a else a, sig[5]])
                    and used.get(a) == used.get(b) == used.get(sig[5]) == 1):
                out.append(f'Swish swish_{folded} 1 1 {before} {mul[6]}')
                i += 3
                folded += 1
                continue
        out.append(lines[i])
        i += 1
    return out, folded


def main():
    source, target = map(Path, sys.argv[1:3])
    lines = source.read_text().splitlines()
    if lines[0] != '7767517':
        raise SystemExit('Not an NCNN param file')
    layers, folded = fold(lines[2:])
    blobs = set()
    for line in layers:
        f = line.split()
        bottoms, tops = int(f[2]), int(f[3])
        blobs.update(f[4 + bottoms:4 + bottoms + tops])
    if any(l.split()[0] == 'Sigmoid' for l in layers):
        raise SystemExit('SiLU lowering only partly folded')
    target.write_text('7767517\n' + f'{len(layers)} {len(blobs)}\n' + '\n'.join(layers) + '\n')
    print(f'Folded {folded} SiLU groups: {len(lines) - 2} -> {len(layers)} layers')


if __name__ == '__main__':
    main()
