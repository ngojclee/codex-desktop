# Codex Patched Update Handoff - 2026-07-10

## Current Status
- Local installed app is fixed and starts successfully from `Codex (GitHub Patched)`.
- Installed path: `C:\Users\ngocl\AppData\Local\CodexFromGithub`.
- The updated upstream bundle now launches the Electron desktop from `ChatGPT.exe`, while product metadata still says `Codex`.
- Task Manager shows `ChatGPT.exe` for the desktop and `resources\codex.exe` for the app-server sidecar.
- `python patches\verify_markers.py "$env:LOCALAPPDATA\CodexFromGithub"` passes after local repair.

## Issues Found

### 1. Launcher Hardcoded the Wrong Desktop Binary
- Symptom: after update, `Codex (GitHub Patched)` did not open and no visible app appeared in Task Manager.
- Root cause: `runtime/Launch-Codex.ps1` hardcoded `Codex.exe` as the desktop executable.
- New upstream bundle includes `ChatGPT.exe` as the real Electron desktop binary. `Codex.exe` exits immediately with code `1`.
- Local/source fix: prefer `ChatGPT.exe`, fallback to `Codex.exe` for older bundles.
- Source file changed: `runtime/Launch-Codex.ps1`.

### 2. Launcher Passed PowerShell Switches Incorrectly
- Symptom: launch output showed `Shared skills directory is not reachable: -RepairRootLink`.
- Root cause: `& $refreshScript @refreshArgs` caused `-Quiet` and `-RepairRootLink` to bind positionally in this script context.
- Fix: call `Refresh-Codex-SharedSkills.ps1` with named switches directly.
- Source file changed: `runtime/Launch-Codex.ps1`.

### 3. Patch J Corrupted New Minified Bundle Syntax
- Symptom: app opened but stayed forever on splash/loading screen.
- Renderer DevTools showed:
  `SyntaxError: Unexpected token '!'` in `app://-/assets/app-main-BEs0GGm0.js`.
- Root cause: `patch_codex_asar_computer_use_gate.py` matched only one-character function names, e.g. `c(`ID`)`.
- New bundle emitted multi-character minified identifiers, e.g. `bc(`1506311413`)`.
- Old Patch J replaced only the second character/call tail and left invalid tokens such as `b!0            ` and `l!0            `.
- Fix: Patch J must match the full identifier: `[a-zA-Z_$][a-zA-Z0-9_$]*(`ID`)`.
- Repair support added for already-corrupted tokens matching minified identifier + `!0` + padding.
- Source file changed: `patches/patch_codex_asar_computer_use_gate.py`.
- Local installed `app.asar` was repaired with the updated script.

### 4. User Data Profile Was Reset During Debugging
- To isolate splash-loading, the Electron UI profile was renamed, not deleted:
  `C:\Users\ngocl\AppData\Roaming\Codex\web\Codex.bak-loading-20260710-093627`.
- `~\.codex` config, sessions, auth, and model catalog were not reset.
- The clean profile was recreated at `C:\Users\ngocl\AppData\Roaming\Codex\web\Codex`.
- If any old web profile-only state is needed, restore selectively from the backup folder.

## Model Catalog / Reasoning Effort Findings

### GPT 5.6 Sol Max Effort
- Local `~\.codex\model_catalog.json` and `~\.codex\models_cache.json` both include `gpt-5.6-sol` with `supported_reasoning_levels` containing `max`.
- Catalog entry uses schema fields:
  - `default_reasoning_level`
  - `supported_reasoning_levels[].effort`
- Renderer/app-server maps this to `supportedReasoningEfforts` internally.
- Bundle labels already include `composer.mode.local.reasoning.max.label` with default message `Max`.
- But `webview/assets/model-and-reasoning-dropdown-*.js` hardcodes power settings for `gpt-5.6-sol` as:
  - `low`
  - `medium`
  - `high`
  - `xhigh`
  - `ultra`
- It omits `max`, so the dropdown can show up to `Extra High`/`Ultra` but not `Max` from the power-setting list.
- `webview/assets/model-queries-*.js` also initializes `enabledReasoningEfforts` as `[`low`,`medium`,`high`,`xhigh`]`, so `max` can be filtered out unless enabled elsewhere.

### GPT Model Display Names
- Catalog currently has mixed display names:
  - `gpt-5.6-sol` -> `GPT 5.6 Sol`
  - `gpt-5.6-terra` -> `GPT 5.6 Terra`
  - `gpt-5.6-luna` -> `GPT 5.6 Luna`
  - `gpt-5.5` -> `GPT-5.5`
  - `gpt-5.4` -> `GPT 5.4`
  - `gpt-5.4-mini` -> `GPT-5.4-Mini`
  - `gpt-5.3-codex-spark` -> `GPT-5.3 Codex Spark`
  - `gpt-5.2` -> `gpt-5.2`
- The model dropdown strips a leading `GPT-` from display labels using bundle function `W(e).replace(/^GPT-/iu, ``)` in `model-and-reasoning-dropdown-*.js`.
- That is why `GPT-5.5` appears as `5.5` in the UI.
- Patch Q implements the renderer-side fix and normalizes only the leading
  separator:
  - `GPT-5.5` -> `GPT 5.5`
  - `GPT-5.4-Mini` -> `GPT 5.4-Mini`
  - `GPT-5.3 Codex Spark` -> `GPT 5.3 Codex Spark`
  - `gpt-5.2` -> `GPT 5.2`
- Catalog names that already start with `GPT ` remain unchanged.

## Completed Dev Actions
1. Updated the launcher for `ChatGPT.exe` with a `Codex.exe` fallback.
2. Fixed Patch J full-identifier matching and legacy corruption repair.
3. Added CI syntax verification for Patch J and Patch Q renderer chunks.
4. Added Patch P for `gpt-5.6-sol:max`.
5. Added Patch Q so GPT model labels keep a visible `GPT` prefix.
6. Updated the updater to refresh standard shortcut targets and icons.

## Implemented In Repository

- Launcher now prefers `ChatGPT.exe`, falls back to `Codex.exe`, passes shared
  skill switches by name, and tracks the selected desktop executable by path.
- Patch J now matches complete minified identifiers, repairs the known
  malformed legacy output, and marks touched chunks with `/*J*/`.
- Release verification rejects remaining Computer Use gate calls, known
  malformed Patch J tokens, and JavaScript syntax errors in Patch J chunks.
- Patch P preserves catalog-supported `max` reasoning through the renderer
  effort filter and adds `gpt-5.6-sol:max` to the Work power sequence.
- Patch Q normalizes leading `GPT-` labels to `GPT ` in all affected renderer
  model-picker paths, so GPT model names keep a visible `GPT` prefix without
  rewriting the user's model catalog.
- CI verifies `ChatGPT.exe` architecture when the current upstream bundle
  includes it.

The GPT display-name normalization is now implemented in the renderer patch
set and does not rewrite the user's model catalog.

## Verification Commands
```powershell
python patches\verify_markers.py "$env:LOCALAPPDATA\CodexFromGithub"
Get-Process -Name "ChatGPT","codex" -ErrorAction SilentlyContinue | Format-List Id,ProcessName,Path,StartTime,Responding
```

Expected process paths:
```text
C:\Users\ngocl\AppData\Local\CodexFromGithub\ChatGPT.exe
C:\Users\ngocl\AppData\Local\CodexFromGithub\resources\codex.exe
```

## Repository Notes
- The implementation is committed in the main repository patch/runtime set.
- `runtime/Diagnose-Codex-Desktop.ps1` predates this handoff and remains
  intentionally outside these release commits.
