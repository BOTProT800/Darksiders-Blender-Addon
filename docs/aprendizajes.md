# Aprendizajes y mejoras pendientes

Lo que dejó el trabajo sobre Darksiders entre el 2026-08-27 y el 2026-10-10,
cuando vivía en ANANSI, y lo que queda por hacer. Las reglas del formato, con
sus cifras, están en [`formato.md`](formato.md); aquí está lo que **no** es una
regla del formato: qué costó encontrar, qué errores se cometieron y qué
conviene hacer a continuación.

Se mantiene vivo: después de cada hallazgo o de cada fallo con lección, se
añade aquí (o en `formato.md` si es una regla del formato), no en el chat.

## 1. Del formato: lo que costó encontrar

**La redundancia del propio archivo es el mejor oráculo.** Casi cada acierto
salió de una comprobación que el archivo verifica solo, sin adivinar:

- la cabecera del `.2` tiene que terminar exactamente en `data_offset`: es lo
  que delató que `head[3]` y `head[4]` intercambian su significado entre mallas
  estáticas y con piel;
- la AABB declarada contra la leída localizó el bloque de vértices por fuerza
  bruta en seis mallas a la vez, con error 0,004;
- el lector del `.o3d` tiene que **consumir el archivo entero**, hasta el
  último byte;
- en el OBP, el `u32` que precede al blob coincide con la suma de la tabla de
  tamaños: es el tamaño **descomprimido**, no el comprimido.

**Un oráculo puede depender del espacio en que está expresado.** La AABB de las
piezas de equipo está en el espacio del hueso al que se acoplan
(`BBFollowNode`), no en el de la malla, y rechazaba mallas sanas. Hizo falta una
señal independiente del espacio, las normales unitarias, para que el desajuste
pase a aviso en lugar de error.

**La caja también valida una búsqueda.** El nodo de la caja puede no estar en la
paleta de la malla (`Bone_MM_Root` en `makermale_elder.2`): entonces hay que
sacarlo de un esqueleto, y el esqueleto sale de buscar por la carpeta. Aceptarlo
solo si la geometría original cabe en la caja declarada convierte esa búsqueda en
algo verificable: en el corpus no hubo ni un caso ambiguo (2026-10-13).

**Falsos amigos que cuestan una vuelta:**

- el `0xFFFFFFFF` al final de cada registro de atributos no es un centinela de
  stride, es el color por vértice (las mallas de VFX lo usan);
- `head[1]` vale 60 o 64, pero el stride real es 76 o 92;
- un porcentaje bajo de UV en `[0,1]` no significa UV rotas: hay tiling a
  propósito (la guadaña de Reaper va de U −0,37 a 1,99). No reparar con
  *Pack Islands*: rompería la correspondencia con la textura del juego;
- UV que se solapan en el editor no son una malla ajena colada: cada tramo de
  material ocupa el cuadro [0,1] de **su** textura. En
  `makermale_elder.2`, malla 1, `elder_b` (cuerpo, 10.608 caras,
  `ElderB_diff` 2048×2048) y `elder_a` (cabeza, barba y pelo, 2.602 caras,
  `ElderA_diff` 512×1024) se pisan con *UV Sync Selection*; con cada textura
  en su material el render sale correcto (2026-10-13). Los 8 bytes entre la UV
  y el color del registro de atributos con piel (@+8) tampoco son una segunda
  UV: valen −69..75 en esa malla y su significado sigue sin saberse;
- «UV espejadas comparten un vértice» no delataba una edición inválida. Los
  assets traen triángulos de UV doblados de origen (unas 371 de las 1.624
  mallas con piel) y el `.2` guarda un solo signo de bitangente por vértice;
  el exportador rechazaba por eso **cualquier** edición de posición o UV de
  esos modelos, aunque fuera lejos del pliegue. Desde el 2026-10-13 conserva
  el signo de la plantilla.

**Un lector de offsets fijos que «funciona» puede estar leyendo un formato
autodescriptivo.** El script de 2014 lee el `.o3d` de Darksiders 1 como
registros fijos de 116 bytes, y esos 116 bytes son exactamente los tokens del
contenedor `BOD` cuando el esquema es constante. En Deathinitive las
propiedades son opcionales y el mismo script se desincroniza. Descomponer los
saltos mágicos en tokens ahorró escribir un segundo parser.

**Ausente no es cero.** `Position` y `Rotation` ausentes valen identidad;
`Scale` ausente vale 1. Una escala (0, 0, 0) colapsaría toda la descendencia.

**Lo raro no es opcional.** Las etiquetas `0x04` (bool) y `0xff` (referencia a
objeto) aparecen en 7 de 44 `.o3d`, entre ellos `death.o3d`, el esqueleto del
protagonista, y a mitad de archivo.

**La convención de rotación se demuestra contra datos independientes.** Euler
XYZ en grados con convención de fila reproduce las 141 matrices de bind pose
que el `.2` guarda por su cuenta (error 0,023 sobre valores de cientos); las
otras cinco permutaciones dan errores de 100 a 200.

**Decodificar bien no valida la convención.** Los cuaterniones del `.anm` se
leían con las cuatro componentes exactas y aun así la malla se retorcía: el
cuaternión es pasivo respecto al de Blender. Solo se vio mirando la malla
animada. Hay un test sintético para que no vuelva.

**El corpus decide antes de escribir.** Borrar geometría solo se permitió
después de medir las reglas en los 6.421 `.2` legibles (23.327 mallas, 34.939
tramos de material) sin una sola excepción.

**Datos sueltos que conviene no redescubrir:** hay clips con pistas de
identificador repetido (94) y claves posteriores a la duración declarada, y las
dos cosas son válidas; el registro con piel duplica `normal.z` en +32; el signo
de la tangente coincide con la UV de Blender (`u, −v_juego`), no con la del
juego; `Visuals[].Checksum` del `.o3d` no es CRC32, Adler32 ni MD5 del `.2`, de
su bloque ni de sus índices; los `.obp` de 4096 bytes de algunas salas de
`world/environments` están vacíos (0 grupos, 0 recursos).

## 2. Del proceso

**Exportar bien no es cargar en el juego.** Una copia `.2` que se relee
perfecta en Blender puede ser rechazada por el cargador. Son dos contratos (el
del addon y el de Darksiders2DLL) y hay que coordinarlos. La documentación de
uno se desfasa respecto al otro: dos veces se citó aquí una versión del cargador
que ya no era la vigente. Para saber qué carga el juego, mirar el README y los
registros de Darksiders2DLL (`%LOCALAPPDATA%\Darksiders2DLL\logs`), nunca la
documentación de este addon.

**El nombre es la identidad del recurso.** La primera prueba del mod falló porque
una cabeza editada (`death_head.2`, 148.961 bytes, cuatro mallas) se guardó como
`death.2` (381.861 bytes, una malla): renombrar no cambia qué es. Desde la
1.1.1 el exportador propone el nombre original y avisa si se cambia. Los mods
van bajo la misma ruta interna del juego (`mods/<mod>/media/characters/...`).

**Primero la vuelta sin cambios.** Ningún escritor está listo hasta que una
exportación sin ediciones reproduce el original byte a byte. Es la prueba más
barata y la que más errores atrapa.

**Parchear la plantilla en vez de reconstruir.** `native.py` copia el original y
solo toca buffers verificados; los bloques desconocidos viajan intactos. Antes
de escribir, relee la copia y la compara campo a campo; escribe en un temporal y
lo mueve con `os.replace`; nunca sobreescribe el original.

**Dos exportaciones iguales no salen idénticas al último bit.** Con la misma
edición, `calc_tangents` de Blender devuelve tangentes que varían hasta 1,2e-7
entre ejecuciones (1.332 de `makermale_elder.2`). La vuelta sin cambios sigue
siendo byte idéntica porque no recalcula tangentes; las pruebas con ediciones
comparan las tangentes con tolerancia y el resto byte a byte.

**Recalcular no es reescribir.** Al editar, el exportador recalculaba el signo
de bitangente de **todos** los vértices con el método de Blender, que no es el
del juego, y una edición de un vértice cambiaba 58 signos repartidos por la malla.
Medir el corpus dio el método del juego (Lengyel, 99,86 %), pero usarlo tal cual
habría cambiado el 0,14 % restante. Lo fiel es relativo: conservar el dato
original y aplicar solo el **cambio** que produce la edición según el método del
juego (2026-10-14).

**Rechazar con un mensaje que nombre el objeto** es mejor que exportar algo que
no se sabe representar: fusionar, subdividir, una costura UV nueva, materiales
reasignados, una pose que no es la de descanso.

**El doble de `bpy` prueba el cableado, no Blender.** Los tests con
`tests/_fake_bpy.py` cazan erratas y orden de llamadas; lo que hace Blender de
verdad solo se ve en Blender. QA con Blender real, `--factory-startup` y
`BLENDER_USER_RESOURCES` en un temporal, sobre assets reales y con render.
Comparar dos rutas de código que comparten una convención equivocada da un falso
positivo: mirar la malla es obligatorio.

**Un oráculo externo, sin ejecutar el juego.** Para el `.anm` se emularon con
Unicorn solo las rutinas matemáticas del ejecutable instalado, comprobando antes
el SHA-256 del build para no usar direcciones de otro. Se contrastaron 1.242.372
evaluaciones.

**Los avisos van también a la consola.** `Operator.report` no escribe en stdout
y en una sesión con interfaz los avisos se pierden al siguiente clic.

**Preferencias que nadie entiende son un defecto.** Hasta la 1.3.0 el addon
tenía una «Carpeta de ANANSI» y buscaba los parsers por variables de entorno y
carpetas padre. El usuario lo encontró sin saber para qué servía. De ahí las dos
reglas que no se negocian: autosuficiente y con botón de desinstalar (en
[`../AGENTS.md`](../AGENTS.md)). La desinstalación se aplaza con un temporizador
(hacerla dentro del operador desregistra su propia clase) y no se ofrece desde
el código fuente enlazado: borraría el repositorio siguiendo el enlace.

**Copiar parsers desde otro repositorio cuesta más que tenerlos dentro.** En
ANANSI el empaquetador copiaba los parsers a `_vendor/` con una lista a mano, y
el addon registraba un paquete `services` falso para no ejecutar el
`__init__.py` de ANANSI, que arrastraba dependencias que Blender no trae. Al
independizarse, los parsers viven en `io_darksiders/formats/`, el paquete no
importa `bpy` a nivel de módulo (para poder probarlo sin Blender) y el
empaquetador solo comprueba que nada importe algo ajeno.

**Lo que se guarda en el `.blend` no se renombra.** Las propiedades siguen
llamándose `anansi_*` en la 2.0.0: cambiarles el nombre habría dejado sin poder
exportar todas las escenas importadas con la 1.x. En cambio, cambiar el id de la
extensión (`io_anansi_darksiders` → `io_darksiders`) obliga a desinstalar la
versión vieja una vez, porque para Blender es otro addon.

**Los nombres que vienen del archivo son datos.** El OBP aplanaba separadores,
pero dejaba pasar `C:x` (una letra de unidad se sale de la carpeta aunque no
lleve barra), nombres de dispositivo (`CON`) y duplicados que se pisaban. Desde
la 2.0.0 se sanean y los duplicados reciben sufijo.

**Cuadrar las cuentas.** Cada recurso declarado por el OBP acaba escrito,
filtrado o fallido con motivo; si no suman, es un fallo del código.

**Entorno de esta máquina.** Blender de Steam
(`C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe`) no está en
el `PATH`; el de `Program Files\Blender Foundation` es un 2.79 que no sirve. El
error `load_post[3]: TypeError` al arrancar es de otro addon instalado
(`sins2-blender-extension`), no de este. Las muestras del juego están en
`C:\Users\vicen\Documents\Extractions\Darksiders\media\`.

## 3. Mejoras pendientes

Por orden de lo que más ayudaría a quien usa el addon:

1. **Encontrar el esqueleto solo.** Hoy, para `death_head.2` o los guantes hay
   que elegir `death.o3d` a mano en «Esqueleto externo». `skeleton.find_rig` ya
   puntúa los `.o3d` de la carpeta y de las de alrededor por huesos cubiertos
   (21 de 44 `.o3d` son solo visuales, y las mallas de `characters/skeleton/` se
   riggean con `skeleton_common/skeleton_base.o3d`), está probado y no está
   cableado en el importador.
2. **La variante estática de 60 bytes por vértice.** `parse_dcm` supone siempre
   76 B/vértice en las mallas estáticas, pero 450 mallas de 71 archivos no
   llevan la cola de 16 bytes. Posiciones y UV salen bien porque van delante,
   pero la cola se lee del bloque siguiente. La pareja `{i32 + f32}` de la
   cabecera dice cuál es cuál (`(0, 0.0)` = sin cola).
3. **Avisos de AABB que asustan sin motivo.** Cada pieza de equipo avisa de que
   su caja no cuadra, aunque el exportador ya sabe resolver su `BBFollowNode`.
   Comprobar la caja en ese espacio al importar y avisar solo si tampoco cuadra.
   Queda un residuo de 0,46 sin explicar en `necroa01_shoulder.2`.
4. **Exportar `.anm`**: primero una vuelta sin cambios byte idéntica, después
   ediciones validadas numéricamente, y por último la carga con Darksiders2DLL
   (la 0.8.0 ya observa las lecturas de animaciones).
5. **Animaciones completas**: pistas sin hueso (Belial 2–8, Death 5–19),
   precedencia de pistas duplicadas, el flag de cabecera `0x2`, el canal
   escalar, las mezclas de capas.
6. **Editar mallas estáticas**: su registro de vértice no está verificado
   (normales y tangentes).
7. **Validar en Blender 4.2/4.3**, que usa la rama de acciones sin slots.
8. **Formatos que faltan**: geometría de Darksiders 1 (`.oppc`/`.meshpack`/
   `.mesh`, hace falta un `.mesh` real para decidir entre las dos referencias),
   el `.dcm` de 2012 y la capa `.upak`/`manifest.bin` (hace falta un
   `manifest.bin` real).

**Fuera de alcance por decisión (2026-10-10):** texturas (`.dds`), materiales
(`.bmat`) y datos de motor. El addon solo trata modelos, esqueletos y
animaciones; del OBP saca únicamente `.2`, `.o3d` y `.anm`. Los conversores a
OBJ y GLB fuera de Blender también se dejaron: los exportadores de Blender
cubren eso. Añadir vértices o costuras nuevas exigiría inventar registros que
el original no tiene, y sigue fuera.
