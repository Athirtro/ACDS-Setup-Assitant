"""Adaptadores: archivos .ini, contenido de Steam y proceso de acServer.exe.

Sin reglas de negocio: solo traducen entre el mundo exterior y los puertos de `service`.
"""
from __future__ import annotations

import base64
import binascii
import configparser
import json
import os
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import Callable
from .model import CSP_PREFIX, Bundle, Document, Skin, Track

ACDS_EXE = "acServer.exe"
DEFAULT_WELCOME = "cfg/welcome.txt"
CSP_SEPARATOR = "\t" * 32 + "$CSP0:"  # oculta el bloque a quien no use CSP

# ───────────────────────────── Archivos .ini ─────────────────────────────

def _parser() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # type: ignore[assignment]
    return cp


class IniFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Document:
        cp = _parser()
        if self.path.exists():
            cp.read(self.path, encoding="utf-8-sig")
        return Document({section: dict(cp.items(section)) for section in cp.sections()})

    def save(self, document: Document) -> None:
        cp = _parser()
        for section in document.sections():
            cp.add_section(section)
            for key, value in document.items(section).items():
                cp.set(section, key, value)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))

        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\r\n") as fh:
                cp.write(fh, space_around_delimiters=False)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

def _encode_csp(options: Document) -> str:
    lines: list[str] = []
    for section in options.sections():
        lines.append(f"[{section}]")
        lines += [f"{key} = {value}" for key, value in options.items(section).items()]
        lines.append("")
    payload = zlib.compress("\n".join(lines).encode("utf-8"), 6)
    return base64.b64encode(payload).decode("ascii").rstrip("=")


def _decode_csp(encoded: str) -> Document:
    encoded = encoded.strip()
    try:
        raw = zlib.decompress(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
        cp = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=(";",))
        cp.optionxform = str  # type: ignore[assignment]
        cp.read_string(raw.decode("utf-8-sig"))
    except (binascii.Error, zlib.error, ValueError, configparser.Error):
        return Document()
    return Document({section: dict(cp.items(section)) for section in cp.sections()})


def _split_csp(document: Document) -> tuple[Document, Document]:
    """(server_cfg sin opciones de CSP, opciones de CSP sin prefijo)."""
    plain, csp = Document(), Document()
    for section in document.sections():
        target, name = (csp, section[len(CSP_PREFIX):]) if section.startswith(CSP_PREFIX) else (plain, section)
        for key, value in document.items(section).items():
            target.set(name, key, value)
    return plain, csp


class IniConfigStore:
    """server_cfg.ini + entry_list.ini + opciones extra de CSP.

    Las opciones de CSP viajan ocultas en el mensaje de bienvenida (WELCOME_MESSAGE);
    aquí se exponen como secciones con prefijo `CSP/` dentro del documento del servidor.
    """

    def __init__(self, cfg_dir: Path) -> None:
        self.root = cfg_dir.parent
        self.server_file = IniFile(cfg_dir / "server_cfg.ini")
        self.entries_file = IniFile(cfg_dir / "entry_list.ini")

    def read(self) -> tuple[Document, Document]:
        server = self.server_file.load()
        _, encoded = self._read_welcome(server.get("SERVER", "WELCOME_MESSAGE"))
        csp = _decode_csp(encoded) if encoded else Document()
        for section in csp.sections():
            for key, value in csp.items(section).items():
                server.set(f"{CSP_PREFIX}{section}", key, value)
        return server, self.entries_file.load()

    def write(self, bundle: Bundle) -> None:
        plain, csp = _split_csp(bundle.server)
        reference = plain.get("SERVER", "WELCOME_MESSAGE")
        if csp.sections() and not reference:
            reference = DEFAULT_WELCOME
            plain.set("SERVER", "WELCOME_MESSAGE", reference)
        self.server_file.save(plain)
        self.entries_file.save(bundle.entries)
        if reference:
            self._write_welcome(reference, csp)

    def _welcome_path(self, reference: str) -> Path | None:
        if not reference:
            return None
        path = Path(reference)
        return path if path.is_absolute() else self.root / path

    def _read_welcome(self, reference: str) -> tuple[str, str]:
        """(texto visible, bloque CSP codificado)."""
        path = self._welcome_path(reference)
        if path is None or not path.is_file():
            return "", ""
        visible, separator, encoded = path.read_text(encoding="utf-8-sig").partition(CSP_SEPARATOR)
        return visible, encoded if separator else ""

    def _write_welcome(self, reference: str, csp: Document) -> None:
        path = self._welcome_path(reference)
        if path is None:
            return
        exists = path.is_file()
        if not exists and not csp.sections():
            return
        visible, _ = self._read_welcome(reference)
        text = visible + (CSP_SEPARATOR + _encode_csp(csp) if csp.sections() else "")
        if exists and text == path.read_text(encoding="utf-8-sig"):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if exists:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        path.write_text(text, encoding="utf-8", newline="")


# ───────────────────────────── Contenido de Steam ─────────────────────────────

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


# ───────────────────────────── Proceso ACDS ─────────────────────────────

class ServerProcess:
    def __init__(
        self,
        root: Path,
        exe: str = ACDS_EXE,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.exe_path = root / exe
        self.notify = notify or (lambda _message: None)

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
        pids = self._pids()
        if not pids:
            return
        self.notify(f"Deteniendo {self.exe_path.name}...")
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)

    def start(self) -> None:
        if not self.exe_path.is_file():
            self.notify(f"Aviso: no se encontró {self.exe_path}")
            return
        self.notify(f"Iniciando {self.exe_path.name}...")
        subprocess.Popen(
            [str(self.exe_path)],
            cwd=self.root,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )


class NullProcess:
    """Para --no-service: no detiene ni inicia nada."""

    def stop(self) -> None:
        pass

    def start(self) -> None:
        pass
