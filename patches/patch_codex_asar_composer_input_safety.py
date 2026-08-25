#!/usr/bin/env python3
"""Patch U - keep Windows composer input literal and deduplicate paste events.

Codex Desktop's rich composer has three interactions that are inconvenient for
technical prompts:

* inline ProseMirror rules turn `*`, `**`, `_`, `__`, `***`, and `___` into
  active formatting while typing;
* HTML/Markdown paste can import strong/em/list marks into the editor;
* the same clipboard payload can reach the composer twice in a very short
  interval, leaving a duplicate that a single Ctrl+Z removes.

Patch U keeps ordinary text literal, including identifiers containing `_`.
Recognized Codex links and mentions remain available, but they no longer carry
incidental bold/italic marks from pasted Markdown or HTML. Files, images,
explicit keyboard formatting shortcuts, and normal Ctrl+V remain intact.
"""
import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from patch_codex_asar_model_availability_filter import (
    extract,
    read_header,
    repack,
    walk,
)


PATCH_MARKER_INPUT_RULES = "/*U:no-auto-inline-markdown*/"
PATCH_MARKER_DEDUPE = "/*U:paste-dedupe*/"
PATCH_MARKER_HTML = "/*U:plain-html-paste*/"
PATCH_MARKER_MARKDOWN = "/*U:literal-markdown-paste*/"
PATCH_MARKERS = (
    PATCH_MARKER_INPUT_RULES,
    PATCH_MARKER_DEDUPE,
    PATCH_MARKER_HTML,
    PATCH_MARKER_MARKDOWN,
)

INPUT_RULES_PREFIX = "...h?[EJa,DJa]:[],...i?["
INPUT_RULES_SUFFIX = "]:[],...i?[ybn("
PATCHED_INPUT_RULES = (
    "...h?[EJa,DJa]:[],...i?[]"
    f"{PATCH_MARKER_INPUT_RULES}:[],...i?[ybn("
)
INPUT_RULES_PATTERN = re.compile(
    re.escape(INPUT_RULES_PREFIX) + r"(?P<rules>.*?)" + re.escape(INPUT_RULES_SUFFIX)
)

PASTE_ENTRY = (
    "if(s==null)return!1;"
    "let g=c==null||c.length===0?null:jYa({html:c,schema:e.state.schema,text:s});"
)
PATCHED_PASTE_ENTRY = (
    "if(s==null)return!1;"
    "let A=Date.now(),B=e.dom.__codexLiteralPaste;"
    "if(B!=null&&B.text===s&&A-B.at<250)return t.preventDefault(),!0;"
    f"e.dom.__codexLiteralPaste={{at:A,text:s}};{PATCH_MARKER_DEDUPE}"
    f"let g=null{PATCH_MARKER_HTML};"
)

PASTE_PARSE = (
    "let y=e9r(s)||l&&t9r(s),b=i&&fXa(s),x=i&&n9r(s);"
    "if(y||b||x){let t=y?"
    "c9r({schema:e.state.schema,text:s,enableCodeBlocks:h,enableRichText:i,"
    "restoreMarkdownLinksAsTextLinks:l,restorePathLinksAsFileMentions:u}):"
    "i9r({schema:e.state.schema,text:s,enableCodeBlocks:h,enableRichText:i}),"
    "n=+!b;"
)
PATCHED_PASTE_PARSE = (
    "let y=e9r(s)||l&&t9r(s),b=!1,x=!1"
    f"{PATCH_MARKER_MARKDOWN};"
    "if(y||b||x){let t=y?"
    "c9r({schema:e.state.schema,text:s,enableCodeBlocks:h,enableRichText:!1"
    f"{PATCH_MARKER_MARKDOWN},"
    "restoreMarkdownLinksAsTextLinks:l,restorePathLinksAsFileMentions:u}):"
    "i9r({schema:e.state.schema,text:s,enableCodeBlocks:h,enableRichText:!1}),"
    "n=+!b;"
)

PASTE_ENTRY_V2 = (
    "if(s==null)return!1;if(v&&s.length===0)return!0;"
    "let m=v||c==null||c.length===0?null:"
    "Eto({html:c,schema:e.state.schema,text:s}),"
    "h=e.state.selection.$from.parent.type.spec.code===!0,_;"
)
PATCHED_PASTE_ENTRY_V2 = (
    "if(s==null)return!1;if(v&&s.length===0)return!0;"
    "let A=Date.now(),B=e.dom.__codexLiteralPaste;"
    "if(B!=null&&B.text===s&&A-B.at<250)return t.preventDefault(),!0;"
    f"e.dom.__codexLiteralPaste={{at:A,text:s}};{PATCH_MARKER_DEDUPE}"
    f"let m=null{PATCH_MARKER_HTML},"
    "h=e.state.selection.$from.parent.type.spec.code===!0,_;"
)

PASTE_PARSE_V2 = (
    "let C=dui(s)||u&&fui(s),w=i&&nno(s);if(C||w){let t=C?"
    "vui({schema:e.state.schema,text:s,enableCodeBlocks:y,enableRichText:i,"
    "restoreMarkdownLinksAsTextLinks:u,restorePathLinksAsFileMentions:d}):"
    "mui({schema:e.state.schema,text:s,enableCodeBlocks:y,enableRichText:i}),"
    "n=+!w;"
)
PATCHED_PASTE_PARSE_V2 = (
    "let C=dui(s)||u&&fui(s),w=!1"
    f"{PATCH_MARKER_INPUT_RULES};if(C||w){{let t=C?"
    "vui({schema:e.state.schema,text:s,enableCodeBlocks:y,enableRichText:!1"
    f"{PATCH_MARKER_MARKDOWN},"
    "restoreMarkdownLinksAsTextLinks:u,restorePathLinksAsFileMentions:d}):"
    "mui({schema:e.state.schema,text:s,enableCodeBlocks:y,enableRichText:i}),"
    "n=+!w;"
)

V3_INPUT_RULES = "...y?[PLa,FLa]:[],"
V3_PASTE_ENTRY = (
    "let h=b||c==null||c.length===0?null:RRa({enableHtmlLists:i,html:c,"
    "schema:e.state.schema,text:s}),"
)
V3_MARKDOWN_EDITOR = "if(l!=null){let t=l.parse(s),"
V3_MARKDOWN_PARSE = (
    "let E=Xpr(s)||d&&Zpr(s),D=i&&_za(s);"
    "if(!_&&(E||D)){"
)
V3_PLAIN_PASTE_PREFIX = (
    "if(s==null)return!1;if(b&&s.length===0)return!0;"
)
PATCHED_V3_PLAIN_PASTE_PREFIX = (
    "if(s==null)return!1;"
    "let A=Date.now(),B=e.dom.__codexLiteralPaste;"
    "if(B!=null&&B.text===s&&A-B.at<250)return t.preventDefault(),!0;"
    f"e.dom.__codexLiteralPaste={{at:A,text:s}};{PATCH_MARKER_DEDUPE}"
    "if(b&&s.length===0)return!0;"
)

# 26.818 (bundle app-initial-BhpTek7p.js) rebuilt the composer on top of the
# PMU extension stack. Plain text is `o`, HTML is `s`, plain mode is `C`,
# and the Markdown branch already parses with `enableRichText:!1` by default;
# the input rules now arrive as a PMU plugin (`v.inputRules`) in the editor
# plugins array.
V4_PLUGINS = "v.inputRules,v.inputRulesHistoryIsolation"
PATCHED_V4_PLUGINS = (
    f"{PATCH_MARKER_INPUT_RULES}v.inputRulesHistoryIsolation"
)
V4_PASTE_PREFIX = (
    "if(o==null)return!1;if(C&&o.length===0)return!0;"
)
PATCHED_V4_PASTE_PREFIX = (
    "if(o==null)return!1;"
    "let A=Date.now(),B=e.dom.__codexLiteralPaste;"
    "if(B!=null&&B.text===o&&A-B.at<250)return t.preventDefault(),!0;"
    f"e.dom.__codexLiteralPaste={{at:A,text:o}};{PATCH_MARKER_DEDUPE}"
    "if(C&&o.length===0)return!0;"
)
# The 26.818 composer gets re-minified on successive upstream rebuilds, which
# shuffles the short helper names (SGa/rNn became wGa/lNn between 41705 and
# 61809). Match the HTML parse structurally instead of by those unstable names.
V4_HTML_RE = re.compile(
    r"let d=!C&&s!=null&&s\.length>0&&s\.length<=[A-Za-z_$][A-Za-z0-9_$]*"
    r"&&\(_==null\|\|o\.length>=5e3\)&&/<\(\?:a\|ol\|ul\)\\b/i\.test\(s\)"
    r"\?[A-Za-z_$][A-Za-z0-9_$]*\(e,s\):null"
)
PATCHED_V4_HTML = f"let d=null{PATCH_MARKER_HTML}"
V4_MARKDOWN = (
    "text:o,restoreMarkdownLinksAsTextLinks:f,"
    "restorePathLinksAsFileMentions:p"
)
PATCHED_V4_MARKDOWN = (
    f"text:o,enableRichText:!1{PATCH_MARKER_MARKDOWN},"
    "restoreMarkdownLinksAsTextLinks:f,"
    "restorePathLinksAsFileMentions:p"
)


def _inline_rules_match(text: str):
    matches = []
    for match in INPUT_RULES_PATTERN.finditer(text):
        rules = match.group("rules")
        if (
            rules.count("oXa(") == 6
            and "***/" not in rules
            and "\\*\\*\\*" in rules
            and "___" in rules
            and "\\*\\*" in rules
            and "__" in rules
        ):
            matches.append(match)
    return matches


def find_targets(asar: Path):
    header, payload_start = read_header(asar)
    targets = []
    for path, meta in walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        text = extract(asar, payload_start, meta).decode("utf-8", "replace")
        if (
            PATCH_MARKER_INPUT_RULES in text
            or PASTE_ENTRY in text
            or PATCHED_PASTE_ENTRY in text
            or PASTE_PARSE in text
            or PATCHED_PASTE_PARSE in text
            or PASTE_ENTRY_V2 in text
            or PATCHED_PASTE_ENTRY_V2 in text
            or PASTE_PARSE_V2 in text
            or PATCHED_PASTE_PARSE_V2 in text
            or V3_INPUT_RULES in text
            or V3_PASTE_ENTRY in text
            or V3_MARKDOWN_EDITOR in text
            or V3_MARKDOWN_PARSE in text
            or V4_PLUGINS in text
            or V4_PASTE_PREFIX in text
            or V4_HTML_RE.search(text) is not None
            or V4_MARKDOWN in text
            or _inline_rules_match(text)
        ):
            targets.append((path, meta, text))
    return header, payload_start, targets


def patch_text(text: str):
    if all(marker in text for marker in PATCH_MARKERS):
        return text, False

    matches = _inline_rules_match(text)
    has_v1 = (
        len(matches) > 0
        or PASTE_ENTRY in text
        or PASTE_PARSE in text
    )
    has_v2 = (
        PASTE_ENTRY_V2 in text
        or PASTE_PARSE_V2 in text
    )
    has_v3 = (
        V3_INPUT_RULES in text
        or V3_PASTE_ENTRY in text
        or V3_MARKDOWN_EDITOR in text
        or V3_MARKDOWN_PARSE in text
    )
    has_v4 = (
        V4_PLUGINS in text
        or V4_PASTE_PREFIX in text
        or V4_HTML_RE.search(text) is not None
        or V4_MARKDOWN in text
    )
    if has_v4 and (has_v1 or has_v2 or has_v3):
        raise RuntimeError("Found multiple composer layouts")
    if has_v1 and has_v2:
        raise RuntimeError("Found both legacy and current composer layouts")
    if has_v3 and (has_v1 or has_v2):
        raise RuntimeError("Found multiple composer layouts")

    if has_v4:
        if text.count(V4_PLUGINS) != 1:
            raise RuntimeError(
                "Expected exactly one 26.818 input-rules plugin list, "
                f"found {text.count(V4_PLUGINS)}"
            )
        if text.count(V4_PASTE_PREFIX) != 1:
            raise RuntimeError(
                "Expected exactly one 26.818 paste-entry prefix, "
                f"found {text.count(V4_PASTE_PREFIX)}"
            )
        html_count = len(V4_HTML_RE.findall(text))
        if html_count != 1:
            raise RuntimeError(
                "Expected exactly one 26.818 rich HTML parse, "
                f"found {html_count}"
            )
        if text.count(V4_MARKDOWN) != 1:
            raise RuntimeError(
                "Expected exactly one 26.818 Markdown parse call, "
                f"found {text.count(V4_MARKDOWN)}"
            )

        patched = text.replace(V4_PLUGINS, PATCHED_V4_PLUGINS, 1)
        patched = patched.replace(V4_PASTE_PREFIX, PATCHED_V4_PASTE_PREFIX, 1)
        patched = V4_HTML_RE.sub(lambda _: PATCHED_V4_HTML, patched, count=1)
        patched = patched.replace(V4_MARKDOWN, PATCHED_V4_MARKDOWN, 1)
        return patched, True

    if has_v1:
        if len(matches) != 1:
            raise RuntimeError(
                "Expected exactly one legacy inline Markdown input-rule list, "
                f"found {len(matches)}"
            )
        if text.count(PASTE_ENTRY) != 1:
            raise RuntimeError(
                "Expected exactly one legacy composer paste-entry layout, "
                f"found {text.count(PASTE_ENTRY)}"
            )
        if text.count(PASTE_PARSE) != 1:
            raise RuntimeError(
                "Expected exactly one legacy composer Markdown-paste layout, "
                f"found {text.count(PASTE_PARSE)}"
            )

        match = matches[0]
        patched = (
            text[: match.start()]
            + PATCHED_INPUT_RULES
            + text[match.end() :]
        )
        patched = patched.replace(PASTE_ENTRY, PATCHED_PASTE_ENTRY, 1)
        patched = patched.replace(PASTE_PARSE, PATCHED_PASTE_PARSE, 1)
        return patched, True

    if has_v2:
        if text.count(PASTE_ENTRY_V2) != 1:
            raise RuntimeError(
                "Expected exactly one current composer paste-entry layout, "
                f"found {text.count(PASTE_ENTRY_V2)}"
            )
        if text.count(PASTE_PARSE_V2) != 1:
            raise RuntimeError(
                "Expected exactly one current composer Markdown-paste layout, "
                f"found {text.count(PASTE_PARSE_V2)}"
            )

        patched = text.replace(PASTE_ENTRY_V2, PATCHED_PASTE_ENTRY_V2, 1)
        patched = patched.replace(PASTE_PARSE_V2, PATCHED_PASTE_PARSE_V2, 1)
        return patched, True

    if has_v3:
        required = (
            V3_INPUT_RULES,
            V3_PASTE_ENTRY,
            V3_MARKDOWN_EDITOR,
            V3_MARKDOWN_PARSE,
        )
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            raise RuntimeError(
                "Incomplete 26.814 composer layout; missing "
                + ", ".join(repr(fragment[:40]) for fragment in missing)
            )
        if text.count(V3_INPUT_RULES) != 1:
            raise RuntimeError(
                "Expected exactly one 26.814 inline Markdown input-rule list, "
                f"found {text.count(V3_INPUT_RULES)}"
            )
        if text.count(V3_PASTE_ENTRY) != 1:
            raise RuntimeError(
                "Expected exactly one 26.814 composer paste-entry layout, "
                f"found {text.count(V3_PASTE_ENTRY)}"
            )
        if text.count(V3_MARKDOWN_EDITOR) != 1:
            raise RuntimeError(
                "Expected exactly one 26.814 Markdown-editor paste layout, "
                f"found {text.count(V3_MARKDOWN_EDITOR)}"
            )
        if text.count(V3_MARKDOWN_PARSE) != 1:
            raise RuntimeError(
                "Expected exactly one 26.814 Markdown parse layout, "
                f"found {text.count(V3_MARKDOWN_PARSE)}"
            )

        patched = text.replace(
            V3_INPUT_RULES,
            f"...y?[]:[]{PATCH_MARKER_INPUT_RULES},",
            1,
        )
        patched = patched.replace(
            V3_PLAIN_PASTE_PREFIX,
            PATCHED_V3_PLAIN_PASTE_PREFIX,
            1,
        )
        patched = patched.replace(
            V3_PASTE_ENTRY,
            f"let h=null{PATCH_MARKER_HTML},",
            1,
        )
        patched = patched.replace(
            V3_MARKDOWN_EDITOR,
            f"if(!1&&l!=null){{{PATCH_MARKER_MARKDOWN};let t=l.parse(s),",
            1,
        )
        patched = patched.replace(
            V3_MARKDOWN_PARSE,
            "let E=Xpr(s)||d&&Zpr(s),D=!1"
            f"{PATCH_MARKER_MARKDOWN};if(!_&&(E||D)){{",
            1,
        )
        old_rich = (
            "let t=E?rmr({schema:e.state.schema,text:s,enableCodeBlocks:x,"
            "enableRichText:i,restoreMarkdownLinksAsTextLinks:d,"
            "restorePathLinksAsFileMentions:f}):$pr({schema:e.state.schema,"
            "text:s,enableCodeBlocks:x,enableRichText:i}),n=+!D;"
        )
        new_rich = (
            "let t=E?rmr({schema:e.state.schema,text:s,enableCodeBlocks:x,"
            "enableRichText:!1,restoreMarkdownLinksAsTextLinks:d,"
            "restorePathLinksAsFileMentions:f}):$pr({schema:e.state.schema,"
            "text:s,enableCodeBlocks:x,enableRichText:!1}),n=+!D;"
        )
        if old_rich not in patched:
            raise RuntimeError("26.814 rich-text Markdown parse call not found")
        patched = patched.replace(old_rich, new_rich, 1)
        return patched, True

    raise RuntimeError("Could not recognize a supported composer input layout")


def syntax_errors(entries: list[tuple[str, str]]):
    node = shutil.which("node")
    if node is None:
        return ["node executable not found for Patch U syntax verification"]

    errors = []
    with tempfile.TemporaryDirectory(prefix="codex-patch-u-syntax-") as temp_dir:
        for index, (path, text) in enumerate(entries):
            check_path = Path(temp_dir) / f"chunk-{index}.mjs"
            check_path.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(check_path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                errors.append(
                    f"{path}: {detail[-1] if detail else 'node --check failed'}"
                )
    return errors


def status(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    marker_entries = [
        (path, text)
        for path, _meta, text in targets
        if all(marker in text for marker in PATCH_MARKERS)
    ]
    upstream_paths = [
        path
        for path, _meta, text in targets
        if (
            _inline_rules_match(text)
            or PASTE_ENTRY in text
            or PASTE_PARSE in text
            or PASTE_ENTRY_V2 in text
            or PASTE_PARSE_V2 in text
            or V3_INPUT_RULES in text
            or V3_PASTE_ENTRY in text
            or V3_MARKDOWN_EDITOR in text
            or V3_MARKDOWN_PARSE in text
            or V4_PLUGINS in text
            or V4_PASTE_PREFIX in text
            or V4_HTML_RE.search(text) is not None
            or V4_MARKDOWN in text
        )
    ]
    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
        "unpatched_paths": sorted(set(upstream_paths)),
        "syntax_errors": syntax_errors(marker_entries),
    }


def verify(asar: Path):
    result = status(asar)
    if not result["marker_paths"]:
        raise SystemExit("Verification failed: Patch U composer safety markers not found")
    if result["unpatched_paths"]:
        raise SystemExit(
            "Verification failed: unpatched composer input layouts remain: "
            f"{result['unpatched_paths']}"
        )
    if result["syntax_errors"]:
        raise SystemExit(
            "Verification failed: Patch U syntax errors:\n"
            + "\n".join(f"  - {error}" for error in result["syntax_errors"])
        )
    return result


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--app-dir", required=True, help="Codex install dir (contains resources/app.asar)"
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    asar = app_dir / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    header, payload_start, targets = find_targets(asar)
    if not targets:
        raise SystemExit("Could not find the rich composer input target in app.asar")

    patched_by_path = {}
    scanned = []
    for path, _meta, text in targets:
        scanned.append(path)
        try:
            patched_text, changed = patch_text(text)
        except RuntimeError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        if changed:
            patched_by_path[path] = patched_text.encode("utf-8")

    if patched_by_path:
        if not args.no_backup:
            backup = asar.with_name("app.asar.bak-before-composer-input-safety")
            if not backup.exists():
                shutil.copy2(asar, backup)
        repack(asar, header, payload_start, patched_by_path)

    result = verify(asar)
    print(
        json.dumps(
            {
                "status": "patched" if patched_by_path else "already_patched",
                "asar": str(asar),
                "scanned": sorted(set(scanned)),
                "patched": sorted(patched_by_path),
                **result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
