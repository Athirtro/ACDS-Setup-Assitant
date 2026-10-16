# /// script
# requires-python = ">=3.11"
# dependencies = ["questionary>=2.0.1"]
# ///
from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import questionary
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
from datetime import datetime, timezone



ACDS_ROOT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa Dedicated Server")
ACDS_EXE = "acServer.exe"

DEFAULT_PRACTICE = 20
DEFAULT_QUALIFY = 15
DEFAULT_RACE_LAPS = 10
DEFAULT_RACE_MINUTES = 30
DEFAULT_QUALIFY_OVERTIME = 120
DEFAULT_RACE_OVER_TIME = 180
DEFAULT_TIME_MINUTES = 14 * 60  # 14:00 (SUN_ANGLE=16, valor por defecto de Kunos)
DEFAULT_TIME_MULT = 1
WFX_TAIL = re.compile(r"((?:_(?:type|time|mult|start)=-?\d+)+)$")


def minutes_to_sun_angle(minutes: int) -> int:
    return round(16 * (minutes - 13 * 60) / 60)


def sun_angle_to_minutes(angle: int) -> int:
    return round((13 * 60 + angle * 60 / 16) % (24 * 60))

def wfx_split(graphics: str) -> tuple[str, dict[str, str]]:
    match = WFX_TAIL.search(graphics)
    if not match:
        return graphics, {}
    params = dict(p.split("=", 1) for p in match.group(1).split("_") if p)
    return graphics[: match.start()], params

def today_unix() -> int:
    d = datetime.now(timezone.utc).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# ───────────────────────────── Dominio ─────────────────────────────

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


SESSION_SECTIONS = ("PRACTICE", "QUALIFY", "RACE")  # orden de ejecución en el servidor


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

    @classmethod
    def from_files(cls, server: "IniFile", entries: "IniFile") -> "State":
        def num(section: str, key: str, default: int) -> int:
            raw = server.get(section, key)
            return int(raw) if raw.isdigit() else default

        def signed(section: str, key: str, default: int) -> int:
            try:
                return int(server.get(section, key))
            except ValueError:
                return default

        def slot_index(section: str) -> int:
            suffix = section.split("_", 1)[1]
            return int(suffix) if suffix.isdigit() else 0

        def timed(section: str, default: int) -> int | None:
            return num(section, "TIME", default) if server.has_section(section) else None

        def race() -> Race | None:
            if not server.has_section("RACE"):
                return None
            minutes = num("RACE", "TIME", 0)
            if minutes > 0:
                return Race(False, minutes)
            return Race(True, num("RACE", "LAPS", 0) or DEFAULT_RACE_LAPS)

        has_sessions = any(server.has_section(s) for s in SESSION_SECTIONS)

        slots = [
            Slot(entries.get(sec, "MODEL"), entries.get(sec, "SKIN"))
            for sec in sorted(
                (s for s in entries.sections() if s.startswith("CAR_")),
                key=slot_index,
            )
            if entries.get(sec, "MODEL")
        ]
        
        sessions = (
            {
                "practice": timed("PRACTICE", DEFAULT_PRACTICE),
                "qualify": timed("QUALIFY", DEFAULT_QUALIFY),
                "race": race(),
            }
            if has_sessions
            else {}
        )

        wfx: dict[str, str] = {}
        
        for sec in server.sections():
            if sec.startswith("WEATHER_"):
                _, wfx = wfx_split(server.get(sec, "GRAPHICS"))
                if "time" in wfx:
                    break
                    
        if "time" in wfx:
            time_minutes = int(wfx["time"]) // 60 % (24 * 60)
            time_mult = int(wfx.get("mult", DEFAULT_TIME_MULT))
        else:
            time_minutes = sun_angle_to_minutes(signed("SERVER", "SUN_ANGLE", 16))
            time_mult = num("SERVER", "TIME_OF_DAY_MULT", DEFAULT_TIME_MULT)
            
        return cls(
            track=server.get("SERVER", "TRACK"),
            layout=server.get("SERVER", "CONFIG_TRACK"),
            slots=slots,
            qualify_overtime=num("SERVER", "QUALIFY_MAX_WAIT_PERC", DEFAULT_QUALIFY_OVERTIME),
            race_over_time=num("SERVER", "RACE_OVER_TIME", DEFAULT_RACE_OVER_TIME),
            race_extra_lap=server.get("SERVER", "RACE_EXTRA_LAP") == "1",
            time_minutes=time_minutes,
            time_mult=time_mult,
            **sessions,
        )


# ─────────────────────── Acceso a datos (Repository) ───────────────────────

class ContentScanner:
    def __init__(self, content: Path) -> None:
        self.tracks_dir = content / "tracks"
        self.cars_dir = content / "cars"

    @staticmethod
    def _dirs(path: Path) -> list[Path]:
        if not path.is_dir():
            return []
        return sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: p.name)

    def tracks(self) -> list[Track]:
        found: list[Track] = []
        for p in self._dirs(self.tracks_dir):
            layouts = tuple(d.name for d in self._dirs(p) if (d / "data").is_dir())
            if layouts or (p / "data").is_dir():
                found.append(Track(p.name, layouts))
        return found

    def cars(self) -> list[str]:
        return [
            p.name
            for p in self._dirs(self.cars_dir)
            if (p / "data.acd").is_file() or (p / "data").is_dir()
        ]

    def skins(self, model: str) -> list[Skin]:
        return [
            Skin(d.name, self._skin_name(d / "ui_skin.json"))
            for d in self._dirs(self.cars_dir / model / "skins")
        ]

    @staticmethod
    def _skin_name(ui_file: Path) -> str:
        try:
            data = json.loads(ui_file.read_text(encoding="utf-8-sig", errors="replace"))
            return str(data.get("skinname", "")).strip()
        except (OSError, ValueError, AttributeError):
            return ""


class IniFile:
    def __init__(self, path: Path, fresh: bool = False) -> None:
        self.path = path
        self._cp = configparser.ConfigParser(interpolation=None, strict=False)
        self._cp.optionxform = str  # type: ignore[assignment]
        if path.exists() and not fresh:
            self._cp.read(path, encoding="utf-8-sig")

    def sections(self) -> list[str]:
        return self._cp.sections()

    def has_section(self, section: str) -> bool:
        return self._cp.has_section(section)

    def items(self, section: str) -> dict[str, str]:
        return dict(self._cp.items(section)) if self._cp.has_section(section) else {}

    def remove_section(self, section: str) -> None:
        self._cp.remove_section(section)

    def get(self, section: str, key: str, default: str = "") -> str:
        return self._cp.get(section, key, fallback=default).strip()

    def set(self, section: str, key: str, value: object) -> None:
        if not self._cp.has_section(section):
            self._cp.add_section(section)
        self._cp.set(section, key, str(value))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        with self.path.open("w", encoding="utf-8", newline="\r\n") as fh:
            self._cp.write(fh, space_around_delimiters=False)

    def reorder(self, first: Sequence[str]) -> None:
        data = {s: dict(self._cp.items(s)) for s in self._cp.sections()}
        order = [s for s in first if s in data] + [s for s in data if s not in first]
        for s in data:
            self._cp.remove_section(s)
        for s in order:
            self._cp.add_section(s)
            for key, value in data[s].items():
                self._cp.set(s, key, value)


# ───────────────────────── Escritura (Adapter) ─────────────────────────

class ConfigWriter:
    def __init__(self, cfg_dir: Path) -> None:
        self.cfg_dir = cfg_dir

    def apply(self, s: State) -> None:
        server = IniFile(self.cfg_dir / "server_cfg.ini")
        server.set("SERVER", "TRACK", s.track)
        server.set("SERVER", "CONFIG_TRACK", s.layout)
        server.set("SERVER", "CARS", ";".join(dict.fromkeys(slot.model for slot in s.slots)))
        server.set("SERVER", "MAX_CLIENTS", len(s.slots))
        server.set("SERVER", "QUALIFY_MAX_WAIT_PERC", s.qualify_overtime)
        server.set("SERVER", "RACE_OVER_TIME", s.race_over_time)
        server.set(
            "SERVER", "RACE_EXTRA_LAP", int(s.race_extra_lap and s.race is not None and not s.race.by_laps)
        )
        weather = [sec for sec in server.sections() if sec.startswith("WEATHER_")]
        server.set("SERVER", "SUN_ANGLE", max(-80, min(80, minutes_to_sun_angle(s.time_minutes))))
        server.set("SERVER", "TIME_OF_DAY_MULT", 1 if weather else s.time_mult)
        self._apply_weather_time(server, s, weather)
        server.save()

        entries = IniFile(self.cfg_dir / "entry_list.ini", fresh=True)
        for i, slot in enumerate(s.slots):
            sec = f"CAR_{i}"
            entries.set(sec, "MODEL", slot.model)
            entries.set(sec, "SKIN", slot.skin)
            entries.set(sec, "SPECTATOR_MODE", 0)
            entries.set(sec, "DRIVERNAME", "")
            entries.set(sec, "TEAM", "")
            entries.set(sec, "GUID", "")
            entries.set(sec, "BALLAST", 0)
            entries.set(sec, "RESTRICTOR", 0)
        entries.save()

    @staticmethod
    def _apply_sessions(server: IniFile, s: State) -> None:
        defaults = {
            "PRACTICE": {"NAME": "Practice", "IS_OPEN": "1"},
            "QUALIFY": {"NAME": "Qualify", "IS_OPEN": "1"},
            "RACE": {"NAME": "Race", "WAIT_TIME": "60"},
        }
        overrides: dict[str, dict[str, object] | None] = {
            "PRACTICE": {"TIME": s.practice} if s.practice is not None else None,
            "QUALIFY": {"TIME": s.qualify} if s.qualify is not None else None,
            "RACE": (
                # IS_OPEN=1 siempre: con 2 la ventana de entrada se cierra 20 s antes de la salida
                {"LAPS": s.race.value, "IS_OPEN": 1}
                if s.race and s.race.by_laps
                else {"TIME": s.race.value, "IS_OPEN": 1}
                if s.race
                else None
            ),
        }
        saved = {sec: server.items(sec) for sec in SESSION_SECTIONS}
        for sec in SESSION_SECTIONS:
            server.remove_section(sec)
        for sec in SESSION_SECTIONS:
            override = overrides[sec]
            if override is None:
                continue
            values = {**defaults[sec], **saved[sec], **override}
            if sec == "RACE":
                values.pop("TIME" if s.race.by_laps else "LAPS", None)
            for key, value in values.items():
                server.set(sec, key, value)
        server.reorder(("SERVER", *SESSION_SECTIONS))

    @staticmethod
    def _apply_weather_time(server: IniFile, s: State, weather: Sequence[str]) -> None:
        for sec in weather:
            base, params = wfx_split(server.get(sec, "GRAPHICS"))
            parts = [base]
            if "type" in params:
                parts.append(f"type={params['type']}")
            parts += [
                f"time={s.time_minutes * 60}",
                f"mult={s.time_mult}",
                f"start={params.get('start') or today_unix()}",
            ]
            server.set(sec, "GRAPHICS", "_".join(parts))


# ───────────────────────────── Proceso ACDS ─────────────────────────────

class ServerProcess:
    def __init__(self, root: Path, exe: str = ACDS_EXE, enabled: bool = True) -> None:
        self.root = root
        self.exe_path = root / exe
        self.enabled = enabled

    def _pids(self) -> list[int]:
        target = str(self.exe_path).replace("'", "''")
        script = (
            f"Get-Process -Name '{self.exe_path.stem}' -ErrorAction SilentlyContinue "
            f"| Where-Object {{ $_.Path -eq '{target}' }} "
            f"| ForEach-Object {{ $_.Id }}"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
        )
        return [int(line) for line in result.stdout.split() if line.isdigit()]

    def is_active(self) -> bool:
        return bool(self._pids())

    def stop(self) -> None:
        if not self.enabled:
            return
        pids = self._pids()
        if not pids:
            return
        print(f"Deteniendo {self.exe_path.name}...")
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)

    def start(self) -> None:
        if not self.enabled:
            return
        if not self.exe_path.is_file():
            print(f"Aviso: no se encontró {self.exe_path}")
            return
        print(f"Iniciando {self.exe_path.name}...")
        subprocess.Popen(
            [str(self.exe_path)],
            cwd=self.root,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )


# ─────────────────────────── Interacción ───────────────────────────

class Cancelled(Exception):
    pass


Option = tuple[str, str]  # (texto visible, valor)


def ask(question: questionary.Question):
    answer = question.ask()
    if answer is None:
        raise Cancelled
    return answer


def ask_int(message: str, default: int, minimum: int = 1) -> int:
    return int(
        ask(
            questionary.text(
                message,
                default=str(default),
                validate=lambda v: (v.isdigit() and int(v) >= minimum) or f"Entero >= {minimum}",
            )
        )
    )


def ask_select(message: str, options: Sequence[Option], default: str | None = None) -> str:
    values = [value for _, value in options]
    return ask(
        questionary.select(
            message,
            choices=[questionary.Choice(title=title, value=value) for title, value in options],
            default=default if default in values else None,
            use_search_filter=True,
            use_jk_keys=False,
        )
    )


def ask_choice(message: str, choices: Sequence[str], default: str | None = None) -> str:
    return ask_select(message, [(c, c) for c in choices], default)


@dataclass
class Context:
    scanner: ContentScanner
    state: State


class Step(ABC):
    @abstractmethod
    def run(self, ctx: Context) -> None: ...


class TrackStep(Step):
    def run(self, ctx: Context) -> None:
        tracks = {t.name: t for t in ctx.scanner.tracks()}
        if not tracks:
            raise SystemExit("No hay pistas válidas en content\\tracks")
        name = ask_choice("Pista", list(tracks), ctx.state.track)
        track = tracks[name]
        ctx.state.track = name
        ctx.state.layout = (
            ask_choice("Layout", track.layouts, ctx.state.layout) if track.layouts else ""
        )


class SessionsStep(Step):
    PRACTICE = "Práctica"
    QUALIFY = "Qualify"
    RACE = "Carrera"
    BY_LAPS = "Por vueltas"
    BY_TIME = "Por tiempo"

    def run(self, ctx: Context) -> None:
        s = ctx.state
        chosen = ask(
            questionary.checkbox(
                "Sesiones a ejecutar (espacio para marcar/desmarcar)",
                choices=[
                    questionary.Choice(self.PRACTICE, checked=s.practice is not None),
                    questionary.Choice(self.QUALIFY, checked=s.qualify is not None),
                    questionary.Choice(self.RACE, checked=s.race is not None),
                ],
                validate=lambda v: bool(v) or "Selecciona al menos una sesión",
            )
        )

        s.practice = (
            ask_int("Duración de práctica (minutos)", s.practice or DEFAULT_PRACTICE)
            if self.PRACTICE in chosen
            else None
        )
        s.qualify = (
            ask_int("Duración de qualify (minutos)", s.qualify or DEFAULT_QUALIFY)
            if self.QUALIFY in chosen
            else None
        )
        s.race = self._race(s.race) if self.RACE in chosen else None

    def _race(self, previous: Race | None) -> Race:
        by_laps = previous.by_laps if previous else True
        mode = ask_choice(
            "Modo de carrera", [self.BY_LAPS, self.BY_TIME], self.BY_LAPS if by_laps else self.BY_TIME
        )
        by_laps = mode == self.BY_LAPS
        if by_laps:
            default = previous.value if previous and previous.by_laps else DEFAULT_RACE_LAPS
            return Race(True, ask_int("Duración de carrera (vueltas)", default))
        default = previous.value if previous and not previous.by_laps else DEFAULT_RACE_MINUTES
        return Race(False, ask_int("Duración de carrera (minutos)", default))


class OvertimeStep(Step):
    def run(self, ctx: Context) -> None:
        s = ctx.state
        if s.qualify is not None:
            s.qualify_overtime = ask_int(
                "Overtime de qualify (% de la mejor vuelta para terminar la vuelta en curso)",
                s.qualify_overtime,
                minimum=0,
            )
        if s.race is not None:
            s.race_over_time = ask_int(
                "Tiempo para terminar la carrera tras cruzar el líder (segundos)",
                s.race_over_time,
                minimum=0,
            )
            s.race_extra_lap = (
                ask(questionary.confirm("¿Vuelta extra al acabar el tiempo?", default=s.race_extra_lap))
                if not s.race.by_laps
                else False
            )


class TimeStep(Step):
    PATTERN = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")

    def run(self, ctx: Context) -> None:
        s = ctx.state
        default = f"{s.time_minutes // 60:02d}:{s.time_minutes % 60:02d}"
        raw = ask(
            questionary.text(
                "Hora de inicio (HH:MM, formato 24 h)",
                default=default,
                validate=lambda v: bool(self.PATTERN.fullmatch(v.strip())) or "Formato HH:MM, de 00:00 a 23:59",
            )
        )
        match = self.PATTERN.fullmatch(raw.strip())
        s.time_minutes = int(match[1]) * 60 + int(match[2])
        s.time_mult = ask_int(
            "Multiplicador de tiempo (0 = congelado, 1 = tiempo real, 2 = el doble...)",
            s.time_mult,
            minimum=0,
        )


class SlotsStep(Step):
    SAME_CAR = "Mismo coche para todos"
    EACH_CAR = "Un coche por slot"
    SAME_SKIN = "La misma livery para todos"
    EACH_SKIN = "Una livery por slot"
    FREE = "__free__"
    FREE_LABEL = "(libre: la elige el jugador)"

    def run(self, ctx: Context) -> None:
        cars = ctx.scanner.cars()
        if not cars:
            raise SystemExit("No hay coches válidos en content\\cars")

        previous = list(ctx.state.slots)
        count = ask_int("Número de coches (slots)", len(previous) or 2)
        mode = ask_choice("Asignación de coches", [self.SAME_CAR, self.EACH_CAR], self.SAME_CAR)

        if mode == self.SAME_CAR:
            model = ask_choice(
                "Coche para todos los slots", cars, previous[0].model if previous else None
            )
            if len(ctx.scanner.skins(model)) > 1 and count > 1:
                skin_mode = ask_choice(
                    "Asignación de liveries", [self.SAME_SKIN, self.EACH_SKIN], self.SAME_SKIN
                )
            else:
                skin_mode = self.SAME_SKIN

            if skin_mode == self.SAME_SKIN:
                skin = self._pick_skin(ctx, model, "de todos los slots", self._prev(previous, 0, model))
                ctx.state.slots = [Slot(model, skin)] * count
            else:
                ctx.state.slots = [
                    Slot(model, self._pick_skin(ctx, model, f"del slot CAR_{i}", self._prev(previous, i, model)))
                    for i in range(count)
                ]
            return

        slots: list[Slot] = []
        for i in range(count):
            default_model = previous[i].model if i < len(previous) else None
            model = ask_choice(f"Coche del slot CAR_{i}", cars, default_model)
            skin = self._pick_skin(ctx, model, f"del slot CAR_{i}", self._prev(previous, i, model))
            slots.append(Slot(model, skin))
        ctx.state.slots = slots

    @staticmethod
    def _prev(previous: Sequence[Slot], index: int, model: str) -> str:
        if index < len(previous) and previous[index].model == model:
            return previous[index].skin
        return ""

    def _pick_skin(self, ctx: Context, model: str, label: str, default: str) -> str:
        skins = ctx.scanner.skins(model)
        if not skins:
            return ""
        options: list[Option] = [(self.FREE_LABEL, self.FREE)] + [(s.label, s.folder) for s in skins]
        value = ask_select(f"Livery {label} ({model})", options, default or self.FREE)
        return "" if value == self.FREE else value


class ConfirmStep(Step):
    def run(self, ctx: Context) -> None:
        s = ctx.state
        print(
            f"\nPista:     {s.track}{'/' + s.layout if s.layout else ''}"
            f"\nPráctica:  {f'{s.practice} min' if s.practice is not None else 'omitida'}"
            f"\nQualify:   {f'{s.qualify} min' if s.qualify is not None else 'omitida'}"
            f"\nCarrera:   {self._race_label(s.race)}"
            f"\nOvertime:  qualify {s.qualify_overtime}% · carrera +{s.race_over_time}s"
            f"{' · vuelta extra' if s.race_extra_lap else ''}"
            f"\nSlots:     {len(s.slots)}"
            f"\nHora:      {s.time_minutes // 60:02d}:{s.time_minutes % 60:02d} (x{s.time_mult})"
        )
        for i, slot in enumerate(s.slots):
            print(f"  CAR_{i}: {slot.model} · {slot.skin or 'livery libre'}")
        if not ask(questionary.confirm("¿Guardar cambios?", default=True)):
            raise Cancelled

    @staticmethod
    def _race_label(race: Race | None) -> str:
        if race is None:
            return "omitida"
        return f"{race.value} vueltas" if race.by_laps else f"{race.value} min"


class Wizard:
    def __init__(self, steps: Sequence[Step]) -> None:
        self.steps = steps

    def run(self, ctx: Context) -> None:
        for step in self.steps:
            step.run(ctx)


# ───────────────────────────── Entrada ─────────────────────────────

def main() -> int:
    if os.name != "nt":
        print("Este script está diseñado para ejecutarse en Windows.")
        return 2

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ACDS_ROOT, help="Carpeta raíz del ACDS")
    parser.add_argument("--exe", default=ACDS_EXE, help="Nombre del ejecutable del servidor")
    parser.add_argument("--no-service", action="store_true", help="No detener ni iniciar el servidor")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    content = root / "content"
    cfg_dir = root / "cfg"
    if not content.is_dir():
        print(f"No existe {content}")
        return 2

    state = State.from_files(
        IniFile(cfg_dir / "server_cfg.ini"),
        IniFile(cfg_dir / "entry_list.ini"),
    )
    ctx = Context(ContentScanner(content), state)

    wizard = Wizard(
        [TrackStep(), SessionsStep(), OvertimeStep(), TimeStep(), SlotsStep(), ConfirmStep()]
    )
    try:
        wizard.run(ctx)
    except Cancelled:
        print("Cancelado, sin cambios.")
        return 1

    server = ServerProcess(root, args.exe, enabled=not args.no_service)
    server.stop()
    try:
        ConfigWriter(cfg_dir).apply(ctx.state)
        print("Configuración guardada.")
    except PermissionError as exc:
        print(f"Sin permisos para escribir en {cfg_dir} ({exc}). Ejecuta la terminal como administrador.")
        return 3
    finally:
        server.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
