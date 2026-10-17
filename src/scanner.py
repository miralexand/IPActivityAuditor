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
from dataclasses import dataclass, field
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
    mac_map: dict[str, str] = field(default_factory=dict)


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


class _MIB_IPADDRROW(ctypes.Structure):
    _fields_ = [
        ("dwAddr", wintypes.DWORD),
        ("dwIndex", wintypes.DWORD),
        ("dwMask", wintypes.DWORD),
        ("dwBCastAddr", wintypes.DWORD),
        ("dwReasmSize", wintypes.DWORD),
        ("unused1", wintypes.USHORT),
        ("wType", wintypes.USHORT),
    ]


def _dword_to_ip(value: int) -> str:
    return socket.inet_ntoa(struct.pack("<I", value))


def _local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    if sys.platform != "win32":
        return []
    try:
        iphlpapi = ctypes.windll.iphlpapi
        size = wintypes.ULONG(0)
        result = iphlpapi.GetIpAddrTable(None, ctypes.byref(size), False)
        if int(result) not in (0, 122):
            return []
        if size.value == 0:
            return []
        buffer = ctypes.create_string_buffer(size.value)
        result = iphlpapi.GetIpAddrTable(
            ctypes.byref(buffer), ctypes.byref(size), False
        )
        if int(result) != 0:
            return []
        count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
        rows = (_MIB_IPADDRROW * count).from_buffer(buffer, ctypes.sizeof(wintypes.DWORD))
        networks: list[ipaddress.IPv4Network] = []
        for row in rows:
            if not row.dwAddr or not row.dwMask:
                continue
            try:
                network = ipaddress.IPv4Network(
                    f"{_dword_to_ip(row.dwAddr)}/{_dword_to_ip(row.dwMask)}",
                    strict=False,
                )
            except ValueError:
                continue
            if network not in networks:
                networks.append(network)
        return networks
    except Exception:
        return []


def _ensure_local_source(
    network: ipaddress.IPv4Network,
    source_ip: str,
    local_networks: list[ipaddress.IPv4Network] | None = None,
) -> None:
    try:
        source_address = ipaddress.ip_address(source_ip)
    except ValueError:
        source_address = None
    if local_networks and source_address is not None:
        for local_network in local_networks:
            if (
                source_address in local_network
                and network.subnet_of(local_network)
            ):
                return
    elif source_address is not None and source_address in network:
        return
    raise ScanError(
        f"网段 {network} 不在本机可直达的二层网段内"
        f"（路由选中的源地址为 {source_ip}），无法执行 ARP 扫描。"
        "请检查网卡与配置；子网可配置其所在的本机大网段。"
    )


def _format_mac(raw) -> str | None:
    try:
        values = list(raw)[:6]
    except TypeError:
        return None
    if len(values) < 6 or not any(values):
        return None
    try:
        return ":".join(f"{int(value) & 0xFF:02X}" for value in values)
    except (TypeError, ValueError):
        return None


def _normalize_mac(value) -> str | None:
    if not value:
        return None
    text = str(value).replace("-", ":").upper()
    parts = text.split(":")
    if len(parts) == 6:
        try:
            return ":".join(f"{int(part, 16):02X}" for part in parts)
        except ValueError:
            return text
    return text


def _probe_sendarp(function, value: int, retries: int) -> tuple[str | None, int]:
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
            return _format_mac(mac), 0
        last_code = code
        if code in _FATAL_CODES:
            raise ScanError(f"SendARP 返回错误码 {code}，扫描已中止")
        if attempt < retries:
            time.sleep(0.05)
    return None, last_code


def _scan_sendarp_network(
    config: ScanConfig,
    network: ipaddress.IPv4Network,
    function,
) -> tuple[dict[str, str], int, str]:
    source_ip = _route_source_ip(network)
    _ensure_local_source(network, source_ip, _local_ipv4_networks())

    hosts = [str(ip) for ip in network.hosts()]
    if not hosts:
        return {}, 0, f"SendARP ({source_ip})"

    started = time.perf_counter()
    workers = min(128, max(len(hosts), 1))
    online: dict[str, str] = {}
    errors: dict[int, int] = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _probe_sendarp,
                    function,
                    _ip_to_sendarp_value(ip),
                    config.retries,
                ): ip
                for ip in hosts
            }
            for future in as_completed(futures):
                address = futures[future]
                try:
                    mac, code = future.result()
                except ScanError:
                    raise
                except Exception:
                    continue
                if mac:
                    online[address] = mac
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

    return online, duration_ms, f"SendARP ({source_ip})"


def _scan_sendarp(config: ScanConfig) -> ScanResult:
    if sys.platform != "win32":
        raise ScanError("SendARP 扫描后端仅支持 Windows")
    function = _load_sendarp()

    online: dict[str, str] = {}
    duration_ms = 0
    interfaces: list[str] = []
    for network in config.networks:
        partial, partial_ms, iface = _scan_sendarp_network(config, network, function)
        online.update(partial)
        duration_ms += partial_ms
        if iface not in interfaces:
            interfaces.append(iface)

    return ScanResult(
        online_ips=sorted(online, key=ipaddress.ip_address),
        duration_ms=duration_ms,
        iface=", ".join(interfaces),
        backend="sendarp",
        mac_map=online,
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


def _scan_scapy_network(
    config: ScanConfig,
    network: ipaddress.IPv4Network,
    ARP,
    Ether,
    conf,
    srp,
) -> tuple[dict[str, str], int, str]:
    try:
        iface, source_ip, _gateway = conf.route.route(str(network.network_address))
    except Exception as exc:
        raise ScanError(f"无法确定扫描网卡：{exc}") from exc

    _ensure_local_source(network, source_ip, _local_ipv4_networks())

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

    online: dict[str, str] = {}
    for _sent, received in answered:
        try:
            address = received[ARP].psrc
            mac = received[ARP].hwsrc
        except Exception:
            address = getattr(received, "psrc", None)
            mac = getattr(received, "hwsrc", None)
        if not address:
            continue
        try:
            if ipaddress.ip_address(address) not in network:
                continue
        except ValueError:
            continue
        normalized = _normalize_mac(mac)
        if normalized:
            online[str(address)] = normalized
        else:
            online.setdefault(str(address), "")

    return online, duration_ms, str(iface)


def _scan_scapy(config: ScanConfig) -> ScanResult:
    ARP, Ether, conf, srp = _import_scapy()
    conf.verb = 0

    online: dict[str, str] = {}
    duration_ms = 0
    interfaces: list[str] = []
    for network in config.networks:
        partial, partial_ms, iface = _scan_scapy_network(
            config, network, ARP, Ether, conf, srp
        )
        online.update({ip: mac for ip, mac in partial.items() if mac})
        for ip in partial:
            online.setdefault(ip, "")
        duration_ms += partial_ms
        if iface not in interfaces:
            interfaces.append(iface)

    return ScanResult(
        online_ips=sorted(online, key=ipaddress.ip_address),
        duration_ms=duration_ms,
        iface=", ".join(interfaces),
        backend="scapy",
        mac_map={ip: mac for ip, mac in online.items() if mac},
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
