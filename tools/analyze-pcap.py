#!/usr/bin/env python3
"""Camera RTP timing from an Ethernet PCAP: losses, marker bit and burst shape.

Answers whether a frame can be delivered on its last packet (RTP marker bit)
instead of waiting for the next timestamp, and how long that wait is.
Usage: analyze-pcap.py capture.pcap
"""
import json
import statistics
import struct
import sys

MAC = bytes.fromhex('443c88247022')
GROUP = bytes([0xff, 0x14] + [0] * 11 + [2, 0, 0x0d])  # ff14::2:d


def packets(path):
    with open(path, 'rb') as f:
        header = f.read(24)
        magic = header[:4]
        little = magic in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1')
        nano = magic in (b'\x4d\x3c\xb2\xa1', b'\xa1\xb2\x3c\x4d')
        e = '<' if little else '>'
        while True:
            h = f.read(16)
            if len(h) < 16:
                return
            sec, frac, length, original = struct.unpack(e + 'IIII', h)
            data = f.read(length)
            yield sec + frac / (1e9 if nano else 1e6), data


def rtp(data):
    if data[6:12] != MAC:
        return None
    ip, kind = 14, data[12:14]
    while kind in (b'\x81\x00', b'\x88\xa8'):
        kind, ip = data[ip + 2:ip + 4], ip + 4
    if kind != b'\x86\xdd' or data[ip + 24:ip + 40] != GROUP or data[ip + 6] != 17:
        return None
    udp = ip + 40
    if struct.unpack('>H', data[udp + 2:udp + 4])[0] != 42800:
        return None
    r = data[udp + 8:]
    seq, timestamp, ssrc = struct.unpack('>HII', r[2:12])
    return {'seq': seq, 'ts': timestamp, 'ssrc': ssrc, 'marker': bool(r[1] & 0x80),
            'size': len(r), 'nal': r[12 + 4 * (r[0] & 15)] & 31 if len(r) > 12 else None}


def main():
    rows = [(t, p) for t, d in packets(sys.argv[1]) if (p := rtp(d))]
    gaps = sum(1 for (_, a), (_, b) in zip(rows, rows[1:]) if (b['seq'] - a['seq']) % 65536 != 1)
    frames = {}
    for t, p in rows:
        frames.setdefault(p['ts'], []).append((t, p))
    order = sorted(frames, key=lambda ts: frames[ts][0][0])
    spread, wait, marked = [], [], 0
    for ts, nxt in zip(order, order[1:]):
        group = frames[ts]
        spread.append((group[-1][0] - group[0][0]) * 1e3)
        # Today a frame is emitted when the first packet of the next one arrives.
        wait.append((frames[nxt][0][0] - group[-1][0]) * 1e3)
        marked += group[-1][1]['marker'] and not any(p['marker'] for _, p in group[:-1])
    intervals = [(frames[b][0][0] - frames[a][0][0]) * 1e3 for a, b in zip(order, order[1:])]
    q = lambda v, x: statistics.quantiles(v, n=100)[x - 1]
    print(json.dumps({
        'rtp_packets': len(rows), 'frames': len(frames), 'ssrcs': sorted({p['ssrc'] for _, p in rows}),
        'sequence_gaps': gaps,
        'packets_per_frame_mean': len(rows) / len(frames),
        'frame_interval_ms': {'mean': statistics.mean(intervals), 'p95': q(intervals, 95)},
        'fps': 1000 / statistics.mean(intervals),
        'marker_only_on_last_packet': f'{marked}/{len(order) - 1}',
        'frame_spread_ms': {'mean': statistics.mean(spread), 'p95': q(spread, 95), 'max': max(spread)},
        'wait_for_next_frame_ms': {'mean': statistics.mean(wait), 'p50': q(wait, 50), 'p95': q(wait, 95)},
    }, indent=1))


if __name__ == '__main__':
    main()
