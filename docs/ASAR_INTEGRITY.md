# ASAR Integrity Release Gate

## Incident: 2026-10-11

The owner reported an Owl browser-process crash in Crashpad report
`d21d4c31-1122-4777-94c5-ed910d7cb71d.dmp`, exception `0x80000003` in
`chrome.dll`, with `asar_file_validator.cc:129` and
`Failed to validate block while ending ASAR file stream: 0`.
This dump attribution is owner-supplied; the pipeline defect is independently
reproduced below.

The old full-check bundle has 20,986 integrity-bearing packed files and exactly
two mismatches (both file and block hashes):

- `webview/assets/codex-mobile-setup-dialog-14044b54ea6c.js`
- `webview/assets/profile-dropdown-items-a9739dde34e4.js`

The same verified clean upstream ZIP scans with zero mismatches. Patch K wrote
changed payload bytes without updating the entry hashes; Patch J had the same
unsafe write pattern, although this specific bundle did not show a stale J
entry. Same-length replacement preserves extents, not SHA256.

Affected release: `v26.1007.30912-patched-compat`, run `38134454941`,
source `ffb2be99d24a8e7b32c2052e1b9e44dc7024b8c6`.
Its asset SHA256 is
`39671183d551356ab797d2e17363986bef4e1ab3459548c75e9b4e64a66c0e4c`.
It is retained with a warning as a prerelease, excluded from stable updates;
do not silently replace its asset or reuse its tag. The intended successor is
`v26.1007.30912-patched-asar-integrity`.

## Two Separate Layers

1. Entry integrity: each packed file's `integrity.hash`, `blockSize`, and
   `blocks`. `patches/asar_integrity.py` reads all such files in bounded chunks,
   checks file/block SHA256, and exits nonzero for any mismatch, malformed
   metadata or truncated extent. Unpacked files, links, and entries without
   integrity are handled explicitly; absent integrity is counted, not silently
   reported as checked. The 26.1007 bundle has no packed entries without it.
2. Header integrity: Patch B2 updates the hash of the serialized ASAR header
   JSON embedded in the Owl shell executable. This is verified separately by
   `verify_markers.py`. A header-hash PASS says nothing about payload validity.

The scanner adopts the original local diagnostic's scope, adds a failing exit
code, bounded payload reads and metadata/EOF checks, and never repairs bytes.
An empty file has one SHA256-of-empty block, matching Electron ASAR's stream
and buffer integrity helpers. All existing integrity writers now emit that
block consistently.

## Production Writer Audit

All 18 `patch_codex_asar_*.py` scripts were inspected:

- Eight direct repackers update changed entry hashes before serializing:
  recent-window A, autopaginate v2/v3, reconnect D, SOCKS G, WS payload M,
  directive H, and model-availability O.
- Eight other scripts import O's repacker: P, Q, R, S, T, U, Y and Z.
- J and K previously raw-wrote the archive. They now use
  `repack_same_length_changes`, preserving their archive-wide matchers and
  passing changed packed-file payloads to O's repacker. Header/gap/trailer
  changes or length drift are refused. Untouched entries are not rehashed.

Other `write_text` calls in these scripts write temporary syntax-check files,
not production ASARs. The executable/fuse patchers write EXEs, source sidecar
patchers write Rust, and the package-folder repair writes plugin files; these
are different ownership surfaces, not ASAR payload writers.

Regression coverage checks multi-block and empty-file integrity for all eight
writers and bans direct `asar.write_bytes` in ASAR patchers. The full-file gate
remains authoritative even if a future script uses a different write idiom.

## Build And Verification

Use a new disposable directory extracted from the verified upstream ZIP:

```powershell
.\full-check.ps1 -AppDir 'D:\scratch\clean-extracted-app' -UpstreamTag 'v26.1007.30912'
python patches/test_asar_integrity.py
python patches/asar_integrity.py 'D:\scratch\clean-extracted-app\resources\app.asar'
```

`apply-all-patches.ps1` checks entry/block integrity after all ASAR patches and
before B2, stopping CI before expensive sidecar compilation on failure.
`verify_markers.py` repeats the read-only scan first at the final release gate.
The GitHub release workflow runs the regression suite and both gates.

Do not rerun J/K as an automatic healing pass on an already-patched stale
archive: their idempotent no-op is intentional, and the scanner will reject it.
Rebuild from clean verified upstream instead. Preserve the incident evidence.

## Installed And Live Acceptance

The owner's already-hotfixed `%LOCALAPPDATA%\CodexFromGithub` is outside the
build/test target and must not be overwritten during this investigation.
Installation and restart require coordination with the owner because closing
Desktop interrupts running chats.

After the replacement release's CI and downloaded asset checks pass:

1. Finish/pause running lanes, quit Desktop, and use the existing Update-Codex
   shortcut (or `tools\Update-Codex.ps1` with the verified distinct tag).
2. Confirm `tools\.version-tag`, `tools\.release-state.json` and
   `tools\.sidecar-source` match the chosen artifact. Run the read-only scanner
   against installed ASAR and the executable/header check.
3. Open the GitHub Patched shortcut, wait beyond the reported one-minute
   crash window, then open the profile dropdown and Codex mobile setup dialog.
   Confirm the shell/sidecar stay alive and no new matching Crashpad report
   appears. Static checksum checks do not prove these UI steps.

The separate `device-kit-oai` / `@serialport/bindings-cpp` warning about missing
native Electron ABI 143 builds is owner-supplied, nonblocking for this fix, and
not the established cause of the ASAR validator FATAL. No serialport changes
are included.
