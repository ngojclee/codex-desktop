#!/usr/bin/env python3
"""Patch J - Bypass Statsig feature gates for Computer Use on Windows.

The Codex Desktop renderer checks Statsig gates before enabling Computer Use.
Gate IDs: 1506311413 (Any App), 410065390 (Chrome), 410262010 (Browser).

This patch uses a flexible regex to match any minified identifier call wrapping
the gate ID (e.g. c(`ID`), bc(`ID`), l(`ID`), C(`ID`)) and replaces the full
call expression with !0 (true) padded to the same byte length. No ASAR repack
needed.

Idempotent: re-running on an already-patched asar is a no-op.
"""
import argparse
import json
import re
import shutil
from pathlib import Path


GATES = [
    (b"1506311413", "computer_use (Any App)"),
    (b"410065390", "browser_use_external (Google Chrome)"),
    (b"410262010", "browser_use (In-app Browser)"),
]

BT = b"\x60"
PATCH_MARKER = b"/*J*/"
CORRUPTED_TRUE_PATTERN = re.compile(
    rb"(?<![a-zA-Z0-9_$])[a-zA-Z_$][a-zA-Z0-9_$]{0,2}!0 {2,}"
)


def make_pattern(gate_id: bytes):
    return re.compile(rb"[a-zA-Z_$][a-zA-Z0-9_$]*\(" + re.escape(BT + gate_id + BT) + rb"\)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app-dir", required=True,
                    help="Codex install dir (contains resources/app.asar)")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    app = Path(args.app_dir).resolve()
    asar = app / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    data = asar.read_bytes()
    patched = data
    results = []

    corrupted_matches = list(CORRUPTED_TRUE_PATTERN.finditer(patched))
    for m in corrupted_matches:
        old = m.group()
        replacement = b"!0" + PATCH_MARKER
        new = replacement + b" " * (len(old) - len(replacement))
        patched = patched.replace(old, new, 1)
    if corrupted_matches:
        results.append(
            {
                "gate": "repair",
                "label": "corrupted prior Patch J true literals",
                "status": "patched",
                "replaced": len(corrupted_matches),
            }
        )

    for gate_id, label in GATES:
        pattern = make_pattern(gate_id)
        matches = list(pattern.finditer(patched))

        if not matches:
            # Check if already patched (gate ID preceded by !0)
            if gate_id in patched:
                results.append({"gate": gate_id.decode(), "label": label, "status": "pattern_changed"})
            else:
                results.append({"gate": gate_id.decode(), "label": label, "status": "already_patched"})
            continue

        for m in matches:
            old = m.group()
            replacement = b"!0" + PATCH_MARKER
            new = replacement + b" " * (len(old) - len(replacement))
            patched = patched.replace(old, new, 1)
        results.append({"gate": gate_id.decode(), "label": label, "status": "patched", "replaced": len(matches)})

    changed = patched != data
    if changed:
        if not args.no_backup:
            bk = asar.with_name("app.asar.bak-before-cu-gate")
            if not bk.exists():
                shutil.copy2(asar, bk)
        asar.write_bytes(patched)

    # Verify no raw gate patterns remain
    verify = asar.read_bytes()
    corrupted = CORRUPTED_TRUE_PATTERN.search(verify)
    if corrupted:
        raise SystemExit(f"Verification failed: corrupted Patch J token remains: {corrupted.group()[:40]!r}")
    for gate_id, _ in GATES:
        pat = make_pattern(gate_id)
        if pat.search(verify):
            raise SystemExit(f"Verification failed: gate {gate_id.decode()} still present")

    output = {"status": "patched" if changed else "already_patched", "asar": str(asar), "gates": results}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
