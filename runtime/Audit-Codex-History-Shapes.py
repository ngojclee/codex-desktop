#!/usr/bin/env python3
r"""Read-only census of standalone tool-output rows in Codex Desktop histories.

Why this exists
--------------
`turn/start.toolOutput` is app-server-only and carries no model `call_id`. Before
Patch V, sidecars persisted it in the provider-shaped `function_call_output`
variant, and Patch V refuses to send such a row to a strict provider. Because
`thread/fork` - which is what the Desktop duplicate action calls - copies stored
history verbatim, a lane created that way inherits the rows and is refused on its
very first turn. Patch X converts those rows at request-build time, so an
installed Patch X build resumes them; a build without it stays blocked.

A row only matters when it is *live*, that is, after the last compaction boundary
in the rollout, because that is the window the next provider request replays.

This tool prints record types, counts, line numbers, timestamps, namespaces, tool
names and JSON value kinds only. It never prints message or payload content, so
it is safe to paste into a report.

Usage:
    python runtime\\Audit-Codex-History-Shapes.py
    python runtime\\Audit-Codex-History-Shapes.py --root D:\\other\\.codex
    python runtime\\Audit-Codex-History-Shapes.py --thread 01a056df
"""

import argparse
import json
from collections import Counter
from pathlib import Path


OUTPUT_SUFFIX = "_output"


def rollout_files(roots):
    for root in roots:
        base = Path(root).expanduser() / ".codex"
        for folder in ("sessions", "archived_sessions"):
            directory = base / folder
            if directory.exists():
                yield from sorted(directory.rglob("*.jsonl"))


def body_shape(output):
    if isinstance(output, str):
        return "string"
    if isinstance(output, list):
        kinds = sorted({str(item.get("type")) for item in output if isinstance(item, dict)})
        return "array[" + ",".join(kinds) + "]" if kinds else "array[]"
    return type(output).__name__


def scan(path, thread_filter=None):
    if thread_filter and thread_filter not in path.name:
        return None
    meta = {}
    boundary = 0
    rows = []
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for lineno, line in enumerate(handle, 1):
            if "_output" not in line and "compacted" not in line and "session_meta" not in line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "compacted":
                boundary = lineno
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "session_meta" and not meta:
                meta = {
                    "id": payload.get("id"),
                    "timestamp": payload.get("timestamp"),
                    "forked_from_id": payload.get("forked_from_id"),
                    "originator": payload.get("originator"),
                }
                continue
            kind = payload.get("type")
            if not isinstance(kind, str) or not kind.endswith(OUTPUT_SUFFIX):
                continue
            call_id = payload.get("call_id")
            usable = isinstance(call_id, str) and bool(call_id.strip())
            if usable:
                continue
            rows.append(
                {
                    "line": lineno,
                    "ts": str(record.get("timestamp") or ""),
                    "type": kind,
                    "namespace": str(payload.get("namespace")),
                    "name": str(payload.get("name")),
                    "shape": body_shape(payload.get("output")),
                }
            )
    if not rows:
        return None
    # Liveness needs the final boundary: a compaction marker can appear after any
    # given row while the file is still being read, so deciding per row during the
    # scan would call every row before the last boundary live.
    for row in rows:
        row["live"] = row["line"] > boundary
    return {"path": path, "meta": meta, "boundary": boundary, "rows": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        action="append",
        help="home directory holding .codex (repeatable). Defaults to this user profile.",
    )
    parser.add_argument("--thread", help="limit the report to a thread id or filename fragment")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also list the live row line numbers and timestamps",
    )
    args = parser.parse_args()

    roots = args.root or [str(Path.home())]
    found = []
    scanned = 0
    for path in rollout_files(roots):
        if path.stat().st_size == 0:
            continue
        scanned += 1
        result = scan(path, args.thread)
        if result:
            found.append(result)

    shapes = Counter()
    blocked = []
    for result in found:
        live = [row for row in result["rows"] if row["live"]]
        for row in result["rows"]:
            shapes[(row["type"], row["namespace"], row["name"])] += 1
        if live:
            blocked.append((result, live))

    print(f"rollout files scanned: {scanned}")
    print(f"files containing outputs without a usable call_id: {len(found)}")
    for (kind, namespace, name), count in shapes.most_common():
        print(f"    shape {kind} namespace={namespace} name={name} x{count}")
    print(f"threads a sidecar without Patch X cannot resume: {len(blocked)}")

    for result, live in sorted(blocked, key=lambda item: -len(item[1])):
        meta = result["meta"]
        thread = result["path"].stem.split("-")[-1]
        forked = meta.get("forked_from_id") or "none (not a fork)"
        stamps = sorted(row["ts"] for row in live if row["ts"])
        kinds = Counter(row["shape"] for row in live)
        print(f"\n{thread}")
        print(f"    file: {result['path']}")
        print(f"    created: {meta.get('timestamp')}  originator: {meta.get('originator')}")
        print(f"    forked_from_id: {forked}")
        print(f"    unpaired rows: total={len(result['rows'])} live={len(live)}")
        print(f"    last compaction boundary line: {result['boundary'] or 'none'}")
        print(f"    live body shapes: {dict(kinds)}")
        if stamps:
            print(f"    live row timestamps: {stamps[0]} .. {stamps[-1]}")
        if args.verbose:
            for row in live[:10]:
                print(f"      line {row['line']} ts={row['ts']} {row['type']} shape={row['shape']}")

    print("\nRule: start a replacement lane with New task (thread/start).")
    print("Never use Duplicate or fork on a thread that ever called")
    print("codex_app.send_message_to_thread, because fork copies these rows verbatim.")


if __name__ == "__main__":
    main()
