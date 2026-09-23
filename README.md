# twinPC

**Use a second Linux PC as a GPU/AI co-processor for your main PC — over a single ethernet cable.**

twinPC connects a main workstation to a second machine (the *twin*) and makes the twin feel like
part of the main PC: GPU and AI work typed in your terminal runs on the twin automatically, the
twin wakes and shuts down with your PC, its encrypted disk is unlocked from your PC, and its
screen, mouse, keyboard and sound are shared.

## Features

- **`twin` command** — run background jobs, Claude Code agents and GPU work on the twin; watch GPU
  and processes; copy files; manage it all from the main PC (`man twin`, `twin help`).
- **Automatic task routing** — when you press Enter, a bash hook decides where the command should
  run. GPU/AI commands (`ollama`, `*train*.py`, `whisper`, `torchrun`, …) and anything inside the
  shared workspace run on the twin; CPU-heavy commands move there only when the main PC is busy.
  Output, Ctrl-C and exit codes behave as if the command ran locally, and each task also opens a
  window on the twin's desktop.
- **Power** — the twin wakes with the main PC (Wake-on-LAN), its LUKS-encrypted disk is unlocked
  over SSH from the main PC, and it shuts down with the main PC unless jobs are still running.
- **Desktop sharing** — move the mouse off the edge of your screen onto the twin's monitor
  (lan-mouse); with no monitor attached, view the twin's desktop fullscreen with **Super+F12**
  (wayvnc over SSH); drive it by command with `twin gui …`.
- **Sound** — the twin plays through the main PC's speakers (PipeWire over SSH).
- **Remote services** — `ollama` on the main PC uses the twin's GPU; `docker --context twin` runs
  containers there; the twin's `~/work` is mounted on the main PC at `~/twin`.

## How it works

```
 main PC                                                   twin
 ───────                                                   ────
 terminal ──Enter──► twin-route.bash ─► twin-route (rules) │
                           │ "twin"                        │
                           ▼                               │
                       twin-exec ──── ssh ────────────────►│ tmux task session ─► window on workspace 9
 twin CLI ─────────────────────────── ssh ────────────────►│ jobs, GPU, GUI (hyprctl / wtype / ydotool)
 twin-link.service ── Wake-on-LAN, disk unlock ───────────►│ initramfs SSH (dropbear)
 twin-kvm.timer ─ lan-mouse on/off, audio re-link          │ lan-mouse
 twin-audio.service ─ PipeWire socket over ssh -R ◄────────│ "Main PC speakers" output
 twin-mount.service ─ sshfs ~/twin ◄───────────────────────│ ~/work
 ollama / docker ─────────────────────────────────────────►│ ollama (ROCm), dockerd
```

Everything travels over SSH on the direct cable; the only extra listening ports on the twin are
Ollama (11434) and lan-mouse (UDP 4242), both opened on the twin's firewall.

## Requirements

| | Main PC | Twin |
|---|---|---|
| OS | Linux with GNOME (Wayland), bash, systemd, NetworkManager, PipeWire | Arch-based [Omarchy](https://omarchy.org) (Hyprland, PipeWire) |
| Hardware | a free ethernet port | an AMD GPU supported by ROCm, a NIC with Wake-on-LAN |
| Software | `ssh`, `python3` (3.11+), `rsync`, `sshfs`, `remmina`, `curl` | installed by `twinpc/install.sh` |

The two machines are connected directly by an ethernet cable, and the main PC shares its internet
connection over it (NetworkManager "Shared to other computers"). The defaults assume that
NetworkManager subnet: main PC `10.42.0.1`, twin `10.42.0.11`.

## Installation

Replace the `<placeholders>` with your own values.

### 1. Connect the machines

1. Plug the ethernet cable into both PCs.
2. On the main PC, open the wired connection's settings → IPv4 → **Shared to other computers**.
3. On the twin, enable SSH:
   ```bash
   sudo pacman -S --needed openssh
   sudo systemctl enable --now sshd
   sudo ufw allow 22/tcp
   ```
4. On the main PC, clone the project and install your SSH key on the twin:
   ```bash
   git clone https://github.com/<you>/twinPC ~/projects/twinPC
   ssh-copy-id <twin-user>@<twin-ip>
   ```
5. Edit `main/ssh-config` — set `User` to `<twin-user>` (and `HostName` if your twin's address
   differs) — before running the main installer.

### 2. Twin BIOS

Enable Wake-on-LAN in the twin's firmware. On ASUS boards: **Advanced → APM Configuration** →
*ErP Ready* = Disabled, *Power On By PCI-E* = Enabled (optionally *Restore AC Power Loss* = Power On).

### 3. Set up the twin

```bash
scp -r twinpc <twin-user>@<twin-ip>:twinpc-setup
ssh <twin-user>@<twin-ip>
sudo bash ~/twinpc-setup/install.sh        # packages, Ollama (ROCm), Wake-on-LAN, GUI tools, remote unlock
bash ~/twinpc-setup/setup-ai-env.sh        # optional: PyTorch for ROCm in ~/ai-env (~4 GB)
sudo reboot
```

`install.sh` runs each step in `twinpc/` in order; every step can be re-run safely.

### 4. Set up the main PC

```bash
sudo apt install sshfs remmina             # or your distribution's equivalent
mkdir -p ~/.config/twinpc
cat > ~/.config/twinpc/config <<'EOF'
TWIN_MAC=<twin-mac-address>                # twin's wired MAC, for Wake-on-LAN (ip link on the twin)
TWIN_IFACE=<main-pc-wired-interface>       # e.g. enp3s0 (ip -br link on this PC)
EOF
bash ~/projects/twinPC/main/install.sh
```

The installer needs no `sudo`, only writes files whose content differs, and can be re-run at any
time. It installs the `twin` command and manual, the SSH aliases, the user services, the GNOME
shortcut, lan-mouse on both machines, the twin's audio output and the task-routing hook. If your
wired card is an Intel I225-V it prints one optional `sudo` command for a udev rule that stops
that card from hanging.

### 5. Try it

Open a new terminal:

```bash
twin status                     # up / waiting for disk password / off
twin gpu                        # the twin's GPU load and VRAM
ollama run gemma3:4b "hello"    # → runs on the twin's GPU automatically
twin route explain make         # why a command would run here or there
```

## Usage

```bash
twin wake | twin off | twin reboot          # power (wake/reboot ask for the disk password)
twin run train 'python train.py'            # background job on the twin …
twin logs train -f                          # … and its output
twin claude agent1 ~/work/project           # Claude Code session on the twin
twin route on | off | status                # automatic task routing
local <command>                             # force one command to run on this PC
twin desktop                                # the twin's screen fullscreen (also Super+F12)
twin audio status | test                    # the twin's sound through this PC
```

`man twin` documents every command.

## Configuration

Machine-specific settings go in `~/.config/twinpc/config` (plain `NAME=value` lines, read by the
`twin` command and its services); routing rules go in `~/.config/twin-route/rules.toml`.

| Setting | Where | Default |
|---|---|---|
| Which commands run on the twin | `~/.config/twin-route/rules.toml` (`always_twin`, `never_twin`, `load_offload`, `needs_cwd`) | GPU/AI tools on the twin |
| Load thresholds | `rules.toml` `[load]` | CPU 80 %, RAM 85 % |
| Shared workspace | `rules.toml` `[workspace]` | `~/twin` ⇄ twin `~/work` |
| SSH host alias | `TWIN_HOST` (config file) | `twin` |
| Twin's MAC (Wake-on-LAN) | `TWIN_MAC` (config file) | — set it |
| Broadcast address / wired interface | `TWIN_BCAST`, `TWIN_IFACE` (config file) | `10.42.0.255`, set `TWIN_IFACE` |
| Headless virtual screen size | `TWIN_HEADLESS_MODE` (config file) | `2560x1080@60` |
| Local port for the remote desktop | `TWIN_VNC_PORT` (config file) | `5901` |
| Extra environment passed to routed tasks | `TWIN_ROUTE_ENV_ALLOW` | `TERM LANG COLORTERM` only |
| Turn routing off in one shell | `TWIN_ROUTE=off` | on |

## Troubleshooting

- **Nothing reaches the twin** — check the main PC's wired port too: if its `tx_packets`
  (`/sys/class/net/<iface>/statistics/tx_packets`) stops increasing while you ping the twin, the
  main PC's NIC is stuck; reload its driver or reboot.
- **No sound from the twin** — `twin audio heal`, then `twin audio test`.
- **The twin waits at its disk-encryption prompt** — `twin unlock` in a terminal.
- **The twin's screen is locked** — `twin unlock-screen`.
- **`~/twin` is empty** — the twin is off or the mount dropped: `twin wake` or
  `systemctl --user restart twin-mount`.
- **Routing gets in the way** — `local <command>` for one command, `twin route off` for all.

## Project layout

```
twin, twin-completion.bash, man/   the twin command, tab completion and manual
route/                             automatic task routing: router, rules, runner, bash hook
main/                              main-PC services and settings, main/install.sh
twinpc/                            twin setup scripts, twinpc/install.sh, setup-ai-env.sh
tests/                             unit and integration tests
docs/                              design notes
```

## Tests

```bash
python3 -m unittest tests.test_twin_route tests.test_hook   # local, fast
python3 -m unittest tests.test_twin_exec                    # needs the twin running
for t in tests/*.sh; do bash "$t"; done                     # system checks
```
