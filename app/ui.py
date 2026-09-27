from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.widgets import (
    PALETTE,
    CircularProgress,
    GlassCard,
    IconButton,
    LogConsole,
    MetricCard,
    NavButton,
    Pill,
    PrimaryButton,
    ScrollPage,
    SectionHeader,
    SolarSystemWidget,
    StarfieldWidget,
    ToggleSwitch,
)
from core.backup import BackupManager
from core.diagnostics import DiagnosticsManager
from core.engine import OptimizationEngine, OptimizationWorker, RunSummary
from core.system import (
    collect_system_info,
    enumerate_devices,
    enumerate_services,
    is_admin,
    run_command,
    service_state,
    set_service_start_mode,
    start_service,
    stop_service,
)
from optimizations.tweaks import Risk, TweakContext, TweakState


APP_TITLE = "Luna System Core"
APP_SUBTITLE = "Advanced Windows Optimization Suite"


STYLESHEET = f"""
QWidget {{
    color: {PALETTE['text']};
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11px;
}}
QMainWindow, QWidget#Root {{ background: transparent; }}
QLabel {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #263640; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: #35505E; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QFrame#GlassCard, QFrame#LogConsole {{ background: rgba(9,16,23,220); border: 1px solid #1E2D35; border-radius: 14px; }}
QFrame#LogConsole {{ background: rgba(3,7,10,235); }}
QLineEdit, QTextEdit, QComboBox {{ background: #091117; border: 1px solid #21323B; border-radius: 9px; padding: 8px 10px; color: #E7EEF4; selection-background-color: #19F59A; selection-color: #061008; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox QAbstractItemView {{ background: #0A1218; border: 1px solid #253640; color: #DDE6EC; selection-background-color: #143C31; }}
QCheckBox {{ color: #B4C2CA; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #33464F; border-radius: 4px; background: #0A1117; }}
QCheckBox::indicator:checked {{ background: #19F59A; border-color: #19F59A; }}
QHeaderView::section {{ background: #0B141B; color: #7F929E; border: none; padding: 6px; }}
QToolTip {{ color: #E7EEF4; background: #0A1218; border: 1px solid #29414C; padding: 7px; }}
"""


class AppPaths:
    def __init__(self):
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
        self.root = local / "LunaSystemCore"
        if os.environ.get("LUNA_PORTABLE", "0") == "1":
            self.root = Path(__file__).resolve().parents[1] / "runtime"
        self.logs = self.root / "logs"
        self.backups = self.root / "backups"
        self.reports = self.root / "reports"
        self.config = self.root / "config.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(exist_ok=True)
        self.backups.mkdir(exist_ok=True)
        self.reports.mkdir(exist_ok=True)

    def load_config(self) -> dict[str, Any]:
        defaults = {
            "onboarding_complete": False,
            "start_with_windows": False,
            "reduce_animations": False,
            "starfield": True,
            "log_level": "INFO",
            "auto_backup": True,
            "confirm_risky": True,
            "auto_diagnostics": True,
            "animation_intensity": 1.0,
            "portable": bool(os.environ.get("LUNA_PORTABLE")),
        }
        try:
            data = json.loads(self.config.read_text(encoding="utf-8"))
            defaults.update(data if isinstance(data, dict) else {})
        except (OSError, json.JSONDecodeError):
            pass
        return defaults

    def save_config(self, data: dict[str, Any]) -> None:
        self.config.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class OnboardingDialog(QDialog):
    def __init__(self, info: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Luna System Core — Setup")
        self.setModal(True)
        self.resize(720, 470)
        self.setStyleSheet(STYLESHEET)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.page_titles = ["WELCOME", "WHAT THIS PROGRAM DOES", "IMPORTANT", "LICENSE / AGREEMENT", "OPTIMIZATION MODE"]
        pages = [
            self._page("WELCOME", [
                ("Luna System Core", 28, PALETTE["emerald"]),
                (APP_SUBTITLE, 13, PALETTE["muted"]),
                (f"Detected: Windows {info.display_version} / build {info.build}", 11, PALETTE["text"]),
                (f"CPU: {info.cpu.get('name','Unknown')}", 11, PALETTE["muted"]),
                (f"RAM: {info.ram.get('gb','?')} GB", 11, PALETTE["muted"]),
                (f"Administrator: {'YES' if info.admin else 'NO'}", 11, PALETTE["muted"]),
            ]),
            self._page("WHAT THIS PROGRAM DOES", [
                ("A real Windows state controller, not a debloat script.", 15, PALETTE["text"]),
                ("• detects build-specific capabilities\n• inventories scheduled tasks, services, devices and network\n• creates granular backups before changes\n• verifies every applied tweak\n• measures available diagnostics before/after\n• rolls back verified changes when needed", 12, PALETTE["muted"]),
            ]),
            self._page("IMPORTANT", [
                ("You control the system changes.", 18, PALETTE["warning"]),
                ("Every supported change is checked against the current Windows state. Unsupported items are shown as NOT APPLICABLE rather than invented.\n\nA granular backup is created before optimization. A System Restore Point is attempted but is never treated as the only recovery mechanism.", 12, PALETTE["muted"]),
            ]),
            self._license_page(),
            self._mode_page(),
        ]
        for page in pages:
            self.stack.addWidget(page)
        nav = QHBoxLayout()
        self.back = PrimaryButton("BACK", "#81939D")
        self.next = PrimaryButton("NEXT")
        self.back.clicked.connect(self._back)
        self.next.clicked.connect(self._next)
        nav.addWidget(self.back)
        nav.addStretch(1)
        nav.addWidget(self.next)
        root.addLayout(nav)
        self._update_nav()

    def _page(self, title: str, rows: list[tuple[str, int, str]]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        t = QLabel(title)
        t.setStyleSheet(f"font-size:11px;font-weight:700;letter-spacing:2px;color:{PALETTE['blue']};")
        layout.addWidget(t)
        for text, size, color in rows:
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet(f"font-size:{size}px;color:{color};")
            layout.addWidget(label)
        layout.addStretch(1)
        return page

    def _license_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("LICENSE / AGREEMENT")
        title.setStyleSheet(f"font-size:11px;font-weight:700;letter-spacing:2px;color:{PALETTE['blue']};")
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "Luna System Core is freeware and proprietary. Source code is not distributed as part of the product.\n\n"
            "System changes are performed only after user selection. The application does not claim identical behavior across every Windows build, OEM system, firmware configuration, or driver stack.\n\n"
            "The product is not an official Microsoft utility and does not use Microsoft branding."
        )
        self.license_ok = QCheckBox("I understand that this application can modify Windows system state.")
        layout.addWidget(title)
        layout.addWidget(text, 1)
        layout.addWidget(self.license_ok)
        return page

    def _mode_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("OPTIMIZATION MODE")
        title.setStyleSheet(f"font-size:11px;font-weight:700;letter-spacing:2px;color:{PALETTE['blue']};")
        self.mode_box = QComboBox()
        self.mode_box.addItem("DEEP MANUAL", "DEEP MANUAL")
        self.mode_box.addItem("QUICK", "QUICK")
        info = QLabel("DEEP MANUAL exposes build-aware advanced controls. QUICK only selects SAFE/LOW-risk tweaks explicitly marked for automatic use.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#7F929E;font-size:11px;")
        layout.addWidget(title)
        layout.addWidget(self.mode_box)
        layout.addWidget(info)
        layout.addStretch(1)
        return page

    def selected_mode(self) -> str:
        return str(self.mode_box.currentData())

    def _back(self):
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_nav()

    def _next(self):
        idx = self.stack.currentIndex()
        if idx == 3 and not self.license_ok.isChecked():
            QMessageBox.warning(self, "Agreement required", "Confirm that you understand system changes before continuing.")
            return
        if idx < self.stack.count() - 1:
            self.stack.setCurrentIndex(idx + 1)
            self._update_nav()
        else:
            self.accept()

    def _update_nav(self):
        idx = self.stack.currentIndex()
        self.back.setEnabled(idx > 0)
        self.next.setText("FINISH" if idx == self.stack.count() - 1 else "NEXT")


class MainWindow(QWidget):
    def __init__(self, paths: AppPaths, first_run: bool = False):
        super().__init__()
        self.paths = paths
        self.config = paths.load_config()
        self.setObjectName("Root")
        self.setWindowTitle(APP_TITLE)
        self.resize(1440, 900)
        self.setMinimumSize(1180, 760)
        self.setStyleSheet(STYLESHEET)
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1] / "assets" / "luna.svg")))
        self.info = collect_system_info()
        self.engine = OptimizationEngine(paths.root, self.info)
        self.diagnostics = DiagnosticsManager(paths.root / "diagnostics")
        self.backup_manager = BackupManager(paths.root)
        self.log_path = paths.logs / f"luna_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.worker_thread: QThread | None = None
        self.worker: OptimizationWorker | None = None
        self.before_snapshot: dict[str, Any] | None = None
        self.current_page = 0
        self._build_ui()
        self._refresh_all()
        if first_run:
            QTimer.singleShot(350, self._show_onboarding)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)
        background = StarfieldWidget(self, 105)
        background.setGeometry(self.rect())
        background.lower()
        self._background = background
        # The background widget tracks window resizing.
        original_resize = self.resizeEvent
        def resize_event(event):
            background.setGeometry(self.rect())
            original_resize(event)
        self.resizeEvent = resize_event  # type: ignore[method-assign]

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        self.sidebar = self._build_sidebar()
        body.addWidget(self.sidebar, 0)
        content = QVBoxLayout()
        content.setSpacing(8)
        content.addWidget(self._build_header(), 0)
        self.stack = QStackedWidget()
        self.pages = [self._dashboard_page(), self._optimizer_page(), self._diagnostics_page(), self._devices_page(), self._services_page(), self._backup_page(), self._settings_page(), self._about_page()]
        for page in self.pages:
            self.stack.addWidget(page)
        content.addWidget(self.stack, 1)
        body.addLayout(content, 1)
        root.addLayout(body, 1)

    def _build_sidebar(self) -> QWidget:
        panel = GlassCard()
        panel.setFixedWidth(218)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(13, 16, 13, 13)
        layout.setSpacing(5)
        top = QHBoxLayout()
        logo = QLabel("LUNA")
        logo.setStyleSheet(f"font-size:20px;font-weight:800;color:{PALETTE['emerald']};letter-spacing:2px;")
        top.addWidget(logo)
        top.addStretch(1)
        solar = SolarSystemWidget()
        solar.planet_hovered.connect(self._solar_hover)
        self.solar = solar
        top.addWidget(solar)
        layout.addLayout(top)
        sub = QLabel("SYSTEM CORE")
        sub.setStyleSheet("color:#61747F;font-size:9px;letter-spacing:2px;padding:4px;")
        layout.addWidget(sub)
        nav_items = [
            ("Dashboard", "[]"),
            ("Optimizer", "<>"),
            ("Diagnostics", "()"),
            ("Devices", "[]"),
            ("Services", "##"),
            ("Backup & Restore", "//"),
            ("Settings", "**"),
            ("About", "i"),
        ]
        self.nav_buttons: list[NavButton] = []
        for i, (text, icon) in enumerate(nav_items):
            btn = NavButton(text, icon)
            btn.clicked.connect(lambda checked=False, index=i: self._navigate(index))
            self.nav_buttons.append(btn)
            layout.addWidget(btn)
        layout.addStretch(1)
        admin = Pill("ADMIN" if self.info.admin else "ADMIN REQUIRED", PALETTE["emerald"] if self.info.admin else PALETTE["warning"])
        layout.addWidget(admin)
        return panel

    def _build_header(self) -> QWidget:
        card = GlassCard()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 11, 15, 11)
        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size:13px;font-weight:700;color:#E7EEF4;")
        version = QLabel(f"Windows {self.info.display_version} · Build {self.info.build}")
        version.setStyleSheet("color:#71848F;font-size:10px;")
        stack = QVBoxLayout()
        stack.setSpacing(2)
        stack.addWidget(title)
        stack.addWidget(version)
        layout.addLayout(stack)
        layout.addStretch(1)
        self.system_pill = Pill("READY", PALETTE["emerald"])
        layout.addWidget(self.system_pill)
        self.reboot_pill = Pill("NO REBOOT", PALETTE["muted"])
        layout.addWidget(self.reboot_pill)
        refresh = IconButton("R")
        refresh.setToolTip("Refresh system detection")
        refresh.clicked.connect(self._refresh_all)
        layout.addWidget(refresh)
        return card

    def _dashboard_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("Dashboard", "Current Windows state, hardware summary, and optimization status."))
        row = QHBoxLayout()
        self.dashboard_progress = CircularProgress(0)
        progress_card = GlassCard()
        p_layout = QVBoxLayout(progress_card)
        p_layout.setContentsMargins(15, 10, 15, 10)
        p_layout.addWidget(self.dashboard_progress, 1, Qt.AlignCenter)
        p_layout.addWidget(QLabel("SESSION / OPTIMIZATION SCORE"), 0, Qt.AlignCenter)
        row.addWidget(progress_card, 0)
        grid = QGridLayout()
        self.metric_windows = MetricCard("Windows", "—", "", PALETTE["blue"])
        self.metric_cpu = MetricCard("CPU", "—", "", PALETTE["emerald"])
        self.metric_gpu = MetricCard("GPU", "—", "", PALETTE["crimson"])
        self.metric_ram = MetricCard("RAM", "—", "", PALETTE["warning"])
        self.metric_storage = MetricCard("Storage", "—", "", PALETTE["blue"])
        self.metric_network = MetricCard("Network", "—", "", PALETTE["emerald"])
        self.metric_admin = MetricCard("Admin", "—", "", PALETTE["emerald"])
        self.metric_update = MetricCard("Windows Update", "—", "", PALETTE["muted"])
        for idx, card in enumerate([self.metric_windows, self.metric_cpu, self.metric_gpu, self.metric_ram, self.metric_storage, self.metric_network, self.metric_admin, self.metric_update]):
            grid.addWidget(card, idx // 2, idx % 2)
        row.addLayout(grid, 1)
        page.layout.addLayout(row)
        info = GlassCard()
        info_layout = QGridLayout(info)
        info_layout.setContentsMargins(16, 14, 16, 14)
        self.dashboard_state = QLabel("READY")
        self.dashboard_state.setStyleSheet(f"font-size:20px;font-weight:700;color:{PALETTE['emerald']};")
        info_layout.addWidget(QLabel("SYSTEM STATE"), 0, 0)
        info_layout.addWidget(self.dashboard_state, 0, 1)
        self.dashboard_last = QLabel("No optimization session in this run.")
        self.dashboard_last.setStyleSheet("color:#7F929E;")
        info_layout.addWidget(QLabel("LAST SESSION"), 1, 0)
        info_layout.addWidget(self.dashboard_last, 1, 1)
        info_layout.addWidget(QLabel("SECURE BOOT"), 0, 2)
        self.dashboard_secure = QLabel("—")
        info_layout.addWidget(self.dashboard_secure, 0, 3)
        info_layout.addWidget(QLabel("VBS / HVCI"), 1, 2)
        self.dashboard_vbs = QLabel("—")
        info_layout.addWidget(self.dashboard_vbs, 1, 3)
        page.layout.addWidget(info)
        return page

    def _optimizer_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("Optimizer", "Build-aware scan, preview, controlled execution, verification, and rollback."))
        top = QHBoxLayout()
        self.optimizer_progress = CircularProgress(0)
        progress_card = GlassCard()
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.addWidget(self.optimizer_progress, 1, Qt.AlignCenter)
        self.optimizer_count = QLabel("0 / 0")
        self.optimizer_count.setAlignment(Qt.AlignCenter)
        self.optimizer_count.setStyleSheet("font-size:12px;color:#93A5AF;")
        progress_layout.addWidget(self.optimizer_count)
        top.addWidget(progress_card, 0)
        summary = GlassCard()
        sgrid = QGridLayout(summary)
        sgrid.setContentsMargins(15, 14, 15, 14)
        labels = ["Will apply", "Already optimized", "Not applicable", "Risky", "Requires reboot"]
        self.scan_labels: dict[str, QLabel] = {}
        for i, key in enumerate(["will_apply", "already", "not_applicable", "risky", "requires_reboot"]):
            sgrid.addWidget(QLabel(labels[i]), i, 0)
            value = QLabel("—")
            value.setStyleSheet("font-size:15px;font-weight:700;color:#E7EEF4;")
            sgrid.addWidget(value, i, 1)
            self.scan_labels[key] = value
        buttons = QHBoxLayout()
        self.scan_btn = PrimaryButton("SCAN / PREVIEW", PALETTE["blue"])
        self.quick_btn = PrimaryButton("QUICK OPTIMIZE")
        self.deep_btn = PrimaryButton("START SELECTED")
        self.cancel_btn = PrimaryButton("CANCEL", PALETTE["crimson"])
        self.cancel_btn.hide()
        self.scan_btn.clicked.connect(self._scan_preview)
        self.quick_btn.clicked.connect(self._start_quick)
        self.deep_btn.clicked.connect(self._start_selected)
        self.cancel_btn.clicked.connect(self._cancel_prompt)
        for btn in (self.scan_btn, self.quick_btn, self.deep_btn, self.cancel_btn):
            buttons.addWidget(btn)
        sgrid.addLayout(buttons, 5, 0, 1, 2)
        top.addWidget(summary, 1)
        page.layout.addLayout(top)
        categories = GlassCard()
        cat_layout = QVBoxLayout(categories)
        cat_title = QLabel("TWEAK CATALOG")
        cat_title.setStyleSheet("font-size:10px;font-weight:700;letter-spacing:1px;color:#71848F;")
        cat_layout.addWidget(cat_title)
        self.tweak_list = QListWidget()
        self.tweak_list.setStyleSheet("QListWidget{background:transparent;border:none;} QListWidget::item{padding:9px;border-bottom:1px solid #122029;} QListWidget::item:selected{background:#0C1A20;border:1px solid #1E3A3B;border-radius:8px;}")
        self.tweak_list.itemDoubleClicked.connect(self._tweak_details)
        cat_layout.addWidget(self.tweak_list, 1)
        page.layout.addWidget(categories, 1)
        log = LogConsole()
        log.setMinimumHeight(220)
        self.log_console = log
        page.layout.addWidget(log)
        return page

    def _diagnostics_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("Diagnostics", "Real measurements only. Unsupported metrics are explicitly reported as unavailable."))
        actions = QHBoxLayout()
        self.diag_before = PrimaryButton("CAPTURE BEFORE", PALETTE["blue"])
        self.diag_after = PrimaryButton("CAPTURE AFTER")
        self.input_btn = PrimaryButton("SYNTHETIC INPUT TEST", PALETTE["blue"])
        self.latency_btn = PrimaryButton("OPEN LATENCYMON", "#A87CFF")
        actions.addWidget(self.diag_before)
        actions.addWidget(self.diag_after)
        actions.addWidget(self.input_btn)
        actions.addWidget(self.latency_btn)
        actions.addStretch(1)
        self.diag_before.clicked.connect(self._capture_before)
        self.diag_after.clicked.connect(self._capture_after)
        self.input_btn.clicked.connect(self._input_latency)
        self.latency_btn.clicked.connect(self._open_latencymon)
        page.layout.addLayout(actions)
        cards = QGridLayout()
        self.dpc_card = MetricCard("DPC Latency", "Unavailable", "No kernel/ETW sampler installed", PALETTE["warning"])
        self.latency_card = MetricCard("Synthetic Input", "—", "Application dispatch measurement", PALETTE["emerald"])
        self.tasks_card = MetricCard("Scheduled Tasks", "—", "", PALETTE["blue"])
        self.services_card = MetricCard("Services", "—", "", PALETTE["blue"])
        cards.addWidget(self.dpc_card, 0, 0)
        cards.addWidget(self.latency_card, 0, 1)
        cards.addWidget(self.tasks_card, 1, 0)
        cards.addWidget(self.services_card, 1, 1)
        page.layout.addLayout(cards)
        compare = GlassCard()
        cl = QVBoxLayout(compare)
        self.diag_compare = QLabel("BEFORE / AFTER comparison will appear here after both snapshots are captured.")
        self.diag_compare.setStyleSheet("color:#82939D;")
        self.diag_compare.setWordWrap(True)
        cl.addWidget(self.diag_compare)
        page.layout.addWidget(compare)
        return page

    def _devices_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("Devices", "Device Manager-like inventory. Device changes are manual and treated as high-impact."))
        actions = QHBoxLayout()
        refresh = PrimaryButton("REFRESH DEVICES", PALETTE["blue"])
        disable = PrimaryButton("DISABLE SELECTED", PALETTE["crimson"])
        enable = PrimaryButton("ENABLE SELECTED", PALETTE["emerald"])
        copy = PrimaryButton("COPY INSTANCE ID", "#7C91A0")
        refresh.clicked.connect(self._refresh_devices)
        disable.clicked.connect(lambda: self._device_toggle(False))
        enable.clicked.connect(lambda: self._device_toggle(True))
        copy.clicked.connect(self._copy_device_id)
        for b in (refresh, disable, enable, copy):
            actions.addWidget(b)
        actions.addStretch(1)
        page.layout.addLayout(actions)
        card = GlassCard()
        lay = QVBoxLayout(card)
        self.device_list = QListWidget()
        self.device_list.setStyleSheet("QListWidget{background:transparent;border:none;} QListWidget::item{padding:10px;border-bottom:1px solid #122029;} QListWidget::item:selected{background:#0C1A20;border:1px solid #2A454E;border-radius:7px;}")
        lay.addWidget(self.device_list)
        page.layout.addWidget(card)
        return page

    def _services_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("Services", "Inventory of real Windows services. Core service names are protected from optimization actions."))
        search_row = QHBoxLayout()
        self.service_filter = QLineEdit()
        self.service_filter.setPlaceholderText("Filter services by name, state, startup type...")
        refresh = PrimaryButton("REFRESH", PALETTE["blue"])
        refresh.clicked.connect(self._refresh_services)
        search_row.addWidget(self.service_filter, 1)
        search_row.addWidget(refresh)
        page.layout.addLayout(search_row)
        self.service_filter.textChanged.connect(self._filter_services)
        card = GlassCard()
        lay = QVBoxLayout(card)
        self.service_list = QListWidget()
        self.service_list.setStyleSheet("QListWidget{background:transparent;border:none;} QListWidget::item{padding:9px;border-bottom:1px solid #122029;} QListWidget::item:selected{background:#0C1A20;border:1px solid #263D47;border-radius:7px;}")
        lay.addWidget(self.service_list)
        actions = QHBoxLayout()
        disable = PrimaryButton("DISABLE STARTUP", PALETTE["crimson"])
        enable = PrimaryButton("SET MANUAL", PALETTE["emerald"])
        disable.clicked.connect(lambda: self._service_change("disabled"))
        enable.clicked.connect(lambda: self._service_change("demand"))
        actions.addWidget(disable)
        actions.addWidget(enable)
        actions.addStretch(1)
        lay.addLayout(actions)
        page.layout.addWidget(card)
        return page

    def _backup_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("Backup & Restore", "Granular session backups supplement Windows System Restore."))
        actions = QHBoxLayout()
        create = PrimaryButton("CREATE BACKUP")
        restore = PrimaryButton("RESTORE LAST SESSION", PALETTE["crimson"])
        open_loc = PrimaryButton("OPEN BACKUP LOCATION", PALETTE["blue"])
        create.clicked.connect(self._manual_backup)
        restore.clicked.connect(self._restore_last)
        open_loc.clicked.connect(lambda: self._open_path(self.paths.backups))
        for btn in (create, restore, open_loc):
            actions.addWidget(btn)
        actions.addStretch(1)
        page.layout.addLayout(actions)
        card = GlassCard()
        lay = QVBoxLayout(card)
        self.backup_status = QLabel("Scanning backup sessions...")
        self.backup_status.setStyleSheet("color:#8B9DA6;")
        lay.addWidget(self.backup_status)
        self.backup_list = QListWidget()
        self.backup_list.setStyleSheet("QListWidget{background:transparent;border:none;} QListWidget::item{padding:9px;border-bottom:1px solid #122029;}")
        lay.addWidget(self.backup_list)
        page.layout.addWidget(card)
        return page

    def _settings_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("Settings", "UI preferences and local application behavior."))
        card = GlassCard()
        form = QFormLayout(card)
        form.setContentsMargins(16, 16, 16, 16)
        self.setting_start = ToggleSwitch(bool(self.config.get("start_with_windows")))
        self.setting_anim = ToggleSwitch(bool(self.config.get("reduce_animations")))
        self.setting_stars = ToggleSwitch(bool(self.config.get("starfield", True)))
        self.setting_auto_backup = ToggleSwitch(bool(self.config.get("auto_backup", True)))
        self.setting_risky = ToggleSwitch(bool(self.config.get("confirm_risky", True)))
        self.setting_auto_diag = ToggleSwitch(bool(self.config.get("auto_diagnostics", True)))
        form.addRow("Start with Windows", self.setting_start)
        form.addRow("Reduce animations", self.setting_anim)
        form.addRow("Enable starfield", self.setting_stars)
        form.addRow("Auto backup", self.setting_auto_backup)
        form.addRow("Confirm risky tweaks", self.setting_risky)
        form.addRow("Auto diagnostics after optimization", self.setting_auto_diag)
        self.setting_stars.changed.connect(lambda checked: self._save_setting("starfield", checked))
        self.setting_auto_backup.changed.connect(lambda checked: self._save_setting("auto_backup", checked))
        self.setting_risky.changed.connect(lambda checked: self._save_setting("confirm_risky", checked))
        self.setting_auto_diag.changed.connect(lambda checked: self._save_setting("auto_diagnostics", checked))
        self.setting_anim.changed.connect(lambda checked: self._save_setting("reduce_animations", checked))
        self.setting_start.changed.connect(self._toggle_startup)
        page.layout.addWidget(card)
        paths_card = GlassCard()
        pl = QFormLayout(paths_card)
        pl.addRow("Data root", QLabel(str(self.paths.root)))
        pl.addRow("Logs", QLabel(str(self.paths.logs)))
        pl.addRow("Backups", QLabel(str(self.paths.backups)))
        pl.addRow("Reports", QLabel(str(self.paths.reports)))
        page.layout.addWidget(paths_card)
        return page

    def _about_page(self) -> QWidget:
        page = ScrollPage()
        page.layout.addWidget(SectionHeader("About", "Product identity, technical scope, and license information."))
        card = GlassCard()
        lay = QVBoxLayout(card)
        title = QLabel("LUNA SYSTEM CORE")
        title.setStyleSheet(f"font-size:28px;font-weight:800;color:{PALETTE['emerald']};letter-spacing:2px;")
        lay.addWidget(title)
        text = QLabel(
            "Advanced Windows Optimization Suite\n\n"
            "Freeware, proprietary software. The product uses runtime detection instead of assuming that a tweak, task, service, or registry value exists on every Windows 11 build.\n\n"
            "Security boundaries: Windows Defender, Firewall, Secure Boot, TPM, LSASS protections, RPC/DCOM, Event Log, Plug and Play, storage stack, Windows Update core servicing, and other protected infrastructure are not targeted by the automatic optimization catalog."
        )
        text.setWordWrap(True)
        text.setStyleSheet("color:#95A7B0;line-height:1.5;")
        lay.addWidget(text)
        page.layout.addWidget(card)
        tech = GlassCard()
        tl = QVBoxLayout(tech)
        t = QLabel("TECHNICAL STACK")
        t.setStyleSheet("color:#71848F;font-size:10px;font-weight:700;letter-spacing:1px;")
        tl.addWidget(t)
        stack = QLabel("Python 3.12+ · PySide6 / Qt6 · psutil · ctypes · native Windows command tools")
        stack.setStyleSheet("color:#D8E3E9;")
        tl.addWidget(stack)
        page.layout.addWidget(tech)
        return page

    def _navigate(self, index: int):
        self.current_page = index
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        if index == 1:
            self._populate_tweaks()
        elif index == 3:
            self._refresh_devices()
        elif index == 4:
            self._refresh_services()
        elif index == 5:
            self._refresh_backups()

    def _refresh_all(self):
        self.info = collect_system_info()
        self.engine.system = self.info
        self.engine.catalog = __import__("optimizations.tweaks", fromlist=["build_tweak_catalog"]).build_tweak_catalog(self.info)
        self._update_dashboard()
        self._populate_tweaks()
        self._refresh_backups()
        self._log("SYSTEM", f"Windows {self.info.display_version} / build {self.info.build} detected")
        self._log("SYSTEM", f"Admin={'YES' if self.info.admin else 'NO'}; CPU={self.info.cpu.get('name','Unknown')}")
        if self.info.features.get("xbox"):
            self._log("WARNING", "Xbox components detected; Xbox-related changes are not selected automatically")
        if self.info.features.get("vpn"):
            self._log("WARNING", "VPN-like network adapter detected; network/device optimizations require explicit selection")

    def _update_dashboard(self, percentage: float | None = None):
        if percentage is None:
            try:
                scan = self.engine.inspect()
                applicable = scan["will_apply"] + scan["already"]
                total = applicable + scan["not_applicable"]
                percentage = (scan["already"] / max(1, applicable)) * 100 if applicable else 0
            except Exception:
                percentage = 0
        self.dashboard_progress.setValue(percentage)
        self.optimizer_progress.setValue(percentage)
        self.metric_windows.value_label.setText(f"{self.info.display_version}")
        self.metric_windows.detail_label.setText(f"Build {self.info.build} · {self.info.architecture}")
        self.metric_cpu.value_label.setText(str(self.info.cpu.get("name", "Unknown")).replace("(R)", "")[:26])
        self.metric_cpu.detail_label.setText(f"{self.info.cpu.get('logical_processors','?')} logical processors")
        gpu = self.info.gpu[0]["Name"] if self.info.gpu else "Not detected"
        self.metric_gpu.value_label.setText(gpu[:25])
        self.metric_gpu.detail_label.setText(self.info.gpu[0].get("DriverVersion", "") if self.info.gpu else "")
        self.metric_ram.value_label.setText(f"{self.info.ram.get('gb','?')} GB")
        self.metric_ram.detail_label.setText("Physical memory")
        storage = self.info.storage[0] if self.info.storage else {}
        self.metric_storage.value_label.setText(str(storage.get("media_type") or storage.get("type") or "Unknown"))
        self.metric_storage.detail_label.setText(str(storage.get("friendly_name") or storage.get("device") or ""))
        network = ", ".join(sorted(set(a.get("kind", "Other") for a in self.info.network))) or "None"
        self.metric_network.value_label.setText(network[:22])
        self.metric_network.detail_label.setText(f"{len(self.info.network)} adapter(s)")
        self.metric_admin.value_label.setText("YES" if self.info.admin else "NO")
        self.metric_admin.detail_label.setText("Elevated process" if self.info.admin else "Restart as Administrator")
        upd = self.info.windows_update.get("service", "Unknown")
        self.metric_update.value_label.setText(upd.split("/")[0].strip()[:15])
        self.metric_update.detail_label.setText("Windows Update service")
        self.dashboard_secure.setText(self.info.secure_boot)
        self.dashboard_vbs.setText(f"{self.info.vbs.get('VBS')} / {self.info.vbs.get('HVCI')}")
        self.dashboard_state.setText("READY" if self.info.admin else "ACTION REQUIRED")
        self.dashboard_state.setStyleSheet(f"font-size:20px;font-weight:700;color:{PALETTE['emerald'] if self.info.admin else PALETTE['warning']};")
        self.system_pill.setText("READY" if self.info.admin else "ADMIN REQUIRED")
        self.system_pill.setStyleSheet(self.system_pill.styleSheet().replace(PALETTE["emerald"], PALETTE["warning"] if not self.info.admin else PALETTE["emerald"]))

    def _populate_tweaks(self):
        if not hasattr(self, "tweak_list"):
            return
        self.tweak_list.clear()
        for tweak in self.engine.catalog:
            result = tweak.inspect(TweakContext(self.info, self.engine.backup, lambda *_: None, True))
            check = QCheckBox()
            state = result.state.value
            prefix = {TweakState.ALREADY_OPTIMIZED.value: "[OK]", TweakState.NOT_APPLICABLE.value: "[NA]", TweakState.SUPPORTED.value: "[ ]"}.get(state, "[?]")
            item = QListWidgetItem(f"{prefix}  {tweak.name}   | {tweak.category}   | {tweak.risk.value}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if result.state == TweakState.SUPPORTED else Qt.Unchecked)
            if result.state != TweakState.SUPPORTED:
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            item.setData(Qt.UserRole, tweak.id)
            item.setData(Qt.UserRole + 1, result)
            self.tweak_list.addItem(item)
            if result.state == TweakState.NOT_APPLICABLE:
                item.setForeground(QColor(PALETTE["muted"]))
            elif result.state == TweakState.ALREADY_OPTIMIZED:
                item.setForeground(QColor(PALETTE["emerald"]))
            else:
                item.setForeground(QColor(PALETTE["text"]))
        self._scan_preview()

    def _scan_preview(self):
        try:
            scan = self.engine.inspect()
        except Exception as exc:
            self._log("ERROR", f"Scan failed: {exc}")
            return
        for key, label in self.scan_labels.items():
            label.setText(str(scan[key]))
        self._populate_tweaks_no_recursion()
        self._log("INFO", f"Preview: {scan['will_apply']} will apply, {scan['already']} already optimized, {scan['not_applicable']} not applicable, {scan['risky']} risky")

    def _populate_tweaks_no_recursion(self):
        if not hasattr(self, "tweak_list"):
            return
        for idx in range(self.tweak_list.count()):
            item = self.tweak_list.item(idx)
            tweak_id = item.data(Qt.UserRole)
            tweak = next((t for t in self.engine.catalog if t.id == tweak_id), None)
            if not tweak:
                continue
            previous_checked = item.checkState() == Qt.Checked
            result = tweak.inspect(TweakContext(self.info, self.engine.backup, lambda *_: None, True))
            prefix = "OK" if result.state == TweakState.ALREADY_OPTIMIZED else "NA" if result.state == TweakState.NOT_APPLICABLE else " "
            item.setText(f"[{prefix}]  {tweak.name}   | {tweak.category}   | {tweak.risk.value}")
            item.setData(Qt.UserRole + 1, result)
            if result.state == TweakState.SUPPORTED:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if previous_checked else Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        selected = sum(1 for i in range(self.tweak_list.count()) if self.tweak_list.item(i).checkState() == Qt.Checked)
        self.optimizer_count.setText(f"{selected} / {len(self.engine.catalog)}")

    def _selected_ids(self) -> list[str]:
        return [self.tweak_list.item(i).data(Qt.UserRole) for i in range(self.tweak_list.count()) if self.tweak_list.item(i).checkState() == Qt.Checked and self.tweak_list.item(i).data(Qt.UserRole + 1).state == TweakState.SUPPORTED]

    def _start_quick(self):
        ids = self.engine.quick_ids()
        self._begin_run(ids, "QUICK")

    def _start_selected(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Nothing selected", "No currently supported tweaks are available for this run.")
            return
        self._begin_run(ids, "DEEP MANUAL")

    def _begin_run(self, ids: list[str], mode: str):
        if not is_admin():
            QMessageBox.warning(self, "Administrator privileges required", "Restart the application as Administrator before applying system changes.")
            return
        risky = [t for t in self.engine.catalog if t.id in ids and t.risk in (Risk.HIGH, Risk.CRITICAL, Risk.MEDIUM)]
        if risky:
            box = QMessageBox(self)
            box.setWindowTitle("Risk confirmation")
            box.setText(f"{len(risky)} selected tweaks are MEDIUM/HIGH/CRITICAL risk or can alter system behavior.")
            box.setInformativeText("Proceed only if you understand the shown consequences in the catalog.")
            box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
            if box.exec() != QMessageBox.Ok:
                return
        self.before_snapshot = self.diagnostics.snapshot(self.info)
        self.scan_btn.setEnabled(False)
        self.quick_btn.setEnabled(False)
        self.deep_btn.setEnabled(False)
        self.cancel_btn.show()
        self.log_console.add_line(f"Starting {mode} optimization", "INFO")
        self.worker_thread = QThread(self)
        self.worker = OptimizationWorker(self.engine, ids, mode, bool(self.config.get("confirm_risky", True)))
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.log_signal.connect(self._log)
        self.worker.finished.connect(self._run_finished)
        self.worker.error.connect(self._run_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.error.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._worker_cleanup)
        self.worker_thread.start()

    def _cancel_prompt(self):
        box = QMessageBox(self)
        box.setWindowTitle("Optimization cancellation")
        box.setText("Optimization can be stopped cooperatively.")
        box.setInformativeText("Continue leaves the worker running. Stop and rollback restores only verified changes from this session.")
        continue_btn = box.addButton("CONTINUE OPTIMIZATION", QMessageBox.AcceptRole)
        rollback_btn = box.addButton("STOP AND ROLLBACK", QMessageBox.DestructiveRole)
        box.addButton("CANCEL", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == rollback_btn and self.worker:
            self.engine.set_cancel()
            self.engine.cancel_event.set()
            self.log_console.add_line("Cancellation requested; rollback will begin after the current safe point.", "ROLLBACK")
        elif box.clickedButton() == continue_btn:
            self.log_console.add_line("Optimization continues.", "ACTION")

    def _run_finished(self, summary: RunSummary):
        cancelled = self.engine.cancel_event.is_set()
        if cancelled and self.engine.applied:
            self.log_console.add_line("Rolling back changes applied before cancellation.", "ROLLBACK")
            failures = self.engine.rollback_applied()
            summary.rolled_back = len(self.engine.applied) - failures
        after = self.diagnostics.snapshot(self.info)
        self._update_dashboard(summary.optimized_percentage)
        self.optimizer_progress.setValue(summary.optimized_percentage)
        self.optimizer_count.setText(f"{summary.success + summary.already} / {summary.applicable}")
        self.deep_btn.setEnabled(True)
        self.quick_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        self.cancel_btn.hide()
        self.reboot_pill.setText("REBOOT REQUIRED" if summary.requires_reboot else "NO REBOOT")
        self.reboot_pill.setStyleSheet(f"QLabel{{background:rgba(255,204,102,0.10);border:1px solid {PALETTE['warning']}55;color:{PALETTE['warning']};border-radius:13px;padding:2px 9px;font-weight:600;}}" if summary.requires_reboot else self.reboot_pill.styleSheet())
        self.dashboard_last.setText(f"{summary.mode} · {summary.success} applied · {summary.already} already · {summary.failed} failed · {summary.rolled_back} rolled back")
        self.dashboard_state.setText("OPTIMIZED" if summary.failed == 0 and not cancelled else "PARTIALLY OPTIMIZED")
        self._log("SUCCESS", f"Optimization finished: {summary.optimized_percentage:.1f}%")
        report = self.paths.reports / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        try:
            self.engine.export_report(report, summary, self.before_snapshot, after)
            self._log("INFO", f"Report exported: {report}")
        except Exception as exc:
            self._log("ERROR", f"Report export failed: {exc}")
        if self.config.get("auto_diagnostics", True):
            self._update_diagnostics_from_snapshot(after)
        self._populate_tweaks_no_recursion()

    def _run_error(self, message: str):
        self._log("ERROR", message)
        self.scan_btn.setEnabled(True)
        self.quick_btn.setEnabled(True)
        self.deep_btn.setEnabled(True)
        self.cancel_btn.hide()

    def _worker_cleanup(self):
        if self.worker:
            self.worker.deleteLater()
        if self.worker_thread:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None

    def _log(self, level: str, message: str):
        if hasattr(self, "log_console"):
            self.log_console.add_line(message, level)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{datetime.now().isoformat(timespec='seconds')}] [{level}] {message}\n")
        except OSError:
            pass

    def _tweak_details(self, item: QListWidgetItem):
        tweak_id = item.data(Qt.UserRole)
        tweak = next((t for t in self.engine.catalog if t.id == tweak_id), None)
        if not tweak:
            return
        result = tweak.inspect(TweakContext(self.info, self.engine.backup, lambda *_: None, True))
        text = (
            f"{tweak.name}\n\nCategory: {tweak.category}\nRisk: {tweak.risk.value}\nSupported: {tweak.supported_builds}\n"
            f"State: {result.state.value}\nCurrent: {result.message}\n\nDescription:\n{tweak.description}\n\nWhy:\n{tweak.why}\n\nPotential effect:\n{tweak.effect}\n\n"
            f"Requires reboot: {'YES' if tweak.requires_reboot else 'NO'}\n"
        )
        QMessageBox.information(self, "Tweak details", text)

    def _capture_before(self):
        self.before_snapshot = self.diagnostics.snapshot(self.info)
        self._update_diagnostics_from_snapshot(self.before_snapshot)
        self._log("INFO", "Diagnostics BEFORE captured")

    def _capture_after(self):
        after = self.diagnostics.snapshot(self.info)
        self._update_diagnostics_from_snapshot(after)
        if self.before_snapshot:
            self._compare_diagnostics(self.before_snapshot, after)
        self._log("INFO", "Diagnostics AFTER captured")

    def _update_diagnostics_from_snapshot(self, snap: dict[str, Any]):
        self.tasks_card.value_label.setText(str(snap.get("active_relevant_scheduled_tasks", "—")))
        self.tasks_card.detail_label.setText(f"of {snap.get('scheduled_tasks_total','—')} scheduled tasks")
        self.services_card.value_label.setText(str(snap.get("services_total", "—")))
        self.services_card.detail_label.setText("service inventory")
        self.dpc_card.value_label.setText("Unavailable")

    def _compare_diagnostics(self, before: dict[str, Any], after: dict[str, Any]):
        metrics = []
        for key, label in (("active_relevant_scheduled_tasks", "Active scheduled tasks"), ("scheduled_tasks_total", "Scheduled tasks total"), ("services_total", "Services"), ("network_adapters", "Network adapters")):
            if key in before and key in after:
                metrics.append(f"{label}: {before[key]} -> {after[key]} (delta {after[key]-before[key] if isinstance(before[key], (int,float)) and isinstance(after[key], (int,float)) else 'n/a'})")
        self.diag_compare.setText("\n".join(metrics) or "No comparable numeric metrics captured.")

    def _input_latency(self):
        result = self.diagnostics.synthetic_input_latency()
        if result.average_ms is None:
            self.latency_card.value_label.setText("Unavailable")
            self.latency_card.detail_label.setText(result.note)
            return
        self.latency_card.value_label.setText(f"{result.average_ms:.3f} ms")
        self.latency_card.detail_label.setText(f"min {result.minimum_ms:.3f} · max {result.maximum_ms:.3f} · {result.method}")
        self._log("INFO", f"Synthetic input test: avg={result.average_ms:.3f} ms")

    def _open_latencymon(self):
        ok, msg = self.diagnostics.launch_latency_mon()
        self._log("INFO" if ok else "WARNING", msg)
        if not ok:
            QMessageBox.information(self, "LatencyMon", msg)

    def _refresh_devices(self):
        if not hasattr(self, "device_list"):
            return
        self.device_list.clear()
        devices = enumerate_devices()
        for dev in devices:
            item = QListWidgetItem(f"{dev['name']}  |  {dev['status']}  |  {dev['class']}\n{dev['instance_id']}")
            item.setData(Qt.UserRole, dev)
            self.device_list.addItem(item)
        if not devices:
            self.device_list.addItem("No connected device inventory available.")

    def _selected_device(self) -> dict[str, Any] | None:
        item = self.device_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _copy_device_id(self):
        dev = self._selected_device()
        if not dev:
            return
        QApplication.clipboard().setText(str(dev.get("instance_id", "")))
        self._log("INFO", "Device Instance ID copied")

    def _device_toggle(self, enable: bool):
        dev = self._selected_device()
        if not dev or not dev.get("instance_id"):
            return
        action = "enable" if enable else "disable"
        if not enable:
            if QMessageBox.question(self, "High-impact device change", f"Disable this device?\n\n{dev['name']}\n{dev['instance_id']}") != QMessageBox.Yes:
                return
        if not is_admin():
            QMessageBox.warning(self, "Administrator required", "Device state changes require elevation.")
            return
        try:
            if enable:
                code, out, err = run_command(["pnputil.exe", "/enable-device", str(dev["instance_id"])], 30)
            else:
                code, out, err = run_command(["pnputil.exe", "/disable-device", str(dev["instance_id"])], 30)
            if code != 0:
                raise RuntimeError(out or err)
            verify_code, verify_out, _ = run_command(["pnputil.exe", "/enum-devices", "/instanceid", str(dev["instance_id"]), "/properties"], 30)
            if verify_code != 0:
                raise RuntimeError("Device verification query failed")
            self._log("SUCCESS", f"Device {action} requested and command completed")
            self._refresh_devices()
        except Exception as exc:
            self._log("ERROR", f"Device {action} failed: {exc}")

    def _refresh_services(self):
        if not hasattr(self, "service_list"):
            return
        self._all_services = enumerate_services()
        self._filter_services(self.service_filter.text() if hasattr(self, "service_filter") else "")

    def _filter_services(self, text: str):
        if not hasattr(self, "service_list"):
            return
        q = text.strip().lower()
        self.service_list.clear()
        for service in getattr(self, "_all_services", []):
            blob = " ".join(str(service.get(k, "")) for k in ("Name", "DisplayName", "State", "StartMode", "Description")).lower()
            if q and q not in blob:
                continue
            item = QListWidgetItem(f"{service.get('Name','')}  |  {service.get('State','')}  |  {service.get('StartMode','')}\n{service.get('DisplayName','')}")
            item.setData(Qt.UserRole, service)
            self.service_list.addItem(item)

    def _selected_service(self) -> dict[str, Any] | None:
        item = self.service_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _service_change(self, mode: str):
        service = self._selected_service()
        if not service:
            return
        name = str(service.get("Name", ""))
        protected = {"RpcSs", "DcomLaunch", "LSASS", "EventLog", "Schedule", "Winlogon", "MpsSvc", "BFE", "PlugPlay", "CryptSvc", "wuauserv", "WaaSMedicSvc", "UsoSvc"}
        if name in protected:
            QMessageBox.warning(self, "Protected service", "This service is protected by Luna's core-service safety boundary.")
            return
        if not is_admin():
            QMessageBox.warning(self, "Administrator required", "Service changes require elevation.")
            return
        if QMessageBox.question(self, "Service change", f"Change startup mode for {name} to {mode}?") != QMessageBox.Yes:
            return
        try:
            backup = self.engine.backup
            if not backup.current:
                backup.create_session(self.info.to_dict())
            backup.backup_service(name)
            ok, msg = set_service_start_mode(name, mode)
            if not ok:
                raise RuntimeError(msg)
            self._log("SUCCESS", f"Service {name} startup mode set to {mode}")
            self._refresh_services()
        except Exception as exc:
            self._log("ERROR", f"Service change failed: {exc}")

    def _refresh_backups(self):
        if not hasattr(self, "backup_list"):
            return
        sessions = self.backup_manager.list_sessions()
        self.backup_list.clear()
        self.backup_status.setText(f"Snapshots: {len(sessions)}")
        for session in sessions:
            applied = len(session.manifest.get("applied_tweaks", []))
            item = QListWidgetItem(f"{session.backup_id}\n{session.created_at} · {applied} tweak record(s)")
            self.backup_list.addItem(item)

    def _manual_backup(self):
        if not is_admin():
            QMessageBox.warning(self, "Administrator required", "Backup creation for system state requires Administrator privileges.")
            return
        try:
            session = self.backup_manager.create_session(self.info.to_dict())
            self._log("BACKUP", f"Manual backup created: {session.backup_id}")
            self._refresh_backups()
        except Exception as exc:
            self._log("ERROR", f"Backup creation failed: {exc}")

    def _restore_last(self):
        if not is_admin():
            QMessageBox.warning(self, "Administrator required", "Restore requires Administrator privileges.")
            return
        if QMessageBox.question(self, "Restore", "Restore the latest Luna granular session? This can modify registry, tasks, and service startup state.") != QMessageBox.Yes:
            return
        manager = BackupManager(self.paths.root)
        ok, messages = self.engine.restore_last_session()
        for line in messages:
            self._log("ROLLBACK" if ok else "ERROR", line)
        QMessageBox.information(self, "Restore result", "Restore completed." if ok else "Restore completed with failures; review the log.")
        self._refresh_all()

    def _open_path(self, path: Path):
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError:
            pass

    def _save_setting(self, key: str, value: Any):
        self.config[key] = value
        self.paths.save_config(self.config)

    def _toggle_startup(self, enabled: bool):
        self.config["start_with_windows"] = enabled
        self.paths.save_config(self.config)
        if os.name != "nt":
            return
        try:
            import winreg
            run_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    if getattr(sys, "frozen", False):
                        command = f'"{Path(sys.executable)}"'
                    else:
                        command = f'"{Path(sys.executable)}" "{Path(__file__).resolve().parents[1] / "main.py"}"'
                    winreg.SetValueEx(key, "LunaSystemCore", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "LunaSystemCore")
                    except FileNotFoundError:
                        pass
            self._log("INFO", f"Start with Windows {'enabled' if enabled else 'disabled'}")
        except OSError as exc:
            self._log("ERROR", f"Startup setting failed: {exc}")

    def _show_onboarding(self):
        dialog = OnboardingDialog(self.info, self)
        if dialog.exec() == QDialog.Accepted:
            self.config["onboarding_complete"] = True
            self.config["last_mode"] = dialog.selected_mode()
            self.paths.save_config(self.config)
            self._log("INFO", f"Onboarding complete; preferred mode={dialog.selected_mode()}")

    def _solar_hover(self, name: str, text: str):
        if text:
            self.solar.setToolTip(text)
        else:
            self.solar.setToolTip("")
