"""Offline tests: pure mapping, payload shape, and a full agent run against a mock SDCP printer.

Run:  python -m unittest discover -s tests -v   (from the repository root)
"""

import base64
import hashlib
import json
import re
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "centauri_canvas_sync"))

import centauri_canvas_sync as ccs  # noqa: E402  (orca is absent -> stub bases)


CANVAS_REPLY = {
    "Ack": 0,
    "active_canvas_id": 0,
    "active_tray_id": 1,
    "auto_refill": False,
    "canvas_list": [{
        "canvas_id": 0,
        "connected": 1,
        "tray_list": [
            {"tray_id": 1, "brand": "ELEGOO", "filament_type": "PLA", "filament_name": "PLA Matte",
             "filament_code": "0x0000", "filament_color": "#1A2B3C", "min_nozzle_temp": 190,
             "max_nozzle_temp": 230, "status": 2},
            {"tray_id": 0, "brand": "ELEGOO", "filament_type": "PETG", "filament_name": "PETG",
             "filament_color": "#FF0000", "status": 1},
            {"tray_id": 2, "brand": "", "filament_type": "", "filament_name": "",
             "filament_color": "", "status": 0},
            {"tray_id": 3, "brand": "Polymaker", "filament_type": "ABS", "filament_name": "PolyLite ABS",
             "filament_color": "0xFFFFFF", "status": 2},
        ],
    }],
}


class PureHelpers(unittest.TestCase):
    def test_version_matches_header(self):
        text = (ROOT / "centauri_canvas_sync" / "centauri_canvas_sync.py").read_text(encoding="utf-8")
        header = re.search(r'^# version = "([^"]+)"', text, re.M).group(1)
        self.assertEqual(header, ccs.PLUGIN_VERSION)
        self.assertRegex(ccs.RELEASE_DATE, r"^\d{4}-\d{2}-\d{2}$")

    def test_normalize_color(self):
        self.assertEqual(ccs.normalize_color("#1a2b3c"), "1A2B3CFF")
        self.assertEqual(ccs.normalize_color("0xffffff"), "FFFFFFFF")
        self.assertEqual(ccs.normalize_color("ABC"), "AABBCCFF")
        self.assertEqual(ccs.normalize_color("11223344"), "11223344")
        self.assertEqual(ccs.normalize_color("nope"), "")
        self.assertEqual(ccs.normalize_color(""), "")

    def test_parse_host(self):
        self.assertEqual(ccs.parse_host("http://192.168.1.12:3030/x"), "192.168.1.12")
        self.assertEqual(ccs.parse_host(" 192.168.1.12 "), "192.168.1.12")
        self.assertEqual(ccs.parse_host(""), "")

    def test_resolve_filament_id(self):
        self.assertEqual(ccs.resolve_filament_id("ELEGOO", "PLA Matte", "PLA"), ("OFsB2lxm", "elegoo"))
        self.assertEqual(ccs.resolve_filament_id("Elegoo", "", "PETG"), ("OFLcd093", "elegoo"))
        self.assertEqual(ccs.resolve_filament_id("Polymaker", "PolyLite ABS", "ABS"), ("OFY9muEs", "generic"))
        self.assertEqual(ccs.resolve_filament_id("Foo", "Bar", "PVA"), ("", "type-only"))
        overrides = {"polymaker|polylite abs": "GFB99"}
        self.assertEqual(ccs.resolve_filament_id("Polymaker", "PolyLite ABS", "ABS", overrides), ("GFB99", "override"))

    def test_canvas_to_trays_order_and_presence(self):
        trays = ccs.canvas_to_trays(CANVAS_REPLY)
        self.assertEqual([t["label"] for t in trays], ["A1", "A2", "A3", "A4"])
        self.assertEqual(trays[0]["filament_type"], "PETG")
        self.assertEqual(trays[1]["filament_id"], "OFsB2lxm")
        self.assertEqual(trays[1]["color"], "1A2B3CFF")
        self.assertFalse(trays[2]["present"])
        self.assertEqual(trays[3]["id_source"], "generic")

    def test_push_status_shape(self):
        msg = ccs.build_push_status(ccs.canvas_to_trays(CANVAS_REPLY), 7)
        ams = msg["print"]["ams"]
        self.assertEqual(msg["print"]["command"], "push_status")
        self.assertEqual(msg["print"]["printer_type"], "Elegoo-CC")
        self.assertEqual(ams["ams_exist_bits"], "1")
        self.assertEqual(ams["tray_exist_bits"], "B")  # slots 0,1,3
        tray = ams["ams"][0]["tray"]
        self.assertEqual(tray[1]["tray_color"], "1A2B3CFF")
        self.assertEqual(tray[1]["cols"], ["1A2B3CFF"])
        self.assertEqual(tray[2]["tray_slot_placeholder"], "1")

    def test_extract_nested(self):
        self.assertIs(ccs.extract_canvas_result({"Data": {"result": CANVAS_REPLY}}), CANVAS_REPLY)
        self.assertIsNone(ccs.extract_canvas_result({"Ack": 0}))

    def test_describe_errors_are_actionable(self):
        text = ccs.describe_network_error(socket.timeout("timed out"), "10.0.0.9")
        self.assertIn("10.0.0.9", text)
        self.assertIn("IP address", text)
        self.assertIn("refused", ccs.describe_network_error(ConnectionRefusedError(), "h"))


# ---------------------------------------------------------------------------------------------
# Mock SDCP printer (WebSocket server on an ephemeral port)
# ---------------------------------------------------------------------------------------------

class MockPrinter:
    MAINBOARD = "2063141701045b4000000c0000000000"

    def __init__(self):
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.requests = []
        self.canvas = json.loads(json.dumps(CANVAS_REPLY))
        self.stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _send(conn, text, fragment=False):
        data = text.encode()
        def frame(op, payload, fin=True):
            head = bytes([(0x80 if fin else 0) | op])
            n = len(payload)
            head += bytes([n]) if n < 126 else (bytes([126]) + struct.pack("!H", n) if n < 65536 else bytes([127]) + struct.pack("!Q", n))
            return head + payload
        if fragment:
            mid = len(data) // 2
            conn.sendall(frame(0x1, data[:mid], False) + frame(0x9, b"hi") + frame(0x0, data[mid:], True))
        else:
            conn.sendall(frame(0x1, data))

    @staticmethod
    def _recv_frame(conn, buf):
        def need(n):
            nonlocal buf
            while len(buf) < n:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out
        h = need(2)
        op, n = h[0] & 0x0F, h[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", need(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", need(8))[0]
        assert h[1] & 0x80, "client frames must be masked"
        mask = need(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(need(n)))
        return op, payload, buf

    def _serve(self, conn):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(4096)
            key = re.search(rb"Sec-WebSocket-Key: (\S+)", head).group(1).decode()
            accept = base64.b64encode(hashlib.sha1((key + ccs.WebSocket.GUID).encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
            # unsolicited status push, like the real printer
            self._send(conn, json.dumps({"Status": {"CurrentStatus": [0]}, "MainboardID": self.MAINBOARD,
                                         "Topic": "sdcp/status/" + self.MAINBOARD}))
            buf = b""
            count = 0
            while not self.stop:
                op, payload, buf = self._recv_frame(conn, buf)
                if op == 0x8:
                    return
                if op != 0x1:
                    continue
                req = json.loads(payload)
                self.requests.append(req)
                data = req["Data"]
                body = {"Ack": 0}
                if data["Cmd"] == ccs.CMD_CANVAS_STATUS:
                    body = self.canvas
                elif data["Cmd"] == ccs.CMD_ATTRIBUTES:
                    self._send(conn, json.dumps({"Attributes": {"Name": "Mock CC", "MainboardID": self.MAINBOARD,
                                                                "FirmwareVersion": "V1.4.49"}}))
                    continue
                count += 1
                reply = {"Id": "x", "Data": {"Cmd": data["Cmd"], "Data": body, "RequestID": data["RequestID"],
                                              "MainboardID": self.MAINBOARD, "TimeStamp": 0},
                         "Topic": "sdcp/response/" + self.MAINBOARD}
                self._send(conn, json.dumps(reply), fragment=(count == 2))
        except (OSError, ConnectionError, AssertionError):
            pass
        finally:
            conn.close()

    def close(self):
        self.stop = True
        self.server.close()


class SdcpAgainstMock(unittest.TestCase):
    def setUp(self):
        self.printer = MockPrinter()
        self._port = ccs.SDCP_PORT
        ccs.SDCP_PORT = self.printer.port
        self._discover = ccs.discover_printers
        ccs.discover_printers = lambda *a, **k: []  # no UDP in tests

    def tearDown(self):
        ccs.SDCP_PORT = self._port
        ccs.discover_printers = self._discover
        self.printer.close()

    def test_client_learns_mainboard_and_reads_canvas(self):
        client = ccs.SdcpClient("127.0.0.1")
        client.connect()
        try:
            self.assertEqual(client.mainboard_id, MockPrinter.MAINBOARD)
            self.assertEqual(client.printer_name, "Mock CC")
            for _ in range(3):  # second reply is fragmented with a ping in between
                canvas = client.get_canvas()
                self.assertEqual(len(canvas["canvas_list"][0]["tray_list"]), 4)
        finally:
            client.close()
        cmd_req = [r for r in self.printer.requests if r["Data"]["Cmd"] == ccs.CMD_CANVAS_STATUS][0]
        self.assertEqual(cmd_req["Topic"], "sdcp/request/" + MockPrinter.MAINBOARD)
        self.assertEqual(cmd_req["Data"]["MainboardID"], MockPrinter.MAINBOARD)

    def test_agent_pushes_ams_and_reports_connect(self):
        ccs.load_settings = lambda: dict(ccs.DEFAULT_SETTINGS, poll_seconds=1.0)
        agent = ccs.CentauriCanvasAgent()
        events, messages = [], []
        got = threading.Event()
        agent.set_on_local_connect_fn(lambda s, d, m: events.append((s, d, m)))
        def on_msg(dev, text):
            messages.append((dev, json.loads(text)))
            got.set()
        agent.set_on_local_message_fn(on_msg)

        class Params:
            dev_id = "127.0.0.1_8080"
            host = "127.0.0.1"
        self.assertEqual(agent.connect_printer(Params()), 0)
        self.addCleanup(agent.disconnect_printer)
        self.assertTrue(got.wait(5), "no push_status received")
        self.assertEqual(events[0][0], ccs.CONNECT_OK)
        self.assertEqual(events[0][1], "127.0.0.1_8080")
        dev, msg = messages[0]
        self.assertEqual(dev, "127.0.0.1_8080")
        self.assertEqual(msg["print"]["ams"]["ams"][0]["tray"][1]["tray_info_idx"], "OFsB2lxm")

        # colour change on the printer + pushall from Orca -> fresh push
        tray0 = [t for t in self.printer.canvas["canvas_list"][0]["tray_list"] if t["tray_id"] == 0][0]
        tray0["filament_color"] = "#00FF00"
        got.clear()
        before = len(messages)
        agent.send_message_to_printer("127.0.0.1_8080", json.dumps({"pushing": {"command": "pushall"}}), 0, 0)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
                m["print"]["ams"]["ams"][0]["tray"][0]["tray_color"] == "00FF00FF" for _, m in messages[before:]):
            time.sleep(0.05)
        self.assertTrue(any(m["print"]["ams"]["ams"][0]["tray"][0]["tray_color"] == "00FF00FF"
                            for _, m in messages[before:]))
        t0 = time.monotonic()
        agent.disconnect_printer()
        self.assertLess(time.monotonic() - t0, 3.0)

    def test_agent_reports_failure_with_reason(self):
        ccs.load_settings = lambda: dict(ccs.DEFAULT_SETTINGS)
        self.printer.close()
        agent = ccs.CentauriCanvasAgent()
        done = threading.Event()
        events = []
        agent.set_on_local_connect_fn(lambda s, d, m: (events.append((s, d, m)), done.set()))

        class Params:
            dev_id = "dev"
            host = "127.0.0.1"
        agent.connect_printer(Params())
        self.assertTrue(done.wait(10))
        state, dev, msg = events[0]
        self.assertEqual(state, ccs.CONNECT_FAILED)
        self.assertIn("127.0.0.1", msg)
        agent.disconnect_printer()


if __name__ == "__main__":
    unittest.main()
