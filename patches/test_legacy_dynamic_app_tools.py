#!/usr/bin/env python3
"""Focused matcher and idempotence tests for Patch Z."""
import re

from patch_codex_asar_legacy_dynamic_app_tools import (
    PATCHED_PATTERN,
    UPSTREAM_PATTERN,
    patch_text,
    relevant_match,
)


def sample(text: str) -> str:
    return text.replace("LEGACY_GUARD", "a===`dynamic`&&(v||KSa.has(l)||s.namespace===`plugin_management`||s.namespace===`openai_settings`)&&($O(n)||jOt.has(l))")


upstream = sample(
    "let _=0,b=LEGACY_GUARD,x=!b?1:0;"
)
patched, changed = patch_text(upstream)
if not changed:
    raise AssertionError("Patch Z should change the upstream guard")
if len(patched) != len(upstream):
    raise AssertionError("Patch Z must preserve byte length")
if "&!" not in patched or "||jOt" in patched:
    raise AssertionError(f"Patch Z did not rewrite the legacy guard: {patched}")
if not any(relevant_match(m) for m in PATCHED_PATTERN.finditer(patched)):
    raise AssertionError("Patched text does not match Patch Z pattern")
if any(
    relevant_match(m)
    for m in re.finditer(
        r"(?P<var>[A-Za-z_$][A-Za-z0-9_$]*)===`dynamic`&&\("
        r"(?P<middle>[\s\S]{0,1200}?)"
        r"\)&&\((?P<gate>[A-Za-z_$][A-Za-z0-9_$]*)\((?P<host>[A-Za-z_$][A-Za-z0-9_$]*)\)\|\|"
        r"(?P<legacy>[A-Za-z_$][A-Za-z0-9_$]*)\.has\((?P<tool>[A-Za-z_$][A-Za-z0-9_$]*)\)\)",
        patched,
    )
):
    raise AssertionError("Upstream legacy guard remains after Patch Z")

again, changed_again = patch_text(patched)
if changed_again or again != patched:
    raise AssertionError("Patch Z must be idempotent")

non_guard = sample("x=1;LEGACY_GUARD;")
patched_non_guard, changed_non_guard = patch_text(non_guard)
if not changed_non_guard or len(patched_non_guard) != len(non_guard):
    raise AssertionError("Patch Z should preserve unrelated code length")

if any(
    relevant_match(m)
    for m in UPSTREAM_PATTERN.finditer("a===`dynamic`&&(s.namespace===`other`)&&($O(n)||jOt.has(l))")
):
    raise AssertionError("Patch Z should only match known plugin/settings guard")

print("Patch Z legacy dynamic app-tool matcher tests passed.")
