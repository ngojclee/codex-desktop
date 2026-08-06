#!/usr/bin/env python3
"""Regression tests for Patch H's markdown directive matcher."""

from patch_codex_asar_directive_windows_path import (
    MARKER,
    patch_js,
)


def test_upstream_26_730_parser_shape():
    source = (
        "function iQn(e,t){let n=t?.lineStartNames==null?e:sQn(e,t.lineStartNames);"
        "if(n==null)return[];let r=[];"
        "return oQn(PC(n,void 0),r,t?.includeListItems===!0),"
        "ap.debug(`[parseDirectives] directives found`,"
        "{safe:{directiveCount:r.length,directiveNames:r.map(e=>e.name).join(`,`)},"
        "sensitive:{}}),r}"
    )

    patched, info = patch_js(source)

    assert info["status"] == "patched_generic_split_directive_prefix"
    assert info["function"] == "iQn"
    assert MARKER in patched
    assert "n=(globalThis.__PATCH_H_DIRECTIVE_WINDOWS_PATH__=!0," in patched
    assert "includeListItems===!0" in patched


def test_previous_parser_shape_still_uses_existing_matcher():
    source = (
        "function ss(e,t){let n=t?.lineStartNames==null?e:us(e,t.lineStartNames);"
        "if(n==null)return[];let r=[];"
        "return ls(Bo(n,void 0),r),"
        "l.debug(`[parseDirectives] directives found`,"
        "{safe:{directiveCount:r.length,directiveNames:r.map(e=>e.name).join(`,`)},"
        "sensitive:{}}),r}"
    )

    patched, info = patch_js(source)

    assert info["status"] == "patched"
    assert MARKER in patched


if __name__ == "__main__":
    test_upstream_26_730_parser_shape()
    test_previous_parser_shape_still_uses_existing_matcher()
    print("Patch H directive matcher tests passed.")
