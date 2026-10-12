"""Contenedor OBP de Darksiders II: Deathinitive Edition.

Un `.obp` empaqueta todos los recursos de un personaje o de una zona: mallas,
esqueleto, materiales, texturas y animaciones. La estructura es una cabecera con
varias tablas y, al final, **un único blob zlib** con todos los recursos
concatenados; la tabla de tamaños dice dónde corta cada uno.

Disposición (little-endian)::

    +0x00  char[5]  "OBPK\\0"
    +0x05  i32[6]   A: A[1] = offset de la tabla B, A[5] = offset del blob zlib
    +0x1D  u8       ?
    A[1]   i32[4]   B: B[0] = nº de grupos de tipo, B[1] = nº de recursos,
                       B[2] = bytes a saltar antes de los grupos
           i32[4]   descartado
    grupo: i32[4]   M: M[0] = id de tipo, M[1] = nº de recursos del grupo,
                       seguido de M[1] bytes opacos
           i32 + u8
    nombre: i32[2] + i32 longitud + bytes            x B[1]
    extra:  i32[2] + bloque opaco por recurso        x B[1]
    tamaños: i32                                     x B[1]
    A[5]   i32 tamaño comprimido + blob zlib

Los recursos **no** llevan extensión dentro del archivo: se deduce del id de
tipo del grupo al que pertenecen, y los grupos van en el mismo orden que los
nombres. `OBP_TYPE_INFO` traduce ese id a la extensión con la que se vuelcan
(tipo 0 -> `.anm`, tipo 1 -> `.o3d`, tipo 2 -> `.2`).

El addon solo saca de aquí modelos, esqueletos y animaciones
(`MODEL_TYPE_IDS`). El resto se indexa para que las cuentas cuadren, pero no se
escribe.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path


OBP_MAGIC = b"OBPK\0"

# id de tipo -> (extensión, etiqueta para la UI)
OBP_TYPE_INFO: dict[int, tuple[str, str]] = {
    0: (".anm", "Animación"),
    1: (".o3d", "Escena / esqueleto"),
    2: (".2", "Geometría"),
    5: (".bmat", "Material"),
    6: (".dds", "Textura"),
    8: (".8", "Datos de motor"),
    9: (".9", "Datos de motor"),
    20: (".20", "Datos de motor"),
}

ANIMATION_TYPE_ID = 0
SKELETON_TYPE_ID = 1
MESH_TYPE_ID = 2

# Lo único que el addon escribe: animaciones, esqueletos y geometría.
MODEL_TYPE_IDS = (ANIMATION_TYPE_ID, SKELETON_TYPE_ID, MESH_TYPE_ID)


class ObpParseError(ValueError):
    """El archivo no encaja con el contenedor OBP de Deathinitive."""


@dataclass(frozen=True, slots=True)
class ObpEntry:
    """Un recurso dentro del OBP, ya localizado pero todavía sin extraer."""

    name: str
    type_id: int
    size: int
    offset: int

    @property
    def extension(self) -> str:
        return OBP_TYPE_INFO.get(self.type_id, (f".{self.type_id}", ""))[0]

    @property
    def label(self) -> str:
        return OBP_TYPE_INFO.get(self.type_id, ("", "Desconocido"))[1]

    @property
    def filename(self) -> str:
        return f"{self.name}{self.extension}"


@dataclass(frozen=True, slots=True)
class ObpArchive:
    """Índice de un OBP: qué recursos hay y dónde caen dentro del blob."""

    path: Path
    entries: tuple[ObpEntry, ...]
    compressed_offset: int
    compressed_size: int
    payload_size: int

    def by_type(self, type_id: int) -> tuple[ObpEntry, ...]:
        return tuple(e for e in self.entries if e.type_id == type_id)


class _Reader:
    """Lector secuencial que aborta en cuanto se sale del archivo."""

    __slots__ = ("data", "offset")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def seek(self, offset: int) -> None:
        if not 0 <= offset <= len(self.data):
            raise ObpParseError(f"salto fuera del archivo @{offset:#x}")
        self.offset = offset

    def skip(self, count: int) -> None:
        self.seek(self.offset + count)

    def u8(self) -> int:
        if self.offset >= len(self.data):
            raise ObpParseError("lectura fuera del archivo")
        value = self.data[self.offset]
        self.offset += 1
        return value

    def i32(self, count: int = 1) -> tuple[int, ...]:
        end = self.offset + 4 * count
        if end > len(self.data):
            raise ObpParseError(f"lectura fuera del archivo @{self.offset:#x}")
        values = struct.unpack_from(f"<{count}i", self.data, self.offset)
        self.offset = end
        return values

    def text(self, length: int) -> str:
        end = self.offset + length
        if length < 0 or end > len(self.data):
            raise ObpParseError(f"cadena inválida ({length}) @{self.offset:#x}")
        raw = self.data[self.offset : end]
        self.offset = end
        return raw.split(b"\0", 1)[0].decode("latin1")


def parse_obp(path: Path) -> ObpArchive:
    """Indexa un `.obp` sin descomprimir el blob. Lanza ``ObpParseError``."""
    path = Path(path)
    data = path.read_bytes()
    if not data.startswith(OBP_MAGIC):
        raise ObpParseError(f"{path.name}: no empieza por OBPK")

    reader = _Reader(data)
    reader.skip(len(OBP_MAGIC))
    header = reader.i32(6)
    reader.skip(1)

    reader.seek(header[1])
    table = reader.i32(4)
    group_count, entry_count, prelude = table[0], table[1], table[2]
    if group_count == entry_count == 0:
        # Existen de verdad: los `.obp` de 4096 bytes de algunas salas de
        # `world/environments` (zone02_md3_rm01…) no declaran nada.
        raise ObpParseError(f"{path.name}: contenedor vacío, no declara ningún recurso")
    if not 0 < entry_count <= 1_000_000 or not 0 <= group_count <= 4096:
        raise ObpParseError(
            f"{path.name}: tabla incoherente "
            f"({group_count} grupos, {entry_count} recursos)"
        )
    reader.skip(prelude)
    reader.i32(4)  # espeja el encabezado de grupo pero no se usa

    groups: list[tuple[int, int]] = []
    for _ in range(group_count):
        block = reader.i32(4)
        groups.append((block[0], block[1]))
        reader.skip(block[1])
    declared = sum(count for _, count in groups)
    if declared != entry_count:
        raise ObpParseError(
            f"{path.name}: los grupos suman {declared} recursos "
            f"y la tabla declara {entry_count}"
        )
    reader.i32()
    reader.skip(1)

    names: list[str] = []
    for _ in range(entry_count):
        reader.i32(2)
        names.append(reader.text(reader.i32()[0]))

    # El bloque opaco por recurso va agrupado por tipo, en el mismo orden que
    # los nombres: de ahí sale la extensión de cada uno.
    type_ids: list[int] = []
    for type_id, count in groups:
        for _ in range(count):
            type_ids.append(type_id)
            reader.skip(reader.i32(2)[1])

    sizes = reader.i32(entry_count)
    if any(size < 0 for size in sizes):
        raise ObpParseError(f"{path.name}: tamaño de recurso negativo")

    entries: list[ObpEntry] = []
    cursor = 0
    for name, type_id, size in zip(names, type_ids, sizes):
        entries.append(ObpEntry(name=name, type_id=type_id, size=size, offset=cursor))
        cursor += size

    # En A[5] hay un u32 con el tamaño **descomprimido** del blob, no el
    # comprimido: coincide con la suma de la tabla de tamaños. El flujo zlib
    # ocupa desde ahí hasta el final del archivo.
    reader.seek(header[5])
    declared_payload = reader.i32()[0]
    if declared_payload != cursor:
        raise ObpParseError(
            f"{path.name}: la cabecera declara {declared_payload} bytes de payload "
            f"y la tabla de tamaños suma {cursor}"
        )
    compressed_size = len(data) - reader.offset
    if compressed_size <= 0:
        raise ObpParseError(f"{path.name}: no queda blob zlib tras la cabecera")

    return ObpArchive(
        path=path,
        entries=tuple(entries),
        compressed_offset=reader.offset,
        compressed_size=compressed_size,
        payload_size=cursor,
    )


def decompress_obp(archive: ObpArchive, *, allow_partial: bool = True) -> bytes:
    """Descomprime el blob del OBP.

    *allow_partial* deja pasar un blob truncado devolviendo lo que se pudo
    inflar: sirve para rescatar recursos de un archivo dañado. Los que caigan
    más allá del corte se anotan luego en `extract_obp`.
    """
    raw = archive.path.read_bytes()
    start = archive.compressed_offset
    blob = raw[start : start + archive.compressed_size]
    try:
        return zlib.decompress(blob)
    except zlib.error:
        if not allow_partial:
            raise
        return zlib.decompressobj().decompress(blob)


@dataclass(slots=True)
class ObpExtraction:
    """Lo que salió de un OBP, con las cuentas cuadradas contra su tabla.

    Cada recurso declarado acaba en una de tres columnas: escrito, filtrado (no
    es un modelo, un esqueleto ni una animación) o fallido con su motivo.
    """

    source: Path
    declared: int
    written: list[Path] = field(default_factory=list)
    filtered: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        text = (
            f"{self.source.name}: {self.declared} recursos, {len(self.written)} escritos, "
            f"{self.filtered} de otros tipos sin escribir"
        )
        if self.failed:
            text += f", {len(self.failed)} incompletos ({self.failed[0][0]}: {self.failed[0][1]})"
        return text


def extract_obp(
    path: Path,
    out_dir: Path,
    *,
    type_ids: tuple[int, ...] | None = MODEL_TYPE_IDS,
) -> ObpExtraction:
    """Vuelca a *out_dir* los recursos de un `.obp` cuyo tipo esté en *type_ids*.

    ``type_ids=None`` los escribe todos. Un blob truncado no aborta la
    extracción: los recursos completos se escriben igual y el resto se anota
    como fallido. Al terminar, `declarados == escritos + filtrados + fallidos`
    o se lanza ``RuntimeError``: una entrada que no está en ninguna columna es
    un fallo de este código, no del archivo.
    """
    archive = parse_obp(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = decompress_obp(archive)

    result = ObpExtraction(source=archive.path, declared=len(archive.entries))
    used: set[str] = set()
    for entry in archive.entries:
        if type_ids is not None and entry.type_id not in type_ids:
            result.filtered += 1
            continue
        chunk = payload[entry.offset : entry.offset + entry.size]
        if len(chunk) != entry.size:
            result.failed.append((entry.filename, "recurso más allá del blob descomprimido"))
            continue
        try:
            target = out_dir / _unique(safe_name(entry.filename), used)
            target.write_bytes(chunk)
        except (OSError, ValueError) as exc:
            result.failed.append((entry.filename, str(exc)))
            continue
        result.written.append(target)

    accounted = len(result.written) + result.filtered + len(result.failed)
    if accounted != result.declared:
        raise RuntimeError(
            f"{archive.path.name}: {result.declared} recursos declarados y {accounted} contados"
        )
    return result


# Caracteres que Windows no admite en un nombre. Los dos puntos, además, abren
# un flujo alternativo de NTFS (`a:b` escribe en el flujo `b` del archivo `a`).
_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')
# Nombres de dispositivo: `CON`, `NUL.2` o `COM1.o3d` no son archivos en Windows.
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def safe_name(name: str) -> str:
    """Un nombre del OBP convertido en un único componente de ruta seguro.

    Los nombres vienen del archivo y son datos ajenos: pueden traer separadores,
    `..`, una letra de unidad (`C:x` se sale de la carpeta aunque no lleve
    barra) o un nombre de dispositivo. Se aplanan los separadores, se cambian
    los caracteres no válidos por `_` y se recortan los puntos y espacios
    finales que Windows descarta. ``ValueError`` si no queda nada.
    """
    cleaned = _INVALID.sub("_", name.replace("\\", "_").replace("/", "_").strip())
    cleaned = cleaned.rstrip(" .")
    if cleaned.strip(".") == "":
        raise ValueError(f"nombre vacío tras sanearlo: {name!r}")
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def _unique(name: str, used: set[str]) -> str:
    """Evita que dos recursos con el mismo nombre se pisen en silencio."""
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    candidate, counter = name, 1
    while candidate.casefold() in used:
        candidate = f"{stem}_{counter}{dot}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate
