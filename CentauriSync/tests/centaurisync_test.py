# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# id = "centaurisync-ui-smoke-test"
# name = "CentauriSync Test (offline only)"
# description = "Checks the isolated OrcaSlicer plugin UI/message bridge with synthetic tray data."
# author = "amitkuzi"
# version = "0.2.0"
# homepage = "https://amitkuzi.github.io/"
# ///
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Amit Kuzi. License and attribution: https://github.com/amitkuzi/OrcaPlugin
"""Optional diagnostic script; does not import socket, write settings or register a printer agent."""
from __future__ import annotations
import queue
try:
    import orca
except ImportError:
    orca = None

VERSION = "0.2.0"
RELEASE_DATE = "2026-11-21"
_FIXTURE = (
    {"slot": "A1", "material": "PLA", "color": "#1A2B3C"},
    {"slot": "A2", "material": "PETG", "color": "#FF0000"},
    {"slot": "A3", "material": "Empty", "color": ""},
    {"slot": "A4", "material": "ABS", "color": "#FFFFFF"},
)
PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>CentauriSync Test</title><body><h1>CentauriSync Test</h1>
<p>Offline smoke test. These are synthetic trays, not printer data.</p>
<button id="run">Check UI message bridge</button><p id="result" role="status"></p>
<pre id="trays"></pre><script>
document.getElementById('run').onclick=()=>orca.postMessage({command:'fixture'});
orca.onMessage(msg=>{
 document.getElementById('result').textContent='Bridge OK · v'+msg.version+' · '+msg.date;
 document.getElementById('trays').textContent=msg.trays.map(t=>t.slot+' '+t.material+' '+t.color).join('\n');
});
</script></body></html>"""

def fixture_reply(message):
    if not isinstance(message, dict) or message.get("command") != "fixture":
        return None
    return {"version": VERSION, "date": RELEASE_DATE, "trays": [dict(tray) for tray in _FIXTURE]}

if orca is not None:
    class CentauriSyncSmokeScript(orca.script.ScriptPluginCapabilityBase):
        def get_name(self):
            return "CentauriSync offline smoke test"

        def execute(self):
            self._closed = queue.Queue()
            self.window = None
            def on_message(message):
                reply = fixture_reply(message)
                if reply and self.window and self.window.is_open():
                    self.window.post(reply)
            try:
                self.window = orca.host.ui.create_window(
                    title="CentauriSync Test", html=PAGE, width=520, height=400,
                    on_message=on_message, on_close=lambda: self._closed.put(None))
                self._closed.get()
                return orca.ExecutionResult.success("Offline smoke test closed.")
            except Exception as error:
                return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError, str(error))
            finally:
                self.window = None

        def on_cancelled(self):
            closed = getattr(self, "_closed", None)
            if closed is not None:
                closed.put(None)

    @orca.plugin
    class CentauriSyncSmokePackage(orca.base):
        def register_capabilities(self):
            orca.register_capability(CentauriSyncSmokeScript)
