#!/usr/bin/env bash
# Exercise the stale-picture monitor on the Pi with a candidate binary:
# CPU starvation of the video cores, camera silence (SIGUSR1 pauses the input
# inside the receiver, like a cut cable) and a link drop (closes the socket:
# a restart test, not a stale-picture test).
# Run as root from the project directory; the original binary is restored.
# Usage: tools/fault-injection.sh candidate-binary output.log
set -euo pipefail
candidate=$1 output=$2
backup=$(mktemp -p build rear-camera.fault.XXXXXX)
cp -a build/rear-camera "$backup"
hogs=()
restore() {
    kill "${hogs[@]}" 2>/dev/null || true
    ip link set eth0 up || true
    cp -a "$backup" build/rear-camera.restore && mv -f build/rear-camera.restore build/rear-camera
    rm -f "$backup"
    systemctl restart rear-camera
}
trap restore EXIT
starve() { # seconds: two busy loops per video core, above the video's priority
    for core in 0 1; do for _ in 1 2; do
        taskset -c "$core" nice -n -5 python3 -c 'while True: pass' & hogs+=($!)
    done; done
    sleep "$1"; kill "${hogs[@]}"; hogs=()
}
pause_input() { systemctl kill -s USR1 --kill-whom=main rear-camera; }
cp -a "$candidate" build/rear-camera.candidate && mv -f build/rear-camera.candidate build/rear-camera
systemctl restart rear-camera
sleep 25
since=$(date --iso-8601=seconds)
echo "$(date +%T.%N) starve 3 s"; starve 3; sleep 8
echo "$(date +%T.%N) starve 9 s"; starve 9; sleep 12
echo "$(date +%T.%N) camera silent 1 s"; pause_input; sleep 1; pause_input; sleep 8
echo "$(date +%T.%N) camera silent until the watchdog"; pause_input; sleep 15
echo "$(date +%T.%N) link down 4 s (restart test)"; ip link set eth0 down; sleep 4; ip link set eth0 up; sleep 15
echo "$(date +%T.%N) done"
journalctl -u rear-camera --since "$since" -o short-precise --no-pager | grep -vE 'stats:|overlay:' > "$output"
