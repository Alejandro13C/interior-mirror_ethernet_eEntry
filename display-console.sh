#!/usr/bin/env bash
# Consola dedicada: los mensajes de arranque permanecen en tty1.
set -euo pipefail
video_tty=/dev/tty7
# Restablecer atributos ANTES de borrar: el borrado usa el color de fondo actual.
printf '\033[0m\033[37;40m\033[2J\033[3J\033[H\033[?25l' > "$video_tty"
setterm --term linux --blank 0 --cursor off < "$video_tty" > "$video_tty"
chvt 7
printf '\033[12;35HSIN VIDEO\033[H' > "$video_tty"
