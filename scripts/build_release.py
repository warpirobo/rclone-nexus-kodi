#!/usr/bin/env python3
"""Build a deterministic Kodi-installable ZIP from the repository root."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import shutil
import xml.etree.ElementTree as ET
import zipfile

ADDON_ID = "plugin.ariostv"
EXCLUDED_TOP_LEVEL = {
    ".git",
    ".github",
    ".gitignore",
    ".gitattributes",
    "scripts",
    "dist",
    "build",
    "PUBLISHING_GUIDE_ES.md",
}
EXCLUDED_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}


def addon_version(root: Path) -> str:
    manifest = ET.parse(root / "addon.xml").getroot()
    if manifest.attrib.get("id") != ADDON_ID:
        raise ValueError(f"addon.xml id must be {ADDON_ID!r}")
    version = manifest.attrib.get("version", "").strip()
    if not version:
        raise ValueError("addon.xml does not define a version")
    return version


def include_file(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    if relative.parts and relative.parts[0] in EXCLUDED_TOP_LEVEL:
        return False
    if any(part in EXCLUDED_NAMES for part in relative.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def build(root: Path, output_dir: Path) -> Path:
    version = addon_version(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{ADDON_ID}-{version}.zip"
    if destination.exists():
        destination.unlink()

    files = sorted(path for path in root.rglob("*") if include_file(root, path))
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = PurePosixPath(path.relative_to(root).as_posix())
            archive_name = PurePosixPath(ADDON_ID) / relative
            info = zipfile.ZipInfo(str(archive_name), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist", help="Output directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = build(root, root / args.output)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
