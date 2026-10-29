# AGENTS.md — Darksiders para Blender

Instrucciones canónicas para cualquier asistente de IA en este repositorio
(Claude Code, Cursor, Codex, Copilot…). `CLAUDE.md` las importa.

## Qué es y qué no

Un addon de Blender 4.2+ para **modelos (`.2`), esqueletos (`.o3d`) y
animaciones (`.anm`)** de Darksiders. Del contenedor `.obp` solo saca esos
tres tipos. Texturas, materiales (`.bmat`) y datos de motor están **fuera de
alcance por decisión del usuario** (2026-10-10): no se añaden sin que lo pida.
Tampoco hay conversores a OBJ/GLB fuera de Blender; para eso están los
exportadores de Blender.

Antes de investigar un formato, lee [`docs/formato.md`](docs/formato.md) y
[`docs/aprendizajes.md`](docs/aprendizajes.md): casi todo lo que parece nuevo ya
se midió.

## Estructura

| Ruta | Qué hay |
|---|---|
| `io_darksiders/` | la extensión de Blender: lo que va en el zip |
| `io_darksiders/__init__.py` | `bl_info`, `register`/`unregister`; **sin `bpy` a nivel de módulo** |
| `io_darksiders/operators.py` | operadores, menús y preferencias: todo lo que usa `bpy` al importarse |
| `io_darksiders/plan.py` | traduce los parsers a un plan de escena, sin `bpy` |
| `io_darksiders/roundtrip.py` | metadatos en el `.blend` y exportación de la copia `.2` |
| `io_darksiders/animation_import.py` | clips `.anm` → acciones de Blender |
| `io_darksiders/formats/` | parsers y escritor, solo Python + numpy |
| `tests/` | suite sin Blender; `_fake_bpy.py` es un doble de la API |
| `scripts/` | empaquetado y QA con Blender real |
| `docs/` | formato, aprendizajes, animaciones e historial |

## Reglas que no se negocian

1. **Autosuficiente.** El addon instalado no depende de nada fuera de su
   carpeta: ni preferencias con rutas, ni variables de entorno, ni `sys.path`,
   ni descargas, ni paquetes que Blender no trae (solo `numpy`). Todo lo propio
   se importa en relativo. `scripts/build.py` rompe el empaquetado si un módulo
   importa algo ajeno, y `tests/test_package.py` lo vigila.
2. **Botón de desinstalar en cada versión.** `DARKSIDERS_OT_uninstall` está en
   `_CLASSES` y el panel de preferencias lo dibuja cuando el addon está
   instalado como extensión (`bl_ext.<repo>.io_darksiders`). Pide confirmación
   con `invoke_confirm`, desinstala con `extensions.package_uninstall` y lo
   **aplaza** con `bpy.app.timers.register` (dentro del operador desregistraría
   su propia clase). Desde el código fuente no se ofrece: sobre un enlace al
   repositorio borraría el repositorio.
3. **`unregister()` olvida los submódulos** (`forget_submodules`), para que otra
   versión instalada sin reiniciar Blender no herede los parsers viejos.
4. **No se renombra lo que se guarda en el `.blend`.** Las propiedades
   `anansi_*` (`roundtrip.PREFIX`, `animation_import.METADATA`, `anansi_vertex_id`)
   conservan ese nombre para que las escenas de la 1.x sigan funcionando.
5. **Ni assets del juego ni binarios de terceros** en git o en el zip. No se
   deriva código sin licencia declarada (los scripts de Szkaradek123, finale00 y
   zaramot se estudian como documentación, nunca se copian).
6. **Créditos en el mismo cambio.** Todo recurso de origen externo (librería,
   herramienta, especificación, hilo, script consultado) va a
   [`CREDITS.md`](CREDITS.md) cuando se introduce, con el formato de ese archivo.
   Si no se puede verificar autor o licencia: `Desconocida — verificar`, y se
   dice en la respuesta. Nunca se inventa. Lo retirado se marca
   `[RETIRADO — AAAA-MM-DD]`, no se borra.
7. **Los parsers validados no se reescriben por estilo.** Se tocan para
   arreglar o ampliar, con un test que lo demuestre.

## Cuándo algo está terminado

- Una malla importada es sólida y reconocible, con UV coherentes; si va
  riggeada, la jerarquía está completa y los pesos suman 1 (`normalise_weights`).
- Un escritor no está listo hasta que una exportación sin cambios reproduce el
  original **byte a byte**.
- Tres estados, nunca dos: escrito, **sospechoso** (sale, pero algo huele mal:
  la acción `suspicious`, los avisos de AABB) y omitido con motivo.
- Los tests con `_fake_bpy` prueban el cableado, no Blender. Lo que cambie la
  importación o la exportación se comprueba con Blender real y assets reales
  (`scripts/qa_blender.py`, `scripts/qa_animation_blender.py`) y se **mira** el
  render. Si no se pudo hacer, se dice.
- Al informar, separar lo medido de lo inferido.
- Qué carga el juego lo dicen el README y los registros de
  [Darksiders2DLL](https://github.com/BOTProT800/Darksiders2DLL), no este repo.

## Con cada versión del addon

1. Sube `version` en `io_darksiders/blender_manifest.toml` y en `bl_info`
   (`tests/test_package.py` exige que coincidan).
2. `python -m pytest` y `ruff check .`
3. `python scripts/build.py --blender <blender.exe>` (valida el manifiesto).
4. `python scripts/qa_install.py --blender <blender.exe> --sample <un .2 real>`:
   instala el zip en un Blender aislado y comprueba autosuficiencia, importación
   y desinstalación. Todo tiene que salir `OK`.
5. Si cambió la importación o la exportación, `scripts/qa_blender.py` y, si
   tocó animaciones, `scripts/qa_animation_blender.py`, con `--addon-parent`
   apuntando a una carpeta que contenga el zip extraído como `io_darksiders/`.
6. Actualiza `README.md` y, si hubo hallazgo o tropiezo, `docs/aprendizajes.md`
   o `docs/formato.md`.

## Convenciones y entorno

- Código, comentarios, documentación y mensajes en **castellano**.
- `operators.py` no puede usar `from __future__ import annotations`: Blender lee
  `__annotations__` al registrar las clases.
- Blender de Steam no está en el `PATH`:
  `C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe` (5.2.2 LTS).
  Para QA, `--factory-startup` y `BLENDER_USER_RESOURCES` en un temporal, para
  no tocar la configuración del usuario.
- Muestras del juego (fuera del repo):
  `C:\Users\vicen\Documents\Extractions\Darksiders\media\`.
- Salidas locales de QA en `temp/` (ignorado por git).
