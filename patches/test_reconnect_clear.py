#!/usr/bin/env python3
"""Focused matcher tests for Patch D reconnect layouts.

Covers the pre-refactor body, the 26.930+ `this.deps.*` body, and the 26.1007
regression where a caller chunk mentions the method while a different chunk owns
the body. Chunk selection must come from content, never from the file name.
"""

if __package__:
    from .patch_codex_asar_reconnect_clear import (
        LEGACY_PATCHED_EXTRA,
        MARKER,
        PATCHED_BODY_EXTRA,
        UNPATCHED_LEGACY_RE,
        UNPATCHED_MODERN_RE,
        classify_chunk,
        make_patched_replace,
    )
else:
    from patch_codex_asar_reconnect_clear import (
        LEGACY_PATCHED_EXTRA,
        MARKER,
        PATCHED_BODY_EXTRA,
        UNPATCHED_LEGACY_RE,
        UNPATCHED_MODERN_RE,
        classify_chunk,
        make_patched_replace,
    )


LEGACY_BODY = (
    "markAllConversationsNeedResumeAfterReconnect(){"
    "this.threadStore.resetAfterReconnect();"
    "let{previousStreamingCount:e,previousRoleCount:t}=this.streamState.resetAfterReconnect(),n=0;"
    "for(let[e,t]of this.conversations)"
    "t.resumeState!==`needs_resume`&&"
    "(n+=1,this.updateConversationState(e,e=>{e.resumeState=`needs_resume`}));"
    "__LOGGER__.info(`websocket_reconnect_marked_threads_needing_resume`,"
    "{safe:{conversationCount:this.conversations.size,markedCount:n,"
    "previousStreamingCount:e,previousRoleCount:t},sensitive:{}})}"
)

# Verbatim anchor pair from upstream 26.930, still present in 26.1007. Patch D
# splices the cache clear between the two halves, so after patching the pair is
# no longer contiguous even though both halves must survive unchanged.
MODERN_PREFIX = "this.deps.threadStore.resetAfterReconnect();"
MODERN_SUFFIX = (
    "let{previousStreamingCount:r,previousRoleCount:i}"
    "=this.deps.streamState.resetAfterReconnect(e?.restoreStreams===!0),a=0;"
)
MODERN_ANCHOR = MODERN_PREFIX + MODERN_SUFFIX

MODERN_BODY = (
    "markAllConversationsNeedResumeAfterReconnect(e){let t=Symbol();"
    "this.dispose(),this.generation=t,this.activeResumes=0;"
    "this.deps.pagination.resetAfterReconnect(),"
    + MODERN_ANCHOR
    + "for(let[e,t]of this.deps.threadStore.conversations)"
    "(t.resumeState!==`needs_resume`||t.requests.length!==0)&&"
    "(t.resumeState!==`needs_resume`&&(a+=1),"
    "this.deps.manager.updateConversationState(e,e=>{e.resumeState=`needs_resume`,e.requests=[]}));"
    "this.deps.manager.logger.info(`websocket_reconnect_marked_threads_needing_resume`,"
    "{safe:{conversationCount:this.deps.threadStore.conversations.size,markedCount:a,"
    "previousStreamingCount:r,previousRoleCount:i},sensitive:{}})}"
)

# 26.1007 `.vite/build/main-*.js` only *calls* the method. It must never be
# chosen as the patch target even though it mentions the name.
CALLER_ONLY = (
    "this.clearRequests(),e.clearPrewarmedThreads(),"
    "e.markAllConversationsNeedResumeAfterReconnect({restoreStreams:!0,foregroundConversationId:null})"
)


def patch_one(source: str) -> str:
    modern = UNPATCHED_MODERN_RE.search(source)
    if modern is not None:
        return source[: modern.start()] + make_patched_replace(modern, True) + source[modern.end():]
    legacy = UNPATCHED_LEGACY_RE.search(source)
    if legacy is None:
        raise AssertionError("fixture matched neither reconnect layout")
    return source[: legacy.start()] + make_patched_replace(legacy, False) + source[legacy.end():]


def check_legacy(logger: str):
    source = LEGACY_BODY.replace("__LOGGER__", logger)
    if classify_chunk(source) != "legacy":
        raise AssertionError(f"legacy layout not classified for logger: {logger}")
    patched = patch_one(source)
    if MARKER not in patched:
        raise AssertionError(f"marker missing for logger layout: {logger}")
    if "__pdIds=[...this.conversations.keys()]" not in patched:
        raise AssertionError(f"cache clear missing for logger layout: {logger}")
    if "patch_d_cleared:__pdIds.length" not in patched:
        raise AssertionError(f"cleared-count telemetry missing for logger layout: {logger}")
    if classify_chunk(patched) != "patched":
        raise AssertionError(f"legacy patch is not idempotent for logger: {logger}")


def check_modern():
    if classify_chunk(MODERN_BODY) != "modern":
        raise AssertionError("26.930+ deps layout was not classified as modern")
    patched = patch_one(MODERN_BODY)
    for fragment in (MARKER, PATCHED_BODY_EXTRA, MODERN_PREFIX, MODERN_SUFFIX):
        if fragment not in patched:
            raise AssertionError(f"modern patch lost a required fragment: {fragment[:60]}")
    if "removeConversationStoreEntries" not in patched:
        raise AssertionError("modern patch must clear through the store API")
    if patched.count(MODERN_PREFIX) != 1 or patched.count(MODERN_SUFFIX) != 1:
        raise AssertionError("modern anchor halves must each survive exactly once")
    if MODERN_ANCHOR in patched:
        raise AssertionError("the cache clear must sit between the reset call and the counters")
    if classify_chunk(patched) != "patched":
        raise AssertionError("modern patch is not idempotent")
    if "fetchedRecentConversations=!1" not in patched:
        raise AssertionError("modern patch must reset the recent-window flag")


def check_caller_is_not_a_target():
    if classify_chunk(CALLER_ONLY) != "":
        raise AssertionError("a caller-only chunk must not be treated as a patch target")
    # 26.1007 ships the same class twice, so two distinct chunks classify as
    # owners while the caller chunk stays out of the list.
    chunks = [CALLER_ONLY, MODERN_BODY, MODERN_BODY, LEGACY_BODY.replace("__LOGGER__", "n")]
    owners = [c for c in chunks if classify_chunk(c)]
    if len(owners) != 3:
        raise AssertionError(f"expected 3 owner chunks across duplicated bundles, got {len(owners)}")


def check_legacy_extra_uses_conversation_keys():
    if "applyConversationState" not in LEGACY_PATCHED_EXTRA:
        raise AssertionError("legacy clear must go through applyConversationState")


check_legacy("n")
check_legacy("this.logger")
check_modern()
check_caller_is_not_a_target()
check_legacy_extra_uses_conversation_keys()
print("Patch D reconnect matcher tests passed.")
