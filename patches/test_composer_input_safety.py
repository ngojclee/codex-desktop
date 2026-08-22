#!/usr/bin/env python3
"""Focused matcher tests for Patch U composer input safety."""
from patch_codex_asar_composer_input_safety import (
    PATCH_MARKERS,
    PASTE_ENTRY,
    PASTE_PARSE,
    PASTE_ENTRY_V2,
    PASTE_PARSE_V2,
    V3_INPUT_RULES,
    V3_PLAIN_PASTE_PREFIX,
    V3_PASTE_ENTRY,
    V3_MARKDOWN_EDITOR,
    V3_MARKDOWN_PARSE,
    V4_PLUGINS,
    V4_PASTE_PREFIX,
    V4_HTML,
    V4_MARKDOWN,
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

SOURCE_V3 = (
    "function lza(){"
    "let y=i&&n,b=u,x=y&&!b&&l==null;"
    "T=i?UCn({rules:[...y?[PLa,FLa]:[],"
    "GCn(/^\\s*([-+*])\\s$/,g.nodes.bullet_list)]}):null;"
    "handlePaste(e,t){"
    + V3_PLAIN_PASTE_PREFIX
    + V3_PASTE_ENTRY
    + V3_MARKDOWN_EDITOR
    + "if(l!=null&&y)return e.dispatch(e.state.tr.insertText(s)),!0;"
    + V3_MARKDOWN_PARSE
    + "let t=E?rmr({schema:e.state.schema,text:s,enableCodeBlocks:x,"
    "enableRichText:i,restoreMarkdownLinksAsTextLinks:d,"
    "restorePathLinksAsFileMentions:f}):$pr({schema:e.state.schema,"
    "text:s,enableCodeBlocks:x,enableRichText:i}),n=+!D;"
    "return h==null?(jRa(e,s),!0):!0}}"
)

SOURCE_V4 = (
    "function pGa(e=null,opts={}){let n,C=!0,_,v,w,f=!0,p=!1,SGa=1e5;"
    "let g=mxn.create({schema:y,doc:k,"
    "plugins:[mEn(),sGa(),"
    "...v==null?[]:[v.plainTextPaste,v.listInput,v.codeBlockFenceExit,"
    + V4_PLUGINS
    + "],...KHa({triggers:h})});"
    "g.setProps({"
    "handlePaste(e,t){if(t.defaultPrevented)return!0;"
    "let n=t.clipboardData,o=n?.getData(`text/plain`),"
    "s=n?.getData(`text/html`),c=o?.trim();"
    + V4_PASTE_PREFIX
    + V4_HTML
    + ","
    "let h=d!=null?g.state.schema.nodes.doc.create(null,d.content):null;"
    "if(_!=null)return!1;"
    + V4_MARKDOWN
    + ";return e.dispatch(e.state.tr.replaceSelection(new tC(t.content,1,1))),!0}"
    "return h==null?(Iwa(e,o),!0):!0"
    "}});return g}"
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

v3_patched = assert_patched(
    "26.814 composer",
    SOURCE_V3,
    (V3_INPUT_RULES, V3_PASTE_ENTRY, V3_MARKDOWN_EDITOR, V3_MARKDOWN_PARSE),
)
if "...y?[PLa,FLa]:[]," in v3_patched:
    raise AssertionError("26.814 composer: bold/italic input rules remain")
if "let h=null/*U:plain-html-paste*/," not in v3_patched:
    raise AssertionError("26.814 composer: HTML paste is still rich")
if "enableRichText:!1" not in v3_patched:
    raise AssertionError("26.814 composer: Markdown paste is still rich")

v4_patched = assert_patched(
    "26.818 composer",
    SOURCE_V4,
    (V4_PLUGINS, V4_PASTE_PREFIX, V4_HTML, V4_MARKDOWN),
)
if "v.inputRules,v.inputRulesHistoryIsolation" in v4_patched:
    raise AssertionError("26.818 composer: PMU input rules remain in plugins")
if "let d=null/*U:plain-html-paste*/," not in v4_patched:
    raise AssertionError("26.818 composer: HTML paste is still rich")
if "enableRichText:!1/*U:literal-markdown-paste*/" not in v4_patched:
    raise AssertionError("26.818 composer: Markdown paste is still rich")
if "if(C&&o.length===0)return!0;" not in v4_patched:
    raise AssertionError("26.818 composer: paste-entry prefix mangled")

print("Patch U composer input safety matcher tests passed.")
