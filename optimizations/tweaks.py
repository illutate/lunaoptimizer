from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable

from core.system import (
    SystemInfo,
    enumerate_tasks,
    powershell,
    reg_query,
    reg_set,
    run_command,
    set_task_enabled,
    service_state,
    set_service_start_mode,
    stop_service,
    start_service,
    is_windows,
)


class Risk(str, Enum):
    SAFE = "SAFE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TweakState(str, Enum):
    DEFAULT = "DEFAULT"
    SUPPORTED = "SUPPORTED"
    ALREADY_OPTIMIZED = "ALREADY OPTIMIZED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT APPLICABLE"
    REQUIRES_REBOOT = "REQUIRES REBOOT"
    ROLLED_BACK = "ROLLED BACK"
    ROLLBACK_FAILED = "ROLLBACK FAILED"


@dataclass(slots=True)
class TweakResult:
    ok: bool
    state: TweakState
    message: str
    changed: bool = False
    requires_reboot: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TweakContext:
    system: SystemInfo
    backup: Any
    logger: Callable[[str, str], None]
    dry_run: bool = False


CheckFn = Callable[[TweakContext], TweakResult]
ApplyFn = Callable[[TweakContext], TweakResult]
RollbackFn = Callable[[TweakContext], TweakResult]


@dataclass(slots=True)
class TweakDefinition:
    id: str
    name: str
    category: str
    description: str
    why: str
    effect: str
    risk: Risk
    supported_builds: str
    dependencies: tuple[str, ...]
    requires_reboot: bool
    quick: bool
    high_impact: bool
    check: CheckFn
    apply: ApplyFn
    rollback: RollbackFn
    metadata: dict[str, Any] = field(default_factory=dict)

    def inspect(self, ctx: TweakContext) -> TweakResult:
        return self.check(ctx)


def _unsupported(ctx: TweakContext, reason: str) -> TweakResult:
    return TweakResult(False, TweakState.NOT_APPLICABLE, reason, changed=False)


def _task_lookup(ctx: TweakContext, task_path: str) -> dict[str, Any] | None:
    for task in enumerate_tasks():
        if str(task.get("path", "")).lower() == task_path.lower():
            return task
    return None


def _make_task_tweak(task_path: str, name: str, category: str, description: str, why: str, effect: str, risk: Risk, quick: bool, high_impact: bool = False) -> TweakDefinition:
    tweak_id = "task." + task_path.strip("\\").replace("\\", ".").replace(" ", "_").lower()

    def check(ctx: TweakContext) -> TweakResult:
        if not is_windows():
            return _unsupported(ctx, "Windows required")
        supported, _ = _support(ctx)
        if not supported:
            return _unsupported(ctx, f"Unsupported Windows build {ctx.system.build}")
        item = _task_lookup(ctx, task_path)
        if not item:
            return _unsupported(ctx, "Scheduled task is not present on this build")
        enabled = bool(item.get("enabled"))
        return TweakResult(True, TweakState.SUPPORTED if enabled else TweakState.ALREADY_OPTIMIZED, "Enabled" if enabled else "Already disabled", changed=False, details={"path": task_path, "enabled": enabled})

    def apply(ctx: TweakContext) -> TweakResult:
        current = check(ctx)
        if current.state in (TweakState.NOT_APPLICABLE, TweakState.ALREADY_OPTIMIZED):
            return current
        if ctx.dry_run:
            return TweakResult(True, TweakState.SUPPORTED, "Preview: task would be disabled", changed=False, details=current.details)
        ctx.backup.backup_task(task_path)
        ok, message = set_task_enabled(task_path, False)
        if not ok:
            return TweakResult(False, TweakState.FAILED, f"Disable failed: {message}", details=current.details)
        verify = check(ctx)
        if verify.state not in (TweakState.ALREADY_OPTIMIZED,):
            return TweakResult(False, TweakState.FAILED, "Verification failed; task still appears enabled", details=current.details)
        return TweakResult(True, TweakState.REQUIRES_REBOOT if False else TweakState.APPLIED, "Task disabled and verified", changed=True, details=current.details)

    def rollback(ctx: TweakContext) -> TweakResult:
        if not is_windows():
            return _unsupported(ctx, "Windows required")
        ok, message = set_task_enabled(task_path, True)
        if not ok:
            return TweakResult(False, TweakState.ROLLBACK_FAILED, f"Rollback failed: {message}")
        verify = _task_lookup(ctx, task_path)
        if not verify or not verify.get("enabled"):
            return TweakResult(False, TweakState.ROLLBACK_FAILED, "Task re-enable could not be verified")
        return TweakResult(True, TweakState.ROLLED_BACK, "Task restored", changed=True)

    return TweakDefinition(
        tweak_id,
        name,
        category,
        description,
        why,
        effect,
        risk,
        "21H2–26H2; runtime task detection",
        tuple(),
        False,
        quick,
        high_impact,
        check,
        apply,
        rollback,
        {"task_path": task_path},
    )


def _support(ctx: TweakContext) -> tuple[bool, str]:
    build = ctx.system.build
    return (is_windows() and build >= 22000), "Windows 11"


def _registry_check(ctx: TweakContext, path: str, name: str, desired: Any, display_target: str) -> TweakResult:
    if not is_windows():
        return _unsupported(ctx, "Windows required")
    supported, _ = _support(ctx)
    if not supported:
        return _unsupported(ctx, f"Unsupported build {ctx.system.build}")
    current = reg_query(path, name)
    if current is None:
        return _unsupported(ctx, f"Registry value not present: {path}\\{name}")
    if current == desired:
        return TweakResult(True, TweakState.ALREADY_OPTIMIZED, f"Current value already equals {display_target}", details={"current": current, "target": desired, "path": path, "value_name": name})
    return TweakResult(True, TweakState.SUPPORTED, f"Current={current}; target={display_target}", details={"current": current, "target": desired, "path": path, "value_name": name})


def make_registry_tweak(
    tweak_id: str,
    name: str,
    category: str,
    description: str,
    why: str,
    effect: str,
    risk: Risk,
    path: str,
    value_name: str,
    desired: Any,
    target_text: str,
    quick: bool,
    requires_reboot: bool = False,
) -> TweakDefinition:
    def check(ctx: TweakContext) -> TweakResult:
        return _registry_check(ctx, path, value_name, desired, target_text)

    def apply(ctx: TweakContext) -> TweakResult:
        current = check(ctx)
        if current.state in (TweakState.NOT_APPLICABLE, TweakState.ALREADY_OPTIMIZED):
            return current
        if ctx.dry_run:
            return TweakResult(True, TweakState.SUPPORTED, f"Preview: set {target_text}", details=current.details)
        ctx.backup.backup_registry_value(path, value_name)
        try:
            reg_set(path, value_name, desired)
        except (OSError, ValueError, TypeError) as exc:
            return TweakResult(False, TweakState.FAILED, f"Registry write failed: {exc}", details=current.details)
        verify = check(ctx)
        if verify.state != TweakState.ALREADY_OPTIMIZED:
            return TweakResult(False, TweakState.FAILED, "Registry verification failed", details=current.details)
        return TweakResult(True, TweakState.REQUIRES_REBOOT if requires_reboot else TweakState.APPLIED, "Registry change applied and verified", changed=True, requires_reboot=requires_reboot, details=current.details)

    def rollback(ctx: TweakContext) -> TweakResult:
        markers = ctx.backup.current.manifest.get("registry", []) if ctx.backup.current else []
        marker = next((m for m in reversed(markers) if m.get("path") == path and m.get("value_name") == value_name), None)
        if not marker:
            return TweakResult(False, TweakState.ROLLBACK_FAILED, "Registry backup marker not found")
        ok, message = ctx.backup.restore_registry_value(marker)
        if not ok:
            return TweakResult(False, TweakState.ROLLBACK_FAILED, message)
        restored = reg_query(path, value_name)
        expected = marker.get("value") if marker.get("exists") else None
        if restored != expected:
            return TweakResult(False, TweakState.ROLLBACK_FAILED, f"Verification mismatch after rollback: {restored!r} != {expected!r}")
        return TweakResult(True, TweakState.ROLLED_BACK, "Registry restored and verified", changed=True)

    return TweakDefinition(tweak_id, name, category, description, why, effect, risk, "21H2–26H2; value must exist", tuple(), requires_reboot, quick, False, check, apply, rollback, {"registry_path": path, "value_name": value_name, "target": desired})


def make_service_tweak(name: str, display: str, description: str, why: str, risk: Risk, quick: bool) -> TweakDefinition:
    tweak_id = f"service.{name.lower()}"

    def check(ctx: TweakContext) -> TweakResult:
        data = service_state(name)
        if not data:
            return _unsupported(ctx, f"Service {name} is not present")
        protected = {"RpcSs", "DcomLaunch", "LSASS", "EventLog", "Schedule", "Winlogon", "MpsSvc", "BFE", "PlugPlay", "CryptSvc", "wuauserv"}
        if name in protected:
            return _unsupported(ctx, "Protected core service")
        mode = str(data.get("StartMode", "Unknown"))
        state = str(data.get("State", "Unknown"))
        target_disabled = mode.lower() == "disabled"
        return TweakResult(True, TweakState.ALREADY_OPTIMIZED if target_disabled else TweakState.SUPPORTED, f"{state} / {mode}", details={"service": data})

    def apply(ctx: TweakContext) -> TweakResult:
        result = check(ctx)
        if result.state in (TweakState.NOT_APPLICABLE, TweakState.ALREADY_OPTIMIZED):
            return result
        if ctx.dry_run:
            return TweakResult(True, TweakState.SUPPORTED, "Preview: service startup would be disabled", details=result.details)
        ctx.backup.backup_service(name)
        ok, message = set_service_start_mode(name, "disabled")
        if not ok:
            return TweakResult(False, TweakState.FAILED, f"Service configuration failed: {message}")
        data = service_state(name) or {}
        if str(data.get("StartMode", "")).lower() != "disabled":
            return TweakResult(False, TweakState.FAILED, "Service verification failed")
        if str(data.get("State", "")).lower() == "running":
            stop_service(name)
        return TweakResult(True, TweakState.APPLIED, "Service disabled and verified", changed=True)

    def rollback(ctx: TweakContext) -> TweakResult:
        markers = ctx.backup.current.manifest.get("services", []) if ctx.backup.current else []
        marker = next((m for m in reversed(markers) if m.get("Name") == name or m.get("name") == name), None)
        if not marker:
            return TweakResult(False, TweakState.ROLLBACK_FAILED, "Service backup not found")
        original = str(marker.get("StartMode", "manual")).lower()
        mapping = {"auto": "auto", "manual": "demand", "disabled": "disabled", "boot": "boot", "system": "system"}
        ok, message = set_service_start_mode(name, mapping.get(original, "demand"))
        if not ok:
            return TweakResult(False, TweakState.ROLLBACK_FAILED, message)
        if bool(marker.get("Started")):
            start_service(name)
        after = service_state(name) or {}
        if str(after.get("StartMode", "")).lower() != original:
            return TweakResult(False, TweakState.ROLLBACK_FAILED, "Service startup mode rollback could not be verified")
        return TweakResult(True, TweakState.ROLLED_BACK, "Service restored and verified", changed=True)

    return TweakDefinition(tweak_id, display, "SERVICES", description, why, "Disables an unused service startup path; service is not core-protected", risk, "21H2–26H2; service presence required", tuple(), True, quick, risk in (Risk.HIGH, Risk.CRITICAL), check, apply, rollback, {"service": name})


def _task_candidates() -> list[tuple[str, str, str, str, str, Risk, bool]]:
    """Exact baseline task names. Presence is verified on the target machine before use."""
    return [
        (r"\Microsoft\Windows\Feedback\Siuf\DmClient", "Feedback Client", "TELEMETRY", "Disables the legacy Feedback Client scheduled task when it exists.", "Reduces one periodic feedback workload; does not disable the Windows diagnostics platform.", "Feedback collection task stops scheduling", Risk.LOW, True),
        (r"\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload", "Feedback Scenario Client", "TELEMETRY", "Disables the Feedback scenario download task when it exists.", "Reduces scheduled feedback activity on systems where the task is present.", "Feedback scenario task stops scheduling", Risk.LOW, True),
        (r"\Microsoft\Windows\Customer Experience Improvement Program\Consolidator", "CEIP Consolidator", "TELEMETRY", "Disables the exact CEIP Consolidator task when present.", "Reduces periodic Customer Experience Improvement Program activity.", "CEIP task stops scheduling", Risk.LOW, True),
        (r"\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask", "CEIP Kernel Task", "TELEMETRY", "Disables the exact CEIP Kernel task when present.", "Reduces one scheduled CEIP component while leaving Windows Error Reporting intact.", "Kernel CEIP task stops scheduling", Risk.LOW, True),
        (r"\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip", "CEIP USB Task", "TELEMETRY", "Disables the exact USB CEIP task when present.", "Reduces scheduled USB experience telemetry.", "USB CEIP task stops scheduling", Risk.LOW, True),
        (r"\Microsoft\Windows\Maps\MapsToastTask", "Maps Toast Task", "LOCATION", "Disables the Maps toast task only when the task exists.", "Reduces Maps-specific scheduling on systems that do not need offline/maps UX.", "Maps toast scheduling stops", Risk.MEDIUM, False),
        (r"\Microsoft\Windows\Maps\MapsUpdateTask", "Maps Update Task", "LOCATION", "Disables the Maps update task only when the task exists.", "Stops scheduled offline map updates; not appropriate when Maps is needed.", "Maps updates stop scheduling", Risk.MEDIUM, False),
        (r"\Microsoft\Windows\Power Efficiency Diagnostics\AnalyzeAll", "Power Efficiency Analysis", "MAINTENANCE", "Disables the Power Efficiency diagnostics task only when explicitly selected.", "Reduces scheduled diagnostics workload; Reliability/diagnostic tooling may be affected.", "Power-efficiency analysis no longer runs automatically", Risk.MEDIUM, False),
        (r"\Microsoft\Windows\RAC\RacTask", "RAC Reliability Analysis", "DIAGNOSTICS", "Disables the exact Reliability Analysis Component task when selected.", "Reduces background reliability analysis at the cost of less automated historical diagnostics.", "Reliability analysis task stops scheduling", Risk.MEDIUM, False),
        (r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser", "Compatibility Appraiser", "TELEMETRY", "Disables the exact compatibility appraisal task when present.", "Reduces compatibility inventory/appraisal activity; may reduce telemetry available to Microsoft for compatibility analysis.", "Compatibility Appraiser task stops scheduling", Risk.MEDIUM, False),
    ]


def build_tweak_catalog(ctx_system: SystemInfo) -> list[TweakDefinition]:
    ctx_placeholder = TweakContext(ctx_system, None, lambda *_: None, True)
    tweaks: list[TweakDefinition] = []
    for path, name, category, description, why, effect, risk, quick in _task_candidates():
        tweak = _make_task_tweak(path, name, category, description, why, effect, risk, quick)
        tweaks.append(tweak)

    tweaks.append(make_registry_tweak(
        "registry.network_throttling_index",
        "NetworkThrottlingIndex",
        "NETWORK",
        "Controls the Multimedia Class Scheduler network throttling index where the value exists.",
        "Provides an advanced, explicit way to inspect and tune the legacy multimedia networking throttle.",
        "A value of 0xFFFFFFFF disables the throttling index. Performance impact is workload-dependent and not guaranteed.",
        Risk.MEDIUM,
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
        "NetworkThrottlingIndex",
        0xFFFFFFFF,
        "0xFFFFFFFF",
        False,
    ))
    tweaks.append(make_registry_tweak(
        "registry.system_responsiveness_zero",
        "SystemResponsiveness (advanced)",
        "CPU SCHEDULER",
        "Allows explicit editing of the SystemResponsiveness registry value when Windows exposes it.",
        "This is a scheduler-related knob frequently discussed in gaming-tuning tools, but it is workload- and build-dependent.",
        "Target 0 is offered only as a manually selected advanced change; it is not part of Quick mode.",
        Risk.HIGH,
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
        "SystemResponsiveness",
        0,
        "0",
        False,
        True,
    ))
    tweaks.append(make_registry_tweak(
        "registry.win32_priority_separation_24",
        "Win32PrioritySeparation = 24",
        "CPU SCHEDULER",
        "Explicit advanced registry change matching the reference value, only when the value is present.",
        "Provides an advanced scheduler setting without pretending that the reference value is universally optimal.",
        "Changes foreground/background scheduling policy encoded by Windows; validate with before/after workload testing.",
        Risk.HIGH,
        r"HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl",
        "Win32PrioritySeparation",
        24,
        "24",
        False,
        True,
    ))
    return tweaks


def get_all_catalog_metadata(ctx_system: SystemInfo) -> list[TweakDefinition]:
    return build_tweak_catalog(ctx_system)
