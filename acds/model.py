"""Dominio: entidades, reglas puras y documento de configuración en memoria.

No importa nada de I/O (configparser, subprocess, pathlib, questionary).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

DEFAULT_PRACTICE = 20
DEFAULT_QUALIFY = 15
DEFAULT_RACE_LAPS = 10
DEFAULT_RACE_MINUTES = 30
DEFAULT_QUALIFY_OVERTIME = 120
DEFAULT_RACE_OVER_TIME = 180
DEFAULT_TIME_MINUTES = 14 * 60  # 14:00 (SUN_ANGLE=16, valor por defecto de Kunos)
DEFAULT_TIME_MULT = 1

ASSIST_MODES = (0, 1, 2)
MIN_DURATION = 1
MIN_OVERTIME = 0
MINUTES_PER_DAY = 24 * 60

SESSION_SECTIONS = ("PRACTICE", "QUALIFY", "RACE")  # orden de ejecución en el servidor
TIME_PATTERN = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")

DEFAULT_ABS_MODE = 1  # 0 = prohibido, 1 = solo coches que lo incluyen, 2 = todos
DEFAULT_TC_MODE = 1  # ídem ABS
DEFAULT_STABILITY = False
DEFAULT_AUTOCLUTCH = False


# ───────────────────────────── Reglas puras ─────────────────────────────

def minutes_to_sun_angle(minutes: int) -> int:
    return round(16 * (minutes - 13 * 60) / 60)


def sun_angle_to_minutes(angle: int) -> int:
    return round((13 * 60 + angle * 60 / 16) % MINUTES_PER_DAY)


def parse_time(text: str) -> int | None:
    """'HH:MM' (24 h) → minutos desde 00:00, o None si el formato no es válido."""
    match = TIME_PATTERN.fullmatch(text.strip())
    return int(match[1]) * 60 + int(match[2]) if match else None


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class Violation:
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


# ───────────────────────────── Entidades ─────────────────────────────

@dataclass(frozen=True)
class Track:
    name: str
    layouts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Skin:
    folder: str
    name: str = ""

    @property
    def label(self) -> str:
        return f"{self.folder} — {self.name}" if self.name and self.name != self.folder else self.folder


@dataclass(frozen=True)
class Slot:
    model: str
    skin: str = ""


@dataclass(frozen=True)
class Race:
    by_laps: bool = True
    value: int = DEFAULT_RACE_LAPS


@dataclass
class State:
    """Sesiones: None = omitida (su sección no se escribe en server_cfg.ini)."""

    track: str = ""
    layout: str = ""
    practice: int | None = DEFAULT_PRACTICE
    qualify: int | None = DEFAULT_QUALIFY
    race: Race | None = field(default_factory=Race)
    qualify_overtime: int = DEFAULT_QUALIFY_OVERTIME
    race_over_time: int = DEFAULT_RACE_OVER_TIME
    race_extra_lap: bool = False
    slots: list[Slot] = field(default_factory=list)
    time_minutes: int = DEFAULT_TIME_MINUTES  # minutos desde 00:00
    time_mult: int = DEFAULT_TIME_MULT
    abs_mode: int = DEFAULT_ABS_MODE
    tc_mode: int = DEFAULT_TC_MODE
    stability: bool = DEFAULT_STABILITY
    autoclutch: bool = DEFAULT_AUTOCLUTCH

    @property
    def runs_practice(self) -> bool:
        return self.practice is not None

    @property
    def runs_qualify(self) -> bool:
        return self.qualify is not None

    @property
    def runs_race(self) -> bool:
        return self.race is not None

    @property
    def runs_any_session(self) -> bool:
        return self.runs_practice or self.runs_qualify or self.runs_race

    @property
    def race_by_time(self) -> bool:
        return self.race is not None and not self.race.by_laps

    @property
    def effective_extra_lap(self) -> bool:
        """La vuelta extra solo aplica a carreras por tiempo."""
        return self.race_extra_lap and self.race_by_time


# ───────────────────── Documento de configuración (en memoria) ─────────────────────

class Document:
    """Secciones y claves ordenadas, sin formato de archivo ni I/O."""

    def __init__(self, data: dict[str, dict[str, str]] | None = None) -> None:
        self._data: dict[str, dict[str, str]] = {s: dict(v) for s, v in (data or {}).items()}

    def sections(self) -> list[str]:
        return list(self._data)

    def has_section(self, section: str) -> bool:
        return section in self._data

    def items(self, section: str) -> dict[str, str]:
        return dict(self._data.get(section, {}))

    def get(self, section: str, key: str, default: str = "") -> str:
        return self._data.get(section, {}).get(key, default).strip()

    def set(self, section: str, key: str, value: object) -> None:
        self._data.setdefault(section, {})[key] = str(value)

    def remove_section(self, section: str) -> None:
        self._data.pop(section, None)

    def reorder(self, first: Sequence[str]) -> None:
        order = [s for s in first if s in self._data] + [s for s in self._data if s not in first]
        self._data = {s: self._data[s] for s in order}

    def copy(self) -> "Document":
        return Document(self._data)


@dataclass
class Bundle:
    """Resultado de proyectar un State: los dos documentos que se escriben."""

    server: Document
    entries: Document
