# /// script
# requires-python = ">=3.11"
# dependencies = ["questionary>=2.0.1"]
# ///
from __future__ import annotations

import argparse
import configparser
import json
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import questionary

ACDS_ROOT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa Dedicated Server")
ACDS_EXE = "acServer.exe"


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


@dataclass
class State:
    track: str = ""
    layout: str = ""
    practice: int = 20
    qualify: int = 10
    race_laps: int = 10
    slots: list[Slot] = field(default_factory=list)

    @classmethod
    def from_files(cls, server: "IniFile", entries: "IniFile") -> "State":
        def num(section: str, key: str, default: int) -> int:
            raw = server.get(section, key)
            return int(raw) if raw.isdigit() else default

        def slot_index(section: str) -> int:
            suffix = section.split("_", 1)[1]
            return int(suffix) if suffix.isdigit() else 0

        slots = [
            Slot(entries.get(sec, "MODEL"), entries.get(sec, "SKIN"))
            for sec in sorted(
                (s for s in entries.sections() if s.startswith("CAR_")),
                key=slot_index,
            )
            if entries.get(sec, "MODEL")
        ]
        return cls(
            track=server.get("SERVER", "TRACK"),
            layout=server.get("SERVER", "CONFIG_TRACK"),
            practice=num("PRACTICE", "TIME", 20),
            qualify=num("QUALIFY", "TIME", 10),
            race_laps=num("RACE", "LAPS", 10),
            slots=slots,
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
        server.set("PRACTICE", "TIME", s.practice)
        server.set("QUALIFY", "TIME", s.qualify)
        server.set("RACE", "LAPS", s.race_laps)
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


class DurationStep(Step):
    def run(self, ctx: Context) -> None:
        s = ctx.state
        s.practice = ask_int("Duración de práctica (minutos)", s.practice)
        s.qualify = ask_int("Duración de qualify (minutos)", s.qualify)
        s.race_laps = ask_int("Duración de carrera (vueltas)", s.race_laps)


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
            f"\nPráctica:  {s.practice} min"
            f"\nQualify:   {s.qualify} min"
            f"\nCarrera:   {s.race_laps} vueltas"
            f"\nSlots:     {len(s.slots)}"
        )
        for i, slot in enumerate(s.slots):
            print(f"  CAR_{i}: {slot.model} · {slot.skin or 'livery libre'}")
        if not ask(questionary.confirm("¿Guardar cambios?", default=True)):
            raise Cancelled


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

    wizard = Wizard([TrackStep(), DurationStep(), SlotsStep(), ConfirmStep()])
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
