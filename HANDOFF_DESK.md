# Codex Desktop - shared lane desk

**This file is the single live channel between the two lanes working on this repo.**
It replaces the dated snapshot `HANDOFF_REPORT_20260911.md`, which stays as an
archive and must not be used to record new decisions.

Purpose: if one lane breaks the installed app, the other lane can pick it up
from evidence written here, not from chat memory.

## Tom tat cho anh (tieng Viet)

- File nay la noi hai ben ghi cho nhau. Co gi thay doi thi **them** mot muc mới
  vào cuối mục "Log", ghi ro may nao, bang chung nao. Khong xoa moi cua ben kia.
- Chuan trinh push git -> merge main -> build -> cai 2 máy:
  [docs/RELEASE_RUNBOOK.md](docs/RELEASE_RUNBOOK.md). Ben nao lam thay em cung
  chay dung thu tu do.
- Cam chung: tao release moi bang cach **tam** tag cu (vi du `-z2` cuoi
  `a64c` cua ben kia, `b175ef1` da ra loi nay), va sua `Patch B2` theo huong
  "header integrity" - da co bang chung cach do da nam trong code.

## Lane protocol

1. Append-only. Never rewrite or delete another lane's entry; correct it with a
   new entry that cites the one it supersedes.
2. Every claim carries its evidence type: `measured` (command + result),
   `source-derived` (file + line), or `inferred`. An assertion without one of
   these three is treated as an open question, not as a finding.
3. Before touching the installed app, the release pipeline, or `config.toml`,
   post the intended action here first if it requires an app restart, because a
   restart interrupts the other lane's live tasks.
4. Reporting between lanes goes through this file or the shared sidecar relay.
   Do not use native `send_message_to_thread`; failed calls on that path are
   what wrote the `call_id`-less tool outputs that later blocked threads.

## Machine identity

| Machine | Host | Notes |
| --- | --- | --- |
| 10.11.1.1 | this lane | user desktop, primary test bed |
| 10.11.1.3 | `pcfr-des-01-lan` | second desktop, reached over SSH |

Installed identity on any machine, three cheap reads:

```powershell
Get-Content "$env:LOCALAPPDATA\CodexFromGithub\tools\.version-tag" -Raw
Get-Content "$env:LOCALAPPDATA\CodexFromGithub\tools\.release-state.json" -Raw
(Get-FileHash "$env:LOCALAPPDATA\CodexFromGithub\resources\app.asar" -Algorithm SHA256).Hash
```

---

## Settled facts (re-verifiable, do not re-litigate)

**S1. The transport that currently serves `codex_app` is the plugin manifest, not
the static `config.toml` block.** `measured` on 10.11.1.1.

The live child process is:

```
cmd.exe /d /s /c call ./scripts/launch_codex_app_tools_mcp.cmd ./server.mjs
```

relative paths and `cwd = "."`, which matches
`~\.codex\plugins\cache\openai-bundled\codex-app-tools\0.1.3\desktop-mcp.json`
character for character. The static block in `config.toml` uses absolute paths,
so it is not the definition that spawned this process. The companion
`.mcp.json` mirror ships `enabled: false`.

**S2. There is no app-tools named pipe.** `measured`.
`[System.IO.Directory]::GetFiles('\\.\pipe\')` returns only
`codex-browser-use-*` and `codex-ipc`. Any design that assumes
`CODEX_APP_TOOLS_PIPE_PATH` is published per session must be verified on the
actual bundle before it is relied on.

**S3. Patch B2 already hashes the ASAR header JSON.** `source-derived`,
`patches/patch_codex_exe_asar_integrity_hash.py`, function
`compute_asar_header_sha256()` plus its module docstring, which states the value
is "NOT the SHA256 of the whole resources/app.asar file, and NOT a header
`integrity` field (this build's header has no `integrity` section at all)".
Changing B2 to read a header `integrity` field would break a working patch.

**S4. `patches/test_app_tools_pipe_runtime.ps1` runs in CI.** `source-derived`,
`auto-repatch-release.yml:212` and `repack-existing-patched.yml:165`. It asserts
that a static `[mcp_servers.codex_app]` block is removed. Any change that keeps
the block must change this contract deliberately, in the same commit, with a
reason recorded here.

**S5. Never republish a release tag with different bytes.** `measured` history,
commits `8c7ba07` and `b175ef1`. Every rebuilt artifact gets its own distinct
tag, and `Update-Codex.ps1` compares the asset digest, not only the version
string.

**S6. Automation scheduling rules.** `measured` on `-z2`, this machine:

| Request | Result |
| --- | --- |
| `create` + `FREQ=DAILY;BYHOUR=9;BYMIN=0` | rejected, `no future runs` |
| `create` + `FREQ=DAILY;BYHOUR=23;BYMIN=30`, still hours ahead today | rejected |
| `create` + `FREQ=DAILY;INTERVAL=1` (no `BY*`) | accepted |
| `create` + `FREQ=MINUTELY;INTERVAL=60` | accepted |
| `create` + `DTSTART:...` | rejected by an explicit refine |
| `suggested_create` + `DTSTART` + `FREQ=DAILY;BYHOUR=9;BYMIN=0` | accepted |

Consequence, and this is a real capability hole, not a caller error: rrule
`BYHOUR` only matches when the iterating `dtstart` already carries that hour,
the immediate `create` path forbids `DTSTART`, so **an immediate create cannot
express a fixed wall-clock daily schedule at all**. Use `suggested_create` for
wall-clock times, or an interval rule for immediate creates.

**S7. `mode=view` is not proof of existence.** `measured`. A deleted id still
returns `Rendered automation card in the app`. Confirm against
`~\.codex\automations\<id>\automation.toml`, and confirm deletions through the
`deleteStatus` field plus that directory.

---

## Open items

**O1. Is the static `[mcp_servers.codex_app]` block load-bearing? UNRESOLVED.**

10.11.1.1 currently has the block at `config.toml:493` and is healthy: this
thread is one of the 114 legacy `dynamic_tools: codex_app` threads, it resumed,
and `automation_update` create/update/delete all worked in-session. That proves
"block present => works". Neither lane has yet proved "block absent => breaks"
or "block absent => still works". S1 points toward the block being redundant,
but it is indirect.

The decisive test is cheap and needs one restart, so it must be scheduled, not
sneaked in:

```powershell
# 1. back up, then disable (do not delete, so rollback is one edit)
Copy-Item "$env:USERPROFILE\.codex\config.toml" "$env:USERPROFILE\.codex\config.toml.bak-ab-codexapp"
# set enabled = false inside [mcp_servers.codex_app], then fully quit and relaunch
```

Then reopen thread `019e17c5-b7e2-7df2-9393-05ec157a06e6` and create one
`PAUSED` heartbeat. Healthy => the block is redundant, keep the shipped
"remove static block" contract and revert the uncommitted keep-block edit.
`invalid transport` returns => the block is load-bearing, keep the fix **and**
update S4's test in the same commit. Record which branch happened, here.

**O2. Patch Y anchor drift is the current build blocker.** `measured`.
Run `34609905922` on `478e85f` failed at step "Apply patches":

```
==> Patch Y - use plain unions for automation mode validation
Could not find renderer automation mode validation unions
```

`source-derived`: `patch_codex_asar_automation_mode_union.py` hard-codes
minified member names (`sWn`, `_Wn`, `bWn`, `vWn`, `cWn`) and the builder name
`zh`, which are per-bundle. The installed `-z2` bundle carries
`/*Y:automation-mode-union*/` exactly twice, so the anchors matched when it was
built; upstream has since renamed them.

**RESOLVED by 10.11.1.1 lane, same day.** The remedy above is implemented and
proven against the real upstream bundle, not only fixtures:

- Anchors are now structural. The automation schema is identified by its own
  `view` and `delete` enum literals; a `mode` union is selected only when its
  member list contains both, which also rejects the look-alike annotation-mode
  union that inlines object literals.
- Outcomes are `unpatched` / `already_patched` / `upstream_safe` /
  `indeterminate`, and `indeterminate` fails the release rather than shipping
  without the fix. Member order is copied from the source, never re-derived.
- When no automation schema can be found at all, the patcher now prints a drift
  report (chunk count, `mode` union call sites, enum literal counts, first union
  shapes) so one failed run is enough to write the next anchor.

Evidence, all `measured`:

| Check | Result |
| --- | --- |
| Installed `-z2` bundle | 1 schema chunk, `already_patched`, `marker_count=2` |
| `verify_markers.py` on installed build | exit 0, "All patch markers verified", `Patch Y outcome: patched` |
| Upstream `v26.908.40401` win-x64 `app.asar`, **unpatched** | chunk `webview/assets/app-initial-f094ef01c64d.js`, members `Sqn`/`Cqn`, state `unpatched`, 2 unions |
| Same bundle after running Patch Y | `status: patched`, `marker_count=2`, `syntax_errors: []` |
| Second run on that bundle | `already_patched`, file untouched (idempotent) |

The upstream jump also renames the chunk hash suffix (`app-initial-f87238153a19`
on 26.903 to `app-initial-f094ef01c64d` on 26.908) **and** every identifier
(`sWn/cWn` to `Sqn/Cqn`), which is exactly the failure mode the old hard-coded
anchor could not survive. `patches/test_automation_mode_union.py` now pins that
rename case so it cannot regress silently again.

Not yet proven: whether patches A-X survive the 26.903 to 26.908 jump. Only Y
was exercised against the new bundle. The next CI run on `v26.908.40401` is the
first real test for the rest, and anchor drift there is expected, not
surprising.

**O3. Explains the `8d98a573` vs `8dc558b4` integrity FATAL on 10.11.1.3.**
`inferred`, flagged for whoever owns the next build. Given S3, a mismatch of
this shape means B2 did not run against the final `app.asar` bytes, or the
install mixes an exe and an asar from different builds. Do not "fix" this by
changing the hash function.

Correction to this entry as first written, which told a reader to go and add an
integrity assertion: **the assertion already exists and is already must-pass.**
`asar_integrity_manifest_status()` in `patches/verify_markers.py` computes
`sha256(header JSON)` of `resources/app.asar` and compares it to the manifest
embedded in every top-level exe, and gate
`"Patch B2 — exe app.asar integrity manifest matches the asar header hash"` is
enforced at `verify_markers.py:1098`, which both release lanes run. `source-derived`.
So a release that passed the gate cannot ship this mismatch, and the bytes on
10.11.1.3 did not come from a gated build. Before changing any code for this,
establish which pipeline actually produced the installed `resources/` pair,
including whether it was a manual `apply-all-patches.ps1` run or an older tag.

**O4. The sidecar lane must not build `openai/codex@main`. FIXED in `cafca54`.**
`measured`.

Run `34639099045` (`v26.908.40401`, default `codex_ref=main`) passed **every
renderer patch**, which independently confirms the structural Patch Y anchor on
a real unpatched upstream bundle, and then failed further along:

```
Building source-patched sidecar from openai/codex ref: main
Patch V drift: expected exactly one standalone output history write anchor, found 0
```

`source-derived`: patches I, V, X, W1 and N match exact upstream Rust snippets,
and `patch_codex_sidecar_standalone_tool_output.py` fails loudly through
`replace_once()` whenever an anchor count is not exactly one. The workflow set
`default: 'main'` and had two `if (-not $codexRef) { $codexRef = 'main' }`
fallbacks, so the hourly cron rebuilt against a moving target and any upstream
commit could break a release with nothing changed in this repo.

The verified revision `d6489472f3c15e87d2d7763a5fde033545c530f8` is now recorded
once as workflow env `VERIFIED_CODEX_REF` and used as the dispatch default and by
both fallback sites, so the cron path and the manual path cannot disagree. To
raise it deliberately:

```powershell
gh workflow run auto-repatch-release.yml --repo ngojclee/codex-desktop `
  -f force=true -f codex_ref=<new-openai-codex-sha> -f release_suffix=<suffix>
```

Confirm I, V, X, W1 and N all apply, then move the pin. Never leave it on a
branch name.

**Still unproven:** whether renderer patches other than Y survive 26.903 to
26.908. Run `34639099045` cleared all of them, so this is now `measured` for the
renderer lane as a whole, not just Y.

**O5. `v26.908.40401-patched-yfix` is a REGRESSION for app-tools. Do not install it.**
`measured`, caused by the 10.11.1.1 lane (this file's author), found on 2026-09-12.

After the owner installed `-yfix` on 10.11.1.1, every `codex_app` app-tool call fails:

```
2026-09-12T05:53:34Z ERROR codex_core::tools::router:
  error=unsupported call: mcp__codex_app__automation_update
```

No `codex_app` MCP server process exists, and no app-tools or `codex-ipc` pipe
exists. The renderer still advertises the tool, so the failure surfaces only when
it is called.

**Root cause: the two release lanes are not equivalent for the sidecar.**

| Release | Lane | Sidecar handling | Result |
| --- | --- | --- | --- |
| `-z2` (26.903) | `Repack existing patched release`, run `34586150673` | reuses the already-verified sidecar, never rebuilds it | app-tools worked |
| `-yfix` (26.908) | `Auto repatch on upstream release`, run `34640654953` | step 17 **overwrote** `resources/codex.exe` with the pinned source build | app-tools broken |

Run `34640654953` printed the downgrade itself:

```
Bundled sidecar before update: codex-cli 0.144.6-cometix
Bundled sidecar after update:  codex-cli 0.0.0
```

So the full lane shipped a sidecar built from `d6489472` (2026-09-08) under an app
released later, and with no version stamped at all. The O4 pin made the pin
reproducible but did **not** make it correct: a pinned sidecar still has to be
contemporary with the app it is packaged with. That is the mistake this lane made
while believing it had removed a class of failure.

Required remedy, in order:

1. Tell owners to stay on, or return to, `-z2`. It is the last release with a
   working `codex_app` lane and verified legacy-thread resume.
2. Gate the sidecar swap. The build must refuse to publish when the source build
   reports `codex-cli 0.0.0`, and must compare the built sidecar against the one
   the app shipped with, failing when ours is older. A versionless sidecar must
   never reach a release, because the downgrade is otherwise invisible in the
   artifact.
3. Produce the 26.908 release by raising `VERIFIED_CODEX_REF` to a revision
   matching `0.144.6-cometix` or newer, confirming I, V, X, W1 and N still apply,
   and stamping the sidecar version. Expect Patch V anchor drift there and fix it
   the way O2 was fixed.
4. Until 3 is done, the only safe way to move renderer patches forward is the
   repack lane, which does not touch the sidecar.

Unrelated noise, recorded so nobody chases it: the repeated
`rmcp::transport::worker ... http://localhost:21721/mcp` errors are
`[mcp_servers.xpipe]` being down, not `codex_app`.

O1 status: still unresolved. The owner restored the static block after the
`-yfix` install (`config.toml.bak-restore-074924`, `bak-prekeep-075126`), so the
removal path was not cleanly observed, and `-yfix` is not a valid test subject
because of O5.

---

## Log

### 2026-09-11 - lane 10.11.1.1

- Installed `-z2`, digest `sha256:107e36...246ea9`, `installedAtUtc`
  `2026-09-11T16:56:04Z`. `measured`.
- Patch Y presence confirmed in the installed `app.asar`: the literal
  `/*Y:automation-mode-union*/` occurs exactly twice, as
  `yWn=sWn.or(_Wn).or(vWn).or(cWn)` and `xWn=sWn.or(bWn).or(vWn).or(cWn)`.
  `measured`.
- Patch Z cannot be confirmed by a marker grep and must not be. Z is a
  **same-length in-place edit** with no marker string, and `verify_markers.py`
  checks it structurally by importing `status` from
  `patch_codex_asar_legacy_dynamic_app_tools`. A grep for `jOt.has` returns 3
  occurrences in this bundle, which proves the guard exists but **not** whether
  it is the relaxed or the original form. Anyone re-verifying Z must use
  `verify_markers.py`, not a string search. `measured`.
- B2 presence was asserted from the release workflow and the patcher source, not
  re-derived from the installed exe this pass. `inferred`.
- Ran the live automation round trip from a legacy thread: create `PAUSED`,
  update to `ACTIVE`, delete, all through `mcp__codex_app__automation_update`.
  Zero `invalid transport` in sidecar logs since install. `measured`.
- Cleaned up: no automation is left on this machine. The `automation-lane-test`
  entry was removed by the owner, not by the app, so the earlier "why did an
  ACTIVE automation vanish" note is closed and must not be investigated again.
- Corrected one of my own earlier errors: a first pass used
  `Select-String -SimpleMatch 'mcp_servers\.codex_app'`, whose literal
  backslash can never match. That produced a false "config has no block"
  reading, which wrongly supported reverting the keep-block edit. S1 and O1
  replace that conclusion.
- Wrote `docs/RELEASE_RUNBOOK.md` as the shared push/build/install procedure.

### 2026-09-11 (later) - lane 10.11.1.1

- Closed O2: Patch Y now anchors on schema structure instead of minified names,
  gained `upstream_safe`, gained a drift report, and is proven against the real
  unpatched `v26.908.40401` bundle. See O2 for the evidence table.
- Downloaded upstream `v26.908.40401` win-x64 to test, then deleted the 706 MB
  archive and every extracted copy. `patches/` holds no binary left over from
  this work; confirm with
  `git status --short` plus a check for non-`.py`/`.ps1` files in `patches/`.
- Left `runtime/Ensure-Codex-AppToolsMcp.ps1` untouched. The keep-block edit is
  another lane's uncommitted work, O1 is still open, and committing it would
  redden `test_app_tools_pipe_runtime.ps1` in CI, which runs that test at
  `auto-repatch-release.yml:212` and `repack-existing-patched.yml:165`. Nothing
  about it has reached `main`, so current CI is unaffected by it.
- Note for whoever picks up O1: the `-z2` install and every later install runs
  `Ensure-Codex-AppToolsMcp.ps1`, which removes the static block. This machine
  still has the block at `config.toml:493`, so it has not been through that path
  since. The O1 experiment is the first time it will.

### 2026-09-11 (release) - lane 10.11.1.1

**First green build on upstream 26.908 is published.**

| Field | Value |
| --- | --- |
| Tag | `v26.908.40401-patched-yfix` |
| Asset | `CodexDesktop-Patched-win-x64-v26.908.40401-patched-yfix.zip` |
| Size | `762610380` |
| Digest | `sha256:7aea44f34e7c4d07890ee4f4fe673b947cb7a773a2aae3dcad19e8c664b86f14` |
| CI run | `34640654953`, all steps success |
| Renderer patches | applied on the real unpatched 26.908 bundle, step 15 green |
| Sidecar lane | built from pinned `d6489472`, step 17 green |
| Full marker gate | step 18 green, includes the B2 header-hash assertion |

This is the release to install on **both** machines. It is a genuine upstream
version jump, 26.903 to 26.908, not a re-patch of the same version, so expect
normal upstream UI movement alongside our patches.

Two things worth knowing before installing:

1. The earlier failure at `Apply patches` was Patch V, not Patch Y. Patch Y was
   already fixed by then; run `34639099045` cleared every renderer patch and
   died later in the sidecar lane. That is what O4 records.
2. Installing this runs `Update-Codex.ps1`, which calls
   `Ensure-Codex-AppToolsMcp.ps1`, which removes the static `[mcp_servers.codex_app]`
   block. On 10.11.1.1 that block is still present at `config.toml:493`, so
   **this install is the O1 experiment**. Rollback is one edit:
   `Ensure-Codex-AppToolsMcp.ps1` writes a timestamped
   `config.toml.bak-before-codex-app-pipe-*` before removing anything. If legacy
   threads then fail to resume, restore that file and reopen O1 as "block is
   load-bearing" rather than guessing.

### 2026-09-12 - lane 10.11.1.1 (user / Nyx)

- **O1 RESOLVED: block is load-bearing.** `measured`. After installing
  `v26.908.40401-patched-yfix`, `Launch-Codex.ps1` → `Ensure-Codex-AppToolsMcp.ps1`
  removed the static `[mcp_servers.codex_app]` block (per S4 CI contract). On reopen
  of legacy thread `019e17c5-b7e2-7df2-9393-05ec157a06e6` (one of the 114
  `dynamic_tools: codex_app` threads) the app threw
  `invalid transport in mcp_servers.codex_app` and the thread could not resume.
  Restoring the block from `config.toml.bak-before-codex-app-pipe-20260912-065038`
  made the thread resume and `automation_update` create/update/delete worked
  in-session, zero `invalid transport` since. **Conclusion: block absent => breaks
  legacy threads; keep the fix.**
- **Installed `tools/Ensure-Codex-AppToolsMcp.ps1` patched locally** (NOT yet in
  repo): added `$hasValidTransport` guard inside `Remove-StaticCodexAppServerConfig`
  → returns `status='kept'` when the block carries `command`+`args`+`cwd`. Verified:
  restore block → run installed Ensure → block survives. This contradicts S4's
  `test_app_tools_pipe_runtime.ps1` contract, which must be updated in the same
  commit if the keep-block fix goes to `main`.
- **Repo state unchanged.** `runtime/Ensure-Codex-AppToolsMcp.ps1` in the repo still
  has remove-only logic; only the installed copy on 10.11.1.1 is patched. Per Lane
  protocol #3 this is a config/app change requiring restart — recorded here before
  any push.
- **Pending user test:** creating an automation from a legacy thread on the patched
  install. Will append the result here.

---

> **DEV REVIEW NEEDED:** confirm O1 resolution above. If you agree block is
> load-bearing for legacy threads, the keep-block guard must land in
> `runtime/Ensure-Codex-AppToolsMcp.ps1` AND `test_app_tools_pipe_runtime.ps1` (S4)
> must be relaxed in the same commit. If you disagree, show a legacy-thread resume
> that works WITHOUT the static block.

### 2026-09-12 - O1 closed and durable fix landed

- **O1 is resolved.** Removing the static block caused `invalid transport` on
  legacy thread `019e17c5-b7e2-7df2-9393-05ec157a06e6`; restoring it let the
  thread resume and `automation_update` create/update/delete work. This is the
  load-bearing evidence.
- Commit `c901574` contains both halves of the fix:
  `runtime/Ensure-Codex-AppToolsMcp.ps1` keeps a static block only when
  `command`, `args`, and `cwd` are all present; incomplete blocks are still
  removed. `patches/test_app_tools_pipe_runtime.ps1` was changed in the same
  commit to prove valid LF/CRLF blocks survive, malformed blocks are removed,
  unrelated MCP tables survive, mirror creation works, and reruns are idempotent.
- Local evidence: the runtime test passed with `status=kept`; all eight Python
  patch matcher tests passed. `source-derived` and `measured`.
- **Release rule:** do not use broken full-lane
  `v26.908.40401-patched-yfix` as a base. Its sidecar was downgraded to
  `codex-cli 0.0.0` and broke `mcp__codex_app__automation_update` (O5). The
  next release must repack known-good
  `v26.903.61454-patched-automation-pipe-z2` so its healthy sidecar is
  preserved while this runtime fix is bundled into `tools/`.

### 2026-09-12 - keep-block release published

- Repack run `34677128764` completed successfully from known-good `-z2`.
- Release tag: `v26.903.61454-patched-automation-pipe-z2-keepblock`.
- Asset: `CodexDesktop-Patched-win-x64-v26.903.61454-patched-automation-pipe-z2-keepblock.zip`.
- Asset digest: `sha256:5612dfb8a7c69ade021c8986dbb0edb1fb9679d1550b383f9ff97f78c6f06bf5`.
- Asset size: `790357349` bytes.
- This release preserves the verified `-z2` sidecar, bundles the O1 runtime
  guard, and passed the updated runtime regression test. It does not use the
  broken 26.908 full-lane `-yfix` artifact.

### 2026-09-12 - live automation route failed after the keepblock install

- After installing `...-keepblock`, this legacy thread resumed normally, but
  `mcp__codex_app__automation_update` returned
  `unsupported call: mcp__codex_app__automation_update`.
- Shared sidecar identity is `codex-cli 0.0.0`. The current app process is its
  child; `app/list` returns empty and the direct app-tools server rejects
  `tools/list` with `Codex did not provide CODEX_APP_TOOLS_PIPE_PATH`.
- `resources/app.asar` does contain the literal
  `CODEX_APP_TOOLS_PIPE_PATH`, so capability-marker checks are not enough. The
  runtime must provide a live pipe path to the stdio MCP server before it can
  list/call app tools.
- Published fix2 repack:
  `v26.903.61454-patched-automation-pipe-z2-keepblock-fix2`
  - run `34679230956`
  - digest `sha256:843f70d11a8b8268bd87941a52206a3d97f2aa9b50afa2c8b52cca369f95b00c`
  - size `790357349`
- The repack intentionally preserves the `-z2` sidecar and bundles the O1
  keep-block runtime. It is not yet a verified fix for the app-tools route until
  the owner installs it, fully restarts, and both new and legacy threads can
  create an automation.
- New CI guard in `b9d0cff` blocks future full-lane releases whose source-built
  sidecar is unstamped or older than the bundled sidecar.
- Correction after the owner's observation: `codex-cli 0.0.0` is not by itself
  evidence of a broken or downgraded app sidecar. OpenAI's packaged app may use
  an unstamped sidecar in normal installs. Revert the version-only CI guard in
  `b077be3`; future gates must test the app-tools route directly, not the
  `--version` string.
- Re-check O5's causal claim. The direct evidence is only the observed full-lane
  version transition `0.144.6-cometix -> 0.0.0` and the later route failure.
  It does not prove that replacing the sidecar caused the failure; the missing
  live `CODEX_APP_TOOLS_PIPE_PATH` is the demonstrated runtime cause. Treat
  "the 26.908 full-lane sidecar swap broke app-tools" as unproven until the
  old/new sidecar route comparison is done.

---

## Desktop lane -> Dev lane

### 2026-09-12 - app-tools route root-caused; static block normalized to disabled

- `unsupported call: mcp__codex_app__automation_update` comes from the **Rust
  sidecar**, not the renderer. Evidence: the string is absent from
  `resources/app.asar` (searched whole file, index `-1`) and present in
  `resources/codex.exe` next to `unsupported custom tool call:` in
  `core\src\tools\...`. It means the tool was never registered, i.e. the
  `codex_app` MCP server failed to start. `measured`.
- Chain, `source-derived` from the live install:
  1. `config.toml` line 493 carried a static `[mcp_servers.codex_app]` with
     `enabled = true` and no `env_vars`.
  2. The sidecar therefore spawns `server.mjs` itself, without Desktop's
     per-session `CODEX_APP_TOOLS_PIPE_PATH`.
  3. `server.mjs` aborts at startup: "Codex did not provide
     CODEX_APP_TOOLS_PIPE_PATH to the app tools MCP".
  4. Server never registers, so both the in-app route and the shared-sidecar
     probe return `unsupported call`.
- The keep-block guard (`c901574`) was necessary but insufficient: it preserved
  legacy transport resolution and left `enabled = true` untouched, which is the
  spawn path. `measured`.
- Fix: a kept static block is now normalized to `enabled = false`, and any stale
  `CODEX_APP_TOOLS_PIPE_PATH` assignment inside the block or its child tables is
  removed. This mirrors the plugin's own `.mcp.json` mirror, which already uses
  `enabled: false` for exactly this reason ("resolve the transport without
  launching a duplicate server"). Desktop's `desktop-mcp.json` keeps
  `enabled: true` plus `env_vars` and owns the live pipe server. `source-derived`.
- Runtime fix is in `runtime/Ensure-Codex-AppToolsMcp.ps1`, same file as O1, and
  `patches/test_app_tools_pipe_runtime.ps1` now asserts the contract: kept block
  survives with command/cwd, is `enabled = false`, loses the stale pipe value,
  unrelated servers keep `enabled = true`, rewrite backs up exactly once, rerun
  is a no-op. `measured` (test green; Patch Y/Z tests green).
- Applied live on 10.11.1.1: `status=kept-disabled`, backup
  `config.toml.bak-before-codex-app-pipe-20260912-130550`, verified by
  `tomllib` as `enabled=False`, no `env` child table, `open-design` still
  `enabled=True`. This is a config/app change requiring restart; it takes effect
  on the next app-server start, not mid-turn. `measured`.
- Baseline repro release rebuilt from the last known-good app-tools artifact
  without touching its sidecar:
  `v26.903.61454-patched-automation-pipe-z2-repro`, run `34689500863`,
  digest `sha256:1b92a24da56f1d8a38304297326a4e7bbf04ce58260717140cb9777c2af8b0a3`,
  size `790357349`. Note the sidecar in `-z2`, `-keepblock-fix2` and `-repro` is
  the same binary, so `-repro` alone cannot fix the pipe route; the disabled-block
  normalization is the actual change. `source-derived`.
- **Unproven / next acceptance:** whether an `enabled = false` user-level entry
  still lets Desktop's dynamic injection register the tools, or whether the user
  entry shadowing the injected one is itself the blocker. Needs owner restart and
  then a real `PAUSED` heartbeat create on a legacy thread plus a new thread.
  If it still fails with the block disabled, the correct next move is to drop the
  user-level entry entirely and give the 114 legacy threads a resolvable
  transport through the plugin mirror only.

### 2026-09-12 - CI caught a CRLF bug in the first normalization attempt

- Run `34690299642` failed in `test_app_tools_pipe_runtime.ps1`, and the failure
  was a real defect, not a flaky assertion: the first `enabled = true -> false`
  rewrite used `(?m)^...true[ \t]*(?:#.*)?$`. In .NET multiline, `$` only sits
  before `\n`, and `[ \t]` never matches `\r`, so on a CRLF config the pattern
  matched nothing. `server.mjs` pipe-line stripping still changed the block, so
  `RemoveStaticCodexAppServerConfig` returned `kept-disabled` while
  `enabled = true` survived. `measured`.
  Locally it passed only because this checkout carries LF fixtures. Same trap the
  repo already hit once in `15b2280`.
- `1fbf54a` switches to a lookahead form that tolerates trailing spaces, a
  comment and `\r`, and moves both fixtures onto one
  `Assert-DisabledLegacyTransport` helper so the CRLF case asserts the same
  `enabled = false` contract instead of only checking the table header.
- Reproduced with a local CRLF simulation of the runner (both scripts rewritten
  to CRLF in a temp tree, then executed): green. `measured`.
- Do not conclude `kept-disabled` proves the server is disabled. Any future
  change to this block must assert on parsed TOML or on the normalized section,
  not on the returned status string.
- Release lane: run `34691007210` publishes
  `v26.903.61454-patched-automation-pipe-z2-appdisabled` from base
  `v26.903.61454-patched-automation-pipe-z2`, preserving that sidecar.

### 2026-09-12 - RECOVERY NOTE: the disabled-block fix failed, entry now dropped

Read this first if the app will not open a thread or shows `invalid transport`.

**Live machine state on 10.11.1.1 (this note is the hand-off point).**

| Item | Value |
| --- | --- |
| Installed release tag | `v26.903.61454-patched-automation-pipe-z2-keepblock-fix2` |
| Installed asset digest | `sha256:843f70d11a8b8268bd87941a52206a3d97f2aa9b50afa2c8b52cca369f95b00c` |
| Bundled Ensure script | old keep-guard version, hash `5BB589306A7F35C63697D7AC392EB5F81FEA5D03E176B23BAF7F4E6B0ED96C71`, no disable normalization |
| `config.toml` `[mcp_servers.codex_app]` | **ABSENT as of this note** (deliberate experiment) |
| Restore point | `~\.codex\config.toml.bak-drop-codexapp-entry` |
| Earlier restore point | `~\.codex\config.toml.bak-before-codex-app-pipe-20260912-130550` |
| Legacy thread under test | `019e17c5-b7e2-7df2-9393-05ec157a06e6` |

**Hypothesis that was tested and REJECTED (measured, twice, fresh sidecars).**

Normalizing the user-level `[mcp_servers.codex_app]` block to `enabled = false`
does not restore the app-tools route. After a genuine restart (sidecar PID
`24220`, start 17:38:22, i.e. after the edit) `mcp__codex_app__automation_update`
still returned `unsupported call: mcp__codex_app__automation_update`. So the mere
presence of a user-level `codex_app` entry, enabled or not, is what prevents the
tools from registering. The `a6cd2ac` plus `1fbf54a` normalization is therefore
**not** the automation fix; it only stops the pipeless server from being spawned.
Keep it for that narrower reason, or drop it, but do not sell it as a fix.

**Open experiment the owner is running.**

With the entry absent, the next Desktop start is the state that `-z2` had when
app-tools last worked. Two outcomes, both informative:

1. Threads open and `automation_update` works: the user-level entry is the sole
   blocker. Then the durable fix is "never write a user-level `codex_app` entry",
   `RemoveStaticCodexAppServerConfig` goes back to unconditional removal, O1 is
   re-opened, and the legacy-thread transport must come from the plugin mirror.
2. `config.toml: invalid transport in mcp_servers.codex_app` returns: O1 is
   confirmed and the conflict is real. Then stop editing `config.toml` and fix the
   writer instead: Desktop writes `mcp_servers.codex_app.enabled_tools` into user
   config without a transport, which the loader rejects.

**Restore command if outcome 2 blocks the owner or a lane is locked out.**

Fully quit Codex first (clear `ChatGPT.exe` and `codex.exe`), then:

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml.bak-drop-codexapp-entry" `
          "$env:USERPROFILE\.codex\config.toml" -Force
```

That returns the machine to `enabled = false` with working chat. No reinstall is
needed; the change is config-only and read at app-server start.

**Machine drift the owner declined to test on.**

`10.11.1.3` is still on `v26.903.61454-patched-automation`, which predates `-z`,
`-z2`, Patch X/W1/Y/Z and the keep guard, and its installed Ensure is the
remove-only version with `enabled = true` still in `config.toml`. The two desktops
are not on the same artifact, so any cross-machine conclusion drawn before
re-syncing is unreliable.

**Standing hygiene for whoever continues.**

- `unsupported call:` is a sidecar string, `This app tool is no longer available
  through dynamic tools.` is a renderer shim string. They mean different layers;
  quote the exact one when reporting.
- Never conclude from `status=kept-disabled` or any returned status string; assert
  on parsed TOML.
- Restarting Desktop ends the in-flight lane turn. Coordinate before killing.
- Report through the shared sidecar or relay, never through native
  `send_message_to_thread`, which is what produced the `call_id`-less rows.
