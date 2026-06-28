#!/usr/bin/env python3
"""Patch O - keep local catalog models visible when Statsig model allowlist is on.

Recent Desktop builds filter `model/list` through a Statsig payload. When
`use_hidden_models` is true, the renderer only shows models named in
`available_models`; this can hide local catalog entries such as
`gpt-5.3-codex-spark` and custom proxy model ids even though the sidecar
returns them with `hidden=false`.

This patch changes the model-list filter so the allowlist still unlocks hidden
models, but non-hidden models returned by the sidecar remain visible.
"""
import argparse
import json
import os
import shutil
import struct
from pathlib import Path


OLD = "if(s?t.has(n.model):!n.hidden)"
NEW = "if(s?(t.has(n.model)||!n.hidden):!n.hidden)"

REPLACEMENTS = (
    (OLD, NEW),
    (
        "if(u?n.has(r.model):!r.hidden)",
        "if(u?(n.has(r.model)||!r.hidden):!r.hidden)",
    ),
)


def read_header(asar_path: Path):
    with asar_path.open("rb") as f:
        first, header_size, _pickle_payload_size, json_size = struct.unpack("<IIII", f.read(16))
        if first != 4 or json_size <= 0:
            raise RuntimeError(f"Unexpected ASAR prefix: {(first, header_size, json_size)}")
        header = json.loads(f.read(json_size).decode("utf-8"))
    return header, 8 + header_size


def walk(node, parts=()):
    for name, meta in node.get("files", {}).items():
        path = parts + (name,)
        if "files" in meta:
            yield from walk(meta, path)
        else:
            yield "/".join(path), meta


def extract(asar_path: Path, payload_start: int, meta: dict) -> bytes:
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))


def update_integrity(meta: dict, data: bytes):
    meta["size"] = len(data)
    integrity = meta.get("integrity")
    if not integrity:
        return
    import hashlib

    block_size = int(integrity.get("blockSize") or 4194304)
    blocks = [
        hashlib.sha256(data[i : i + block_size]).digest().hex()
        for i in range(0, len(data), block_size)
    ]
    integrity["blocks"] = blocks
    integrity["hash"] = hashlib.sha256(data).digest().hex()


def packed_entries(header: dict):
    entries = []
    for path, meta in walk(header):
        if "offset" in meta and "size" in meta and not meta.get("unpacked"):
            entries.append((path, meta, int(meta["offset"])))
    entries.sort(key=lambda item: item[2])
    return entries


def make_header(header: dict):
    raw = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    pad = (4 - (len(raw) % 4)) % 4
    header_size = 8 + len(raw) + pad
    prefix = struct.pack("<IIII", 4, header_size, len(raw) + 4 + pad, len(raw))
    return prefix + raw + (b"\0" * pad)


def repack(asar_path: Path, header: dict, payload_start: int, patched_by_path: dict[str, bytes]):
    entries = packed_entries(header)
    entry_map = {path: (meta, old_offset) for path, meta, old_offset in entries}
    for path, data in patched_by_path.items():
        update_integrity(entry_map[path][0], data)

    previous = None
    header_blob = b""
    for _ in range(8):
        offset = 0
        for path, meta, _old_offset in entries:
            meta["offset"] = str(offset)
            offset += len(patched_by_path[path]) if path in patched_by_path else int(meta["size"])
        header_blob = make_header(header)
        if header_blob == previous:
            break
        previous = header_blob
    else:
        raise RuntimeError("ASAR header did not stabilize")

    tmp = asar_path.with_suffix(asar_path.suffix + ".tmp")
    with tmp.open("wb") as dst:
        dst.write(header_blob)
        with asar_path.open("rb") as src:
            for path, meta, old_offset in entries:
                if path in patched_by_path:
                    dst.write(patched_by_path[path])
                else:
                    src.seek(payload_start + old_offset)
                    remaining = int(meta["size"])
                    while remaining:
                        chunk = src.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise RuntimeError(f"Unexpected EOF while copying {path}")
                        dst.write(chunk)
                        remaining -= len(chunk)
    os.replace(tmp, asar_path)


def find_targets(asar: Path):
    header, payload_start = read_header(asar)
    targets = []
    for path, meta in walk(header):
        if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
            continue
        text = extract(asar, payload_start, meta).decode("utf-8", "replace")
        if "availableModels" in text and "useHiddenModels" in text and "model-list-filter" in path:
            targets.append((path, meta, text))
        elif OLD in text or NEW in text:
            targets.append((path, meta, text))
    return header, payload_start, targets


def verify(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    patched_needles = [new for _old, new in REPLACEMENTS]
    unpatched_needles = [old for old, _new in REPLACEMENTS]
    marker_paths = [
        path
        for path, _meta, text in targets
        if any(needle in text for needle in patched_needles)
    ]
    unpatched = [
        path
        for path, _meta, text in targets
        if any(needle in text for needle in unpatched_needles)
    ]
    if not marker_paths:
        raise SystemExit("Verification failed: Patch O marker not found")
    if unpatched:
        raise SystemExit(f"Verification failed: unpatched model filters remain: {unpatched}")
    return sorted(set(marker_paths))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-dir", required=True, help="Codex install dir (contains resources/app.asar)")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    asar = app_dir / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    header, payload_start, targets = find_targets(asar)
    patched_by_path = {}
    scanned = []
    for path, meta, text in targets:
        scanned.append(path)
        if any(new in text for _old, new in REPLACEMENTS):
            continue
        patched_text = text
        for old, new in REPLACEMENTS:
            if old in patched_text:
                patched_text = patched_text.replace(old, new, 1)
                break
        if patched_text == text:
            continue
        patched_by_path[path] = patched_text.encode("utf-8")

    if not targets:
        raise SystemExit("Could not find renderer model-list filter target in app.asar")

    if patched_by_path:
        if not args.no_backup:
            backup = asar.with_name("app.asar.bak-before-model-availability-filter")
            if not backup.exists():
                shutil.copy2(asar, backup)
        repack(asar, header, payload_start, patched_by_path)

    marker_paths = verify(asar)
    print(
        json.dumps(
            {
                "status": "patched" if patched_by_path else "already_patched",
                "asar": str(asar),
                "scanned": sorted(set(scanned)),
                "patched": sorted(patched_by_path),
                "marker_paths": marker_paths,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
