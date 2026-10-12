# Darksiders para Blender

Addon de Blender 4.2+ para los **modelos, esqueletos y animaciones** de
*Darksiders II: Deathinitive Edition* (y los esqueletos de *Darksiders 1*).
Importa la geometría, la armadura y los clips, y exporta copias editadas del
modelo que parten del archivo original.

| Menú | Formato | Qué hace |
|------|---------|----------|
| `File > Import > Darksiders geometría (.2)` | `.2` | malla(s), UV, materiales por submalla y, si hay un `.o3d` al lado, armadura y grupos de vértices |
| `File > Import > Darksiders esqueleto (.o3d)` | `.o3d` | solo la armadura (vale para **Darksiders 1 y 2**) |
| `File > Import > Darksiders animación (.anm)` | `.anm` | acciones sobre la armadura activa; varios clips a la vez |
| `File > Import > Darksiders contenedor (.obp)` | `.obp` | saca sus modelos (`.2`), esqueletos (`.o3d`) y animaciones (`.anm`) a `<nombre>_extraido/` |
| `File > Export > Darksiders copia del modelo (.2)` | `.2` | copia del modelo importado con posiciones, UV, pesos y geometría borrada; conserva el esqueleto |

Para sacar OBJ o GLB, importa el `.2` y usa los exportadores de Blender.

## Instalación

```bash
python scripts/build.py
```

Deja `dist/io_darksiders-<versión>.zip`. En Blender:

1. `Edit > Preferences > Add-ons`, desplegable de la esquina, `Install from Disk`.
2. Elige el zip.
3. Si había otra versión cargada, reinicia Blender.

El addon es **autosuficiente**: lleva dentro todo lo que necesita y no busca
nada fuera de su carpeta. No hay nada que configurar. Para quitarlo, despliega
«Darksiders» en `Edit > Preferences > Add-ons` y pulsa **Desinstalar addon**;
tus `.blend` no cambian.

### Si venías de la versión de ANANSI (1.x)

Hasta la 1.4.0 el addon vivía en ANANSI y se llamaba **Darksiders (ANANSI)**
(`io_anansi_darksiders`). La 2.0.0 cambia de identificador, así que para
Blender es otro addon:

1. En `Edit > Preferences > Add-ons`, despliega «Darksiders (ANANSI)» y pulsa
   **Desinstalar addon**.
2. Instala `io_darksiders-2.0.0.zip` y reinicia Blender.

Las escenas importadas con la 1.1.0 o posterior siguen pudiendo exportar el
`.2`, y los rigs importados con la 1.2.0 o posterior siguen admitiendo
animaciones: lo que el addon guarda en el `.blend` no ha cambiado de nombre.
Cambian los identificadores de los operadores (`import_scene.darksiders_dcm` en
lugar de `import_scene.anansi_darksiders_dcm`), por si los usabas en scripts.

## Importar modelos

Importa el `.2`. Si hay un `.o3d` con el mismo nombre al lado, se crea la
armadura y la malla queda vinculada a ella. Para piezas sin esqueleto propio
(`death_head.2`, los guantes), elige `media/characters/death/death.o3d` en
**Esqueleto externo**.

* **Frente para Blender (giro 180° Z)**, activado por defecto, gira malla y
  esqueleto para ver el frente en la vista frontal. Es una rotación, no un
  espejo, y se deshace al exportar.
* El `.o3d` es un bosque (esqueleto, cámaras, enganches de VFX), así que la
  armadura sale con varias raíces. Es fiel al archivo.
* **Conectar huesos** está desactivado por defecto: queda más limpio, pero
  desplaza la cabeza de los huesos cuyo padre no termina donde empiezan ellos.
* Los materiales se crean con el nombre de la submalla y **sin textura**.
* Las UV se conservan siempre, aunque salgan de `[0,1]`: el juego repite
  texturas a propósito.
* Los avisos van a la barra de estado y **además** a la consola con el prefijo
  `[Darksiders]` (`Window > Toggle System Console` en Windows). Los avisos de
  «AABB que no cuadra» en piezas de equipo son esperables: su caja está en el
  espacio del hueso al que se acoplan.

## Importar animaciones

1. Importa el modelo con su `.o3d` (o el esqueleto externo).
2. Activa la armadura o una malla vinculada y abre
   `File > Import > Darksiders animación (.anm)`. Puedes elegir varios clips:
   cada uno crea una acción y la última queda activa.
3. Si hay pistas sin hueso, comprueba que el esqueleto es el del clip:
   **Death y Reaper no comparten esqueleto**. Para cargar solo las pistas que
   coinciden, activa **Permitir pistas sin hueso**; la acción queda marcada como
   `suspicious` y se enumeran las pistas omitidas.
4. Reproduce con Espacio y cambia de clip en `Dope Sheet > Action Editor`.

Se conservan movimiento de raíz, posición, rotación y escala. Los tiempos de
origen (30 FPS) se convierten a los FPS de la escena; si los cambias después,
reimporta el clip. No se reproducen mezclas de capas, eventos, telas, cámaras
ni efectos. Todavía **no se exporta `.anm`**.

## Exportar una copia del modelo

1. Edita posiciones, UV existentes o pesos, y borra la geometría que sobre con
   `X` (**Vertices**, **Edges**, **Faces** u **Only Faces**). No añadas vértices
   ni triángulos y conserva los grupos de huesos. Máximo cuatro influencias por
   vértice; se normalizan al exportar.
2. Con cualquier malla del modelo activa,
   `File > Export > Darksiders copia del modelo (.2)`. Se guardan **todas** las
   mallas de esa importación. Elige una ruta distinta del original; el selector
   propone el nombre del recurso original y avisa si lo cambias (`death.2` y
   `death_head.2` son recursos distintos).

El original tiene que seguir disponible y sin modificar: la copia parte de él y
conserva bloques desconocidos, índices, materiales, colores, paletas y bind
poses. Una exportación sin ediciones es **idéntica byte a byte**. Al cambiar
geometría o UV se recalculan normales y tangentes.

Se rechaza, con un mensaje que nombra el objeto, todo lo que no es una edición
soportada: fusionar, disolver, subdividir, extruir, costuras UV nuevas, cambios
de material, editar el esqueleto, exportar con una pose animada o con
modificadores activos distintos de Armature. Tampoco se admite todavía vaciar un
material o una malla entera, ni editar mallas estáticas (solo vuelven sin
cambios).

### Cargarlo en el juego

El addon no toca los archivos del juego. Para cargar la copia se usa
[Darksiders2DLL](https://github.com/BOTProT800/Darksiders2DLL), un proyecto
aparte. **Qué acepta el juego lo dice su README y sus registros, no este
documento**: a 2026-10-10 su `0.8.1-deletion-trial`, con
`models=override_shape`, acepta copias con geometría borrada en mallas con piel.

## Desarrollo

```bash
python -m pip install numpy pytest ruff
python -m pytest                     # suite completa, sin Blender
ruff check .
python scripts/build.py --blender "C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe"
```

QA con Blender real (no sustituye a la suite ni al revés):

| Script | Qué comprueba |
|---|---|
| `scripts/qa_install.py` | instala el zip en un Blender aislado, importa un `.2` real y pulsa **Desinstalar addon** |
| `scripts/qa_blender.py` | ida y vuelta byte idéntica, ediciones, borrado, rechazos y render frontal |
| `scripts/qa_animation_blender.py` | matrices frente al evaluador propio, FPS, guardado/reapertura y renders |
| `scripts/qa_animation_corpus.py` | decodifica todos los `.anm` de una carpeta y hace inventario |
| `scripts/qa_animation_native.py` | oráculo opcional: emula las rutinas del ejecutable (pefile + Unicorn) |

Los scripts de Blender se ejecutan con
`blender --background --factory-startup --python-exit-code 1 --python <script> -- …`;
cada uno explica sus argumentos en su cabecera. Escriben solo en un directorio
de salida nuevo y nunca tocan los originales.

| Documento | Contenido |
|---|---|
| [`docs/formato.md`](docs/formato.md) | los formatos `.obp`, `.2`, `.o3d` y `.anm`, con cómo se verificó cada cosa |
| [`docs/aprendizajes.md`](docs/aprendizajes.md) | lecciones del trabajo y mejoras pendientes |
| [`docs/animaciones.md`](docs/animaciones.md) | plan y evidencia de calidad de las animaciones |
| [`docs/historial/`](docs/historial/) | informes de fechas concretas, tal como se escribieron |
| [`AGENTS.md`](AGENTS.md) | instrucciones para asistentes de IA |
| [`CREDITS.md`](CREDITS.md) | fuentes externas |

## Origen y licencia

El addon nació dentro de [ANANSI](https://github.com/BOTProT800/ANANSI) y se
independizó en la 2.0.0 (2026-10-10). Es código original con licencia MIT
([`LICENSE`](LICENSE)). **No** es una actualización de los scripts de Blender
2.49 de Szkaradek123: esos no declaran licencia y no se derivan; se estudiaron
como documentación del formato, igual que las demás fuentes de
[`CREDITS.md`](CREDITS.md). El repositorio no contiene ni distribuye archivos
del juego.
