#!/usr/bin/env bash
# A token copied on each PC can be pasted on the other (needs both PCs up and the GNOME extension loaded).
set -u
TWENV='export XDG_RUNTIME_DIR=/run/user/$(id -u); export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-$(ls $XDG_RUNTIME_DIR | grep -m1 "^wayland-[0-9]*$")}'

tok=main-$RANDOM$RANDOM
printf %s "$tok" | wl-copy
got=
for _ in $(seq 20); do
  got=$(ssh twin "$TWENV; wl-paste -n 2>/dev/null")
  [[ $got == "$tok" ]] && break; sleep 0.5
done
[[ $got == "$tok" ]] || { echo "FAIL this PC → twin: the twin has '${got:0:40}'"; exit 1; }

tok=twin-$RANDOM$RANDOM
ssh twin "$TWENV; printf %s $tok | wl-copy >/dev/null 2>&1"
for _ in $(seq 20); do
  got=$(wl-paste -n 2>/dev/null)
  [[ $got == "$tok" ]] && break; sleep 0.5
done
[[ $got == "$tok" ]] || { echo "FAIL twin → this PC: this PC has '${got:0:40}'"; exit 1; }
echo "PASS clipboard both ways"
