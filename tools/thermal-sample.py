#!/usr/bin/env python3
"""Sample ARM clock, temperature and throttling for a while, then summarize
the video and detector counters logged in the same window (run as root).
Usage: thermal-sample.py seconds output.json"""
import json
import re
import statistics
import subprocess
import sys
import time


def vc(*args):
    return subprocess.run(['vcgencmd', *args], capture_output=True, text=True).stdout.strip()


def numbers(line):
    return {k: float(v) for k, v in re.findall(r'(\w+)=([-\d.]+)', line)}


def main():
    seconds, output = float(sys.argv[1]), sys.argv[2]
    since = subprocess.run(['date', '--iso-8601=seconds'], capture_output=True, text=True).stdout.strip()
    samples, start = [], time.monotonic()
    while time.monotonic() - start < seconds:
        samples.append({'mhz': int(vc('measure_clock', 'arm').split('=')[1]) / 1e6,
                        'temp_c': float(vc('measure_temp').split('=')[1].rstrip("'C")),
                        'throttled': vc('get_throttled').split('=')[1]})
        time.sleep(5)
    log = subprocess.run(['journalctl', '-u', 'rear-camera', '-u', 'rear-camera-detector', '--since', since,
                          '-o', 'cat', '--no-pager'], capture_output=True, text=True).stdout.splitlines()
    stats = [numbers(x.replace('age_all_ms ', '')) for x in log if x.startswith('stats: ')]
    detector = [numbers(x) for x in log if x.startswith('detector: ')]
    mhz = [s['mhz'] for s in samples]
    result = {
        'seconds': seconds, 'samples': len(samples),
        'arm_mhz_mean': statistics.mean(mhz), 'arm_mhz_min': min(mhz),
        'share_at_1400_mhz': sum(m >= 1399 for m in mhz) / len(mhz),
        'temp_c_mean': statistics.mean(s['temp_c'] for s in samples),
        'temp_c_max': max(s['temp_c'] for s in samples),
        'throttled_values': sorted({s['throttled'] for s in samples}),
        'fps_mean': statistics.mean(s['fps'] for s in stats) if stats else None,
        'age_p50_mean_ms': statistics.mean(s['p50_lt'] for s in stats) if stats else None,
        'decoded_ms': statistics.mean(s['decoded'] for s in stats) if stats else None,
        'projected_ms': statistics.mean(s['projected'] for s in stats) if stats else None,
        'video_cpu_core': statistics.mean(s['cpu_core'] for s in stats) if stats else None,
        'damaged': sum(s['damaged'] for s in stats), 'late_display': sum(s['late_display'] for s in stats),
    }
    if len(detector) >= 2:
        # The log holds running means; recover the mean over this window only.
        a, b = detector[0], detector[-1]
        n = b['processed'] - a['processed']
        if n > 0:
            result['inference_ms'] = (b['mean_inference_ms'] * b['processed'] - a['mean_inference_ms'] * a['processed']) / n
            result['analyses_per_s'] = n / seconds
    json.dump(result, open(output, 'w'), indent=1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
