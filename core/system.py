from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
import sys
try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None  # type: ignore[assignment]
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import psutil
except ImportError:  # pragma: no cover - dependency handled by requirements.txt
    psutil = None  # type: ignore[assignment]

APP_NAME = "Luna System Core"
SUPPORTED_RELEASES = {
    "21H2": 22000,
    "22H2": 22621,
    "23H2": 22631,
    "24H2": 26100,
    "25H2": 26200,
    "26H1": 28000,
    "26H2": 26300,
}


def is_windows() -> bool:
    return os.name == "nt"


def run_command(args: list[str], timeout: float = 20.0, encoding: str = "utf-8") -> tuple[int, str, str]:
    """Run a native Windows command safely; never invokes a shell."""
    try:
        completed = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except FileNotFoundError as exc:
        return 9009, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return 1460, str(stdout), str(stderr) or "Command timed out"
    except OSError as exc:
        return getattr(exc, "winerror", 1) or 1, "", str(exc)


def powershell(script: str, timeout: float = 30.0) -> tuple[int, str, str]:
    return run_command(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout=timeout,
    )


def reg_query(path: str, value: str | None = None, hive: Any = None) -> Any:
    if not is_windows() or winreg is None:
        return None
    if hive is None:
        hive = winreg.HKEY_LOCAL_MACHINE
    subkey = path.split("\\", 1)[1] if path.startswith("HKLM\\") or path.startswith("HKCU\\") else path
    if path.upper().startswith("HKCU\\"):
        hive = winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            if value is None:
                return {str(winreg.EnumValue(key, i)[0]): winreg.EnumValue(key, i)[1] for i in range(winreg.QueryInfoKey(key)[1])}
            return winreg.QueryValueEx(key, value)[0]
    except (FileNotFoundError, OSError):
        return None


def reg_set(path: str, value_name: str, value: Any, value_type: int | None = None) -> None:
    if not is_windows() or winreg is None:
        raise OSError("Registry is only available on Windows")
    if path.upper().startswith("HKCU\\"):
        hive = winreg.HKEY_CURRENT_USER
        subkey = path[5:]
    elif path.upper().startswith("HKLM\\"):
        hive = winreg.HKEY_LOCAL_MACHINE
        subkey = path[5:]
    else:
        hive = winreg.HKEY_LOCAL_MACHINE
        subkey = path
    if value_type is None:
        if isinstance(value, int):
            value_type = winreg.REG_DWORD
        elif isinstance(value, bytes):
            value_type = winreg.REG_BINARY
        else:
            value_type = winreg.REG_SZ
    with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
        key.SetValueEx(value_name, 0, value_type, value)


def reg_delete_value(path: str, value_name: str) -> None:
    if not is_windows() or winreg is None:
        raise OSError("Registry is only available on Windows")
    if path.upper().startswith("HKCU\\"):
        hive = winreg.HKEY_CURRENT_USER
        subkey = path[5:]
    elif path.upper().startswith("HKLM\\"):
        hive = winreg.HKEY_LOCAL_MACHINE
        subkey = path[5:]
    else:
        hive = winreg.HKEY_LOCAL_MACHINE
        subkey = path
    with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
        key.DeleteValue(value_name)


def value_exists(path: str, value_name: str) -> bool:
    sentinel = object()
    if not is_windows() or winreg is None:
        return False
    if path.upper().startswith("HKCU\\"):
        hive = winreg.HKEY_CURRENT_USER
        subkey = path[5:]
    elif path.upper().startswith("HKLM\\"):
        hive = winreg.HKEY_LOCAL_MACHINE
        subkey = path[5:]
    else:
        hive = winreg.HKEY_LOCAL_MACHINE
        subkey = path
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, value_name)[0] is not sentinel
    except OSError:
        return False


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def uac_enabled() -> bool | None:
    value = reg_query(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", "EnableLUA")
    return bool(value) if isinstance(value, int) else None


def secure_boot_state() -> str:
    code, out, _ = powershell("try { Confirm-SecureBootUEFI } catch { 'Unsupported' }", 8)
    if code != 0:
        return "Unknown"
    return out.strip() or "Unknown"


def vbs_state() -> dict[str, str]:
    result = {"VBS": "Unknown", "HVCI": "Unknown", "CredentialGuard": "Unknown"}
    code, out, _ = powershell(
        "Get-CimInstance -Namespace root\\Microsoft\\Windows\\DeviceGuard -ClassName Win32_DeviceGuard | "
        "Select-Object -Property VirtualizationBasedSecurityStatus,SecurityServicesRunning,AvailableSecurityProperties | "
        "ConvertTo-Json -Compress",
        10,
    )
    if code == 0 and out:
        try:
            obj = json.loads(out)
            if isinstance(obj, dict):
                result["VBS"] = {0: "Disabled", 1: "Enabled", 2: "Running"}.get(int(obj.get("VirtualizationBasedSecurityStatus", -1)), "Unknown")
                services = obj.get("SecurityServicesRunning") or []
                services = [int(x) for x in services]
                result["HVCI"] = "Running" if 2 in services else "Not running"
                result["CredentialGuard"] = "Running" if 1 in services else "Not running"
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
    return result


def windows_version() -> tuple[str, int, str]:
    if not is_windows():
        return platform.system(), 0, platform.release()
    product = str(reg_query(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName") or platform.platform())
    build_raw = reg_query(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuildNumber")
    ubr = reg_query(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "UBR")
    build = 0
    try:
        build = int(build_raw)
    except (TypeError, ValueError):
        pass
    display = str(reg_query(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion") or "Unknown")
    return display, build, product


def logical_processors() -> int:
    return int(psutil.cpu_count(logical=True) or os.cpu_count() or 1) if psutil else int(os.cpu_count() or 1)


def cpu_info() -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": platform.processor() or "Unknown",
        "logical_processors": logical_processors(),
        "physical_cores": psutil.cpu_count(logical=False) if psutil else None,
        "max_frequency_mhz": None,
    }
    if psutil:
        try:
            freq = psutil.cpu_freq()
            if freq:
                data["max_frequency_mhz"] = round(freq.max or 0, 1)
        except Exception:
            pass
    if is_windows():
        code, out, _ = powershell("(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)", 10)
        if code == 0 and out:
            data["name"] = out.strip()
    return data


def ram_info() -> dict[str, Any]:
    total = int(psutil.virtual_memory().total) if psutil else 0
    return {"bytes": total, "gb": round(total / (1024**3), 2)}


def gpu_info() -> list[dict[str, str]]:
    if not is_windows():
        return []
    code, out, _ = powershell(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress",
        15,
    )
    if code != 0 or not out:
        return []
    try:
        obj = json.loads(out)
        rows = obj if isinstance(obj, list) else [obj]
        return [{k: str(row.get(k, "")) for k in ("Name", "DriverVersion", "PNPDeviceID")} for row in rows if isinstance(row, dict)]
    except json.JSONDecodeError:
        return []


def storage_info() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not is_windows() or psutil is None:
        return rows
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        device = part.device
        root = device.rstrip("\\/")
        if root in seen:
            continue
        seen.add(root)
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        rows.append({"device": device, "mountpoint": part.mountpoint, "filesystem": part.fstype, "total_gb": round(usage.total / 1e9, 2), "free_gb": round(usage.free / 1e9, 2), "type": "Unknown"})
    code, out, _ = powershell(
        "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,BusType,HealthStatus,Size | ConvertTo-Json -Compress",
        15,
    )
    if code == 0 and out:
        try:
            obj = json.loads(out)
            disk_rows = obj if isinstance(obj, list) else [obj]
            for i, row in enumerate(disk_rows):
                if not isinstance(row, dict):
                    continue
                if i < len(rows):
                    rows[i].update({"friendly_name": str(row.get("FriendlyName", "")), "media_type": str(row.get("MediaType", "")), "bus_type": str(row.get("BusType", "")), "health": str(row.get("HealthStatus", ""))})
                else:
                    rows.append({"device": "", "mountpoint": "", "filesystem": "", "total_gb": round(int(row.get("Size") or 0) / 1e9, 2), "free_gb": 0, "type": "Physical", "friendly_name": str(row.get("FriendlyName", "")), "media_type": str(row.get("MediaType", "")), "bus_type": str(row.get("BusType", "")), "health": str(row.get("HealthStatus", ""))})
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return rows


def network_adapters() -> list[dict[str, Any]]:
    if not is_windows():
        return []
    code, out, _ = powershell(
        "Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,MacAddress,LinkSpeed,Virtual,PhysicalMediaType,ifIndex,DriverVersion | ConvertTo-Json -Compress",
        20,
    )
    if code != 0 or not out:
        return []
    try:
        obj = json.loads(out)
        rows = obj if isinstance(obj, list) else [obj]
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name", ""))
            desc = str(row.get("InterfaceDescription", ""))
            text_blob = (name + " " + desc).lower()
            kind = "Ethernet" if "ethernet" in text_blob or "gbe" in text_blob else "Wi-Fi" if "wi-fi" in text_blob or "wifi" in text_blob or "wireless" in text_blob else "Other"
            result.append({**{k: row.get(k) for k in ("Name", "InterfaceDescription", "Status", "MacAddress", "LinkSpeed", "Virtual", "PhysicalMediaType", "ifIndex", "DriverVersion")}, "kind": kind, "vpn_like": any(x in text_blob for x in ("vpn", "wireguard", "tap", "tun", "fortinet", "cisco anyconnect", "tailscale", "zerotier"))})
        return result
    except json.JSONDecodeError:
        return []


def feature_presence() -> dict[str, Any]:
    result: dict[str, Any] = {
        "wifi": False,
        "bluetooth": False,
        "vpn": False,
        "virtual_network": False,
        "store": False,
        "xbox": False,
        "insider": False,
        "domain_joined": False,
        "hyper_v": False,
    }
    adapters = network_adapters()
    result["wifi"] = any(a.get("kind") == "Wi-Fi" for a in adapters)
    result["vpn"] = any(a.get("vpn_like") for a in adapters)
    result["virtual_network"] = any(bool(a.get("Virtual")) for a in adapters)
    if not is_windows():
        return result
    code, out, _ = powershell("$p=Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue; @($p).Count", 10)
    result["bluetooth"] = code == 0 and out.strip().isdigit() and int(out.strip()) > 0
    code, out, _ = powershell("try { Get-AppxPackage -AllUsers Microsoft.WindowsStore | Select-Object -First 1 -ExpandProperty Name } catch {}", 10)
    result["store"] = bool(out.strip())
    code, out, _ = powershell("try { Get-AppxPackage -AllUsers | Where-Object {$_.Name -match 'Xbox|GamingApp|GameBar'} | Select-Object -First 1 -ExpandProperty Name } catch {}", 15)
    result["xbox"] = bool(out.strip())
    code, out, _ = powershell("try { Get-Service -Name 'iphlpsvc','WaaSMedicSvc','Flighting' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name } catch {}", 10)
    result["insider"] = "Flighting" in out.splitlines()
    code, out, _ = powershell("(Get-CimInstance Win32_ComputerSystem).PartOfDomain", 10)
    result["domain_joined"] = out.strip().lower() == "true"
    code, out, _ = powershell("try { (Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All).State } catch {}", 15)
    result["hyper_v"] = out.strip().lower() == "enabled"
    return result


def enumerate_services() -> list[dict[str, Any]]:
    if not is_windows():
        return []
    code, out, _ = powershell(
        "Get-CimInstance Win32_Service | Select-Object Name,DisplayName,State,StartMode,StartName,PathName,Description | ConvertTo-Json -Compress",
        30,
    )
    if code != 0 or not out:
        return []
    try:
        obj = json.loads(out)
        return obj if isinstance(obj, list) else [obj]
    except json.JSONDecodeError:
        return []


def service_state(name: str) -> dict[str, Any] | None:
    if not is_windows():
        return None
    code, out, _ = powershell(
        f"$s=Get-CimInstance Win32_Service -Filter \"Name='{name.replace(chr(39), chr(39)*2)}'\" -ErrorAction SilentlyContinue; if($s){{$s | Select-Object Name,State,StartMode,StartName,PathName,Description,Started,AcceptStop | ConvertTo-Json -Compress}}",
        10,
    )
    if code != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def set_service_start_mode(name: str, mode: str) -> tuple[bool, str]:
    code, out, err = run_command(["sc.exe", "config", name, "start=", mode], 15)
    return code == 0, out or err


def stop_service(name: str) -> tuple[bool, str]:
    code, out, err = run_command(["sc.exe", "stop", name], 20)
    return code == 0 or "STOP_PENDING" in (out + err), out or err


def start_service(name: str) -> tuple[bool, str]:
    code, out, err = run_command(["sc.exe", "start", name], 20)
    return code == 0 or "START_PENDING" in (out + err), out or err


def enumerate_tasks() -> list[dict[str, Any]]:
    if not is_windows():
        return []
    code, out, err = run_command(["schtasks.exe", "/Query", "/FO", "CSV", "/V", "/NH"], 45)
    if code != 0:
        return []
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return []
    import csv
    reader = csv.reader(lines)
    rows = list(reader)
    if not rows:
        return []
    headers = rows[0] if rows and len(rows[0]) > 1 else []
    results: list[dict[str, Any]] = []
    for row in rows[1:]:
        if len(row) != len(headers):
            continue
        item = dict(zip(headers, row))
        path = item.get("TaskName", "")
        results.append({
            "path": path,
            "status": item.get("Status", ""),
            "schedule_type": item.get("Schedule Type", ""),
            "run_as": item.get("Run As User", ""),
            "author": item.get("Author", ""),
            "next_run": item.get("Next Run Time", ""),
            "last_run": item.get("Last Run Time", ""),
            "enabled": "Disabled" not in item.get("Schedule Status", "") and item.get("Status", "") != "Disabled",
            "raw": item,
        })
    return results


def task_query_xml(path: str) -> str | None:
    code, out, _ = run_command(["schtasks.exe", "/Query", "/TN", path, "/XML"], 20)
    return out if code == 0 else None


def set_task_enabled(path: str, enabled: bool) -> tuple[bool, str]:
    code, out, err = run_command(["schtasks.exe", "/Change", "/TN", path, "/ENABLE" if enabled else "/DISABLE"], 20)
    return code == 0, out or err


def enumerate_devices() -> list[dict[str, Any]]:
    if not is_windows():
        return []
    code, out, _ = run_command(["pnputil.exe", "/enum-devices", "/connected", "/properties"], 60)
    if code != 0:
        return []
    blocks = re.split(r"\r?\n\r?\n", out)
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for block in blocks:
        current = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            current[key.strip()] = value.strip()
        if current:
            devices.append({
                "name": current.get("Device description", current.get("Name", "Unknown device")),
                "instance_id": current.get("Instance ID", ""),
                "class": current.get("Class Name", ""),
                "manufacturer": current.get("Manufacturer Name", ""),
                "driver": current.get("Driver Name", ""),
                "status": current.get("Device status", ""),
                "problem_code": current.get("Problem code", ""),
                "service": current.get("Service", ""),
                "raw": current,
            })
    return devices


def processor_topology() -> dict[str, Any]:
    result = {"logical_processors": logical_processors(), "groups": 1, "numa_nodes": 1, "affinity_limit": None}
    if not is_windows():
        return result
    try:
        groups = ctypes.windll.kernel32.GetActiveProcessorGroupCount()
        result["groups"] = int(groups)
        total = 0
        for i in range(groups):
            total += int(ctypes.windll.kernel32.GetActiveProcessorCount(i))
        result["logical_processors"] = total
        if total <= 64:
            result["affinity_limit"] = (1 << total) - 1
    except Exception:
        pass
    code, out, _ = powershell("try { (Get-CimInstance Win32_NumaNode).NodeNumber.Count } catch {}", 10)
    if code == 0 and out.strip().isdigit():
        result["numa_nodes"] = max(1, int(out.strip()))
    return result


def system_protection_state() -> str:
    if not is_windows():
        return "Not Windows"
    code, out, err = powershell(
        "$p=Get-ComputerRestorePoint -ErrorAction SilentlyContinue; if($p){'EnabledWithRestorePoints'}else{'NoRestorePointsOrUnavailable'}",
        10,
    )
    if code == 0 and out:
        return out.strip()
    return "Unknown"


def windows_update_state() -> dict[str, str]:
    state = {"service": "Unknown", "medic": "Unknown", "update_orchestrator": "Unknown"}
    for name, key in (("wuauserv", "service"), ("WaaSMedicSvc", "medic"), ("UsoSvc", "update_orchestrator")):
        data = service_state(name)
        if data:
            state[key] = f"{data.get('State', 'Unknown')} / {data.get('StartMode', 'Unknown')}"
    return state


@dataclass(slots=True)
class SystemInfo:
    os_name: str
    display_version: str
    build: int
    product_name: str
    architecture: str
    user: str
    admin: bool
    uac: bool | None
    secure_boot: str
    vbs: dict[str, str]
    cpu: dict[str, Any]
    ram: dict[str, Any]
    gpu: list[dict[str, str]]
    storage: list[dict[str, Any]]
    network: list[dict[str, Any]]
    features: dict[str, Any]
    topology: dict[str, Any]
    system_protection: str
    windows_update: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_system_info() -> SystemInfo:
    display, build, product = windows_version()
    return SystemInfo(
        os_name="Windows" if is_windows() else platform.system(),
        display_version=display,
        build=build,
        product_name=product,
        architecture=platform.machine(),
        user=os.environ.get("USERNAME") or os.environ.get("USER") or "Unknown",
        admin=is_admin(),
        uac=uac_enabled(),
        secure_boot=secure_boot_state() if is_windows() else "Unavailable",
        vbs=vbs_state() if is_windows() else {"VBS": "Unavailable", "HVCI": "Unavailable", "CredentialGuard": "Unavailable"},
        cpu=cpu_info(),
        ram=ram_info(),
        gpu=gpu_info(),
        storage=storage_info(),
        network=network_adapters(),
        features=feature_presence(),
        topology=processor_topology(),
        system_protection=system_protection_state(),
        windows_update=windows_update_state(),
    )


def build_support(display_version: str, build: int) -> tuple[bool, str]:
    if not is_windows():
        return False, "Non-Windows host"
    if display_version in SUPPORTED_RELEASES:
        minimum = SUPPORTED_RELEASES[display_version]
        return build >= minimum, display_version
    known_builds = sorted(SUPPORTED_RELEASES.values())
    if build and build >= min(known_builds):
        return True, f"Unknown/newer build {build}"
    return False, f"Unsupported build {build}"


def export_system_snapshot(path: Path, info: SystemInfo | None = None) -> None:
    info = info or collect_system_info()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
