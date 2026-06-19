#!/usr/bin/env python3
"""Patch C v3 — Always-paginate (remove v2 guard).

History
=======
- v1: unconditional paginate loop on every refetchThreadList. Caused symptom
      where streaming thread B got partially overwritten — we *thought* it
      was the paginate loop's fault.
- v2: guarded paginate (only first call per session). Fixed the partial-
      stuck symptom but introduced a NEW symptom: subsequent refetches
      return only the first page (100 threads), and the renderer's
      `applyRecentConversations` REPLACES the cache wholesale -> sidebar
      shrinks from 2000 to ~100 when any external trigger (e.g.
      `codex resume -all` from terminal causing A's session to bump to top)
      fires a refetchThreadList.
- v3: always paginate (back to v1 behavior). Rationale: Patch D now
      correctly clears the renderer's conversations Map on sidecar
      reconnect, which was the *real* fix for the partial-stuck case.
      v1's paginate loop was not the actual culprit — it was Patch D's
      absence. With D in place, v1-style unconditional paginate is safe
      and gives us a stable >100-thread sidebar.

What it does
============
Rewrites the refetchThreadList method in
  webview/assets/app-server-manager-signals-*.js
to call listRecentThreads({limit:100, cursor}) in a loop, paging until
nextCursor is exhausted or a safety cap of 2000 threads is reached. No
guard. Idempotent via marker `__capV3=2000`.

Codex Desktop 26.608.x added a native expanded-history path:
`getHistoryLimit` + `useStateDbOnly` + `threadSummaries`. When Patch A has
already bumped that native fallback to 1000, this patch records the same v3
marker and leaves the native path intact instead of inserting the older manual
paginate loop.

The script automatically rolls back from v1 or v2 state (via the existing
`app.asar.bak-before-autopaginate` snapshot) before applying v3.
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path


V1_SEARCH = (
    "let t=await this.listRecentThreads({limit:100,cursor:null});"
    "{let __c=t.nextCursor,__cap=2000;"
    "while(__c&&t.data.length<__cap){"
    "let __p=await this.listRecentThreads({limit:100,cursor:__c});"
    "if(!__p||!__p.data||__p.data.length===0)break;"
    "t.data.push(...__p.data);__c=__p.nextCursor"
    "}t.nextCursor=__c}"
    "this.fetchedRecentConversations=!0,this.nextRecentConversationCursor=t.nextCursor;"
)

V2_GUARD = "if(!this.fetchedRecentConversations)"
V3_MARKER = "__capV3=2000"
NATIVE_HISTORY_PATCHED_PATTERNS = (
    "this.params.getHistoryLimit?.()??1000,n=t>50,r=n?t:50*this.recentConversationPageCount",
    "this.params.getHistoryLimit?.()??1000,i=(t===`expanded`||n)&&r>50,a=i?r:50",
)
NATIVE_HISTORY_MARKER_ANCHOR = "this.params.onHistoryLoaded?.("

UNPATCHED_SEARCH = (
    "let t=await this.listRecentThreads({limit:1000*this.recentConversationPageCount,cursor:null});"
    "this.fetchedRecentConversations=!0,this.nextRecentConversationCursor=t.nextCursor;"
)

# v3 replacement: always paginate, no guard. New marker `__capV3` distinguishes
# from v1's `__cap` so the verify step can tell them apart.
V3_REPLACE = (
    "let t=await this.listRecentThreads({limit:100,cursor:null});"
    "{let __c=t.nextCursor,__capV3=2000;"
    "while(__c&&t.data.length<__capV3){"
    "let __p=await this.listRecentThreads({limit:100,cursor:__c});"
    "if(!__p||!__p.data||__p.data.length===0)break;"
    "t.data.push(...__p.data);__c=__p.nextCursor"
    "}t.nextCursor=__c}"
    "this.fetchedRecentConversations=!0,this.nextRecentConversationCursor=t.nextCursor;"
)


def read_header(asar_path: Path):
    with asar_path.open("rb") as f:
        prefix = f.read(16)
        if len(prefix) != 16:
            raise RuntimeError("ASAR header too short")
        first, header_size, _, json_size = struct.unpack("<IIII", prefix)
        if first != 4 or json_size <= 0:
            raise RuntimeError(f"Unexpected ASAR header prefix")
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


def find_target(header, asar_path: Path | None = None, payload_start: int | None = None):
    for path, meta in iter_files(header):
        if (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "app-server-manager-signals-" in path
            and "offset" in meta
        ):
            return path, meta
    if asar_path is not None and payload_start is not None:
        candidates = []
        for path, meta in iter_files(header):
            if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
                continue
            text = extract(asar_path, payload_start, meta).decode("utf-8", "replace")
            if (
                V3_MARKER in text
                or UNPATCHED_SEARCH in text
                or (any(pattern in text for pattern in NATIVE_HISTORY_PATCHED_PATTERNS) and "useStateDbOnly:n" in text)
            ):
                candidates.append((path, meta))
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise RuntimeError(f"Multiple recent-conversation chunks found: {[p for p, _ in candidates]}")
    raise RuntimeError("Could not find recent-conversation renderer chunk")


def extract(asar_path: Path, payload_start: int, meta: dict) -> bytes:
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))


def detect_state(text: str) -> str:
    if V3_MARKER in text:
        return "v3"
    if V2_GUARD in text and "__cap=2000" in text:
        return "v2"
    if V1_SEARCH in text:
        return "v1"
    if UNPATCHED_SEARCH in text:
        return "unpatched"
    if any(pattern in text for pattern in NATIVE_HISTORY_PATCHED_PATTERNS) and "useStateDbOnly:n" in text:
        return "native_expanded_history"
    return "unknown"


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def update_integrity(meta: dict, data: bytes):
    meta["size"] = len(data)
    integrity = meta.get("integrity")
    if isinstance(integrity, dict) and integrity.get("algorithm") == "SHA256":
        integrity["hash"] = sha256_hex(data)
        block = int(integrity.get("blockSize") or 4194304)
        integrity["blocks"] = [sha256_hex(data[i : i + block]) for i in range(0, len(data), block)]


def packed_entries(header):
    entries = []
    for path, meta in iter_files(header):
        if "offset" in meta and "size" in meta and not meta.get("unpacked"):
            entries.append((path, meta, int(meta["offset"])))
    entries.sort(key=lambda e: e[2])
    return entries


def serialize_header(header):
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    pad = (4 - (len(raw) % 4)) % 4
    header_size = 8 + len(raw) + pad
    return struct.pack("<IIII", 4, header_size, len(raw) + 4 + pad, len(raw)) + raw + (b"\0" * pad)


def repack(asar_path: Path, header: dict, payload_start: int, target_path: str, patched: bytes):
    entries = packed_entries(header)
    by_path = {target_path: patched}
    target_meta = next((m for p, m, _ in entries if p == target_path), None)
    update_integrity(target_meta, patched)
    last = None
    for _ in range(10):
        offset = 0
        for path, meta, _old in entries:
            meta["offset"] = str(offset)
            offset += len(by_path[path]) if path in by_path else int(meta["size"])
        bs = serialize_header(header)
        if bs == last:
            break
        last = bs
    else:
        raise RuntimeError("ASAR header did not stabilize")
    tmp = asar_path.with_suffix(asar_path.suffix + ".tmp")
    with tmp.open("wb") as out:
        out.write(last)
        with asar_path.open("rb") as src:
            for path, meta, old_offset in entries:
                if path in by_path:
                    out.write(by_path[path])
                else:
                    src.seek(payload_start + old_offset)
                    remaining = int(meta["size"])
                    while remaining:
                        chunk = src.read(min(1024 * 1024, remaining))
                        out.write(chunk)
                        remaining -= len(chunk)
    os.replace(tmp, asar_path)


def apply_v3(app_dir: Path) -> dict:
    asar_path = app_dir / "resources" / "app.asar"
    if not asar_path.exists():
        return {"status": "missing", "asar": str(asar_path)}

    header, payload_start = read_header(asar_path)
    target_path, target_meta = find_target(header, asar_path, payload_start)
    original = extract(asar_path, payload_start, target_meta).decode("utf-8", "replace")
    state = detect_state(original)

    if state == "v3":
        return {"status": "already_v3", "asar": str(asar_path), "target": target_path}

    # For v1 or v2, roll back to the pre-autopaginate snapshot which is the
    # "Patch A only" state. The bak is shared with v1/v2 patchers.
    if state in ("v1", "v2"):
        bak = asar_path.with_name("app.asar.bak-before-autopaginate")
        if not bak.exists():
            return {"status": "error", "reason": f"{state} present but bak missing", "asar": str(asar_path)}
        pre_v3_bak = asar_path.with_name(f"app.asar.bak-before-v3-from-{state}")
        if not pre_v3_bak.exists():
            shutil.copy2(asar_path, pre_v3_bak)
        shutil.copy2(bak, asar_path)
        header, payload_start = read_header(asar_path)
        target_path, target_meta = find_target(header, asar_path, payload_start)
        original = extract(asar_path, payload_start, target_meta).decode("utf-8", "replace")
        state = detect_state(original)

    if state != "unpatched":
        if state == "native_expanded_history":
            if NATIVE_HISTORY_MARKER_ANCHOR not in original:
                return {"status": "error", "reason": "native marker anchor missing", "asar": str(asar_path)}

            pre_v3_bak = asar_path.with_name("app.asar.bak-before-v3")
            if not pre_v3_bak.exists():
                shutil.copy2(asar_path, pre_v3_bak)

            patched_text = original.replace(
                NATIVE_HISTORY_MARKER_ANCHOR,
                f"/*{V3_MARKER}*/{NATIVE_HISTORY_MARKER_ANCHOR}",
                1,
            )
            repack(asar_path, header, payload_start, target_path, patched_text.encode("utf-8"))

            h2, ps2 = read_header(asar_path)
            tp2, tm2 = find_target(h2, asar_path, ps2)
            js2 = extract(asar_path, ps2, tm2).decode("utf-8", "replace")
            if detect_state(js2) != "v3":
                return {"status": "error", "reason": "native marker verify failed"}

            return {
                "status": "native_expanded_history_marked_v3",
                "asar": str(asar_path),
                "target": target_path,
                "old_size": len(original),
                "new_size": len(patched_text),
                "delta_bytes": len(patched_text) - len(original),
            }
        return {"status": "error", "reason": f"unexpected state after rollback: {state}", "asar": str(asar_path)}

    if UNPATCHED_SEARCH not in original:
        return {"status": "error", "reason": "unpatched marker missing", "asar": str(asar_path)}

    patched_text = original.replace(UNPATCHED_SEARCH, V3_REPLACE, 1)
    if V3_MARKER not in patched_text:
        return {"status": "error", "reason": "v3 marker missing after replace"}

    pre_v3_bak = asar_path.with_name("app.asar.bak-before-v3")
    if not pre_v3_bak.exists():
        shutil.copy2(asar_path, pre_v3_bak)

    repack(asar_path, header, payload_start, target_path, patched_text.encode("utf-8"))

    h2, ps2 = read_header(asar_path)
    tp2, tm2 = find_target(h2, asar_path, ps2)
    js2 = extract(asar_path, ps2, tm2).decode("utf-8", "replace")
    if detect_state(js2) != "v3":
        return {"status": "error", "reason": "verify failed"}

    return {
        "status": "patched_v3",
        "asar": str(asar_path),
        "target": target_path,
        "old_size": len(original),
        "new_size": len(patched_text),
        "delta_bytes": len(patched_text) - len(original),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--app-dir", action="append")
    p.add_argument("--auto", action="store_true")
    args = p.parse_args()
    targets = []
    if args.auto:
        base = Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "CodexDesktopPatched"
        for d in sorted(base.glob("OpenAI.Codex_*_x64*/app")):
            targets.append(d)
    if args.app_dir:
        targets.extend(Path(d).resolve() for d in args.app_dir)
    if not targets:
        raise SystemExit("No targets. Pass --auto or --app-dir.")
    results = []
    for d in targets:
        try:
            results.append(apply_v3(d))
        except Exception as e:
            results.append({"status": "exception", "app_dir": str(d), "error": str(e)})
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
