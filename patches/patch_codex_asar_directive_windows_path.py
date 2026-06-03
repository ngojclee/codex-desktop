#!/usr/bin/env python3
r"""Patch H - make renderer markdown directive parsing fail-soft for Windows paths.

Problem
=======
Codex Desktop renders model/user text through a markdown pipeline that includes
micromark directive parsing. App directives such as `::git-stage{cwd="..."}`
are valid for the app, but Windows paths inside quoted attributes can contain
backslashes (for example `D:\Python\projects\codex-desktop`). In some builds,
the directive parser treats those backslashes as escapes and throws:

    invalid syntax at line 1 col 5: cwd="D:\\Python\\projects\\codex-desktop"

That exception bubbles through `LocalConversationPage` and shows the global
"Oops, an error has occurred" page even though the thread/backend is healthy.

Patch H
=======
Inject a tiny sanitizer at the start of the renderer Markdown component. It
only touches lines that look like Codex app directives (`::git-*{...}`,
`::code-comment{...}`, `::archive{...}`), replacing backslashes with forward
slashes inside that single directive line before the markdown/directive parser
runs. Normal prose, code blocks, JSONL storage, sidecar behavior, and non-
directive markdown are untouched.

Idempotent marker: `__PATCH_H_DIRECTIVE_WINDOWS_PATH__`.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
from pathlib import Path

MARKER = "__PATCH_H_DIRECTIVE_WINDOWS_PATH__"

SEARCH_PATTERNS = (
    # 26.506-style markdown bundle
    "ee=t??S,te=(0,P.useMemo)(()=>u===`indexed`?Mh(e):e,[e,u]),T=(0,P.useMemo)",
    # 26.513-style markdown bundle
    "p??b,T=l===`indexed`,E=n,ne=T?ar(Hr(E)):E,re=(0,L.useMemo)",
    # 26.519-style markdown bundle: centralized pre-tokenize transform.
    "function mr(e,t){return e}",
    # 26.527-style directive parser bundle: sanitize before micromark parses
    # Codex app directives out of completed agent messages.
    "function JT(e,t){let n=t?.lineStartNames==null?e:ZT(e,t.lineStartNames);if(n==null)return[];let r=[];return XT(MT(n,void 0),r)",
    # 26.601-style directive parser bundle: same parser path, new minified
    # function names after upstream rebundle.
    "function JE(e,t){let n=t?.lineStartNames==null?e:ZE(e,t.lineStartNames);if(n==null)return[];let r=[];return XE(ME(n,void 0),r)",
)

REPLACEMENTS = (
    "ee=t??S,te=(0,P.useMemo)(()=>{let __PATCH_H_DIRECTIVE_WINDOWS_PATH__=e.replace(/^(::(?:git-[a-z-]+|code-comment|archive)\\{[^\\n]*\\})$/gm,e=>e.replace(/\\\\/g,`/`));return u===`indexed`?Mh(__PATCH_H_DIRECTIVE_WINDOWS_PATH__):__PATCH_H_DIRECTIVE_WINDOWS_PATH__},[e,u]),T=(0,P.useMemo)",
    "p??b,T=l===`indexed`,E=(globalThis.__PATCH_H_DIRECTIVE_WINDOWS_PATH__=!0,n.replace(/^(::(?:git-[a-z-]+|code-comment|archive)\\{[^\\n]*\\})$/gm,e=>e.replace(/\\\\/g,`/`))),ne=T?ar(Hr(E)):E,re=(0,L.useMemo)",
    "function mr(e,t){return globalThis.__PATCH_H_DIRECTIVE_WINDOWS_PATH__=!0,e.replace(/^(::(?:git-[a-z-]+|code-comment|archive)\\{[^\\n]*\\})$/gm,e=>e.replace(/\\\\/g,`/`))}",
    "function JT(e,t){let n=t?.lineStartNames==null?e:ZT(e,t.lineStartNames);if(n==null)return[];n=(globalThis.__PATCH_H_DIRECTIVE_WINDOWS_PATH__=!0,n.replace(/^(::(?:git-[a-z-]+|code-comment|archive)\\{[^\\n]*\\})$/gm,e=>e.replace(/\\\\/g,`/`)));let r=[];return XT(MT(n,void 0),r)",
    "function JE(e,t){let n=t?.lineStartNames==null?e:ZE(e,t.lineStartNames);if(n==null)return[];n=(globalThis.__PATCH_H_DIRECTIVE_WINDOWS_PATH__=!0,n.replace(/^(::(?:git-[a-z-]+|code-comment|archive)\\{[^\\n]*\\})$/gm,e=>e.replace(/\\\\/g,`/`)));let r=[];return XE(ME(n,void 0),r)",
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

def find_target(header, asar_path, payload_start):
    candidates = []
    for p, m in iter_files(header):
        if not (p.startswith("webview/assets/") and p.endswith(".js") and "offset" in m):
            continue
        text = extract(asar_path, payload_start, m).decode("utf-8", "replace")
        if MARKER in text or any(pattern in text for pattern in SEARCH_PATTERNS):
            candidates.append((p, m, text))
    if not candidates:
        raise RuntimeError("Markdown component bundle not found or unsupported upstream layout")
    if len(candidates) > 1:
        names = [p for p, _m, _t in candidates]
        raise RuntimeError(f"Multiple markdown component candidates: {names}")
    return candidates[0]

def extract(asar_path, payload_start, meta):
    with asar_path.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"]))

def patch_js(text: str):
    if MARKER in text:
        return text, {"status": "already_patched"}
    for search, repl in zip(SEARCH_PATTERNS, REPLACEMENTS):
        if search in text:
            return text.replace(search, repl, 1), {"status": "patched"}
    raise RuntimeError("Markdown sanitizer insertion point not found")

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
    for p, m, _ in entries:
        if p == target_path:
            update_integrity(m, patched_data)
            break
    else:
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
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    app = Path(args.app_dir).resolve()
    asar = app / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    header, payload_start = read_header(asar)
    target_path, _target_meta, original_text = find_target(header, asar, payload_start)
    patched_text, info = patch_js(original_text)

    if info["status"] == "already_patched":
        print(json.dumps({"status": "already_patched", "target": target_path}, indent=2))
        return

    if not args.no_backup:
        bk = asar.with_name("app.asar.bak-before-patch-h")
        if not bk.exists():
            shutil.copy2(asar, bk)

    repack(asar, header, payload_start, target_path, patched_text.encode("utf-8"))

    vh, vps = read_header(asar)
    vt, vm, verify_text = find_target(vh, asar, vps)
    if MARKER not in verify_text:
        raise SystemExit("Verification failed: Patch H marker missing after repack")

    print(json.dumps({"status": "patched", "target": vt, "asar": str(asar)}, indent=2))

if __name__ == "__main__":
    main()
