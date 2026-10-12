"""Contenedor OBP de Deathinitive: tablas, tipos y blob zlib."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from io_darksiders.formats.obp import (
    MODEL_TYPE_IDS,
    OBP_MAGIC,
    ObpParseError,
    extract_obp,
    parse_obp,
    safe_name,
)

# (nombre, id de tipo, contenido)
RESOURCES = [
    ("Idle", 0, b"ANM-clip"),
    ("belial", 1, b"esqueleto-belial"),
    ("trident", 1, b"esqueleto-trident"),
    ("belial", 2, b"geometria-belial-larga"),
    ("BelialA01", 5, b"material"),
    ("BelialA01_Diffuse", 6, b"DDS " + bytes(28)),
]


def build_obp(resources=RESOURCES) -> bytes:
    """Un `.obp` con la misma disposición que los del juego."""
    groups: dict[int, list[tuple[str, bytes]]] = {}
    for name, type_id, blob in resources:
        groups.setdefault(type_id, []).append((name, blob))
    ordered = [(t, groups[t]) for t in sorted(groups)]
    flat = [item for _, items in ordered for item in items]

    tables = bytearray()
    tables += struct.pack("<4i", len(ordered), len(flat), 0, 0)
    tables += struct.pack("<4i", 0, 0, 0, 0)  # encabezado descartado
    for type_id, items in ordered:
        tables += struct.pack("<4i", type_id, len(items), 0, 0)
        tables += bytes(len(items))  # bloque opaco, un byte por recurso
    tables += struct.pack("<i", 0) + bytes(1)
    for name, _ in flat:
        raw = name.encode("latin1")
        tables += struct.pack("<2i", 0, 0) + struct.pack("<i", len(raw)) + raw
    for _ in flat:
        tables += struct.pack("<2i", 0, 0)
    for _, blob in flat:
        tables += struct.pack("<i", len(blob))

    payload = b"".join(blob for _, blob in flat)
    table_offset = len(OBP_MAGIC) + 24 + 1
    blob_offset = table_offset + len(tables)

    head = bytearray(OBP_MAGIC)
    head += struct.pack("<6i", 9, table_offset, 0, 0, 0, blob_offset)
    head += bytes(1)
    return bytes(head) + bytes(tables) + struct.pack("<i", len(payload)) + zlib.compress(
        payload
    )


def test_parse_obp_indexa_nombres_tipos_y_tamanos() -> None:
    archive = parse_obp_bytes(build_obp())

    assert len(archive.entries) == len(RESOURCES)
    assert archive.payload_size == sum(len(b) for _, _, b in RESOURCES)
    assert [e.name for e in archive.by_type(1)] == ["belial", "trident"]


def test_el_id_de_tipo_da_la_extension() -> None:
    archive = parse_obp_bytes(build_obp())
    extensions = {e.name + e.extension for e in archive.entries}

    assert "belial.o3d" in extensions
    assert "belial.2" in extensions
    assert "BelialA01_Diffuse.dds" in extensions


def test_por_defecto_solo_salen_modelos_esqueletos_y_animaciones(tmp_path: Path) -> None:
    source = tmp_path / "belial_dlc90.obp"
    source.write_bytes(build_obp())

    result = extract_obp(source, tmp_path / "out")

    assert MODEL_TYPE_IDS == (0, 1, 2)
    assert result.failed == []
    assert {p.name for p in result.written} == {
        "Idle.anm",
        "belial.o3d",
        "trident.o3d",
        "belial.2",
    }
    assert result.filtered == 2  # el material y la textura
    assert (tmp_path / "out" / "belial.2").read_bytes() == b"geometria-belial-larga"
    assert not list((tmp_path / "out").glob("*.dds"))


def test_las_cuentas_cuadran_con_la_tabla(tmp_path: Path) -> None:
    source = tmp_path / "belial.obp"
    source.write_bytes(build_obp())

    result = extract_obp(source, tmp_path / "out")

    assert result.declared == len(RESOURCES)
    assert len(result.written) + result.filtered + len(result.failed) == result.declared
    assert result.complete
    assert "6 recursos, 4 escritos, 2 de otros tipos" in result.summary()


def test_sin_filtro_salen_todos(tmp_path: Path) -> None:
    source = tmp_path / "belial.obp"
    source.write_bytes(build_obp())

    result = extract_obp(source, tmp_path / "todo", type_ids=None)
    assert len(result.written) == len(RESOURCES)
    assert result.filtered == 0

    result = extract_obp(source, tmp_path / "solo", type_ids=(2,))
    assert [p.name for p in result.written] == ["belial.2"]


def test_un_blob_truncado_rescata_lo_que_se_pueda(tmp_path: Path) -> None:
    """Un OBP dañado no debe costar los recursos que sí están completos."""
    raw = bytearray(build_obp())
    del raw[-12:]
    source = tmp_path / "roto.obp"
    source.write_bytes(bytes(raw))

    result = extract_obp(source, tmp_path / "out", type_ids=None)
    assert result.written or result.failed  # no revienta
    assert all(p.stat().st_size > 0 for p in result.written)
    assert len(result.written) + len(result.failed) == result.declared
    if result.failed:
        assert not result.complete
        assert "incompletos" in result.summary()


@pytest.mark.parametrize(
    "nombre",
    ["../../fuera", r"..\..\fuera", "C:fuera", "/absoluto", r"\\servidor\x"],
)
def test_un_nombre_del_archivo_no_se_sale_de_la_carpeta(tmp_path: Path, nombre: str) -> None:
    """Los nombres vienen del archivo: son datos, no rutas en las que confiar."""
    source = tmp_path / "malicioso.obp"
    source.write_bytes(build_obp([(nombre, 2, b"geometria")]))
    out = tmp_path / "anidado" / "out"

    result = extract_obp(source, out)

    assert len(result.written) == 1
    assert result.written[0].parent == out
    assert result.written[0].read_bytes() == b"geometria"


def test_nombres_de_dispositivo_y_vacios() -> None:
    assert safe_name("CON.2") == "_CON.2"
    assert safe_name("nul.o3d") == "_nul.o3d"
    assert safe_name("a:b.2") == "a_b.2"
    with pytest.raises(ValueError, match="vacío"):
        safe_name(" .. ")


def test_un_nombre_que_es_solo_puntos_queda_dentro_de_la_carpeta(tmp_path: Path) -> None:
    """La extensión se añade siempre, así que `..` acaba en el archivo `...2`."""
    source = tmp_path / "puntos.obp"
    out = tmp_path / "out"
    source.write_bytes(build_obp([("..", 2, b"x"), ("bueno", 2, b"y")]))

    result = extract_obp(source, out)

    assert [p.name for p in result.written] == ["...2", "bueno.2"]
    assert all(p.parent == out and p.is_file() for p in result.written)


def test_dos_recursos_con_el_mismo_nombre_no_se_pisan(tmp_path: Path) -> None:
    source = tmp_path / "duplicado.obp"
    source.write_bytes(build_obp([("cabeza", 2, b"uno"), ("cabeza", 2, b"dos")]))

    result = extract_obp(source, tmp_path / "out")

    assert [p.name for p in result.written] == ["cabeza.2", "cabeza_1.2"]
    assert {p.read_bytes() for p in result.written} == {b"uno", b"dos"}


def test_un_archivo_sin_magic_se_rechaza(tmp_path: Path) -> None:
    source = tmp_path / "no.obp"
    source.write_bytes(b"ZIP\0" + bytes(64))
    with pytest.raises(ObpParseError, match="OBPK"):
        parse_obp(source)


def test_un_obp_vacio_se_explica_como_vacio(tmp_path: Path) -> None:
    """Las salas vacías de `world/environments` traen 0 grupos y 0 recursos."""
    source = tmp_path / "sala.obp"
    source.write_bytes(build_obp([]))

    with pytest.raises(ObpParseError, match="vacío"):
        parse_obp(source)


def test_una_tabla_de_grupos_incoherente_se_rechaza(tmp_path: Path) -> None:
    """Los grupos tienen que sumar exactamente el número de recursos."""
    raw = bytearray(build_obp())
    offset = len(OBP_MAGIC) + 24 + 1
    struct.pack_into("<i", raw, offset + 4, 99)
    source = tmp_path / "malo.obp"
    source.write_bytes(bytes(raw))

    with pytest.raises(ObpParseError, match="recursos"):
        parse_obp(source)


def parse_obp_bytes(data: bytes):
    """Escribe *data* a un temporal y lo indexa (parse_obp trabaja con rutas)."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".obp", delete=False) as handle:
        handle.write(data)
        path = Path(handle.name)
    try:
        return parse_obp(path)
    finally:
        path.unlink(missing_ok=True)
