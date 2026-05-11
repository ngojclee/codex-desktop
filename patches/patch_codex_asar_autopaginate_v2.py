#!/usr/bin/env python3
"""Patch C v2 — Guarded auto-pagination.

Rationale
=========
Patch C v1 rewrote refetchThreadList() into an unconditional 20-page loop
that ran on every invocation. Symptom: when a thread received realtime
events (e.g. when Desktop session A delegated to session B via CLI), an
inflight refetchThreadList would load 2000 stale per-thread snapshots and
call applyConversationState() for each, overwriting the streaming state on
thread B mid-update. Visible as: B jumps to top, shows a few lines, then
stops updating even though the underlying CLI process is still writing
JSONL.

V2 fix
======
Keep the loop, but guard it with this.fetchedRecentConversations. That
flag is set to true at the end of the same method, so:
  * 1st invocation per session: loop runs -> sidebar shows >100 threads
  * subsequent invocations: loop skipped -> behaves like 1-page original,
    no state-replace storm on streaming threads.

That preserves the sidebar-visibility benefit (>100 threads) without the
realtime-event regression.

Idempotency: presence of `if(!this.fetchedRecentConversations)` near the
refetch site marks v2. Presence of `__cap=2000` without the guard marks
v1 (we rollback first). Presence of `limit:1000*this.recentConversationPageCount`
marks un-patched.

Operation
---------
This script auto-rolls back v1 (via `app.asar.bak-before-autopaginate`)
then applies v2. Idempotent. Backs up the pre-v2 state to
`app.asar.bak-before-autopaginate-v2` if not already present.
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

# v2 marker is the guard condition — uniquely identifies v2.
V2_GUARD = "if(!this.fetchedRecentConversations)"

UNPATCHED_SEARCH = (
    "let t=await this.listRecentThreads({limit:1000*this.recentConversationPageCount,cursor:null});"
    "this.fetchedRecentConversations=!0,this.nextRecentConversationCursor=t.nextCursor;"
)

V2_REPLACE = (
    "let t=await this.listRecentThreads({limit:100,cursor:null});"
    "if(!this.fetchedRecentConversations){"
    "let __c=t.nextCursor,__cap=2000;"
    "while(__c&&t.data.length<__cap){"
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
            raise RuntimeError(f"Unexpected ASAR header prefix: {(first, header_size, json_size)}")
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
    candidates = []
    for path, meta in iter_files(header):
        if (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "app-server-manager-signals-" in path
            and "offset" in meta
        ):
            candidates.append((path, meta))
    if not candidates:
        raise RuntimeError("Could not find app-server-manager-signals-*.js in app.asar")
    if len(candidates) > 1:
        raise RuntimeError(f"Expected one target chunk, found {len(candidates)}: {[p for p,_ in candidates]}")
    return candidates[0]


def extract(asar_path: Path, payload_start: int, meta: dict) -> bytes:
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))


def detect_state(text: str) -> str:
    if V2_GUARD in text and "__cap=2000" in text:
        return "v2"
    if V1_SEARCH in text:
        return "v1"
    if UNPATCHED_SEARCH in text:
        return "unpatched"
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
    if target_meta is None:
        raise RuntimeError(f"Target {target_path} disappeared from header")
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
                        if not chunk:
                            raise RuntimeError(f"EOF copying {path}")
                        out.write(chunk)
                        remaining -= len(chunk)
    os.replace(tmp, asar_path)


def apply_v2(app_dir: Path) -> dict:
    asar_path = app_dir / "resources" / "app.asar"
    if not asar_path.exists():
        return {"status": "missing", "asar": str(asar_path)}

    header, payload_start = read_header(asar_path)
    target_path, target_meta = find_target(header)
    original = extract(asar_path, payload_start, target_meta).decode("utf-8", "replace")
    state = detect_state(original)

    if state == "v2":
        return {"status": "already_v2", "asar": str(asar_path), "target": target_path}

    # If v1 is currently applied, rollback to .bak-before-autopaginate so we
    # have a clean canvas. The bak is the state with Patch A (limit-bump)
    # applied but no auto-paginate — the unpatched-from-v1's perspective.
    if state == "v1":
        bak = asar_path.with_name("app.asar.bak-before-autopaginate")
        if not bak.exists():
            return {"status": "error", "reason": "v1 present but bak missing", "asar": str(asar_path)}
        # Save pre-v2 snapshot
        pre_v2_bak = asar_path.with_name("app.asar.bak-before-v2-from-v1")
        if not pre_v2_bak.exists():
            shutil.copy2(asar_path, pre_v2_bak)
        shutil.copy2(bak, asar_path)
        # Re-read after rollback
        header, payload_start = read_header(asar_path)
        target_path, target_meta = find_target(header)
        original = extract(asar_path, payload_start, target_meta).decode("utf-8", "replace")
        state = detect_state(original)

    if state != "unpatched":
        return {"status": "error", "reason": f"unexpected state after rollback: {state}", "asar": str(asar_path)}

    if UNPATCHED_SEARCH not in original:
        return {"status": "error", "reason": "unpatched marker missing", "asar": str(asar_path)}

    patched_text = original.replace(UNPATCHED_SEARCH, V2_REPLACE, 1)
    if V2_GUARD not in patched_text:
        return {"status": "error", "reason": "v2 guard missing after replace", "asar": str(asar_path)}

    # Snapshot pre-v2 backup if not already present
    pre_v2_bak = asar_path.with_name("app.asar.bak-before-v2")
    if not pre_v2_bak.exists():
        shutil.copy2(asar_path, pre_v2_bak)

    repack(asar_path, header, payload_start, target_path, patched_text.encode("utf-8"))

    # Verify
    h2, ps2 = read_header(asar_path)
    tp2, tm2 = find_target(h2)
    js2 = extract(asar_path, ps2, tm2).decode("utf-8", "replace")
    if detect_state(js2) != "v2":
        return {"status": "error", "reason": "verify failed", "asar": str(asar_path)}

    return {
        "status": "patched_v2",
        "asar": str(asar_path),
        "target": target_path,
        "old_size": len(original),
        "new_size": len(patched_text),
        "delta_bytes": len(patched_text) - len(original),
        "guard_present": True,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--app-dir", action="append", help="App dir containing resources/app.asar (repeatable)")
    p.add_argument("--auto", action="store_true",
                   help="Auto-discover all OpenAI.Codex_*/app dirs under %%LOCALAPPDATA%%\\OpenAI\\CodexDesktopPatched")
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
            results.append(apply_v2(d))
        except Exception as e:
            results.append({"status": "exception", "app_dir": str(d), "error": str(e)})

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
