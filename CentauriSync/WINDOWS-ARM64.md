# CentauriSync for native Windows on ARM64 (validation candidate)

This ZIP is **not yet validated on Windows ARM64**. It contains a source-based
OrcaSlicer plugin, not an EXE. OrcaSlicer must be running natively on ARM64,
with its built-in Python plugin support. No separate Python installation is
needed by this plugin. Use the native Windows ARM64 build from the [official
OrcaSlicer 2.5.2 release](https://github.com/OrcaSlicer/OrcaSlicer/releases/tag/v2.5.2).
Do not use the ZIP in Orca's file picker.

1. Extract the archive. Verify its SHA-256 against SHA256SUMS.txt.
2. In a disposable OrcaSlicer profile, use File > Plugins > Install local plugin
   and choose centauri_canvas_sync.py. Keep the original filename.
3. For the independent UI probe, install the separate centaurisync_test.py
   asset and run CentauriSync offline smoke test twice. It should say
   Bridge OK and show exactly A1, A2, A3, A4. No printer is needed for this step.
4. For hardware acceptance, select a disposable first-generation Elegoo
   Centauri Carbon preset and follow the setup in README.md. Test when idle.
   Do not upload or print during QA; leave the normal print host configured.

## Evidence required before publishing support

- Record the OS architecture and OrcaSlicer binary architecture (both ARM64;
  do not count an x64 emulated process), full OrcaSlicer version, and SHA-256
  of the ZIP and the installed .py. Start from a disposable profile.
- Pass the separate UI probe, install and enable the production plugin, restart
  Orca, and verify the intended plugin remains enabled.
- Follow TEST-PLAN.md for permissions, safe failure cases, upgrade preservation
  and rollback. Verify Centauri Carbon 2 remains unsupported.
- On a supported idle first-generation Centauri Carbon with CANVAS, record
  firmware, match filament types and colors and empty trays against the
  printer, verify native Sync filaments, and repeat after disconnect/reconnect.
  Do not share printer identifiers, local addresses or secrets in public logs.
- Save the results and candidate hashes before deciding to publish. Passing
  Python tests, including on an ARM64 VM, does not substitute for native Orca UI
  and physical-printer checks. Do not replace any existing release asset.
