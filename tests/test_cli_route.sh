#!/usr/bin/env bash
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); T="$ROOT/twin"
XDG_CONFIG_HOME=$(mktemp -d); export XDG_CONFIG_HOME
trap 'rm -rf "$XDG_CONFIG_HOME"' EXIT
fail=0
"$T" route off >/dev/null; "$T" route status | grep -q "routing: OFF" || { echo "FAIL off"; fail=1; }
"$T" route on  >/dev/null; "$T" route status | grep -q "routing: ON"  || { echo "FAIL on"; fail=1; }
"$T" route explain ollama run x | head -1 | grep -q "^twin always:ollama" || { echo "FAIL explain"; fail=1; }
(( fail == 0 )) && echo PASS
exit $fail
