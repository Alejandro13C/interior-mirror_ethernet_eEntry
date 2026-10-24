#!/usr/bin/env python3
"""Comprueba estabilidad del servicio; no certifica imagen ni latencia visual."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def service():
    run = subprocess.run(['systemctl', 'show', 'rear-camera',
        '--property=ActiveState,SubState,MainPID,NRestarts,MemoryCurrent,CPUUsageNSec'],
        check=True, capture_output=True, text=True, timeout=5)
    return dict(line.split('=', 1) for line in run.stdout.splitlines() if '=' in line)


def sample():
    state = service()
    pid = int(state.get('MainPID', '0'))
    state['rss_kb'] = None
    if pid:
        try:
            for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    state['rss_kb'] = int(line.split()[1])
        except FileNotFoundError:
            pass
    thermal = Path('/sys/class/thermal/thermal_zone0/temp')
    state['temperature_c'] = int(thermal.read_text()) / 1000 if thermal.exists() else None
    state['throttled'] = None
    try:
        result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True,
                                text=True, check=True, timeout=3)
        state['throttled'] = int(result.stdout.strip().split('=')[1], 16)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 86400 or not 1 <= args.interval <= 60:
        parser.error('seconds: 1..86400; interval: 1..60')
    started = time.monotonic()
    first = sample()
    samples = [first]
    problems = set()
    while True:
        current = samples[-1]
        if (current.get('ActiveState') != 'active' or current.get('SubState') != 'running'
                or int(current.get('MainPID', '0')) <= 0):
            problems.add('servicio sin ejecutar')
        if current.get('MainPID') != first.get('MainPID') or current.get('NRestarts') != first.get('NRestarts'):
            problems.add('reinicio durante la observacion')
        if current['throttled'] is not None and current['throttled'] & 0xf:
            problems.add('alimentacion/frecuencia/temperatura limitada actualmente')
        remaining = args.seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        time.sleep(min(args.interval, remaining))
        samples.append(sample())
    rss = [s['rss_kb'] for s in samples if s['rss_kb'] is not None]
    temperatures = [s['temperature_c'] for s in samples if s['temperature_c'] is not None]
    print(json.dumps({'seconds': round(time.monotonic() - started, 2),
        'samples': len(samples), 'first': first, 'last': samples[-1],
        'rss_min_kb': min(rss) if rss else None, 'rss_max_kb': max(rss) if rss else None,
        'temperature_max_c': max(temperatures) if temperatures else None,
        'problems': sorted(problems),
        'scope': 'Servicio y recursos; no comprueba fotogramas ni latencia de pantalla.'},
        ensure_ascii=False, indent=2))
    return bool(problems)


if __name__ == '__main__':
    raise SystemExit(main())
