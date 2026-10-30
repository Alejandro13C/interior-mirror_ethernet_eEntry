#!/usr/bin/env bash
# Install a staged copy of the project on the Pi, keeping a backup and rolling
# back automatically if the build fails or video/detection do not come back.
# Run as root from the staged directory:
#   sudo NCNN=<ncnn install dir> tools/deploy-pi.sh [live project dir]
# NCNN (optional) installs that library as build/ncnn-install and rebuilds
# the detector (needed when the model uses layers the old build lacks).
set -euo pipefail
# With the read-only root every change lives in RAM and is lost at the next boot.
if grep -qw 'overlayroot=tmpfs' /proc/cmdline; then
    echo 'Read-only root: run "raspi-config nonint disable_overlayfs", reboot and deploy again' >&2
    exit 1
fi
stage=$(pwd) live=${1:-/home/alejandro/Rear_camera}
user=$(stat -c %U "$live") group=$(stat -c %G "$live")
backup="$live/build/deploy-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
tar czf "$backup/tree.tgz" -C "$live" --exclude=./build .
cp -a "$live/build/rear-camera" "$backup/"
[[ -e $live/build/vehicle-detector ]] && cp -a "$live/build/vehicle-detector" "$backup/"
[[ -n ${NCNN:-} && -d $live/build/ncnn-install ]] && tar czf "$backup/ncnn-install.tgz" -C "$live/build" ncnn-install
cp -a /etc/systemd/system/rear-camera.service /etc/systemd/system/rear-camera-detector.service "$backup/"
[[ -e /etc/udev/rules.d/90-rear-camera-net.rules ]] && cp -a /etc/udev/rules.d/90-rear-camera-net.rules "$backup/"
mkdir -p "$backup/console"
for asset in display-console.sh boot-logo boot-logo.jpg; do
    [[ ! -e /usr/local/lib/rear-camera/$asset ]] || cp -a "/usr/local/lib/rear-camera/$asset" "$backup/console/"
done
echo "Backup: $backup"

replace() { cp -a "$1" "$2.deploy" && mv -f "$2.deploy" "$2"; } # running binaries: rename over them
rollback() {
    echo "Deployment failed; restoring $backup" >&2
    find "$live" -mindepth 1 -maxdepth 1 ! -name build -exec rm -rf {} +
    tar xzf "$backup/tree.tgz" -C "$live"
    replace "$backup/rear-camera" "$live/build/rear-camera"
    [[ -e $backup/vehicle-detector ]] && replace "$backup/vehicle-detector" "$live/build/vehicle-detector"
    if [[ -e $backup/ncnn-install.tgz ]]; then
        rm -rf "$live/build/ncnn-install"
        tar xzf "$backup/ncnn-install.tgz" -C "$live/build"
    fi
    cp -a "$backup/rear-camera.service" "$backup/rear-camera-detector.service" /etc/systemd/system/
    for asset in display-console.sh boot-logo boot-logo.jpg; do
        rm -f "/usr/local/lib/rear-camera/$asset"
        [[ ! -e $backup/console/$asset ]] || cp -a "$backup/console/$asset" /usr/local/lib/rear-camera/
    done
    rm -f /etc/udev/rules.d/90-rear-camera-net.rules
    [[ -e $backup/90-rear-camera-net.rules ]] && cp -a "$backup/90-rear-camera-net.rules" /etc/udev/rules.d/
    systemctl daemon-reload
    systemctl restart rear-camera
}
trap rollback ERR

# Sources, documentation and model descriptions; build products and weights stay in place.
rsync -a --delete --exclude=/build/ --exclude='/models/*.bin' --exclude='/models/*.onnx' \
    --chown="$user:$group" "$stage/" "$live/"
cd "$live"
sudo -u "$user" make -B build/rear-camera build/boot-logo >/dev/null
if [[ -n ${NCNN:-} ]]; then
    rsync -a --delete --chown="$user:$group" "$NCNN/" "$live/build/ncnn-install/"
    sudo -u "$user" make -B build/vehicle-detector >/dev/null
fi
install -D -o root -g root -m 0755 display-console.sh /usr/local/lib/rear-camera/display-console.sh
install -o root -g root -m 0755 build/boot-logo /usr/local/lib/rear-camera/boot-logo
install -o root -g root -m 0644 logo/logo-renault-sport-invertido.jpg /usr/local/lib/rear-camera/boot-logo.jpg
install -m 0644 systemd/rear-camera.service systemd/rear-camera-detector.service /etc/systemd/system/
install -m 0644 systemd/90-rear-camera-net.rules /etc/udev/rules.d/
udevadm control --reload
udevadm trigger --action=change --subsystem-match=net --sysname-match=eth0
systemctl daemon-reload
since=$(date --iso-8601=seconds)
systemctl restart rear-camera
sleep 15
systemctl is-active --quiet rear-camera
systemctl is-active --quiet rear-camera-detector
log=$(journalctl -u rear-camera --since "$since" -o cat --no-pager)
detector=$(journalctl -u rear-camera-detector --since "$since" -o cat --no-pager)
grep -q '^stats: ' <<<"$log"                      # video frames are flowing
grep -q 'Vehicle detector ready' <<<"$detector"   # the model loaded
# A negated command never triggers errexit/ERR, hence the explicit `false`.
if grep -qE 'Invalid|failed|Watchdog triggered' <<<"$log"; then false; fi
trap - ERR
grep -E '^(Panini map|Camera SPS|Receiver C|stats: )' <<<"$log" | head -5
echo "Deployed. Roll back with the files in $backup"
