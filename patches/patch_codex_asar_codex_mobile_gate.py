#!/usr/bin/env python3
"""Patch K - Expose the Codex mobile setup entrypoint.

Recent Codex Desktop builds ship the Codex mobile page and setup flow in the
renderer bundle, but hide the sidebar entrypoint behind remote-control Statsig
gates. This patch makes the local UI entrypoint visible while leaving the real
ChatGPT/WHAM pairing API untouched, so account auth and server entitlement are
still enforced by upstream.

Changes:
- Bypass Statsig gates 1042620455 (remote-control feature visibility) and
  2798711298 (Codex mobile onboarding).
- Relax the sidebar Codex mobile gate from
  enabled && remoteControlFeaturesVisible && remoteControlOnboardingEnabled &&
  !hasCompletedCodexMobileSetup
  to enabled && !hasCompletedCodexMobileSetup.

All replacements are same-length byte patches inside app.asar. No ASAR repack
is needed, and reruns are safe.
"""
import argparse
import json
import re
import shutil
from pathlib import Path


BT = b"\x60"

GATES = [
    (b"1042620455", "remote_control_features_visible"),
    (b"2798711298", "codex_mobile_onboarding"),
]

SIDEBAR_GATE_PATTERN = re.compile(
    rb"(?P<prefix>function [A-Za-z_$][A-Za-z0-9_$]*\(\{"
    rb"enabled:(?P<enabled>[A-Za-z_$][A-Za-z0-9_$]*),"
    rb"hasCompletedCodexMobileSetup:(?P<completed>[A-Za-z_$][A-Za-z0-9_$]*),"
    rb"(?:isChatGptAuth:(?P<auth>[A-Za-z_$][A-Za-z0-9_$]*),)?"
    rb"remoteControlFeaturesVisible:(?P<visible>[A-Za-z_$][A-Za-z0-9_$]*),"
    rb"remoteControlOnboardingEnabled:(?P<onboard>[A-Za-z_$][A-Za-z0-9_$]*)"
    rb"\}\)\{)"
    rb"(?P<expr>return (?P=enabled)&&(?:(?P=auth)&&)?(?P=visible)&&(?P=onboard)&&!(?P=completed))"
)

PROFILE_FOOTER_GATE_PATTERN = re.compile(
    rb"(?P<expr>"
    rb"(?P<auth>[A-Za-z_$][A-Za-z0-9_$]*)==="
    + re.escape(BT + b"chatgpt" + BT)
    + rb"&&(?P<completed>[A-Za-z_$][A-Za-z0-9_$]*)&&(?P<visible>[A-Za-z_$][A-Za-z0-9_$]*)"
    rb")(?=\?\(0,[A-Za-z_$][A-Za-z0-9_$]*\.jsx\)\([A-Za-z_$][A-Za-z0-9_$]*,\{tooltipContent:)"
)

PATCH_MARKER = b"/*K*/"


def make_gate_pattern(gate_id: bytes):
    return re.compile(
        rb"[A-Za-z_$][A-Za-z0-9_$]*\("
        + re.escape(BT + gate_id + BT)
        + rb"\)"
    )


def patch_gate_calls(data: bytes):
    patched = data
    results = []

    for gate_id, label in GATES:
        pattern = make_gate_pattern(gate_id)
        matches = list(pattern.finditer(patched))
        if not matches:
            if gate_id in patched:
                results.append({
                    "gate": gate_id.decode(),
                    "label": label,
                    "status": "pattern_changed",
                })
            else:
                results.append({
                    "gate": gate_id.decode(),
                    "label": label,
                    "status": "already_patched",
                })
            continue

        for match in matches:
            old = match.group()
            new = b"!0" + b" " * (len(old) - 2)
            patched = patched.replace(old, new, 1)

        results.append({
            "gate": gate_id.decode(),
            "label": label,
            "status": "patched",
            "replaced": len(matches),
        })

    return patched, results


def patch_sidebar_gate(data: bytes):
    match = SIDEBAR_GATE_PATTERN.search(data)
    if match:
        old_expr = match.group("expr")
        enabled = match.group("enabled")
        completed = match.group("completed")
        new_expr = b"return " + enabled + b"&&!" + completed + PATCH_MARKER
        if len(new_expr) > len(old_expr):
            raise SystemExit("Internal error: Codex mobile gate replacement is too long")
        new_expr += b" " * (len(old_expr) - len(new_expr))

        patched = data[:match.start("expr")] + new_expr + data[match.end("expr"):]
        return patched, {
            "status": "patched",
            "layout": "legacy_sidebar",
            "old": old_expr.decode("utf-8", "replace"),
            "new": new_expr.decode("utf-8", "replace").rstrip(),
        }

    match = PROFILE_FOOTER_GATE_PATTERN.search(data)
    if match:
        old_expr = match.group("expr")
        completed = match.group("completed")
        visible = match.group("visible")
        # 26.623 moved the entrypoint into the profile footer and only showed
        # it after setup had completed. Keep the upstream feature-visible check
        # (gates are patched above) and let the existing inner UI switch between
        # Codex Mobile and Remote based on the persisted completion state.
        new_expr = visible + PATCH_MARKER
        if len(new_expr) > len(old_expr):
            raise SystemExit("Internal error: Codex mobile profile footer replacement is too long")
        new_expr += b" " * (len(old_expr) - len(new_expr))

        patched = data[:match.start("expr")] + new_expr + data[match.end("expr"):]
        return patched, {
            "status": "patched",
            "layout": "profile_footer_26_623",
            "old": old_expr.decode("utf-8", "replace"),
            "new": new_expr.decode("utf-8", "replace").rstrip(),
        }

    if PATCH_MARKER in data:
        return data, {"status": "already_patched"}
    raise SystemExit(
        "Could not find Codex mobile sidebar/profile gate. Upstream renderer "
        "shape changed; inspect the bundle before releasing."
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--app-dir",
        required=True,
        help="Codex install dir (contains resources/app.asar)",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    app = Path(args.app_dir).resolve()
    asar = app / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    data = asar.read_bytes()
    patched, gate_results = patch_gate_calls(data)
    patched, sidebar_result = patch_sidebar_gate(patched)
    changed = patched != data

    if changed:
        if not args.no_backup:
            backup = asar.with_name("app.asar.bak-before-codex-mobile-gate")
            if not backup.exists():
                shutil.copy2(asar, backup)
        asar.write_bytes(patched)

    verify = asar.read_bytes()
    if SIDEBAR_GATE_PATTERN.search(verify) or PROFILE_FOOTER_GATE_PATTERN.search(verify):
        raise SystemExit("Verification failed: original Codex mobile entrypoint gate remains")
    if PATCH_MARKER not in verify:
        raise SystemExit("Verification failed: Patch K entrypoint marker missing")
    for gate_id, _label in GATES:
        if make_gate_pattern(gate_id).search(verify):
            raise SystemExit(f"Verification failed: gate {gate_id.decode()} call remains")

    print(json.dumps({
        "status": "patched" if changed else "already_patched",
        "asar": str(asar),
        "sidebar_gate": sidebar_result,
        "gates": gate_results,
    }, indent=2))


if __name__ == "__main__":
    main()
