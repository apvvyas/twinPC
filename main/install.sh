#!/usr/bin/env bash
# main/install.sh — kept for compatibility: twinPC is installed by the twinpc tool.
set -euo pipefail
T="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)/tool/twinpc"
[[ -f ${XDG_CONFIG_HOME:-$HOME/.config}/twinpc/profile.toml ]] || "$T" detect
exec "$T" install "$@"
