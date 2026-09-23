# Carry-over shelf (drag and drop between the PCs) — design

**Status:** approved in brainstorming 2026-09-23 · branch `feat/shelf`

## Goal

Drag files from a folder on one PC and drop them into **any folder** on the other PC. Both PCs
get a thin drop strip on the screen edge that faces the other PC. Dropping files there makes a
small **shelf** pop up at the matching edge of the other PC, holding those files; the user moves
the mouse across (lan-mouse) and drags them from the shelf into whichever folder they want.
It ships as a new `shelf` feature of `twinpc`.

## Why not a real drag across the edge

- lan-mouse 0.11 forwards pointer and keyboard events only; it has no drag-and-drop protocol.
- On Wayland the compositor owns a drag. While one is in progress, the pointer can't be handed
  to the other machine, and neither GNOME nor Hyprland can pass a drag to another host.
- Hyprland's input-capture path is also what crashed on 2026-09-23, so nothing here may depend on it.

## What the user chose

- Folder-to-folder, into any folder — not ~/Downloads or a fixed bookmarked folder.
- The **carry-over shelf** design.
- **Approach A:**
  - a GTK4 app on both PCs;
  - file data moved by `rsync` over SSH;
  - control messages over the existing `twin-clipd` link.

## Verified on the real machines (2026-09-23)

| Piece | Finding |
|---|---|
| Main (Ubuntu, GNOME 50.1) | python3-gi with GTK 4 works; `rsync` present; no layer-shell on GNOME |
| Twin (Omarchy, Hyprland 0.56) | python-gobject 3.56, gtk4 4.22, **gtk4-layer-shell 1.3**, `rsync` present |
| Link | main → twin ssh works; the twin has **no** ssh key for the main PC (by design) |
| Edge | lan-mouse puts the twin on the main PC's **left** (profile `twin_side`, default `left`) |

## Architecture

```
MAIN PC (GNOME)                                    TWIN (Hyprland)
│▌ drop strip ◀─ drop files                        drop strip ▐│ ◀─ drop files
│  twin-shelf (GTK4)                                twin-shelf (GTK4 + layer-shell)
│      │ unix socket                                     │ unix socket
│  twin-clipd service ══ ssh (existing link) ══ twin-clipd --agent
│      └──────── rsync over ssh (file data) ─────────────┘
│  shelf pops up here                              shelf pops up here
```

### Components

1. **`clip/twin-shelf`**: one Python 3 + GTK4 script, the same file on both PCs.
   - **Drop strip:**
     - an always-present, thin (6 px), subtle window on the middle third of the facing edge;
     - a `Gtk.DropTarget` for `Gdk.FileList`;
     - it highlights on drag-over, and shows "twin not connected" in grey when the link is down.
   - **Shelf:**
     - a small window next to the strip listing the received files (icon and name);
     - each item is a `Gtk.DragSource` offering a `Gdk.FileList` (`text/uri-list`), and a "drag all" handle offers every item;
     - a ✕ closes it; it hides itself 2 minutes after the last activity;
     - while files are arriving it shows "receiving… N %"; items become draggable only when complete.
   - **Placement:**
     - twin: `gtk4-layer-shell` anchors the strip and shelf to the facing edge;
     - main: our GNOME extension places them (component 3).
   - It talks to its local `twin-clipd` over a unix socket, reconnecting every 5 s.
2. **`clip/twin-clipd`** gains the shelf protocol and transfers.
   - **Main service:**
     - accepts the shelf app on its existing socket (`hello` with `role: "shelf"`);
     - runs `rsync`: push for main → twin, pull for twin → main;
     - sends `shelf` messages to the local shelf app.
   - **Twin agent:**
     - opens its own socket `$XDG_RUNTIME_DIR/twinpc/shelf.sock` (directory mode 0700) for the twin's shelf app;
     - forwards its `drop` messages over the link;
     - makes holding folders on request;
     - passes `shelf` messages to the twin's shelf app.
   - `rsync` always runs **on the main PC**, so the twin still needs no key for the main PC.
3. **GNOME extension** (`twinpc-clipboard@twinpc`) gains window placement.
   - A window whose title is `twinPC drop strip` or `twinPC shelf` is moved to the facing edge, kept above other windows, shown on all workspaces, and skipped from the taskbar.
   - The edge comes from a settings file the shelf app writes (`$XDG_RUNTIME_DIR/twinpc/shelf.json`: `{"edge": "left"}`).
4. **The `shelf` feature** in `twinpc`, after `clipboard`:
   - packages:
     - main: `python3-gi` and `gir1.2-gtk-4.0`;
     - twin: `python-gobject`, `gtk4` and `gtk4-layer-shell` (added to `tool/packages.toml`);
   - `twin-shelf` installed on both PCs (a symlink here, a copy on the twin);
   - a user unit `twin-shelf.service` on both PCs, `WantedBy=graphical-session.target`, run with `--edge <left|right>` taken from `twin_side`;
   - a doctor check: `twin-shelf --selftest` (the app answers on its socket);
   - it needs the `clipboard` feature (the link and the GNOME extension). Without it, `shelf` is skipped with that reason.
5. **`twin shelf status|on|off`** in the `twin` CLI; a man page section; README updated (feature card, cheat sheet, troubleshooting, layout, test counts).

## Messages (added to the twin-clipd frame format)

None of these carries file bytes (all are control frames with an empty body):

| Frame | From → to | Fields |
|---|---|---|
| `hello` role `shelf` | shelf app → local twin-clipd | — |
| `drop` | shelf app → local twin-clipd; twin agent → main service | `paths`: list of absolute paths |
| `slot` / `slot-ready` | main service → twin agent / back | `dir` in the reply |
| `shelf` | twin-clipd → local shelf app | `dir`, `paths`, `state`: `receiving` or `ready` or `failed`, `progress` 0–100, `error` |
| `link` | twin-clipd → local shelf app | `up`: bool (for the grey strip) |

## How a drop travels

- **Main → twin:**
  1. Drop on the main strip. The shelf app sends `drop {paths}`.
  2. The service checks the paths exist and their total size. It asks the agent for a `slot`; the agent makes `~/.cache/twinpc/shelf/<n>/`, prunes to the newest 5 and replies `slot-ready {dir}`.
  3. The service checks the twin's free space (`df` over ssh) against the total. It sends the agent `shelf {state: receiving}`, which passes it to the twin's shelf app.
  4. The service runs `rsync -a --protect-args --info=progress2 -e ssh -- <paths> <host>:<dir>/`, forwarding progress to the twin's shelf.
  5. On success it sends `shelf {state: ready, paths}`.
- **Twin → main:**
  1. Drop on the twin strip. The twin's shelf app sends `drop {paths}` to the agent, which forwards it over the link.
  2. The service creates `~/.cache/twinpc/shelf/<n>/` locally and checks free space. It runs `rsync -a --protect-args --info=progress2 -e ssh -- <host>:<path>… <dir>/`, sending progress and then `ready` to the main shelf app.

## Error handling

| Situation | Behaviour |
|---|---|
| Link down (twin off, agent not connected) | Strip is grey; a drop is refused with a notification; nothing is queued |
| `rsync` fails or is interrupted | Shelf shows "transfer failed — <reason>"; the partial holding folder is deleted; sources are never modified |
| Some files unreadable or special | `rsync` skips them; shelf shows "3 of 4 copied"; the names are logged |
| Not enough free space on the receiver | Refused before copying, with a notification |
| Shelf app not running on the receiver | Files still arrive; a notification says where |
| A path from the other side that isn't absolute or doesn't exist | That drop is refused and logged |

## Security

- Data travels only over the existing SSH link; no new ports; the twin gets no key for the main PC.
- Every path is its own argv element for `rsync` (`--protect-args`, a `--` before the paths). No shell is involved on the main PC, and there's no remote shell expansion.
- Holding folders are created only under `~/.cache/twinpc/shelf/`; the receiver never writes elsewhere.
- The twin can only *offer* paths; the main PC decides to pull them, into its own holding folder.
- The sockets live in `$XDG_RUNTIME_DIR/twinpc` with mode 0700.

## Testing

- **Unit tests** (`tests/test_shelf.py`, plus additions to `tests/test_clipd.py`):
  - new frames, valid and malformed;
  - building `rsync` argv for spaces, quotes, a leading `-` and unicode;
  - numbering and pruning holding folders;
  - the free-space check;
  - progress-line parsing;
  - the facing edge from `twin_side`;
  - the `shelf` feature's steps on GNOME, Hyprland and unsupported desktops, and the dependency on `clipboard`.
- **Integration test** (`tests/test_clipd_shelf.py`): service and agent through a local pipe, with a stub `rsync` doing local copies and fake shelf clients on both sockets. It checks:
  - both directions;
  - progress, then ready;
  - a failed transfer cleaning up;
  - the twin away (the drop is refused);
  - a bad path from the "twin" being refused.
- **System check** `tests/test_shelf.sh`: drops a temporary folder in each direction through the sockets and checks the files and the `ready` message on the other side.
- **By hand:** drag real files and a large folder between Files windows on both PCs.

## Out of scope

- A true single drag across the edge (not possible today; see above).
- Moving instead of copying.
- Dropping onto the shelf to send files back the other way — the strip on each PC is the way in.
- Desktops other than GNOME (main) and Hyprland (twin) — they arrive through the same adapter pattern later.
