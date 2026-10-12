# CREDITS.md — Atribuciones de terceros

Registro de todo lo de **procedencia externa** que usa este addon: librerías,
herramientas, especificaciones y fuentes de ingeniería inversa. La política está
en [`AGENTS.md`](AGENTS.md), regla 6.

- Una entrada por recurso; si ya existe, se actualiza, no se duplica.
- Autor y licencia **no se inventan**: si no se pudieron verificar, la entrada
  dice `Desconocida — verificar`.
- Lo retirado se marca `[RETIRADO — AAAA-MM-DD]`, no se borra.
- Ninguna herramienta externa se redistribuye, y el repositorio no contiene
  archivos del juego.

Casi todas estas entradas vienen del `CREDITS.md` de ANANSI, donde se
registraron entre el 2026-08-27 y el 2026-10-07; al trasladarse el addon
(2026-10-10) se copiaron con las rutas de este repositorio. La fecha de
incorporación es la original.

---

## 1. Código de origen

### ANANSI — commit `e77e9bf` (2026-10-10)
- **Autor / Organización:** BOTProT800
- **Fuente:** https://github.com/BOTProT800/ANANSI
- **Licencia:** MIT (archivo `LICENSE` del repositorio, «Copyright (c) 2026 BOTProT800»)
- **Fecha de incorporación:** 2026-10-10
- **Dónde se usa:** todo `io_darksiders/`, `tests/`, `scripts/` y `docs/`
- **Para qué nos sirvió:** es donde se escribió el addon hasta la 1.4.0. Los
  parsers (`services/darksiders/`), el addon (`tools/blender/io_anansi_darksiders/`),
  sus pruebas, los scripts de QA y la documentación se trasladaron aquí, y
  `io_darksiders/formats/quality.py` es la parte de
  `services/common/mesh_quality.py` que usaban los parsers.
- **Modificaciones:** los parsers pasan de `services/darksiders/` a
  `io_darksiders/formats/` con imports relativos; fuera los conversores a OBJ y
  GLB, la contabilidad, la cancelación y el progreso de la interfaz de ANANSI;
  el OBP solo saca modelos, esqueletos y animaciones y sanea los nombres; el
  addon cambia de id (`io_anansi_darksiders` → `io_darksiders`) y deja de copiar
  los parsers a `_vendor/`.

## 2. Dependencias y herramientas de desarrollo

### NumPy — v2.5.1 (pyproject: `>=1.26.0`)
- **Autor / Organización:** Travis E. Oliphant et al. / NumPy Developers
- **Fuente:** https://numpy.org
- **Licencia:** `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`
- **Fecha de incorporación:** 2026-08-27 (preexistente en ANANSI)
- **Dónde se usa:** `io_darksiders/formats/`, `io_darksiders/roundtrip.py`,
  `io_darksiders/animation_import.py`, `tests/`, `scripts/`
- **Para qué nos sirvió:** leer y escribir los buffers del `.2` con stride
  arbitrario, normalizar pesos, evaluar curvas de animación y calcular las
  métricas de calidad de malla. Blender lo trae dentro, así que el addon no
  añade ninguna dependencia.
- **Modificaciones:** ninguna

### pytest — v9.1.1 (pyproject: `>=8.0`)
- **Autor / Organización:** Holger Krekel, Bruno Oliveira, Ronny Pfannschmidt,
  Floris Bruynooghe, Brianna Laugher, Florian Bruhin y otros (ver AUTHORS)
- **Fuente:** https://docs.pytest.org
- **Licencia:** `MIT`
- **Fecha de incorporación:** 2026-08-27 (preexistente en ANANSI)
- **Dónde se usa:** `tests/`
- **Para qué nos sirvió:** la suite que protege los parsers y el cableado del
  addon sin Blender.
- **Modificaciones:** ninguna

### Ruff — v0.16.7 (pyproject: `>=0.16`)
- **Autor / Organización:** Astral Software Inc.
- **Fuente:** https://docs.astral.sh/ruff
- **Licencia:** `MIT`
- **Fecha de incorporación:** 2026-08-31 (en ANANSI)
- **Dónde se usa:** `pyproject.toml` (`[tool.ruff]`)
- **Para qué nos sirvió:** detectar errores reales sin reglas de estilo. En
  ANANSI la carpeta del addon estaba excluida; aquí se revisa entero.
- **Modificaciones:** ninguna; reglas `E4`, `E7`, `E9`, `F`

### Blender — v5.2.2 LTS (mínimo declarado: 4.2.0)
- **Autor / Organización:** Blender Foundation
- **Fuente:** https://www.blender.org/
- **Licencia:** GNU GPL v2 o posterior (la aplicación) — **no se redistribuye**
- **Fecha de incorporación:** 2026-09-02 (en ANANSI)
- **Dónde se usa:** destino del addon (`io_darksiders/`); `scripts/build.py`
  invoca `blender --command extension validate`; los scripts `qa_*` lo
  ejecutan en segundo plano.
- **Para qué nos sirvió:** su subcomando `extension` define el formato del zip
  instalable y lo valida; su API `bpy`/`mathutils` es la de los operadores. Para
  las animaciones se consultó la API de acciones con slots:
  https://developer.blender.org/docs/release_notes/4.4/upgrading/slotted_actions/
  y https://docs.blender.org/api/5.2/bpy.types.ActionChannelbag.html . También
  sirve de oráculo visual en el QA.
- **Modificaciones:** ninguna

### Manual de Blender — v4.2 LTS
- **Autor / Organización:** Blender Documentation Team
- **Fuente:** https://docs.blender.org/manual/en/4.2/advanced/blender_directory_layout.html,
  https://docs.blender.org/manual/en/4.2/advanced/deploying_blender.html y
  https://docs.blender.org/manual/en/4.2/render/shader_nodes/textures/image.html
- **Licencia:** CC-BY-SA-4.0 (texto; se excluyen logos, marcas, iconos y código).
  Declaración: https://docs.blender.org/manual/ar/4.2/copyright.html
- **Fecha de incorporación:** 2026-09-15 (en ANANSI; consulta documental)
- **Dónde se usa:** `io_darksiders/blender_manifest.toml`, `scripts/build.py`,
  `docs/formato.md` (UV de la guadaña de Reaper)
- **Para qué nos sirvió:** confirmar que Blender instala las extensiones en
  repositorios de `extensions`, y explicar con la extensión `Repeat` del nodo
  Image Texture las UV fuera de `[0,1]`.
- **Modificaciones:** ninguna; consulta, sin copiar código.

### pefile — v2024.8.26
- **Autor / Organización:** Ero Carrera y colaboradores
- **Fuente:** https://github.com/erocarrera/pefile/blob/master/LICENSE
- **Licencia:** MIT
- **Fecha de incorporación:** 2026-09-16 (en ANANSI)
- **Dónde se usa:** `scripts/qa_animation_native.py` (opcional, solo QA); no se
  incluye en el addon.
- **Para qué nos sirvió:** localizar secciones y direcciones del ejecutable PE
  durante la comprobación de la compresión de animaciones.
- **Modificaciones:** ninguna

### Unicorn Engine — v2.1.4
- **Autor / Organización:** Unicorn Engine contributors
- **Fuente:** https://github.com/unicorn-engine/unicorn
- **Licencia:** GPL-2.0 (motor); componentes internos según sus avisos
- **Fecha de incorporación:** 2026-09-16 (en ANANSI)
- **Dónde se usa:** `scripts/qa_animation_native.py` (opcional, solo QA); no se
  incluye en el addon ni se redistribuye.
- **Para qué nos sirvió:** ejecutar aisladamente las rutinas matemáticas del
  lector de ANM del ejecutable instalado para contrastar el decodificador propio,
  sin iniciar el juego.
- **Modificaciones:** ninguna

### Capstone — v5.0.6
- **Autor / Organización:** Nguyen Anh Quynh / COSEINC y colaboradores
- **Fuente:** https://github.com/capstone-engine/capstone/blob/5.0.6/LICENSE.TXT
- **Licencia:** BSD-3-Clause
- **Fecha de incorporación:** 2026-09-16 (en ANANSI)
- **Dónde se usa:** ninguno en este repositorio; investigación local de `.anm`
  con herramientas temporales que no se versionaron.
- **Para qué nos sirvió:** desensamblar funciones del ejecutable instalado para
  comprobar la interpretación de las pistas comprimidas (`docs/formato.md`,
  apartado ANM).
- **Modificaciones:** ninguna

## 3. Fuentes de ingeniería inversa

Ninguna declara licencia. Se estudiaron como especificación; **no se copió ni
derivó código** de ninguna.

### finale00 — `fmt_DarkSiders2_dcm.py` (plugin Noesis)
- **Autor / Organización:** finale00 (la cabecera del archivo firma «Tsukihime»)
- **Fuente:** https://github.com/Techokami/noesis-plugins-official/blob/master/finale00/fmt_DarkSiders2_dcm.py
- **Licencia:** Desconocida — verificar
- **Fecha de incorporación:** 2026-08-27
- **Dónde se usa:** `io_darksiders/formats/mesh.py`
- **Para qué nos sirvió:** primera lectura legible de la cabecera del `.dcm`:
  las tres tablas de recuentos, el reparto por malla, la distinción entre
  `modelType 0` (estática) y `1` (con piel), y que en la ruta con piel la
  posición está en el desplazamiento 4 del registro de 52 bytes.
- **Modificaciones:** ninguna — no se copió código. La reimplementación corrige
  dos cosas: los recuentos de índices son `u32`, no `u16`, y los anchos de
  Deathinitive Edition son otros.

### zaramot — maxscripts de Darksiders (`DarkSiders_II_PC.ms`, `Darksiders.ms`)
- **Autor / Organización:** zaramot
- **Fuente:**
  - Darksiders II: https://github.com/XeNTaXBackup/XeNTaXBackup.github.io/blob/main/file/7442_DarkSiders_II_PC.7z
    (hilo archivado: https://github.com/XeNTaXBackup/XeNTaXBackup.github.io/blob/main/markdown/PC%20Darksiders%20II%20Death%20Lives%20maxscript_11574.md)
  - Darksiders 1: https://github.com/XeNTaXBackup/XeNTaXBackup.github.io/blob/main/file/7303_Darksiders.7z
    (hilo archivado: https://github.com/XeNTaXBackup/XeNTaXBackup.github.io/blob/main/markdown/PC%20Darksiders%20Wrath%20of%20War%20maxscript_11483.md)
- **Licencia:** Desconocida — verificar
- **Fecha de incorporación:** 2026-08-27
- **Dónde se usa:** `io_darksiders/formats/mesh.py`, `io_darksiders/formats/skin.py`,
  `docs/formato.md`
- **Para qué nos sirvió:** segunda opinión sobre el `.dcm`, la más completa en
  el rigging: bloque de pesos `f32[4]` seguido de índices `u8[4]`, registro de
  hueso con matriz 4x4 que ya es la **bind pose inversa**, winding invertido. El
  de Darksiders 1 documenta el `.mesh` (formato distinto) y sus variantes de
  `ModelTypeId`; se usó para la ficha de Darksiders 1, no para código.
- **Modificaciones:** ninguna — no se copió código. Su lectura de dos bytes
  antes de los cinco `i32` de la cabecera por malla no vale para Deathinitive,
  que lleva uno.

### Szkaradek123 — scripts Blender 2.49 para Darksiders y Deathinitive Edition
- **Autor / Organización:** Szkaradek123
- **Fuente:**
  - Darksiders (PC), lector de `.o3d`:
    https://github.com/XeNTaXBackup/XeNTaXBackup.github.io/blob/main/file/8187_Blender249%5BDarksiders%5D%5BPC%5D%5B2014-12-03%5D.zip
  - Deathinitive Edition, extractor OBP e importador `.2`:
    http://www.mediafire.com/file/4crafqf0qqj3a3y/Blender249%5BDarksidersIIDE%5D%5Bobp%5D%5B2%5D%5B2019-01-10%5D.zip
    (publicado en http://zenhax.com/viewtopic.php@t=8962.html). SHA-256 del zip:
    `fefdb43ea58eb0a3b064559a6c9c2b6cc48f0c2dbb59869c48591c3c617a71f8`.
- **Licencia:** Desconocida — verificar
- **Fecha de incorporación:** 2026-08-27
- **Dónde se usa:** `io_darksiders/formats/obp.py`, `io_darksiders/formats/mesh.py`,
  `io_darksiders/formats/skeleton.py`
- **Para qué nos sirvió:** la única fuente pública que documenta el contenedor
  **OBP** y la única que lee `.o3d`: qué se guarda por nodo, que las rotaciones
  van en grados y que la cabecera por malla del `.2` lleva **un** byte de tipo.
- **Modificaciones:** ninguna — no se copió código. En ANANSI hubo una copia
  literal del pack de Deathinitive (`tools/blender/darksiders2/`) como
  documentación; no se trasladó aquí. Dos hallazgos propios se apartan de sus
  scripts: el `u32` que precede al blob del OBP es el tamaño **descomprimido**, y
  el `.o3d` de Deathinitive **no** son registros fijos de 116 bytes sino un
  contenedor `BOD` de objetos etiquetados.

### Darksiders2.bms (QuickBMS) — manifest.bin + .upak
- **Autor / Organización:** Desconocida — verificar (el script cita a
  *DS2extract* como origen de la información del manifiesto)
- **Fuente:** https://github.com/XeNTaXBackup/XeNTaXBackup.github.io/blob/main/file/11044_Darksiders2.rar
- **Licencia:** Desconocida — verificar
- **Fecha de incorporación:** 2026-08-27
- **Dónde se usa:** ninguno todavía — `docs/formato.md` lo anota como pista
- **Para qué nos sirvió:** confirma que por encima del OBP hay `manifest.bin` /
  `pc.mnfst` indexando varios `.upak`, que lo que sale de ahí son OBPK, y que los
  offsets pasan a 64 bits cuando `Version >= 0x0d`. Sin implementar: hace falta
  un `manifest.bin` real.
- **Modificaciones:** n/a — solo lectura del script.

### Hilo de ZenHAX «Darksiders 2 Deathinitive Edition» (t=8962)
- **Autor / Organización:** Doctor Loboto y otros participantes del hilo
- **Fuente:** http://zenhax.com/viewtopic.php@t=8962.html
- **Licencia:** Desconocida — verificar
- **Fecha de incorporación:** 2026-08-27
- **Dónde se usa:** `io_darksiders/formats/mesh.py`
- **Para qué nos sirvió:** Doctor Loboto apunta que los modelos del OBP vienen
  «in a format .dcm»: la hipótesis de partida, que se confirmó y ahorró rehacer
  la ingeniería inversa del vertex buffer.
- **Modificaciones:** n/a — solo lectura del hilo.

### Khronos glTF 2.0 — especificación y glTF-Validator
- **Autor / Organización:** The Khronos Group
- **Fuente:** https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#skins ·
  https://github.com/KhronosGroup/glTF-Validator
- **Licencia:** Desconocida — verificar (la especificación de Khronos; el
  validador tiene la suya en su repositorio)
- **Fecha de incorporación:** 2026-08-27 (en ANANSI)
- **Dónde se usa:** `docs/formato.md`, apartado «Rig» (histórico: el exportador
  GLB de ANANSI no se trasladó)
- **Para qué nos sirvió:** su validador destapó que una inverse bind matrix
  tiene que ser afín exacta (`ACCESSOR_INVALID_IBM`).
- **Modificaciones:** n/a

## 4. Proyectos y datos locales

### Darksiders2DLL — v0.8.1-deletion-trial (consultado el 2026-10-10)
- **Autor / Organización:** BOTProT800 (propietario del repositorio)
- **Fuente:** https://github.com/BOTProT800/Darksiders2DLL
- **Licencia:** Desconocida — verificar (sin licencia propia localizada en el clon)
- **Fecha de incorporación:** 2026-09-16 (en ANANSI; consulta local, no se incorpora el binario)
- **Dónde se usa:** `README.md` («Cargarlo en el juego»), `docs/formato.md`,
  `docs/aprendizajes.md`, `docs/historial/estado-2026-09-16.md`
- **Para qué nos sirvió:** es el cargador que sustituye en memoria los recursos
  de `media.upak`, la única vía conocida para ver en el juego lo que exporta el
  addon. Sus registros y su validador fuera del juego mostraron en septiembre
  que exportar bien y cargar son contratos distintos; su README del 2026-10-10
  documenta que la 0.8.1 acepta geometría borrada en mallas con piel.
- **Modificaciones:** ninguna; consulta, sin copiar código ni distribuir la DLL.

### Muestras locales de Darksiders II: Deathinitive Edition — versión no determinada
- **Autor / Organización:** Desconocida — verificar (titulares de los assets del juego)
- **Fuente:** archivos del usuario en `C:/Users/vicen/Documents/Extractions/Darksiders/media/`;
  sin URL pública verificada
- **Licencia:** Desconocida — verificar. No se incluyen en git ni en el zip.
- **Fecha de incorporación:** 2026-09-15 (en ANANSI; uso para verificación local)
- **Dónde se usa:** `scripts/qa_blender.py`, `scripts/qa_animation_blender.py`,
  `scripts/qa_animation_corpus.py`, `scripts/qa_animation_native.py`
- **Para qué nos sirvió:** contrastar winding, orientación, normales, tangentes
  y `BBFollowNode` en `base_glovesa`, `abyssala01_gloves` y Belial; la ida y
  vuelta byte idéntica; 6.865 `.anm` de corpus; y, por análisis estático y
  emulación aislada, las rutinas de `Darksiders2.exe` (SHA-256
  `5580738ef70bc5bbcc72d7dc4a9c319956cd14dbfef6f9dbec54c1b5d97799fb`). No se
  copió código del ejecutable ni se redistribuyeron sus bytes.
- **Modificaciones:** originales intactos; copias de QA bajo `temp/`, fuera de git.

## 5. Asistentes de IA

### Claude Code — versión de la aplicación no determinada
- **Autor / Organización:** Anthropic
- **Fuente:** https://code.claude.com/docs
- **Licencia:** Desconocida — verificar (aplicación de escritorio y su documentación)
- **Fecha de incorporación:** 2026-10-10
- **Dónde se usa:** todo el repositorio (traslado desde ANANSI)
- **Para qué nos sirvió:** como agente, se usó para trasladar el addon desde
  ANANSI: reorganizar los parsers dentro del paquete, adaptar tests y scripts,
  reescribir el empaquetador, sanear los nombres del OBP, repetir el QA en
  Blender 5.2.2 con el zip 2.0.0 y escribir esta documentación.
- **Modificaciones:** ninguna.

### OpenAI Codex — versión de la aplicación no determinada
- **Autor / Organización:** OpenAI
- **Fuente:** https://openai.com/codex/
- **Licencia:** Desconocida — verificar (aplicación y herramientas de agente)
- **Fecha de incorporación:** 2026-09-15 (en ANANSI; registro de uso)
- **Dónde se usa:** historia del addon en ANANSI
- **Para qué nos sirvió:** como agente, en ANANSI: diagnóstico de orientación,
  recorrido de importación y exportación `.2`, diagnóstico de la copia de
  `death_head.2` e informe `docs/historial/estado-2026-09-16.md`.
- **Modificaciones:** ninguna.

## Pendiente de verificación

Licencias marcadas arriba como `Desconocida — verificar`: finale00, zaramot,
Szkaradek123, Darksiders2.bms, el hilo de ZenHAX, la especificación de Khronos,
Darksiders2DLL, los assets del juego, Claude Code y OpenAI Codex.
