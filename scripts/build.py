#!/usr/bin/env python3
"""Empaqueta el addon como extensión .zip instalable en Blender.

    python scripts/build.py [--output-dir DIR] [--blender RUTA]

Deja `dist/io_darksiders-<version>.zip` con el mismo reparto que produce
`blender --command extension build`: el manifiesto en la raíz del zip, sin
carpeta contenedora que lo envuelva. Se instala desde
`Edit > Preferences > Add-ons > Install from Disk`.

No se llama a Blender para construirlo, a propósito: el formato es un zip
corriente y depender del ejecutable obligaría a tenerlo instalado y a adivinar
dónde está. Si Blender aparece en el PATH (o se pasa con `--blender`), se usa
solo para *validar* el resultado.

El zip es autosuficiente porque la carpeta `io_darksiders/` ya lo es: los
parsers viven dentro, en `formats/`. Antes de escribir nada se comprueba que
ningún módulo importe algo que Blender no trae.
"""
from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "io_darksiders"
MANIFEST = "blender_manifest.toml"

# Mismo criterio que los excludes por defecto de `extension build`: fuera los
# artefactos de Python y de git, y fuera cualquier zip previo que estuviera
# dentro de la carpeta.
EXCLUDED_DIRS = {"__pycache__", ".git", ".ruff_cache", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip"}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}

# Lo único de fuera de la biblioteca estándar que el addon puede importar:
# la API de Blender y numpy, que Blender trae dentro.
ALLOWED_THIRD_PARTY = {"bpy", "bpy_extras", "mathutils", "bmesh", "numpy"}


def read_manifest(addon: Path) -> dict:
    """Lee el manifiesto y comprueba lo que Blender exige para instalar."""
    path = addon / MANIFEST
    if not path.is_file():
        raise SystemExit(f"Falta {path}: sin manifiesto no hay extensión que instalar.")

    with path.open("rb") as handle:
        manifest = tomllib.load(handle)

    for field in ("schema_version", "id", "version", "name", "type"):
        if not manifest.get(field):
            raise SystemExit(f"{MANIFEST} no declara «{field}», que Blender da por obligatorio.")

    # El id tiene que casar con la carpeta: Blender instala bajo el id, y si no
    # coinciden los imports relativos del addon apuntan a un paquete que no está.
    if manifest["id"] != addon.name:
        raise SystemExit(
            f"El id del manifiesto («{manifest['id']}») no coincide con la carpeta "
            f"(«{addon.name}»). Blender instala bajo el id y los imports se romperían."
        )

    return manifest


def foreign_imports(addon: Path) -> list[str]:
    """Imports absolutos que el addon instalado no podría resolver.

    Todo lo propio se importa en relativo (`from .formats import mesh`); un
    import absoluto solo puede ser de la biblioteca estándar o de
    `ALLOWED_THIRD_PARTY`. Cualquier otra cosa funcionaría en esta máquina y
    fallaría dentro de Blender, lejos de aquí.
    """
    stdlib = set(sys.stdlib_module_names)
    problems: list[str] = []
    for path in sorted(addon.rglob("*.py")):
        if EXCLUDED_DIRS.intersection(path.relative_to(addon).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in stdlib or top in ALLOWED_THIRD_PARTY or top == "__future__":
                    continue
                problems.append(f"{path.relative_to(addon).as_posix()} importa {name}")
    return problems


def files_to_pack(addon: Path) -> list[Path]:
    """Archivos que entran en el zip, en orden estable."""
    keep: list[Path] = []
    for path in sorted(addon.rglob("*")):
        if not path.is_file():
            continue
        if EXCLUDED_DIRS.intersection(path.relative_to(addon).parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES:
            continue
        keep.append(path)
    return keep


def build(addon: Path, output_dir: Path) -> Path:
    manifest = read_manifest(addon)
    # Antes de escribir nada: un zip que falla al importar es peor que ninguno.
    problems = foreign_imports(addon)
    if problems:
        raise SystemExit(
            "El addon importa módulos que Blender no trae (el zip saldría roto):\n  "
            + "\n  ".join(problems)
        )
    members = files_to_pack(addon)

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{manifest['id']}-{manifest['version']}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in members:
            bundle.write(path, path.relative_to(addon).as_posix())

    print(f"Empaquetado {target}  ({target.stat().st_size:,} bytes)")
    for path in members:
        print(f"  {path.relative_to(addon).as_posix()}")
    return target


def validate(bundle: Path, blender: str | None = None) -> bool:
    """Pasa el zip por el validador de Blender, si hay uno a mano."""
    blender = blender or shutil.which("blender")
    if blender is None:
        # Habitual en Windows: ni los instaladores ni Steam lo ponen en el PATH.
        print("\nBlender no está en el PATH: me salto la validación del manifiesto.")
        print("Para validar, pasa la ruta del ejecutable con --blender.")
        return True

    result = subprocess.run(
        [blender, "--command", "extension", "validate", str(bundle)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # El zip queda escrito para poder mirarlo, pero Blender lo rechazaría al
        # instalarlo, así que no se puede dar por bueno.
        print("\nBlender rechaza el manifiesto:", file=sys.stderr)
        print((result.stdout + result.stderr).strip(), file=sys.stderr)
        return False

    print("\nValidado con `blender --command extension validate`.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="Dónde dejar el zip (por defecto: dist/ en la raíz del repo).",
    )
    parser.add_argument(
        "--blender",
        help="Ruta a blender.exe para validar el zip. Por defecto se busca en el PATH.",
    )
    args = parser.parse_args()

    bundle = build(ADDON, args.output_dir)
    # El zip se deja escrito aunque Blender lo rechace, para poder mirarlo,
    # pero el código de salida no puede decir que todo fue bien.
    if not validate(bundle, args.blender):
        return 1

    print(
        "\nPara instalarlo:"
        "\n  1. Blender > Edit > Preferences > Add-ons > desplegable > Install from Disk"
        f"\n  2. Elige {bundle}"
        "\n  3. Reinicia Blender si había otra versión cargada."
        "\n\nEs autosuficiente: no busca nada fuera de su carpeta."
        "\nPara quitarlo, «Desinstalar addon» en sus preferencias."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
