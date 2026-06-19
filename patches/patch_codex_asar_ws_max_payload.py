#!/usr/bin/env python3
"""Patch M - raise shared app-server WebSocket max payload.

The patched Desktop lane uses CODEX_APP_SERVER_WS_URL so the renderer connects
to a shared local `codex.exe app-server --listen ws://127.0.0.1:<port>`.
On machines with heavy threads, many automations, or large hydrated state, the
renderer can receive a message larger than the Node `ws` client default
payload limit. Electron then logs `Max payload size exceeded`, closes the
connection with code 1006, and transiently reports `Codex app-server is not
available`; model, provider, MCP, and thread resume UI state all appear missing
until the reconnect catches up.

This patch adds `maxPayload: 1024*1024*1024` to the minified WebSocket
constructor used for the app-server transport. It is scoped to constructors
that pass `this.options.websocketUrl` and `perMessageDeflate:!1`, so unrelated
WebSockets are left alone.

Idempotent: rerunning normalizes the target object to include the
`/*M*/maxPayload:1024*1024*1024` marker and then becomes a no-op.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
from pathlib import Path

PATCH_MARKER = "/*M*/maxPayload:1024*1024*1024"
TARGET_HINT = "this.options.websocketUrl"

WS_CONSTRUCTOR_PATTERN = re.compile(
    r"new\s+(?P<ctor>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\(this\.options\.websocketUrl,\{(?P<body>[^{}]*?perMessageDeflate:!1[^{}]*?)\}\)"
)
WS_OPTIONS_VAR_PATTERN = re.compile(
    r"(?P<prefix>[A-Za-z_$][A-Za-z0-9_$]*=)\{(?P<body>headers:[^{}]*?perMessageDeflate:!1[^{}]*?)\}"
    r"(?P<suffix>,[A-Za-z_$][A-Za-z0-9_$]*=.*?new\s+[A-Za-z_$][A-Za-z0-9_$]*"
    r"\(this\.options\.websocketUrl,)",
)

MAX_PAYLOAD_PATTERN = re.compile(r"(?:/\*M\*/)?maxPayload:[^,}]+")


def read_header(asar_path: Path):
    with asar_path.open("rb") as f:
        prefix = f.read(16)
        first, header_size, _, json_size = struct.unpack("<IIII", prefix)
        if first != 4 or json_size <= 0:
            raise RuntimeError(f"Unexpected ASAR prefix: {(first, header_size, json_size)}")
        raw_json = f.read(json_size)
        header = json.loads(raw_json.decode("utf-8"))
        payload_start = 8 + header_size
    return header, payload_start


def iter_files(node, parts=()):
    for name, meta in node.get("files", {}).items():
        cp = parts + (name,)
        if "files" in meta:
            yield from iter_files(meta, cp)
        else:
            yield "/".join(cp), meta


def iter_js_entries(header):
    for p, m in iter_files(header):
        if p.endswith(".js") and "offset" in m:
            yield p, m


def extract(asar_path, payload_start, meta):
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))


def patch_body(body: str):
    if PATCH_MARKER in body:
        return body, False
    if "maxPayload:" in body:
        patched, count = MAX_PAYLOAD_PATTERN.subn(PATCH_MARKER, body, count=1)
        return patched, count > 0
    return body.replace("perMessageDeflate:!1", f"perMessageDeflate:!1,{PATCH_MARKER}", 1), True


def patch_js(data: bytes):
    text = data.decode("utf-8")
    if TARGET_HINT not in text or "perMessageDeflate:!1" not in text:
        return data, {"status": "not_target", "changed": 0, "targets": 0}

    changed = 0
    targets = 0
    out = []
    last = 0

    for match in WS_CONSTRUCTOR_PATTERN.finditer(text):
        body = match.group("body")
        if "headers:" not in body:
            continue

        targets += 1
        patched_body, did_change = patch_body(body)
        if did_change:
            changed += 1

        out.append(text[last : match.start()])
        out.append(f"new {match.group('ctor')}(this.options.websocketUrl,{{{patched_body}}})")
        last = match.end()

    if not targets:
        var_match = WS_OPTIONS_VAR_PATTERN.search(text)
        if var_match is None:
            raise RuntimeError(
                "Found websocketUrl/perMessageDeflate hints but target constructor pattern did not match"
            )
        body = var_match.group("body")
        patched_body, did_change = patch_body(body)
        targets = 1
        changed = 1 if did_change else 0
        patched = (
            text[: var_match.start()]
            + f"{var_match.group('prefix')}{{{patched_body}}}{var_match.group('suffix')}"
            + text[var_match.end() :]
        )
        if changed == 0:
            return data, {"status": "already_patched", "changed": 0, "targets": targets}
        return patched.encode("utf-8"), {"status": "patched", "changed": changed, "targets": targets}

    if changed == 0:
        return data, {"status": "already_patched", "changed": 0, "targets": targets}

    out.append(text[last:])
    return "".join(out).encode("utf-8"), {"status": "patched", "changed": changed, "targets": targets}


def sha256_hex(b):
    return hashlib.sha256(b).hexdigest()


def update_integrity(meta, data):
    meta["size"] = len(data)
    integ = meta.get("integrity")
    if isinstance(integ, dict) and integ.get("algorithm") == "SHA256":
        integ["hash"] = sha256_hex(data)
        bs = int(integ.get("blockSize") or 4194304)
        integ["blocks"] = [sha256_hex(data[i : i + bs]) for i in range(0, len(data), bs)]


def packed_entries(header):
    entries = []
    for p, m in iter_files(header):
        if "offset" in m and "size" in m and not m.get("unpacked"):
            entries.append((p, m, int(m["offset"])))
    entries.sort(key=lambda x: x[2])
    return entries


def serialize_header(header):
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    pad = (4 - (len(raw) % 4)) % 4
    header_size = 8 + len(raw) + pad
    prefix = struct.pack("<IIII", 4, header_size, len(raw) + 4 + pad, len(raw))
    return prefix + raw + (b"\0" * pad)


def repack(asar_path, header, payload_start, patched_by_path):
    entries = packed_entries(header)
    patched_paths = set(patched_by_path)
    for p, m, _ in entries:
        if p in patched_by_path:
            update_integrity(m, patched_by_path[p])
            patched_paths.remove(p)
    if patched_paths:
        raise RuntimeError(f"Target entries missing after iteration: {sorted(patched_paths)}")

    last = None
    for _ in range(10):
        off = 0
        for p, m, _old in entries:
            m["offset"] = str(off)
            off += len(patched_by_path[p]) if p in patched_by_path else int(m["size"])
        hb = serialize_header(header)
        if hb == last:
            break
        last = hb
    else:
        raise RuntimeError("ASAR header did not stabilize")

    tmp = asar_path.with_suffix(asar_path.suffix + ".tmp")
    with tmp.open("wb") as out:
        out.write(last)
        with asar_path.open("rb") as src:
            for p, m, old_off in entries:
                if p in patched_by_path:
                    out.write(patched_by_path[p])
                else:
                    src.seek(payload_start + old_off)
                    remaining = int(m["size"])
                    while remaining:
                        chunk = src.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise RuntimeError(f"Unexpected EOF copying {p}")
                        out.write(chunk)
                        remaining -= len(chunk)
    os.replace(tmp, asar_path)


def verify(asar: Path):
    header, payload_start = read_header(asar)
    marker_paths = []
    unpatched_targets = []
    for js_path, js_meta in iter_js_entries(header):
        data = extract(asar, payload_start, js_meta).decode("utf-8", "replace")
        if PATCH_MARKER in data:
            marker_paths.append(js_path)
        for match in WS_CONSTRUCTOR_PATTERN.finditer(data):
            body = match.group("body")
            if "headers:" in body and "maxPayload:" not in body:
                unpatched_targets.append(js_path)

    if not marker_paths:
        raise SystemExit("Verification failed: Patch M marker missing")
    if unpatched_targets:
        raise SystemExit(f"Verification failed: WS target(s) missing maxPayload: {unpatched_targets}")
    return marker_paths


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app-dir", required=True, help="Codex install dir (contains resources/app.asar)")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    app = Path(args.app_dir).resolve()
    asar = app / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    header, payload_start = read_header(asar)
    patched_by_path = {}
    inspected = []
    changed = 0

    for js_path, js_meta in iter_js_entries(header):
        original = extract(asar, payload_start, js_meta)
        if TARGET_HINT.encode("utf-8") not in original:
            continue
        patched, info = patch_js(original)
        if info["status"] in {"patched", "already_patched"}:
            inspected.append(js_path)
        if info["status"] == "patched":
            patched_by_path[js_path] = patched
            changed += info["changed"]

    if not inspected:
        raise SystemExit("Could not find the app-server websocket transport target in app.asar")

    if not patched_by_path:
        print(json.dumps({"status": "already_patched", "targets": inspected}, indent=2))
        verify(asar)
        return

    if not args.no_backup:
        bk = asar.with_name("app.asar.bak-before-ws-max-payload")
        if not bk.exists():
            shutil.copy2(asar, bk)

    repack(asar, header, payload_start, patched_by_path)
    marker_paths = verify(asar)

    print(
        json.dumps(
            {
                "status": "patched",
                "targets": inspected,
                "marker_paths": marker_paths,
                "changed": changed,
                "asar": str(asar),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
