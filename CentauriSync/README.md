# CentauriSync

Sync the filament type and colour from an Elegoo Centauri Carbon CANVAS to OrcaSlicer's native **Sync filaments** action. LAN only, no cloud and no Python dependencies beyond the standard library.

**Version 0.2.0 prerelease · 2026-11-21.** Mock printer and synthetic upgrade tests passed. It has not been validated inside OrcaSlicer or on a real printer; test only in a disposable profile first. No Centauri Carbon 2/CC2, macOS or Linux compatibility claim. [Amit Kuzi](https://amitkuzi.github.io/) · [Changelog](CHANGELOG.md) · [Test and rollback plan](TEST-PLAN.md).

## Fresh install

1. OrcaSlicer **2.5.1** (requires its Python plugin system): choose **File → Plugins → Install local plugin** and point to `centauri_canvas_sync.py` from the extracted ZIP release package (which also contains the license and notices). Do not rename the file or select the ZIP itself.
2. Enable **CentauriSync**. Select an Elegoo Centauri Carbon preset and run **Centauri CANVAS setup**.
3. Enter the printer IP or choose **Find printer**, then **Test & preview**. When satisfied, choose **Use this printer in the current printer preset** and save the preset.
4. Click OrcaSlicer's **Sync filaments** beside Filament. If Orca asks for network/background-thread permissions, review the prompts. Do not turn on unrestricted full access.
5. Keep **Preferences → Developer → Experimental Features → Use printer agents instead of print hosts** off. CentauriSync does not print or upload; continue using the existing print host. Setup retains any existing print-host URL for the same device address; choosing a different address changes its host field, so verify special ports or paths manually.

Elegoo documents CANVAS firmware 1.44 or above in its [CANVAS recognition guide](https://wiki.elegoo.com/en/canvas/canvas-not-recognized). That is a manufacturer prerequisite, not our compatibility test result.

## Upgrade without losing settings (Windows)

Keep the original filename `centauri_canvas_sync.py` and the Orca agent ID `elegoo-cc-canvas`. Orca's native updater keeps enabled/capability selections and asks for permissions again. The helper preserves only the local `settings.json` and `filament_map.json`, which Orca can otherwise replace. No plugin permission grants are copied.

1. Close Orca (not mid-print). Choose a new private backup folder outside Orca data or release folders.
2. Run `./install.ps1 -Action Backup -BackupDir 'C:\path\your-centaurisync-backup'`.
3. Open Orca and install the same original basename using the native UI. Close Orca again before using setup.
4. Run `./install.ps1 -Action Restore -BackupDir 'C:\path\your-centaurisync-backup'`. Existing conflicting settings are not overwritten.
5. Restart Orca, check that only one production entry is enabled, confirm the saved preset and review permission prompts.

If the old installer created both native and legacy directories, the helper stops until you choose the data source with `-SourceLayout Native` or `-SourceLayout Legacy`. It never deletes files or edits Orca's install-state sidecar. Inspect the duplicate in Orca and disable it if present. Keep a copy of the old plugin release for rollback. To add a new override, copy the example to `filament_map.json` in the native installed plugin directory.

## Test plugin

`centaurisync_test.py` installs independently through the same Orca menu. It is clearly labeled **CentauriSync Test (offline only)**, checks the UI bridge with four synthetic trays, and registers no printer agent. It does not use the LAN or write settings. It does not substitute for actual hardware testing.

```powershell
python -B -m unittest discover -s tests -v
```

## Mapping and licensing

Mapping precedence: user override → Elegoo preset name/type → Generic by type → type-only. The existing filament IDs were matched against OrcaSlicer v2.5.1's [Elegoo BASE filament profiles](https://github.com/OrcaSlicer/OrcaSlicer/tree/v2.5.1/resources/profiles/Elegoo/filament/BASE) (including Generic PET) and [OrcaFilamentLibrary filament profiles](https://github.com/OrcaSlicer/OrcaSlicer/tree/v2.5.1/resources/profiles/OrcaFilamentLibrary/filament) (other generic fallbacks). See the upstream [tagged license](https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.5.1/LICENSE.txt). License for the CentauriSync project: GNU AGPL v3.0 only (SPDX: `AGPL-3.0-only`). See the bundled license and attribution notice or the [repository notice](https://github.com/amitkuzi/OrcaPlugin/blob/OrcaPlugin/NOTICE.md). OrcaSlicer and Elegoo have not endorsed this independent plug-in.
