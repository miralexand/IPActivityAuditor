from __future__ import annotations

import ctypes
import ipaddress
import os
import socket
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from config import ScanConfig


class ScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanResult:
    online_ips: list[str]
    duration_ms: int
    iface: str
    backend: str = "scapy"


_SENDARP = None
_NO_RESPONSE_CODES = {
    31,   # ERROR_GEN_FAILURE
    67,   # ERROR_BAD_NET_NAME
    121,  # ERROR_SEM_TIMEOUT
    1168, # ERROR_NOT_FOUND
    1231, # ERROR_NETWORK_UNREACHABLE
    1232, # ERROR_HOST_UNREACHABLE
    1233, # ERROR_PROTOCOL_UNREACHABLE
    1234, # ERROR_PORT_UNREACHABLE
    1235, # ERROR_REQUEST_ABORTED
}
_FATAL_CODES = {
    5,    # ERROR_ACCESS_DENIED
    87,   # ERROR_INVALID_PARAMETER
}


def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def resolve_backend(config: ScanConfig) -> str:
    backend = (config.backend or "auto").strip().lower()
    if backend == "auto":
        return "sendarp" if sys.platform == "win32" else "scapy"
    return backend


def npcap_present() -> bool:
    if sys.platform != "win32":
        return False
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [
        system_root / "System32" / "Npcap" / "wpcap.dll",
        system_root / "System32" / "wpcap.dll",
        system_root / "SysWOW64" / "Npcap" / "wpcap.dll",
        system_root / "SysWOW64" / "wpcap.dll",
    ]
    return any(path.exists() for path in candidates)


def sendarp_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        _load_sendarp()
        return True
    except ScanError:
        return False


def backend_status(config: ScanConfig | None = None) -> tuple[str, bool, str]:
    if config is not None:
        backend = resolve_backend(config)
    else:
        backend = "sendarp" if sys.platform == "win32" else "scapy"
    if backend == "sendarp":
        available = sendarp_available()
        detail = (
            "Windows 原生 ARP 扫描，无需 Npcap"
            if available
            else "当前系统无法加载 SendARP"
        )
        return "SendARP", available, detail
    available = npcap_present()
    detail = (
        "Npcap 已就绪"
        if available
        else "Scapy 后端需要安装 Npcap 并启用 WinPcap 兼容模式"
    )
    return "Scapy", available, detail


def _load_sendarp():
    global _SENDARP
    if _SENDARP is not None:
        return _SENDARP
    if sys.platform != "win32":
        raise ScanError("SendARP 扫描后端仅支持 Windows")
    try:
        function = ctypes.windll.iphlpapi.SendARP
    except Exception as exc:
        raise ScanError(f"无法加载 Windows SendARP：{exc}") from exc
    function.argtypes = [
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.ULONG),
    ]
    function.restype = wintypes.ULONG
    _SENDARP = function
    return function


def _ip_to_sendarp_value(address: str) -> int:
    return struct.unpack("<I", socket.inet_aton(address))[0]


def _route_source_ip(network: ipaddress.IPv4Network) -> str:
    if network.num_addresses > 2:
        probe = str(network.network_address + 1)
    else:
        probe = str(network.network_address)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((probe, 9))
        return str(sock.getsockname()[0])
    except OSError as exc:
        raise ScanError(f"无法确定扫描网卡：{exc}") from exc
    finally:
        sock.close()


def _ensure_local_source(network: ipaddress.IPv4Network, source_ip: str) -> None:
    try:
        source_in_network = ipaddress.ip_address(source_ip) in network
    except ValueError:
        source_in_network = False
    if not source_in_network:
        raise ScanError(
            f"本机没有位于 {network} 网段的地址（路由选中的源地址为 {source_ip}），"
            "无法执行 ARP 扫描，请检查网卡与配置"
        )


def _probe_sendarp(function, value: int, retries: int) -> tuple[bool, int]:
    last_code = 0
    for attempt in range(retries + 1):
        mac = (ctypes.c_ubyte * 8)()
        length = wintypes.ULONG(6)
        code = int(
            function(
                value,
                0,
                ctypes.cast(mac, ctypes.c_void_p),
                ctypes.byref(length),
            )
        )
        if code == 0 and length.value >= 6 and any(mac[:6]):
            return True, 0
        last_code = code
        if code in _FATAL_CODES:
            raise ScanError(f"SendARP 返回错误码 {code}，扫描已中止")
        if attempt < retries:
            time.sleep(0.05)
    return False, last_code


def _scan_sendarp(config: ScanConfig) -> ScanResult:
    if sys.platform != "win32":
        raise ScanError("SendARP 扫描后端仅支持 Windows")
    function = _load_sendarp()
    network = config.network
    source_ip = _route_source_ip(network)
    _ensure_local_source(network, source_ip)

    hosts = [str(ip) for ip in network.hosts()]
    if not hosts:
        return ScanResult(
            online_ips=[],
            duration_ms=0,
            iface=f"SendARP ({source_ip})",
            backend="sendarp",
        )

    started = time.perf_counter()
    workers = min(128, max(len(hosts), 1))
    online: list[str] = []
    errors: dict[int, int] = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_probe_sendarp, function, _ip_to_sendarp_value(ip), config.retries): ip
                for ip in hosts
            }
            for future in as_completed(futures):
                address = futures[future]
                try:
                    alive, code = future.result()
                except ScanError:
                    raise
                except Exception:
                    continue
                if alive:
                    online.append(address)
                elif code:
                    errors[code] = errors.get(code, 0) + 1
    except ScanError:
        raise
    except Exception as exc:
        raise ScanError(f"SendARP 扫描失败：{exc}") from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    if not online and errors:
        unknown = {
            code: count
            for code, count in errors.items()
            if code not in _NO_RESPONSE_CODES
        }
        if unknown:
            code, count = max(unknown.items(), key=lambda item: item[1])
            raise ScanError(
                f"SendARP 返回异常错误码 {code}（{count} 个地址），"
                "为避免误判已中止本次扫描"
            )

    return ScanResult(
        online_ips=sorted(online, key=ipaddress.ip_address),
        duration_ms=duration_ms,
        iface=f"SendARP ({source_ip})",
        backend="sendarp",
    )


def _import_scapy():
    try:
        from scapy.all import ARP, Ether, conf, srp
    except Exception as exc:
        raise ScanError(f"无法加载 Scapy（请确认依赖已安装）：{exc}") from exc
    if sys.platform == "win32" and not getattr(conf, "use_pcap", False):
        raise ScanError(
            "Scapy 后端未检测到 Npcap/libpcap 提供程序，"
            "请安装 Npcap 并启用 WinPcap 兼容模式，或将 backend 改为 auto/sendarp"
        )
    return ARP, Ether, conf, srp


def _scan_scapy(config: ScanConfig) -> ScanResult:
    network = config.network
    ARP, Ether, conf, srp = _import_scapy()
    conf.verb = 0

    try:
        iface, source_ip, _gateway = conf.route.route(str(network.network_address))
    except Exception as exc:
        raise ScanError(f"无法确定扫描网卡：{exc}") from exc

    _ensure_local_source(network, source_ip)

    packet = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=str(network))
    started = time.perf_counter()
    try:
        answered, _unanswered = srp(
            packet,
            iface=iface,
            timeout=config.timeout_ms / 1000.0,
            retry=config.retries,
            verbose=0,
        )
    except Exception as exc:
        raise ScanError(f"ARP 扫描失败：{exc}") from exc
    duration_ms = int((time.perf_counter() - started) * 1000)

    online: set[str] = set()
    for _sent, received in answered:
        try:
            address = received[ARP].psrc
        except Exception:
            address = getattr(received, "psrc", None)
        if not address:
            continue
        try:
            if ipaddress.ip_address(address) in network:
                online.add(str(address))
        except ValueError:
            continue

    return ScanResult(
        online_ips=sorted(online, key=ipaddress.ip_address),
        duration_ms=duration_ms,
        iface=str(iface),
        backend="scapy",
    )


def scan(config: ScanConfig) -> ScanResult:
    if os.environ.get("IPAA_FORCE_SCAN_FAIL") == "1":
        raise ScanError("已通过 IPAA_FORCE_SCAN_FAIL 模拟扫描失败")

    backend = resolve_backend(config)
    if backend == "sendarp":
        return _scan_sendarp(config)
    if backend == "scapy":
        return _scan_scapy(config)
    raise ScanError(f"不支持的扫描后端：{backend}")
