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

### Si venías de la 1.x

Hasta la 1.4.0 el addon tenía otro identificador (`io_anansi_darksiders`). La
2.0.0 lo cambia, así que para Blender es otro addon:

1. En `Edit > Preferences > Add-ons`, despliega el «Darksiders» de versión 1.x
   y pulsa **Desinstalar addon**.
2. Instala el zip de esta versión (`io_darksiders-<versión>.zip`) y reinicia
   Blender.

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
   `X` (**Vertices**, **Edges**, **Faces** u **Only Faces**). Máximo cuatro
   influencias por vértice; se normalizan al exportar.

   En las mallas con piel también puedes **cambiar la geometría**: añadir,
   fusionar, subdividir, abrir costuras UV o sustituir la malla por la de otro
   modelo (borra la suya y une la otra con `Ctrl+J`, dejando activa la de este
   modelo). Esa malla se reescribe entera; ver «Malla nueva» abajo.
2. Con cualquier malla del modelo activa,
   `File > Export > Darksiders copia del modelo (.2)`. Se guardan **todas** las
   mallas de esa importación. Elige una ruta distinta del original; el selector
   propone el nombre del recurso original y avisa si lo cambias (`death.2` y
   `death_head.2` son recursos distintos).

El original tiene que seguir disponible y sin modificar: la copia parte de él y
conserva bloques desconocidos, índices, materiales, colores, paletas y bind
poses. Una exportación sin ediciones es **idéntica byte a byte**. Al cambiar
geometría o UV se recalculan normales y tangentes; el signo de la bitangente se
conserva del original y solo se invierte donde la edición lo invierte (por
ejemplo, al espejar una isla UV).

Si mueves vértices de una malla cuya caja va referida a un hueso que la malla no
trae (pasa en `makermale_elder.2`), el exportador busca ese hueso en el
esqueleto elegido al importar, en los `.o3d` de la carpeta y en los de alrededor,
y solo lo usa si la caja original lo confirma. Si no lo encuentra, rechaza la
exportación con «falta resolver BBFollowNode».

Se rechaza, con un mensaje que nombra el objeto, todo lo que no es una edición
soportada: editar el esqueleto, exportar con una pose animada o con
modificadores activos distintos de Armature, vaciar un material o una malla
entera y editar mallas estáticas (solo vuelven sin cambios).

### Malla nueva

Si una malla con piel ya no es la original con trozos borrados, se reescribe con
lo que hay en Blender. Del original se conservan su **paleta de huesos**, sus
**materiales**, sus bind poses y las demás mallas; el aviso de la exportación
dice cuántos vértices y triángulos tiene la nueva frente a la original.

- **Huesos:** solo los de la paleta de esa malla. Si la paleta tiene un único
  hueso (pasa en `ds1a01_shoulder.2`, solo `Bone_Clavicle_L`), la malla entera va
  rígida a él y se avisa de los grupos que no caben. Con varios, un peso en un
  hueso ajeno se rechaza y hay que pasarlo a uno de la paleta.
- **Materiales:** las caras con un material que no es de la plantilla van a su
  único material, si solo tiene uno (se avisa: la textura será la de ese
  material); con varios, hay que asignárselos.
- **Triángulos:** triangula antes (`Ctrl+T`).
- No se reconstruyen las mallas que usan un segundo canal de atributos de
  significado desconocido (349 de 1.624 en el corpus, Belial entre ellas) ni las
  que tienen color por vértice: no hay de dónde sacar esos valores.

### Cargarlo en el juego

El addon no toca los archivos del juego. Para cargar la copia se usa
[Darksiders2DLL](https://github.com/BOTProT800/Darksiders2DLL), un proyecto
aparte. **Qué acepta el juego lo dice su README y sus registros, no este
documento**: a 2026-10-10 su `0.8.1-deletion-trial`, con
`models=override_shape`, acepta copias con geometría borrada en mallas con piel.
Una malla nueva necesita `models=override_unchecked` (código 0.9.0, pendiente de
probar en el juego a 2026-10-17), que la encaja en las lecturas del original:
solo la carga si cada material tiene **como mucho los vértices y triángulos del
original**. El exportador avisa cuando la malla nueva no cabe.

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

Es código original con licencia MIT ([`LICENSE`](LICENSE)). **No** es una actualización de los scripts de Blender
2.49 de Szkaradek123: esos no declaran licencia y no se derivan; se estudiaron
como documentación del formato, igual que las demás fuentes de
[`CREDITS.md`](CREDITS.md). El repositorio no contiene ni distribuye archivos
del juego.
