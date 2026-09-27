from __future__ import annotations

import ctypes
import json
import os
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import psutil

try:
    from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal, QCoreApplication
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget
except ImportError:  # Allows static syntax validation without GUI dependencies.
    QEvent = QObject = QPoint = Qt = Signal = QCoreApplication = QMouseEvent = QTest = QWidget = None  # type: ignore[misc,assignment]

from core.system import SystemInfo, collect_system_info, enumerate_tasks


@dataclass(slots=True)
class LatencyResult:
    samples_ms: list[float]
    minimum_ms: float | None
    average_ms: float | None
    maximum_ms: float | None
    method: str
    note: str


class DiagnosticsManager:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def snapshot(self, system: SystemInfo | None = None) -> dict[str, Any]:
        system = system or collect_system_info()
        tasks = enumerate_tasks()
        services_count = None
        try:
            from core.system import enumerate_services
            services_count = len(enumerate_services())
        except Exception:
            services_count = None
        task_enabled = [t for t in tasks if t.get("enabled")]
        return {
            "timestamp": time.time(),
            "windows": {"display_version": system.display_version, "build": system.build},
            "cpu": system.cpu,
            "ram_gb": system.ram.get("gb"),
            "active_relevant_scheduled_tasks": len(task_enabled),
            "scheduled_tasks_total": len(tasks),
            "services_total": services_count,
            "network_adapters": len(system.network),
            "vbs": system.vbs,
            "secure_boot": system.secure_boot,
            "storage": system.storage,
        }

    def dpc_latency(self) -> dict[str, Any]:
        """A real DPC metric requires a kernel/ETW provider or external tracer."""
        return {
            "available": False,
            "current_ms": None,
            "peak_ms": None,
            "average_ms": None,
            "reason": "No native DPC/ISR sampler is installed. Luna does not fabricate DPC latency values.",
        }

    def find_latency_mon(self) -> dict[str, Any]:
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Resplendence" / "LatencyMon" / "LatencyMon.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LatencyMon" / "LatencyMon.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Resplendence" / "LatencyMon" / "LatencyMon.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LatencyMon" / "LatencyMon.exe",
        ]
        for path in candidates:
            if path and path.exists():
                version = "Unknown"
                try:
                    code, out, _ = self._powershell_file_version(path)
                    if code == 0 and out:
                        version = out.strip()
                except Exception:
                    pass
                return {"installed": True, "path": str(path), "version": version, "api": "No documented structured data API used"}
        return {"installed": False, "path": None, "version": None, "api": None}

    @staticmethod
    def _powershell_file_version(path: Path) -> tuple[int, str, str]:
        from core.system import powershell
        escaped = str(path).replace("'", "''")
        return powershell(f"(Get-Item -LiteralPath '{escaped}').VersionInfo.FileVersion", 10)

    def launch_latency_mon(self, path: str | None = None) -> tuple[bool, str]:
        info = self.find_latency_mon()
        target = path or info.get("path")
        if not target:
            return False, "No LatencyMon executable detected"
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return True, f"Started {target}"
        except OSError as exc:
            return False, str(exc)

    def synthetic_input_latency(self, widget: QWidget | None = None, samples: int = 10) -> LatencyResult:
        """Measures Qt application input dispatch, not hardware-to-photon latency."""
        if QWidget is None or QTest is None or Qt is None:
            return LatencyResult([], None, None, None, "Unavailable", "PySide6 QtTest is not installed")
        target = widget or QWidget()
        owned = widget is None
        if owned:
            target.resize(260, 140)
            target.show()
            QCoreApplication.processEvents()
        times: list[float] = []
        timestamps: dict[str, int] = {}

        class Probe(QWidget):
            def mousePressEvent(self, event):  # type: ignore[override]
                stamp = timestamps.pop("t0", None)
                if stamp is not None:
                    now = self._qpc()
                    times.append((now - stamp) * 1000.0)
                super().mousePressEvent(event)

            @staticmethod
            def _qpc() -> int:
                counter = ctypes.c_longlong()
                ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
                return counter.value

        if not hasattr(target, "mousePressEvent"):
            return LatencyResult([], None, None, None, "Unavailable", "Target is not a QWidget")

        probe = target if isinstance(target, Probe) else None
        if probe is None:
            class Forwarder(QWidget):
                def mousePressEvent(self, event):  # type: ignore[override]
                    stamp = timestamps.pop("t0", None)
                    if stamp is not None and os.name == "nt":
                        counter = ctypes.c_longlong()
                        ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
                        times.append((counter.value - stamp) / self._frequency() * 1000.0)
                    super().mousePressEvent(event)
                @staticmethod
                def _frequency() -> int:
                    freq = ctypes.c_longlong()
                    ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
                    return max(1, freq.value)
            # A dedicated probe avoids disturbing an application-owned widget.
            probe = Forwarder()
            probe.setWindowTitle("Luna Synthetic Input Probe")
            probe.resize(280, 160)
            probe.show()
            QCoreApplication.processEvents()
        try:
            for _ in range(max(1, samples)):
                timestamps["t0"] = self._qpc_ns()
                QTest.mouseClick(probe, Qt.LeftButton, Qt.NoModifier, QPoint(probe.width() // 2, probe.height() // 2), 0)
                QCoreApplication.processEvents()
            values = times[:samples]
            return LatencyResult(
                values,
                min(values) if values else None,
                statistics.fmean(values) if values else None,
                max(values) if values else None,
                "Synthetic Input Dispatch Latency",
                "Measures synthetic Qt event delivery to an application handler; it is not hardware-to-photon mouse latency.",
            )
        finally:
            if owned and target is not None:
                target.close()
            if probe is not None and probe is not target:
                probe.close()

    @staticmethod
    def _qpc_ns() -> int:
        if os.name != "nt":
            return time.perf_counter_ns()
        counter = ctypes.c_longlong()
        ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
        return counter.value

    @staticmethod
    def qpc_frequency() -> int:
        if os.name != "nt":
            return 1_000_000_000
        freq = ctypes.c_longlong()
        ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
        return max(1, int(freq.value))
