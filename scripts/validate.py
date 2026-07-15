#!/usr/bin/env python3
"""Run lightweight checks that do not require a Kodi installation."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILES = [ROOT / "addon.py", ROOT / "service.py", *sorted((ROOT / "resources/lib").rglob("*.py"))]
XML_FILES = [ROOT / "addon.xml", ROOT / "resources/settings.xml"]
SPANISH_MARKERS = (
    "No se ", "Ajustes", "Biblioteca", "Contenido nuevo", "Favoritos", "Herramientas",
    "Sincroniz", "Diagnóstico", "Carpeta", "Películas", "NUEVO", "Añadidos",
    "Eliminados", "Limpieza", "Arquitectura elegida", "Último reporte",
)
ALLOWED_COMPATIBILITY_MARKERS = ("'si'", "'sí'", "'automatico'", "'automático'")


def main() -> int:
    errors: list[str] = []
    for path in PYTHON_FILES:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            errors.append(f"Python parse error in {path.relative_to(ROOT)}: {exc}")

    for path in XML_FILES:
        try:
            ET.parse(path)
        except Exception as exc:
            errors.append(f"XML parse error in {path.relative_to(ROOT)}: {exc}")

    manifest = ET.parse(ROOT / "addon.xml").getroot()
    if manifest.attrib.get("id") != "plugin.ariostv":
        errors.append("addon.xml has an unexpected add-on id")

    for path in [*PYTHON_FILES, ROOT / "resources/settings.xml", ROOT / "README.md", ROOT / "CHANGELOG.md"]:
        text = path.read_text(encoding="utf-8")
        scrubbed = text
        for marker in ALLOWED_COMPATIBILITY_MARKERS:
            scrubbed = scrubbed.replace(marker, "")
        for marker in SPANISH_MARKERS:
            if marker in scrubbed:
                errors.append(f"Possible untranslated text in {path.relative_to(ROOT)}: {marker!r}")

    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
