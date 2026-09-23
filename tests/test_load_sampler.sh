#!/usr/bin/env bash
# The sampler writes an integer percentage within a couple of intervals.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
XDG_RUNTIME_DIR=$tmp TWIN_ROUTE_LOAD_INTERVAL=1 "$ROOT/route/twin-route-load" &
pid=$!
trap 'kill $pid 2>/dev/null; rm -rf "$tmp"' EXIT
for _ in $(seq 30); do [[ -s $tmp/twin-route.load ]] && break; sleep 0.2; done
v=$(cat "$tmp/twin-route.load" 2>/dev/null)
if [[ $v =~ ^[0-9]+$ ]] && (( v >= 0 && v <= 100 )); then echo "PASS load=$v"; else echo "FAIL got '$v'"; exit 1; fi
