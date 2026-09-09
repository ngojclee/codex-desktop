#!/usr/bin/env python3
"""Focused matcher and idempotence tests for Patch Y."""

from patch_codex_asar_automation_mode_union import (
    PATCH_MARKER,
    PATCHED_X_PATTERN,
    PATCHED_Y_PATTERN,
    X_MODE_PATTERN,
    Y_MODE_PATTERN,
    patch_text,
)


SOURCE = (
    "sWn=Ih({mode:Uh(`view`),id:rWn}),"
    "cWn=Ih({mode:Uh(`delete`),id:rWn}),"
    "yWn=zh(`mode`,[sWn,_Wn,vWn,cWn]).refine(e=>e.targetThreadId==null),"
    "bWn=zh(`kind`,[pWn,mWn]),"
    "xWn=zh(`mode`,[sWn,bWn,vWn,cWn]),"
    "SWn=yWn.transform(GUn),CWn=xWn.transform(GUn),"
)

patched, changed = patch_text(SOURCE)
if not changed:
    raise AssertionError("Patch Y should change the upstream layout")
if patched.count(PATCH_MARKER) != 2:
    raise AssertionError("Patch Y should mark both replaced unions")
if Y_MODE_PATTERN.search(patched) or X_MODE_PATTERN.search(patched):
    raise AssertionError("upstream discriminated unions remain")
if not PATCHED_Y_PATTERN.search(patched) or not PATCHED_X_PATTERN.search(patched):
    raise AssertionError("plain-union replacements are missing")
if ".or(_Wn).or(vWn).or(cWn)" not in patched:
    raise AssertionError("yWn plain union is incomplete")
if ".or(bWn).or(vWn).or(cWn)" not in patched:
    raise AssertionError("xWn plain union is incomplete")
if (
    ".or(cWn)"
    + PATCH_MARKER
    + ".refine(e=>e.targetThreadId==null)"
) not in patched:
    raise AssertionError("existing yWn refinement was not preserved")

patched_again, changed_again = patch_text(patched)
if changed_again or patched_again != patched:
    raise AssertionError("Patch Y is not idempotent")

try:
    patch_text("yWn=zh(`mode`,[sWn,_Wn,vWn,cWn]),")
except RuntimeError as exc:
    if "bWn" not in str(exc):
        raise AssertionError(f"unexpected drift error: {exc}") from exc
else:
    raise AssertionError("incomplete upstream layout should fail loudly")

try:
    patch_text("/*Y:automation-mode-union*/yWn=sWn.or(_Wn).or(vWn).or(cWn)")
except RuntimeError as exc:
    if "partial or malformed" not in str(exc):
        raise AssertionError(f"unexpected partial-marker error: {exc}") from exc
else:
    raise AssertionError("partial marker should fail loudly")

print("Patch Y automation mode union tests passed.")
