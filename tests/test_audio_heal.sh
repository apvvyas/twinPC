#!/usr/bin/env bash
# `twin audio heal` re-creates the twin's stream when it has no output or plays on another output than
# this PC's default (e.g. Bluetooth came back under a new id); it leaves a correctly placed stream alone.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
stubs=$(mktemp -d); trap 'rm -rf "$stubs"' EXIT
cat > "$stubs/pactl" <<'STUB'
#!/bin/sh
case "$*" in
  "get-default-sink") echo bluez_output.speaker ;;
  "list short sinks") printf '79\talsa_output.usb\tPipeWire\n3056\tbluez_output.speaker\tPipeWire\n' ;;
  "list sink-inputs") printf 'Sink Input #2384\n\tSink: %s\n\t\tmedia.name = "Tunnel for you@twin"\n' "$STREAM_SINK" ;;
esac
STUB
printf '#!/bin/sh\necho "$*" >> "%s/systemctl.log"\n' "$stubs" > "$stubs/systemctl"
chmod +x "$stubs"/*
run() { rm -f "$stubs/systemctl.log"; PATH="$stubs:$PATH" STREAM_SINK=$1 "$ROOT/twin" audio heal; }
run 79 >/dev/null;         grep -q "restart twin-audio.service" "$stubs/systemctl.log" 2>/dev/null || { echo "FAIL a stream on the wrong output was left there"; exit 1; }
run 4294967295 >/dev/null; grep -q "restart twin-audio.service" "$stubs/systemctl.log" 2>/dev/null || { echo "FAIL an unlinked stream was left unlinked"; exit 1; }
run 3056 >/dev/null;       [ ! -e "$stubs/systemctl.log" ] || { echo "FAIL a correctly placed stream was restarted"; exit 1; }
echo "PASS twin audio heal follows this PC's default output"
