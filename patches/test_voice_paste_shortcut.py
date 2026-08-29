#!/usr/bin/env python3
"""Focused matcher tests for Patch T's Voice Mode shortcut removal."""

from patch_codex_asar_voice_paste_shortcut import (
    PATCH_MARKER,
    UPSTREAM_PATTERN,
    UPSTREAM_SAFE_PATTERN,
    patch_text,
)


def assert_patched(name: str, source: str):
    patched, changed = patch_text(source)
    if not changed:
        raise AssertionError(f"{name}: expected a change")
    if "Ctrl+Shift+V" in patched:
        raise AssertionError(f"{name}: conflicting shortcut remains")
    if PATCH_MARKER not in patched:
        raise AssertionError(f"{name}: patch marker missing")
    if "composer.startVoiceMode" not in patched:
        raise AssertionError(f"{name}: Voice Mode command was removed")

    patched_again, changed_again = patch_text(patched)
    if changed_again or patched_again != patched:
        raise AssertionError(f"{name}: patch is not idempotent")


assert_patched(
    "current backtick bundle",
    "{id:`composer.startVoiceMode`,titleIntlId:`codex.command.composer.startVoiceMode`,"
    "descriptionIntlId:`codex.commandDescription.composer.startVoiceMode`,"
    "shortcutScope:`app`,electron:{defaultKeybindings:[{key:`Ctrl+Shift+V`}]}}",
)
assert_patched(
    "double-quoted bundle",
    '{"id":"composer.startVoiceMode","titleIntlId":"voice",'
    '"shortcutScope":"app","electron":{"defaultKeybindings":[{"key":"Ctrl+Shift+V"}]}}',
)
assert_patched(
    "single-quoted bundle",
    "{id:'composer.startVoiceMode',titleIntlId:'voice',shortcutScope:'app',"
    "electron:{defaultKeybindings:[{key:'Ctrl+Shift+V'}]}}",
)

# 26.825 scopes the binding per platform and leaves non-macOS empty, which is
# already the outcome Patch T produces on Windows: it must be detected as safe
# and never match the legacy removal pattern.
PLATFORM_SCOPED = (
    "{id:`composer.startVoiceMode`,titleIntlId:`codex.command.composer.startVoiceMode`,"
    "descriptionIntlId:`codex.commandDescription.composer.startVoiceMode`,"
    "shortcutScope:`app`,electron:{platformDefaultKeybindings:"
    "{macOS:[{key:`Ctrl+Shift+V`}],default:[]}}}"
)
if not UPSTREAM_SAFE_PATTERN.search(PLATFORM_SCOPED):
    raise AssertionError("platform-scoped bundle: safe layout not recognized")
if UPSTREAM_PATTERN.search(PLATFORM_SCOPED):
    raise AssertionError("platform-scoped bundle: legacy removal pattern matched")

# A platform-scoped bundle that still binds Windows must NOT look safe.
WINDOWS_STILL_BOUND = PLATFORM_SCOPED.replace("default:[]", "default:[{key:`Ctrl+Shift+V`}]")
if UPSTREAM_SAFE_PATTERN.search(WINDOWS_STILL_BOUND):
    raise AssertionError("platform-scoped bundle: Windows binding misread as safe")

print("Patch T Voice Mode shortcut matcher tests passed.")
