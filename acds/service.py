"""Aplicación: puertos y casos de uso. No importa presentación ni infraestructura."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from .features import Feature, build, check_registry, default_features, load_state, validate_state
from .model import Bundle, Document, Skin, State, Track, Violation


# ───────────────────────────── Puertos ─────────────────────────────

class ConfigStore(Protocol):
    def read(self) -> tuple[Document, Document]:
        """(server_cfg, entry_list) actuales."""

    def write(self, bundle: Bundle) -> None: ...


class ContentCatalog(Protocol):
    def tracks(self) -> list[Track]: ...

    def cars(self) -> list[str]: ...

    def skins(self, model: str) -> list[Skin]: ...


class ProcessControl(Protocol):
    def stop(self) -> None: ...

    def start(self) -> None: ...


# ───────────────────────────── Casos de uso ─────────────────────────────

@dataclass
class Result:
    written: bool = False
    violations: list[Violation] = field(default_factory=list)
    permission_error: str = ""


class ConfigService:
    def __init__(
        self,
        store: ConfigStore,
        process: ProcessControl,
        features: Sequence[Feature] | None = None,
    ) -> None:
        self.store = store
        self.process = process
        self.features = list(features) if features is not None else default_features()
        check_registry(self.features)

    def load_state(self) -> State:
        server, entries = self.store.read()
        return load_state(self.features, server, entries)

    def validate(self, state: State) -> list[Violation]:
        return validate_state(self.features, state)

    def apply(self, state: State) -> Result:
        violations = self.validate(state)
        if violations:
            return Result(violations=violations)

        base_server, _ = self.store.read()
        bundle = build(self.features, state, base_server)

        self.process.stop()
        try:
            self.store.write(bundle)
            return Result(written=True)
        except PermissionError as exc:
            return Result(permission_error=str(exc))
        finally:
            self.process.start()
