// twinPC clipboard: tells twin-clipd (a user service) when GNOME's clipboard changes, hands it the
// content it asks for, and puts content from the twin on the clipboard. GNOME has no data-control
// protocol, so this is the only way to watch the clipboard. Frame format: see clip/twin-clipd.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const CLIPBOARD = Meta.SelectionType.SELECTION_CLIPBOARD;
const DATA_LIMIT = 50 * 1024 * 1024;
const RETRY_SECONDS = 5;
const encoder = new TextEncoder();
const decoder = new TextDecoder();
// twin-shelf's windows: Wayland apps can't place themselves on GNOME, so the extension does it
const PLACED = ['twinPC drop strip', 'twinPC shelf'];
const EDGES = ['left', 'right', 'top', 'bottom'];

function frame(header, bytes = new Uint8Array(0)) {
    const sha = GLib.compute_checksum_for_data(GLib.ChecksumType.SHA256, bytes);
    const head = encoder.encode(`${JSON.stringify({v: 1, ...header, size: bytes.length, sha})}\n`);
    const out = new Uint8Array(head.length + bytes.length);
    out.set(head);
    out.set(bytes, head.length);
    return out;
}

export default class TwinClipboard extends Extension {
    enable() {
        this._cancel = new Gio.Cancellable();
        this._selection = global.display.get_selection();
        this._ownerId = this._selection.connect('owner-changed', (_selection, type) => {
            if (type === CLIPBOARD)
                this._offer();
        });
        this._connect();
        this._watched = new Map();
        this._createdId = global.display.connect('window-created', (_display, win) => this._watch(win));
        for (const actor of global.get_window_actors())
            this._watch(actor.meta_window);
    }

    disable() {
        global.display.disconnect(this._createdId);
        for (const win of [...this._watched.keys()])
            this._unwatch(win);
        this._watched = null;
        this._selection.disconnect(this._ownerId);
        this._cancel.cancel();
        if (this._retryId)
            GLib.source_remove(this._retryId);
        this._conn?.close(null);
        this._conn = this._in = this._out = this._selection = this._retryId = null;
    }

    _connect() {
        const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'twinpc', 'clip.sock']);
        new Gio.SocketClient().connect_async(new Gio.UnixSocketAddress({path}), this._cancel, (client, res) => {
            try {
                this._conn = client.connect_finish(res);
            } catch (e) {
                this._retry();
                return;
            }
            this._in = new Gio.DataInputStream({base_stream: this._conn.get_input_stream()});
            this._out = this._conn.get_output_stream();
            this._queue = [];
            this._writing = false;
            this._send({kind: 'hello', role: 'extension'});
            this._readFrame();
        });
    }

    _retry() {
        if (this._cancel.is_cancelled())
            return;
        this._conn = this._in = this._out = null;
        this._queue = [];
        this._writing = false;
        this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, RETRY_SECONDS, () => {
            this._retryId = null;
            this._connect();
            return GLib.SOURCE_REMOVE;
        });
    }

    _drop() {
        this._conn?.close(null);
        this._retry();
    }

    // Writes are queued and asynchronous: a blocking write would stall gnome-shell, and the whole desktop.
    _send(header, bytes) {
        if (!this._out)
            return;
        this._queue.push(new GLib.Bytes(frame(header, bytes)));
        this._flush();
    }

    _flush() {
        if (this._writing || !this._out || this._queue.length === 0)
            return;
        this._writing = true;
        const out = this._out;
        const bytes = this._queue[0];
        out.write_bytes_async(bytes, GLib.PRIORITY_DEFAULT, this._cancel, (stream, res) => {
            this._writing = false;
            let written;
            try {
                written = stream.write_bytes_finish(res);
            } catch (e) {
                if (!this._cancel.is_cancelled() && out === this._out)
                    this._drop();
                return;
            }
            if (out !== this._out)
                return;                                   // the connection was replaced meanwhile
            if (written < bytes.get_size())
                this._queue[0] = GLib.Bytes.new_from_bytes(bytes, written, bytes.get_size() - written);
            else
                this._queue.shift();
            this._flush();
        });
    }

    _offer() {
        this._send({kind: 'offer', mimes: this._selection.get_mimetypes(CLIPBOARD)});
    }

    _readFrame() {
        this._in.read_line_async(GLib.PRIORITY_DEFAULT, this._cancel, (stream, res) => {
            let header;
            try {
                const [line] = stream.read_line_finish_utf8(res);
                if (line === null)
                    throw new Error('the service went away');
                header = JSON.parse(line);
            } catch (e) {
                if (!this._cancel.is_cancelled())
                    this._drop();
                return;
            }
            this._readBody(header, header.size || 0, [], 0);
        });
    }

    _readBody(header, size, chunks, got) {
        if (got >= size) {
            const body = new Uint8Array(size);
            let at = 0;
            for (const chunk of chunks) {
                body.set(chunk, at);
                at += chunk.length;
            }
            this._handle(header, body);
            this._readFrame();
            return;
        }
        this._in.read_bytes_async(Math.min(size - got, 1 << 20), GLib.PRIORITY_DEFAULT, this._cancel, (stream, res) => {
            let data;
            try {
                data = stream.read_bytes_finish(res).toArray();
                if (data.length === 0)
                    throw new Error('the service went away');
            } catch (e) {
                if (!this._cancel.is_cancelled())
                    this._drop();
                return;
            }
            chunks.push(data);
            this._readBody(header, size, chunks, got + data.length);
        });
    }

    _handle(header, body) {
        if (header.kind === 'want')
            this._transfer(header.mime, header.id);
        else if (header.kind === 'set')
            this._set(header.mime, body);
    }

    _transfer(mime, id) {
        const out = Gio.MemoryOutputStream.new_resizable();
        this._selection.transfer_async(CLIPBOARD, mime, -1, out, this._cancel, (selection, res) => {
            try {
                selection.transfer_finish(res);
                out.close(null);
                const bytes = out.steal_as_bytes().toArray();
                if (bytes.length > DATA_LIMIT)
                    this._send({kind: 'data', mime, id, over: bytes.length});
                else
                    this._send({kind: 'data', mime, id}, bytes);
            } catch (e) {
                this._send({kind: 'data', mime, id});    // the clipboard changed meanwhile: an empty answer
            }
        });
    }

    _set(mime, body) {
        const clipboard = St.Clipboard.get_default();
        if (mime.startsWith('text/plain'))
            clipboard.set_text(St.ClipboardType.CLIPBOARD, decoder.decode(body));
        else
            clipboard.set_content(St.ClipboardType.CLIPBOARD, mime, new GLib.Bytes(body));
    }

    _watch(win) {
        if (this._watched.has(win))
            return;
        this._watched.set(win, [
            win.connect('notify::title', () => this._place(win)),
            win.connect('size-changed', () => this._place(win)),
            win.connect('unmanaged', () => this._unwatch(win)),
        ]);
        this._place(win);
    }

    _unwatch(win) {
        for (const id of this._watched?.get(win) ?? [])
            win.disconnect(id);
        this._watched?.delete(win);
    }

    _place(win) {
        const title = win.get_title();
        if (!PLACED.includes(title))
            return;
        const edge = this._edge();
        const mon = this._edgeMonitor(edge);
        const rect = win.get_frame_rect();
        const gap = title === PLACED[1] ? 14 : 0;           // the shelf sits just inside the strip
        let x, y;
        if (edge === 'left' || edge === 'right') {
            x = edge === 'left' ? mon.x + gap : mon.x + mon.width - rect.width - gap;
            y = mon.y + Math.round((mon.height - rect.height) / 2);
        } else {
            x = mon.x + Math.round((mon.width - rect.width) / 2);
            y = edge === 'top' ? mon.y + gap : mon.y + mon.height - rect.height - gap;
        }
        if (rect.x !== x || rect.y !== y)
            win.move_frame(true, x, y);
        if (!win.is_above())
            win.make_above();
        if (!win.is_on_all_workspaces())
            win.stick();
    }

    _edge() {
        try {
            const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'twinpc', 'shelf.json']);
            const [, bytes] = GLib.file_get_contents(path);
            const edge = JSON.parse(decoder.decode(bytes)).edge;
            if (EDGES.includes(edge))
                return edge;
        } catch (e) {
            // not written yet: fall back to the default below
        }
        return 'left';
    }

    _edgeMonitor(edge) {
        let best = global.display.get_monitor_geometry(0);
        for (let i = 1; i < global.display.get_n_monitors(); i++) {
            const g = global.display.get_monitor_geometry(i);
            if ((edge === 'left' && g.x < best.x) ||
                (edge === 'right' && g.x + g.width > best.x + best.width) ||
                (edge === 'top' && g.y < best.y) ||
                (edge === 'bottom' && g.y + g.height > best.y + best.height))
                best = g;
        }
        return best;
    }
}
