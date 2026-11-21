# CentauriSync checks, evidence and rollback

## Automated in scratch
- Original mapping/payload/connect and failure cases using only mock-loopback printer. Check command 324 and AMS subscription payload, stable package ID and agent ID.
- Verify same PEP 723/plugin runtime candidate version/date, author link, surgical setup changes and native printer-agent signatures. No generic command, print or upload is actually transmitted.
- Verify a required WebSocket acceptance header, bounded message/fragment budget and absolute deadline, closed on invalid transport, fragmented mock data and ping/pong.
- Offline test plug-in: independent identity, script-only registration, fixture output and no shared settings.
- Synthetic Windows installer fixture: native path, settings/overrides round trip byte-for-byte, sidecar preserved, read/restore conflicts fail closed, duplicate-layout disambiguation.
- Build from an allowlist. Verify ZIP inventory and checksums, inspect source for no workstation-specific paths or IDs, ensure release and candidate filenames remain explicit.

## Before a stable release (not run here)
1. Confirm Orca 2.5.1 in an isolated Orca user profile; fresh install test .py first. Open **CentauriSync offline smoke test**, click twice, verify Bridge OK and exact four synthetic slots; close and reopen. Confirm no network or preset modifications.
2. Fresh install production .py under original basename, verify name, version/date and author link. Decline then approve narrow permission as appropriate. Simulate no/invalid/disconnected printer; UI should recover without settings uncertainty. Only run against user's printer with authorization and never during printing.
3. Upgrade from 0.1.0: preserve settings/override via backup, same preset identity, one production entry, permissions re-prompt expected. Validate setup with an unrelated preset and a Centauri Carbon 2 preset without applying changes, then use a disposable Centauri Carbon preset. Verify a preconfigured host URL for the same printer retains its scheme/port/path; inspect classic upload routing without sending or printing.
4. Hardware acceptance: record tested firmware and Orca version. Compare 4 trays against printer display (Elegoo, generic, empty and overridden), confirm native Sync fills expected slots, redo after disconnect/reconnect. Do not start prints or modify printer state as part of QA.
5. Before publishing any release, verify its actual date, licensing and GitHub ownership; publish only after explicit approval. Public prereleases must be labeled prerelease and state that hardware acceptance is pending. Extract the exact final asset and confirm metadata matches release version/date.

## Rollback
Close Orca; preserve and verify private settings backup, reinstall the previous original filename via native installer, close Orca, restore backed-up settings, and re-open. Let Orca handle install state and permission prompts. Verify saved preset/agent and detach the test plugin. If conflicting settings or side-by-side layouts appear, stop and reconcile before any delete.
