# Twin task routing — design

Date: 2026-09-23 · Status: approved in conversation, awaiting spec review

## Goal

Commands typed in a terminal on the main PC run on the twin PC automatically when twin is the better
place for them — without the user typing anything different — and every task sent to twin is visible
as a live session on twin's desktop.

### Machines

| | Main PC | Twin |
|---|---|---|
| OS / desktop | Ubuntu 26.04, GNOME (Wayland), bash 5.3 | Omarchy (Arch), Hyprland 0.56 (Lua dispatch) |
| CPU / RAM | fast desktop CPU (many cores) | slower CPU, less RAM |
| GPU | none | AMD GPU with ROCm (RDNA2: HSA_OVERRIDE_GFX_VERSION=10.3.0) |
| Link | 10.42.0.1 (NetworkManager shared connection) | 10.42.0.11, ssh alias `twin`, existing `twin` CLI |

Because the main PC is much faster at CPU work, twin is used for GPU/AI work, background agents,
and CPU work only when the main PC is loaded.

### Decisions made

| Topic | Decision |
|---|---|
| Workloads | Heavy commands, AI/GPU work, background agents. GUI apps are out of scope. |
| Decision method | Rules file + main-PC load. |
| CPU-bound commands | Stay local unless the main PC is loaded. |
| Files | Twin-only workspaces: projects live in twin `~/work`, mounted on the main PC at `~/twin`. |
| Interception | Transparent bash hook that rewrites the command line on Enter (approach A). |
| Twin unavailable | Ask each time: [h]ere / [w]ake twin / [c]ancel. |
| Visibility | Each twin task gets a terminal window on twin's Hyprland workspace 9. |

### Out of scope

GUI applications; commands started by IDEs, scripts or other programs (only interactive bash is
intercepted — PATH shims could be added later for specific programs); syncing files from main-PC-only
folders; shells other than bash.

## Components

### 1. `twin-route` — decision (Python 3, stdlib only)

`twin-route [--explain] [--load CPU,RAM] [--cwd DIR] -- "<command line>"` prints one line:
`twin <reason>` or `local <reason>`, exit 0. Pure function of (line, cwd, load, rules) — no network, no
side effects — so it is unit-testable; `--load` injects load for tests. Target < 20 ms.

Order of evaluation (first match wins):
1. Unroutable line (multi-line, trailing `\`, heredoc, unparsable quoting, empty) → `local unparsable`.
2. Line starts with `local ` → `local forced`; with `twin-exec` or `twin ` → `local passthrough`
   (already goes to twin through its own tooling).
3. First word of *every* pipeline stage in `never_twin` → `local never:<pattern>`.
4. cwd inside a workspace (`~/twin/...`) → `twin workspace`.
5. Any stage matches `always_twin` → `twin always:<pattern>`.
6. Any stage matches `load_offload` and (CPU ≥ cpu_threshold or RAM ≥ ram_threshold) →
   `twin load:<pattern> cpu=…% ram=…%`; if it matches but the load is below the thresholds → `local load-ok`.
7. Otherwise → `local default`.

8. Files guard — a twin decision from rules 5–6 is downgraded to `local files-not-on-twin` when cwd
   is outside a workspace **and** either a stage matches `needs_cwd` (commands that work on the current
   folder: `claude`, `make`, `pytest`, `npm`, `cargo`, `gradle`, `docker build`, `python <file>`) or an
   argument is a relative path that exists locally (`./x`, `x.py`, `data/`). The marker tells the user to
   move the project into `~/twin` to have it run on twin.

Load = 1-minute CPU utilisation from `/proc/stat` samples cached by the hook (see 4) and `MemAvailable`
from `/proc/meminfo`.

### 2. `~/.config/twin-route/rules.toml` — policy

```toml
[load]
cpu_threshold = 80        # % busy, 1-minute average
ram_threshold = 85        # % used

[workspace]
local = "~/twin"
remote = "~/work"

always_twin = ["ollama", "python *train*.py", "python3 *train*.py", "torchrun", "accelerate",
               "whisper", "comfyui", "stable-diffusion*", "claude -p*", "llama-*"]
never_twin  = ["cd", "ls", "git", "vim", "nvim", "nano", "code", "sudo", "apt", "systemctl",
               "ssh", "exit", "source", ".", "export", "history", "man", "local", "twin", "twin-exec"]
load_offload = ["make", "cargo build*", "cargo test*", "pytest*", "npm run build*", "npm test*",
                "ffmpeg", "docker build*", "gradle*", "python *"]
needs_cwd   = ["claude*", "make*", "pytest*", "npm *", "cargo *", "gradle*", "docker build*",
               "python *.py*", "python3 *.py*"]
```

Patterns are shell-style globs matched against a pipeline stage's text (first word, or the stage
with its arguments). A default file is written on first run; the user edits it freely.

### 3. `twin-exec` — runner (bash)

`twin-exec [--name N] -- "<command line>"`:
1. Maps cwd: `~/twin/<p>` → `~/work/<p>`, else twin `~`.
2. Creates a tmux session on twin named `task-<MMDD-HHMMSS>-<first word>` running
   `cd <dir> && <REMOTE_ENV> && <line>`, output logged to `~/jobs/<session>.log`, final line
   `[exit N]` (same as `twin run`), with an allow-list of env vars passed (`TERM`, `LANG`, `COLORTERM`,
   plus `TWIN_ROUTE_ENV_ALLOW` additions).
3. Opens the task viewer on twin (component 6).
4. Attaches the local terminal: `ssh -t twin tmux attach -t <session>`; Ctrl-C passes through tmux to
   the process; Ctrl-b d detaches (task keeps running, `twin ls` shows it).
5. After the session ends, reads `[exit N]` from the log and exits with N. If the connection drops,
   prints `twin connection lost — task keeps running on twin (twin attach <session>)` and exits 255.
6. If the task ran > 60 s, sends a GNOME notification with the command and exit code.

### 4. Bash hook — `twin-route.bash` (sourced from `~/.bashrc`)

- Binds Enter (`\C-m` and `\C-j`) to a two-step readline macro: `"\C-x\C-t\C-x\C-a"`, where
  `\C-x\C-t` is a `bind -x` function and `\C-x\C-a` is bound to `accept-line` (`bind -x` alone
  cannot accept a line). The function reads `READLINE_LINE`, calls `twin-route`, and on a `twin`
  decision rewrites `READLINE_LINE` to `twin-exec -- <quoted original>`, remembers the original in
  `_TWIN_ROUTE_ORIG`, and prints a dim marker `→ twin (<reason>)`.
- History: bash records the rewritten line, so a `PROMPT_COMMAND` step placed **before** the existing
  `history -a; history -c; history -r` replaces the last history entry with `_TWIN_ROUTE_ORIG`
  (`history -d -1; history -s "$orig"`) and clears the variable. History therefore shows what the user
  typed.
- Before calling the router, checks twin's stage via the existing banner check, cached 5 s. If the
  decision is twin and twin is not `up`, prompts `twin is <state>: [h]ere / [w]ake / [c]ancel`:
  h → run the original line locally; w → `twin wake` (Wake-on-LAN + disk password) then run on twin;
  c → clear the line.
- Maintains the CPU load sample (`/proc/stat` delta) in `$XDG_RUNTIME_DIR/twin-route.load`, refreshed at
  most every 5 s by a background sampler started once per login.
- Fail-safe: any router error or > 200 ms → run the original line locally, unchanged.
- Kill switch: `TWIN_ROUTE=off` (per shell) or `twin route off|on|status` (persistent flag file).

### 5. Workspace mount

`~/.config/systemd/user/twin-mount.service` mounts `twin:work` at `~/twin` with sshfs
(`reconnect,ServerAliveInterval=15,idmap=user,follow_symlinks`), ordered after `twin-link.service`, and
restarts on failure. Requires the `sshfs` package on the main PC and `~/work` on twin. If the mount is
down, the hook refuses to route commands whose cwd is under `~/twin` and suggests
`systemctl --user restart twin-mount` or `twin wake`.

### 6. Task viewer on twin

After creating the session, `twin-exec` runs on twin (via the existing GUI environment):
`hyprctl dispatch 'hl.dsp.exec_cmd("[workspace 9 silent] <terminal> -e twin-task-view <session>")'`
where `twin-task-view` attaches to the tmux session, and after it ends shows `[exit N]`, closes after
30 s on exit 0, and stays open on non-zero exit. Workspace 9 is named "tasks". Existing `twin run`,
`twin cc` and `twin bizdev` jobs open a viewer too. If no Hyprland session is running (twin not logged
in), the viewer step is skipped silently.

## Errors

| Situation | Behaviour |
|---|---|
| Router error / slow | Run locally unchanged |
| Twin not up | h / w / c prompt |
| Connection lost mid-task | Task continues on twin; message shows how to reattach; exit 255 |
| Mount down, cwd in `~/twin` | Refuse to run; explain; suggest restart/wake |
| No Hyprland session on twin | Task runs; no viewer window |
| Unparsable / multi-line input | Local, unchanged |

## Testing

- Unit tests for `twin-route` (~40 cases): each rule type, precedence, load thresholds via `--load`,
  workspace mapping, pipelines (`ollama run x | tee out`), forced prefixes, parse edge cases, the
  files-not-on-twin rule.
- Integration test for `twin-exec` against the real twin: `echo` output, exit code propagation
  (`exit 3` → 3), Ctrl-C reaches the remote process, tmux session + workspace-9 window created.
- Hook test: scripted interactive bash (pty) feeding lines, asserting the executed line and history.
- Manual acceptance with the user: `ollama run gemma3:4b hi` routes to twin and appears on workspace 9;
  with twin off the h/w/c prompt appears; `git status` in `~/twin/proj` runs locally on the mounted files.

## Files

```
~/projects/twinPC/
  twin                         (existing CLI: + `twin route on|off|status`, viewer for run/cc/bizdev)
  route/twin-route             (Python router)
  route/twin-exec              (runner)
  route/twin-route.bash        (bash hook)
  route/rules.default.toml
  route/twin-task-view         (installed to twin ~/.local/bin)
  route/twin-mount.service
  tests/test_twin_route.py
  tests/test_twin_exec.sh
  tests/test_hook.py
```
