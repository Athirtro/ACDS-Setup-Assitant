# /// script
# requires-python = ">=3.11"
# dependencies = ["questionary>=2.0.1"]
# ///
"""Punto de entrada y composición: conecta adaptadores, servicio y asistente."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from acds.cli import Cancelled, Context, default_wizard, report
from acds.infra import ACDS_EXE, ContentScanner, IniConfigStore, NullProcess, ServerProcess
from acds.service import ConfigService, Result

ACDS_ROOT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa Dedicated Server")

EXIT_OK = 0
EXIT_CANCELLED = 1
EXIT_BAD_ENVIRONMENT = 2
EXIT_NO_PERMISSION = 3
EXIT_INVALID = 4


def exit_code(result: Result) -> int:
    if result.written:
        return EXIT_OK
    if result.permission_error:
        return EXIT_NO_PERMISSION
    return EXIT_INVALID


def main() -> int:
    if os.name != "nt":
        print("Este script está diseñado para ejecutarse en Windows.")
        return EXIT_BAD_ENVIRONMENT

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ACDS_ROOT, help="Carpeta raíz del ACDS")
    parser.add_argument("--exe", default=ACDS_EXE, help="Nombre del ejecutable del servidor")
    parser.add_argument("--no-service", action="store_true", help="No detener ni iniciar el servidor")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    content = root / "content"
    if not content.is_dir():
        print(f"No existe {content}")
        return EXIT_BAD_ENVIRONMENT

    catalog = ContentScanner(content)
    process = NullProcess() if args.no_service else ServerProcess(root, args.exe, notify=print)
    service = ConfigService(IniConfigStore(root / "cfg"), process)

    ctx = Context(catalog, service.load_state())
    try:
        default_wizard().run(ctx)
    except Cancelled:
        print("Cancelado, sin cambios.")
        return EXIT_CANCELLED

    result = service.apply(ctx.state)
    report(result)
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
