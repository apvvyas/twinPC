#!/bin/sh
# probe.sh — read-only facts about this machine for `twinpc detect`: prints key=value lines.
# Never uses sudo, never writes. Test hooks: TWINPC_ROOT prefixes every file read (including /proc),
# TWINPC_TOOLPATH replaces PATH when looking for tools.
R=${TWINPC_ROOT:-}

have() {   # print the path of tool $1 if it is on TWINPC_TOOLPATH (or PATH)
  _old=$IFS; IFS=:
  for _d in ${TWINPC_TOOLPATH:-$PATH}; do
    if [ -x "$_d/$1" ]; then IFS=$_old; echo "$_d/$1"; return 0; fi
  done
  IFS=$_old; return 1
}
tool() { _t=$(have "$1") || return 127; shift; "$_t" "$@"; }
say() { printf '%s=%s\n' "$1" "$2"; }

# --- distribution family (ID, then ID_LIKE)
family=unknown
for i in $(sed -n 's/^ID=//p; s/^ID_LIKE=//p' "$R/etc/os-release" 2>/dev/null | tr -d '"'); do
  case $i in
    debian|ubuntu) family=debian; break ;;
    fedora|rhel|centos) family=fedora; break ;;
    arch|archlinux) family=arch; break ;;
  esac
done
say family "$family"

# --- package manager
if have apt-get >/dev/null; then pkg=apt
elif have dnf >/dev/null; then pkg=dnf
elif have pacman >/dev/null; then pkg=pacman
else pkg=unknown; fi
say pkg "$pkg"

# --- desktop and session: own environment, else the logged-in session's processes (over ssh)
cur=${XDG_CURRENT_DESKTOP:-}; stype=${XDG_SESSION_TYPE:-}
if [ -z "$cur" ]; then
  for e in "$R"/proc/[0-9]*/environ; do
    v=$( { tr '\0' '\n' < "$e"; } 2>/dev/null | sed -n 's/^XDG_CURRENT_DESKTOP=//p' | head -1)
    [ -n "$v" ] || continue
    cur=$v
    stype=$( { tr '\0' '\n' < "$e"; } 2>/dev/null | sed -n 's/^XDG_SESSION_TYPE=//p' | head -1)
    break
  done
fi
case $(printf '%s' "$cur" | tr 'A-Z' 'a-z') in
  *gnome*) desktop=gnome ;;
  *kde*|*plasma*) desktop=kde ;;
  *hyprland*) desktop=hyprland ;;
  *sway*) desktop=sway ;;
  '') desktop=none ;;
  *) if [ "$stype" = x11 ]; then desktop=x11-other; else desktop=unknown; fi ;;
esac
case $stype in wayland|x11) ;; *) stype=none ;; esac
say desktop "$desktop"
say session "$stype"

# --- GPU: a discrete vendor wins over an integrated one
gpu=none
for f in "$R"/sys/class/drm/card*/device/vendor; do
  [ -r "$f" ] || continue
  case $(cat "$f") in
    0x10de) gpu=nvidia ;;
    0x1002) [ "$gpu" = nvidia ] || gpu=amd ;;
    0x8086) [ "$gpu" = none ] && gpu=intel ;;
  esac
done
say gpu "$gpu"
gpu_arch=
if [ "$gpu" = amd ]; then
  for p in "$R"/sys/class/kfd/kfd/topology/nodes/*/properties; do
    v=$(sed -n 's/^gfx_target_version //p' "$p" 2>/dev/null)
    [ -n "$v" ] && [ "$v" != 0 ] || continue
    gpu_arch=$(printf 'gfx%d%d%x' $((v / 10000)) $((v / 100 % 100)) $((v % 100)))
    break
  done
fi
say gpu_arch "$gpu_arch"

# --- disk encryption, initramfs tool, bootloader
if tool lsblk -rno TYPE 2>/dev/null | grep -qx crypt; then say luks true; else say luks false; fi
if have mkinitcpio >/dev/null; then initramfs=mkinitcpio
elif have dracut >/dev/null; then initramfs=dracut
elif have update-initramfs >/dev/null; then initramfs=initramfs-tools
else initramfs=unknown; fi
say initramfs "$initramfs"
if have limine >/dev/null; then bootloader=limine
elif have grub-mkconfig >/dev/null || have grub2-mkconfig >/dev/null; then bootloader=grub
elif have bootctl >/dev/null; then bootloader=systemd-boot
else bootloader=unknown; fi
say bootloader "$bootloader"

# --- wired port: a real (non-virtual, non-wireless) interface with a link
wired_iface=
for d in "$R"/sys/class/net/*; do
  n=${d##*/}
  [ "$n" = lo ] && continue
  [ -d "$d/wireless" ] && continue
  [ -e "$d/device" ] || continue
  [ "$(cat "$d/carrier" 2>/dev/null)" = 1 ] || continue
  wired_iface=$n; break
done
say wired_iface "$wired_iface"
wired_mac=; addr=; wol=unknown
if [ -n "$wired_iface" ]; then
  wired_mac=$(cat "$R/sys/class/net/$wired_iface/address" 2>/dev/null)
  addr=$(tool ip -4 -o addr show dev "$wired_iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
  w=$(tool ethtool "$wired_iface" 2>/dev/null | sed -n 's/^[[:space:]]*Wake-on:[[:space:]]*//p' | head -1)
  [ -n "$w" ] && wol=$w
fi
say wired_mac "$wired_mac"
say addr "$addr"
say wol "$wol"
say user "$(id -un)"
