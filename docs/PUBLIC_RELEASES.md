# Public source and release hygiene

The public repository contains reusable patches, tests, workflows and generic
documentation. Operator handoffs, machine configuration, credentials, transcripts,
crash dumps and temporary probes belong in ignored local storage. Use `.local/`
or the ignored root `HANDOFF*.md` files; never force-add these files.

Use environment variables and placeholders for installation paths and endpoints.
Use RFC 5737 documentation addresses such as `192.0.2.10` in fixtures, not a
machine's real private address. The installer resolves each machine's own
profile/install directory. Google Workspace routing is not managed by this app.

Before publishing, run:

```powershell
python -m unittest discover -s patches -p test_public_source.py -v
python patches/verify_public_source.py
pwsh -NoProfile -File patches/test_sidecar_tools_sha.ps1
pwsh -NoProfile -File patches/test_google_mcp_runtime.ps1
```

Both release workflows run the source hygiene gate before packaging. It reports
categories and filenames, never matched secret values. This gate supplements,
not replaces, GitHub secret scanning and a private comparison against real local
credentials. Audit the downloaded public source and release bytes as well.

Runtime helpers have one source of truth: `runtime/`. Every package refreshes
`tools/` from that directory and hash-checks all files. Do not publish snapshots
copied from a live install or a temporary staging directory. Repack provenance
checks must accept both Windows CRLF and Unix LF metadata while rejecting a
different source SHA.

## Historical cleanup

The October 28, 2026 privacy cleanup rewrites source branches and tags to remove
private handoff files and machine identifiers, including commit messages.
Maintainer author metadata uses the public GitHub identity and its no-reply
email address. Release asset bytes are not
replaced by this operation; their embedded original source SHAs still describe
the builds that produced them. Future builds use the sanitized source history
and a new distinct release tag. Never silently reuse an artifact's tag.

After a history rewrite, operators with another development clone should retain
local work privately and make a fresh clone. Do not merge or push the old history
back. Users who only install release ZIPs or use the Update-Codex shortcut do not
need a Git clone migration.

A force-push removes the old history from current branches/tags, but cannot
revoke copies already downloaded, forks, or all cached old commit views. Actual
credential exposure requires rotation first; GitHub Support may be needed for
eligible server-side cache removal. Never claim that a rewrite alone proves
historical erasure everywhere.
