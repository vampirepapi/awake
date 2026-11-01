"""Helpers for inspecting the stylesheet template (used by tests and tooling)."""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).with_name("awake.qss")


def template_tokens() -> set[str]:
    return set(re.findall(r"\{\{(\w+)\}\}", TEMPLATE.read_text(encoding="utf-8")))
