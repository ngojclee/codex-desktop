#!/usr/bin/env python3
"""Patch U 26.1007 shape, drift, preservation, and executable paste tests."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from patch_codex_asar_composer_input_safety import (
    PATCH_MARKERS,
    V6_MARKER,
    V6_PASTE_BLOCK_RE,
    patch_text,
)


SOURCE = (Path(__file__).parent / "fixtures/composer_261007.js").read_text(encoding="utf-8")


def runtime_assertions(source: str):
    """Also used to drive the actual copied ASAR paste body during acceptance."""
    node = shutil.which("node")
    if node is None:
        raise AssertionError("Node is required for executable composer tests")
    script = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const source = SOURCE;
function runtime() {
  const state = { now: 1000, inserted: [], events: [], prevented: 0, page: false, special: false,
    media: { files: [], imageFiles: [], otherFiles: [] }, code: false };
  const context = {
    S: { listInput: "list", codeBlockFenceExit: "fence", inputRules: "inline-rules", inputRulesHistoryIsolation: "rule-history" },
    M: { consume: () => false }, Date: { now: () => state.now }, j: false, o: false,
    $Lc: () => state.page,
    kma: () => state.media,
    mma: (text, html, images) => images.length > 0,
    T: { emit: (name, data) => { state.events.push({name, data}); return false; } },
    JRe: () => { throw Error("rich HTML parse must be disabled"); },
    DJr: () => { throw Error("HTML-to-Markdown conversion must be disabled"); },
    x: { pastePreferredHtml: () => { throw Error("rich HTML fallback must be disabled"); }, pasteLiteralText: () => false },
    FVr: () => "code", il: value => value,
    PRc: (view, text) => { if (!state.code) return false; state.inserted.push(text); return true; },
    CJr: () => state.special,
    dc: () => null, sm: { getState: () => null }, hHr: () => null,
    nUr: (view, text) => state.inserted.push(text),
  };
  const tr = { scrollIntoView() { return this; } };
  const view = { dom: {}, dispatch() {}, state: { tr, schema: {},
    selection: { empty: false, $from: { parent: { type: { spec: { get code() { return state.code; } } } } } } } };
  const tool = vm.runInNewContext(source + ";({paste:props.handlePaste,plugins:composerPlugins})", context);
  function paste(text, html = "<b>incidental formatting</b>") {
    return tool.paste(view, {
      defaultPrevented: false,
      preventDefault() { state.prevented++; },
      clipboardData: { getData: type => type === "text/plain" ? text : html },
    });
  }
  return {state, view, paste, plugins:tool.plugins};
}
let app = runtime();
assert.deepEqual(Array.from(app.plugins), ["list", "fence"]);
assert.equal(app.paste("identifier_a_b **literal**"), true);
assert.deepEqual(app.state.inserted, ["identifier_a_b **literal**"]);
app.state.now = 1100;
assert.equal(app.paste("identifier_a_b **literal**"), true);
assert.equal(app.state.prevented, 1);
assert.equal(app.state.inserted.length, 1);
app.state.now = 1301;
app.paste("identifier_a_b **literal**");
assert.equal(app.state.inserted.length, 2);
app = runtime();
const largeText = "_".repeat(6000), largeHtml = "<p>" + "x".repeat(100001) + "</p>";
app.paste(largeText, largeHtml);
assert.deepEqual(app.state.inserted, [largeText]);
app = runtime();
app.state.media = {files:["image"], imageFiles:["image"], otherFiles:[]};
app.paste("", "");
assert.equal(app.state.events[0].name, "pasted-images");
assert.equal(app.state.inserted.length, 0);
assert.equal(app.view.dom.__codexLiteralPaste, undefined);
app = runtime();
app.state.media = {files:["file"], imageFiles:[], otherFiles:["file"]};
app.paste("", "");
assert.equal(app.state.events[0].name, "pasted-files");
assert.equal(app.view.dom.__codexLiteralPaste, undefined);
app = runtime();
app.state.page = true;
app.paste("page context");
assert.equal(app.view.dom.__codexLiteralPaste, undefined);
app = runtime();
app.state.special = true;
assert.equal(app.paste("library reference"), true);
assert.equal(app.state.inserted.length, 0);
app = runtime();
app.state.code = true;
assert.equal(app.paste("literal_in_code\r\nnext"), true);
assert.deepEqual(app.state.inserted, ["literal_in_code\nnext"]);
"""
    result = subprocess.run(
        [node, "-e", script.replace("SOURCE", json.dumps(source))],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)


class ModernComposerTests(unittest.TestCase):
    def test_patch_and_idempotency(self):
        patched, changed = patch_text(SOURCE)
        self.assertTrue(changed)
        for marker in (*PATCH_MARKERS, V6_MARKER):
            self.assertIn(marker, patched)
        self.assertNotIn(".pastePreferredHtml(", patched)
        self.assertNotIn("S.inputRules", patched)
        self.assertEqual(patch_text(patched), (patched, False))

    def test_reminified_plugin_and_modes(self):
        source = re.sub(r"\bS\b", "pluginStack", SOURCE)
        source = re.sub(r"\bj\b", "plainMode", source)
        source = re.sub(r"\bn\b", "consumedPaste", source)
        patched, changed = patch_text(source)
        self.assertTrue(changed)
        self.assertIn("pluginStack.codeBlockFenceExit/*U:no-auto-inline-markdown*/", patched)
        self.assertIn("if((plainMode||consumedPaste)&&c.length===0)return!0;", patched)
        self.assertEqual(patch_text(patched), (patched, False))

    def test_file_reference_and_code_paths_preserved(self):
        patched, _ = patch_text(SOURCE)
        for fragment in (
            "if(!n&&$Lc(e,r))return!0;",
            "T.emit(`pasted-images`,a)",
            "T.emit(`pasted-files`,{files:i,dataTransfer:r})",
            "if(PRc(e,c))return e.dispatch(e.state.tr.scrollIntoView()),!0;",
            "if(CJr(e,l,c))return!0;",
            "T.emit(`pasted-link`,new pBc(c,l??``))",
            "hHr({displayText:c,href:c})",
        ):
            self.assertIn(fragment, patched)

    def test_missing_large_fallback_fails(self):
        with self.assertRaisesRegex(RuntimeError, "fallback layout drifted"):
            patch_text(SOURCE.replace("x?.pastePreferredHtml(e,l.trim(),c)", "other(e,c)"))

    def test_missing_plugins_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Incomplete 26.1007"):
            patch_text(SOURCE.replace("S.inputRulesHistoryIsolation", "S.other"))

    def test_duplicate_body_fails(self):
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            patch_text(SOURCE + SOURCE)

    def test_partial_marker_does_not_hide_rich_path(self):
        patched, _ = patch_text(SOURCE)
        broken = patched.replace(
            "x?.pasteLiteralText(e,c)/*U:literal-markdown-paste*/?!0:",
            "x?.pastePreferredHtml(e,l,c)||x?.pasteLiteralText(e,c)/*U:literal-markdown-paste*/?!0:",
        )
        with self.assertRaisesRegex(RuntimeError, "unpatched rich"):
            patch_text(broken)

    def test_executable_paste_paths(self):
        patched, _ = patch_text(SOURCE)
        runtime_assertions(patched)


if __name__ == "__main__":
    unittest.main()
