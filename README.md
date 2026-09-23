# twinPC

Turns a second PC ("twin") into a GPU/AI co-processor of this one. The two are joined by a
single ethernet cable; this PC shares its internet with the twin over it.

| | Main PC (this one) | Twin |
|---|---|---|
| OS | Ubuntu 26.04, GNOME (Wayland), bash | Omarchy (Arch), Hyprland 0.56 |
| Strength | Ryzen 9 7950X, 32 threads, 30 GB | AMD RX 6600 (8 GB, ROCm), 12 threads, 15 GB |
| Address | 10.42.0.1 (`enp11s0`, Intel I225-V) | 10.42.0.11 (`enp6s0`, Realtek RTL8111) |

What you get:

- **`twin` command**: run jobs, Claude Code agents and GPU work on twin, watch GPU/processes,
  move files. See `man twin` or `twin help`.
- **Automatic task routing**: GPU/AI commands typed in any terminal here (`ollama`, `*train*.py`,
  `whisper`, …) run on twin by themselves, with live output here and a window on twin's
  workspace 9. See `twin route status`.
- **Power**: twin wakes with this PC (Wake-on-LAN), its encrypted disk is unlocked from here, and
  it shuts down with this PC unless jobs are running.
- **Desktop**: shared mouse/keyboard onto twin's monitor (lan-mouse), twin's screen fullscreen
  here with **Super+F12** when it is headless, `twin gui …` to drive it by command.
- **Sound**: twin plays through this PC's speakers.
- **Ollama/Docker**: `ollama` here talks to twin's GPU; `docker --context twin` runs containers there.

## How it fits together

```
 main PC                                                   twin
 ───────                                                   ────
 terminal ──Enter──► twin-route.bash ─► twin-route (rules) │
                           │ "twin"                        │
                           ▼                               │
                       twin-exec ──── ssh ────────────────►│ tmux task session ─► window on ws 9
 twin CLI ─────────────────────────── ssh ────────────────►│ jobs, gpu, gui (hyprctl/wtype/ydotool)
 twin-link.service ── Wake-on-LAN / dropbear unlock ──────►│ initramfs SSH (disk password)
 twin-kvm.timer ─ lan-mouse on/off, audio heal             │ lan-mouse
 twin-audio.service ─ PipeWire socket over ssh -R ◄────────│ "Main PC speakers" output
 twin-mount.service ─ sshfs ~/twin ◄───────────────────────│ ~/work
 ollama / docker ─────────────────────────────────────────►│ ollama-rocm :11434, dockerd
```

## Layout

```
twin, twin-completion.bash, man/twin.1   the twin command and its manual
route/        automatic task routing (router, rules, twin-exec, bash hook, viewer, installer)
main/         this PC: user services, autostart, Remmina profile, ssh aliases, NIC udev rule,
              and main/install.sh
twinpc/       the twin: setup scripts run with sudo (twinpc/install.sh), PyTorch env script,
              PipeWire output, lan-mouse unit
tests/        unit + integration tests (the integration ones need twin up)
docs/superpowers/   design spec and implementation plan for task routing
```

Not in this repo: the `it-services-bizdev` Claude skill lives only on twin
(`~/.claude/skills/it-services-bizdev`, its own git repo) — use it with `twin bizdev`.

## First-time setup

1. **Cable and internet sharing.** Plug the cable in; in this PC's network settings, set the wired
   connection to "Shared to other computers" (gives twin 10.42.0.x and internet).
2. **SSH on twin** (at twin's keyboard):
   `sudo pacman -S --needed openssh && sudo systemctl enable --now sshd && sudo ufw allow 22/tcp`
3. **Key login** (from a normal terminal here): `ssh-copy-id appspubs@10.42.0.11`
4. **Twin BIOS (ASUS)**, Advanced → APM Configuration: *ErP Ready* = Disabled,
   *Power On By PCI-E* = Enabled, *Restore AC Power Loss* = Power On (optional).
5. **Twin setup** (from here, then on twin):
   `scp -r twinpc twin:twinpc-setup` → on twin: `sudo bash ~/twinpc-setup/install.sh`, then reboot twin.
   Optional GPU PyTorch: `bash ~/twinpc-setup/setup-ai-env.sh` (≈4 GB).
6. **This PC**: `bash main/install.sh` (no sudo; safe to re-run — only writes what differs).
   It prints one sudo command for the Intel NIC rule if that is missing; run it.
7. **Once**: `sudo apt install sshfs` (for `~/twin`), then run `main/install.sh` again.
8. Open a new terminal: `twin status`, `twin gpu`, `ollama run gemma3:4b hi` (→ runs on twin).

## Daily use

```bash
twin status | twin wake | twin off | twin reboot     # power (wake/reboot ask for the disk password)
twin run train 'python train.py'; twin logs train -f # background jobs on twin
twin claude agent1 ~/work/proj                       # Claude Code session on twin
twin route explain <cmd>; twin route off|on          # automatic routing
twin gpu | twin top | twin models                    # what twin is doing
twin desktop  (Super+F12) | twin gui shot            # twin's screen
twin audio status|test                               # sound through this PC
```

## Troubleshooting

- **"Twin hangs / nothing connects"** — check this PC first: if
  `cat /sys/class/net/enp11s0/statistics/tx_packets` doesn't grow while you `ping 10.42.0.11`,
  this PC's Intel I225-V wedged (it did once). `sudo modprobe -r igc && sudo modprobe igc` or a
  reboot fixes it; `main/80-i225-no-aspm.rules` prevents it.
- **No sound from twin** — `twin audio heal` (runs every 20 s anyway), then `twin audio test`.
- **Twin's screen locked** — `twin unlock-screen` (types the password; Omarchy has no remote unlock).
- **Twin sits at the disk prompt** — `twin unlock` in a normal terminal.
- **`~/twin` empty or hanging** — twin is off: `twin wake`, or `systemctl --user restart twin-mount`.
- **Routing in the way** — `local <cmd>` for one command, `twin route off` for all.

## Tests

```bash
python3 -m unittest tests.test_twin_route tests.test_hook        # fast, local
python3 -m unittest tests.test_twin_exec                         # needs twin up
for t in tests/*.sh; do bash "$t"; done                          # sampler, viewer, mount, CLI
```
