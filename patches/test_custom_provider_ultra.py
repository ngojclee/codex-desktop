#!/usr/bin/env python3
"""Focused matcher tests for Patch S custom-provider Ultra support."""
from patch_codex_asar_custom_provider_ultra import PATCH_MARKER, patch_text


def check(name: str, source: str, expected: str):
    patched, changed = patch_text(source)
    if not changed:
        raise AssertionError(f"{name}: expected a change")
    if expected not in patched:
        raise AssertionError(f"{name}: expected fragment missing: {expected}")
    patched_again, changed_again = patch_text(patched)
    if changed_again or patched_again != patched:
        raise AssertionError(f"{name}: patch is not idempotent")


check(
    "current 26.721 layout",
    "G=ja(Q,({additionalAvailableModels:e,authMethod:t,hostId:n,"
    "includeUltraReasoningEffort:r,limit:i},{get:a})=>{let o=a(R),"
    "s=a(j),c=r&&a(Fh,`1186680773`);return{select:()=>c}})",
    "c=r&&(t===`apikey`||a(Fh,`1186680773`))" + PATCH_MARKER,
)

check(
    "renamed minifier variables",
    "Z=ja(Q,({additionalAvailableModels:n,authMethod:o,hostId:s,"
    "includeUltraReasoningEffort:l,limit:u},{get:d})=>{let f=d(R),"
    "p=d(j),m=l&&d(Fh,`1186680773`);return{select:()=>m}})",
    "m=l&&(o===`apikey`||d(Fh,`1186680773`))" + PATCH_MARKER,
)

try:
    patch_text(
        "({authMethod:t,includeUltraReasoningEffort:r},{get:a})=>{"
        "let c=r&&a(Fh,`1186680773`),d=r&&a(Fh,`1186680773`)}"
    )
except RuntimeError as exc:
    if "found 2" not in str(exc):
        raise
else:
    raise AssertionError("multiple Ultra gates should fail loudly")

print("Patch S matcher tests passed.")
