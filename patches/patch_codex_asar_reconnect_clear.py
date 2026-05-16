#!/usr/bin/env python3
"""Patch D — Clear renderer conversations cache on sidecar reconnect.

Problem
=======
After Patch C v2 fixed the renderer-side state-replace storm, a residual bug
remains: when delegation A->B is invoked via CLI external to the running
sidecar, the sidecar's in-memory cache for B goes stale. Killing the sidecar
restarts it (Electron supervisor auto-respawn) so the new sidecar reads B's
JSONL fresh. BUT the renderer's worker holds its own `this.conversations`
Map across the reconnect — the existing `markAllConversationsNeedResume
AfterReconnect` only flips a flag, it does NOT discard cached turn data. So
the renderer keeps showing the stale snapshot it cached before the kill.

The "close & reopen full app" workaround works only because it kills the
renderer process too, throwing away that Map.

Patch D
=======
Inject cache-clear into `markAllConversationsNeedResumeAfterReconnect`:
after the existing resume-state flag loop, call `applyConversationState(id,
null)` for every cached conversation. That method deletes the entry from
the map and fires `conversationStateCallbacks` with null, which the React
layer reacts to by re-fetching from the (now-fresh) sidecar.

Also reset `recentConversationsLoaded` and `fetchedRecentConversations` so
the sidebar list is re-fetched too — Patch C v2's auto-paginate guard
keys off `fetchedRecentConversations`, so clearing it re-enables the
full paginate on the next refetch.

Combined effect: kill sidecar -> Electron respawn -> renderer reconnect ->
Patch D fires -> Map cleared -> React re-fetches everything from fresh
sidecar -> UI shows current content including external CLI appends.

Idempotency: presence of marker `PATCH_D_RECONNECT_CLEAR` in the JS.
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path


UNPATCHED_SEARCHES = (
    (
        "markAllConversationsNeedResumeAfterReconnect(){"
        "let{previousStreamingCount:e,previousRoleCount:t}=this.streamState.resetAfterReconnect(),n=0;"
        "for(let[e,t]of this.conversations)"
        "t.resumeState!==`needs_resume`&&(n+=1,this.updateConversationState(e,e=>{e.resumeState=`needs_resume`}));"
        "z.info(`websocket_reconnect_marked_threads_needing_resume`,"
        "{safe:{conversationCount:this.conversations.size,markedCount:n,previousStreamingCount:e,previousRoleCount:t},sensitive:{}})"
        "}"
    ),
    (
        "markAllConversationsNeedResumeAfterReconnect(){"
        "let{previousStreamingCount:e,previousRoleCount:t}=this.streamState.resetAfterReconnect(),n=0;"
        "for(let[e,t]of this.conversations)"
        "t.resumeState!==`needs_resume`&&(n+=1,this.updateConversationState(e,e=>{e.resumeState=`needs_resume`}));"
        "R.info(`websocket_reconnect_marked_threads_needing_resume`,"
        "{safe:{conversationCount:this.conversations.size,markedCount:n,previousStreamingCount:e,previousRoleCount:t},sensitive:{}})"
        "}"
    ),
)

PATCHED_REPLACE = (
    "markAllConversationsNeedResumeAfterReconnect(){"
    "let{previousStreamingCount:e,previousRoleCount:t}=this.streamState.resetAfterReconnect(),n=0;"
    "for(let[e,t]of this.conversations)"
    "t.resumeState!==`needs_resume`&&(n+=1,this.updateConversationState(e,e=>{e.resumeState=`needs_resume`}));"
    # PATCH_D_RECONNECT_CLEAR start
    "let __pdIds=[...this.conversations.keys()];"
    "for(let __pdId of __pdIds){try{this.applyConversationState(__pdId,null)}catch(_){}}"
    "try{this.recentConversationsLoaded=!1}catch(_){}"
    "try{this.fetchedRecentConversations=!1}catch(_){}"
    # PATCH_D_RECONNECT_CLEAR end
    "__PATCH_D_LOGGER__.info(`websocket_reconnect_marked_threads_needing_resume`,"
    "{safe:{conversationCount:this.conversations.size,markedCount:n,previousStreamingCount:e,previousRoleCount:t,patch_d_cleared:__pdIds.length},sensitive:{}})"
    "}"
)

MARKER = "__pdIds"  # unique token in patched output


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


def find_target(header):
    for path, meta in iter_files(header):
        if (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "app-server-manager-signals-" in path
            and "offset" in meta
        ):
            return path, meta
    raise RuntimeError("Could not find app-server-manager-signals-*.js")


def extract(asar_path: Path, payload_start: int, meta: dict) -> bytes:
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))


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


def apply(app_dir: Path) -> dict:
    asar_path = app_dir / "resources" / "app.asar"
    if not asar_path.exists():
        return {"status": "missing", "asar": str(asar_path)}

    header, payload_start = read_header(asar_path)
    target_path, target_meta = find_target(header)
    original = extract(asar_path, payload_start, target_meta).decode("utf-8", "replace")

    if MARKER in original:
        return {"status": "already_patched", "asar": str(asar_path)}

    matched_search = None
    matched_logger = None
    for candidate in UNPATCHED_SEARCHES:
        if candidate in original:
            matched_search = candidate
            matched_logger = "R" if "R.info(" in candidate else "z"
            break

    if matched_search is None:
        # Try to give a useful hint
        sample = ""
        idx = original.find("markAllConversationsNeedResumeAfterReconnect")
        if idx >= 0:
            sample = original[idx:idx+400]
        return {"status": "pattern_not_found", "asar": str(asar_path), "near": sample}

    patched_text = original.replace(
        matched_search,
        PATCHED_REPLACE.replace("__PATCH_D_LOGGER__", matched_logger),
        1,
    )
    if MARKER not in patched_text:
        return {"status": "error", "reason": "marker missing after replace"}

    bak = asar_path.with_name("app.asar.bak-before-patch-d")
    if not bak.exists():
        shutil.copy2(asar_path, bak)

    repack(asar_path, header, payload_start, target_path, patched_text.encode("utf-8"))

    h2, ps2 = read_header(asar_path)
    tp2, tm2 = find_target(h2)
    js2 = extract(asar_path, ps2, tm2).decode("utf-8", "replace")
    if MARKER not in js2:
        return {"status": "error", "reason": "verify failed"}

    return {
        "status": "patched",
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
            result = apply(d)
            results.append(result)
            if result.get("status") not in {"patched", "already_patched"}:
                print(json.dumps(results, indent=2))
                raise SystemExit(1)
        except Exception as e:
            results.append({"status": "exception", "app_dir": str(d), "error": str(e)})
            print(json.dumps(results, indent=2))
            raise SystemExit(1)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
