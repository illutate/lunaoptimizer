from __future__ import annotations

import html
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QObject, Signal, Slot

from core.backup import BackupManager
from core.system import SystemInfo, collect_system_info, export_system_snapshot, is_admin
from optimizations.tweaks import Risk, TweakContext, TweakDefinition, TweakResult, TweakState, build_tweak_catalog


@dataclass(slots=True)
class RunSummary:
    mode: str
    total_selected: int
    applicable: int
    success: int
    already: int
    skipped: int
    failed: int
    rolled_back: int
    requires_reboot: bool
    started_at: str
    ended_at: str | None = None

    @property
    def optimized_percentage(self) -> float:
        denominator = max(self.applicable, 1)
        return round(((self.success + self.already) / denominator) * 100.0, 1)


class OptimizationEngine:
    def __init__(self, base_dir: Path, system: SystemInfo | None = None, logger: Any = None):
        self.base_dir = base_dir
        self.system = system or collect_system_info()
        self.logger = logger
        self.backup = BackupManager(base_dir)
        self.cancel_event = threading.Event()
        self.catalog: list[TweakDefinition] = build_tweak_catalog(self.system)
        self.applied: list[TweakDefinition] = []
        self.results: dict[str, TweakResult] = {}
        self.current_summary: RunSummary | None = None
        self._session_baseline: dict[str, Any] = {}

    def refresh_system(self) -> None:
        self.system = collect_system_info()
        self.catalog = build_tweak_catalog(self.system)

    def set_cancel(self) -> None:
        self.cancel_event.set()

    def clear_cancel(self) -> None:
        self.cancel_event.clear()

    def log(self, level: str, message: str) -> None:
        if self.logger:
            self.logger(level, message)

    def inspect(self) -> dict[str, Any]:
        ctx = TweakContext(self.system, self.backup, self.log, True)
        rows: list[dict[str, Any]] = []
        for tweak in self.catalog:
            result = tweak.inspect(ctx)
            rows.append({
                "id": tweak.id,
                "name": tweak.name,
                "category": tweak.category,
                "risk": tweak.risk.value,
                "state": result.state.value,
                "message": result.message,
                "requires_reboot": tweak.requires_reboot,
                "quick": tweak.quick,
                "details": result.details,
            })
        applicable = [r for r in rows if r["state"] not in (TweakState.NOT_APPLICABLE.value,)]
        already = [r for r in applicable if r["state"] == TweakState.ALREADY_OPTIMIZED.value]
        will_apply = [r for r in applicable if r["state"] == TweakState.SUPPORTED.value]
        risky = [r for r in will_apply if r["risk"] in (Risk.MEDIUM.value, Risk.HIGH.value, Risk.CRITICAL.value)]
        return {"rows": rows, "will_apply": len(will_apply), "already": len(already), "not_applicable": len(rows) - len(applicable), "risky": len(risky), "requires_reboot": sum(bool(r["requires_reboot"]) for r in will_apply)}

    def create_backup(self) -> None:
        if not is_admin():
            raise PermissionError("Administrator privileges are required")
        self.log("BACKUP", "Creating granular backup session")
        self.backup.create_session(self.system.to_dict())
        self.log("BACKUP", f"Backup session: {self.backup.current.backup_id}")
        try:
            devices = __import__("core.system", fromlist=["enumerate_devices"]).enumerate_devices()
            self.backup.backup_device_snapshot(devices)
        except Exception as exc:
            self.backup.note(f"Device snapshot unavailable: {exc}")
        try:
            self.backup.backup_network_snapshot(self.system.network)
        except Exception as exc:
            self.backup.note(f"Network snapshot unavailable: {exc}")

    def create_restore_point(self) -> tuple[bool, str]:
        if not is_admin():
            return False, "Administrator privileges are required"
        display_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        description = f"LunaOptimization_{display_stamp}"
        ps = (
            f"try {{ Checkpoint-Computer -Description '{description}' -RestorePointType 'MODIFY_SETTINGS'; "
            "'Restore point created' }} catch { Write-Error $_; exit 1 }"
        )
        from core.system import powershell
        code, out, err = powershell(ps, 90)
        if code == 0 and "Restore point created" in out:
            self.log("BACKUP", f"Restore point created: {description}")
            return True, description
        self.log("WARNING", f"Restore point could not be created: {err or out}")
        return False, err or out or "Restore point creation failed"

    def _selected(self, ids: Iterable[str], quick: bool = False) -> list[TweakDefinition]:
        wanted = set(ids)
        selected = [t for t in self.catalog if t.id in wanted]
        if quick:
            selected = [t for t in selected if t.quick and t.risk in (Risk.SAFE, Risk.LOW)]
        return selected

    def quick_ids(self) -> list[str]:
        return [t.id for t in self.catalog if t.quick and t.risk in (Risk.SAFE, Risk.LOW)]

    def run(self, ids: list[str], mode: str = "DEEP MANUAL", risk_confirmation: bool = True) -> RunSummary:
        self.clear_cancel()
        selected = self._selected(ids, mode == "QUICK")
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        summary = RunSummary(mode, len(selected), 0, 0, 0, 0, 0, 0, False, started)
        self.current_summary = summary
        if not selected:
            summary.ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
            return summary
        self.applied = []
        self.results = {}
        if not is_admin():
            raise PermissionError("Administrator privileges are required")
        self.create_backup()
        restore_ok, _ = self.create_restore_point()
        if not restore_ok:
            self.backup.note("System restore point unavailable; granular backup remains active safety net")
        ctx = TweakContext(self.system, self.backup, self.log, False)
        dependency_failed: set[str] = set()
        for index, tweak in enumerate(selected, 1):
            if self.cancel_event.is_set():
                self.log("ACTION", "Cancellation requested; stopping before next tweak")
                break
            if any(dep in dependency_failed for dep in tweak.dependencies):
                result = TweakResult(False, TweakState.SKIPPED, "Skipped because a dependency failed")
                self.results[tweak.id] = result
                summary.skipped += 1
                continue
            inspect = tweak.inspect(ctx)
            self.results[tweak.id] = inspect
            if inspect.state == TweakState.NOT_APPLICABLE:
                summary.skipped += 1
                self.log("SKIP", f"{tweak.name}: {inspect.message}")
                continue
            summary.applicable += 1
            if inspect.state == TweakState.ALREADY_OPTIMIZED:
                summary.already += 1
                self.log("SUCCESS", f"{tweak.name}: already optimized")
                continue
            if tweak.risk in (Risk.HIGH, Risk.CRITICAL) and not risk_confirmation:
                result = TweakResult(True, TweakState.SKIPPED, "Skipped because risky changes were not confirmed")
                self.results[tweak.id] = result
                summary.skipped += 1
                self.log("SKIP", f"{tweak.name}: risky change not confirmed")
                continue
            self.log("ACTION", f"Applying {index}/{len(selected)}: {tweak.name}")
            try:
                result = tweak.apply(ctx)
            except Exception as exc:  # defensive boundary around a system tweak
                result = TweakResult(False, TweakState.FAILED, f"Unhandled tweak exception: {type(exc).__name__}: {exc}")
            self.results[tweak.id] = result
            self.backup.record_tweak_application(tweak.id, result.state.value)
            if result.ok and result.changed:
                self.applied.append(tweak)
            if result.ok:
                if result.state == TweakState.REQUIRES_REBOOT or result.requires_reboot or tweak.requires_reboot:
                    summary.requires_reboot = True
                summary.success += 1
                self.log("VERIFY", f"{tweak.name}: {result.message}")
            elif result.state == TweakState.NOT_APPLICABLE:
                summary.skipped += 1
                self.log("SKIP", f"{tweak.name}: {result.message}")
            else:
                summary.failed += 1
                dependency_failed.add(tweak.id)
                self.log("ERROR", f"{tweak.name}: {result.message}")
                if self.applied:
                    self.log("ROLLBACK", "Stopping dependent operations after verification failure")
                    rollback_failures = self.rollback_applied(ctx)
                    summary.rolled_back += len(self.applied) - rollback_failures
                break
        summary.ended_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return summary

    def rollback_applied(self, ctx: TweakContext | None = None) -> int:
        ctx = ctx or TweakContext(self.system, self.backup, self.log, False)
        failures = 0
        for tweak in reversed(self.applied):
            self.log("ROLLBACK", f"Restoring {tweak.name}")
            try:
                result = tweak.rollback(ctx)
            except Exception as exc:
                result = TweakResult(False, TweakState.ROLLBACK_FAILED, f"Unhandled rollback exception: {type(exc).__name__}: {exc}")
            self.results[f"rollback:{tweak.id}"] = result
            if result.ok:
                self.log("ROLLBACK", f"{tweak.name}: {result.message}")
            else:
                failures += 1
                self.log("ERROR", f"Rollback failed for {tweak.name}: {result.message}")
        return failures

    def restore_last_session(self) -> tuple[bool, list[str]]:
        sessions = self.backup.list_sessions()
        if not sessions:
            return False, ["No backup sessions found"]
        session = sessions[0]
        self.backup.current = session
        messages: list[str] = []
        failures = 0
        from core.system import reg_query, service_state, set_service_start_mode, start_service, stop_service, set_task_enabled
        for marker in reversed(session.manifest.get("registry", [])):
            if "value_name" not in marker:
                continue
            ok, msg = self.backup.restore_registry_value(marker)
            messages.append(f"Registry {marker.get('path')}\\{marker.get('value_name')}: {msg}")
            failures += 0 if ok else 1
        for marker in reversed(session.manifest.get("tasks", [])):
            path = marker.get("path")
            xml = marker.get("xml")
            if not path or not xml:
                continue
            ok, msg = self.backup.restore_task(marker)
            messages.append(f"Task {path}: {msg}")
            failures += 0 if ok else 1
        for marker in reversed(session.manifest.get("services", [])):
            name = marker.get("Name") or marker.get("name")
            mode = str(marker.get("StartMode", "Manual")).lower()
            mapping = {"auto": "auto", "manual": "demand", "disabled": "disabled", "boot": "boot", "system": "system"}
            if name:
                ok, msg = set_service_start_mode(name, mapping.get(mode, "demand"))
                messages.append(f"Service {name} startup: {msg}")
                failures += 0 if ok else 1
                if marker.get("Started"):
                    ok2, msg2 = start_service(name)
                    messages.append(f"Service {name} start: {msg2}")
                    failures += 0 if ok2 else 1
        return failures == 0, messages

    def export_report(self, path: Path, summary: RunSummary | None = None, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None) -> Path:
        summary = summary or self.current_summary
        if summary is None:
            raise RuntimeError("No optimization run available")
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for tweak in self.catalog:
            result = self.results.get(tweak.id)
            if not result:
                continue
            rows.append({"id": tweak.id, "name": tweak.name, "category": tweak.category, "risk": tweak.risk.value, "state": result.state.value, "message": result.message})
        html_rows = "".join(
            f"<tr><td>{html.escape(str(r['name']))}</td><td>{html.escape(str(r['category']))}</td><td>{html.escape(str(r['risk']))}</td><td>{html.escape(str(r['state']))}</td><td>{html.escape(str(r['message']))}</td></tr>" for r in rows
        )
        payload = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "summary": summary.__dict__ if hasattr(summary, "__dict__") else {
                "mode": summary.mode, "total_selected": summary.total_selected, "applicable": summary.applicable, "success": summary.success,
                "already": summary.already, "skipped": summary.skipped, "failed": summary.failed, "rolled_back": summary.rolled_back,
                "requires_reboot": summary.requires_reboot, "optimized_percentage": summary.optimized_percentage,
                "started_at": summary.started_at, "ended_at": summary.ended_at,
            },
            "system": self.system.to_dict(),
            "before": before or {},
            "after": after or {},
            "tweaks": rows,
        }
        html_doc = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Luna System Core Report</title>
<style>body{{background:#05070b;color:#d9e1ea;font-family:Consolas,monospace;margin:32px}} h1{{color:#19f59a}} table{{width:100%;border-collapse:collapse}} th,td{{border:1px solid #24303a;padding:8px;text-align:left}} th{{color:#35a7ff}} .metric{{display:inline-block;margin:8px;padding:12px;border:1px solid #24303a;border-radius:10px}}</style></head>
<body><h1>Luna System Core</h1>
<div class='metric'>Mode: {html.escape(summary.mode)}</div>
<div class='metric'>Optimization: {summary.optimized_percentage:.1f}%</div>
<div class='metric'>Success: {summary.success}</div><div class='metric'>Already: {summary.already}</div>
<div class='metric'>Skipped: {summary.skipped}</div><div class='metric'>Failed: {summary.failed}</div>
<div class='metric'>Rollback: {summary.rolled_back}</div><div class='metric'>Reboot: {html.escape(str(summary.requires_reboot))}</div>
<h2>System</h2><pre>{html.escape(json.dumps(self.system.to_dict(), ensure_ascii=False, indent=2))}</pre>
<h2>Diagnostics Before</h2><pre>{html.escape(json.dumps(before or {}, ensure_ascii=False, indent=2))}</pre>
<h2>Diagnostics After</h2><pre>{html.escape(json.dumps(after or {}, ensure_ascii=False, indent=2))}</pre>
<h2>Tweaks</h2><table><thead><tr><th>Name</th><th>Category</th><th>Risk</th><th>State</th><th>Message</th></tr></thead><tbody>{html_rows}</tbody></table>
</body></html>"""
        path.write_text(html_doc, encoding="utf-8")
        (path.with_suffix(".json")).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path


class OptimizationWorker(QObject):
    progress = Signal(int, int, str)
    log_signal = Signal(str, str)
    finished = Signal(object)
    error = Signal(str)
    rollback_finished = Signal(bool, list)

    def __init__(self, engine: OptimizationEngine, ids: list[str], mode: str, risk_confirmation: bool):
        super().__init__()
        self.engine = engine
        self.ids = ids
        self.mode = mode
        self.risk_confirmation = risk_confirmation
        self.cancelled = False
        self.engine.logger = self._log

    def _log(self, level: str, message: str) -> None:
        self.log_signal.emit(level, message)

    @Slot()
    def run(self) -> None:
        try:
            summary = self.engine.run(self.ids, self.mode, self.risk_confirmation)
            self.finished.emit(summary)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")

    @Slot()
    def cancel(self) -> None:
        self.engine.set_cancel()

    @Slot()
    def rollback(self) -> None:
        try:
            failures = self.engine.rollback_applied()
            messages = [f"Rollback failures: {failures}"]
            self.rollback_finished.emit(failures == 0, messages)
        except Exception as exc:
            self.rollback_finished.emit(False, [f"Rollback error: {type(exc).__name__}: {exc}"])
