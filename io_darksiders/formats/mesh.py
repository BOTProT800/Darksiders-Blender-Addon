"""Geometría `.2` de Darksiders II: Deathinitive Edition.

El `.2` es la variante Deathinitive del `.dcm` del Darksiders II de 2012: misma
cabecera, mismas tablas y las mismas dos rutas (`mesh_type` 0 estática,
1 con piel). Lo que cambia es el **ancho de los bloques de atributos**, y ahí es
donde se rompen los scripts de la comunidad escritos para el juego original.

## Cabecera (little-endian)

::

    u32 data_offset          fin de la cabecera / base de los bloques de datos
    u32 mesh_count
    u32 n  + u32 index_count[n]
    u32 n  + i32 vertex_count[n]      (valor absoluto; + con piel, - estática)
    i32 n                             (valor absoluto; n = mallas)
    { i32 + f32 } x n                 una pareja por malla, ver abajo
    por malla:
        u8   mesh_type                0 estática, 1 con piel
        i32  head[5]
             head[0] = vértices
             head[1] = 60 (estática) / 64 (con piel)
             head[2] = offset del bloque de datos, relativo a data_offset
             head[3] = materiales (piel) / recuento auxiliar (estática)
             head[4] = huesos (piel)   / materiales (estática)
        f32  bbox[6]                  min xyz, max xyz
        si mesh_type == 1: head[4] huesos
            u8 marca, u16 longitud, nombre, f32 m[16], u16 id, u16 relleno
        materiales:
            u8 marca; si marca == 255: u16 longitud + nombre
            i32 e[6] con nombre, i32 e[5] sin él
            e = (índice, 0, vértices, primer vértice, índices, primer índice)

`head[3]`/`head[4]` **intercambian su significado** entre las dos rutas. Se
verifica solo: al terminar la cabecera el cursor tiene que caer exactamente en
`data_offset`, y es lo que comprueba `parse_dcm`.

Medido sobre los 6421 `.2` legibles del corpus (23 327 mallas, 34 939 tramos):

* La pareja `{i32 + f32}` de una malla con piel es siempre `(0, 0.0)`. En una
  estática vale `(vértices, factor)` cuando su bloque lleva la cola de 16 B por
  vértice (76 B/vértice, 21 253 mallas) y `(0, 0.0)` cuando no la lleva
  (60 B/vértice, 450 mallas). `parse_dcm` todavía supone la cola siempre.
* Los tramos de material son contiguos en el búfer de índices, nunca vacíos y
  lo cubren entero; su rango de vértices es exactamente `[mín, máx]` de sus
  índices, no se solapan entre tramos y teselan `[0, n)`. No hay vértices sin
  triángulo. `native.py` se apoya en esto para borrar.
* El primer bloque empieza en `data_offset`, los bloques van en el orden de las
  mallas y no queda nada tras el último.

## Bloques de datos

En `data_offset + head[2] - 8` empieza el bloque de la malla::

    u32 bytes de índices, u32 tamaño de índice (2)
    u16 índices[index_count]
    con piel (92 B/vértice):
        n x 20  atributos: f32 u @+0, f32 v @+4, RGBA8 color @+16
        n x 52  vértices:  u32 nº de influencias @+0, f32 posición @+4
        n x 16  pesos f32[4]
        n x  4  huesos u8[4]  (índices **locales** a la lista de la malla)
    estática (76 B/vértice):
        n x 12  posiciones f32[3]
        n x 48  atributos: f32 u @+28, f32 v @+32, RGBA8 color @+44
        n x 16  cola

El `.dcm` de 2012 usaba 12 bytes de atributos con piel y 20 sin ella, con UV en
half-float. Deathinitive los ensanchó a 20 y 48 y pasó las UV a float32.

El campo del final de cada registro de atributos vale 0xFFFFFFFF en casi todas
las mallas y es tentador tomarlo por un centinela de stride, pero es el **color
por vértice**: las mallas de VFX lo usan de verdad. Para validar el stride se
compara la AABB leída contra la que declara la cabecera (`_check_bbox`), que no
tiene ese falso positivo.

La V viene negada (rango -1..0) y el winding invertido, igual que en el juego
original. El motor es Z-up como Blender, así que las posiciones entran tal cual;
el giro frontal opcional lo aplica `plan.py`, no este módulo.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .quality import MeshHealth, calculate_mesh_health

# Anchos verificados sobre archivos reales de Deathinitive.
SKINNED_ATTR_STRIDE = 20
SKINNED_VERTEX_STRIDE = 52
SKINNED_WEIGHT_STRIDE = 16
SKINNED_JOINT_STRIDE = 4
# 20 + 52 + 16 + 4: la suma que tiene que cuadrar con el hueco entre mallas.
SKINNED_TOTAL_STRIDE = 92

STATIC_POSITION_STRIDE = 12
STATIC_ATTR_STRIDE = 48
STATIC_TAIL_STRIDE = 16
# 12 + 48 + 16.
STATIC_TOTAL_STRIDE = 76

# Normal (3 float32) dentro del registro de vértice skinned. Solo se conoce
# para esta ruta: en la estática no hay archivo de muestra que lo confirme.
SKINNED_NORMAL_OFFSET = 20

# Desplazamiento de la UV dentro del bloque de atributos de cada ruta.
SKINNED_UV_OFFSET = 0
STATIC_UV_OFFSET = 28

# Color por vértice (RGBA8) al final de cada registro de atributos.
COLOUR_BYTES = 4

MESH_TYPE_STATIC = 0
MESH_TYPE_SKINNED = 1


class DcmParseError(ValueError):
    """El archivo no encaja con la geometría `.2` de Deathinitive."""


@dataclass(frozen=True, slots=True)
class DcmBone:
    """Hueso declarado por la malla, con su matriz de bind pose inversa.

    Los índices de `JOINTS_0` del `.2` son **locales a esta lista**: es la
    paleta de huesos de la submalla. Para exportar hay que traducirlos contra el
    esqueleto global del `.o3d`, y de eso se encarga `skin.py`.
    """

    name: str
    inverse_bind: tuple[float, ...]
    bone_id: int


@dataclass(frozen=True, slots=True)
class DcmSubmesh:
    """Tramo de la malla que comparte material."""

    name: str
    material_index: int
    vertex_start: int
    vertex_count: int
    index_start: int
    index_count: int
    # Offset de la entrada (su byte de marca) para reescribir los tramos al
    # borrar geometría. No forma parte de la identidad del tramo.
    offset: int = field(default=-1, compare=False)


@dataclass(slots=True)
class DcmMesh:
    """Una malla del `.2`, ya decodificada."""

    index: int
    mesh_type: int
    positions: np.ndarray  # (n, 3) float32
    uvs: np.ndarray  # (n, 2) float32
    indices: np.ndarray  # (m, 3) int32
    bbox: tuple[float, ...]
    bones: tuple[DcmBone, ...] = ()
    submeshes: tuple[DcmSubmesh, ...] = ()
    joints: np.ndarray | None = None  # (n, 4) uint8, índices locales
    weights: np.ndarray | None = None  # (n, 4) float32
    influences: np.ndarray | None = None  # (n,) uint32
    colours: np.ndarray | None = None  # (n, 4) uint8, color por vértice
    warnings: tuple[str, ...] = ()  # avisos del parseo, no fatales
    normals: np.ndarray | None = None
    # Offsets del archivo fuente para la escritura conservadora de .2.
    data_base: int = -1
    bbox_offset: int = -1

    @property
    def is_skinned(self) -> bool:
        return self.mesh_type == MESH_TYPE_SKINNED and self.joints is not None

    @property
    def vertex_count(self) -> int:
        return int(len(self.positions))

    @property
    def triangle_count(self) -> int:
        return int(len(self.indices))

    def health(self) -> MeshHealth:
        return calculate_mesh_health(self.positions, self.indices, self.uvs)


@dataclass(slots=True)
class DcmFile:
    """El `.2` entero: todas sus mallas y de dónde salió."""

    path: Path | None
    meshes: tuple[DcmMesh, ...]

    @property
    def main_mesh(self) -> DcmMesh | None:
        """La malla con más triángulos: el LOD principal, no un decal."""
        return max(self.meshes, key=lambda m: m.triangle_count, default=None)


class _Reader:
    __slots__ = ("data", "offset")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def u8(self) -> int:
        if self.offset >= len(self.data):
            raise DcmParseError("lectura fuera del archivo")
        value = self.data[self.offset]
        self.offset += 1
        return value

    def u16(self) -> int:
        if self.offset + 2 > len(self.data):
            raise DcmParseError(f"lectura fuera del archivo @{self.offset:#x}")
        value = struct.unpack_from("<H", self.data, self.offset)[0]
        self.offset += 2
        return value

    def i32(self, count: int = 1) -> tuple[int, ...]:
        end = self.offset + 4 * count
        if end > len(self.data):
            raise DcmParseError(f"lectura fuera del archivo @{self.offset:#x}")
        values = struct.unpack_from(f"<{count}i", self.data, self.offset)
        self.offset = end
        return values

    def f32(self, count: int = 1) -> tuple[float, ...]:
        end = self.offset + 4 * count
        if end > len(self.data):
            raise DcmParseError(f"lectura fuera del archivo @{self.offset:#x}")
        values = struct.unpack_from(f"<{count}f", self.data, self.offset)
        self.offset = end
        return values

    def text(self, length: int) -> str:
        end = self.offset + length
        if length < 0 or end > len(self.data):
            raise DcmParseError(f"cadena inválida ({length}) @{self.offset:#x}")
        raw = self.data[self.offset : end]
        self.offset = end
        return raw.rstrip(b"\0").decode("latin1")

    def skip(self, count: int) -> None:
        end = self.offset + count
        if end > len(self.data):
            raise DcmParseError(f"salto fuera del archivo @{self.offset:#x}")
        self.offset = end


def parse_dcm(data: bytes, path: Path | None = None) -> DcmFile:
    """Lee un `.2` entero. Lanza ``DcmParseError`` si algo no cuadra."""
    reader = _Reader(data)
    data_offset, mesh_count = reader.i32(2)
    if not 0 < mesh_count <= 4096:
        raise DcmParseError(f"{mesh_count} mallas, fuera de rango")
    if not 0 < data_offset <= len(data):
        raise DcmParseError(f"data_offset {data_offset} fuera del archivo")

    index_counts = reader.i32(reader.i32()[0])
    vertex_counts = [abs(v) for v in reader.i32(abs(reader.i32()[0]))]
    for _ in range(abs(reader.i32()[0])):
        reader.i32()
        reader.f32()

    if len(index_counts) < mesh_count or len(vertex_counts) < mesh_count:
        raise DcmParseError(
            f"tablas cortas: {len(index_counts)} índices y "
            f"{len(vertex_counts)} vértices para {mesh_count} mallas"
        )

    headers: list[tuple[int, tuple[int, ...], tuple[float, ...], tuple[DcmBone, ...], tuple[DcmSubmesh, ...]]] = []
    bbox_offsets = []
    for _ in range(mesh_count):
        mesh_type = reader.u8()
        head = reader.i32(5)
        bbox_offsets.append(reader.offset)
        bbox = reader.f32(6)
        if mesh_type == MESH_TYPE_SKINNED:
            bone_count, material_count = head[4], head[3]
        else:
            bone_count, material_count = 0, head[4]
        if not 0 <= bone_count <= 4096 or not 0 <= material_count <= 4096:
            raise DcmParseError(
                f"malla con {bone_count} huesos y {material_count} materiales"
            )

        bones: list[DcmBone] = []
        for _ in range(bone_count):
            reader.u8()
            name = reader.text(reader.u16())
            matrix = reader.f32(16)
            bone_id = reader.u16()
            reader.u16()
            bones.append(DcmBone(name=name, inverse_bind=matrix, bone_id=bone_id))

        submeshes: list[DcmSubmesh] = []
        for _ in range(material_count):
            entry = reader.offset
            marker = reader.u8()
            name = reader.text(reader.u16()) if marker == 255 else ""
            fields = reader.i32(6 if marker == 255 else 5)
            submeshes.append(
                DcmSubmesh(
                    name=name,
                    material_index=fields[0],
                    vertex_count=fields[2],
                    vertex_start=fields[3],
                    index_count=fields[4],
                    index_start=fields[5] if len(fields) > 5 else 0,
                    offset=entry,
                )
            )
        headers.append((mesh_type, head, bbox, tuple(bones), tuple(submeshes)))

    # La cabecera tiene que terminar justo en data_offset. Es la comprobación
    # que delata un reparto equivocado de head[3]/head[4].
    if reader.offset != data_offset:
        raise DcmParseError(
            f"la cabecera termina en {reader.offset} y data_offset es {data_offset}"
        )

    meshes: list[DcmMesh] = []
    for i, (mesh_type, head, bbox, bones, submeshes) in enumerate(headers):
        meshes.append(
            _read_mesh_data(
                data,
                index=i,
                mesh_type=mesh_type,
                base=data_offset + head[2] - 8,
                vertex_count=head[0] or vertex_counts[i],
                index_count=index_counts[i],
                bbox=bbox,
                bones=bones,
                submeshes=submeshes,
            )
        )
    for mesh, offset in zip(meshes, bbox_offsets):
        mesh.bbox_offset = offset
    return DcmFile(path=path, meshes=tuple(meshes))


def _read_mesh_data(
    data: bytes,
    *,
    index: int,
    mesh_type: int,
    base: int,
    vertex_count: int,
    index_count: int,
    bbox: tuple[float, ...],
    bones: tuple[DcmBone, ...],
    submeshes: tuple[DcmSubmesh, ...],
) -> DcmMesh:
    if index_count % 3:
        raise DcmParseError(
            f"malla {index}: {index_count} índices no son múltiplo de 3"
        )
    stride = SKINNED_TOTAL_STRIDE if mesh_type == MESH_TYPE_SKINNED else STATIC_TOTAL_STRIDE
    needed = 8 + index_count * 2 + vertex_count * stride
    if base < 0 or base + needed > len(data):
        raise DcmParseError(
            f"malla {index}: necesita {needed} bytes en {base} y el archivo tiene {len(data)}"
        )

    declared_bytes, index_size = struct.unpack_from("<2I", data, base)
    if index_size != 2 or declared_bytes != index_count * 2:
        raise DcmParseError(
            f"malla {index}: cabecera de datos incoherente "
            f"({declared_bytes} bytes, elemento de {index_size})"
        )

    cursor = base + 8
    indices = np.frombuffer(data, dtype="<u2", count=index_count, offset=cursor)
    cursor += index_count * 2
    if index_count and int(indices.max()) >= vertex_count:
        raise DcmParseError(
            f"malla {index}: índice {int(indices.max())} para {vertex_count} vértices"
        )
    # El motor dibuja con winding invertido respecto a Blender.
    faces = indices.reshape(-1, 3).astype(np.int32)[:, ::-1].copy()

    joints = weights = influences = normals = None
    if mesh_type == MESH_TYPE_SKINNED:
        attrs = _block(data, cursor, vertex_count, SKINNED_ATTR_STRIDE)
        cursor += vertex_count * SKINNED_ATTR_STRIDE
        colours = _vertex_colours(attrs, SKINNED_ATTR_STRIDE - COLOUR_BYTES)
        uvs = attrs[:, SKINNED_UV_OFFSET : SKINNED_UV_OFFSET + 8].copy().view("<f4")

        verts = _block(data, cursor, vertex_count, SKINNED_VERTEX_STRIDE)
        cursor += vertex_count * SKINNED_VERTEX_STRIDE
        layout_avalado = _normals_are_unit(verts, SKINNED_NORMAL_OFFSET)
        influences = verts[:, 0:4].copy().view("<u4").reshape(-1)
        positions = verts[:, 4:16].copy().view("<f4")
        normals = verts[:, 20:32].copy().view("<f4")

        weights = np.frombuffer(
            data, dtype="<f4", count=vertex_count * 4, offset=cursor
        ).reshape(-1, 4).copy()
        cursor += vertex_count * SKINNED_WEIGHT_STRIDE
        joints = np.frombuffer(
            data, dtype=np.uint8, count=vertex_count * 4, offset=cursor
        ).reshape(-1, 4).copy()
        if bones and int(joints.max(initial=0)) >= len(bones):
            raise DcmParseError(
                f"malla {index}: índice de hueso {int(joints.max())} "
                f"para una paleta de {len(bones)}"
            )
    else:
        layout_avalado = False
        positions = np.frombuffer(
            data, dtype="<f4", count=vertex_count * 3, offset=cursor
        ).reshape(-1, 3).copy()
        cursor += vertex_count * STATIC_POSITION_STRIDE
        attrs = _block(data, cursor, vertex_count, STATIC_ATTR_STRIDE)
        colours = _vertex_colours(attrs, STATIC_ATTR_STRIDE - COLOUR_BYTES)
        uvs = attrs[:, STATIC_UV_OFFSET : STATIC_UV_OFFSET + 8].copy().view("<f4")

    aviso = _check_bbox(
        np.asarray(positions, dtype=np.float64), bbox, index, layout_avalado
    )

    return DcmMesh(
        index=index,
        mesh_type=mesh_type,
        positions=np.ascontiguousarray(positions, dtype=np.float32),
        uvs=np.ascontiguousarray(uvs, dtype=np.float32),
        indices=faces,
        bbox=tuple(float(v) for v in bbox),
        warnings=(aviso,) if aviso else (),
        bones=bones,
        submeshes=submeshes,
        joints=joints,
        weights=weights,
        influences=influences,
        colours=colours,
        normals=normals,
        data_base=base,
    )


def _block(data: bytes, offset: int, count: int, stride: int) -> np.ndarray:
    return np.frombuffer(
        data, dtype=np.uint8, count=count * stride, offset=offset
    ).reshape(count, stride)


def _vertex_colours(block: np.ndarray, offset: int) -> np.ndarray:
    """Color por vértice, al final de cada registro de atributos.

    Vale 0xFFFFFFFF (blanco) en la mayoría de las mallas, y por eso es tentador
    tomarlo por un centinela de stride. **No lo es**: las mallas de VFX lo usan
    de verdad (`vfx3D_Belial_RuptureHit` trae 120 de 364 vértices teñidos), así
    que exigirlo rompe archivos perfectamente válidos. Para validar el stride
    está `_check_bbox`, que compara contra la caja que declara la cabecera.
    """
    if not len(block):
        return np.zeros((0, 4), dtype=np.uint8)
    return block[:, offset : offset + COLOUR_BYTES].copy()


def _normals_are_unit(block: np.ndarray, offset: int) -> bool:
    """Normales de longitud 1: aval de que el reparto de bloques es correcto.

    Es una señal **independiente del espacio** en que esté la AABB declarada.
    Si el stride estuviera mal, en `offset` no habría normales sino trozos de
    otros campos, y las longitudes no darían 1 ni de lejos.
    """
    if not len(block) or block.shape[1] < offset + 12:
        return False
    normals = block[:, offset : offset + 12].copy().view("<f4").astype(np.float64)
    if not np.isfinite(normals).all():
        return False
    return bool(np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-3))


def _check_bbox(
    positions: np.ndarray,
    bbox: tuple[float, ...],
    index: int,
    layout_avalado: bool = False,
) -> str | None:
    """La AABB real contra la que declara la cabecera: el oráculo del stride.

    Si el reparto de bloques estuviera mal, las posiciones caerían en otro sitio
    y la caja no cuadraría ni de lejos.

    Ahora bien, que no cuadre no siempre significa que se haya leído mal. En las
    piezas de equipo la caja viene expresada en el nodo al que se acoplan, no en
    el espacio local de la pieza. En `media/items/armor/necroa01_shoulder.2` el
    error es de 52.8 tal cual y baja a 0.46 al llevar los vértices por la matriz
    de `Bone_Spine2` —y no es casualidad: el `.o3d` de al lado no trae esqueleto
    sino un descriptor de visuals, y su `SkinMeshVisual` nombra precisamente
    `Bone_Spine2` junto a `BBFollowNode`—. Esa malla es sana (normales unitarias,
    cero caras degeneradas, cero picos), así que rechazarla es un falso positivo.

    El detalle completo está en `docs/formato.md`, sección «La AABB de la
    cabecera».

    De ahí `layout_avalado`: cuando otra señal confirma el reparto, esto degrada
    a aviso y la malla entra. Sin aval se mantiene fatal, que es su razón de ser.
    """
    if not len(positions) or len(bbox) != 6:
        return None
    if not np.isfinite(positions).all():
        raise DcmParseError(f"malla {index}: posiciones no finitas (NaN/Inf)")
    low = positions.min(axis=0)
    high = positions.max(axis=0)
    declared = np.asarray(bbox, dtype=np.float64)
    scale = max(float(np.abs(declared).max()), 1.0)
    error = float(np.abs(np.concatenate([low, high]) - declared).max())
    if error <= scale * 1e-3:
        return None

    detalle = (
        f"malla {index}: la AABB leída {tuple(round(float(v), 2) for v in low)}-"
        f"{tuple(round(float(v), 2) for v in high)} no cuadra con la declarada "
        f"{tuple(round(v, 2) for v in bbox)} (error {error:.3f})"
    )
    if layout_avalado:
        return detalle + "; se acepta porque las normales son unitarias"
    raise DcmParseError(detalle)


def load_dcm(path: Path) -> DcmFile:
    path = Path(path)
    return parse_dcm(path.read_bytes(), path=path)
