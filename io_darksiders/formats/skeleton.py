"""Esqueleto y jerarquía de nodos de Darksiders 1 y 2 (`.o3d`).

El `.o3d` **no es una malla**: es el árbol de nodos de la escena (el esqueleto,
los enganches de VFX, las cámaras y los nodos de colisión) más la lista de qué
mallas cuelgan de qué nodo. La geometría vive aparte, en el `.2`
(`mesh.py`).

## El contenedor `BOD`

El archivo es un serializador de objetos **autodescriptivo**: cada cadena se
escribe una sola vez con su hash y recibe un id correlativo; a partir de ahí se
referencia por ese id. Por eso "Position" aparece una única vez en todo el
archivo aunque lo lleven ciento ochenta nodos.

Tras 16 bytes de cabecera (`BOD\\xfd` + versión + dos tamaños) va un byte 0x01 y
el objeto raíz, ya sin etiqueta::

    cadena  := 0x01 u64 hash u16 longitud bytes   (nueva, recibe el id siguiente)
             | 0x00 u32 id                        (referencia a una ya escrita)
    objeto  := 0x07 u32 id 0x01 cadena(clase) u32 nprops { cadena(nombre) valor }
    valor   := 0x01 u8 | 0x02 i32 | 0x03 f32 | 0x04 bool | 0x0f cadena
             | 0x0b u32 n { valor }               (array)
             | 0x09 u32 n u8 marca { valor }      (array con marca de tipo)
             | 0x07 ...                           (objeto anidado)
             | 0xff u32 id                        (referencia a un objeto ya escrito)

El objeto raíz es un `Object3D` con `Skeleton` (un `Skeleton3D`), `Visuals` y
`Bodies`. Los nodos del esqueleto son `Node3D` y sus propiedades interesantes
son `Name`, `Position`, `Rotation` y `Children`.

## Trampas verificadas sobre archivos reales

* `Position` y `Rotation` son **opcionales**: si faltan valen identidad. Un
  parser que las dé por hechas se desincroniza.
* Las etiquetas `0x04` (bool de un byte) y `0xff` (referencia a un objeto ya
  serializado, `u32` sin payload) son raras pero **no opcionales**: sin ellas se
  caen 7 de los 44 `.o3d` del corpus local de pruebas, y uno es `death.o3d`,
  el esqueleto del protagonista (255 nodos). Aparecen a mitad de archivo, así
  que el fallo no se ve hasta que se ha leído medio `.o3d`.
* `Rotation` son ángulos de Euler en **grados**, orden **XYZ**, y el motor usa
  convención de fila (`v' = v*M`). Verificado reproduciendo las 141 matrices de
  bind pose que el `.2` guarda por su cuenta: error máximo 0,023 sobre valores
  de cientos de unidades, es decir ruido de float32.
* El nivel superior es un **bosque**, no un único root: junto a
  `Bone_BE_Root` cuelgan nodos de VFX, cámaras y phantoms. La armadura de
  Blender lo admite tal cual, con varias raíces.

## Por qué esto también lee el `.o3d` de Darksiders 1

El script de referencia de Darksiders 1 busca la cadena `Node3D`, salta 19
bytes y lee registros **fijos de 116 bytes**. Esos números no son mágicos: son
justo lo que ocupan los tokens de este contenedor cuando el nodo trae las cinco
propiedades del esquema de Darksiders 1 y los nombres ya están internados::

    19 = propcount(4) + ref a Name(5) + etiqueta 0x0f(1) + strdef(1) + hash(8)
    97 = 3 x [ref(5) + cabecera de array(5) + 3 x (etiqueta + f32)(15)] + cola(22)

y la cola de 22 bytes es `ref a Children(5) + 0x09 + recuento(4) + marca(1) +
0x07 + id(4) + 0x01 + ref a la clase(5)`, de donde salen el `A[6]` del que ese
script saca el número de hijos y el `A[11] == 7` con el que decide si continuar.
`tests/test_skeleton.py` lo comprueba byte a byte.

O sea que **es el mismo formato**, y este parser lo lee sin casos especiales.
Además aguanta lo que al de offsets fijos lo rompe: nodos que omiten
propiedades, que es exactamente lo que hace Deathinitive.

La diferencia real entre los dos juegos es que los nodos de Darksiders 1 traen
`Scale` y los de Deathinitive no (verificado sobre los nueve `.o3d` del OBP de
Belial). Ausente vale 1, no 0.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

O3D_MAGIC = b"BOD\xfd"
HEADER_SIZE = 16

# Etiquetas de valor del contenedor BOD.
_TAG_U8 = 0x01
_TAG_I32 = 0x02
_TAG_F32 = 0x03
_TAG_BOOL = 0x04
_TAG_OBJECT = 0x07
_TAG_ARRAY = 0x09
_TAG_LIST = 0x0B
_TAG_STRING = 0x0F
_TAG_BACKREF = 0xFF

class O3dParseError(ValueError):
    """El archivo no encaja con el contenedor BOD de Deathinitive."""


@dataclass(frozen=True, slots=True)
class BackRef:
    """Referencia a un objeto ya serializado (etiqueta 0xff).

    No se resuelve porque ningún nodo del esqueleto llega por esta vía: basta
    con consumir sus cuatro bytes y devolver algo que no sea un ``dict``, que es
    lo que el recorrido de nodos ignora sin ruido.
    """

    object_id: int


@dataclass(frozen=True, slots=True)
class Bone:
    """Un nodo del árbol, ya aplanado con su índice de padre.

    ``parent`` es ``-1`` en las raíces. El orden garantiza
    ``parent < index``, que es lo que exige el paso 9 de la skill.
    """

    name: str
    parent: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    node_class: str = "Node3D"
    name_hash: int | None = None


@dataclass(slots=True)
class Skeleton:
    """Árbol de nodos del `.o3d` en orden padre-antes-que-hijo."""

    name: str
    bones: tuple[Bone, ...]
    path: Path | None = None
    _index: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self._index:
            self._index = {bone.name: i for i, bone in enumerate(self.bones)}

    def __len__(self) -> int:
        return len(self.bones)

    def index_of(self, name: str) -> int | None:
        return self._index.get(name)

    @property
    def roots(self) -> tuple[int, ...]:
        return tuple(i for i, b in enumerate(self.bones) if b.parent < 0)

    def local_matrices(self) -> np.ndarray:
        """Matrices locales 4x4 en convención de fila, una por hueso."""
        out = np.zeros((len(self.bones), 4, 4), dtype=np.float64)
        for i, bone in enumerate(self.bones):
            out[i] = _local_matrix(bone.position, bone.rotation, bone.scale)
        return out

    def world_matrices(self) -> np.ndarray:
        """Matrices de mundo en pose de descanso, compuestas de raíz a hoja.

        Como ``parent < index``, una sola pasada en orden basta.
        """
        local = self.local_matrices()
        world = np.empty_like(local)
        for i, bone in enumerate(self.bones):
            if bone.parent < 0:
                world[i] = local[i]
            else:
                world[i] = local[i].dot(world[bone.parent])
        return world

    def inverse_bind_matrices(self) -> np.ndarray:
        """`IBM = inverse(world)`.

        Es lo que el `.2` guarda por su cuenta para cada hueso de su paleta, y
        reproducirlo es la prueba de que la convención de Euler es la buena.
        """
        return np.linalg.inv(self.world_matrices())

    def validate(self) -> tuple[str, ...]:
        """Problemas estructurales del árbol. Vacío = árbol sano."""
        problems: list[str] = []
        for i, bone in enumerate(self.bones):
            if bone.parent >= i:
                problems.append(
                    f"hueso {i} ({bone.name}) apunta a un padre posterior ({bone.parent})"
                )
            if bone.parent < -1 or bone.parent >= len(self.bones):
                problems.append(f"hueso {i} ({bone.name}) con padre fuera de rango")
        names = [b.name for b in self.bones]
        if len(set(names)) != len(names):
            problems.append("hay nombres de hueso repetidos")
        return tuple(problems)


def _local_matrix(
    position: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> np.ndarray:
    """Matriz local 4x4 (fila) a partir de escala, Euler XYZ en grados y traslación.

    Los `.o3d` de Deathinitive no traen `Scale` en ningún nodo (verificado sobre
    los nueve del OBP de Belial), pero los de Darksiders 1 sí, así que se aplica
    cuando está. Ausente vale identidad, y entonces esta matriz es exactamente la
    misma que antes de contemplar la escala.
    """
    rx, ry, rz = (math.radians(a) for a in rotation)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, sx], [0, -sx, cx]], dtype=np.float64)
    my = np.array([[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]], dtype=np.float64)
    mz = np.array([[cz, sz, 0], [-sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    out = np.eye(4)
    out[:3, :3] = np.diag(scale).dot(mx).dot(my).dot(mz)
    out[3, :3] = position
    return out


class _BodReader:
    """Recorre el flujo de tokens del contenedor BOD."""

    __slots__ = ("data", "offset", "strings", "hashes")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = HEADER_SIZE
        self.strings: list[str] = []
        self.hashes: dict[str, int] = {}

    def u8(self) -> int:
        if self.offset >= len(self.data):
            raise O3dParseError("lectura fuera del archivo")
        value = self.data[self.offset]
        self.offset += 1
        return value

    def u32(self) -> int:
        if self.offset + 4 > len(self.data):
            raise O3dParseError(f"lectura fuera del archivo @{self.offset:#x}")
        value = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def i32(self) -> int:
        if self.offset + 4 > len(self.data):
            raise O3dParseError(f"lectura fuera del archivo @{self.offset:#x}")
        value = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return value

    def f32(self) -> float:
        if self.offset + 4 > len(self.data):
            raise O3dParseError(f"lectura fuera del archivo @{self.offset:#x}")
        value = struct.unpack_from("<f", self.data, self.offset)[0]
        self.offset += 4
        return float(value)

    def string(self) -> str:
        """Cadena nueva (con hash y texto) o referencia a una ya interna."""
        tag = self.u8()
        if tag == 0x01:
            if self.offset + 8 > len(self.data):
                raise O3dParseError(f"hash truncado @{self.offset:#x}")
            name_hash = struct.unpack_from("<Q", self.data, self.offset)[0]
            self.offset += 8
            if self.offset + 2 > len(self.data):
                raise O3dParseError(f"cadena truncada @{self.offset:#x}")
            length = struct.unpack_from("<H", self.data, self.offset)[0]
            self.offset += 2
            end = self.offset + length
            if end > len(self.data):
                raise O3dParseError(f"cadena de {length} bytes fuera del archivo")
            text = self.data[self.offset : end].rstrip(b"\0").decode("latin1")
            self.offset = end
            self.strings.append(text)
            if text in self.hashes and self.hashes[text] != name_hash:
                raise O3dParseError(f"hashes distintos para la cadena {text!r}")
            self.hashes[text] = name_hash
            return text
        if tag == 0x00:
            index = self.u32()
            if not 0 <= index < len(self.strings):
                raise O3dParseError(f"referencia a cadena {index} inexistente")
            return self.strings[index]
        raise O3dParseError(f"etiqueta de cadena {tag:#x} @{self.offset - 1:#x}")

    def value(self):
        tag = self.u8()
        if tag == _TAG_F32:
            return self.f32()
        if tag == _TAG_I32:
            return self.i32()
        if tag == _TAG_U8:
            return self.u8()
        if tag == _TAG_BOOL:
            return bool(self.u8())
        if tag == _TAG_BACKREF:
            return BackRef(self.u32())
        if tag == _TAG_STRING:
            return self.string()
        if tag == _TAG_LIST:
            return [self.value() for _ in range(self.u32())]
        if tag == _TAG_ARRAY:
            count = self.u32()
            self.u8()  # marca de tipo de elemento
            return [self.value() for _ in range(count)]
        if tag == _TAG_OBJECT:
            return self.obj(tagged=False)
        raise O3dParseError(f"etiqueta de valor {tag:#x} @{self.offset - 1:#x}")

    def obj(self, *, tagged: bool = True) -> dict:
        if tagged:
            tag = self.u8()
            if tag != _TAG_OBJECT:
                raise O3dParseError(f"se esperaba un objeto @{self.offset - 1:#x}")
        self.u32()  # id de instancia
        self.u8()  # marca constante 0x01
        node: dict = {"class": self.string()}
        for _ in range(self.u32()):
            key = self.string()
            node[key] = self.value()
        return node


def parse_object3d(data: bytes, *, hashes: dict[str, int] | None = None) -> dict:
    """Lee el descriptor completo, incluso cuando no contiene Skeleton."""
    if not data.startswith(O3D_MAGIC):
        raise O3dParseError("no empieza por BOD")

    reader = _BodReader(data)
    reader.u8()  # marca constante que precede al objeto raíz
    root: dict = {"class": reader.string()}
    for _ in range(reader.u32()):
        key = reader.string()
        root[key] = reader.value()

    if reader.offset != len(data):
        raise O3dParseError(
            f"sobran {len(data) - reader.offset} bytes tras el objeto raíz"
        )
    if hashes is not None:
        hashes.update(reader.hashes)
    return root


def parse_o3d(data: bytes, path: Path | None = None) -> Skeleton:
    """Lee un `.o3d` completo. Lanza ``O3dParseError`` si no cuadra."""
    hashes: dict[str, int] = {}
    root = parse_object3d(data, hashes=hashes)

    scene = root.get("Skeleton")
    if not isinstance(scene, dict):
        raise O3dParseError("el objeto raíz no trae Skeleton")

    bones: list[Bone] = []

    def walk(node: dict, parent: int) -> None:
        if not isinstance(node, dict):
            return
        name = node.get("Name")
        if not isinstance(name, str):
            return
        index = len(bones)
        bones.append(
            Bone(
                name=name,
                parent=parent,
                position=_vec3(node.get("Position")),
                rotation=_vec3(node.get("Rotation")),
                scale=_vec3(node.get("Scale"), default=1.0),
                node_class=str(node.get("class", "Node3D")),
                name_hash=hashes.get(name),
            )
        )
        for child in node.get("Children") or ():
            walk(child, index)

    for child in scene.get("Children") or ():
        walk(child, -1)

    if not bones:
        raise O3dParseError("el Skeleton no trae ningún nodo")

    name = scene.get("Name")
    return Skeleton(
        name=name if isinstance(name, str) else (path.stem if path else "skeleton"),
        bones=tuple(bones),
        path=path,
    )


def _vec3(value, *, default: float = 0.0) -> tuple[float, float, float]:
    """Position/Rotation/Scale ausentes valen identidad, no cero por accidente.

    De ahí el *default*: la identidad de la escala es 1, no 0. Un nodo sin
    `Scale` al que se le asignara (0, 0, 0) colapsaría toda su descendencia.
    """
    if not isinstance(value, list) or len(value) != 3:
        return (default, default, default)
    return (float(value[0]), float(value[1]), float(value[2]))


def load_o3d(path: Path) -> Skeleton:
    path = Path(path)
    return parse_o3d(path.read_bytes(), path=path)


def find_o3d(mesh_path: Path) -> Path | None:
    """Localiza el `.o3d` que acompaña a un `.2` (mismo nombre, misma carpeta)."""
    mesh_path = Path(mesh_path)
    for candidate in (
        mesh_path.with_suffix(".o3d"),
        mesh_path.with_suffix(".O3D"),
        mesh_path.with_suffix(".1"),
    ):
        if candidate.is_file():
            return candidate
    return None


def load_skeleton(mesh_path: Path) -> Skeleton | None:
    """El esqueleto de una malla, o ``None`` si falta o no se puede leer.

    Es material opcional: sin él la malla entra igual, con sus grupos de
    vértices pero sin armadura.
    """
    candidate = find_o3d(mesh_path)
    if candidate is None:
        return None
    try:
        return load_o3d(candidate)
    except (O3dParseError, OSError):
        return None


def _rig_candidates(
    mesh_path: Path, search_root: Path | None, max_files: int
) -> list[Path]:
    """`.o3d` a puntuar, del más probable al menos: hermano, carpeta, árbol."""
    folder = mesh_path.parent
    ordered: dict[Path, None] = {}

    same_name = find_o3d(mesh_path)
    if same_name is not None:
        ordered[same_name] = None

    roots = [folder]
    fallback = search_root if search_root is not None else folder.parent
    if fallback != folder:
        roots.append(fallback)

    for depth, root in enumerate(roots):
        if not root.is_dir():
            continue
        walk = sorted(root.glob("*")) if depth == 0 else sorted(root.rglob("*"))
        for path in walk:
            if len(ordered) >= max_files:
                return list(ordered)
            if path.is_file() and path.suffix.casefold() == ".o3d":
                ordered[path] = None
    return list(ordered)


def find_rig(
    mesh_path: Path,
    bone_names: Sequence[str],
    *,
    search_root: Path | None = None,
    max_files: int = 400,
) -> tuple[Path | None, int]:
    """Localiza el `.o3d` que más huesos cubre de la paleta de una malla.

    `find_o3d` empareja por nombre, y acierta cuando el rig se llama igual que
    la malla (``death.2`` -> ``death.o3d``). El problema es que la mayoría de
    los `.o3d` hermanos **no traen** ``Skeleton``: son objetos solo visuales, y
    son 21 de los 44 del corpus local de pruebas. Ahí `load_skeleton` devuelve
    ``None`` y la malla entra sin armadura.

    **Todavía no lo usa el importador**, que pide el esqueleto externo a mano:
    cablearlo es la mejora pendiente descrita en `docs/aprendizajes.md`.

    El rig suele estar en otro archivo, a veces en **otra carpeta**: las mallas
    de ``characters/skeleton/`` se riggean con
    ``characters/skeleton_common/skeleton_base.o3d``. Por eso aquí se puntúa por
    huesos cubiertos y no por nombre.

    Devuelve ``(ruta, huesos_cubiertos)``, o ``(None, 0)`` si ningún `.o3d` del
    ámbito cubre un solo hueso: eso significa que el rig no está extraído, no
    que esté roto.
    """
    names = list(bone_names)
    if not names:
        return (None, 0)

    best_path: Path | None = None
    best_score = 0
    for candidate in _rig_candidates(mesh_path, search_root, max_files):
        try:
            skeleton = load_o3d(candidate)
        except Exception:  # noqa: BLE001 - un .o3d ilegible no corta la búsqueda
            continue
        score = sum(1 for name in names if skeleton.index_of(name) is not None)
        if score > best_score:
            best_path, best_score = candidate, score
            if best_score == len(names):
                break
    return (best_path, best_score)
