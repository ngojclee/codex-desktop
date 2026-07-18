#!/usr/bin/env python3
"""Patch A — widen the recent-thread discovery window.

Problem: official Codex Desktop builds historically capped the sidebar at a
small recent-thread window (`limit:50`). Codex Desktop 26.608.x introduced a
new native expanded-history path (`getHistoryLimit` + `useStateDbOnly`) while
keeping the old paged load-more path. Codex Desktop 26.715.x moved the default
history limits into a runtime-settings helper and added native cursor
pagination. A patcher that only knows the older minified shapes fails even
though the feature can still be widened safely.

Fix: bump the known old initial/load-more limits, and on newer builds bump the
native `getHistoryLimit` fallback from 50 to the requested limit. On 26.715+
replace the runtime helper's local/remote defaults (500/50) with the requested
limit while preserving the catalog-consume value of 0. Verification accepts
the old widened initial-refresh shape, either native fallback shape, or the new
runtime-settings marker.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
from pathlib import Path


PATCH_PATTERNS = (
    # Codex Desktop 26.506.x renamed pageCount→recentConversationPageCount and
    # nextCursor→nextRecentConversationCursor. Search default is already 1000
    # in this build, so search patterns are no longer needed here.
    ("limit:50*this.recentConversationPageCount,cursor:null", "limit:{limit}*this.recentConversationPageCount,cursor:null"),
    ("limit:50,cursor:this.nextRecentConversationCursor", "limit:{limit},cursor:this.nextRecentConversationCursor"),
    # Codex Desktop 26.608.x has a native expanded-history path. The default is
    # still 50, so bumping it to 1000 makes the new native path do the work that
    # Patch C's old manual paginate loop used to do.
    (
        "this.params.getHistoryLimit?.()??50,n=t>50,r=n?t:50*this.recentConversationPageCount",
        "this.params.getHistoryLimit?.()??{limit},n=t>50,r=n?t:50*this.recentConversationPageCount",
    ),
    # Codex Desktop 26.611.x keeps the native expanded-history path, but the
    # initial page size is now held in a local `a` rather than the old
    # `recentConversationPageCount` expression.
    (
        "this.params.getHistoryLimit?.()??50,i=(t===`expanded`||n)&&r>50,a=i?r:50",
        "this.params.getHistoryLimit?.()??{limit},i=(t===`expanded`||n)&&r>50,a=i?r:50",
    ),
)

RUNTIME_HISTORY_MARKER = "/*A:history-limit*/"
RUNTIME_HISTORY_PATTERN = re.compile(
    r"function (?P<fn>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\((?P<local>[A-Za-z_$][A-Za-z0-9_$]*),(?P<catalog>[A-Za-z_$][A-Za-z0-9_$]*)\)"
    r"\{return (?P=local)&&(?P=catalog)\?0:(?P=local)\?500:50\}"
)


def read_header(asar_path: Path):
    with asar_path.open("rb") as f:
        prefix = f.read(16)
        if len(prefix) != 16:
            raise RuntimeError("ASAR header is too short")
        first, header_size, pickle_payload_size, json_size = struct.unpack("<IIII", prefix)
        if first != 4 or json_size <= 0:
            raise RuntimeError(f"Unexpected ASAR header prefix: {(first, header_size, pickle_payload_size, json_size)}")
        raw_json = f.read(json_size)
        header = json.loads(raw_json.decode("utf-8"))
        payload_start = 8 + header_size
    return header, payload_start


def iter_files(node, parts=()):
    for name, meta in node.get("files", {}).items():
        child_parts = parts + (name,)
        if "files" in meta:
            yield from iter_files(meta, child_parts)
        else:
            yield "/".join(child_parts), meta


def find_target(header, asar_path: Path | None = None, payload_start: int | None = None):
    candidates = []
    for path, meta in iter_files(header):
        if (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "app-server-manager-signals-" in path
            and "offset" in meta
        ):
            candidates.append((path, meta))
    if not candidates and asar_path is not None and payload_start is not None:
        for path, meta in iter_files(header):
            if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
                continue
            text = extract_file(asar_path, payload_start, meta).decode("utf-8", "replace")
            if (
                "listRecentThreads" in text
                and "getHistoryLimit" in text
                and "recentConversationSortKey" in text
            ):
                candidates.append((path, meta))
    if not candidates:
        raise RuntimeError("Could not find recent-conversation renderer chunk in app.asar")
    if len(candidates) > 1:
        raise RuntimeError(f"Expected one target chunk, found {len(candidates)}: {[p for p, _ in candidates]}")
    return candidates[0]


def extract_file(asar_path: Path, payload_start: int, meta: dict) -> bytes:
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))


def patch_js(data: bytes, limit: int):
    text = data.decode("utf-8")
    replacements = {}
    already = {}
    for old, new_template in PATCH_PATTERNS:
        new = new_template.replace("{limit}", str(limit))
        old_count = text.count(old)
        new_count = text.count(new)
        replacements[old] = old_count
        already[new] = new_count
        if old_count:
            text = text.replace(old, new)

    runtime_matches = list(RUNTIME_HISTORY_PATTERN.finditer(text))
    if len(runtime_matches) > 1:
        raise RuntimeError(
            f"Expected at most one 26.715 runtime history helper, found {len(runtime_matches)}"
        )
    replacements["runtime_history_defaults_26_715"] = len(runtime_matches)
    runtime_marker = f"{RUNTIME_HISTORY_MARKER}{limit}"
    already["runtime_history_limit_26_715"] = text.count(runtime_marker)
    if runtime_matches:
        match = runtime_matches[0]
        replacement = (
            f"function {match.group('fn')}({match.group('local')},{match.group('catalog')})"
            f"{{return {match.group('local')}&&{match.group('catalog')}?0:"
            f"{runtime_marker}}}"
        )
        text = text[: match.start()] + replacement + text[match.end() :]

    if not any(replacements.values()):
        if any(already.values()):
            return data, {"already_patched": True, "replacements": replacements, "already": already}
        raise RuntimeError(f"No known recent-window patterns found; already={already}")
    return text.encode("utf-8"), {"already_patched": False, "replacements": replacements, "already": already}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def update_integrity(meta: dict, data: bytes):
    meta["size"] = len(data)
    integrity = meta.get("integrity")
    if isinstance(integrity, dict) and integrity.get("algorithm") == "SHA256":
        digest = sha256_hex(data)
        integrity["hash"] = digest
        block_size = int(integrity.get("blockSize") or 4194304)
        integrity["blocks"] = [sha256_hex(data[i : i + block_size]) for i in range(0, len(data), block_size)]


def packed_entries(header):
    entries = []
    for path, meta in iter_files(header):
        if "offset" in meta and "size" in meta and not meta.get("unpacked"):
            entries.append((path, meta, int(meta["offset"])))
    entries.sort(key=lambda item: item[2])
    return entries


def serialize_header(header):
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padding = (4 - (len(raw) % 4)) % 4
    header_size = 8 + len(raw) + padding
    # Pickle payload_size = 4-byte json-length-header + json bytes + padding.
    # Missing the padding term silently corrupts asar headers when json % 4 != 0
    # (Codex Desktop 26.506+ chunk hits a 2-byte padding case).
    prefix = struct.pack("<IIII", 4, header_size, len(raw) + 4 + padding, len(raw))
    return prefix + raw + (b"\0" * padding)


def repack(asar_path: Path, header: dict, payload_start: int, target_path: str, patched_data: bytes):
    entries = packed_entries(header)
    data_by_path = {target_path: patched_data}
    target_meta = None
    for path, meta, _old_offset in entries:
        if path == target_path:
            target_meta = meta
            update_integrity(meta, patched_data)
            break
    if target_meta is None:
        raise RuntimeError(f"Target entry disappeared from header: {target_path}")

    # Offset strings live in the header, so recalculate until the serialized
    # header is stable. Offsets are relative to the payload start.
    last_header_bytes = None
    for _ in range(10):
        offset = 0
        for path, meta, _old_offset in entries:
            meta["offset"] = str(offset)
            offset += len(data_by_path[path]) if path in data_by_path else int(meta["size"])
        header_bytes = serialize_header(header)
        if header_bytes == last_header_bytes:
            break
        last_header_bytes = header_bytes
    else:
        raise RuntimeError("ASAR header did not stabilize while recalculating offsets")

    tmp_path = asar_path.with_suffix(asar_path.suffix + ".tmp")
    with tmp_path.open("wb") as out:
        out.write(last_header_bytes)
        with asar_path.open("rb") as src:
            for path, meta, old_offset in entries:
                if path in data_by_path:
                    out.write(data_by_path[path])
                else:
                    src.seek(payload_start + old_offset)
                    remaining = int(meta["size"])
                    while remaining:
                        chunk = src.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise RuntimeError(f"Unexpected EOF while copying {path}")
                        out.write(chunk)
                        remaining -= len(chunk)
    os.replace(tmp_path, asar_path)


def main():
    parser = argparse.ArgumentParser(description="Patch Codex Desktop ASAR recent-thread window in a copied app folder.")
    parser.add_argument("--app-dir", required=True, help="Path to copied Codex app directory containing resources/app.asar")
    parser.add_argument("--limit", type=int, default=1000, help="Recent conversation page size to use")
    parser.add_argument("--no-backup", action="store_true", help="Do not create app.asar backup before patching")
    args = parser.parse_args()

    if args.limit < 51 or args.limit > 5000:
        raise SystemExit("--limit must be between 51 and 5000")

    app_dir = Path(args.app_dir).resolve()
    asar_path = app_dir / "resources" / "app.asar"
    if not asar_path.exists():
        raise SystemExit(f"Missing ASAR: {asar_path}")

    header, payload_start = read_header(asar_path)
    target_path, target_meta = find_target(header, asar_path, payload_start)
    original = extract_file(asar_path, payload_start, target_meta)
    patched, info = patch_js(original, args.limit)

    if info["already_patched"]:
        print(json.dumps({"status": "already_patched", "target": target_path, **info}, indent=2))
        return

    if patched == original:
        raise SystemExit("Patch produced identical output; refusing to write")

    if not args.no_backup:
        backup = asar_path.with_name(f"app.asar.bak-before-recent-window-{args.limit}")
        if not backup.exists():
            shutil.copy2(asar_path, backup)

    repack(asar_path, header, payload_start, target_path, patched)

    verify_header, verify_payload_start = read_header(asar_path)
    verify_target_path, verify_target_meta = find_target(verify_header, asar_path, verify_payload_start)
    verify_data = extract_file(asar_path, verify_payload_start, verify_target_meta)
    verify_text = verify_data.decode("utf-8")
    expected = {
        "refresh_limit": verify_text.count(f"limit:{args.limit}*this.recentConversationPageCount,cursor:null"),
        "load_more_limit": verify_text.count(f"limit:{args.limit},cursor:this.nextRecentConversationCursor"),
        "native_history_limit_26_608": verify_text.count(
            f"this.params.getHistoryLimit?.()??{args.limit},n=t>50,r=n?t:50*this.recentConversationPageCount"
        ),
        "native_history_limit_26_611": verify_text.count(
            f"this.params.getHistoryLimit?.()??{args.limit},i=(t===`expanded`||n)&&r>50,a=i?r:50"
        ),
        "runtime_history_limit_26_715": verify_text.count(
            f"{RUNTIME_HISTORY_MARKER}{args.limit}"
        ),
    }
    native_history_limit = (
        expected["native_history_limit_26_608"]
        + expected["native_history_limit_26_611"]
        + expected["runtime_history_limit_26_715"]
    )
    if native_history_limit:
        pass
    elif expected["load_more_limit"] != 1 or expected["refresh_limit"] != 1:
        raise SystemExit(f"Patch verification failed: {expected}")

    print(
        json.dumps(
            {
                "status": "patched",
                "app_dir": str(app_dir),
                "target": verify_target_path,
                "limit": args.limit,
                "old_size": len(original),
                "new_size": len(patched),
                "replacements": info["replacements"],
                "verified": expected,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
