#!/usr/bin/env python3
"""Focused matcher tests for Patch D reconnect layouts."""

from patch_codex_asar_reconnect_clear import MARKER, UNPATCHED_RE, make_patched_replace


BODY = (
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


def check(logger: str):
    source = BODY.replace("__LOGGER__", logger)
    match = UNPATCHED_RE.search(source)
    if match is None:
        raise AssertionError(f"Patch D did not match logger layout: {logger}")
    patched = source[: match.start()] + make_patched_replace(match) + source[match.end() :]
    if MARKER not in patched:
        raise AssertionError(f"Patch D marker missing for logger layout: {logger}")
    if "__pdIds=[...this.conversations.keys()]" not in patched:
        raise AssertionError(f"Patch D cache clear missing for logger layout: {logger}")
    if patched.count(MARKER) < 2:
        raise AssertionError(f"Patch D cache-clear marker incomplete for logger layout: {logger}")


check("n")
check("this.logger")
print("Patch D reconnect matcher tests passed.")
