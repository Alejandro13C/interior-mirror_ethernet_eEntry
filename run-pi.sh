#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Exit 78 (EX_CONFIG) only for invalid settings: systemd does not retry those.
interface=${CAMERA_INTERFACE:-eth0}
args=(--interface "$interface" --hardware --kms --rotate "${CAMERA_ROTATION:-0}")
case ${CAMERA_MIRROR:-0} in
    0) ;;
    1) args+=(--mirror) ;;
    *) echo 'CAMERA_MIRROR must be 0 or 1' >&2; exit 78 ;;
esac
case ${CAMERA_PANINI:-0} in
    0) ;;
    1) args+=(--panini --compression "${CAMERA_COMPRESSION:-1}"
              --lens-fov "${CAMERA_LENS_FOV:-180}" --view-fov "${CAMERA_VIEW_FOV:-130}"
              --pitch "${CAMERA_PITCH:-0}") ;;
    *) echo 'CAMERA_PANINI must be 0 or 1' >&2; exit 78 ;;
esac
case ${CAMERA_DETECTION:-0} in
    0) ;;
    1) args+=(--detect) ;;
    *) echo 'CAMERA_DETECTION must be 0 or 1' >&2; exit 78 ;;
esac
while [[ ! -e /sys/class/net/$interface ]]; do sleep 0.5; done
# Started early in boot: give udev up to 10 s to create the hardware decoder.
for _ in {1..20}; do [[ -e /dev/video10 ]] && break; sleep 0.5; done
# Every boot creates new /dev nodes, so GStreamer rescans its v4l2, va and vulkan
# plugins (~5 s before the first frame). Skip that check while the cache lists the
# elements in use and no package has changed the GStreamer library directories since
# (not all of /usr/lib: ProtectKernelModules mounts a new empty /usr/lib/modules).
registry_fresh() {
    local registry directory found=0
    for registry in "${XDG_CACHE_HOME:-$HOME/.cache}"/gstreamer-1.0/registry.*.bin; do
        [[ -e $registry ]] || return 1
        grep -qa v4l2h264dec "$registry" && grep -qa kmssink "$registry" || return 1
        for directory in /usr/lib/*-linux-*/ /usr/lib/*-linux-*/gstreamer-1.0/; do
            [[ $directory -nt $registry ]] && return 1
        done
        found=1
    done
    (( found ))
}
registry_fresh && export GST_REGISTRY_UPDATE=no
# A USB adapter that is still settling is transient: plain failure, retried.
ip link set dev "$interface" up || exit 1
exec ./build/rear-camera "${args[@]}"
