#!/usr/bin/env python3
"""Focused regression tests for Patch G's loopback-safe classification."""

import unittest

if __package__:
    from .patch_codex_asar_ws_socks_bypass import SOCKS_LITERAL, patch_js
else:
    from patch_codex_asar_ws_socks_bypass import SOCKS_LITERAL, patch_js


class PatchWsSocksBypassTests(unittest.TestCase):
    def test_legacy_hardcoded_proxy_is_removed(self):
        source = (
            "new Ws(this.options.websocketUrl,{headers:{},"
            f"agent:new P.SocksProxyAgent(`{SOCKS_LITERAL}`)}})"
        ).encode()

        patched, info = patch_js(source)

        self.assertEqual(info, {"status": "patched", "replaced": 1})
        self.assertNotIn(SOCKS_LITERAL.encode(), patched)

    def test_26930_guard_is_recognized_as_upstream_loopback_safe(self):
        source = (
            f"var T5=`{SOCKS_LITERAL}`;"
            "function sqe(e,t){let n=new URL(t).hostname;"
            "if(n!==`localhost`&&n!==`127.0.0.1`&&n!==`[::1]`)"
            "return r.Rn(e.id)?n.endsWith(`.internal.api.openai.org`)?T5:void 0:T5}"
        ).encode()

        patched, info = patch_js(source)

        self.assertEqual(patched, source)
        self.assertEqual(info, {"status": "upstream_loopback_safe", "replaced": 0})

    def test_unrecognized_proxy_literal_still_fails_loudly(self):
        with self.assertRaisesRegex(RuntimeError, "pattern did not match"):
            patch_js(f"const proxy=`{SOCKS_LITERAL}`;".encode())


if __name__ == "__main__":
    unittest.main()
