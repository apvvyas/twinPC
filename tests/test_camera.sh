#!/usr/bin/env bash
# The twin's "Main PC camera" shows live video while something reads it (and this PC's camera is on only
# then), and "Main PC microphone" records sound on the twin.
set -u
ssh twin '[ -e /dev/video9 ]' || { echo "FAIL the twin has no /dev/video9"; exit 1; }

# read the twin's webcam for 6 s; the brightest frame's average luma must be above black (16)
ssh twin 'ffmpeg -hide_banner -loglevel error -f v4l2 -i /dev/video9 -t 6
  -vf signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=/tmp/twinpc-camera-test.txt -f null -' &
reader=$!
on=0
for _ in $(seq 30); do
  pgrep -f "input_format mjpeg" >/dev/null && { on=1; break; }; sleep 0.3
done
wait $reader
peak=$(ssh twin 'sed -n "s/.*YAVG=//p" /tmp/twinpc-camera-test.txt | sort -n | tail -1; rm -f /tmp/twinpc-camera-test.txt')
[[ $on == 1 ]] || { echo "FAIL this PC's camera never turned on"; exit 1; }
python3 -c "import sys; sys.exit(0 if float('${peak:-0}') > 20 else 1)" || { echo "FAIL only black frames (peak Y ${peak:-none})"; exit 1; }
off=0
for _ in $(seq 40); do
  pgrep -f "input_format mjpeg" >/dev/null || { off=1; break; }; sleep 0.5
done
[[ $off == 1 ]] || { echo "FAIL this PC's camera stayed on after the twin stopped reading"; exit 1; }

# record 1 s from the main PC's microphone on the twin; it must not be pure silence
rms=$(ssh twin 'bash -s' <<'EOF'
export XDG_RUNTIME_DIR=/run/user/$(id -u)
timeout 2 parecord -d main-pc-mic --raw --format=s16le --rate=16000 --channels=1 2>/dev/null |
  python3 -c 'import sys, array, math; a = array.array("h", sys.stdin.buffer.read()); print(int(math.sqrt(sum(x * x for x in a) / max(len(a), 1))))'
EOF
)
[[ ${rms:-0} -gt 0 ]] || { echo "FAIL the twin recorded silence from 'Main PC microphone'"; exit 1; }
echo "PASS camera and mic (peak Y $peak, mic rms $rms)"
