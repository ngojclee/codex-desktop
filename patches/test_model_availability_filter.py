#!/usr/bin/env python3
import unittest

from patch_codex_asar_model_availability_filter import (
    FILTER_PATTERN,
    FILTER_PATTERN_V3,
    FILTER_PATTERN_V4,
    FILTER_PATTERN_V5,
    PATCHED_FILTER_PATTERN,
    PATCHED_FILTER_PATTERN_V3,
    PATCHED_FILTER_PATTERN_V4,
    PATCHED_FILTER_PATTERN_V5,
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

    def test_26_825_configured_catalog_layout(self):
        source = (
            "function x({availableModels:n,hasConfiguredModelCatalog:r,"
            "isCustomModelProvider:i,model:a,useHiddenModels:o}){"
            "return e?.has(a.model)===!0||a.model!==`codex-auto-review`&&"
            "(r&&!a.hidden||(o&&!i&&t!==`amazonBedrock`?n.has(a.model):!a.hidden))}"
        )
        patched, count = patch_filter_text(source)
        self.assertEqual(count, 1)
        self.assertIn(
            "r&&!a.hidden||(o&&!i&&t!==`amazonBedrock`?"
            "(n.has(a.model)||/*O*/!a.hidden):!a.hidden)",
            patched,
        )
        self.assertIsNone(FILTER_PATTERN_V4.search(patched))
        self.assertIsNotNone(PATCHED_FILTER_PATTERN_V4.search(patched))

    def test_26_1007_access_program_clause_layout(self):
        # Verbatim ternary head from 26.1007, including the access-program clause
        # that made the whole-expression V1-V4 matchers miss the filter entirely.
        cyber_clause = (
            "n===`apikey`&&t&&!o.hidden&&c?.some(e=>e!==`standard`)===!0"
        )
        source = (
            "function ki({additionalAvailableModels:e,authMethod:n,availableModels:r,"
            "isCustomModelProvider:t,model:o,useHiddenModels:s}){"
            "let c=o.availableAccessPrograms?.cyber;"
            "return n===`apikey`&&!t&&c!=null&&c.length>0&&!c.includes(`standard`)?!1:"
            "e?.has(o.model)===!0||o.model!==`codex-auto-review`&&"
            f"(i&&!o.hidden||(s&&!a&&n!==`amazonBedrock`?r.has(o.model)||{cyber_clause}:!o.hidden))"
            "}"
        )
        self.assertIsNotNone(FILTER_PATTERN_V5.search(source))
        patched, count = patch_filter_text(source)
        self.assertEqual(count, 1)
        self.assertIn(
            "n!==`amazonBedrock`?(r.has(o.model)||/*O*/!o.hidden)||" + cyber_clause,
            patched,
        )
        # The clause upstream added must survive untouched.
        self.assertIn(cyber_clause, patched)
        self.assertIsNone(FILTER_PATTERN_V5.search(patched))
        self.assertIsNotNone(PATCHED_FILTER_PATTERN_V5.search(patched))
        again, second_count = patch_filter_text(patched)
        self.assertEqual(second_count, 0)
        self.assertEqual(again, patched)


if __name__ == "__main__":
    unittest.main()
