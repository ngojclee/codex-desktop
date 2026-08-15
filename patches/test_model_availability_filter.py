#!/usr/bin/env python3
import unittest

from patch_codex_asar_model_availability_filter import (
    FILTER_PATTERN,
    FILTER_PATTERN_V3,
    PATCHED_FILTER_PATTERN,
    PATCHED_FILTER_PATTERN_V3,
    PATCH_MARKER,
    patch_filter_text,
)


class ModelAvailabilityFilterTests(unittest.TestCase):
    def assert_patches_once(self, source: str):
        patched, count = patch_filter_text(source)
        self.assertEqual(count, 1)
        self.assertIn(PATCH_MARKER, patched)
        self.assertIsNone(FILTER_PATTERN.search(patched))
        self.assertIsNotNone(PATCHED_FILTER_PATTERN.search(patched))
        return patched

    def test_26_715_direct_filter_layout(self):
        patched = self.assert_patches_once(
            "function f(){if(s?t.has(n.model):!n.hidden)return n}"
        )
        self.assertIn(
            "s?(t.has(n.model)||/*O*/!n.hidden):!n.hidden",
            patched,
        )

    def test_26_721_additional_models_wrapper_layout(self):
        patched = self.assert_patches_once(
            "if(e?.has(r.model)===!0||(u?n.has(r.model):!r.hidden)){c.push(r)}"
        )
        self.assertIn(
            "e?.has(r.model)===!0||(u?(n.has(r.model)||/*O*/!r.hidden):!r.hidden)",
            patched,
        )

    def test_upstream_safe_filter_is_recognized(self):
        source = "if(u?(n.has(r.model)||!r.hidden):!r.hidden){c.push(r)}"
        patched, count = patch_filter_text(source)
        self.assertEqual(count, 0)
        self.assertEqual(patched, source)
        self.assertIsNotNone(PATCHED_FILTER_PATTERN.search(source))

    def test_26_810_custom_provider_layout(self):
        source = (
            "function vti({additionalAvailableModels:e,authMethod:t,"
            "availableModels:n,isCustomModelProvider:r,model:i,useHiddenModels:a}){"
            "return e?.has(i.model)===!0||i.model!==`codex-auto-review`&&"
            "(a&&!r&&t!==`amazonBedrock`?n.has(i.model):!i.hidden)}"
        )
        patched, count = patch_filter_text(source)
        self.assertEqual(count, 1)
        self.assertIn(
            "a&&!r&&t!==`amazonBedrock`?"
            "(n.has(i.model)||/*O*/!i.hidden):!i.hidden",
            patched,
        )
        self.assertIsNone(FILTER_PATTERN_V3.search(patched))
        self.assertIsNotNone(PATCHED_FILTER_PATTERN_V3.search(patched))

    def test_unrelated_set_filter_is_ignored(self):
        source = "if(flag?allowed.has(item.id):!item.hidden){items.push(item)}"
        patched, count = patch_filter_text(source)
        self.assertEqual(count, 0)
        self.assertEqual(patched, source)


if __name__ == "__main__":
    unittest.main()
