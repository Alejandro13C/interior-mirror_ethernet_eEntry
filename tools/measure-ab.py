#!/usr/bin/env python3
"""Alternate video binaries (ABAB...) on the Pi and measure each run.

Run as root from the project directory. Each run installs one variant as
build/rear-camera, applies its extra environment through a temporary
systemd drop-in (checked in /proc/<pid>/environ), restarts the services,
waits for warm-up, samples CPU, RAM, temperature, ARM clock and throttling,
then stops the video service so it prints its totals. The original binaries
(video and detector) and unit configuration are always restored, and the
service restarted.
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path

LIVE = Path('build/rear-camera')
DETECTOR = Path('build/vehicle-detector')
ENV_FILE = Path('/run/rear-camera-measure.env')
DROPINS = [Path(f'/run/systemd/system/{unit}.service.d/90-measure.conf')
           for unit in ('rear-camera', 'rear-camera-detector')]


def install(binary, target=LIVE):
    # A running executable cannot be overwritten; rename over it atomically.
    temporary = target.with_name(target.name + '.measure')
    shutil.copy2(binary, temporary)
    os.replace(temporary, target)


def sh(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def apply_env(settings):
    """EnvironmentFile= in a later drop-in overrides /etc/default/rear-camera,
    unlike `systemctl set-environment`, which the unit's own files override."""
    if settings:
        ENV_FILE.write_text(''.join(f'{s}\n' for s in settings))
        for dropin in DROPINS:
            dropin.parent.mkdir(parents=True, exist_ok=True)
            dropin.write_text(f'[Service]\nEnvironmentFile={ENV_FILE}\n')
    else:
        clear_env()
    sh('systemctl', 'daemon-reload')


def clear_env():
    for path in [ENV_FILE, *DROPINS]:
        path.unlink(missing_ok=True)


def check_env(n, settings):
    environ = Path(f'/proc/{n}/environ').read_bytes().split(b'\0')
    missing = [s for s in settings if s.encode() not in environ]
    if missing:
        raise RuntimeError(f'environment not applied to PID {n}: {missing}')


def pid(service):
    return int(sh('systemctl', 'show', service, '--property=MainPID', '--value'))


def ticks(n):
    if not n:
        return None
    fields = Path(f'/proc/{n}/stat').read_text().rsplit(')', 1)[1].split()
    status = dict(x.split(':', 1) for x in Path(f'/proc/{n}/status').read_text().splitlines() if ':' in x)
    return int(fields[11]) + int(fields[12]), int(status['VmHWM'].split()[0]) / 1024


def vc(*args):
    try:
        return sh('vcgencmd', *args)
    except (OSError, subprocess.CalledProcessError):
        return ''


def numbers(line):
    return {k: float(v) for k, v in re.findall(r'(\w+)=([-\d.]+)', line)}


def run(variant, binary, warmup, seconds, env):
    video_binary, _, detector_binary = binary.partition(',')
    install(video_binary)
    install(detector_binary or DETECTOR_BACKUP, DETECTOR)
    apply_env(env)
    sh('systemctl', 'restart', 'rear-camera')
    since = sh('date', '--iso-8601=seconds')
    time.sleep(warmup)
    video, detector = pid('rear-camera'), pid('rear-camera-detector')
    check_env(video, env)
    hz = int(sh('getconf', 'CLK_TCK'))
    first = {'video': ticks(video), 'detector': ticks(detector)}
    start, samples = time.monotonic(), []
    while time.monotonic() - start < seconds:
        time.sleep(2)
        clock = vc('measure_clock', 'arm')
        samples.append({
            'temperature_c': int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000,
            'arm_hz': int(clock.split('=')[1]) if '=' in clock else None,
            'throttled': vc('get_throttled').split('=')[-1],
        })
    elapsed = time.monotonic() - start
    last = {'video': ticks(video), 'detector': ticks(detector)}
    restarted = (video, detector) != (pid('rear-camera'), pid('rear-camera-detector'))
    sh('systemctl', 'stop', 'rear-camera')
    time.sleep(1)
    log = sh('journalctl', f'_PID={video}', '--since', since, '-o', 'cat', '--no-pager').splitlines()
    det = sh('journalctl', f'_PID={detector}', '--since', since, '-o', 'cat', '--no-pager').splitlines() if detector else []
    totals = next((numbers(x) for x in log if x.startswith('packets=')), {})
    latency = next((numbers(x.replace('p95_lt', 'p95')) for x in log if x.startswith('local_first')), {})
    every = [numbers(x.replace('age_all_ms ', '')) for x in log if x.startswith('stats: ')]
    clocks = [s['arm_hz'] for s in samples if s['arm_hz']]
    result = {
        'variant': variant, 'env': env, 'seconds': elapsed, 'warmup_s': warmup,
        'restarted_during_run': restarted,
        'fps_whole_run': totals.get('rendered', 0) / totals['wall_s'] if totals.get('wall_s') else None,
        'totals': totals, 'presented_latency_ms': latency,
        'stale_log': [x for x in log if x.startswith(('Video stale', 'Video resumed', 'Frames arrive'))],
        'cpu_core_percent': {k: 100 * (last[k][0] - first[k][0]) / hz / elapsed
                             for k in first if first[k] and last[k]},
        'peak_rss_mib': {k: last[k][1] for k in last if last[k]},
        'temperature_max_c': max(s['temperature_c'] for s in samples),
        'arm_mhz_mean': statistics.mean(clocks) / 1e6 if clocks else None,
        'arm_mhz_min': min(clocks) / 1e6 if clocks else None,
        'throttled': sorted({s['throttled'] for s in samples}),
        'detector_last': next((numbers(x) for x in reversed(det) if x.startswith('detector:')), {}),
    }
    # Periodic lines (new binaries only) exclude warm-up exactly. Durations come
    # from consecutive timestamps, so intervals without any frame still count.
    steady = [(prev['t'], s) for prev, s in zip(every, every[1:]) if prev['t'] >= warmup]
    if steady:
        span = steady[-1][1]['t'] - steady[0][0]
        lines = [s for _, s in steady]
        result['steady'] = {
            'fps': sum(s['presented'] for s in lines) / span if span else None,
            **{k: sum(s[k] for s in lines) for k in ('presented', 'late_display', 'damaged', 'sequence_errors', 'stale', 'socket_dropped', 'stale_events')},
            **{f'{k}_ms': statistics.mean(s[k] for s in lines) for k in ('assembled', 'decoded', 'projected') if all(k in s for s in lines)},
            'p95_lt_ms_max': max(s['p95_lt'] for s in lines),
            'p99_lt_ms_max': max(s['p99_lt'] for s in lines),
            'max_gap_ms': max(s['max_gap_ms'] for s in lines),
        }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--variant', action='append', required=True, metavar='NAME=VIDEO[,DETECTOR]',
                   help='video binary and, optionally, a detector binary for this variant')
    p.add_argument('--env', action='append', default=[], metavar='NAME:KEY=VALUE',
                   help='extra service environment for one variant (video and detector)')
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--seconds', type=float, default=120)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    variants = dict(v.split('=', 1) for v in a.variant)
    env = {name: [] for name in variants}
    for item in a.env:
        name, setting = item.split(':', 1)
        env[name].append(setting)
    a.output.mkdir(parents=True, exist_ok=True)
    backup = a.output / 'original-rear-camera'
    shutil.copy2(LIVE, backup)
    global DETECTOR_BACKUP
    DETECTOR_BACKUP = a.output / 'original-vehicle-detector'
    shutil.copy2(DETECTOR, DETECTOR_BACKUP)
    runs = []
    try:
        for r in range(a.rounds):
            for name, binary in variants.items():
                print(f'round {r + 1}: {name}', flush=True)
                runs.append(run(name, binary, a.warmup, a.seconds, env[name]))
                # Keep partial results if a later run fails.
                (a.output / 'runs.json').write_text(json.dumps(runs, indent=1) + '\n')
                print(json.dumps({k: runs[-1][k] for k in ('variant', 'fps_whole_run', 'cpu_core_percent', 'arm_mhz_mean', 'temperature_max_c')}), flush=True)
    finally:
        clear_env()
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        install(backup)
        install(DETECTOR_BACKUP, DETECTOR)
        # restart, not start: an exception may leave a variant running.
        sh('systemctl', 'restart', 'rear-camera')
    summary = {}
    for name in variants:
        mine = [x for x in runs if x['variant'] == name]
        def stat(get):
            values = [v for v in map(get, mine) if v is not None]
            return {'mean': statistics.mean(values), 'sd': statistics.stdev(values) if len(values) > 1 else 0,
                    'values': values} if values else None
        summary[name] = {
            'fps_whole_run': stat(lambda x: x['fps_whole_run']),
            'fps_steady': stat(lambda x: x.get('steady', {}).get('fps')),
            'late_display': stat(lambda x: x['totals'].get('late_display')),
            'damaged': stat(lambda x: x['totals'].get('damaged')),
            'sequence_errors': stat(lambda x: x['totals'].get('sequence_errors')),
            'latency_mean_ms': stat(lambda x: x['presented_latency_ms'].get('mean')),
            'decoded_ms': stat(lambda x: x.get('steady', {}).get('decoded_ms')),
            'projected_ms': stat(lambda x: x.get('steady', {}).get('projected_ms')),
            'video_cpu': stat(lambda x: x['cpu_core_percent'].get('video')),
            'detector_cpu': stat(lambda x: x['cpu_core_percent'].get('detector')),
            'detector_inference_ms': stat(lambda x: x['detector_last'].get('mean_inference_ms')),
            'arm_mhz': stat(lambda x: x['arm_mhz_mean']),
            'temperature_max_c': stat(lambda x: x['temperature_max_c']),
        }
    out = {'variants': variants, 'env': env, 'rounds': a.rounds, 'warmup_s': a.warmup,
           'seconds': a.seconds, 'summary': summary, 'runs': runs}
    (a.output / 'result.json').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
