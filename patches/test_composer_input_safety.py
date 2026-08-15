#!/usr/bin/env python3
"""Focused matcher tests for Patch U composer input safety."""
from patch_codex_asar_composer_input_safety import (
    PATCH_MARKERS,
    PASTE_ENTRY,
    PASTE_PARSE,
    PASTE_ENTRY_V2,
    PASTE_PARSE_V2,
    patch_text,
)


SOURCE = (
    "function nXa(){let q={rules:[...h?[EJa,DJa]:[],...i?["
    "oXa(/(?:^|\\s)(\\*\\*\\*([^*\\n]+)\\*\\*\\*)$/,[a,b]),"
    "oXa(/(?:^|\\s)(___([^_\\n]+)___)$/,[a,b]),"
    "oXa(/(?:^|\\s)(\\*\\*([^*\\n]+)\\*\\*)$/,[a]),"
    "oXa(/(?:^|\\s)(__([^_\\n]+)__)$/,[a]),"
    "oXa(/(?:^|\\s)(\\*([^*\\n]+)\\*)$/,[b]),"
    "oXa(/(?:^|\\s)(_([^_\\n]+)_)$/,[b])"
    "]:[],...i?[ybn(/^\\s*([-+*])\\s$/,c)]:[]};"
    "handlePaste(e,t){let s=`18d1d87f_a6c083_2a4d0c`,c=`<em>x</em>`,"
    + PASTE_ENTRY
    + PASTE_PARSE
    + "return g}}"
)

SOURCE_V2 = (
    "function Jto(){let C=i?JBn({rules:[..._?[Ceo,weo]:[],"
    "XBn(/^\\s*([-+*])\\s$/,vK.nodes.bullet_list)]}):null;"
    "handlePaste(e,t){let s=`18d1d87f_a6c083_2a4d0c`,"
    "c=`<strong>x</strong>`,v=!1;"
    + PASTE_ENTRY_V2
    + PASTE_PARSE_V2
    + "return m}}"
)


def assert_patched(name: str, source: str, upstream_fragments: tuple[str, ...]):
    patched, changed = patch_text(source)
    if not changed:
        raise AssertionError(f"{name}: expected Patch U to modify the fixture")
    if any(marker not in patched for marker in PATCH_MARKERS):
        raise AssertionError(f"{name}: missing Patch U marker")
    if any(fragment in patched for fragment in upstream_fragments):
        raise AssertionError(f"{name}: unpatched composer layout remains")
    if "e.dom.__codexLiteralPaste" not in patched:
        raise AssertionError(f"{name}: duplicate paste guard missing")
    patched_again, changed_again = patch_text(patched)
    if changed_again or patched_again != patched:
        raise AssertionError(f"{name}: Patch U is not idempotent")
    return patched


legacy_patched = assert_patched("legacy composer", SOURCE, (PASTE_ENTRY, PASTE_PARSE))
if "oXa(/(?:^|\\s)(_([^_\\n]+)_)$/" in legacy_patched:
    raise AssertionError("legacy composer: underscore auto-format rule remains")
if "b=!1,x=!1" not in legacy_patched:
    raise AssertionError("legacy composer: Markdown paste is still enabled")

current_patched = assert_patched(
    "26.810 composer",
    SOURCE_V2,
    (PASTE_ENTRY_V2, PASTE_PARSE_V2),
)
if "w=!1/*U:no-auto-inline-markdown*/" not in current_patched:
    raise AssertionError("26.810 composer: list/Markdown paste remains enabled")
if "enableRichText:!1/*U:literal-markdown-paste*/" not in current_patched:
    raise AssertionError("26.810 composer: rich Markdown conversion remains enabled")

print("Patch U composer input safety matcher tests passed.")
