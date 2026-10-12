# Formatos de Darksiders: lo que se sabe y cómo se sabe

Referencia técnica de los formatos que lee y escribe el addon (`io_darksiders/`).
Nació como la sección «Vigil Engine» de la referencia de mallas de ANANSI
(`agents/skills/mesh-reverse-engineering/reference.md`), donde se fue escribiendo
entre el 2026-08-27 y el 2026-10-07, y se trasladó aquí el 2026-10-10 cuando
Darksiders salió de ANANSI. Las fechas de cada apartado son las de su hallazgo.

Fuentes externas en [`../CREDITS.md`](../CREDITS.md): finale00, zaramot,
Szkaradek123 y el hilo 8962 de ZenHAX. Ninguna declara licencia, así que se
estudiaron como especificación y **no** se derivó código de ellas. Los scripts
de Blender 2.49 de Szkaradek123 estuvieron copiados en ANANSI como
documentación; aquí no se incluyen.

Lo aprendido que no es una regla del formato —procesos, errores y mejoras
pendientes— está en [`aprendizajes.md`](aprendizajes.md). El plan y la
evidencia de las animaciones, en [`animaciones.md`](animaciones.md).

**Estado por juego.** Deathinitive Edition (2015) está completo y validado
contra archivos reales. De Darksiders 1 (2010) está resuelto el `.o3d` —es el
mismo contenedor, ver más abajo— y **falta** la cadena de geometría:
`.oppc` (contenedor), `.meshpack` (paquete de mallas, cada entrada con su
propio zlib) y `.mesh` (geometría). Nada de eso comparte formato con el `.2`.
Del Darksiders II original (2012) falta la variante del `.dcm`: la cabecera por
malla lleva **dos** bytes antes de los cinco `i32` en vez de uno, los bloques de
atributos son más estrechos (12 con piel, 20 estática) y las UV van en
half-float. El resto de la cabecera es idéntico.

**Capa por encima del OBP (pendiente).** En Darksiders 2 los OBPK no están
sueltos: viven dentro de varios `.upak` indexados por `manifest.bin` (o
`pc.mnfst`). Un script QuickBMS de la comunidad documenta esa tabla — rutas,
referencias a `.upak`, y offsets que pasan a 64 bits cuando `Version >= 0x0d` —
y confirma que lo que sale de ahí son OBPK, o sea el contenedor que
`io_darksiders/formats/obp.py` ya lee. No implementado: hace falta un
`manifest.bin` real. Fuente en `CREDITS.md` («Darksiders2.bms»).

## El reparto de archivos

| Extensión | Tipo OBP | Qué es |
|---|---|---|
| `.o3d` | 1 | **árbol de nodos** de la escena: esqueleto, enganches de VFX, cámaras |
| `.2` | 2 | **geometría**: es el `.dcm` de Darksiders II (2012) con los bloques ensanchados |
| `.bmat` | 5 | material |
| `.dds` | 6 | textura, DDS estándar sin reordenado |

El `.o3d` **no es una malla**. Los nombres de hueso son la única llave entre los
dos archivos, y funcionan: los 141 nombres que declaran las seis paletas de
`belial.2` resuelven contra los 186 nodos de `belial.o3d`.

## OBP: un solo blob zlib

Magic `OBPK\0`. Cabecera con `A[1]` = offset de la tabla de grupos y `A[5]` =
offset del blob. Grupos por id de tipo, luego los nombres en una lista plana,
luego la tabla de tamaños; el orden de los grupos es el mismo que el de los
nombres, y de ahí sale la extensión de cada recurso.

**Trampa:** el `u32` que hay en `A[5]`, justo antes del flujo zlib, es el tamaño
**descomprimido** (coincide con la suma de la tabla de tamaños), no el
comprimido. El script de referencia lo pasa a `read(n)`, que sobre un `file` de
Python devuelve lo que haya y por eso le funciona por accidente. El blob llega
hasta el final del archivo.

## `.2`: el stride es lo único que cambió respecto a 2012

La cabecera es idéntica a la del `.dcm`. Lo que Deathinitive movió son los
anchos, y es donde se rompen los scripts de la comunidad:

```
                        .dcm 2012      .2 Deathinitive
con piel  atributos      12 B           20 B    (UV half -> UV f32)
          vértice        52 B           52 B    (posición en +4, no en +0)
          pesos          16 B           16 B
          huesos          4 B            4 B
          total          84 B           92 B
estática  posición       12 B           12 B
          atributos      20 B           48 B    (UV en +28)
          cola           16 B           16 B
          total          48 B           76 B
```

Tres comprobaciones que se validan solas y evitan adivinar:

1. **La cabecera tiene que terminar exactamente en `data_offset`.** Es lo que
   delata el reparto de `head[3]`/`head[4]`, que **intercambian su significado**
   entre las dos rutas (con piel: materiales, huesos; estática: auxiliar,
   materiales) y que además no llevan registros de hueso en la ruta estática
   aunque el recuento no sea cero.
2. `dataSize == index_count * 2` y `structSize == 2` al principio del bloque.
3. **La AABB declarada en la cabecera (6 floats) contra la leída.** Es el mejor
   oráculo de stride que se ha encontrado en todo el repo: localizó el bloque de
   vértices por fuerza bruta en las seis mallas a la vez, con error 0,004.

**Falso amigo:** al final de cada registro de atributos hay un `u32` que vale
`0xFFFFFFFF` en casi todas las mallas. **No es un centinela de stride, es el
color por vértice**: `vfx3D_Belial_RuptureHit.2` lo usa de verdad en 120 de sus
364 vértices. Exigirlo rechaza archivos válidos — se probó y falló. Para validar
el stride está la AABB.

Otros datos: índices `u16` con winding invertido; la V se guarda **negada**
(rango -1..0); la UV llega a 2,0 por tiling, así que un «% en [0,1]» bajo es
normal y no indica UVs rotas. El campo `head[1]` vale 60 en las estáticas y 64
en las de piel, pero **no es el stride** (son 76 y 92): es otra constante del
formato, como el `0x134` de Battleship.

## `.o3d`: contenedor `BOD`, no registros fijos

Aquí es donde la referencia externa **no sirve**. Szkaradek123 lee el `.o3d` de
Darksiders 1 como registros fijos de 116 bytes tras buscar la cadena `Node3D`.
Aplicado al `.o3d` de Deathinitive eso devuelve basura: coge «Node Motion» como
primer hueso y se desincroniza en el segundo.

El archivo es un **serializador de objetos autodescriptivo**. Magic `BOD\xfd`,
16 bytes de cabecera, y luego un flujo de tokens:

```
cadena := 0x01 u64 hash u16 len bytes    (nueva; recibe el id correlativo siguiente)
        | 0x00 u32 id                    (referencia a una ya escrita)
objeto := 0x07 u32 id 0x01 cadena(clase) u32 nprops { cadena(nombre) valor }
valor  := 0x01 u8 | 0x02 i32 | 0x03 f32 | 0x0f cadena
        | 0x0b u32 n { valor }           (array)
        | 0x09 u32 n u8 marca { valor }  (array con marca de tipo)
        | 0x07 ...                       (objeto anidado)
```

Cada cadena se escribe **una sola vez** en todo el archivo: por eso `Position`
aparece una vez aunque la lleven ciento ochenta nodos. El objeto raíz es un
`Object3D` con `Skeleton` (un `Skeleton3D`), `Visuals` y `Bodies`.

Cómo se sabe que la gramática es correcta: **consume el archivo entero**, byte a
byte, hasta el último. Merece la pena exigirlo en el parser — es la comprobación
más barata y la más contundente.

Trampas:

* `Position` y `Rotation` son **opcionales**; ausentes valen identidad. Darlas
  por hechas desincroniza (`Bone_BE_Pelvis` no trae `Position`).
* El nivel superior es un **bosque**, no un root único: `belial.o3d` tiene 20
  raíces (esqueleto, phantoms, cámaras, nodos de VFX). Para glTF conviene
  sintetizar una raíz que los agrupe.
* `Rotation` son **grados**, orden **XYZ**, convención de fila (`v' = v*M`).
  Verificado, no supuesto: reconstruir `inverse(world)` desde el `.o3d` reproduce
  las 141 matrices de bind pose que el `.2` guarda por su cuenta con error
  máximo 0,023 sobre valores de cientos de unidades. Las otras cinco
  permutaciones de Euler dan errores de 100 a 200.
* Deathinitive **no usa `Scale`** en ningún nodo (comprobado en los nueve `.o3d`
  del OBP de Belial); Darksiders 1 sí. Ausente vale **1**, no 0.

### El `.o3d` de Darksiders 1 es este mismo contenedor

El script de 2014 busca `Node3D`, salta 19 bytes y lee registros **fijos de 116
bytes**. Esos números son exactamente el tamaño de los tokens BOD cuando el nodo
trae las cinco propiedades del esquema de Darksiders 1 y los nombres ya están
internados:

```
 19 = propcount(4) + ref a Name(5) + etiqueta 0x0f(1) + strdef(1) + hash(8)
 97 = 3 x [ref(5) + cabecera de array(5) + 3 x (etiqueta + f32)(15)] + cola(22)
116 = 19 + 97
```

La cola de 22 bytes es `ref a Children(5) + 0x09 + recuento(4) + marca(1) +
0x07 + id(4) + 0x01 + ref a la clase(5)`. De ahí salen las dos constantes que
ese script usa a ciegas: `A[6]` es el byte bajo del recuento de hijos y
`A[11] == 7` es la etiqueta de objeto del primer hijo. Cuadra byte a byte
(`tests/test_skeleton.py`).

**Lección general:** un lector de offsets fijos que "funciona" puede estar
leyendo un formato autodescriptivo cuyo esquema resulta ser constante en ese
juego. Descomponer sus saltos mágicos en tokens antes de dar por hecho que hay
dos formatos distintos ahorró aquí un parser entero — y explica por qué el mismo
script se rompe en la secuela: allí las propiedades son opcionales.

## Darksiders 1: lo que falta y por qué no se implementó a ciegas

`.mesh` es una **cadena de bloques** `u32 count` + `count x stride`, nada que ver
con el `.2`. Índices `u32` (no `u16`), pesos como `(i32 hueso, f32 peso) x 4`
intercalados (32 B/vértice, no dos bloques separados), UV en `f32` dentro de un
registro de 12 bytes, y la tabla de huesos **al final** del archivo, con
`u32 BoneCount, u32 UsedBoneCount` y matriz 4x4 + `i32` por hueso.

Las dos referencias **no coinciden** en la cadena de bloques intermedios entre
la UV y los pesos: Szkaradek lee `u32+12n`, `u32+16n`, `u32+16n`, `skip 4`;
zaramot lee `skip 8`, `u32+16n`, `skip 4` para `ModelTypeId 6` y `skip 0x10`,
`u32+32n` para el 5. El primer `u32` del archivo es ese `ModelTypeId` y hay al
menos tres variantes (5, 6 y 7 estático, que zaramot ni intenta). Sin un `.mesh`
real no se puede decidir cuál vale, así que no se escribió: habría sido adivinar
offsets, que es justo lo que la skill prohíbe.

> **Nota (2026-10-10).** Los dos apartados siguientes describen el exportador
> GLB que tuvo ANANSI (`services/darksiders/rig.py`). El addon no lo conserva:
> para sacar OBJ o GLB se usan los exportadores de Blender. Se dejan porque las
> trampas que cuentan (IBM afín exacta, una primitiva por submalla) valen para
> cualquier exportador de glTF que se escriba en el futuro.

## Rig

Los `JOINTS_0` del `.2` son **índices locales a la lista de huesos de cada
malla** — el patrón de *bone palette*. Se traducen por
nombre contra el `.o3d`. Los pesos ya vienen normalizados de fábrica
(`sum = 1.0`, peor caso 0,99992), pero hay que normalizar igual por si acaso: la
suma cero es lo que dispara el vértice al origen.

Las bind pose inversas se **calculan** como `inverse(world)` desde el árbol del
`.o3d` en vez de copiar las del `.2`. Coinciden, y así hay una única fuente de
verdad.

**Trampa de glTF, no del juego:** `np.linalg.inv` deja ruido de ~1e-19 en la
última columna y el validador de Khronos lo rechaza con
`ACCESSOR_INVALID_IBM` — la especificación exige que la IBM sea afín exacta, no
«casi». Hay que fijar la columna a `[0, 0, 0, 1]` a mano. Con eso, y emitiendo
una primitiva por submalla con su material (si no, salta `UNUSED_OBJECT` sobre
`TEXCOORD_0`), el validador da **0 errores y 0 avisos**.

## QA de esta extracción

* `belial.2`: 6 mallas, 9876 vértices, 13 736 triángulos, todas con piel.
* Validador de Khronos: 0 errores / 0 avisos en los tres GLB.
* Pose de descanso comprobada releyendo el GLB y componiendo como un visor:
  `max |IBM_j * world_j - I|` = 6,4e-06 y desplazamiento máximo de vértice
  2,5e-05 sobre una diagonal de AABB de 405 — ruido de float32, sin espinas.
* QA visual: la silueta es reconociblemente Belial (demonio alado, yelmo
  cornudo, cola segmentada) y `belial_polearma01.2` es la alabarda. El aviso
  «arista máxima 11,7x la mediana» de esa alabarda es un **falso positivo
  legítimo**: es un arma larga y fina.

---

## La AABB de la cabecera no siempre está en el espacio de los vértices (2026-09-03)

`_check_bbox` usa la caja declarada en la cabecera del `.2` como oráculo del
stride: una redundancia que verifica el propio archivo. Funciona en los personajes (Belial pasa),
pero **rechaza piezas de equipo perfectamente sanas**.

Caso: `media/items/armor/necroa01_shoulder.2`, dos mallas skinned.

| | Malla 0 |
|---|---|
| AABB leída | (−3.56, −8.73, 47.07)−(5.68, −6.21, 53.82) |
| AABB declarada | (4.12, −8.23, −5.69)−(11.22, −4.50, 3.54) |
| Error | 52.759 |

La lectura es correcta, y hay dos pruebas independientes:

1. **Las normales son unitarias.** En el registro skinned de 52 B el layout es
   `pos.xyz` en 4:16, `w = 1.0` exacto en 16, y normal en 20:32 con `|n| = 1.0000`
   en las dos mallas. Con el stride mal repartido eso no saldría. El reparto
   global también encaja al byte: la malla 0 acaba en 5390, justo donde empieza
   la 1 (92 B/vértice = 20 + 52 + 16 + 4).
2. **El `.o3d` de al lado lo dice por escrito.** No es un esqueleto: es un
   descriptor de visuals, con `ClothVisual` (`SpringRadius`, `SpringIterations`,
   `Drag`, `ClothCollisionChannels`) y un `SkinMeshVisual` que nombra
   **`Bone_Spine2`** junto a `BBFollowNode`.

Y ahí está la explicación: llevando los vértices por la matriz de `Bone_Spine2`
el error cae de **52.759 a 0.461**. La caja está expresada en el nodo al que la
pieza se acopla, no en el espacio local de la malla. El residuo de 0.46 queda
sin explicar —hará falta más de una muestra para cerrarlo—.

**Lección:** en Vigil, la AABB de la cabecera vale como oráculo de stride solo
cuando la malla vive en su propio espacio. Para las piezas de equipo hace falta
una señal independiente del espacio, y las normales unitarias lo son. Por eso
`_check_bbox` acepta un `layout_avalado` y degrada a aviso en vez de abortar.

**Trampa relacionada:** `load_skeleton()` devuelve `None` tanto si no hay `.o3d`
como si lo hay y no trae `Skeleton`. Decir «sin .o3d al lado» cuando el archivo
está delante manda a buscar un problema que no existe: hay que distinguir los
dos casos con `find_o3d()`.

## Orientación frontal y retorno nativo `.2` (2026-09-15)

Compartir Z-up no define el frente. El addon 1.0 dejaba las coordenadas nativas;
para los personajes que miran hacia +Y, la vista frontal de Blender ve la espalda.
El addon 1.1 añade un giro **opcional**, activado por defecto: `(x,y,z)->(-x,-y,z)`.
Se aplica a posiciones, normales y matrices globales de huesos (`R @ world.T`).
Su determinante es +1: no es un espejo ni exige invertir más caras. Se guarda
por importación y se deshace exactamente al escribir el `.2`.

No quitar la inversión de winding existente: con las normales originales @+20,
**2.093/2.093** caras no degeneradas de `base_glovesa.2` tienen producto escalar
positivo tras esa inversión. `abyssala01_gloves.2`: 5.094/5.096 (dos caras con
normales suavizadas casi tangenciales). La captura de guantes mirando atrás no
es evidencia de winding incorrecto.

Los `.o3d` de esos dos guantes son `Object3D` con dos `SkinMeshVisual`, sin
`Skeleton`; `BBFollowNode`/`RefNode` nombran `Bone_ForeArm_L/R`, `MeshID` 0/1.
Al transformar las posiciones con las inverse-bind de esos huesos, la geometría
cabe dentro de la caja declarada: son cajas acolchadas, no AABB mínimas de los
vértices en el espacio del modelo. El exportador conserva los márgenes y amplía
la caja por unión con la nueva geometría en ese espacio. Si una caja no coincide
y no se resuelve su `BBFollowNode`, rechaza editar posiciones.

Registro skinned verificado: influencia u32 @0, posición xyz @4, w @16,
normal xyz @20, **normal.z duplicada @32**, tangente xyz @36, signo @48.
En `base_glovesa`, el signo almacenado coincide con el calculado usando la UV de
Blender (`u,-v_juego`); con la V cruda del juego tiene el signo opuesto.
Antes de escribir normales, se exige que la plantilla cumpla la duplicación @32.

`io_darksiders/formats/native.py` parchea una plantilla, no inventa un formato:
conserva tamaño, índices, cabeceras desconocidas, materiales, paletas y bind poses.
`roundtrip.py` guarda ruta, SHA-256, instancia, orientación e identidad de vértices
en el `.blend`; conserva los pesos crudos cuando el usuario no los editó.
Soporta posiciones/UV/pesos skinned con topología fija; recalcula normales y
tangentes al cambiar geometría/UV. Rechaza cambios de topología (desde 1.3.0
admite borrar; ver «Borrar geometría en el `.2`» más abajo), nueva costura
por loop, más de cuatro influencias, pose/rest rig editada y sobreescritura del
original. No escribe `.o3d`, `.bmat`, animaciones ni recompone `.obp`/`.upak`.

QA real reproducible: `scripts/qa_blender.py`. En Blender 5.2.2 LTS:
`base_glovesa` (1.377 verts/2.093 tris), `abyssala01_gloves` (3.826/5.096) y Belial
(9.876/13.736) pasan importación-exportación **byte idéntica** con ambos modos
frontales. Edición de vértice/UV/peso releída correctamente. Guantes con esqueleto
externo `characters/death/death.o3d`: sin deformación en reposo (<1e-3 unidades)
y vuelta idéntica. Render comparativo frontal inspeccionado. El test admite el
ZIP extraído con `--addon-parent` para comprobar el addon empaquetado, sin el
código del repositorio. Repetido el 2026-10-10 con el zip 2.0.0: mismos
resultados.
**Pendiente**: prueba de una edición dentro del juego y reconstrucción libre de
geometría/contenedores. El roundtrip binario no demuestra esa compatibilidad.

## Borrar geometría en el `.2` (2026-10-07, addon 1.3.0)

Borrar es la única edición de topología que no obliga a inventar datos: cada
superviviente tiene un registro original completo (atributos, vértice, pesos,
huesos) que se copia íntegro. Añadir vértices sí lo obligaría, y sigue fuera.

**Cómo se reconoce el borrado.** `anansi_vertex_id` (atributo INT en POINT) lo
conserva Blender al borrar, y los supervivientes quedan en su orden relativo.
Comprobado con `bpy.ops.mesh.delete(type="VERT")` y `type="ONLY_FACE"` en Blender
5.2.2. Un borrado es: ids estrictamente crecientes y triángulos (traducidos por
los ids) que forman una subsecuencia de los originales. Fusionar (M) o subdividir
rompen esa forma y se rechazan; está en el QA real.

**Qué depende del número de vértices.** Medido sobre 6.421 `.2` legibles (23.327
mallas, 34.939 tramos de material), sin una sola excepción:

- tablas de recuentos de la cabecera, `head[0]`, la cabecera del bloque
  (`bytes de índices`) y `head[2]` de **todas** las mallas posteriores;
- tramos de material `(índice, 0, vértices, primer vértice, índices, primer
  índice)`: contiguos en el búfer de índices, nunca vacíos, lo cubren entero, su
  rango de vértices es exactamente `[mín, máx]` de sus índices, no se solapan y
  teselan `[0, n)`; ningún vértice queda sin triángulo;
- el primer bloque empieza en `data_offset`, van en orden de malla y no hay nada
  tras el último.

Con eso los tramos se recalculan sin adivinar: rango de vértices = mín/máx de los
índices remapeados, rango de índices = contando triángulos supervivientes. Los
vértices que se quedan sin triángulo se descartan para conservar la teselación.

**La tabla `{i32 + f32} × n` era una pareja por malla.** Con piel, siempre
`(0, 0.0)` (1.624/1.624). Estática: `(vértices, factor)` cuando el bloque lleva
la cola de 16 B/vértice (76 B/vértice, 21.253 mallas) y `(0, 0.0)` cuando no
(60 B/vértice, 450 mallas en 71 archivos). Coincidencia exacta malla a malla.
`parse_dcm` aún supone 76 siempre: en esas 450 lee 16·n bytes del bloque
siguiente. Las posiciones y UV salen bien porque van delante.

**`Visuals[].Checksum` del `.o3d` no protege el `.2`.** En 400 muestras no
coincide con CRC32, Adler32 ni MD5 del archivo, del bloque de la malla ni de sus
índices. Su significado sigue sin saberse.

**Caja.** Se conserva la declarada: 1.439 de las 1.624 mallas con piel ya traen
una caja que no es la AABB de sus vértices (espacio `BBFollowNode`), y las 1.624
tienen normales unitarias, así que la relectura la acepta con aviso.

**Validación.** Borrando una zona conexa al azar (1 vértice hasta ¼ de la malla)
en las 1.624 mallas con piel del corpus: 1.624 podadas, cada una verificada
campo a campo contra el original. Blender real: `base_glovesa` 527→471 vértices
con borrado + «solo caras» + mover un vértice en la misma exportación, Belial
1.808→1.799; ambas copias se reimportan, y el render muestra un hueco limpio.

**Fuera:** borrar en mallas estáticas, vaciar un material o una malla entera y
cualquier adición. No probado en el juego, y el cargador `0.5.0-model-trial`
rechaza por definición un archivo de distinto tamaño.

> **Actualización (2026-10-10).** Darksiders2DLL `0.8.1-deletion-trial`, con
> `models=override_shape`, acepta un `.2` con vértices o caras borrados en mallas
> con piel: lo reexpande al tamaño original colapsando en su sitio los
> triángulos borrados, y el motor recibe sus lecturas con los tamaños de
> siempre. Su README dice que se confirmó en el juego con `death_head.2`. Para
> saber qué carga el juego, mirar siempre el README y los registros de
> Darksiders2DLL, no este documento.

---

# Integración con el juego, animaciones y UV

## Darksiders II: copia nativa frente a carga del mod (2026-09-16)

> Estado de esa fecha. Las versiones posteriores del cargador (0.5.1, 0.6.0,
> 0.8.0 y 0.8.1) cambiaron el contrato; ver la actualización de «Borrar
> geometría en el `.2`».

La prueba de la cabeza confirma que exportar correctamente y cargar el reemplazo
son comprobaciones distintas. La copia llamada `death.2` en el mod tiene 148961
bytes y cuatro mallas, igual que `death_head.2`; cambia 71 vertices de la malla 3,
con desplazamiento maximo 1.17219925. El `death.2` original tiene 381861 bytes y
una sola malla: renombrar una cabeza como ese recurso no cambia su identidad.
El addon 1.1.1 propone el nombre de la fuente activa, conserva la carpeta elegida
y avisa de nombres diferentes; no modifica los archivos del juego.

El log local Darksiders2DLL-20260916-055706-20648-000.log declara
`version=0.4.1-diagnostic mode=dds_loader` y registra solo la mascara DDS. Esa DLL
ignora los .2. La preparacion local 0.5.0-model-trial admite solo XYZ dentro de
los limites locales calculados del original; todos los demas bytes deben ser
identicos. Su `offline_tests.exe --model-check` rechaza la copia editada con
`MODEL_REJECTED: non-position bytes changed`: el exportador recalcula normales y
tangentes. Renombrar la copia o cambiar solo la DLL no basta. Pendiente coordinar
el contrato del cargador y exportador, validar contra el paquete instalado y
observar/verificar la sustitucion real en el juego. No afirmar compatibilidad
por una relectura correcta en Blender ni relajar el validador a ciegas.

Fuentes locales: Darksiders2DLL/distribution/MODELOS_PRUEBA.md,
research/MODEL_TRIAL.md, registro de sesion y validador offline. Credito en
CREDITS.md; sin copiar codigo ni distribuir su DLL.

## Darksiders II Deathinitive — ANM v1 (2026-09-16)

Implementación propia en `io_darksiders/formats/animation.py`; plan y evidencia en
[`animaciones.md`](animaciones.md). Corpus local: 6.865 ANM, 134.862.492
bytes. No incluir assets ni bytes del ejecutable en git/ZIP.

- Cabecera 16 bytes: `ANM`, u8 versión=1, u8 endian=0, u8 FPS=30, u16 frames,
  u32 bytes después de cabecera, u32 flags (bit0 loop; bit1 semántica pendiente).
  Sigue nombre NUL. Duración `(frames-1)/FPS`. Pistas hasta EOF.
- Pista: u8 flags; bit0 nombre NUL o, si está apagado, alineación absoluta8 +
  u64 hash. Alinear2; i16 peso de mezcla. Preservar hashes originales del BOD
  en Bone.name_hash. Hay pistas duplicadas válidas: no sobrescribirlas callado.
- Rotación: u16 N; segmentos4B hasta sumar N. Bytes0..2: base sign6 en bits0..5
  y paso (1,2,4,16) en bits6..7. Byte3: componente omitida en bits6..7 y
  longitud menos1 en bits0..5. Siguen N*3 i8 residuos y N u8 deltas de frame;
  alinear2. La base se interpola hacia el siguiente segmento con tiempo
  acumulado, escalada256 y truncada como float32. Sumar residuo*paso y +128
  si flag0x20. Convertir i16 y multiplicar por float32(sqrt(2)/16384).
  Componente omitida positiva=sqrt(max(0,1-suma cuadrados)); XYZW.
- IMPORTANTE: el cuaternión ANM es pasivo respecto a Blender. Transponer su
  matriz activa (conjugar XYZ) al construir columnas. Sin ello el parser y
  comparaciones internas pueden pasar mientras la malla se retuerce.
- Posición (flag2): u16 N; segmentos8B `<Hhhh>` (longitud y bases) hasta N;
  N*3 i8 residuos y N u8 deltas; alinear2. Base*.5 + residuo*float32(.032).
  Multiplicar por1/32 con flag0x10. Evaluación lineal entre claves.
- Escala (flag4): u16 N, N*3 half, N u8 deltas; alinear2. Lineal.
- Escalar (flag8): u16 N, N u8/255, N u8 deltas. Efecto visual pendiente.
- Rotaciones interpolan SLERP por arco corto (NLERP para dot cercano a1).
  Las curvas son TRS locales absolutas al reproducir un clip aislado; convertir
  a matrix_basis con inversa de rest local real. Mantener giro frontal solo
  en raíces; no aplicarlo otra vez en cada hijo.
- Claves posteriores al último frame declarado son válidas para interpolación.
  No recortar los buffers a la duración. Rechazar tiempos repetidos dentro de
  una curva, segmentos con cuenta0/desbordada, valores no finitos y endian no
  implementado. Permitir importación parcial solo explícitamente y con avisos.
- Oráculo opcional: `scripts/qa_animation_native.py`, Unicorn/pefile
  (créditos en CREDITS). Comprueba SHA256 del EXE antes de usar RVAs y emula solo
  las rutinas matemáticas. Lector7c6a68, rot7ea584, pos7e9bcc, escala7e9ea4,
  escalar7ea3d0. No ejecutar el juego ni redistribuir binarios.

## Darksiders II Deathinitive — UV de la guadaña de Reaper (2026-09-18)

Las muestras locales `characters/death/reaperform_scythe.2` y
`characters/reapers/scythe_solo.2` tienen 2.252 vértices y 3.154 triángulos;
`characters/death/reaperform_ghostscythe.2` tiene 4.526 vértices y 6.308 triángulos.
En las tres, U original abarca [-0.372528, 1.991967] y V, tras la conversión de
signo para Blender, [0.005386, 0.990763]. Hay coordenadas fuera de [0,1] en el
archivo fuente: su presencia por sí sola no demuestra un fallo del importador.
`build_mesh_plan` conserva U y niega V; `roundtrip.py` revierte ese signo sin
empaquetar ni normalizar las islas. No hacer Pack Islands como reparación
automática: cambiaría la correspondencia con la textura del juego.

El nodo Image Texture de Blender permite repetir la imagen fuera del cuadrado
mediante Extension=Repeat (manual oficial, acreditado en CREDITS.md). Esto
explica cómo se pueden reutilizar partes de una textura con UV desplazadas;
no constituye una comprobación del sampler del juego. Esta revisión confirma
rangos nativos y transformación reversible, no la apariencia final: sigue
pendiente inspeccionar el modelo con su textura aplicada. La captura aportada
no identifica el nombre del archivo y solo muestra geometría/UV sin textura.

