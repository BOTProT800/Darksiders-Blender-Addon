#!/usr/bin/env python3
"""QA de la extensión instalada: autosuficiente y con botón de desinstalar.

    python scripts/qa_install.py --blender <blender.exe> --sample <modelo.2>

Instala `dist/io_darksiders-<versión>.zip` como extensión en una configuración
de Blender **aislada y temporal** (`BLENDER_USER_RESOURCES`), sin tocar la del
usuario, y comprueba lo que exige `AGENTS.md` (autosuficiente y con botón de
desinstalar):

* no hay preferencia de carpeta y los parsers se cargan desde su carpeta;
* importa un `.2` real;
* «Desinstalar addon» está disponible y, al pulsarlo, borra la carpeta,
  desactiva el addon, retira sus operadores y descarga sus parsers.

El diálogo de confirmación no se ve sin ventana; se comprueba que Blender acepta
sus parámetros. Sale con código 1 si falla cualquier comprobación.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "io_darksiders"
MODULE = "bl_ext.user_default.io_darksiders"
MARK = "QA_INSTALACION "


def inside_blender(sample: Path) -> None:
    """Se ejecuta dentro de Blender, con la extensión ya instalada."""
    import importlib

    import addon_utils
    import bpy

    out = {}
    addon_utils.enable(MODULE, default_set=True)
    module = importlib.import_module(MODULE + ".operators")
    folder = Path(module.__file__).parent
    out["habilitado"] = MODULE in bpy.context.preferences.addons
    prefs = bpy.context.preferences.addons[MODULE].preferences
    out["sin_preferencias_de_ruta"] = not [p.identifier for p in prefs.bl_rna.properties
                                           if getattr(p, "subtype", "") in {"DIR_PATH", "FILE_PATH"}]
    out["importa"] = bpy.ops.import_scene.darksiders_dcm(filepath=str(sample)) == {"FINISHED"}
    loaded = Path(sys.modules[MODULE + ".formats.mesh"].__file__).resolve()
    out["parsers_desde_su_carpeta"] = loaded.is_relative_to(folder.resolve())
    out["sin_paquete_services"] = not any(name == "services" or name.startswith("services.")
                                          for name in sys.modules)

    confirm = bpy.types.WindowManager.bl_rna.functions["invoke_confirm"].parameters
    out["dialogo_valido"] = all(p in confirm for p in ("title", "message", "confirm_text", "icon")) \
        and "WARNING" in confirm["icon"].enum_items.keys()
    identity = module._extension_identity(bpy.context)
    out["boton_disponible"] = identity is not None and bpy.ops.preferences.darksiders_uninstall.poll()
    if identity is not None:
        # Pulsar el botón sin el diálogo y ejecutar lo que el temporizador hace
        # en la siguiente vuelta del bucle de eventos (en segundo plano no gira).
        out["boton_responde"] = bpy.ops.preferences.darksiders_uninstall() == {"FINISHED"}
        module._uninstall_later(*identity)()
    out["carpeta_borrada"] = not folder.exists()
    out["deshabilitado"] = MODULE not in bpy.context.preferences.addons
    out["operadores_fuera"] = "darksiders_dcm" not in dir(bpy.ops.export_scene)
    out["parsers_descargados"] = not any(name.startswith(MODULE + ".") for name in sys.modules)
    print(MARK + json.dumps(out, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blender", required=True, help="Ruta a blender.exe (4.2 o posterior)")
    parser.add_argument("--sample", type=Path, required=True, help="Un .2 real para importar")
    parser.add_argument("--zip", type=Path, help="Por defecto, el de la versión del manifiesto en dist/")
    args = parser.parse_args()
    with (ADDON / "blender_manifest.toml").open("rb") as handle:
        version = tomllib.load(handle)["version"]
    bundle = (args.zip or ROOT / "dist" / f"io_darksiders-{version}.zip").resolve()
    if not bundle.is_file():
        print(f"No existe {bundle}: genera antes el zip con scripts/build.py", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="darksiders-blender-", ignore_cleanup_errors=True) as tmp:
        env = dict(os.environ, BLENDER_USER_RESOURCES=tmp)
        install = subprocess.run(
            [args.blender, "--factory-startup", "--command", "extension", "install-file",
             "-r", "user_default", "-e", str(bundle)],
            env=env, capture_output=True, text=True)
        if install.returncode != 0:
            print("Blender no instaló el zip:\n" + install.stdout + install.stderr, file=sys.stderr)
            return 1
        run = subprocess.run(
            [args.blender, "--background", "--factory-startup", "--python-exit-code", "1",
             "--python", str(Path(__file__).resolve()), "--", "--inside", str(args.sample.resolve())],
            env=env, capture_output=True, text=True)
    line = next((ln for ln in run.stdout.splitlines() if ln.startswith(MARK)), None)
    if line is None:
        print("Blender no terminó la comprobación:\n" + run.stdout[-3000:] + run.stderr[-3000:],
              file=sys.stderr)
        return 1
    results = json.loads(line[len(MARK):])
    for check, ok in results.items():
        print(f"  {'OK   ' if ok else 'FALLO'} {check}")
    failed = [check for check, ok in results.items() if not ok]
    print(f"\n{bundle.name}: " + ("todo correcto" if not failed else f"{len(failed)} fallos"))
    return 1 if failed else 0


if __name__ == "__main__":
    if "--inside" in sys.argv:
        inside_blender(Path(sys.argv[sys.argv.index("--inside") + 1]))
    else:
        raise SystemExit(main())
