# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# id = "centauri-canvas-sync"
# name = "CentauriSync"
# description = "Syncs the filament types and colours loaded in an Elegoo Centauri Carbon CANVAS unit into OrcaSlicer's filament slots (native Sync button)."
# author = "amitkuzi"
# version = "0.2.0"
# homepage = "https://amitkuzi.github.io/"
# ///
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Amit Kuzi. License and attribution: https://github.com/amitkuzi/OrcaPlugin
"""CentauriSync: Centauri Carbon CANVAS -> OrcaSlicer filament sync.

Two capabilities ship in this plugin:

* ``CentauriCanvasAgent`` - an OrcaSlicer *printer agent*. Selected through the printer preset's
  ``printer_agent`` option, it talks SDCP v1 (WebSocket ``ws://<ip>:3030/websocket``) to the
  printer, reads the CANVAS trays (command 324) and feeds them to OrcaSlicer in the AMS JSON
  dialect, so OrcaSlicer's own "Sync filaments" button fills the slots with the right presets and
  colours.
* ``CentauriCanvasSetup`` - a script with a small window: find/test the printer, preview what
  will be synced, and configure the active printer preset (agent + IP) in one click.

Only the Python standard library is used.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
import json
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path

try:  # Inside OrcaSlicer. The pure helpers below stay importable without it (unit tests).
    import orca  # type: ignore
except ImportError:  # pragma: no cover - exercised only outside OrcaSlicer
    orca = None

# --------------------------------------------------------------------------------------------
# Canonical version source. The PEP 723 header above must carry the same version string; the
# unit tests check that the two agree.
# --------------------------------------------------------------------------------------------
PLUGIN_VERSION = "0.2.0"
RELEASE_DATE = "2026-11-21"
RELEASE_STATUS = "prerelease"
AUTHOR_URL = "https://amitkuzi.github.io/"

AGENT_ID = "elegoo-cc-canvas"
AGENT_NAME = "Elegoo Centauri Carbon (CANVAS sync)"
# OrcaSlicer compares the connected machine with the preset through the vendor model_id
# (resources/profiles/Elegoo/machine/ECC/Elegoo Centauri Carbon.json).
DEFAULT_PRINTER_TYPE = "Elegoo-CC"

SDCP_PORT = 3030
SDCP_PATH = "/websocket"
DISCOVERY_PORT = 3000
CMD_STATUS = 0
CMD_ATTRIBUTES = 1
CMD_CANVAS_STATUS = 324

NETWORK_SUCCESS = 0
NETWORK_FAILURE = -1
CMD_NOT_SUPPORTED = -7010

CONNECT_OK = 0
CONNECT_FAILED = 1
CONNECT_LOST = 2

TRAYS_PER_UNIT = 4

# --------------------------------------------------------------------------------------------
# Filament id tables (OrcaSlicer v2.5.1 resources/profiles/Elegoo/filament/BASE). The CC presets
# "Elegoo <name> @ECC" inherit these ids, so OrcaSlicer resolves them to the CC presets.
# Generic fallback IDs also use resources/profiles/OrcaFilamentLibrary/filament.
# Keys are normalised with _norm().
# --------------------------------------------------------------------------------------------
ELEGOO_FILAMENT_IDS = {
    "abs": "OFxOHhZw",
    "asa": "OFobDOW5",
    "asa cf": "OFryjl35",
    "paht cf": "OF20kXr5",
    "pc": "OFUyDDNv",
    "pc fr": "OFlsTpJG",
    "pet cf": "OFe98A1y",
    "petg": "OFLcd093",
    "petg hf": "OFlP3KWq",
    "petg pro": "OFOsHDnB",
    "petg translucent": "OF6Ll8lK",
    "petg cf": "OFuUuIhR",
    "petg gf": "OFmveLWz",
    "pla": "OFvgE0Zh",
    "pla basic": "OFm06H6V",
    "pla galaxy": "OFU1zPxE",
    "pla glow": "OFjPXrXO",
    "pla marble": "OFfuhviw",
    "pla matte": "OFsB2lxm",
    "pla pro": "OF1hznGB",
    "pla silk": "OFXz7Mwx",
    "pla sparkle": "OFhcYzR8",
    "pla translucent": "OFn1QaY3",
    "pla translucent2": "OFn1QaY3",
    "pla wood": "OFWYtgCa",
    "pla+": "OFBUIlZ7",
    "pla plus": "OFBUIlZ7",
    "pla cf": "OFln72PT",
    "rapid petg": "OFLXvuMr",
    "rapid pla+": "OFkhhUS0",
    "rapid pla plus": "OFkhhUS0",
    "rapid tpu 95a": "OF30Ftju",
    "tpu 95a": "OFqHQNoZ",
    "tpu": "OFqHQNoZ",
}

GENERIC_FILAMENT_IDS = {
    "abs": "OFY9muEs",
    "asa": "OFLPAxz3",
    "pa": "OFg8ndtj",
    "pc": "OFLakOUI",
    "pet": "OF6MOPWx",
    "petg": "OFYPdQJh",
    "pla": "OFDSrzZ8",
}

_PLUGIN_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
SETTINGS_FILE = _PLUGIN_DIR / "settings.json"
FILAMENT_MAP_FILE = _PLUGIN_DIR / "filament_map.json"

DEFAULT_SETTINGS = {
    "host": "",
    "mainboard_id": "",
    "poll_seconds": 3.0,
    "printer_type": DEFAULT_PRINTER_TYPE,
}


PERMISSION_HINT = ("OrcaSlicer blocked the plugin from doing this: %s (%s). Allow 'CentauriSync' "
                   "when OrcaSlicer asks for permission (Plugins dialog → the plugin → permissions), "
                   "then try again.")


def _log(message: str) -> None:
    # stdout of embedded plugins ends up in OrcaSlicer's log; keep it short and secret-free.
    print("[centauri-canvas-sync] " + message, flush=True)


# ============================================================================================
# Pure helpers (no I/O) - unit tested
# ============================================================================================

def _norm(text) -> str:
    """Lower-case, trim, treat '-', '_' and runs of spaces as one space, drop an 'elegoo' prefix."""
    value = re.sub(r"[\s_\-]+", " ", str(text or "").strip().lower()).strip()
    if value.startswith("elegoo "):
        value = value[len("elegoo "):]
    return value


def normalize_color(value) -> str:
    """Printer colour ('#RRGGBB', '0xRRGGBB', 'RRGGBBAA', 'RGB') -> 'RRGGBBAA' upper-case.

    Unknown or malformed colours return '' so the caller can decide what to show."""
    text = str(value or "").strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    text = text.lstrip("#").strip()
    if not re.fullmatch(r"[0-9a-fA-F]+", text or "-"):
        return ""
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        return ""
    return text.upper()


def normalize_type(filament_type) -> str:
    """'pla ' -> 'PLA', 'petg cf' -> 'PETG-CF' (OrcaSlicer's filament_type spelling)."""
    value = re.sub(r"\s+", "-", str(filament_type or "").strip().upper())
    return value


def parse_host(host) -> str:
    """'http://192.168.1.12:3030/x' -> '192.168.1.12'. Empty stays empty."""
    text = str(host or "").strip()
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", text)
    text = text.split("/", 1)[0]
    text = text.rsplit("@", 1)[-1]
    if text.startswith("["):  # [IPv6]:port
        return text[1:text.find("]")] if "]" in text else text[1:]
    if text.count(":") == 1:
        text = text.split(":", 1)[0]
    return text


def resolve_filament_id(brand, name, filament_type, overrides=None):
    """Map a CANVAS tray to an OrcaSlicer filament_id.

    Returns (filament_id, source) where source is 'override', 'elegoo', 'generic' or 'type-only'.
    An empty id makes OrcaSlicer fall back to a preset of the same filament type."""
    overrides = overrides or {}
    b, n, t = _norm(brand), _norm(name), _norm(filament_type)
    for key in (f"{b}|{n}", f"{b}|{t} {n}", f"{b}|{t}", f"*|{n}", f"*|{t}"):
        if key in overrides and overrides[key]:
            return overrides[key], "override"
    if not b or "elegoo" in b:
        candidates = [n, f"{t} {n}".strip(), t]
        for candidate in candidates:
            if candidate and candidate in ELEGOO_FILAMENT_IDS:
                return ELEGOO_FILAMENT_IDS[candidate], "elegoo"
    base_type = t.split(" ")[0] if t else ""
    for candidate in (t, base_type):
        if candidate in GENERIC_FILAMENT_IDS:
            return GENERIC_FILAMENT_IDS[candidate], "generic"
    return "", "type-only"


def _as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_canvas_result(payload):
    """Find the CANVAS result object inside an SDCP response's Data.Data (firmware variants nest
    it under 'Data' or 'result'). Returns None if there is no canvas_list."""
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("canvas_list"), list):
                return node
            for key in ("Data", "data", "result", "Result"):
                if key in node:
                    stack.append(node[key])
    return None


def canvas_to_trays(canvas_result, overrides=None):
    """CANVAS result -> list of tray dicts in OrcaSlicer order (unit index, slot index).

    Each dict: unit, slot, label ('A1'..), present, filament_type, filament_id, id_source,
    color ('RRGGBBAA'), brand, name, status, note."""
    trays = []
    canvases = list((canvas_result or {}).get("canvas_list") or [])
    canvases.sort(key=lambda c: _as_int(c.get("canvas_id"), 0))
    for unit_index, canvas in enumerate(canvases):
        tray_list = list(canvas.get("tray_list") or [])
        tray_list.sort(key=lambda t: _as_int(t.get("tray_id"), 0))
        for slot_index, tray in enumerate(tray_list[:TRAYS_PER_UNIT]):
            brand = str(tray.get("brand") or "").strip()
            name = str(tray.get("filament_name") or "").strip()
            ftype = normalize_type(tray.get("filament_type"))
            status = _as_int(tray.get("status"), 0)
            color = normalize_color(tray.get("filament_color"))
            note = ""
            present = status in (1, 2) and bool(ftype or name)
            if status in (1, 2) and not present:
                note = "Filament detected but its type is not set on the printer."
            if present and not ftype:
                ftype = normalize_type(name.split(" ")[0])
            filament_id, source = ("", "")
            if present:
                filament_id, source = resolve_filament_id(brand, name, ftype, overrides)
                if not color:
                    color = "FFFFFFFF"
                    note = "The printer reported no colour; white is used."
            trays.append({
                "unit": unit_index,
                "slot": slot_index,
                "label": chr(ord("A") + unit_index) + str(slot_index + 1),
                "present": present,
                "filament_type": ftype if present else "",
                "filament_id": filament_id,
                "id_source": source,
                "color": color if present else "00000000",
                "brand": brand or ("Generic" if present else ""),
                "name": name or ftype,
                "status": status,
                "connected": bool(_as_int(canvas.get("connected"), 1)),
                "note": note,
            })
    return trays


def build_ams_json(trays):
    """Tray dicts -> OrcaSlicer/Bambu 'ams' object parsed by DevFilaSystemParser::ParseV1_0."""
    units = {}
    for tray in trays:
        units.setdefault(tray["unit"], []).append(tray)
    ams_list = []
    ams_exist_bits = 0
    tray_exist_bits = 0
    for unit_index in sorted(units):
        ams_exist_bits |= 1 << unit_index
        tray_json = []
        for tray in sorted(units[unit_index], key=lambda t: t["slot"]):
            entry = {"id": str(tray["slot"]), "tag_uid": "0000000000000000"}
            if tray["present"]:
                tray_exist_bits |= 1 << (unit_index * TRAYS_PER_UNIT + tray["slot"])
                entry.update({
                    "tray_info_idx": tray["filament_id"],
                    "tray_type": tray["filament_type"],
                    "tray_color": tray["color"],
                    "cols": [tray["color"]],
                    "ctype": 1,
                })
            else:
                entry.update({
                    "tray_info_idx": "",
                    "tray_type": "",
                    "tray_color": "00000000",
                    "tray_slot_placeholder": "1",
                })
            tray_json.append(entry)
        ams_list.append({"id": str(unit_index), "info": "0002", "tray": tray_json})
    return {
        "ams": ams_list,
        "ams_exist_bits": format(ams_exist_bits, "X"),
        "tray_exist_bits": format(tray_exist_bits, "X"),
    }


def build_push_status(trays, sequence_id, printer_type=DEFAULT_PRINTER_TYPE):
    """Full 'push_status' message, as OrcaSlicer's MachineObject::parse_json expects on LAN."""
    return {
        "print": {
            "command": "push_status",
            "msg": 0,
            "sequence_id": str(sequence_id),
            "printer_type": printer_type,
            "ams": build_ams_json(trays),
        }
    }


def build_sdcp_request(cmd, mainboard_id, request_id, data=None, timestamp_ms=None):
    """SDCP v1 request envelope (see elegoo-link elegoo_fdm_cc_message_adapter.cpp)."""
    return {
        "Id": mainboard_id,
        "Data": {
            "Cmd": cmd,
            "Data": data if data is not None else {},
            "RequestID": request_id,
            "MainboardID": mainboard_id,
            "TimeStamp": int(timestamp_ms if timestamp_ms is not None else time.time() * 1000),
            "From": 1,
        },
        "Topic": "sdcp/request/" + mainboard_id,
    }


def describe_network_error(error, host) -> str:
    """Human explanation + next step for a connection failure (no stack traces)."""
    where = f"{host}:{SDCP_PORT}"
    if isinstance(error, socket.timeout) or "timed out" in str(error).lower():
        return (f"No answer from the printer at {where} (timeout). Check that the Centauri Carbon "
                f"is powered on, on the same network, and that the IP address is correct.")
    if isinstance(error, ConnectionRefusedError):
        return (f"The printer at {where} refused the connection. Restart the printer, then try again.")
    if isinstance(error, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
        return (f"The connection to the printer at {where} was dropped. Check that the printer is on and "
                f"that no other program is holding its connection, then try again.")
    if isinstance(error, socket.gaierror):
        return f"'{host}' is not a valid IP address or host name. Enter the printer's IP address."
    if isinstance(error, OSError) and getattr(error, "winerror", None) in (10051, 10065):
        return (f"The network to {where} is unreachable. Check this computer's network connection "
                f"and the printer IP address.")
    if isinstance(error, SdcpError):
        return str(error)
    return f"Connecting to {where} failed ({type(error).__name__}: {error}). Check the printer IP and network."


def load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    settings.update(load_json_file(SETTINGS_FILE, {}))
    return settings


def save_settings(settings):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def load_overrides():
    raw = load_json_file(FILAMENT_MAP_FILE, {})
    result = {}
    for key, value in raw.items():
        if key.startswith("_") or not isinstance(value, str):
            continue
        brand, _, name = key.partition("|")
        result[f"{_norm(brand) if brand.strip() != '*' else '*'}|{_norm(name)}"] = value.strip()
    return result


# ============================================================================================
# Minimal RFC 6455 WebSocket client + SDCP client (stdlib only)
# ============================================================================================

class SdcpError(Exception):
    """A failure with a user-readable message."""


class WebSocketClosed(SdcpError):
    pass


class WebSocket:
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    MAX_HANDSHAKE = 65536
    MAX_MESSAGE = 1024 * 1024
    MAX_FRAGMENTS = 1024

    def __init__(self, host, port=SDCP_PORT, path=SDCP_PATH, timeout=5.0):
        if not host or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in host):
            raise SdcpError("Enter a valid printer IP address or hostname.")
        self.host = host
        deadline = time.monotonic() + timeout
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._send_timeout = timeout
        self._buffer = b""
        self._lock = threading.Lock()
        try:
            self._apply_deadline(deadline)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            authority = f"[{host}]" if ":" in host else host
            request = (f"GET {path} HTTP/1.1\r\nHost: {authority}:{port}\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
            self.sock.sendall(request.encode("ascii"))
            head = b""
            while b"\r\n\r\n" not in head:
                self._apply_deadline(deadline)
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise WebSocketClosed(f"The printer at {host}:{port} closed during the WebSocket handshake.")
                head += chunk
                if len(head) > self.MAX_HANDSHAKE and b"\r\n\r\n" not in head[:self.MAX_HANDSHAKE]:
                    raise SdcpError("The printer sent an oversized handshake reply.")
            head, self._buffer = head.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            fields = lines[0].split()
            if len(fields) < 2 or fields[:2] != ["HTTP/1.1", "101"]:
                raise SdcpError(f"The device at {host}:{port} is not an SDCP WebSocket server.")
            expected = base64.b64encode(hashlib.sha1((key + self.GUID).encode("ascii")).digest())
            headers = {}
            for line in lines[1:]:
                name, separator, value = line.partition(":")
                if not separator:
                    raise SdcpError("The printer's WebSocket handshake was malformed.")
                headers.setdefault(name.strip().lower(), []).append(value.strip())
            accept = headers.get("sec-websocket-accept", [])
            connection = {part.strip().lower() for item in headers.get("connection", []) for part in item.split(",")}
            if (len(accept) != 1 or not hmac.compare_digest(accept[0].encode("latin-1"), expected)
                    or [v.lower() for v in headers.get("upgrade", [])] != ["websocket"]
                    or "upgrade" not in connection):
                raise SdcpError("The printer's WebSocket handshake did not validate.")
            self.sock.settimeout(timeout)
        except BaseException:
            self.sock.close()
            raise

    def _apply_deadline(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("The printer's WebSocket read exceeded its deadline.")
        self.sock.settimeout(remaining)

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text):
        self.sock.settimeout(self._send_timeout)
        self._send_frame(0x1, text.encode("utf-8"))

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(header + mask + masked)

    def _read_exact(self, count, deadline, wait_first=False):
        """Read exactly count bytes, under one absolute deadline."""
        while len(self._buffer) < count:
            try:
                self._apply_deadline(deadline)
                chunk = self.sock.recv(min(65536, count - len(self._buffer)))
            except socket.timeout:
                if wait_first and not self._buffer:
                    return None
                raise
            if not chunk:
                raise WebSocketClosed("The printer closed the connection.")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def recv_text(self, timeout):
        """Next JSON text frame/message, or None if idle before any data."""
        deadline = time.monotonic() + timeout
        parts = []
        size = 0
        try:
            while True:
                head = self._read_exact(2, deadline, wait_first=not parts)
                if head is None:
                    return None
                fin, opcode = bool(head[0] & 0x80), head[0] & 0x0F
                if head[0] & 0x70 or head[1] & 0x80:
                    raise SdcpError("The printer sent an unsupported WebSocket frame.")
                length = head[1]
                if opcode >= 8 and (not fin or length > 125):
                    raise SdcpError("The printer sent an invalid WebSocket control frame.")
                if length == 126:
                    length = struct.unpack("!H", self._read_exact(2, deadline))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._read_exact(8, deadline))[0]
                if length > self.MAX_MESSAGE or (opcode < 8 and size + length > self.MAX_MESSAGE):
                    raise SdcpError("The printer's WebSocket message exceeded the size limit.")
                payload = self._read_exact(length, deadline)
                if opcode == 0x8:
                    raise WebSocketClosed("The printer closed the connection.")
                if opcode == 0x9:
                    self._apply_deadline(deadline)
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if ((opcode == 0x1 and not parts) or (opcode == 0x0 and parts)):
                    parts.append(payload)
                    size += length
                    if len(parts) > self.MAX_FRAGMENTS:
                        raise SdcpError("The printer's WebSocket message contained too many fragments.")
                    if fin:
                        return b"".join(parts).decode("utf-8")
                    continue
                raise SdcpError("The printer sent an unexpected WebSocket frame type.")
        except (OSError, UnicodeDecodeError, SdcpError):
            # A failed or partial frame leaves the stream unsynchronized.
            self.sock.close()
            raise


def discover_printers(target="255.255.255.255", timeout=2.0):
    """SDCP UDP discovery ('M99999' to port 3000). Returns a list of dicts
    {host, mainboard_id, name, model, firmware}."""
    found = {}
    targets = [target]
    if target == "255.255.255.255":  # with several adapters (Hyper-V, VPN) also hit each /24 broadcast
        try:
            local_ips = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        except OSError:
            local_ips = set()
        targets += sorted({ip.rsplit(".", 1)[0] + ".255" for ip in local_ips if not ip.startswith("127.")})
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.3)
        for address in targets:
            try:
                sock.sendto(b"M99999", (address, DISCOVERY_PORT))
            except OSError:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, (ip, _port) = sock.recvfrom(65536)
            except socket.timeout:
                continue
            try:
                reply = json.loads(data.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            info = reply.get("Data") if isinstance(reply, dict) else None
            if not isinstance(info, dict):
                continue
            found[ip] = {
                "host": ip,
                "mainboard_id": str(info.get("MainboardID") or reply.get("Id") or ""),
                "name": str(info.get("Name") or ""),
                "model": str(info.get("MachineName") or ""),
                "firmware": str(info.get("FirmwareVersion") or ""),
            }
            if target != "255.255.255.255":
                break
    finally:
        sock.close()
    return list(found.values())


class SdcpClient:
    """One SDCP v1 session over WebSocket. Not thread-safe: use from one thread."""

    def __init__(self, host, mainboard_id="", timeout=5.0):
        self.host = parse_host(host)
        self.mainboard_id = mainboard_id or ""
        self.timeout = timeout
        self.ws = None
        self.printer_name = ""
        self.firmware = ""

    def connect(self):
        if not self.host:
            raise SdcpError("No printer IP address is set. Enter the Centauri Carbon's IP address.")
        if not self.mainboard_id:
            try:
                replies = discover_printers(self.host, timeout=1.5)
            except OSError:
                replies = []
            if replies:
                self.mainboard_id = replies[0]["mainboard_id"]
                self.printer_name = replies[0]["name"]
                self.firmware = replies[0]["firmware"]
        self.ws = WebSocket(self.host, SDCP_PORT, SDCP_PATH, timeout=self.timeout)
        if not self.mainboard_id:
            self._learn_mainboard_id()

    def _learn_mainboard_id(self):
        """Ask for the attributes; the reply (or any status push) carries the MainboardID."""
        try:
            reply = self.request(CMD_ATTRIBUTES, timeout=3.0)
            attributes = reply.get("Attributes") if isinstance(reply, dict) else None
            if isinstance(attributes, dict) and attributes.get("MainboardID"):
                self.mainboard_id = str(attributes["MainboardID"])
        except SdcpError:
            pass

    def close(self):
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def _remember_identity(self, message):
        if not isinstance(message, dict):
            return
        for container in (message, message.get("Data"), message.get("Attributes"), message.get("Status")):
            if isinstance(container, dict) and container.get("MainboardID") and not self.mainboard_id:
                self.mainboard_id = str(container["MainboardID"])
        attributes = message.get("Attributes")
        if isinstance(attributes, dict):
            self.printer_name = str(attributes.get("Name") or self.printer_name)
            self.firmware = str(attributes.get("FirmwareVersion") or self.firmware)

    def request(self, cmd, data=None, timeout=None):
        """Send one command and return the response's Data.Data object."""
        if self.ws is None:
            raise SdcpError("Not connected to the printer.")
        request_id = os.urandom(8).hex()
        self.ws.send_text(json.dumps(build_sdcp_request(cmd, self.mainboard_id, request_id, data)))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SdcpError(f"The printer did not answer command {cmd} within {timeout or self.timeout:.0f} s. "
                                f"Its firmware may not support it; update the Centauri Carbon firmware.")
            text = self.ws.recv_text(remaining)
            if text is None:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                continue
            self._remember_identity(message)
            if cmd == CMD_ATTRIBUTES and isinstance(message, dict) and isinstance(message.get("Attributes"), dict):
                return message
            data_obj = message.get("Data") if isinstance(message, dict) else None
            if isinstance(data_obj, dict) and data_obj.get("RequestID") == request_id:
                inner = data_obj.get("Data")
                inner = inner if isinstance(inner, dict) else {}
                ack = inner.get("Ack", 0)
                if isinstance(ack, int) and ack != 0:
                    raise SdcpError(f"The printer rejected command {cmd} (Ack={ack}).")
                return inner

    def get_canvas(self):
        reply = self.request(CMD_CANVAS_STATUS)
        result = extract_canvas_result(reply)
        if result is None:
            raise SdcpError("The printer answered without CANVAS data. Check that the CANVAS unit is "
                            "connected and the printer firmware is 1.44 or above per Elegoo.")
        return result


# ============================================================================================
# OrcaSlicer capabilities
# ============================================================================================

if orca is not None:
    _AgentBase = orca.printer_agent.PrinterAgentBase
    _ScriptBase = orca.script.ScriptPluginCapabilityBase
else:  # pragma: no cover - lets the tests import the module without OrcaSlicer
    class _AgentBase:  # noqa: D401 - stub
        def __init__(self):
            pass

    class _ScriptBase:
        def __init__(self):
            pass


class CentauriCanvasAgent(_AgentBase):
    """OrcaSlicer printer agent: pushes CANVAS trays as AMS data (Subscription filament sync)."""

    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self._callbacks = {}
        self._selected = ""
        self._worker = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._dev_id = ""
        self._host = ""
        self._sequence = 0
        self.last_trays = []
        self.last_error = ""

    # ---- identity ---------------------------------------------------------------------------
    def get_name(self):
        return AGENT_NAME

    def get_agent_info(self):
        return orca.printer_agent.AgentInfo(AGENT_ID, AGENT_NAME, PLUGIN_VERSION,
                                            "Reads the Centauri Carbon CANVAS trays over the LAN (SDCP) and "
                                            "syncs filament types and colours. v%s (%s)" % (PLUGIN_VERSION, RELEASE_DATE))

    def get_filament_sync_mode(self):
        return orca.printer_agent.FilamentSyncMode.Subscription

    def get_camera_stream_mode(self):
        return orca.printer_agent.CameraStreamMode.None_

    def get_camera_url(self):
        return ""

    # ---- callbacks --------------------------------------------------------------------------
    def _set_cb(self, name, fn):
        with self._lock:
            self._callbacks[name] = fn
        return NETWORK_SUCCESS

    def _cb(self, name):
        with self._lock:
            return self._callbacks.get(name)

    def set_on_ssdp_msg_fn(self, fn): return self._set_cb("ssdp", fn)
    def set_on_printer_connected_fn(self, fn): return self._set_cb("printer_connected", fn)
    def set_on_subscribe_failure_fn(self, fn): return self._set_cb("subscribe_failure", fn)
    def set_on_message_fn(self, fn): return self._set_cb("message", fn)
    def set_on_user_message_fn(self, fn): return self._set_cb("user_message", fn)
    def set_on_local_connect_fn(self, fn): return self._set_cb("local_connect", fn)
    def set_on_local_message_fn(self, fn): return self._set_cb("local_message", fn)
    def set_queue_on_main_fn(self, fn): return self._set_cb("queue_on_main", fn)
    def set_server_callback(self, fn): return self._set_cb("server", fn)

    def _emit_connect(self, state, dev_id, msg):
        fn = self._cb("local_connect")
        if fn:
            try:
                fn(int(state), dev_id, msg)
            except Exception as error:  # never let a host callback kill the worker
                _log("local_connect callback failed: %s" % error)

    def _emit_message(self, dev_id, payload):
        fn = self._cb("local_message")
        if fn:
            try:
                fn(dev_id, json.dumps(payload, separators=(",", ":")))
            except Exception as error:
                _log("local_message callback failed: %s" % error)

    # ---- connection -------------------------------------------------------------------------
    def connect_printer(self, params):
        settings = load_settings()
        host = parse_host(getattr(params, "host", "")) or parse_host(settings.get("host"))
        dev_id = getattr(params, "dev_id", "") or host
        if not host:
            self.last_error = "No printer IP address: set it in the printer preset (Hostname/IP) or run 'Centauri CANVAS setup'."
            self._emit_connect(CONNECT_FAILED, dev_id, self.last_error)
            return NETWORK_FAILURE
        self.disconnect_printer()
        with self._lock:
            self._dev_id, self._host = dev_id, host
            self._stop = threading.Event()
            self._wake = threading.Event()
            stop, wake = self._stop, self._wake
            worker = threading.Thread(target=self._run, name="centauri-canvas-sync",
                                      args=(dev_id, host, settings, stop, wake), daemon=True)
            try:
                # OrcaSlicer audits thread creation: the first time it asks the user to allow it.
                worker.start()
            except Exception as error:
                self.last_error = PERMISSION_HINT % ("start its background connection", error)
                self._emit_connect(CONNECT_FAILED, dev_id, self.last_error)
                return NETWORK_FAILURE
            self._worker = worker
        _log("connecting to %s (dev_id=%s)" % (host, dev_id))
        return NETWORK_SUCCESS

    def disconnect_printer(self):
        with self._lock:
            worker, stop = self._worker, self._stop
            self._worker = None
        stop.set()
        self._wake.set()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.5)
        return NETWORK_SUCCESS

    def _run(self, dev_id, host, settings, stop, wake):
        printer_type = str(settings.get("printer_type") or DEFAULT_PRINTER_TYPE)
        poll = max(1.0, float(settings.get("poll_seconds") or 3.0))
        mainboard_id = str(settings.get("mainboard_id") or "")
        connected_once = False
        failures = 0
        while not stop.is_set():
            client = SdcpClient(host, mainboard_id)
            try:
                client.connect()
                canvas = client.get_canvas()
                mainboard_id = client.mainboard_id or mainboard_id
                if not connected_once:
                    connected_once = True
                    self._emit_connect(CONNECT_OK, dev_id, "0")
                    _log("connected to %s (%s)" % (host, client.printer_name or "Centauri Carbon"))
                failures = 0
                self.last_error = ""
                while not stop.is_set():
                    self._publish(dev_id, canvas, printer_type)
                    wake.wait(poll)
                    wake.clear()
                    if stop.is_set():
                        break
                    canvas = client.get_canvas()
            except Exception as error:  # report, then retry or give up
                self.last_error = describe_network_error(error, host)
                failures += 1
                _log("connection problem: %s" % self.last_error)
                if not connected_once:
                    if not stop.is_set():
                        self._emit_connect(CONNECT_FAILED, dev_id, self.last_error)
                    return
                if failures >= 4:
                    if not stop.is_set():
                        self._emit_connect(CONNECT_LOST, dev_id, self.last_error)
                    return
                stop.wait(min(10.0, 2.0 * failures))
            finally:
                client.close()

    def _publish(self, dev_id, canvas, printer_type):
        trays = canvas_to_trays(canvas, load_overrides())
        self.last_trays = trays
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        self._emit_message(dev_id, build_push_status(trays, sequence, printer_type))

    # ---- messages from OrcaSlicer -----------------------------------------------------------
    def send_message_to_printer(self, dev_id, json_str, qos, flag):
        try:
            message = json.loads(json_str)
        except ValueError:
            return NETWORK_FAILURE
        if not isinstance(message, dict):
            return NETWORK_FAILURE
        if isinstance(message.get("pushing"), dict) or isinstance(message.get("print"), dict) and \
                message["print"].get("command") in ("pushall", "push_status"):
            self._wake.set()  # publish fresh CANVAS data now
            return NETWORK_SUCCESS
        info = message.get("info")
        if isinstance(info, dict) and info.get("command") == "get_version":
            self._emit_message(dev_id or self._dev_id, {"info": {
                "command": "get_version", "sequence_id": str(info.get("sequence_id", "0")),
                "module": [{"name": "ota", "sw_ver": PLUGIN_VERSION, "hw_ver": "", "sn": dev_id}]}})
            return NETWORK_SUCCESS
        return CMD_NOT_SUPPORTED  # read-only agent: never acknowledge unhandled device commands

    def send_message(self, dev_id, json_str, qos, flag):
        return self.send_message_to_printer(dev_id, json_str, qos, flag)

    # ---- discovery / binding (LAN only, nothing to bind) ------------------------------------
    def start_discovery(self, start, sending):
        return True

    def bind_detect(self, dev_ip, sec_link, detect):
        detect.dev_id = dev_ip
        detect.dev_name = "Elegoo Centauri Carbon"
        detect.model_id = DEFAULT_PRINTER_TYPE
        detect.connect_type = "lan"
        detect.bind_state = "free"
        detect.version = PLUGIN_VERSION
        return NETWORK_SUCCESS

    def get_user_selected_machine(self):
        return self._selected

    def set_user_selected_machine(self, dev_id):
        self._selected = dev_id
        return NETWORK_SUCCESS

    def fetch_filament_info(self, dev_id, sync_mode):
        self._wake.set()  # Subscription mode asks for fresh read-only tray data.
        return True

    # ---- printing is not handled by this agent ----------------------------------------------
    def _no_print(self, *args):
        _log("printing through the CANVAS sync agent is not supported; use Elegoo's upload/print host")
        return CMD_NOT_SUPPORTED

    def start_send_gcode_to_sdcard(self, params, update_fn, cancel_fn, wait_fn): return self._no_print()
    def start_local_print(self, params, update_fn, cancel_fn): return self._no_print()
    def start_print(self, params, update_fn, cancel_fn, wait_fn): return self._no_print()
    def start_local_print_with_record(self, params, update_fn, cancel_fn, wait_fn): return self._no_print()
    def start_sdcard_print(self, params, update_fn, cancel_fn): return self._no_print()

    def on_unload(self):
        self.disconnect_printer()


# --------------------------------------------------------------------------------------------
# Setup window
# --------------------------------------------------------------------------------------------

PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><style>
:root{color-scheme:light dark;--a:#087f78;--line:rgba(127,127,127,.3);--soft:rgba(127,127,127,.1);--err:#c0392b;--ok:#1e8449}
*{box-sizing:border-box}body{margin:0;font:13px/1.45 var(--orca-font,system-ui,sans-serif);color:var(--orca-fg,#20242a);background:var(--orca-bg,#fafafa);display:flex;flex-direction:column;height:100vh}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:10px}h1{font-size:15px;margin:0}.ver{opacity:.65;font-size:11px;margin-left:auto}
main{padding:14px 16px;overflow:auto;flex:1;display:flex;flex-direction:column;gap:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}input{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:6px;padding:6px 8px;min-width:180px}
button{font:inherit;color:inherit;padding:6px 12px;border-radius:6px;border:1px solid var(--line);background:transparent;cursor:pointer}button.primary{background:var(--a);border-color:var(--a);color:#fff}button:disabled{opacity:.45;cursor:default}
.msg{padding:8px 10px;border-radius:6px;background:var(--soft);white-space:pre-wrap}.msg.err{border-left:3px solid var(--err)}.msg.ok{border-left:3px solid var(--ok)}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left}th{font-size:11px;text-transform:uppercase;opacity:.65}
.sw{display:inline-block;width:16px;height:16px;border-radius:4px;border:1px solid var(--line);vertical-align:middle}.muted{opacity:.65}.small{font-size:11px}
ol{margin:0;padding-left:18px}
</style></head><body>
<header><h1>CentauriSync</h1><span class="ver" id="ver"></span></header>
<main>
<div class="row"><label for="host">Printer IP</label><input id="host" placeholder="192.168.1.12"><button id="find">Find printer</button><button id="test" class="primary">Test &amp; preview</button></div>
<div id="msg" class="msg">Enter the printer's IP address (printer screen: Settings → Network), or press "Find printer".</div>
<div id="trays"></div>
<div class="row"><button id="setup" class="primary">Use this printer in the current printer preset</button><span class="small muted" id="preset"></span></div>
<div class="small muted"><ol>
<li>Select an "Elegoo Centauri Carbon" printer preset in OrcaSlicer.</li>
<li>Press the button above: it sets Printer Agent = "Elegoo Centauri Carbon (CANVAS sync)" and the printer IP, then save the printer preset.</li>
<li>Click the Sync filaments button (↻) next to "Filament" in the sidebar.</li></ol></div>
<p class="small">By Amit Kuzi · <a href="__CENTauri_AUTHOR_URL__" target="_blank" rel="noopener noreferrer">Author website</a></p>
</main>
<script>
const $=id=>document.getElementById(id);let busy=false;
function esc(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function setBusy(b){busy=b;['find','test','setup'].forEach(i=>$(i).disabled=b)}
function say(text,kind){$('msg').textContent=text;$('msg').className='msg'+(kind?' '+kind:'')}
function send(command){setBusy(true);say('Working…');orca.postMessage({command,host:$('host').value.trim()})}
function trays(list){if(!list||!list.length){$('trays').innerHTML='';return}
 $('trays').innerHTML='<table><tr><th>Slot</th><th>Colour</th><th>Filament</th><th>Orca match</th><th>Note</th></tr>'+list.map(t=>t.present?
 `<tr><td>${esc(t.label)}</td><td><span class="sw" style="background:#${esc(t.color.slice(0,6))}"></span> #${esc(t.color.slice(0,6))}</td><td>${esc(t.brand)} ${esc(t.name)} <span class="muted">(${esc(t.filament_type)})</span></td><td>${esc(t.match)}</td><td class="small">${esc(t.note)}</td></tr>`:
 `<tr><td>${esc(t.label)}</td><td>—</td><td class="muted">empty</td><td></td><td class="small">${esc(t.note)}</td></tr>`).join('')+'</table>'}
orca.onMessage(m=>{setBusy(false);if(m.version)$('ver').textContent='v'+m.version+' · '+m.date+(m.releaseStatus==='prerelease'?' · prerelease':'');if(m.host!==undefined&&!$('host').value)$('host').value=m.host;
 if(m.preset!==undefined)$('preset').textContent=m.preset;if(m.trays)trays(m.trays);if(m.error)say(m.error,'err');else if(m.message)say(m.message,'ok')});
$('find').onclick=()=>send('find');$('test').onclick=()=>send('test');$('setup').onclick=()=>send('setup');
orca.postMessage({command:'hello'});
</script></body></html>'''.replace('__CENTauri_AUTHOR_URL__', AUTHOR_URL)


def _match_label(tray):
    source = tray.get("id_source")
    if source == "elegoo":
        return "Elegoo preset (%s)" % tray["filament_id"]
    if source == "override":
        return "filament_map.json (%s)" % tray["filament_id"]
    if source == "generic":
        return "Generic %s" % tray["filament_type"]
    return "by type: %s" % tray["filament_type"]


class CentauriCanvasSetup(_ScriptBase):
    window = None
    _commands = None
    _cancelled = False

    def get_name(self):
        return "Centauri CANVAS setup"

    def execute(self):
        import queue
        if self.window is not None and self.window.is_open():
            return orca.ExecutionResult.success("The Centauri CANVAS setup window is already open.")
        commands = queue.Queue()
        self._commands = commands
        self._cancelled = False
        try:
            self.window = orca.host.ui.create_window(
                title="CentauriSync v%s" % PLUGIN_VERSION, html=PAGE, width=760, height=560,
                on_message=self._on_message, on_close=lambda: commands.put({"command": "close"}))
            while True:
                message = commands.get()
                if self._cancelled or message.get("command") == "close":
                    return orca.ExecutionResult.success("Centauri CANVAS setup closed.")
                try:
                    reply = self._handle(message)
                except Exception as error:
                    reply = {"error": "Changing the printer preset failed (%s: %s). Select an 'Elegoo Centauri "
                                      "Carbon' printer preset and try again." % (type(error).__name__, error)}
                self._post(reply)
        except Exception as error:
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError,
                                                "Centauri CANVAS setup could not open its window: %s" % error)
        finally:
            if self._commands is commands:
                self._commands = None
            self.window = None

    def on_cancelled(self):
        self._cancelled = True
        if self._commands is not None:
            self._commands.put({"command": "close"})

    # UI thread: read presets here (never from the worker), hand network work to the worker.
    def _on_message(self, message):
        message = dict(message) if isinstance(message, dict) else {}
        if message.get("command") == "hello":
            settings = load_settings()
            if self.window is not None and self.window.is_open():
                self.window.post({"version": PLUGIN_VERSION, "date": RELEASE_DATE, "releaseStatus": RELEASE_STATUS,
                                  "host": settings.get("host", ""), "preset": self._preset_summary()})
            return
        if message.get("command") == "setup":
            # Preset edits must run on the Script worker (begin_update blocks on dialogs).
            message["preset_model"] = self._current_printer_model()
            preset = self._current_printer_preset()
            try:
                message["previous_print_host"] = str(preset.config_value("print_host") or "")
            except Exception:
                message["previous_print_host"] = ""
            if self._commands is not None:
                self._commands.put(message)
            return
        # Network work runs on a thread of our own so the UI stays responsive.
        try:
            threading.Thread(target=self._run_and_post, args=(message,), daemon=True).start()
        except Exception as error:
            self._post({"error": PERMISSION_HINT % ("start a background task", error)})

    def _post(self, reply):
        window = self.window
        if window is not None and window.is_open():
            window.post(reply)

    def _run_and_post(self, message):
        try:
            reply = self._handle(message)
        except Exception as error:
            reply = {"error": "Unexpected failure (%s: %s). Try again; if it repeats, check the OrcaSlicer "
                              "log for 'centauri-canvas-sync'." % (type(error).__name__, error)}
        self._post(reply)

    def _current_printer_preset(self):
        try:
            return orca.host.preset_bundle().current_printer_preset()
        except Exception:
            return None

    def _current_printer_model(self):
        preset = self._current_printer_preset()
        if preset is None:
            return ""
        try:
            return str(preset.config_value("printer_model") or "")
        except Exception:
            return ""

    def _preset_summary(self):
        preset = self._current_printer_preset()
        if preset is None:
            return "Current printer preset: unknown"
        try:
            agent = preset.config_value("printer_agent") or "(default)"
            host = preset.config_value("print_host") or "(not set)"
        except Exception:
            agent, host = "?", "?"
        return "Current printer preset: %s · agent: %s · IP: %s" % (preset.name, agent, host)

    def _handle(self, message):
        command = message.get("command")
        host = parse_host(message.get("host"))
        if command == "find":
            printers = discover_printers()
            if not printers:
                return {"error": "No Elegoo printer answered on this network within 2 s. Check that the printer "
                                 "is on and on the same network, or type its IP address (printer screen: "
                                 "Settings → Network)."}
            first = printers[0]
            save_settings({**load_settings(), "host": first["host"], "mainboard_id": first["mainboard_id"]})
            names = ", ".join("%s (%s)" % (p["name"] or p["model"] or "Elegoo", p["host"]) for p in printers)
            return {"host": first["host"], "message": "Found: %s. Press 'Test & preview'." % names}
        if command == "test":
            if not host:
                return {"error": "Enter the printer's IP address first."}
            client = SdcpClient(host)
            try:
                client.connect()
                canvas = client.get_canvas()
            except Exception as error:
                return {"error": describe_network_error(error, host)}
            finally:
                client.close()
            save_settings({**load_settings(), "host": host, "mainboard_id": client.mainboard_id})
            trays = canvas_to_trays(canvas, load_overrides())
            for tray in trays:
                tray["match"] = _match_label(tray) if tray["present"] else ""
            loaded = sum(1 for t in trays if t["present"])
            if not trays:
                return {"trays": [], "error": "Connected, but the printer reports no CANVAS unit. Connect "
                                              "the CANVAS and check it on the printer screen."}
            return {"trays": trays, "message": "Connected to %s%s. %d of %d trays loaded." % (
                client.printer_name or "Centauri Carbon", " (fw %s)" % client.firmware if client.firmware else "",
                loaded, len(trays))}
        if command == "setup":
            if not host:
                return {"error": "Enter the printer's IP address first, then press the button again."}
            model = message.get("preset_model") or ""
            if model.strip().casefold() != "elegoo centauri carbon":
                return {"error": "The selected printer preset is '%s', not an Elegoo Centauri Carbon. Select "
                                 "an 'Elegoo Centauri Carbon' printer preset, then press the button again."
                                 % (model or "unknown")}
            transaction = orca.host.preset.printer.begin_update("")
            transaction.set("printer_agent", AGENT_ID)
            previous_host = str(message.get("previous_print_host") or "")
            if parse_host(previous_host).casefold() != host.casefold():
                transaction.set("print_host", host)
            result = transaction.commit()
            save_settings({**load_settings(), "host": host})
            return {"message": "Printer preset '%s' now uses the CANVAS sync agent with IP %s.%s Save the "
                               "printer preset, then click the Sync filaments button next to 'Filament'."
                               % (result.target_name, host, " It is unsaved:" if result.dirty else ""),
                    "preset": "Current printer preset: %s · agent: %s · IP: %s" % (result.target_name, AGENT_ID, host)}
        return {"error": "Unknown command '%s'." % command}


if orca is not None:
    @orca.plugin
    class CentauriCanvasSyncPackage(orca.base):
        def register_capabilities(self):
            orca.register_capability(CentauriCanvasAgent)
            orca.register_capability(CentauriCanvasSetup)
