from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, Signal, QPropertyAnimation, Property
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


PALETTE = {
    "bg": "#05070B",
    "panel": "#091017",
    "panel2": "#0B141C",
    "border": "#20303A",
    "border_hot": "#2B4852",
    "text": "#E7EEF4",
    "muted": "#7F929E",
    "emerald": "#19F59A",
    "crimson": "#FF3B5F",
    "blue": "#35A7FF",
    "warning": "#FFCC66",
    "error": "#FF4D5E",
}


class GlassCard(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None, clickable: bool = False):
        super().__init__(parent)
        self.setObjectName("GlassCard")
        self.setFrameShape(QFrame.NoFrame)
        self._clickable = clickable
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(shadow)

    def mousePressEvent(self, event):
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Pill(QLabel):
    def __init__(self, text: str, accent: str = PALETTE["emerald"], parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(26)
        self.setStyleSheet(
            f"QLabel{{background:rgba(25,245,154,0.08);border:1px solid {accent}55;color:{accent};border-radius:13px;padding:2px 9px;font-weight:600;}}"
        )


class PrimaryButton(QPushButton):
    def __init__(self, text: str, accent: str = PALETTE["emerald"], parent: QWidget | None = None):
        super().__init__(text, parent)
        self.accent = accent
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setStyleSheet(
            f"QPushButton{{background:rgba(25,245,154,0.10);border:1px solid {accent}66;border-radius:10px;color:{PALETTE['text']};padding:0 16px;font-weight:600;}}"
            f"QPushButton:hover{{background:rgba(25,245,154,0.18);border-color:{accent};}}"
            f"QPushButton:pressed{{background:rgba(25,245,154,0.25);}}"
            f"QPushButton:disabled{{color:#53636D;border-color:#26323A;background:#0C1115;}}"
        )


class IconButton(QToolButton):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(34, 34)
        self.setStyleSheet(
            "QToolButton{background:transparent;color:#9EB0BA;border:1px solid transparent;border-radius:8px;}"
            "QToolButton:hover{background:#0D171E;border-color:#263A45;color:#E7EEF4;}"
        )


class CircularProgress(QWidget):
    def __init__(self, value: float = 0.0, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self._value = float(value)
        self._target = float(value)
        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.setDuration(320)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

    def get_progress(self) -> float:
        return self._value

    def set_progress(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def setValue(self, value: float) -> None:
        self._target = max(0.0, min(100.0, float(value)))
        self._animation.stop()
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(self._target)
        self._animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = side / 2 - 14
        pen = QPen(QColor("#132128"), 11, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2), 0, 360 * 16)
        pen.setColor(QColor(PALETTE["emerald"]))
        painter.setPen(pen)
        span = -int(self._value / 100.0 * 360 * 16)
        painter.drawArc(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2), 90 * 16, span)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(PALETTE["emerald"]))
        painter.drawEllipse(QRectF(center.x() - 3, center.y() - radius, 6, 6))
        painter.setPen(QColor(PALETTE["text"]))
        font = QFont("JetBrains Mono", 22, QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(QRectF(0, center.y() - 22, self.width(), 36), Qt.AlignCenter, f"{self._value:.0f}%")
        painter.setPen(QColor(PALETTE["muted"]))
        painter.setFont(QFont("JetBrains Mono", 9))
        painter.drawText(QRectF(0, center.y() + 14, self.width(), 26), Qt.AlignCenter, "OPTIMIZED")


@dataclass
class Star:
    x: float
    y: float
    radius: float
    speed: float
    alpha: int
    phase: float


class StarfieldWidget(QWidget):
    def __init__(self, parent: QWidget | None = None, star_count: int = 90):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        random.seed(20260927)
        self.stars = [
            Star(random.random(), random.random(), random.uniform(0.4, 1.6), random.uniform(0.00008, 0.00045), random.randint(70, 180), random.random() * math.tau)
            for _ in range(star_count)
        ]
        self._last = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.08, now - self._last)
        self._last = now
        for star in self.stars:
            star.y += star.speed * dt * 60
            star.phase += dt * 0.8
            if star.y > 1.03:
                star.y = -0.02
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PALETTE["bg"]))
        p.setRenderHint(QPainter.Antialiasing)
        for star in self.stars:
            twinkle = (math.sin(star.phase) + 1.0) * 0.5
            alpha = int(star.alpha * (0.65 + 0.35 * twinkle))
            color = QColor(210, 235, 245, alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            r = star.radius
            p.drawEllipse(QRectF(star.x * self.width() - r, star.y * self.height() - r, r * 2, r * 2))


class SolarSystemWidget(QWidget):
    planet_hovered = Signal(str, str)

    PLANETS = [
        ("Mercury", 14, 1.5, "~167°C", "Small rocky planet with extreme day/night temperature differences."),
        ("Venus", 25, 1.1, "~464°C", "Dense CO₂ atmosphere and runaway greenhouse effect."),
        ("Earth", 38, 0.85, "~15°C", "Liquid-water world with a nitrogen-rich atmosphere."),
        ("Mars", 51, 0.72, "~-63°C", "Cold, dusty rocky planet with a thin CO₂ atmosphere."),
    ]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(150, 150)
        self.setMaximumSize(190, 190)
        self.angle = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)
        self._hovered: str | None = None
        self.setMouseTracking(True)

    def _tick(self):
        self.angle = (self.angle + 0.35) % 360
        self.update()

    def planet_position(self, index: int) -> QPointF:
        cx, cy = self.width() / 2, self.height() / 2
        radius = self.PLANETS[index][1] * 1.25
        speed = self.PLANETS[index][2]
        angle = math.radians(self.angle * speed + index * 70)
        return QPointF(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius * 0.62)

    def mouseMoveEvent(self, event):
        hit = None
        for i, (name, orbit, size, *_rest) in enumerate(self.PLANETS):
            pos = self.planet_position(i)
            if (pos - event.position()).manhattanLength() <= 8:
                hit = name
                break
        if hit != self._hovered:
            self._hovered = hit
            if hit:
                data = next(p for p in self.PLANETS if p[0] == hit)
                self.planet_hovered.emit(hit, f"Planet: {data[0]}\nTemperature: {data[3]}\n\n{data[4]}")
            else:
                self.planet_hovered.emit("", "")
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        for name, orbit, size, *_ in self.PLANETS:
            r = orbit * 1.25
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawEllipse(QRectF(cx - r, cy - r * 0.62, r * 2, r * 1.24))
        sun_grad = QRadialGradient(QPointF(cx, cy), 15)
        sun_grad.setColorAt(0, QColor(255, 225, 125, 255))
        sun_grad.setColorAt(1, QColor(255, 130, 40, 35))
        p.setPen(Qt.NoPen)
        p.setBrush(sun_grad)
        p.drawEllipse(QRectF(cx - 12, cy - 12, 24, 24))
        colors = [QColor("#9EA3A7"), QColor("#D6A95A"), QColor("#4FA6FF"), QColor("#CC5F4D")]
        for i, (name, orbit, size, *_rest) in enumerate(self.PLANETS):
            pos = self.planet_position(i)
            radius = 2.7 + i * 0.7
            p.setBrush(colors[i])
            p.drawEllipse(QRectF(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2))


class ToggleSwitch(QPushButton):
    changed = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(48, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.toggled.connect(self.changed.emit)
        self._update_style()
        self.toggled.connect(lambda _: self._update_style())

    def _update_style(self):
        accent = PALETTE["emerald"] if self.isChecked() else "#26333A"
        self.setStyleSheet(
            f"QPushButton{{background:{accent};border:1px solid #38525E;border-radius:13px;}}"
            f"QPushButton::indicator{{width:20px;height:20px;}}"
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QColor(PALETTE["emerald"] if self.isChecked() else "#26333A")
        p.setPen(QPen(QColor("#38525E"), 1))
        p.setBrush(track)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 13, 13)
        knob_x = self.width() - 17 if self.isChecked() else 17
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#F0F5F7"))
        p.drawEllipse(QPointF(knob_x, self.height() / 2), 9, 9)


class LogConsole(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("LogConsole")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.label = QLabel("SESSION LOG")
        self.label.setStyleSheet("color:#7F929E;font-size:10px;font-weight:700;letter-spacing:1px;")
        self.text = QScrollArea()
        self.text.setWidgetResizable(True)
        self.text.setFrameShape(QFrame.NoFrame)
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QLabel()
        self.content.setWordWrap(True)
        self.content.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.content.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.content.setStyleSheet("background:transparent;color:#DDE6EC;padding:2px;font-family:'JetBrains Mono';font-size:11px;")
        self.text.setWidget(self.content)
        layout.addWidget(self.label)
        layout.addWidget(self.text, 1)
        self._lines: list[str] = []

    def add_line(self, line: str, level: str = "INFO") -> None:
        color = {
            "SUCCESS": PALETTE["emerald"],
            "INFO": "#DDE6EC",
            "DEBUG": "#647783",
            "WARNING": PALETTE["warning"],
            "ERROR": PALETTE["error"],
            "ACTION": PALETTE["blue"],
            "ROLLBACK": "#D18CFF",
            "SYSTEM": "#65D8E8",
            "BACKUP": "#A6B4C2",
            "VERIFY": PALETTE["emerald"],
            "SKIP": PALETTE["warning"],
        }.get(level, "#DDE6EC")
        timestamp = time.strftime("%H:%M:%S")
        self._lines.append(f"<span style='color:#5D6E77'>[{timestamp}]</span> <span style='color:{color}'>[{level}]</span> {line}")
        self._lines = self._lines[-600:]
        self.content.setText("<br>".join(self._lines))
        QTimer.singleShot(0, lambda: self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum()))


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size:26px;font-weight:700;color:#E7EEF4;")
        layout.addWidget(title_label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet("font-size:11px;color:#7F929E;")
            sub.setWordWrap(True)
            layout.addWidget(sub)


class MetricCard(GlassCard):
    def __init__(self, title: str, value: str, detail: str = "", accent: str = PALETTE["emerald"], parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(100)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 14, 15, 14)
        title_label = QLabel(title.upper())
        title_label.setStyleSheet("font-size:9px;font-weight:700;letter-spacing:1.2px;color:#71848F;")
        val = QLabel(value)
        val.setStyleSheet(f"font-size:22px;font-weight:700;color:{accent};")
        detail_label = QLabel(detail)
        detail_label.setStyleSheet("font-size:10px;color:#8396A0;")
        detail_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(val)
        layout.addWidget(detail_label)
        self.value_label = val
        self.detail_label = detail_label


class NavButton(QPushButton):
    def __init__(self, text: str, icon_text: str, parent: QWidget | None = None):
        super().__init__(f"  {icon_text}  {text}", parent)
        self.setCheckable(True)
        self.setMinimumHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton{text-align:left;background:transparent;border:1px solid transparent;color:#81939D;border-radius:9px;padding-left:10px;font-size:11px;}"
            "QPushButton:hover{background:#0B151C;color:#DCE7ED;}"
            f"QPushButton:checked{{background:rgba(25,245,154,0.10);border-color:#19F59A33;color:{PALETTE['emerald']};font-weight:600;}}"
        )


class ScrollPage(QScrollArea):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(24, 20, 28, 28)
        self.layout.setSpacing(14)
        self.layout.setAlignment(Qt.AlignTop)
        self.setWidget(self.container)
