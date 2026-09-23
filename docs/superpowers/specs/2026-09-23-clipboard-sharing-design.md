# Clipboard sharing — design

**Status:** approved in brainstorming 2026-09-23 · branch `feat/clipboard-sharing`

## Goal

Copy on either PC, paste on the other — text, images and files — automatically, over the
existing SSH link, with no new open ports. It ships as a new `clipboard` feature of `twinpc`,
so it follows the portable design (desktop adapters, idempotent steps, doctor).

## What the user asked for

- Share **text, images and files** in both directions.
- Files go across **automatically up to a limit** (500 MB total); bigger selections are
  skipped with a notification pointing at `twin push`.
- Approach **A**: a GNOME Shell extension on the main PC plus a twinpc clipboard service.

## Decisions taken without asking (the user did not object)

- Content marked secret by password managers (`x-kde-passwordManagerHint`) is never sent.
- The primary selection (middle-click paste) is not synced.
- No queue: a copy made while the twin is unreachable is dropped; the next copy syncs.
- Received files live in `~/.cache/twinpc/clipboard/<n>/`; the newest 5 are kept.

## Verified on the real machines (2026-09-23)

| Piece | Finding |
|---|---|
| Main: GNOME Shell 50.1, Wayland | No data-control protocol: `wl-paste --watch` fails ("requires a compositor that supports the data-control protocol"). One-shot `wl-copy`/`wl-paste` work. |
| Main: GNOME APIs | Meta-18 has `Selection` `owner-changed`, `get_mimetypes`, `transfer_async`, `SelectionSourceMemory`, `set_owner`; St-18 has `Clipboard.get_content`/`set_content`/`get_mimetypes`. `gnome-extensions` 50.1 present. |
| Twin: Hyprland (Omarchy) | `wl-paste --watch` fires over SSH once `XDG_RUNTIME_DIR` and `WAYLAND_DISPLAY` are set (`wayland-1`). `wl-copy` works but forks a server that keeps the SSH stdout open — the agent must start it with stdin/stdout/stderr detached. |
| lan-mouse 0.11 | No clipboard support, so this is a separate feature. |

## Architecture

```
MAIN PC (GNOME)                                         TWIN (Hyprland)
┌──────────────────────────┐                            ┌──────────────────────────┐
│ GNOME extension          │  unix socket               │ twin-clipd --agent       │
│ twinpc-clipboard@twinpc  │◀──────────▶┐               │  • wl-paste --watch      │
│ (watch + set clipboard)  │            │               │  • wl-copy (detached)    │
└──────────────────────────┘      ┌─────┴────────┐ ssh  │  • packs/unpacks files   │
                                  │ twin-clipd   │══════▶ (stdin/stdout frames)     │
                                  │ user service │      └──────────────────────────┘
                                  └──────────────┘
```

### Components

1. **`clip/twin-clipd`** — one Python 3 stdlib script, two roles.
   - Default role (main PC), run by the user unit `main/twin-clip.service`:
     - listens on `$XDG_RUNTIME_DIR/twinpc/clip.sock` (directory mode `0700`) for the extension;
     - keeps `ssh -o BatchMode=yes -o ServerAliveInterval=15 -- <twin_host> <path>/twin-clipd --agent` running and reconnects every 10 s after it ends.
   - `--agent` role (twin): started by that SSH command, not a service of its own.
     - Finds the Wayland display (`XDG_RUNTIME_DIR=/run/user/$UID`, the first `wayland-*` socket that isn't a `.lock`).
     - Runs `wl-paste --watch` to learn about copies and `wl-copy --type <mime>` to apply them.
     - Starts `wl-copy` with stdin fed the content and stdout/stderr on `/dev/null`, so it can't hold the SSH stream open.
   - A `--selftest` flag reports whether the extension socket and the agent answer (used by doctor and `twin clip status`).
2. **`clip/gnome-extension/twinpc-clipboard@twinpc/`** — `metadata.json` (shell-version `["50"]`) + `extension.js`.
   - Watches `global.display.get_selection()` `owner-changed` for the CLIPBOARD selection.
   - Reads offered types with `get_mimetypes` and content with `transfer_async`.
   - Sets the clipboard with `Meta.SelectionSourceMemory` + `set_owner`, one source per mime type offered.
   - Talks to `twin-clipd` over the unix socket with the same frame format as the SSH link (below).
   - Ignores owner changes it caused itself.
3. **Desktop adapters** gain `clipboard_steps(feature, pk, v)`:
   - **Gnome**: copy the extension to `~/.local/share/gnome-shell/extensions/twinpc-clipboard@twinpc/`, then `gnome-extensions enable`. A manual step says: "log out and back in once to load the clipboard extension".
   - **Hyprland**: the `wl-clipboard` package (added to `tool/packages.toml`); the agent needs nothing else.
   - A desktop adapter without `clipboard_steps`, or an unsupported desktop, is skipped with `desktop '<v>' on the main PC|the twin is not supported yet (planned)` — the same wording as the other desktop features.
4. **The `clipboard` feature** in `tool/twinpc_lib/features.py`, after `kvm`:
   - main-side package `wl-clipboard` (the fallback path below);
   - the `twin-clipd` symlink into `~/.local/bin` on both machines;
   - the user unit, enabled `--now` on main;
   - the desktop steps.
5. **`twin clip status|on|off`** in the `twin` CLI; the man page and completion are updated.

## The frame protocol

One frame = one header line, then exactly `size` bytes of body:

```
{"v":1,"kind":"text"|"image"|"files","mime":"<mime>","sha":"<sha256 hex of body>","size":<int>}\n
<body>
```

- `text`: UTF-8 body, mime `text/plain;charset=utf-8`.
- `image`: body is PNG bytes, mime `image/png`.
- `files`: body is an uncompressed tar of the copied paths (top-level names only).
- Control frames: `{"v":1,"kind":"hello"}` (answered with `hello`, used by `--selftest`) and `{"v":1,"kind":"offer","mimes":[...]}` / `{"v":1,"kind":"want","mime":"..."}` between the extension and the service.
- A header that isn't valid JSON, has an unknown `v`, a `size` over its kind's limit, or a body whose sha doesn't match is rejected. The rest of that frame is discarded, the connection is closed, and the service reconnects.

**Limits:** text 10 MB, image 50 MB, files 500 MB (the sum of the file sizes, checked before packing).

## How a copy travels

- **Main → twin:**
  1. The extension sees `owner-changed` and sends `offer` with the mime list.
  2. The service picks the best type: files (`text/uri-list` present) > `image/png` > text. It sends `want`, and the extension answers with a content frame.
  3. The service forwards it over SSH, and the agent applies it.
- **Twin → main:**
  1. `wl-paste --watch` wakes the agent, which runs `wl-paste --list-types`, picks the best type the same way, reads it and sends a frame.
  2. The service passes it to the extension, which sets GNOME's clipboard.
- **Files:**
  - The sender turns the `file://` URIs into paths and checks the 500 MB limit (over → `notify-send "twinPC clipboard" "too big — use twin push"`, nothing sent). It then packs with `tarfile`.
  - The receiver unpacks into a new `~/.cache/twinpc/clipboard/<n>/`, deletes all but the newest 5 folders, and sets both `text/uri-list` and `x-special/gnome-copied-files` (`copy\nfile:///…`).
- **No echo:** each side stores the sha of the last frame it applied or sent, and never sends a frame whose sha equals it.
- **Secrets:** an offer containing `x-kde-passwordManagerHint` is dropped.

## Error handling

| Situation | Behaviour |
|---|---|
| Twin off or asleep | Main copies aren't sent; the service retries SSH every 10 s; the next copy after reconnect syncs. |
| SSH drops mid-frame | The partial frame is discarded; the receiving clipboard is unchanged. |
| Too big / unpack fails | `notify-send` on the PC where it happened; the clipboard is unchanged. |
| Extension not loaded yet | Twin → main uses one-shot `wl-copy` on GNOME; main → twin waits for the extension; doctor shows ⚠️ "log out and back in to finish". |
| Unsafe tar member (`..`, absolute path, a link pointing outside, a device file) | The whole frame is rejected before anything is written. |

## Security

- Traffic goes only through the existing SSH link; no new ports and no firewall rules.
- The unix socket directory is `0700` in `$XDG_RUNTIME_DIR`.
- Tar extraction goes through a checked member list (above), never `extractall` on unchecked input.
- `twin clip off` stops syncing for private copying.

## Testing

- **Unit tests** in `tests/tool/test_clipboard.py` (stdlib `unittest`):
  - frame encode/decode: a round trip, a truncated body, a sha mismatch, over-limit, and bad JSON;
  - best-type choice and the password-manager hint;
  - echo suppression;
  - the file limit;
  - safe unpacking against `../x`, `/etc/x`, and a symlink to `/` followed by a file through it;
  - pruning to the newest 5 folders;
  - `clipboard` steps for gnome/hyprland and for an unsupported desktop.
- **Integration test:** the service and `--agent` connected through a local pipe, with stub `wl-copy`/`wl-paste` on `PATH` and a fake extension client on the socket. Checks text, image and files in both directions, and that nothing echoes.
- **System check** `tests/test_clipboard.sh`: copies a random token on each PC and reads it back on the other (it needs both PCs up and the extension loaded).

## Out of scope

- The primary selection.
- A clipboard history.
- Sync while the twin is unreachable.
- Other desktops' watchers (KDE, Sway) — they arrive with sub-project 4 through the same adapter method.
