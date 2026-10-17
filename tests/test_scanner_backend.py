from __future__ import annotations

import ctypes
import ipaddress
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import scanner
from config import ScanConfig
from scanner import (
    ScanError,
    _ip_to_sendarp_value,
    _scan_sendarp,
    backend_status,
    resolve_backend,
    sendarp_available,
)


def make_config(backend: str) -> ScanConfig:
    network = ipaddress.ip_network("192.168.0.0/24")
    return ScanConfig(
        cidr=str(network),
        network=network,
        timeout_ms=1500,
        retries=1,
        backend=backend,
    )


class ScannerBackendTests(unittest.TestCase):
    def test_explicit_backends(self) -> None:
        self.assertEqual(resolve_backend(make_config("sendarp")), "sendarp")
        self.assertEqual(resolve_backend(make_config("scapy")), "scapy")

    def test_auto_backend(self) -> None:
        expected = "sendarp" if sys.platform == "win32" else "scapy"
        self.assertEqual(resolve_backend(make_config("auto")), expected)

    def test_sendarp_ip_conversion(self) -> None:
        self.assertEqual(_ip_to_sendarp_value("192.168.1.1"), 0x0101A8C0)
        self.assertEqual(_ip_to_sendarp_value("0.0.0.0"), 0)

    @unittest.skipUnless(sys.platform == "win32", "SendARP is Windows-only")
    def test_sendarp_backend_status(self) -> None:
        name, available, detail = backend_status(make_config("sendarp"))
        self.assertEqual(name, "SendARP")
        self.assertEqual(available, sendarp_available())
        self.assertIn("Npcap", detail)

    @unittest.skipUnless(sys.platform == "win32", "SendARP is Windows-only")
    def test_sendarp_scan_with_fake_provider(self) -> None:
        online_values = {
            _ip_to_sendarp_value("192.168.0.2"),
            _ip_to_sendarp_value("192.168.0.3"),
        }

        def fake_sendarp(dest, _src, mac_ptr, length_ptr):
            length = ctypes.cast(length_ptr, ctypes.POINTER(ctypes.c_ulong))
            if dest not in online_values:
                length.contents.value = 0
                return 67
            mac = ctypes.cast(mac_ptr, ctypes.POINTER(ctypes.c_ubyte))
            for index, value in enumerate((0x00, 0x11, 0x22, 0x33, 0x44, 0x55)):
                mac[index] = value
            length.contents.value = 6
            return 0

        with mock.patch.object(scanner, "_load_sendarp", return_value=fake_sendarp), \
             mock.patch.object(scanner, "_route_source_ip", return_value="192.168.0.1"):
            result = _scan_sendarp(make_config("sendarp"))

        self.assertEqual(result.backend, "sendarp")
        self.assertEqual(result.online_ips, ["192.168.0.2", "192.168.0.3"])

    @unittest.skipUnless(sys.platform == "win32", "SendARP is Windows-only")
    def test_sendarp_unknown_error_aborts_when_nothing_online(self) -> None:
        def fake_sendarp(_dest, _src, _mac_ptr, length_ptr):
            length = ctypes.cast(length_ptr, ctypes.POINTER(ctypes.c_ulong))
            length.contents.value = 0
            return 87

        with mock.patch.object(scanner, "_load_sendarp", return_value=fake_sendarp), \
             mock.patch.object(scanner, "_route_source_ip", return_value="192.168.0.1"):
            with self.assertRaises(ScanError):
                _scan_sendarp(make_config("sendarp"))


if __name__ == "__main__":
    unittest.main()
