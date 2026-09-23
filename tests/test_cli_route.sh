#!/usr/bin/env bash
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); T="$ROOT/twin"
XDG_CONFIG_HOME=$(mktemp -d); export XDG_CONFIG_HOME
trap 'rm -rf "$XDG_CONFIG_HOME"' EXIT
fail=0
"$T" route off >/dev/null; "$T" route status | grep -q "routing: OFF" || { echo "FAIL off"; fail=1; }
"$T" route on  >/dev/null; "$T" route status | grep -q "routing: ON"  || { echo "FAIL on"; fail=1; }
"$T" route explain ollama run x | head -1 | grep -q "^twin always:ollama" || { echo "FAIL explain"; fail=1; }
# the twin command takes the twin's address from the twinpc profile
mkdir -p "$XDG_CONFIG_HOME/twinpc"
printf 'TWIN_ADDR=${TWIN_ADDR:-10.99.0.7}\n' > "$XDG_CONFIG_HOME/twinpc/profile.env"
bash -c "source <(sed -n '1,/^cmd=/p' '$T' | sed '\$d'); echo \"\$TWIN_ADDR\"" | grep -qx 10.99.0.7 || { echo "FAIL profile address"; fail=1; }
(( fail == 0 )) && echo PASS
exit $fail
