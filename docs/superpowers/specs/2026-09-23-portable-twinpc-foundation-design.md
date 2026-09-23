# Portable twinPC — foundation design (sub-project 1 of 5)

Date: 2026-09-23 · Status: approved in conversation, awaiting spec review

## Goal of the whole effort

Any capable AI agent, following a portable Agent-Skills-format skill, can use this repository to set
up twinPC on another person's pair of Linux machines — whatever their distribution, desktop and GPU —
instead of only Ubuntu/GNOME (main) + Omarchy/Hyprland/AMD (twin).

### Decisions (from the conversation)

| Topic | Decision |
|---|---|
| Platforms | Linux family: Debian/Ubuntu, Fedora, Arch; GNOME, KDE, Hyprland, Sway, generic X11; NVIDIA (CUDA), AMD (ROCm), Intel/CPU-only |
| Features | Everything on every setup; a feature is skipped only when it is impossible there (e.g. remote unlock on an unencrypted twin) |
| Network | Direct cable with internet sharing, or both PCs on the same LAN |
| Approach | Portable repo with platform adapters; the skill directs the tool. No procedure for agents to write new adapters: an unsupported value is reported as unsupported |
| Order | 1 foundation → 2 core features on all platforms → 3 power (WoL, remote unlock) → 4 desktop (mouse, audio, remote desktop, GUI control) → 5 the skill |

### Sub-projects

1. **Foundation (this spec)** — probe/detect, profile, step engine, adapter interfaces, doctor, and
   today's platforms moved behind adapters without breaking a working setup.
2. Core features for all listed platforms — connection (cable/LAN), `twin` CLI and jobs, GPU AI
   stack per GPU vendor, task routing.
3. Power — Wake-on-LAN and remote LUKS unlock for initramfs-tools, dracut and mkinitcpio.
4. Desktop — shared mouse, audio, remote desktop, GUI control for GNOME, KDE, Hyprland, Sway, X11.
5. The skill — `SKILL.md` + references describing how an agent drives `twinpc`.

Each later sub-project gets its own spec → plan → implementation cycle.

## Scope of this sub-project

In: the `twinpc` tool (`detect`, `install`, `doctor`), the probe, the profile, the step engine,
the adapter interfaces for packages / desktop / GPU / boot-unlock / network, and adapters for the
platforms the project already supports (apt, pacman; GNOME, Hyprland; AMD ROCm; mkinitcpio; cable).
Existing features are expressed as steps using those adapters.

Out (later sub-projects): adapters for dnf, KDE, Sway, X11, NVIDIA, Intel/CPU, initramfs-tools,
dracut, LAN mode; the skill. The foundation must, however, *detect* all listed values correctly, so
`doctor` can already say "unsupported (planned)" for them.

## Components

### 1. `twinpc` tool — `tool/twinpc` (Python 3.11+, stdlib only)

```
twinpc detect [--twin HOST]     probe both machines, write ~/.config/twinpc/profile.toml
twinpc install [FEATURE…] [--dry-run] [--yes]
twinpc doctor [FEATURE…]
```

Exit codes: 0 success, 1 a step failed / a check failed, 2 usage or missing profile.

### 2. Probe — `tool/probe.sh` (POSIX sh, read-only, no sudo)

Prints `key=value` lines:

| Key | Source | Values |
|---|---|---|
| `family` | `/etc/os-release` `ID`/`ID_LIKE` | `debian`, `fedora`, `arch`, `unknown` |
| `pkg` | first of `apt-get`, `dnf`, `pacman` on PATH | `apt`, `dnf`, `pacman`, `unknown` |
| `desktop` | `XDG_CURRENT_DESKTOP` of the logged-in graphical session (via `loginctl` / the session leader's environment when run over SSH) | `gnome`, `kde`, `hyprland`, `sway`, `x11-other`, `none` |
| `session` | `XDG_SESSION_TYPE` of that session | `wayland`, `x11`, `none` |
| `gpu` | vendor IDs from `/sys/class/drm/card*/device/vendor` (0x10de, 0x1002, 0x8086) | `nvidia`, `amd`, `intel`, `none` (discrete preferred over integrated) |
| `gpu_arch` | AMD: `/sys/class/kfd/kfd/topology/nodes/*/properties` `gfx_target_version` if present | e.g. `gfx1032`, empty |
| `luks` | `lsblk -o TYPE` contains `crypt` | `true`/`false` |
| `initramfs` | first of `mkinitcpio`, `dracut`, `update-initramfs` on PATH | `mkinitcpio`, `dracut`, `initramfs-tools`, `unknown` |
| `bootloader` | `limine`, `systemd-boot` (`bootctl is-installed`), `grub` | name or `unknown` |
| `wired_iface` | interface with carrier on the default/peer route | name |
| `wired_mac` | `/sys/class/net/<iface>/address` | MAC |
| `wol` | `ethtool <iface>` without sudo; unreadable → `unknown` | `g`, `d`, `unknown` |
| `addr` | IPv4 of `wired_iface` | address |
| `user` | `id -un` | name |

### 3. Profile — `~/.config/twinpc/profile.toml`

```toml
version = 1
[network]
mode = "cable"            # "cable": main has 10.42.0.1 on a shared wired connection; "lan": both on a router
twin_host = "twin"        # ssh alias
twin_addr = "10.42.0.11"
main_iface = "…"
[main]
family = "debian"; pkg = "apt"; desktop = "gnome"; session = "wayland"; gpu = "none"
[twin]
family = "arch"; pkg = "pacman"; desktop = "hyprland"; session = "wayland"; gpu = "amd"; gpu_arch = "gfx1032"
luks = true; initramfs = "mkinitcpio"; bootloader = "limine"; wired_iface = "…"; wired_mac = "…"; wol = "g"
[user]
twin_user = "…"
```

- `detect` writes it and the user may edit it. When a profile already exists, `detect` only fills
  in keys that are missing and never changes an existing value; `detect --force` rewrites every
  detected value (user-only keys such as `[user]` are always kept).
- Network mode: `cable` if the main PC holds `10.42.0.1/24` on the interface that reaches the twin,
  otherwise `lan`.
- Existing `~/.config/twinpc/config` (`TWIN_USER`, `TWIN_MAC`, `TWIN_IFACE`, …) remains supported;
  the `twin` CLI, hook and `twin-exec` read the profile first and the config file as fallback.

### 4. Step engine — `tool/twinpc_lib/steps.py`

```python
@dataclass
class Step:
    id: str                    # "audio.twin-output"
    feature: str               # "audio"
    machine: str               # "main" | "twin"
    root: bool
    describe: str              # one line for the plan / dry run
    check: Callable[[Ctx], bool]
    apply: Callable[[Ctx], None] | None   # None = manual step (instructions shown, user confirms)
    manual: str = ""           # instructions for manual steps (e.g. BIOS)
```

Runner: for each step in order — `check()`; if already satisfied → "✓ already done"; else show
`describe`, run `apply()` (or show `manual` and wait for confirmation unless `--yes`), then
`check()` again; a failed re-check stops the run with the step id, captured output and a hint.
`Ctx` gives `run(machine, cmd, root=False)` which runs locally or over `ssh <twin_host>`; root
commands use `sudo` (`ssh -t` for the twin) and prompt at most once per machine per run.
`--dry-run` prints the plan with each step's `check()` result and changes nothing.

### 5. Adapter interfaces — `tool/twinpc_lib/adapters/`

| Adapter | Interface | Implemented now |
|---|---|---|
| `packages` | `install(ctx, machine, logical_names)`, `installed(ctx, machine, name)`; logical → distro names in `packages.toml` | apt, pacman |
| `desktop` | `add_shortcut(…)`, `gui_backend()`, `remote_desktop_server()`, `session_env()` | gnome (main), hyprland (twin) |
| `gpu` | `ai_stack_steps(profile)` (Ollama variant, PyTorch index, env like `HSA_OVERRIDE_GFX_VERSION`) | amd |
| `bootunlock` | `unlock_steps(profile)` | mkinitcpio |
| `network` | `connection_steps(profile)`, `twin_address(profile)` | cable |

`adapters.get(kind, value)` returns the adapter or `Unsupported(kind, value, planned=True/False)`;
features whose adapter is unsupported yield a single step whose `check` reports the reason and
which `install` skips with a message (not an error).

### 6. Features as steps — `tool/twinpc_lib/features/`

The existing scripts become steps grouped by feature: `connection`, `cli`, `gpu-stack`, `routing`,
`power` (WoL), `unlock`, `kvm`, `audio`, `desktop`, `gui`, `mount`. Behaviour is unchanged for the
supported platforms; `main/install.sh` and `twinpc/install.sh` remain as thin wrappers that call
`twinpc install` for their machine.

### 7. `twinpc doctor`

Runs every feature's checks (read-only) and prints one row per feature:
`✅ working` · `⚠️ skipped: <reason>` · `❌ broken: <failing step id> — <hint>`. Exit 1 if any ❌.

## Errors

| Situation | Behaviour |
|---|---|
| Twin unreachable during `detect` | Write the main section, mark `[twin]` missing, exit 1 with how to fix SSH |
| Unknown / unsupported platform value | Recorded as is; affected features skipped with "unsupported" in `install`/`doctor` |
| A step's apply fails or its re-check fails | Stop; print step id, output, hint; nothing after it runs |
| `sudo` refused / no TTY for a root step | Stop with the exact command to run by hand |
| Profile from a newer schema version | Refuse with "upgrade twinpc" |

## Testing

- Probe: run `tool/probe.sh` in Docker containers of ubuntu, fedora and archlinux; assert `family`
  and `pkg`. Desktop, GPU, LUKS, initramfs and bootloader detection are tested with recorded
  fixture environments (fake `/sys`, `/etc/os-release`, PATH stubs) via env overrides the probe
  honours (`TWINPC_ROOT` prefix for file reads, PATH for tools).
- Profile: merge/preserve-user-edits, version check, cable/lan detection.
- Step engine: ordering, skip-when-done, stop-on-failure, manual steps, dry run, root prompting
  (with a fake runner).
- Adapters: package-name mapping; unsupported handling.
- Acceptance on the author's working machines: `twinpc doctor` all ✅, and `twinpc install --dry-run`
  reports every step already done; the existing 54 tests keep passing.
