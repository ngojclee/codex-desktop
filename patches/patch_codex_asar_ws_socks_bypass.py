#!/usr/bin/env python3
"""Patch G — bypass hardcoded SOCKS5 proxy in the websocket app-server transport.

The Codex Desktop WS transport class hardcodes a SOCKS5 proxy at
`socks5h://127.0.0.1:1080` for ALL websocket connections. When you set
`CODEX_APP_SERVER_WS_URL=ws://127.0.0.1:PORT` to share a local sidecar between
Desktop and CLI, the WS client tries to dial through the non-existent SOCKS
proxy and fails. The renderer interprets the failure as "no auth" and shows a
login page even though the user is on apikey/cliproxy mode.

This patch removes the `agent: new <minified>.SocksProxyAgent(...)` option from
the WS transport so the connection goes direct. Auth headers from `th()` are
already empty `{}`; no other tweak is needed.

Idempotent: re-running on an already-patched asar is a no-op. Verified by the
absence of the `socks5h://127.0.0.1:1080` string in the workspace bundle.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
from pathlib import Path

# Minifier-emitted identifier prefix (e.g. `Qm`) may differ between builds, so
# match flexibly. The leading comma is part of the match so removing it does
# not leave a stray `,,` in the option object.
SOCKS_PATTERN = re.compile(
    r",agent:new \w+\.SocksProxyAgent\(`socks5h://127\.0\.0\.1:1080`\)"
)


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


def find_target(header):
    cands = []
    for p, m in iter_files(header):
        if (
            p.startswith(".vite/build/workspace-root-drop-handler-")
            and p.endswith(".js")
            and "offset" in m
        ):
            cands.append((p, m))
    if not cands:
        raise RuntimeError("workspace-root-drop-handler bundle not found in app.asar")
    if len(cands) > 1:
        raise RuntimeError(f"Multiple workspace bundles: {[p for p, _ in cands]}")
    return cands[0]


def extract(asar_path, payload_start, meta):
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))


def patch_js(data: bytes):
    text = data.decode("utf-8")
    matches = list(SOCKS_PATTERN.finditer(text))
    if not matches:
        if "socks5h://127.0.0.1:1080" not in text:
            return data, {"status": "already_patched", "replaced": 0}
        raise RuntimeError(
            "Found 'socks5h://127.0.0.1:1080' but pattern did not match — bundle layout changed"
        )
    patched = SOCKS_PATTERN.sub("", text)
    return patched.encode("utf-8"), {"status": "patched", "replaced": len(matches)}


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


def repack(asar_path, header, payload_start, target_path, patched_data):
    entries = packed_entries(header)
    data_by_path = {target_path: patched_data}
    found = False
    for p, m, _ in entries:
        if p == target_path:
            update_integrity(m, patched_data)
            found = True
            break
    if not found:
        raise RuntimeError(f"Target entry missing after iteration: {target_path}")

    last = None
    for _ in range(10):
        off = 0
        for p, m, _old in entries:
            m["offset"] = str(off)
            off += len(data_by_path[p]) if p in data_by_path else int(m["size"])
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
                if p in data_by_path:
                    out.write(data_by_path[p])
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
    target_path, target_meta = find_target(header)
    original = extract(asar, payload_start, target_meta)
    patched, info = patch_js(original)

    if info["status"] == "already_patched":
        print(json.dumps({"status": "already_patched", "target": target_path}, indent=2))
        return

    if not args.no_backup:
        bk = asar.with_name("app.asar.bak-before-ws-socks-bypass")
        if not bk.exists():
            shutil.copy2(asar, bk)

    repack(asar, header, payload_start, target_path, patched)

    vh, vps = read_header(asar)
    vt, vm = find_target(vh)
    vd = extract(asar, vps, vm).decode("utf-8")
    if "socks5h://127.0.0.1:1080" in vd:
        raise SystemExit("Verification failed: SOCKS5 hardcode still present after repack")

    print(
        json.dumps(
            {
                "status": "patched",
                "target": target_path,
                "replaced": info["replaced"],
                "asar": str(asar),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
