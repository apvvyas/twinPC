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
    }

    disable() {
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
            this._send({kind: 'hello', role: 'extension'});
            this._readFrame();
        });
    }

    _retry() {
        if (this._cancel.is_cancelled())
            return;
        this._conn = this._in = this._out = null;
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

    _send(header, bytes) {
        if (!this._out)
            return;
        try {
            this._out.write_all(frame(header, bytes), null);
        } catch (e) {
            this._drop();
        }
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
            this._transfer(header.mime);
        else if (header.kind === 'set')
            this._set(header.mime, body);
    }

    _transfer(mime) {
        const out = Gio.MemoryOutputStream.new_resizable();
        this._selection.transfer_async(CLIPBOARD, mime, -1, out, this._cancel, (selection, res) => {
            try {
                selection.transfer_finish(res);
                out.close(null);
                const bytes = out.steal_as_bytes().toArray();
                if (bytes.length > DATA_LIMIT)
                    this._send({kind: 'data', mime, over: bytes.length});
                else
                    this._send({kind: 'data', mime}, bytes);
            } catch (e) {
                this._send({kind: 'data', mime});        // the clipboard changed meanwhile: an empty answer
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
}
