#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
interface=${CAMERA_INTERFACE:-eth0}
while [[ ! -e /sys/class/net/$interface ]]; do sleep 0.5; done
ip link set dev "$interface" up
args=(--interface "$interface" --hardware --kms --rotate "${CAMERA_ROTATION:-0}")
case ${CAMERA_MIRROR:-0} in
    0) ;;
    1) args+=(--mirror) ;;
    *) echo 'CAMERA_MIRROR must be 0 or 1' >&2; exit 2 ;;
esac
case ${CAMERA_PANINI:-0} in
    0) ;;
    1) args+=(--panini --compression "${CAMERA_COMPRESSION:-1}"
              --lens-fov "${CAMERA_LENS_FOV:-180}" --view-fov "${CAMERA_VIEW_FOV:-120}"
              --pitch "${CAMERA_PITCH:-0}") ;;
    *) echo 'CAMERA_PANINI must be 0 or 1' >&2; exit 2 ;;
esac
case ${CAMERA_DETECTION:-0} in
    0) ;;
    1) args+=(--detect) ;;
    *) echo 'CAMERA_DETECTION must be 0 or 1' >&2; exit 2 ;;
esac
exec ./build/rear-camera "${args[@]}"
