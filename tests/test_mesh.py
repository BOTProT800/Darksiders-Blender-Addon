"""Geometría `.2` de Deathinitive: cabecera, strides y puertas de calidad.

Las fixtures son sintéticas y se arman con los mismos anchos verificados sobre
archivos reales del juego (92 B/vértice con piel, 76 sin ella). No se guardan
assets del juego en el repo.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from io_darksiders.formats.mesh import (
    MESH_TYPE_SKINNED,
    MESH_TYPE_STATIC,
    DcmParseError,
    load_dcm,
    parse_dcm,
)

WHITE = struct.pack("<I", 0xFFFFFFFF)


def _quad_positions() -> list[tuple[float, float, float]]:
    return [(-1.0, -2.0, 0.0), (1.0, -2.0, 0.0), (1.0, 3.0, 4.0), (-1.0, 3.0, 4.0)]


def _bbox(positions: list[tuple[float, float, float]]) -> bytes:
    arr = np.asarray(positions, dtype=np.float32)
    return struct.pack("<6f", *arr.min(axis=0), *arr.max(axis=0))


def _uv(i: int) -> tuple[float, float]:
    """La V se guarda negada, igual que en el juego."""
    return (0.25 * i, -0.25 * i)


def _skinned_blocks(positions, palette_size: int) -> bytes:
    count = len(positions)
    attrs = b"".join(
        struct.pack("<2f", *_uv(i)) + bytes(8) + WHITE for i in range(count)
    )
    verts = b"".join(
        struct.pack("<I", 2) + struct.pack("<3f", *positions[i]) + bytes(36)
        for i in range(count)
    )
    weights = b"".join(struct.pack("<4f", 0.75, 0.25, 0.0, 0.0) for _ in range(count))
    joints = b"".join(
        bytes((i % palette_size, (i + 1) % palette_size, 0, 0)) for i in range(count)
    )
    return attrs + verts + weights + joints


def _static_blocks(positions) -> bytes:
    count = len(positions)
    pos = b"".join(struct.pack("<3f", *p) for p in positions)
    attrs = b"".join(
        bytes(28) + struct.pack("<2f", *_uv(i)) + bytes(8) + WHITE
        for i in range(count)
    )
    tail = bytes(16 * count)
    return pos + attrs + tail


def _bone(name: str) -> bytes:
    matrix = np.eye(4, dtype=np.float32).reshape(-1)
    return (
        bytes((255,))
        + struct.pack("<H", len(name))
        + name.encode("latin1")
        + struct.pack("<16f", *matrix)
        + struct.pack("<2H", 0, 0)
    )


def _submesh(name: str, vcount: int, icount: int) -> bytes:
    return (
        bytes((255,))
        + struct.pack("<H", len(name))
        + name.encode("latin1")
        + struct.pack("<6i", 0, 0, vcount, 0, icount, 0)
    )


def build_dcm(
    *,
    skinned: bool = True,
    bones: tuple[str, ...] = ("Bone_Root", "Bone_Tip"),
    positions=None,
) -> bytes:
    """Un `.2` de una sola malla: dos triángulos que forman un quad."""
    positions = positions or _quad_positions()
    indices = [0, 1, 2, 0, 2, 3]
    mesh_type = MESH_TYPE_SKINNED if skinned else MESH_TYPE_STATIC
    nvert, nidx = len(positions), len(indices)

    head_tables = (
        struct.pack("<i", 1)
        + struct.pack("<i", nidx)
        + struct.pack("<i", 1)
        + struct.pack("<i", nvert)
        + struct.pack("<i", -1)
        + struct.pack("<i", nvert)
        + struct.pack("<f", 0.5)
    )
    bone_blob = b"".join(_bone(n) for n in bones) if skinned else b""
    submesh_blob = _submesh("MatA", nvert, nidx)
    if skinned:
        counts = struct.pack("<5i", nvert, 64, 8, 1, len(bones))
    else:
        counts = struct.pack("<5i", nvert, 60, 8, 0, 1)

    mesh_header = bytes((mesh_type,)) + counts + _bbox(positions) + bone_blob + submesh_blob
    data_offset = 8 + len(head_tables) + len(mesh_header)

    body = struct.pack("<2I", nidx * 2, 2)
    body += b"".join(struct.pack("<H", i) for i in indices)
    body += (
        _skinned_blocks(positions, len(bones)) if skinned else _static_blocks(positions)
    )
    return struct.pack("<2i", data_offset, 1) + head_tables + mesh_header + body


def test_parse_dcm_con_piel_lee_geometria_y_paleta() -> None:
    parsed = parse_dcm(build_dcm())
    assert len(parsed.meshes) == 1
    mesh = parsed.meshes[0]

    assert mesh.is_skinned
    assert mesh.vertex_count == 4
    assert mesh.triangle_count == 2
    assert [b.name for b in mesh.bones] == ["Bone_Root", "Bone_Tip"]
    assert mesh.submeshes[0].name == "MatA"
    np.testing.assert_allclose(mesh.positions[0], (-1.0, -2.0, 0.0))
    assert mesh.influences is not None and int(mesh.influences[0]) == 2


def test_parse_dcm_estatica_no_trae_huesos_ni_pesos() -> None:
    mesh = parse_dcm(build_dcm(skinned=False)).meshes[0]

    assert not mesh.is_skinned
    assert mesh.bones == ()
    assert mesh.joints is None and mesh.weights is None
    np.testing.assert_allclose(mesh.positions[2], (1.0, 3.0, 4.0))


def test_el_winding_se_invierte_al_leer() -> None:
    """El motor dibuja al revés que OBJ/glTF: la cara sale volteada."""
    mesh = parse_dcm(build_dcm()).meshes[0]
    np.testing.assert_array_equal(mesh.indices[0], (2, 1, 0))


def test_cabecera_que_no_cae_en_data_offset_se_rechaza() -> None:
    """Es la comprobación que delata un reparto malo de head[3]/head[4]."""
    raw = bytearray(build_dcm())
    struct.pack_into("<i", raw, 0, struct.unpack_from("<i", raw, 0)[0] + 4)
    with pytest.raises(DcmParseError, match="data_offset"):
        parse_dcm(bytes(raw))


def test_stride_equivocado_lo_delata_la_aabb() -> None:
    """La caja declarada es el oráculo: si el stride baila, no cuadra."""
    raw = bytearray(build_dcm())
    # Se estropea la caja declarada, que es lo mismo que leer en otro sitio.
    struct.pack_into("<f", raw, raw.index(_bbox(_quad_positions())), 99.0)
    with pytest.raises(DcmParseError, match="AABB"):
        parse_dcm(bytes(raw))


def test_indice_fuera_de_rango_se_rechaza() -> None:
    raw = bytearray(build_dcm())
    body = raw.index(struct.pack("<2I", 12, 2))
    struct.pack_into("<H", raw, body + 8, 99)
    with pytest.raises(DcmParseError, match="índice"):
        parse_dcm(bytes(raw))


def test_color_por_vertice_distinto_de_blanco_no_rompe_el_parseo() -> None:
    """El campo del final de los atributos es color, no un centinela.

    Las mallas de VFX lo usan de verdad; exigir 0xFFFFFFFF rompería archivos
    perfectamente válidos.
    """
    raw = bytearray(build_dcm())
    marker = raw.index(WHITE, raw.index(struct.pack("<2I", 12, 2)))
    raw[marker : marker + 4] = bytes((0x20, 0x40, 0x60, 0x80))
    mesh = parse_dcm(bytes(raw)).meshes[0]
    assert mesh.vertex_count == 4


def test_load_dcm_recorre_el_archivo_entero(tmp_path: Path) -> None:
    source = tmp_path / "modelo.2"
    source.write_bytes(build_dcm(skinned=False))
    assert load_dcm(source).main_mesh is not None


def test_normales_unitarias_avalan_el_layout() -> None:
    """La señal que sostiene el aviso: normales de longitud 1 en su offset."""
    from io_darksiders.formats.mesh import (
        SKINNED_NORMAL_OFFSET,
        SKINNED_VERTEX_STRIDE,
        _normals_are_unit,
    )

    bloque = np.zeros((4, SKINNED_VERTEX_STRIDE), dtype=np.uint8)
    normales = np.array(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.6, 0.8, 0.0]],
        dtype="<f4",
    )
    off = SKINNED_NORMAL_OFFSET
    bloque[:, off : off + 12] = normales.view(np.uint8).reshape(4, 12)
    assert _normals_are_unit(bloque, off) is True

    # Con el stride mal repartido ahí no hay normales, y se nota.
    torcido = bloque.copy()
    torcido[:, off : off + 12] = np.array(
        [[3.0, 4.0, 12.0]] * 4, dtype="<f4"
    ).view(np.uint8).reshape(4, 12)
    assert _normals_are_unit(torcido, off) is False


def test_una_aabb_que_no_cuadra_sin_aval_sigue_siendo_fatal() -> None:
    """Sin otra señal, la AABB declarada manda: es el oráculo del stride."""
    from io_darksiders.formats.mesh import DcmParseError, _check_bbox

    posiciones = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    declarada = (50.0, 50.0, 50.0, 60.0, 60.0, 60.0)
    with pytest.raises(DcmParseError, match="no cuadra"):
        _check_bbox(posiciones, declarada, 0, layout_avalado=False)


def test_con_aval_la_aabb_que_no_cuadra_solo_avisa() -> None:
    """El caso de las piezas de equipo: caja en el esqueleto del personaje."""
    from io_darksiders.formats.mesh import _check_bbox

    posiciones = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    declarada = (50.0, 50.0, 50.0, 60.0, 60.0, 60.0)
    aviso = _check_bbox(posiciones, declarada, 0, layout_avalado=True)

    assert aviso is not None
    assert "no cuadra" in aviso and "normales son unitarias" in aviso


def test_una_aabb_que_cuadra_no_avisa_de_nada() -> None:
    from io_darksiders.formats.mesh import _check_bbox

    posiciones = np.array([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]])
    declarada = (0.0, 0.0, 0.0, 1.0, 2.0, 3.0)
    assert _check_bbox(posiciones, declarada, 0, layout_avalado=True) is None
    assert _check_bbox(posiciones, declarada, 0, layout_avalado=False) is None


def test_posiciones_no_finitas_siguen_siendo_fatales_con_aval() -> None:
    """El aval habla del reparto de bloques, no de que los datos valgan."""
    from io_darksiders.formats.mesh import DcmParseError, _check_bbox

    posiciones = np.array([[0.0, 0.0, 0.0], [np.nan, 1.0, 1.0]])
    with pytest.raises(DcmParseError, match="no finitas"):
        _check_bbox(posiciones, (0.0, 0.0, 0.0, 1.0, 1.0, 1.0), 0, layout_avalado=True)
