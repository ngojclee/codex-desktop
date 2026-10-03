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

Idempotency: presence of marker `__pdIds` in the JS.
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


JS_IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"

# Upstream 26.930 refactored this method to use `this.deps.*`, `restoreStreams` and a
# foreground-conversation retry path, so the old anchor no longer matches. The defect is
# unchanged: `markAllConversationsNeedResumeAfterReconnect` still only flips
# `resumeState` on `this.deps.threadStore.conversations`, it never drops the cached
# turn data. Insert the same conversation-clear step, but through the store's own
# `removeConversationStoreEntries` so titles, summaries, and subscriptions are
# invalidated properly instead of deleting the Map entry directly.
# Upstream 26.930 refactored this method to use `this.deps.*`, `restoreStreams` and a
# foreground-conversation retry path, so the old anchor no longer matches. The defect is
# unchanged: `markAllConversationsNeedResumeAfterReconnect` still only flips
# `resumeState` on `this.deps.threadStore.conversations`, it never drops the cached
# turn data. Insert the same conversation-clear step, but through the store's own
# `removeConversationStoreEntries` so titles, summaries, and subscriptions are
# invalidated properly instead of deleting the Map entry directly.
#
# The two anchors we need are contiguous in the minified source:
#   this.deps.threadStore.resetAfterReconnect();
#   let{previousStreamingCount:r,previousRoleCount:i}=this.deps.streamState.resetAfterReconnect(e?.restoreStreams===!0),a=0;
# We insert the clear between them, preserving the retry machinery that follows.
# The legacy form used plain `this.*` and no `deps.` / `restoreStreams`, so both
# shapes are covered: the new 26.930 body and the pre-refactor one. A match on either
# means this thread cache must still be cleared.

UNPATCHED_LEGACY_RE = re.compile(
    "markAllConversationsNeedResumeAfterReconnect\\(\\)\\{"
    rf"(?P<pag_cancel>this\.pagination\.cancelItemLoads\(\),)?"
    r"(?P<thread_store>this\.threadStore\.resetAfterReconnect\(\);)?"
    rf"let\{{previousStreamingCount:(?P<stream>{JS_IDENT}),previousRoleCount:(?P<role>{JS_IDENT})\}}=this\.streamState\.resetAfterReconnect\(\),(?P<count>{JS_IDENT})=0;"
    rf"for\(let\[(?P<id>{JS_IDENT}),(?P<conv>{JS_IDENT})\]of this\.conversations\)"
    r"(?P=conv)\.resumeState!==`needs_resume`&&\((?P=count)\+=1,this\.updateConversationState\((?P=id),"
    rf"(?P<cb>{JS_IDENT})=>\{{(?P=cb)\.resumeState=`needs_resume`\}}\)\);"
    rf"(?P<logger>(?:this\.)?{JS_IDENT})\.info\(`websocket_reconnect_marked_threads_needing_resume`,"
    r"\{safe:\{conversationCount:this\.conversations\.size,markedCount:(?P=count),previousStreamingCount:(?P=stream),previousRoleCount:(?P=role)\},sensitive:\{\}\}\)"
    r"\}"
)

# The 26.930 form: `this.deps.*`, `restoreStreams`, and the store reset call placed
# before the streaming counters. We anchor on the contiguous pair
# `this.deps.threadStore.resetAfterReconnect();let{previousStreamingCount:` and splice
# the clear between that call and the count loop.
# The two anchors are contiguous in the minified source:
#   this.deps.threadStore.resetAfterReconnect();
#   let{previousStreamingCount:r,previousRoleCount:i}=this.deps.streamState.resetAfterReconnect(e?.restoreStreams===!0),a=0;
# We capture the contiguous pair and splice the clear between them, preserving the
# foreground-retry machinery that follows.
# The contiguous pair we anchor on, verbatim from the upstream minified source:
#   this.deps.threadStore.resetAfterReconnect();
#   let{previousStreamingCount:r,previousRoleCount:i}=this.deps.streamState.resetAfterReconnect(e?.restoreStreams===!0),a=0;
# Capturing only that pair keeps the replacement tied to the method's reset call, not
# to any incidental earlier lines.
# The contiguous pair we anchor on, verbatim from the upstream minified source:
#   this.deps.threadStore.resetAfterReconnect();
#   let{previousStreamingCount:r,previousRoleCount:i}=this.deps.streamState.resetAfterReconnect(e?.restoreStreams===!0),a=0;
# Capturing only that pair keeps the replacement tied to the method's reset call, not
# to any incidental earlier lines.
# Anchor on the contiguous pair that actually marks the reset call, not the whole
# method body. `this.deps.threadStore.resetAfterReconnect();let{previousStreamingCount:`
# is the last reset before the streaming counters, and inserting our clear between the
# two keeps the retry machinery that follows.
UNPATCHED_MODERN_RE = re.compile(
    r"this\.deps\.threadStore\.resetAfterReconnect\(\);"
    rf"let\{{previousStreamingCount:(?P<stream>{JS_IDENT}),previousRoleCount:(?P<role>{JS_IDENT})\}}=this\.deps\.streamState\.resetAfterReconnect\(e\?\.restoreStreams===!0\),"
    rf"(?P<count>{JS_IDENT})=0;"
)

PATCHED_BODY_EXTRA = (
    "let __pdIds=[];"
    "for(let[__pdId,__pdConv]of this.deps.threadStore.conversations)__pdIds.push(__pdId);"
    "for(let __pdId of __pdIds){try{this.deps.threadStore.removeConversationStoreEntries(__pdId)}catch(_){}}"
    "try{this.fetchedRecentConversations=!1}catch(_){}"
)

LEGACY_PATCHED_EXTRA = (
    "let __pdIds=[...this.conversations.keys()];"
    "for(let __pdId of __pdIds){try{this.applyConversationState(__pdId,null)}catch(_){}}"
    "try{this.recentConversationsLoaded=!1}catch(_){}"
    "try{this.fetchedRecentConversations=!1}catch(_){}"
)

def make_patched_replace(match: re.Match, modern: bool) -> str:
    if modern:
        stream = match.group("stream")
        role = match.group("role")
        count = match.group("count")
        return (
            "this.deps.threadStore.resetAfterReconnect();"
            + PATCHED_BODY_EXTRA
            + f"let{{previousStreamingCount:{stream},previousRoleCount:{role}}}=this.deps.streamState.resetAfterReconnect(e?.restoreStreams===!0),{count}=0;"
        )

    pag_cancel = match.group("pag_cancel") or ""
    thread_store = match.group("thread_store") or ""
    stream = match.group("stream")
    role = match.group("role")
    count = match.group("count")
    ident = match.group("id")
    conv = match.group("conv")
    cb = match.group("cb")
    logger = match.group("logger")
    return (
        "markAllConversationsNeedResumeAfterReconnect(){"
        f"{pag_cancel}{thread_store}"
        f"let{{previousStreamingCount:{stream},previousRoleCount:{role}}}=this.streamState.resetAfterReconnect(),{count}=0;"
        f"for(let[{ident},{conv}]of this.conversations)"
        f"{conv}.resumeState!==`needs_resume`&&({count}+=1,this.updateConversationState({ident},{cb}=>{{{cb}.resumeState=`needs_resume`}}));"
        + LEGACY_PATCHED_EXTRA
        + f"{logger}.info(`websocket_reconnect_marked_threads_needing_resume`,"
        + f"{{safe:{{conversationCount:this.conversations.size,markedCount:{count},previousStreamingCount:{stream},previousRoleCount:{role},patch_d_cleared:__pdIds.length}},sensitive:{{}}}})"
        + "}"
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
            if not (path.endswith(".js") and "offset" in meta):
                continue
            text = extract(asar_path, payload_start, meta).decode("utf-8", "replace")
            # Only the chunk that owns the reconnect method body may be patched, not any
            # chunk that merely mentions it or carries the marker.
            if "markAllConversationsNeedResumeAfterReconnect" not in text:
                continue
            if (
                path.startswith("webview/assets/")
                or path.startswith(".vite/")
                or path.startswith("src/")
            ):
                candidates.append((path, meta))
        # Upstream 26.930 puts the reconnect body in two chunk groups: the Vite
        # `bootstrap-*.js` loader and the `app-shared-*.js` bundle. The bootstrap chunk is
        # the owner that actually runs the reconnect path, so prefer it and only fall
        # back to app-shared if it is the only match.
        bootstrap = [c for c in candidates if "bootstrap" in c[0]]
        if bootstrap:
            if len(bootstrap) > 1:
                raise RuntimeError(f"Multiple bootstrap reconnect chunks found: {[p for p, _ in bootstrap]}")
            return bootstrap[0]
        if candidates:
            return candidates[0]
        raise RuntimeError("Could not find reconnect renderer chunk")
    raise RuntimeError("Could not find reconnect renderer chunk")


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
    target_path, target_meta = find_target(header, asar_path, payload_start)
    original = extract(asar_path, payload_start, target_meta).decode("utf-8", "replace")

    if MARKER in original:
        return {"status": "already_patched", "asar": str(asar_path)}

    modern_match = UNPATCHED_MODERN_RE.search(original)
    legacy_match = UNPATCHED_LEGACY_RE.search(original) if modern_match is None else None
    match = modern_match or legacy_match
    is_modern = modern_match is not None

    if match is None:
        # Try to give a useful hint
        sample = ""
        idx = original.find("markAllConversationsNeedResumeAfterReconnect")
        if idx >= 0:
            sample = original[idx:idx+400]
        return {"status": "pattern_not_found", "asar": str(asar_path), "near": sample}

    patched_text = original[: match.start()] + make_patched_replace(match, is_modern) + original[match.end() :]
    if MARKER not in patched_text:
        return {"status": "error", "reason": "marker missing after replace"}

    bak = asar_path.with_name("app.asar.bak-before-patch-d")
    if not bak.exists():
        shutil.copy2(asar_path, bak)

    repack(asar_path, header, payload_start, target_path, patched_text.encode("utf-8"))

    h2, ps2 = read_header(asar_path)
    tp2, tm2 = find_target(h2, asar_path, ps2)
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
