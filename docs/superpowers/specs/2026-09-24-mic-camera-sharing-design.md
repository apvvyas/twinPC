# Mic and camera sharing — design

**Status:** approved in brainstorming 2026-09-24 · branch `feat/camera`

## Goal

Apps on the twin use the **main PC's microphone and camera** as if they were plugged into the
twin. That covers:

- video calls in a browser;
- desktop call apps (Zoom, Slack, Discord, Teams);
- recording and streaming (OBS);
- AI and speech work (Whisper, OpenCV).

It all runs over the existing SSH link, and each device is used only while an app on the twin
actually needs it. It ships as a new `camera` feature of `twinpc`, covering both the mic and the
camera.

## What the user chose

- **All four uses**, so the twin needs a standard PipeWire microphone and a standard V4L2 webcam
  that every app sees.
- **Mic:** follow the main PC's default input (switch it in Sound settings and the twin follows).
- **Camera:** stream only while an app on the twin has the webcam open.
- **Approach A:** a `v4l2loopback` virtual webcam, with on-demand MJPEG streaming over SSH and a
  placeholder picture while idle.

## Verified on the real machines (2026-09-24)

| Piece | Finding |
|---|---|
| Main camera | "HD Camera", `/dev/video0`; MJPEG up to 1920×1080, also YUYV |
| Main default input | currently the camera's built-in mic (`alsa_input.usb-2e7e_HD_Camera-04.mono-fallback`); a USB mic is also present |
| Mic tunnel | a `module-tunnel-source` on the twin through the existing `127.0.0.1:4713` forward creates a record stream on the main PC that is **corked while nobody records** and uncorked while a twin app records — on-demand by itself |
| Twin kernel | `linux-omarchy 7.2.5-3`; `linux-omarchy-headers 7.2.5-3` exists (omarchy repo); `v4l2loopback-dkms 0.15.4` in extra; module not loaded yet |
| ffmpeg | present on both PCs |

## Architecture

```
MAIN PC                                              TWIN
PipeWire (default input) ◀── existing -R 4713 tunnel ── "Main PC microphone" (pulse-tunnel source)
                                                         corked until something records

/dev/video0 (HD Camera)                               /dev/video9  "Main PC camera" (v4l2loopback)
      │                                                      ▲
twin-camera service ══ ssh twin twin-camera --watch ══▶ watcher: who has /dev/video9 open?
      │   ◀── "wanted" / "idle" ─────────────────────────┘   (placeholder frames while idle)
      └─ ffmpeg -c copy MJPEG ══ ssh twin twin-camera --feed ══▶ ffmpeg decode → /dev/video9
```

### Components

1. **Mic:** `twinpc/main-pc-mic.conf` in the twin's `~/.config/pipewire/pipewire.conf.d/`:
   - a `libpipewire-module-pulse-tunnel` with `tunnel.mode = source`, `pulse.server.address = "tcp:127.0.0.1:4713"`, `reconnect.interval.ms = 5000`, `node.name = "main-pc-mic"`, `node.description = "Main PC microphone"`;
   - no remote source named, so it follows the main PC's default input;
   - the same forward is provided by `twin-audio.service`, so no new service is needed;
   - it is made the twin's default input (`pactl set-default-source main-pc-mic`).
2. **Virtual webcam on the twin** (one-time root steps):
   - packages `v4l2loopback-dkms` and the kernel's headers package; for `linux-omarchy` that's `linux-omarchy-headers`, otherwise `<kernel package>-headers`;
   - `/etc/modprobe.d/twinpc-camera.conf`: `options v4l2loopback video_nr=9 card_label="Main PC camera" exclusive_caps=1`;
   - `/etc/modules-load.d/twinpc-camera.conf`: `v4l2loopback`;
   - `modprobe v4l2loopback` now.
3. **`clip/twin-camera`:** one Python 3 stdlib script with three roles.
   - **Main (default): the user service `twin-camera.service`.**
     - It keeps `ssh twin .local/bin/twin-camera --watch` running and reconnects every 10 s.
     - On `wanted` it starts `ffmpeg -f v4l2 -input_format mjpeg -video_size <SIZE> -framerate <FPS> -i /dev/video0 -c copy -f mjpeg -`, with its stdout piped into `ssh twin .local/bin/twin-camera --feed`.
     - On `idle` it stops both.
     - If ffmpeg can't open the camera (busy or missing), it sends `busy {reason}` on the watch link.
     - If the stream ends while still wanted, it retries after 3 s.
     - Settings come from `~/.config/twinpc/config`: `TWIN_CAMERA_SIZE` (default `1280x720`), `TWIN_CAMERA_FPS` (default `30`) and `TWIN_CAMERA_DEVICE` (default `/dev/video0`).
   - **`--watch` (twin):**
     - It keeps the placeholder writer running while nothing is feeding: `ffmpeg -f lavfi -i color=black:size=<SIZE>:rate=2 -pix_fmt yuv420p -f v4l2 /dev/video9`.
     - Every 1 s it counts the processes, other than its own writers, with `/dev/video9` open (from `/proc/<pid>/fd`).
     - It prints `wanted` when that count goes from 0 to more than 0, and `idle` when it has been 0 for 5 s.
     - It reads `busy {reason}` lines from the main PC and shows a notification.
     - It stops the placeholder while `--feed` runs (coordinated through a lock file in `$XDG_RUNTIME_DIR/twinpc/`).
   - **`--feed` (twin):**
     - takes the lock, then runs `ffmpeg -f mjpeg -i - -pix_fmt yuv420p -f v4l2 /dev/video9` on its stdin;
     - releases the lock when the stream ends, and the watcher restarts the placeholder within 1 s.
   - The watch-link protocol is one JSON object per line: `{"event": "wanted" | "idle"}` from the twin, and `{"event": "busy", "reason": "…"}` from the main PC.
4. **The `camera` feature** in `twinpc`, placed after `audio` (it needs its forward):
   - the mic file and the default input;
   - the root steps for the module;
   - `twin-camera` on both PCs (a symlink here, a copy on the twin);
   - the user unit on the main PC;
   - doctor checks: the module is loaded, `/dev/video9` exists, the watcher is connected (`twin-camera --selftest`), and the mic input exists on the twin.
   - It is supported with a `pacman` twin; other package managers and kernels get "not supported yet (planned)".
5. **`twin camera status|on|off`:**
   - `off` stops `twin-camera.service` and unloads the twin's mic tunnel (`pactl unload-module` of `main-pc-mic`), until `on` or the next login;
   - there are also a man page section and README updates (feature card, cheat sheet, troubleshooting, layout, test counts).

## Behaviour over time

- **Camera:**
  1. Idle: placeholder, and the main PC's camera light is off.
  2. An app opens `/dev/video9`; within about 1 s, `wanted` is sent.
  3. The main PC streams MJPEG (about 10–30 Mbit/s at 720p30, not re-encoded).
  4. The twin decodes it into the device, and the app's picture goes from black to live in about 1–2 s, without reopening the device.
  5. 5 s after the last reader leaves, `idle` is sent; the stream stops, the light goes off and the placeholder returns.
- **Mic:** the record stream on the main PC stays corked until an app on the twin records from "Main PC microphone", and it follows the main PC's default input.
- **Privacy:** the camera is opened on the main PC only while a twin app is actually using the webcam, and the mic is only read while a twin app records. `twin camera off` switches both off.

## Error handling

| Situation | Behaviour |
|---|---|
| Twin off or link down | The main service retries ssh every 10 s; twin apps see the placeholder |
| Main camera busy (used here) or unplugged | The placeholder stays; the twin shows "Main PC camera is busy / not found"; retried the next time a reader appears |
| Stream breaks mid-call | `--feed` exits and the placeholder returns within 1 s (the app sees black, not a frozen device); retried after 3 s while still wanted |
| Module not loaded (e.g. the DKMS build failed for a new kernel) | The watcher logs it and exits; doctor ❌ "virtual camera module not loaded — sudo modprobe v4l2loopback"; the mic still works |
| Audio forward (`twin-audio.service`) down | The mic is unavailable as well; the twin's default input falls back; `twin audio heal` restarts the forward |

## Security

- Everything runs over the existing SSH link: no new ports, and the twin still has no key for the main PC.
- Only the main PC starts the camera, and only after the twin reports a reader. The twin can only ask, and `twin camera off` stops the main PC answering.
- `/dev/video9` has the usual `video` group permissions.
- The ffmpeg command lines are fixed argv lists; the size, frame rate and device from the config are validated (`^\d{2,4}x\d{2,4}$`, `^\d{1,3}$`, `^/dev/video\d+$`) before use.

## Testing

- **Unit tests** (`tests/test_camera.py`):
  - counting readers against a fake `/proc` tree, with our own writer pids excluded;
  - the `wanted`/`idle` debounce with a fake clock;
  - the ffmpeg argv, and validation of the config values;
  - the watch-link line protocol.
- **Tool tests** (`tests/tool/test_camera.py`):
  - the `camera` feature steps on Arch/Hyprland;
  - skipped with a reason for apt or dnf twins;
  - the headers package taken from the kernel;
  - the content of the mic PipeWire file.
- **Integration test** (`tests/test_camera_link.py`): the main-side loop with `--watch` and `--feed` connected by local pipes, with stub `ffmpeg` programs and a plain file standing in for the device. It checks:
  - a reader appears → the stream starts;
  - the reader leaves → it stops after the delay;
  - camera busy → a notice reaches the twin;
  - the feed dies → the placeholder comes back.
- **System check** `tests/test_camera.sh`:
  - on the twin, grab frames from `/dev/video9` for 3 s and check they aren't all black; check the main PC's camera was opened during that and closed again afterwards;
  - record 1 s from "Main PC microphone" on the twin and check it isn't silence.
- **By hand:** a Meet test call in the twin's browser (camera and mic), plus one desktop app.

## Out of scope

- Sharing the twin's devices with the main PC.
- More than one camera; choosing the camera per app.
- Audio/video sync beyond what the apps do themselves (the mic and camera are separate streams).
- Twins that don't use pacman, or have kernels without a headers package — "not supported yet (planned)".
