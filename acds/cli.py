"""Presentación: asistente interactivo. Solo pregunta, asigna al State y muestra resultados.

No conoce claves ni secciones del .ini, ni importa infraestructura.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

import questionary

from .model import (
    DEFAULT_PRACTICE,
    DEFAULT_QUALIFY,
    DEFAULT_RACE_LAPS,
    DEFAULT_RACE_MINUTES,
    MIN_OVERTIME,
    Race,
    Slot,
    State,
    format_time,
    parse_time,
)
from .service import ContentCatalog, Result


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

def assists_label(s: State) -> str:
    return (
        f"ABS={s.abs_mode} · TC={s.tc_mode} · "
        f"estabilidad={'sí' if s.stability else 'no'} · embrague auto={'sí' if s.autoclutch else 'no'}"
    )


def ask_choice(message: str, choices: Sequence[str], default: str | None = None) -> str:
    return ask_select(message, [(c, c) for c in choices], default)


@dataclass
class Context:
    catalog: ContentCatalog
    state: State


class Step(ABC):
    @abstractmethod
    def run(self, ctx: Context) -> None: ...


class TrackStep(Step):
    def run(self, ctx: Context) -> None:
        tracks = {t.name: t for t in ctx.catalog.tracks()}
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
                    questionary.Choice(self.PRACTICE, checked=s.runs_practice),
                    questionary.Choice(self.QUALIFY, checked=s.runs_qualify),
                    questionary.Choice(self.RACE, checked=s.runs_race),
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
        if s.runs_qualify:
            s.qualify_overtime = ask_int(
                "Overtime de qualify (% de la mejor vuelta para terminar la vuelta en curso)",
                s.qualify_overtime,
                minimum=MIN_OVERTIME,
            )
        if s.runs_race:
            s.race_over_time = ask_int(
                "Tiempo para terminar la carrera tras cruzar el líder (segundos)",
                s.race_over_time,
                minimum=MIN_OVERTIME,
            )
            s.race_extra_lap = (
                ask(questionary.confirm("¿Vuelta extra al acabar el tiempo?", default=s.race_extra_lap))
                if s.race_by_time
                else False
            )


class TimeStep(Step):
    def run(self, ctx: Context) -> None:
        s = ctx.state
        raw = ask(
            questionary.text(
                "Hora de inicio (HH:MM, formato 24 h)",
                default=format_time(s.time_minutes),
                validate=lambda v: parse_time(v) is not None or "Formato HH:MM, de 00:00 a 23:59",
            )
        )
        s.time_minutes = parse_time(raw) or 0
        s.time_mult = ask_int(
            "Multiplicador de tiempo (0 = congelado, 1 = tiempo real, 2 = el doble...)",
            s.time_mult,
            minimum=0,
        )

class AssistsStep(Step):
    """Opcional: si no se configura, se conservan los valores actuales."""

    LEVELS = (
        ("0", "Prohibido"),
        ("1", "Solo coches que lo incluyen"),
        ("2", "Permitido en todos los coches"),
    )

    def run(self, ctx: Context) -> None:
        s = ctx.state
        if not ask(questionary.confirm(f"¿Configurar ayudas? (actual: {assists_label(s)})", default=False)):
            return
        s.abs_mode = self._level("ABS", s.abs_mode)
        s.tc_mode = self._level("Control de tracción", s.tc_mode)
        s.stability = ask(questionary.confirm("¿Permitir control de estabilidad?", default=s.stability))
        s.autoclutch = ask(questionary.confirm("¿Permitir embrague automático?", default=s.autoclutch))

    def _level(self, name: str, current: int) -> int:
        options = [(f"{value} — {label}", value) for value, label in self.LEVELS]
        return int(ask_select(name, options, str(current)))

class SlotsStep(Step):
    SAME_CAR = "Mismo coche para todos"
    EACH_CAR = "Un coche por slot"
    SAME_SKIN = "La misma livery para todos"
    EACH_SKIN = "Una livery por slot"
    FREE = "__free__"
    FREE_LABEL = "(libre: la elige el jugador)"

    def run(self, ctx: Context) -> None:
        cars = ctx.catalog.cars()
        if not cars:
            raise SystemExit("No hay coches válidos en content\\cars")

        previous = list(ctx.state.slots)
        count = ask_int("Número de coches (slots)", len(previous) or 2)
        mode = ask_choice("Asignación de coches", [self.SAME_CAR, self.EACH_CAR], self.SAME_CAR)

        if mode == self.SAME_CAR:
            model = ask_choice(
                "Coche para todos los slots", cars, previous[0].model if previous else None
            )
            if len(ctx.catalog.skins(model)) > 1 and count > 1:
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
        skins = ctx.catalog.skins(model)
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
            f"\nPráctica:  {f'{s.practice} min' if s.runs_practice else 'omitida'}"
            f"\nQualify:   {f'{s.qualify} min' if s.runs_qualify else 'omitida'}"
            f"\nCarrera:   {self._race_label(s.race)}"
            f"\nOvertime:  qualify {s.qualify_overtime}% · carrera +{s.race_over_time}s"
            f"{' · vuelta extra' if s.race_extra_lap else ''}"
            f"\nSlots:     {len(s.slots)}"
            f"\nHora:      {format_time(s.time_minutes)} (x{s.time_mult})"
            f"\nAyudas:    {assists_label(s)}"
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


def default_wizard() -> Wizard:
    return Wizard(
        [TrackStep(), SessionsStep(), OvertimeStep(), TimeStep(), AssistsStep(), SlotsStep(), ConfirmStep()]
    )


def report(result: Result) -> None:
    if result.violations:
        print("Configuración inválida, sin cambios:")
        for violation in result.violations:
            print(f"  - {violation}")
    elif result.permission_error:
        print(f"Sin permisos para escribir ({result.permission_error}). Ejecuta la terminal como administrador.")
    elif result.written:
        print("Configuración guardada.")
