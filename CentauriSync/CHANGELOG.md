# Changelog

Version and date come from `PLUGIN_VERSION` / `RELEASE_DATE` in
`centauri_canvas_sync/centauri_canvas_sync.py` (the PEP 723 header carries the same version; a unit
test enforces it).

## 0.2.0 prerelease – 2026-11-21

Prerelease. Mock-printer and upgrade fixtures passed. Real OrcaSlicer and physical printer verification are pending. Branding is now CentauriSync. The original plugin filename, package ID and agent ID remain unchanged for upgrades.

- Native Orca install and explicit settings-only backup/restore; no copied permission grants.
- Defensive WebSocket validation and bounded receive, unsupported commands fail without device writes.
- Separate optional CentauriSync Test side-loadable script uses synthetic data only.
- Author: https://amitkuzi.github.io/. License: AGPL-3.0-only, with upstream attribution in NOTICE.md. Hardware and firmware acceptance remain prerequisites for a stable release.


## 0.1.0 — 2026-11-21

First local build, for OrcaSlicer 2.5.1.

- Printer agent `elegoo-cc-canvas` ("Elegoo Centauri Carbon (CANVAS sync)"). It reads the CANVAS
  trays over SDCP v1 (`ws://<ip>:3030/websocket`, command 324) and publishes them as AMS data, so
  OrcaSlicer's own *Sync filaments* button sets each slot's type, colour and matching preset.
- Elegoo filament names map to the Elegoo presets (`OF…` filament ids). Other brands map to
  *Generic* by type. `filament_map.json` overrides a mapping.
- *Centauri CANVAS setup* window. It finds the printer (UDP 3000), tests it and previews the trays,
  then configures the current printer preset (`printer_agent` and `print_host`). It shows the
  version and date.
- Not included: printing and uploading. They stay with Elegoo's print host / upload.
