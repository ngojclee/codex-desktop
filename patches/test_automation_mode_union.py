#!/usr/bin/env python3
"""Focused matcher, idempotence and drift tests for Patch Y.

The regression these tests exist for: the anchors used to hard-code minified
identifiers (`sWn`, `_Wn`, `bWn`, `vWn`, `cWn`) and the builder name `zh`.
Upstream renames those on every rebuild, which made the release fail with
"Could not find renderer automation mode validation unions" even though the
schema was intact. Renaming every identifier here must still patch cleanly.
"""

from patch_codex_asar_automation_mode_union import (
    PATCH_MARKER,
    find_flat_mode_unions,
    find_upstream_mode_unions,
    mode_union_state,
    patch_text,
)


# Upstream layout, real minified names as first observed.
SOURCE = (
    "sWn=Ih({mode:Uh(`view`),id:rWn}),"
    "cWn=Ih({mode:Uh(`delete`),id:rWn}),"
    "_Wn=zh(`mode`,[lWn,uWn]),"
    "yWn=zh(`mode`,[sWn,_Wn,vWn,cWn]).refine(e=>e.targetThreadId==null),"
    "bWn=zh(`kind`,[pWn,mWn]),"
    "xWn=zh(`mode`,[sWn,bWn,vWn,cWn]),"
    "SWn=yWn.transform(GUn),CWn=xWn.transform(GUn),"
)

# The same bundle after one upstream rebuild renamed every identifier. Nothing
# here but the structural enum literals may be relied upon.
RENAMED_SOURCE = (
    "aB1=qo({mode:Zr(`view`),id:xK9}),"
    "cD3=qo({mode:Zr(`delete`),id:xK9}),"
    "eF5=qo(`mode`,[gH7,iJ1]),"
    "kL2=qo(`mode`,[aB1,eF5,mN4,cD3]).refine(e=>e.targetThreadId==null),"
    "oP6=qo(`kind`,[qR8,sT0]),"
    "uV2=qo(`mode`,[aB1,oP6,mN4,cD3]),"
    "wX4=kL2.transform(yZ6),wA8=uV2.transform(yZ6),"
)


def assert_flattened(patched, expected_chains, label):
    """Assert both unions became plain chains, in the source's own member order."""
    if patched.count(PATCH_MARKER) != 2:
        raise AssertionError(f"{label}: expected two markers, got {patched.count(PATCH_MARKER)}")
    if find_upstream_mode_unions(patched):
        raise AssertionError(f"{label}: discriminated mode unions remain")
    flat = find_flat_mode_unions(patched)
    if len(flat) != 2:
        raise AssertionError(f"{label}: expected two flattened unions, got {len(flat)}")
    for entry in flat:
        if not entry["marked"]:
            raise AssertionError(f"{label}: flattened union {entry['target']} lacks the marker")
    # Member order must survive verbatim rather than being re-derived from a
    # remembered name list, which is the failure mode this patch replaces.
    for chain in expected_chains:
        if chain not in patched:
            raise AssertionError(f"{label}: expected chain {chain} was not produced")


patched, changed = patch_text(SOURCE)
if not changed:
    raise AssertionError("Patch Y should change the upstream layout")
assert_flattened(
    patched,
    [
        "yWn=sWn.or(_Wn).or(vWn).or(cWn)",
        "xWn=sWn.or(bWn).or(vWn).or(cWn)",
    ],
    "real-name layout",
)
if (
    ".or(cWn)"
    + PATCH_MARKER
    + ".refine(e=>e.targetThreadId==null)"
) not in patched:
    raise AssertionError("existing yWn refinement was not preserved")

# The CI-breaking case: identical structure, every name different.
renamed_patched, renamed_changed = patch_text(RENAMED_SOURCE)
if not renamed_changed:
    raise AssertionError("Patch Y must follow an upstream identifier rename")
assert_flattened(
    renamed_patched,
    [
        "kL2=aB1.or(eF5).or(mN4).or(cD3)",
        "uV2=aB1.or(oP6).or(mN4).or(cD3)",
    ],
    "renamed layout",
)

patched_again, changed_again = patch_text(patched)
if changed_again or patched_again != patched:
    raise AssertionError("Patch Y is not idempotent")

# A bundle that already ships both unions flat needs no edit at all, and must
# report upstream_safe rather than fail or rewrite anything.
SAFE_SOURCE = (
    "sWn=Ih({mode:Uh(`view`),id:rWn}),"
    "cWn=Ih({mode:Uh(`delete`),id:rWn}),"
    "yWn=sWn.or(_Wn).or(vWn).or(cWn),"
    "xWn=sWn.or(bWn).or(vWn).or(cWn),"
)
safe_state = mode_union_state(SAFE_SOURCE)
if safe_state["state"] != "upstream_safe":
    raise AssertionError(f"already-flat bundle should be upstream_safe, got {safe_state['state']}")
safe_text, safe_changed = patch_text(SAFE_SOURCE)
if safe_changed or safe_text != SAFE_SOURCE:
    raise AssertionError("upstream_safe bundle must not be modified")

# A look-alike `mode` union built from inline object literals (the annotation
# mode schema) must never be treated as an automation target.
DECOY_SOURCE = (
    "sWn=Ih({mode:Uh(`view`),id:rWn}),"
    "cWn=Ih({mode:Uh(`delete`),id:rWn}),"
    "yWn=zh(`mode`,[sWn,_Wn,vWn,cWn]),"
    "xWn=zh(`mode`,[sWn,bWn,vWn,cWn]),"
    "a9=zh(`mode`,[n.ql({mode:n.Wl(`create`)}),n.ql({mode:n.Wl(`edit`)})]),"
)
if len(find_upstream_mode_unions(DECOY_SOURCE)) != 2:
    raise AssertionError("inline-literal mode union must not be selected")

# One union missing means the layout moved in a way this patch cannot prove, so
# it must stop the release instead of shipping half the fix.
try:
    patch_text("sWn=Ih({mode:Uh(`view`),id:rWn}),cWn=Ih({mode:Uh(`delete`),id:rWn}),"
               "yWn=zh(`mode`,[sWn,_Wn,vWn,cWn]),")
except RuntimeError as exc:
    if "exactly two" not in str(exc):
        raise AssertionError(f"unexpected drift error: {exc}") from exc
else:
    raise AssertionError("incomplete upstream layout should fail loudly")

# With neither the member schemas nor a union present, silence is the worst
# outcome, so this must also fail loudly.
try:
    patch_text("const unrelated = zh(`mode`,[A,B,C]);")
except RuntimeError as exc:
    if "cannot classify" not in str(exc):
        raise AssertionError(f"unexpected no-schema error: {exc}") from exc
else:
    raise AssertionError("missing automation schema should fail loudly")

# A single marker means a previous run was interrupted mid-replacement.
try:
    one_marked = (
        "sWn=Ih({mode:Uh(`view`),id:rWn}),"
        "cWn=Ih({mode:Uh(`delete`),id:rWn}),"
        f"yWn=sWn.or(_Wn).or(vWn).or(cWn){PATCH_MARKER},"
        "xWn=zh(`mode`,[sWn,bWn,vWn,cWn]),"
    )
    patch_text(one_marked)
except RuntimeError as exc:
    if "at once" not in str(exc):
        raise AssertionError(f"unexpected partial-marker error: {exc}") from exc
else:
    raise AssertionError("partial marker should fail loudly")

if mode_union_state(SOURCE)["state"] != "unpatched":
    raise AssertionError("upstream layout must classify as unpatched")
if mode_union_state(patched)["state"] != "already_patched":
    raise AssertionError("patched layout must classify as already_patched")

print("Patch Y automation mode union tests passed.")
