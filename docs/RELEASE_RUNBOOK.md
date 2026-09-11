# Release runbook - push, merge main, build, install

Canonical procedure for taking a patch change from this repo to a running Codex
Desktop on **both** machines. Follow it in order. Two lanes work this repo
concurrently, and the steps that look optional are the ones that get skipped and
cause the damage.

Live coordination channel: [`HANDOFF_DESK.md`](../HANDOFF_DESK.md) at the repo
root. Read its "Open items" before starting, and append your outcome when done.

## Machines

| Role | Name | How to reach |
| --- | --- | --- |
| A | 10.11.1.1, this desktop | local |
| B | 10.11.1.3, `pcfr-des-01-lan` | `ssh pcfr-des-01-lan` |

Both must end a release cycle on the **same tag and the same asset digest**. A
half-synced pair is how the integrity and transport failures in the desk log
were produced.

## Step 0 - Record intent on the desk

Append to `HANDOFF_DESK.md` what you are about to change, and whether it needs
an app restart. A restart kills the other lane's running tasks. This is the only
step that is safe to skip when the change is documentation only.

## Step 1 - Establish repo truth before editing

```powershell
cd D:\Python\projects\codex-desktop
git status --porcelain=v1
git log --oneline -8
git rev-parse HEAD origin/main
```

The worktree is shared. Unrelated dirty files belong to the other lane: never
`git reset --hard`, never `git checkout --` someone else's file, and stage only
the paths you own. If `HEAD` and `origin/main` differ, you are behind; fetch
first.

Also record the installed state of both machines (Step 6 block), because you
need "before" evidence to prove the update took effect.

## Step 2 - Make the change durable, not one-off

Rules that exist because breaking them cost days:

- A renderer or sidecar behavior fix becomes a **patch script** under
  `patches/`, registered in `apply-all-patches.ps1`, in
  `patches/verify_markers.py`, and in
  [`PATCH_INVENTORY.md`](PATCH_INVENTORY.md). Editing the installed binary or
  the installed `app.asar` by hand is never the fix; the next update erases it
  and the next person cannot tell what state anything is in.
- Patch order is fixed. Renderer lane ends with `... -> Y -> Z -> B2`; **B2 must
  stay last**, since it embeds the ASAR header hash of the final bytes.
- Anchors must be **semantic**. Do not hard-code minified identifiers such as
  `zh` or `sWn`; they are renamed by every upstream rebuild, which is exactly how
  O2 in the desk log got produced. Anchor on structure and stable string
  literals, and fail loud on drift rather than silently no-op'ing.
- Every patch must distinguish `patched`, `already_patched`, `upstream_safe`,
  and `skipped`. Only anchor drift, a missing marker, a syntax failure, an
  integrity mismatch, or a non-zero exit is a real failure.

## Step 3 - Run the same gates CI runs, locally, first

CI's `Test patcher matchers` step (`auto-repatch-release.yml:197-212`) is the
gate. Run it before you push so CI is not your first feedback:

```powershell
cd D:\Python\projects\codex-desktop
python patches/test_directive_windows_path.py
python patches/test_model_availability_filter.py
python patches/test_custom_provider_ultra.py
python patches/test_voice_paste_shortcut.py
python patches/test_composer_input_safety.py
python patches/test_reconnect_clear.py
python patches/test_automation_mode_union.py
python patches/test_legacy_dynamic_app_tools.py
./patches/test_model_catalog_sync.ps1
./patches/test_update_release_state.ps1
./patches/test_repair_project_config.ps1
./patches/test_app_tools_pipe_runtime.ps1
```

Add a focused regression test for anything you change, and add it to that list.
A fix with no test in that list will be reverted by the next person who cannot
tell why it exists.

If you touched a patcher that rewrites minified JS, also verify against a real
extracted bundle before pushing:

```powershell
python patches/verify_markers.py <extractedAppDir> --upstream-tag <vXXXX>
```

**If any gate is red, stop.** Do not push a red test to "see what CI says", and
do not edit a test to green without recording why in the desk log and in the
commit message. `test_app_tools_pipe_runtime.ps1` in particular encodes the
`config.toml` static-block contract; weakening it silently re-opens O1.

## Step 4 - Commit and merge to main

```powershell
git add <only your named paths>
git status --short              # confirm nothing unrelated got staged
git commit -m "<imperative, specific subject>"
git pull --rebase origin main
git push origin main
git rev-parse HEAD origin/main  # must now print the same sha twice
```

Never trust "it pushed". Compare `HEAD` to `origin/main`. Do not force-push, do
not amend a commit the other lane may have built on, and do not rewrite shared
history.

## Step 5 - Build the release

Two lanes exist; pick the right one.

**Full lane** - `auto-repatch-release.yml`. Downloads upstream, applies every
renderer patch, rebuilds the source sidecar lane `I -> V -> X -> W1 -> N`,
verifies markers, bundles `runtime/` into `tools/`, publishes. Triggered hourly
by cron, and it skips when this repo already carries the current upstream
version, so a plain push may produce no build. To force one:

```powershell
gh workflow run auto-repatch-release.yml --repo ngojclee/codex-desktop `
  -f force=true -f release_suffix=<short-meaningful-suffix>
```

**Renderer-only lane** - `repack-existing-patched.yml`. Reuses an already
verified patched artifact that contains the sidecar work, applies renderer
patches, refreshes B2, publishes a distinct tag. Fast, and correct when only
`app.asar` content changed:

```powershell
gh workflow run repack-existing-patched.yml --repo ngojclee/codex-desktop `
  -f base_release_tag=<verified-tag> -f release_tag=<new-distinct-tag>
```

Tag naming, non-negotiable: **one build, one tag, never reused.** If an artifact
is wrong, delete or supersede it and publish a *different* tag. Republishing the
same tag with different bytes makes every already-installed machine look
up-to-date while holding a different digest, which is the trap closed by
`8c7ba07` and `b175ef1`.

Watch it:

```powershell
gh run list --repo ngojclee/codex-desktop --limit 5
gh run watch <run-id> --repo ngojclee/codex-desktop
gh run view <run-id> --log-failed --repo ngojclee/codex-desktop
```

A green run is not a shippable release until Step 6 proves the artifact.

## Step 6 - Verify the artifact before anyone installs it

```powershell
gh release view <tag> --repo ngojclee/codex-desktop `
  --json tagName,createdAt,assets --jq '{tag:.tagName,created:.createdAt,assets:[.assets[].name]}'
```

Then confirm the published bytes carry the patches, by downloading the asset to
a scratch directory and running `verify_markers.py` against it. Specifically,
when an ASAR change is involved, assert that the hash embedded in the executable
equals `sha256(header JSON)` of the final `app.asar`. A release that passes CI
but fails this assertion is the `expected vs actual` integrity failure class
recorded in the desk log, and it must not be installed anywhere.

## Step 7 - Install on both machines

`Update-Codex` handles download, verification, and the `tools/` refresh. The
`runtime/` scripts in this repo are bundled into the installed `tools/` folder,
so an installer change only reaches a machine through an install, never by
editing the repo copy.

Shortcut: `Start Menu\Programs\Update-Codex.lnk`. Explicit form, and the form to
use when you must pin a tag:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\CodexFromGithub\tools\Update-Codex.ps1" -Tag <tag>
```

On machine B, run the same thing over SSH, but keep quoting simple: write the
command into a local `.ps1`, `scp` it, and invoke `ssh pcfr-des-01-lan` straight
into PowerShell rather than nesting `cmd /c`.

After installing, on **each** machine:

1. Fully quit, then confirm nothing is left. Config and sidecar wiring are only
   read at process start, so a windowed close is not a restart.
2. Relaunch through `Launch-Codex.vbs` / `Launch-Codex.ps1` so the shared
   `--listen` app-server is the one that comes up.

```powershell
Get-Process | Where-Object { $_.Name -match 'ChatGPT|codex|Codex' } | Select-Object Name, Id
```

## Step 8 - Prove the install, then prove the features

Installed identity per machine:

```powershell
Get-Content "$env:LOCALAPPDATA\CodexFromGithub\tools\.version-tag" -Raw
Get-Content "$env:LOCALAPPDATA\CodexFromGithub\tools\.release-state.json" -Raw
(Get-FileHash "$env:LOCALAPPDATA\CodexFromGithub\resources\app.asar" -Algorithm SHA256).Hash
```

The `.version-tag`, the `assetDigest` in `.release-state.json`, and the
`app.asar` hash must all match what Step 6 recorded for the published asset, on
both machines. Report installed proof separately from source proof; a commit on
`main` says nothing about what a machine is running.

Then the feature checks that this repo has actually regressed on:

```powershell
Select-String -Path "$env:USERPROFILE\.codex\config.toml" -Pattern '\[mcp_servers\.codex_app\]'
$f = Get-ChildItem "$env:TEMP\codex-shared" -File -Filter *.err.log |
     Sort-Object LastWriteTime -Descending | Select-Object -First 3
$f | ForEach-Object { Select-String -Path $_.FullName -Pattern 'invalid transport|Integrity check failed' }
```

Manual, in the app, on both machines: open a known **legacy** thread and confirm
it renders; open a **new** thread; run one `automation_update` `mode=create`
with a `PAUSED` interval schedule, confirm the id exists on disk under
`~\.codex\automations\`, then delete it. `mode=view` alone is not proof, see the
desk log.

## Step 9 - Close out on the desk

Append: tag, run id, digest, per-machine installed proof, which feature checks
were measured and which were not, and anything still open. If you changed a
contract, name it.

## Rollback

There is no downgrade switch. Rollback is an install of the previous known-good
tag, so always record the last known-good tag on the desk before a risky build.
For config-only damage, `Ensure-Codex-AppToolsMcp.ps1` writes a timestamped
`config.toml.bak-before-codex-app-pipe-*` next to `config.toml` before it edits;
restore that rather than hand-reconstructing the file. If an app refuses to
launch after an install, first check the integrity pair (`expected` vs `actual`)
in the app log before touching anything else; that pair tells you whether the
artifact or the config is at fault.

## Things this repo has already been hurt by

- Republishing a tag with new bytes. Never.
- Hard-coding minified identifiers in an anchor.
- Editing the installed `app.asar` or `codex.exe` instead of adding a patch.
- Trusting a green CI run as proof the artifact is good.
- Trusting `mode=view` as proof an automation exists.
- Syncing `config.toml` between machines by copying whole files, which drags a
  stale pipe path across.
- Deciding the static `codex_app` block is unnecessary without running the O1
  experiment on a machine that is free to restart.
