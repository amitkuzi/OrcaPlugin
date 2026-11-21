import importlib.util
import inspect
from pathlib import Path
import socket
from types import SimpleNamespace
import sys
import tomllib
import unittest
from unittest.mock import patch

import test_centauri_canvas_sync as base
ccs = base.ccs
ROOT = Path(__file__).resolve().parents[1]

def load_fixture(fake_orca=None):
    e = ROOT / "tests" / "centaurisync_test.py"
    spec = importlib.util.spec_from_file_location("centaurisync_test", e)
    module = importlib.util.module_from_spec(spec)
    if fake_orca is None:
        spec.loader.exec_module(module)
    else:
        with patch.dict(sys.modules, {"orca": fake_orca}):
            spec.loader.exec_module(module)
    return module

def metadata(path):
    text = path.read_text(encoding="utf-8").splitlines()
    i = text.index("# /// script")
    j = text.index("# ///", i+1)
    return tomllib.loads("\n".join(line[2:] for line in text[i+1:j]))["tool"]["orcaslicer"]["plugin"]

class CandidateContract(unittest.TestCase):
    def test_brand_link_and_upgrade_ids(self):
        p = metadata(ROOT / "centauri_canvas_sync" / "centauri_canvas_sync.py")
        self.assertEqual(p["id"], "centauri-canvas-sync")
        self.assertEqual(ccs.AGENT_ID, "elegoo-cc-canvas")
        self.assertEqual(p["name"], "CentauriSync")
        self.assertEqual(p["homepage"], ccs.AUTHOR_URL)
        self.assertIn('href="https://amitkuzi.github.io/"', ccs.PAGE)

    def test_test_plugin_isolated_fixture_and_registration(self):
        registry = []
        fake = SimpleNamespace(script=SimpleNamespace(ScriptPluginCapabilityBase=object),
                               plugin=lambda c: c, base=object,
                               register_capability=registry.append)
        t = load_fixture(fake)
        t.CentauriSyncSmokePackage().register_capabilities()
        self.assertEqual(registry, [t.CentauriSyncSmokeScript])
        self.assertIsNone(t.fixture_reply({"command":"other","host":"unused"}))
        msg = t.fixture_reply({"command":"fixture"})
        self.assertEqual([d["slot"] for d in msg["trays"]], ["A1","A2","A3","A4"])
        msg["trays"][0]["slot"] = "altered"
        self.assertEqual(t.fixture_reply({"command":"fixture"})["trays"][0]["slot"],"A1")
        data = metadata(ROOT / "tests" / "centaurisync_test.py")
        self.assertEqual(data["id"], "centaurisync-ui-smoke-test")
        self.assertEqual(data["version"], ccs.PLUGIN_VERSION)
        self.assertEqual(t.RELEASE_DATE, ccs.RELEASE_DATE)

    def test_print_upload_and_unknown_commands_are_denied_without_callbacks(self):
        agent = ccs.CentauriCanvasAgent()
        def no_callback(*_): self.fail("unsupported action invoked callback")
        methods = {"start_print":4,"start_send_gcode_to_sdcard":4, "start_local_print":3,
                   "start_local_print_with_record":4,"start_sdcard_print":3}
        for name,nargs in methods.items():
            f=getattr(agent,name)
            self.assertEqual(len(inspect.signature(f).parameters), nargs)
            self.assertEqual(f(*([no_callback]*nargs)), -7010)
        self.assertEqual(agent.send_message("printer",'{"other":{}}',0,0), -7010)
        self.assertTrue(agent.fetch_filament_info("printer", object()))
        self.assertTrue(agent._wake.is_set())

    def test_carbon_2_setup_does_not_change_presets_or_settings(self):
        from unittest.mock import Mock
        fake_orca = Mock()
        with patch.object(ccs, "orca", fake_orca), patch.object(ccs, "save_settings") as save:
            result = ccs.CentauriCanvasSetup()._handle(
                {"command":"setup","host":"127.0.0.1","preset_model":"Elegoo Centauri Carbon 2"})
        self.assertIn("error", result)
        fake_orca.host.preset.printer.begin_update.assert_not_called()
        save.assert_not_called()

    def test_setup_preserves_same_host_url_for_classic_printing(self):
        from unittest.mock import Mock
        fake_orca = Mock()
        transaction = fake_orca.host.preset.printer.begin_update.return_value
        transaction.commit.return_value = SimpleNamespace(target_name="Test",dirty=False)
        with patch.object(ccs, "orca", fake_orca), patch.object(ccs, "save_settings"):
            result = ccs.CentauriCanvasSetup()._handle(
                {"command":"setup","host":"127.0.0.1","preset_model":"Elegoo Centauri Carbon",
                 "previous_print_host":"http://127.0.0.1:3001/upload"})
        self.assertIn("message",result)
        transaction.set.assert_called_once_with("printer_agent", ccs.AGENT_ID)

    def test_handshake_requires_accept_and_closes_failed_connection(self):
        class MissingAccept:
            closed = False
            def settimeout(self, _): pass
            def sendall(self, _): pass
            def recv(self, _):
                return b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
            def close(self): self.closed = True
        conn = MissingAccept()
        with patch.object(socket, "create_connection", return_value=conn):
            with self.assertRaisesRegex(ccs.SdcpError, "handshake did not validate"):
                ccs.WebSocket("127.0.0.1", 1)
        self.assertTrue(conn.closed)

    def test_read_budget_rejection_closes_transport(self):
        from unittest.mock import Mock
        transport = Mock()
        ws = ccs.WebSocket.__new__(ccs.WebSocket)
        ws.sock = transport
        ws._buffer = b""
        ws.MAX_MESSAGE = 4
        transport.recv.side_effect = [bytes((0x81, 5))]
        with self.assertRaisesRegex(ccs.SdcpError, "size limit"):
            ws.recv_text(1)
        transport.close.assert_called_once()
        self.assertEqual(transport.recv.call_count, 1)

    def test_absolute_deadline_bounds_partial_frame(self):
        from unittest.mock import Mock
        transport = Mock()
        ws = ccs.WebSocket.__new__(ccs.WebSocket)
        ws.sock = transport
        ws._buffer = b""
        transport.recv.side_effect = [b"\x81", b"\x01"]
        with patch.object(ccs.time,"monotonic",side_effect=[10.0,10.3,11.1]):
            with self.assertRaises(socket.timeout):
                ws.recv_text(1.0)
        transport.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
