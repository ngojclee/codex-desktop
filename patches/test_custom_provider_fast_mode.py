#!/usr/bin/env python3
"""Patch R matcher and executable capability-path regression tests."""
import json
import re
import shutil
import subprocess
import unittest

from patch_codex_asar_custom_provider_fast_mode import (
    MODERN_REQUEST_MARKER,
    MODERN_UI_MARKER,
    PATCH_MARKER,
    has_unpatched_gate,
    patch_text,
)


UI = (
    "function KVi(e){let t=(0,qVi.c)(10),n=G(Li),r=e?.hostId??n,"
    "i=BYe(r),a=i?.authMethod===`chatgpt`||i?.authMethod===`personalAccessToken`,"
    "o=i?.authMethod??null,s;t[0]!==r||t[1]!==o?(s={authMethod:o,hostId:r},"
    "t[0]=r,t[1]=o,t[2]=s):s=t[2];let{data:c,isPending:l}=Zs(Hd,s),"
    "u=!!i?.isLoading||a&&l,d;t[3]!==c||t[4]!==u||t[5]!==a?"
    "(d=a&&!u&&c!=null?oee(c):null,t[3]=c,t[4]=u,t[5]=a,t[6]=d):d=t[6];"
    "let f=d,p;return t[7]!==u||t[8]!==f?(p={serviceTierAccess:f,isLoading:u},"
    "t[7]=u,t[8]=f,t[9]=p):p=t[9],p}"
)
REQUEST = (
    "async function bga(e,t){let n=await _ga(e,t);"
    "if(n!==`chatgpt`&&n!==`personalAccessToken`)return null;"
    "let r=await KPe(e,t,{priority:`critical`});"
    "return e.query.setData(Hd,{authMethod:n,hostId:t},r),oee(r)}"
)
MODERN = UI + REQUEST
LEGACY = "let enabled=chatgpt&&!loading&&req!=null&&req?.requirements?.featureRequirements?.fast_mode!==!1;"


class FastModeTests(unittest.TestCase):
    def test_legacy_and_idempotency(self):
        patched, changed = patch_text(LEGACY)
        self.assertTrue(changed)
        self.assertIn("!loading&&(!chatgpt||req!=null&&", patched)
        self.assertIn(PATCH_MARKER, patched)
        self.assertEqual(patch_text(patched), (patched, False))

    def test_modern_pair_and_idempotency(self):
        patched, changed = patch_text(MODERN)
        self.assertTrue(changed)
        self.assertIn(MODERN_UI_MARKER, patched)
        self.assertIn(MODERN_REQUEST_MARKER, patched)
        self.assertIn("t[5]!==o", patched)
        self.assertIn("t[5]=o", patched)
        self.assertFalse(has_unpatched_gate(patched))
        self.assertEqual(patch_text(patched), (patched, False))

    def test_missing_request_gate_fails(self):
        with self.assertRaisesRegex(RuntimeError, "found 1 and 0"):
            patch_text(UI)

    def test_duplicate_gate_fails(self):
        with self.assertRaisesRegex(RuntimeError, "found 2 and 1"):
            patch_text(UI + MODERN)

    def test_helper_disagreement_fails(self):
        with self.assertRaisesRegex(RuntimeError, "share a capability helper"):
            patch_text(UI + REQUEST.replace("oee(r)", "other(r)"))

    def test_changed_auth_binding_fails(self):
        with self.assertRaisesRegex(RuntimeError, "entitlement flag"):
            patch_text(MODERN.replace("`personalAccessToken`", "`unknown`", 1))

    def test_partial_patch_fails(self):
        patched, _ = patch_text(MODERN)
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            patch_text(patched.replace(MODERN_REQUEST_MARKER, ""))

    def test_reminified_bindings(self):
        names = {"t": "cache", "i": "account", "a": "entitled", "o": "auth",
                 "c": "requirements", "u": "loading", "d": "capabilities"}
        renamed = re.sub(r"\b(?:" + "|".join(names) + r")\b", lambda m: names[m.group(0)], MODERN)
        patched, changed = patch_text(renamed)
        self.assertTrue(changed)
        self.assertEqual(patch_text(patched), (patched, False))

    def test_memo_drift_fails_even_with_markers(self):
        patched, _ = patch_text(MODERN)
        with self.assertRaisesRegex(RuntimeError, "shape drifted"):
            patch_text(patched.replace("t[5]=o", "t[5]=a"))

    def test_executable_ui_and_request_paths(self):
        patched, _ = patch_text(MODERN)
        self.assert_runtime_paths(MODERN, patched)

    def assert_runtime_paths(self, original: str, patched: str):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for behavioral Patch R tests")
        script = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const originalSource = ORIGINAL;
const patchedSource = PATCHED;
function runtime(source) {
  const state = { authMethod: null, isLoading: false, req: undefined, pending: false, reads: 0, writes: 0 };
  const cache = [];
  const context = {
    qVi: { c: () => cache }, Li: "host", Hd: "requirements", G: () => "local",
    BYe: () => state, Zs: () => ({ data: state.req, isPending: state.pending }),
    oee: (e) => {
      const requirements = e.requirements?.featureRequirements;
      const fast = requirements?.fast_mode !== false;
      return { fast, ultrafast: (e.supportsIndependentSpeedModes === true || fast) && requirements?.ultrafast_mode !== false };
    },
    _ga: async () => state.authMethod,
    KPe: async () => { state.reads++; return state.req; },
  };
  const scope = { query: { setData: () => { state.writes++; } } };
  return { state, scope, ...vm.runInNewContext(source + ";({KVi,bga})", context) };
}
function plain(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
async function main() {
  // Actual bundled caller bodies with stubbed account/query services, not UI or provider acceptance.
  for (const authMethod of ["chatgpt", "personalAccessToken", "copilot", "amazonBedrock", null]) {
    for (const req of [undefined, {}, { requirements: { featureRequirements: { fast_mode: false, ultrafast_mode: false } } }]) {
      for (const isLoading of [false, true]) {
        for (const pending of [false, true]) {
          const before = runtime(originalSource), after = runtime(patchedSource);
          Object.assign(before.state, { authMethod, req, isLoading, pending });
          Object.assign(after.state, { authMethod, req, isLoading, pending });
          assert.deepEqual(plain(after.KVi().serviceTierAccess), plain(before.KVi().serviceTierAccess));
          // A missing ChatGPT requirements reply is not a valid remote response;
          // leave its upstream failure behavior unchanged.
          if (req !== undefined || !["chatgpt", "personalAccessToken"].includes(authMethod)) {
            assert.deepEqual(plain(await after.bga(after.scope, "local")), plain(await before.bga(before.scope, "local")));
          }
        }
      }
    }
  }
  const app = runtime(patchedSource);
  Object.assign(app.state, { authMethod: "apikey", req: undefined, pending: true });
  assert.deepEqual(plain(app.KVi().serviceTierAccess), { fast: true, ultrafast: true });
  assert.deepEqual(plain(await app.bga(app.scope, "local")), { fast: true, ultrafast: true });
  assert.equal(app.state.reads, 0, "API-key path must not fetch ChatGPT entitlements");
  assert.equal(app.state.writes, 0, "API-key path must not poison the ChatGPT query cache");
  app.state.isLoading = true;
  assert.equal(app.KVi().serviceTierAccess, null);
  app.state.isLoading = false;
  app.state.authMethod = "copilot";
  assert.equal(app.KVi().serviceTierAccess, null);
  app.state.authMethod = "apikey";
  assert.deepEqual(plain(app.KVi().serviceTierAccess), { fast: true, ultrafast: true }, "memo must track authMethod, not only ChatGPT boolean");
  app.state.authMethod = "chatgpt";
  app.state.pending = false;
  app.state.req = { requirements: { featureRequirements: { fast_mode: false, ultrafast_mode: false } } };
  assert.deepEqual(plain(app.KVi().serviceTierAccess), { fast: false, ultrafast: false });
  assert.deepEqual(plain(await app.bga(app.scope, "local")), { fast: false, ultrafast: false });
}
main().catch(error => { console.error(error); process.exitCode = 1; });
"""
        script = script.replace("ORIGINAL", json.dumps(original)).replace("PATCHED", json.dumps(patched))
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
