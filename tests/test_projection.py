"""Golden tests del comportamiento verificado. Sin disco, sin procesos, sin questionary.

Ejecutar:  uv run tests/test_projection.py        (o: uv run --with pytest pytest)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acds.features import (  # noqa: E402
    SERVER,
    Contribution,
    Feature,
    OwnershipError,
    build,
    check_registry,
    default_features,
    load_state,
    validate_state,
)
from acds.model import Bundle, Document, Race, Slot, State  # noqa: E402
from acds.service import ConfigService  # noqa: E402

FIXED_START = 1_700_000_000
FEATURES = default_features(clock=lambda: FIXED_START)


def base_server(**extra: dict[str, str]) -> Document:
    data = {
        "SERVER": {"TRACK": "old", "KEEP_ME": "yes"},
        "RACE": {"WAIT_TIME": "90", "LAPS": "5", "NAME": "Mi carrera"},
        "DYNAMIC_TRACK": {"SESSION_START": "95"},
    }
    data.update(extra)
    return Document(data)


def make_state(**changes) -> State:
    values = {"track": "monza", "layout": "gp", "slots": [Slot("ks_mazda_miata", "red")]}
    values.update(changes)
    return State(**values)


# ───────────────────────────── Sesiones ─────────────────────────────

def test_sessions_order_and_omission():
    bundle = build(FEATURES, make_state(practice=None, qualify=15, race=Race(True, 8)), base_server())
    assert bundle.server.sections() == ["SERVER", "QUALIFY", "RACE", "DYNAMIC_TRACK"]
    assert not bundle.server.has_section("PRACTICE")


def test_race_by_laps_writes_only_laps_and_keeps_saved_keys():
    bundle = build(FEATURES, make_state(race=Race(True, 8)), base_server())
    race = bundle.server.items("RACE")
    assert race["LAPS"] == "8" and race["IS_OPEN"] == "1"
    assert "TIME" not in race
    assert race["WAIT_TIME"] == "90" and race["NAME"] == "Mi carrera"


def test_race_by_time_writes_only_time():
    bundle = build(FEATURES, make_state(race=Race(False, 30)), base_server())
    race = bundle.server.items("RACE")
    assert race["TIME"] == "30" and race["IS_OPEN"] == "1"
    assert "LAPS" not in race


def test_session_defaults():
    bundle = build(FEATURES, make_state(), Document({"SERVER": {}}))
    assert bundle.server.items("PRACTICE") == {"NAME": "Practice", "IS_OPEN": "1", "TIME": "20"}
    assert bundle.server.items("QUALIFY") == {"NAME": "Qualify", "IS_OPEN": "1", "TIME": "15"}
    assert bundle.server.items("RACE")["LAPS"] == "10"


# ───────────────────────────── Overtime ─────────────────────────────

def test_overtime_keys():
    state = make_state(qualify_overtime=130, race_over_time=200)
    server = build(FEATURES, state, base_server()).server
    assert server.get("SERVER", "QUALIFY_MAX_WAIT_PERC") == "130"
    assert server.get("SERVER", "RACE_OVER_TIME") == "200"


def test_extra_lap_only_for_timed_races():
    timed = build(FEATURES, make_state(race=Race(False, 20), race_extra_lap=True), base_server())
    laps = build(FEATURES, make_state(race=Race(True, 20), race_extra_lap=True), base_server())
    assert timed.server.get("SERVER", "RACE_EXTRA_LAP") == "1"
    assert laps.server.get("SERVER", "RACE_EXTRA_LAP") == "0"


# ───────────────────────────── Hora ─────────────────────────────

def test_time_without_weather():
    server = build(FEATURES, make_state(time_minutes=13 * 60, time_mult=3), base_server()).server
    assert server.get("SERVER", "SUN_ANGLE") == "0"
    assert server.get("SERVER", "TIME_OF_DAY_MULT") == "3"


def test_sun_angle_is_clamped():
    server = build(FEATURES, make_state(time_minutes=22 * 60), base_server()).server
    assert server.get("SERVER", "SUN_ANGLE") == "80"


def test_time_with_weather_keeps_type_and_start():
    base = base_server(WEATHER_0={"GRAPHICS": "3_clear_type=15_start=5"})
    server = build(FEATURES, make_state(time_minutes=14 * 60, time_mult=2), base).server
    assert server.get("WEATHER_0", "GRAPHICS") == "3_clear_type=15_time=50400_mult=2_start=5"
    assert server.get("SERVER", "TIME_OF_DAY_MULT") == "1"


def test_time_with_weather_without_type_uses_clock():
    base = base_server(WEATHER_0={"GRAPHICS": "3_clear"})
    server = build(FEATURES, make_state(time_minutes=14 * 60, time_mult=1), base).server
    assert server.get("WEATHER_0", "GRAPHICS") == f"3_clear_time=50400_mult=1_start={FIXED_START}"

# ───────────────────────────── Assists ─────────────────────────────

def test_assists_projection():
    state = make_state(abs_mode=2, tc_mode=0, stability=True, autoclutch=True)
    server = build(FEATURES, state, base_server()).server
    assert server.get("SERVER", "ABS_ALLOWED") == "2"
    assert server.get("SERVER", "TC_ALLOWED") == "0"
    assert server.get("SERVER", "STABILITY_ALLOWED") == "1"
    assert server.get("SERVER", "AUTOCLUTCH_ALLOWED") == "1"


def test_assists_defaults_when_keys_are_missing():
    state = load_state(FEATURES, Document({"SERVER": {}}), Document())
    assert (state.abs_mode, state.tc_mode, state.stability, state.autoclutch) == (1, 1, False, False)


def test_invalid_assist_mode_is_rejected():
    assert any(v.field == "ABS" for v in validate_state(FEATURES, make_state(abs_mode=5)))

# ───────────────────────────── Slots ─────────────────────────────

def test_slots_projection():
    slots = [Slot("car_a", "s1"), Slot("car_a", "s2"), Slot("car_b", "")]
    bundle = build(FEATURES, make_state(slots=slots), base_server())
    assert bundle.server.get("SERVER", "CARS") == "car_a;car_b"
    assert bundle.server.get("SERVER", "MAX_CLIENTS") == "3"
    assert bundle.entries.sections() == ["CAR_0", "CAR_1", "CAR_2"]
    assert bundle.entries.items("CAR_1") == {
        "MODEL": "car_a", "SKIN": "s2", "SPECTATOR_MODE": "0", "DRIVERNAME": "",
        "TEAM": "", "GUID": "", "BALLAST": "0", "RESTRICTOR": "0",
    }


def test_unmanaged_keys_are_preserved():
    server = build(FEATURES, make_state(), base_server()).server
    assert server.get("SERVER", "KEEP_ME") == "yes"
    assert server.get("DYNAMIC_TRACK", "SESSION_START") == "95"


# ───────────────────────────── Carga y validación ─────────────────────────────

def test_build_then_load_roundtrip():
    state = make_state(
        practice=10, qualify=5, race=Race(False, 20), race_extra_lap=True,
        qualify_overtime=130, race_over_time=200, time_minutes=14 * 60, time_mult=2,
        slots=[Slot("car_a", "s1"), Slot("car_b", "")],
    )
    bundle = build(FEATURES, state, base_server())
    assert load_state(FEATURES, bundle.server, bundle.entries) == state


def test_validation():
    assert validate_state(FEATURES, make_state()) == []
    fields = {v.field for v in validate_state(FEATURES, State())}
    assert {"pista", "slots"} <= fields
    none = make_state(practice=None, qualify=None, race=None)
    assert any(v.field == "sesiones" for v in validate_state(FEATURES, none))


# ───────────────────────────── Propiedad de claves ─────────────────────────────

class Rogue(Feature):
    id = "rogue"
    owns = ("server:SERVER:TRACK",)

    def project(self, state, base):
        return [Contribution.put(SERVER, "SERVER", "TRACK", "x")]


def test_registry_detects_duplicated_patterns():
    try:
        check_registry([*FEATURES, Rogue()])
    except OwnershipError:
        return
    raise AssertionError("debía detectar el patrón duplicado")


def test_build_detects_collisions():
    class Squatter(Feature):
        id = "squatter"

        def project(self, state, base):
            return [Contribution.put(SERVER, "SERVER", "UNOWNED_KEY", "x")]

    try:
        build([*FEATURES, Squatter()], make_state(), base_server())
    except OwnershipError:
        return
    raise AssertionError("debía rechazar una clave sin propietario")


# ───────────────────────────── Servicio ─────────────────────────────

class FakeStore:
    def __init__(self, events: list[str], fail: bool = False) -> None:
        self.events, self.fail, self.written = events, fail, None

    def read(self):
        return base_server(), Document()

    def write(self, bundle: Bundle) -> None:
        self.events.append("write")
        if self.fail:
            raise PermissionError("denegado")
        self.written = bundle


class FakeProcess:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def stop(self) -> None:
        self.events.append("stop")

    def start(self) -> None:
        self.events.append("start")


def test_apply_order_stop_write_start():
    events: list[str] = []
    service = ConfigService(FakeStore(events), FakeProcess(events), FEATURES)
    result = service.apply(make_state())
    assert result.written and events == ["stop", "write", "start"]


def test_apply_restarts_server_even_if_write_fails():
    events: list[str] = []
    service = ConfigService(FakeStore(events, fail=True), FakeProcess(events), FEATURES)
    result = service.apply(make_state())
    assert not result.written and "denegado" in result.permission_error
    assert events == ["stop", "write", "start"]


def test_apply_invalid_state_touches_nothing():
    events: list[str] = []
    service = ConfigService(FakeStore(events), FakeProcess(events), FEATURES)
    result = service.apply(State())
    assert result.violations and events == []


if __name__ == "__main__":
    failed = 0
    for name, test in sorted(globals().items()):
        if name.startswith("test_") and callable(test):
            try:
                test()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failed else 0)
