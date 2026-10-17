"""Negocio: contrato Feature, features implementadas y pipeline de proyección.

Cada feature declara qué claves posee (`owns`), cómo se carga desde el .ini
(`load`), cómo se valida (`validate`) y qué aporta al documento (`project`).
No importa infraestructura ni presentación.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Callable, Sequence

from .model import (
    DEFAULT_PRACTICE,
    DEFAULT_QUALIFY,
    DEFAULT_QUALIFY_OVERTIME,
    DEFAULT_RACE_LAPS,
    DEFAULT_RACE_OVER_TIME,
    DEFAULT_TIME_MULT,
    MIN_DURATION,
    MIN_OVERTIME,
    MINUTES_PER_DAY,
    SESSION_SECTIONS,
    ASSIST_MODES,
    DEFAULT_ABS_MODE,
    DEFAULT_AUTOCLUTCH,
    DEFAULT_STABILITY,
    DEFAULT_TC_MODE,
    DEFAULT_DAMAGE_MULTIPLIER,
    DEFAULT_FUEL_RATE,
    DEFAULT_TYRE_BLANKETS,
    DEFAULT_TYRE_WEAR_RATE,
    MAX_DAMAGE,
    MIN_RATE,
    CSP_PREFIX,
    DEFAULT_SLIPSTREAM,
    MAX_RATE,
    MAX_SLIPSTREAM,
    MAX_STABILITY,
    Bundle,
    Document,
    Race,
    Slot,
    State,
    Violation,
    minutes_to_sun_angle,
    sun_angle_to_minutes,
)

SERVER = "server"
ENTRIES = "entries"

WFX_TAIL = re.compile(r"((?:_(?:type|time|mult|start)=-?\d+)+)$")


class OwnershipError(RuntimeError):
    """Una clave tiene cero o más de un propietario."""


# ───────────────────────────── Utilidades ─────────────────────────────

def _num(doc: Document, section: str, key: str, default: int) -> int:
    raw = doc.get(section, key)
    return int(raw) if raw.isdigit() else default


def _signed(doc: Document, section: str, key: str, default: int) -> int:
    try:
        return int(doc.get(section, key))
    except ValueError:
        return default


def wfx_split(graphics: str) -> tuple[str, dict[str, str]]:
    match = WFX_TAIL.search(graphics)
    if not match:
        return graphics, {}
    params = dict(p.split("=", 1) for p in match.group(1).split("_") if p)
    return graphics[: match.start()], params


def today_unix() -> int:
    d = datetime.now(timezone.utc).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# ───────────────────────────── Contribuciones ─────────────────────────────

@dataclass(frozen=True)
class Contribution:
    """Aporte de una feature: asignar una clave o eliminar una sección."""

    file: str
    section: str
    key: str = "*"
    value: object = None
    drop: bool = False

    @classmethod
    def put(cls, file: str, section: str, key: str, value: object) -> "Contribution":
        return cls(file, section, key, value)

    @classmethod
    def remove(cls, file: str, section: str) -> "Contribution":
        return cls(file, section, "*", None, True)

    @classmethod
    def discard(cls, file: str, section: str, key: str) -> "Contribution":
        return cls(file, section, key, None, True)

    @property
    def address(self) -> str:
        return f"{self.file}:{self.section}:{self.key}"

    def apply(self, bundle: Bundle) -> None:
        doc = bundle.server if self.file == SERVER else bundle.entries
        if self.drop and self.key == "*":
            doc.remove_section(self.section)
        elif self.drop:
            doc.remove_key(self.section, self.key)
        else:
            doc.set(self.section, self.key, self.value)


# ───────────────────────────── Contrato ─────────────────────────────

class Feature(ABC):
    id: str = ""
    owns: tuple[str, ...] = ()  # patrones glob sobre "archivo:SECCIÓN:CLAVE"

    def load(self, state: State, server: Document, entries: Document) -> None:
        """Rellena su parte del State desde los .ini actuales."""

    def validate(self, state: State) -> list[Violation]:
        return []

    @abstractmethod
    def project(self, state: State, base: Document) -> list[Contribution]:
        """Aportes al documento. `base` es el server_cfg.ini actual (solo lectura)."""


# ───────────────────────────── Features ─────────────────────────────

class TrackFeature(Feature):
    id = "track"
    owns = ("server:SERVER:TRACK", "server:SERVER:CONFIG_TRACK")

    def load(self, state: State, server: Document, entries: Document) -> None:
        state.track = server.get("SERVER", "TRACK")
        state.layout = server.get("SERVER", "CONFIG_TRACK")

    def validate(self, state: State) -> list[Violation]:
        return [] if state.track else [Violation("pista", "Selecciona una pista")]

    def project(self, state: State, base: Document) -> list[Contribution]:
        return [
            Contribution.put(SERVER, "SERVER", "TRACK", state.track),
            Contribution.put(SERVER, "SERVER", "CONFIG_TRACK", state.layout),
        ]


class SessionsFeature(Feature):
    id = "sessions"
    owns = tuple(f"server:{sec}:*" for sec in SESSION_SECTIONS)

    DEFAULTS = {
        "PRACTICE": {"NAME": "Practice", "IS_OPEN": "1"},
        "QUALIFY": {"NAME": "Qualify", "IS_OPEN": "1"},
        "RACE": {"NAME": "Race", "WAIT_TIME": "60"},
    }

    def load(self, state: State, server: Document, entries: Document) -> None:
        if not any(server.has_section(s) for s in SESSION_SECTIONS):
            return

        def timed(section: str, default: int) -> int | None:
            return _num(server, section, "TIME", default) if server.has_section(section) else None

        state.practice = timed("PRACTICE", DEFAULT_PRACTICE)
        state.qualify = timed("QUALIFY", DEFAULT_QUALIFY)
        state.race = self._load_race(server)

    @staticmethod
    def _load_race(server: Document) -> Race | None:
        if not server.has_section("RACE"):
            return None
        minutes = _num(server, "RACE", "TIME", 0)
        if minutes > 0:
            return Race(False, minutes)
        return Race(True, _num(server, "RACE", "LAPS", 0) or DEFAULT_RACE_LAPS)

    def validate(self, state: State) -> list[Violation]:
        out: list[Violation] = []
        if not state.runs_any_session:
            out.append(Violation("sesiones", "Selecciona al menos una sesión"))
        for label, value in (("práctica", state.practice), ("qualify", state.qualify)):
            if value is not None and value < MIN_DURATION:
                out.append(Violation(label, f"La duración mínima es {MIN_DURATION} min"))
        if state.race is not None and state.race.value < MIN_DURATION:
            out.append(Violation("carrera", f"La duración mínima es {MIN_DURATION}"))
        return out

    def project(self, state: State, base: Document) -> list[Contribution]:
        race = state.race
        overrides: dict[str, dict[str, object] | None] = {
            "PRACTICE": {"TIME": state.practice} if state.practice is not None else None,
            "QUALIFY": {"TIME": state.qualify} if state.qualify is not None else None,
            "RACE": (
                # IS_OPEN=1 siempre: con 2 la ventana de entrada se cierra 20 s antes de la salida
                {"LAPS": race.value, "IS_OPEN": 1}
                if race and race.by_laps
                else {"TIME": race.value, "IS_OPEN": 1}
                if race
                else None
            ),
        }
        saved = {sec: base.items(sec) for sec in SESSION_SECTIONS}
        out = [Contribution.remove(SERVER, sec) for sec in SESSION_SECTIONS]
        for sec in SESSION_SECTIONS:
            override = overrides[sec]
            if override is None:
                continue
            values = {**self.DEFAULTS[sec], **saved[sec], **override}
            if sec == "RACE":
                # LAPS o TIME, nunca ambas
                values.pop("TIME" if race.by_laps else "LAPS", None)
            out += [Contribution.put(SERVER, sec, key, value) for key, value in values.items()]
        return out


class OvertimeFeature(Feature):
    id = "overtime"
    owns = (
        "server:SERVER:QUALIFY_MAX_WAIT_PERC",
        "server:SERVER:RACE_OVER_TIME",
        "server:SERVER:RACE_EXTRA_LAP",
    )

    def load(self, state: State, server: Document, entries: Document) -> None:
        state.qualify_overtime = _num(server, "SERVER", "QUALIFY_MAX_WAIT_PERC", DEFAULT_QUALIFY_OVERTIME)
        state.race_over_time = _num(server, "SERVER", "RACE_OVER_TIME", DEFAULT_RACE_OVER_TIME)
        state.race_extra_lap = server.get("SERVER", "RACE_EXTRA_LAP") == "1"

    def validate(self, state: State) -> list[Violation]:
        out: list[Violation] = []
        if state.qualify_overtime < MIN_OVERTIME:
            out.append(Violation("overtime de qualify", f"Mínimo {MIN_OVERTIME}"))
        if state.race_over_time < MIN_OVERTIME:
            out.append(Violation("tiempo de cierre de carrera", f"Mínimo {MIN_OVERTIME}"))
        return out

    def project(self, state: State, base: Document) -> list[Contribution]:
        return [
            Contribution.put(SERVER, "SERVER", "QUALIFY_MAX_WAIT_PERC", state.qualify_overtime),
            Contribution.put(SERVER, "SERVER", "RACE_OVER_TIME", state.race_over_time),
            Contribution.put(SERVER, "SERVER", "RACE_EXTRA_LAP", int(state.effective_extra_lap)),
        ]


class TimeFeature(Feature):
    """Hora y multiplicador. En desarrollo: todo el comportamiento de WeatherFX vive aquí.

    Es la única propietaria de GRAPHICS por ahora; cuando exista la feature de clima
    habrá que extraer un compositor para esa clave compartida.
    """

    id = "time_of_day"
    owns = (
        "server:SERVER:SUN_ANGLE",
        "server:SERVER:TIME_OF_DAY_MULT",
        "server:WEATHER_*:GRAPHICS",
    )

    def __init__(self, clock: Callable[[], int] = today_unix) -> None:
        self.clock = clock

    def load(self, state: State, server: Document, entries: Document) -> None:
        wfx: dict[str, str] = {}
        for sec in server.sections():
            if sec.startswith("WEATHER_"):
                _, wfx = wfx_split(server.get(sec, "GRAPHICS"))
                if "time" in wfx:
                    break

        if "time" in wfx:
            state.time_minutes = int(wfx["time"]) // 60 % MINUTES_PER_DAY
            state.time_mult = int(wfx.get("mult", DEFAULT_TIME_MULT))
        else:
            state.time_minutes = sun_angle_to_minutes(_signed(server, "SERVER", "SUN_ANGLE", 16))
            state.time_mult = _num(server, "SERVER", "TIME_OF_DAY_MULT", DEFAULT_TIME_MULT)

    def validate(self, state: State) -> list[Violation]:
        out: list[Violation] = []
        if not 0 <= state.time_minutes < MINUTES_PER_DAY:
            out.append(Violation("hora", "Debe estar entre 00:00 y 23:59"))
        if state.time_mult < 0:
            out.append(Violation("multiplicador de tiempo", "Mínimo 0"))
        return out

    def project(self, state: State, base: Document) -> list[Contribution]:
        weather = [sec for sec in base.sections() if sec.startswith("WEATHER_")]
        out = [
            Contribution.put(
                SERVER, "SERVER", "SUN_ANGLE", max(-80, min(80, minutes_to_sun_angle(state.time_minutes)))
            ),
            Contribution.put(SERVER, "SERVER", "TIME_OF_DAY_MULT", 1 if weather else state.time_mult),
        ]
        for sec in weather:
            graphics, params = wfx_split(base.get(sec, "GRAPHICS"))
            parts = [graphics]
            if "type" in params:
                parts.append(f"type={params['type']}")
            parts += [
                f"time={state.time_minutes * 60}",
                f"mult={state.time_mult}",
                f"start={params.get('start') or self.clock()}",
            ]
            out.append(Contribution.put(SERVER, sec, "GRAPHICS", "_".join(parts)))
        return out

class AssistsFeature(Feature):
    id = "assists"
    owns = (
        "server:SERVER:ABS_ALLOWED",
        "server:SERVER:TC_ALLOWED",
        "server:SERVER:STABILITY_ALLOWED",
        "server:SERVER:AUTOCLUTCH_ALLOWED",
    )

    def load(self, state: State, server: Document, entries: Document) -> None:
        abs_mode = _num(server, "SERVER", "ABS_ALLOWED", DEFAULT_ABS_MODE)
        tc_mode = _num(server, "SERVER", "TC_ALLOWED", DEFAULT_TC_MODE)
        state.abs_mode = abs_mode if abs_mode in ASSIST_MODES else DEFAULT_ABS_MODE
        state.tc_mode = tc_mode if tc_mode in ASSIST_MODES else DEFAULT_TC_MODE
        state.stability = min(_num(server, "SERVER", "STABILITY_ALLOWED", DEFAULT_STABILITY), MAX_STABILITY)
        state.autoclutch = self._flag(server, "AUTOCLUTCH_ALLOWED", DEFAULT_AUTOCLUTCH)

    @staticmethod
    def _flag(server: Document, key: str, default: bool) -> bool:
        raw = server.get("SERVER", key)
        return default if raw not in ("0", "1") else raw == "1"

    def validate(self, state: State) -> list[Violation]:
        out: list[Violation] = []
        if state.abs_mode not in ASSIST_MODES:
            out.append(Violation("ABS", f"Valores permitidos: {ASSIST_MODES}"))
        if state.tc_mode not in ASSIST_MODES:
            out.append(Violation("control de tracción", f"Valores permitidos: {ASSIST_MODES}"))
        if not 0 <= state.stability <= MAX_STABILITY:
            out.append(Violation("control de estabilidad", f"Debe estar entre 0 y {MAX_STABILITY}"))
        return out

    def project(self, state: State, base: Document) -> list[Contribution]:
        return [
            Contribution.put(SERVER, "SERVER", "ABS_ALLOWED", state.abs_mode),
            Contribution.put(SERVER, "SERVER", "TC_ALLOWED", state.tc_mode),
            Contribution.put(SERVER, "SERVER", "STABILITY_ALLOWED", state.stability),
            Contribution.put(SERVER, "SERVER", "AUTOCLUTCH_ALLOWED", int(state.autoclutch)),
        ]

class RealismFeature(Feature):
    """Realismo. El rebufo no es una clave de acServer: es una opción extra de CSP
    (`[EXTRA_RULES] SLIPSTREAM_MULT`), que el adaptador publica en el mensaje de bienvenida."""

    id = "realism"
    owns = (
        "server:SERVER:TYRE_WEAR_RATE",
        "server:SERVER:FUEL_RATE",
        "server:SERVER:DAMAGE_MULTIPLIER",
        "server:SERVER:TYRE_BLANKETS_ALLOWED",
        f"server:{CSP_PREFIX}EXTRA_RULES:SLIPSTREAM_MULT",
    )
    CSP_SECTION = f"{CSP_PREFIX}EXTRA_RULES"

    def load(self, state: State, server: Document, entries: Document) -> None:
        state.tyre_wear_rate = min(_num(server, "SERVER", "TYRE_WEAR_RATE", DEFAULT_TYRE_WEAR_RATE), MAX_RATE)
        state.fuel_rate = min(_num(server, "SERVER", "FUEL_RATE", DEFAULT_FUEL_RATE), MAX_RATE)
        damage = _num(server, "SERVER", "DAMAGE_MULTIPLIER", DEFAULT_DAMAGE_MULTIPLIER)
        state.damage_multiplier = damage if damage <= MAX_DAMAGE else DEFAULT_DAMAGE_MULTIPLIER
        raw = server.get("SERVER", "TYRE_BLANKETS_ALLOWED")
        state.tyre_blankets = DEFAULT_TYRE_BLANKETS if raw not in ("0", "1") else raw == "1"
        state.slipstream = self._load_slipstream(server)

    def _load_slipstream(self, server: Document) -> int:
        try:
            percent = round(float(server.get(self.CSP_SECTION, "SLIPSTREAM_MULT")) * 100)
        except (ValueError, OverflowError):
            return DEFAULT_SLIPSTREAM
        return min(max(percent, 0), MAX_SLIPSTREAM)

    def validate(self, state: State) -> list[Violation]:
        out: list[Violation] = []
        if not MIN_RATE <= state.tyre_wear_rate <= MAX_RATE:
            out.append(Violation("desgaste de neumáticos", f"Debe estar entre {MIN_RATE} y {MAX_RATE}"))
        if not MIN_RATE <= state.fuel_rate <= MAX_RATE:
            out.append(Violation("consumo de combustible", f"Debe estar entre {MIN_RATE} y {MAX_RATE}"))
        if not 0 <= state.damage_multiplier <= MAX_DAMAGE:
            out.append(Violation("daños", f"Debe estar entre 0 y {MAX_DAMAGE}"))
        if not 0 <= state.slipstream <= MAX_SLIPSTREAM:
            out.append(Violation("rebufo", f"Debe estar entre 0 y {MAX_SLIPSTREAM}"))
        return out

    def project(self, state: State, base: Document) -> list[Contribution]:
        out = [
            Contribution.put(SERVER, "SERVER", "TYRE_WEAR_RATE", state.tyre_wear_rate),
            Contribution.put(SERVER, "SERVER", "FUEL_RATE", state.fuel_rate),
            Contribution.put(SERVER, "SERVER", "DAMAGE_MULTIPLIER", state.damage_multiplier),
            Contribution.put(SERVER, "SERVER", "TYRE_BLANKETS_ALLOWED", int(state.tyre_blankets)),
        ]
        if state.slipstream != DEFAULT_SLIPSTREAM:
            out.append(
                Contribution.put(SERVER, self.CSP_SECTION, "SLIPSTREAM_MULT", f"{state.slipstream / 100:g}")
            )
        elif base.get(self.CSP_SECTION, "SLIPSTREAM_MULT"):
            out.append(Contribution.discard(SERVER, self.CSP_SECTION, "SLIPSTREAM_MULT"))
        return out


class SlotsFeature(Feature):
    id = "slots"
    owns = ("server:SERVER:CARS", "server:SERVER:MAX_CLIENTS", "entries:CAR_*:*")

    def load(self, state: State, server: Document, entries: Document) -> None:
        def slot_index(section: str) -> int:
            suffix = section.split("_", 1)[1]
            return int(suffix) if suffix.isdigit() else 0

        state.slots = [
            Slot(entries.get(sec, "MODEL"), entries.get(sec, "SKIN"))
            for sec in sorted(
                (s for s in entries.sections() if s.startswith("CAR_")),
                key=slot_index,
            )
            if entries.get(sec, "MODEL")
        ]

    def validate(self, state: State) -> list[Violation]:
        if not state.slots:
            return [Violation("slots", "Debe haber al menos un slot")]
        return [
            Violation(f"CAR_{i}", "Falta el modelo de coche")
            for i, slot in enumerate(state.slots)
            if not slot.model
        ]

    def project(self, state: State, base: Document) -> list[Contribution]:
        out = [
            Contribution.put(SERVER, "SERVER", "CARS", ";".join(dict.fromkeys(s.model for s in state.slots))),
            Contribution.put(SERVER, "SERVER", "MAX_CLIENTS", len(state.slots)),
        ]
        for i, slot in enumerate(state.slots):
            sec = f"CAR_{i}"
            for key, value in (
                ("MODEL", slot.model),
                ("SKIN", slot.skin),
                ("SPECTATOR_MODE", 0),
                ("DRIVERNAME", ""),
                ("TEAM", ""),
                ("GUID", ""),
                ("BALLAST", 0),
                ("RESTRICTOR", 0),
            ):
                out.append(Contribution.put(ENTRIES, sec, key, value))
        return out


# ───────────────────────────── Registro y pipeline ─────────────────────────────

def default_features(clock: Callable[[], int] = today_unix) -> list[Feature]:
    """Orden explícito de las features (coincide con el orden del asistente)."""
    return [
        TrackFeature(), SessionsFeature(), OvertimeFeature(), TimeFeature(clock),
        AssistsFeature(), RealismFeature(), SlotsFeature(),
    ]


def check_registry(features: Sequence[Feature]) -> None:
    ids = [f.id for f in features]
    if len(set(ids)) != len(ids):
        raise OwnershipError(f"Identificadores de feature duplicados: {ids}")
    seen: dict[str, str] = {}
    for feature in features:
        for pattern in feature.owns:
            if pattern in seen:
                raise OwnershipError(f"'{pattern}' lo reclaman '{seen[pattern]}' y '{feature.id}'")
            seen[pattern] = feature.id


def load_state(features: Sequence[Feature], server: Document, entries: Document) -> State:
    state = State()
    for feature in features:
        feature.load(state, server, entries)
    return state


def validate_state(features: Sequence[Feature], state: State) -> list[Violation]:
    return [v for feature in features for v in feature.validate(state)]


def _check_owner(features: Sequence[Feature], emitter: Feature, contribution: Contribution) -> None:
    claimants = [
        f.id for f in features if any(fnmatchcase(contribution.address, p) for p in f.owns)
    ]
    if claimants != [emitter.id]:
        raise OwnershipError(
            f"{contribution.address} la emite '{emitter.id}'; propietarios: {claimants or 'ninguno'}"
        )


def build(features: Sequence[Feature], state: State, base_server: Document) -> Bundle:
    """Proyecta el State sobre el server_cfg.ini actual; entry_list.ini se genera desde cero.

    Las claves sin propietario se conservan tal cual están en `base_server`.
    """
    bundle = Bundle(server=base_server.copy(), entries=Document())
    for feature in features:
        for contribution in feature.project(state, base_server):
            _check_owner(features, feature, contribution)
            contribution.apply(bundle)
    bundle.server.reorder(("SERVER", *SESSION_SECTIONS))
    return bundle
