#!/usr/bin/env python3
"""Extract the camera H.264 stream from an Ethernet PCAP, optionally as PNG.

Filtering and frame assembly follow src/camera_rtp.c: source MAC
44:3c:88:24:70:22, up to two VLAN tags, IPv6 to ff14::2:d (hop-by-hop,
routing and destination options allowed), UDP 42800, RTP version 2 PT 127,
Annex B payload grouped by RTP timestamp. A frame must start with an SPS and
have no sequence gap; damaged frames are dropped, never patched.

Usage:
  extract-h264.py camera.pcap out.h264 [--png DIR] [--frames N] [--every K]
PNG frames are decoded with OpenCV's FFmpeg backend (cv2.VideoCapture).
"""
import argparse
import json
import os
import struct
import sys

MAC = bytes.fromhex('443c88247022')
GROUP = bytes([0xff, 0x14] + [0] * 11 + [2, 0, 0x0d])  # ff14::2:d
PORT = 42800
FRAME_LIMIT = 4 * 1024 * 1024  # CAMERA_FRAME_LIMIT


def pcap_records(path):
    with open(path, 'rb') as f:
        header = f.read(24)
        if len(header) < 24:
            sys.exit('not a PCAP file')
        magic = header[:4]
        if magic == b'\x0a\x0d\x0d\x0a':
            sys.exit('pcapng is not supported: capture with tcpdump -w, or convert with editcap -F pcap')
        if magic not in (b'\xd4\xc3\xb2\xa1', b'\xa1\xb2\xc3\xd4', b'\x4d\x3c\xb2\xa1', b'\xa1\xb2\x3c\x4d'):
            sys.exit('not a PCAP file')
        e = '<' if magic in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1') else '>'
        if struct.unpack(e + 'I', header[20:24])[0] != 1:
            sys.exit('PCAP link type is not Ethernet')
        while True:
            h = f.read(16)
            if not h:
                return
            if len(h) < 16:
                sys.exit('truncated PCAP record header')
            _, _, length, original = struct.unpack(e + 'IIII', h)
            data = f.read(length)
            if len(data) != length or length != original:
                sys.exit('truncated packet in PCAP (use tcpdump -s 0)')
            yield data


def rtp_packet(b):
    """Mirror of camera_packet(): (payload, seq, timestamp, ssrc) or None."""
    n = len(b)
    if n < 14 or b[6:12] != MAC:
        return None
    ip, kind, tags = 14, b[12:14], 0
    while kind in (b'\x81\x00', b'\x88\xa8'):
        tags += 1
        if tags > 2 or n - ip < 4:
            return None
        kind, ip = b[ip + 2:ip + 4], ip + 4
    if kind != b'\x86\xdd' or n - ip < 40 or b[ip] >> 4 != 6 or b[ip + 24:ip + 40] != GROUP:
        return None
    end = ip + 40 + struct.unpack('>H', b[ip + 4:ip + 6])[0]
    if end > n:
        return None
    nxt, udp = b[ip + 6], ip + 40
    for _ in range(8):
        if nxt == 17:
            break
        if nxt not in (0, 43, 60) or end - udp < 8:
            return None
        extra = (b[udp + 1] + 1) * 8
        if extra > end - udp:
            return None
        nxt, udp = b[udp], udp + extra
    if nxt != 17 or end - udp < 20 or struct.unpack('>H', b[udp + 2:udp + 4])[0] != PORT:
        return None
    length = struct.unpack('>H', b[udp + 4:udp + 6])[0]
    if length < 20 or length > end - udp:
        return None
    r = b[udp + 8:udp + length]
    rn = len(r)
    if r[0] >> 6 != 2 or (r[1] & 127) != 127:
        return None
    off = 12 + 4 * (r[0] & 15)
    if off > rn:
        return None
    if r[0] & 16:
        if rn - off < 4:
            return None
        ext = 4 + 4 * struct.unpack('>H', r[off + 2:off + 4])[0]
        if ext > rn - off:
            return None
        off += ext
    if r[0] & 32:
        pad = r[rn - 1]
        if not pad or pad > rn - off:
            return None
        rn -= pad
    if off >= rn:
        return None
    seq, ts, ssrc = struct.unpack('>HII', r[2:12])
    return r[off:rn], seq, ts, ssrc


def starts_sps(p):
    return (len(p) >= 5 and p[:4] == b'\0\0\0\1' and p[4] & 31 == 7) or \
           (len(p) >= 4 and p[:3] == b'\0\0\1' and p[3] & 31 == 7)


def frames(path, stats):
    """Mirror of camera_assemble(); yields complete Annex B access units."""
    have = valid = False
    data, seq, ts, ssrc, behind = bytearray(), 0, 0, 0, 0
    for record in pcap_records(path):
        p = rtp_packet(record)
        if p is None:
            continue
        payload, pseq, pts, pssrc = p
        stats['packets'] += 1
        if have and pssrc != ssrc:
            have, data = False, bytearray()
        if have and pseq != (seq + 1) & 0xffff:
            delta = (pseq - seq) & 0xffff
            if not delta:
                stats['duplicates'] += 1
                continue
            if delta > 0x8000:
                behind += 1
                if behind < 32:
                    stats['late_packets'] += 1
                    continue
                stats['resyncs'] += 1
                have = False
            else:
                stats['sequence_errors'] += 1
                valid = False
        behind = 0
        if not have or ts != pts:
            if have:
                if valid and data:
                    stats['complete'] += 1
                    yield bytes(data)
                else:
                    stats['damaged'] += 1
            data = bytearray()
            ts, ssrc, have = pts, pssrc, True
            valid = starts_sps(payload)
        seq = pseq
        if not valid:
            continue
        if len(data) + len(payload) > FRAME_LIMIT:
            valid = False
            stats['oversized'] += 1
            continue
        data += payload
    # The last frame is never emitted, as in the receiver: its end is unknown.
    if have:
        stats['unfinished_tail'] = 1


def write_png(h264, folder, limit, every):
    import cv2
    os.makedirs(folder, exist_ok=True)
    cap = cv2.VideoCapture(h264, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        sys.exit('OpenCV cannot open %s (FFmpeg backend missing?)' % h264)
    index = written = 0
    size = None
    while limit <= 0 or written < limit:
        ok, image = cap.read()
        if not ok:
            break
        size = image.shape[1], image.shape[0]
        if index % every == 0:
            cv2.imwrite(os.path.join(folder, 'frame_%04d.png' % index), image)
            written += 1
        index += 1
    cap.release()
    return {'decoded': index, 'png_written': written, 'size': size}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('pcap')
    ap.add_argument('output', help='Annex B .h264 file to write')
    ap.add_argument('--png', metavar='DIR', help='also decode frames to PNG in DIR')
    ap.add_argument('--frames', type=int, default=0, help='PNG frames to write (0 = all)')
    ap.add_argument('--every', type=int, default=1, help='write one PNG every K decoded frames')
    a = ap.parse_args()
    if a.every < 1:
        ap.error('--every must be >= 1')
    stats = dict.fromkeys(('packets', 'complete', 'damaged', 'sequence_errors', 'duplicates',
                           'late_packets', 'resyncs', 'oversized', 'unfinished_tail'), 0)
    size = 0
    with open(a.output, 'wb') as out:
        for frame in frames(a.pcap, stats):
            out.write(frame)
            size += len(frame)
    stats['h264_bytes'] = size
    if not stats['complete']:
        print(json.dumps(stats))
        sys.exit('no complete camera frame found')
    if a.png:
        stats.update(write_png(a.output, a.png, a.frames, a.every))
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
