"""Design tokens and stylesheet generation.

A single QSS template (``awake.qss``) is rendered with one of two palettes, so
dark and light themes can never drift apart structurally. Custom-painted
widgets read colours from :func:`theme` at paint time and repaint when
``theme_manager().theme_changed`` fires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

from awake.paths import asset_path, cache_dir

_TEMPLATE = Path(__file__).with_name("awake.qss")

# Spacing scale (8px grid) and radii.
SPACE = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32}
RADIUS = {"control": 10, "main": 12, "card": 14}
ANIMATION_MS = 180

# Colours are #RRGGBB or #RRGGBBAA (alpha last, CSS-style).
DARK: dict[str, str] = {
    "bg": "#0C0C0F",
    "sidebar": "#101014",
    "surface": "#15151A",
    "surface_hover": "#1B1B21",
    "surface_alt": "#1D1D24",
    "border": "#24242C",
    "border_strong": "#33333D",
    "text": "#ECECF1",
    "text_secondary": "#A3A3AF",
    "text_muted": "#6C6C78",
    "accent": "#8B7CF6",
    "accent_hover": "#9A8DF8",
    "accent_pressed": "#7A6AEA",
    "accent_soft": "#8B7CF624",
    "accent_border": "#8B7CF673",
    "accent_text": "#FFFFFF",
    "success": "#5CC28A",
    "success_soft": "#5CC28A24",
    "warning": "#D9AE5B",
    "warning_soft": "#D9AE5B22",
    "danger": "#E5676B",
    "danger_soft": "#E5676B1F",
    "knob": "#FFFFFF",
    "shadow": "#00000066",
}

LIGHT: dict[str, str] = {
    "bg": "#F5F5F7",
    "sidebar": "#EEEEF1",
    "surface": "#FFFFFF",
    "surface_hover": "#F4F4F7",
    "surface_alt": "#F1F1F4",
    "border": "#E3E3E8",
    "border_strong": "#D0D0D8",
    "text": "#17171C",
    "text_secondary": "#55555F",
    "text_muted": "#8A8A95",
    "accent": "#6E5BE8",
    "accent_hover": "#7D6CEC",
    "accent_pressed": "#5F4CD8",
    "accent_soft": "#6E5BE81C",
    "accent_border": "#6E5BE866",
    "accent_text": "#FFFFFF",
    "success": "#2F9E63",
    "success_soft": "#2F9E631F",
    "warning": "#B7862A",
    "warning_soft": "#B7862A1F",
    "danger": "#D14348",
    "danger_soft": "#D143481A",
    "knob": "#FFFFFF",
    "shadow": "#0000001F",
}


def _parse(value: str) -> tuple[int, int, int, int]:
    hexstr = value.lstrip("#")
    r, g, b = (int(hexstr[i : i + 2], 16) for i in (0, 2, 4))
    a = int(hexstr[6:8], 16) if len(hexstr) == 8 else 255
    return r, g, b, a


@dataclass(frozen=True)
class Theme:
    name: str
    colors: dict[str, str]

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"

    def color(self, key: str, alpha: float | None = None) -> QColor:
        r, g, b, a = _parse(self.colors[key])
        color = QColor(r, g, b, a)
        if alpha is not None:
            color.setAlphaF(alpha)
        return color

    def css(self, key: str) -> str:
        r, g, b, a = _parse(self.colors[key])
        return f"rgba({r}, {g}, {b}, {a})" if a != 255 else f"#{r:02x}{g:02x}{b:02x}"

    def stylesheet(self) -> str:
        template = _TEMPLATE.read_text(encoding="utf-8")
        images = {"chevron_url": self._themed_svg("chevron-down", "text_muted")}

        def substitute(match: re.Match[str]) -> str:
            key = match.group(1)
            return images[key] if key in images else self.css(key)

        return re.sub(r"\{\{(\w+)\}\}", substitute, template)

    def _themed_svg(self, name: str, color_key: str) -> str:
        """Write a recoloured icon for QSS ``image:`` rules (QSS cannot tint SVGs)."""
        source = asset_path("icons", "lucide", f"{name}.svg").read_text(encoding="utf-8")
        r, g, b, _ = _parse(self.colors[color_key])
        target = cache_dir() / f"{name}-{self.name}.svg"
        content = source.replace("currentColor", f"#{r:02x}{g:02x}{b:02x}")
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")
        return target.as_posix()


DARK_THEME = Theme("dark", DARK)
LIGHT_THEME = Theme("light", LIGHT)


class ThemeManager(QObject):
    theme_changed = Signal(object)  # Theme

    def __init__(self) -> None:
        super().__init__()
        self._theme = DARK_THEME

    @property
    def current(self) -> Theme:
        return self._theme

    def set_dark(self, dark: bool) -> None:
        new = DARK_THEME if dark else LIGHT_THEME
        if new is self._theme:
            return
        self._theme = new
        self.theme_changed.emit(new)


_manager: ThemeManager | None = None


def theme_manager() -> ThemeManager:
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def theme() -> Theme:
    return theme_manager().current
