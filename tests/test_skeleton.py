"""Esqueleto `.o3d` de Deathinitive: contenedor BOD y reconstrucción del árbol.

Las fixtures se escriben con el mismo serializador que usa el juego: cadenas
internadas con id correlativo, valores etiquetados y objetos anidados.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from io_darksiders.formats.skeleton import (
    O3D_MAGIC,
    O3dParseError,
    find_o3d,
    load_o3d,
    load_skeleton,
    parse_o3d,
)


class BodWriter:
    """Escribe el contenedor BOD igual que lo hace el motor."""

    def __init__(self) -> None:
        self.out = bytearray()
        self.ids: dict[str, int] = {}

    def string(self, text: str) -> "BodWriter":
        """Define la cadena la primera vez y la referencia después."""
        if text in self.ids:
            self.out += bytes((0x00,)) + struct.pack("<I", self.ids[text])
            return self
        self.ids[text] = len(self.ids)
        raw = text.encode("latin1")
        self.out += bytes((0x01,)) + bytes(8) + struct.pack("<H", len(raw)) + raw
        return self

    def floats(self, values) -> "BodWriter":
        self.out += bytes((0x0B,)) + struct.pack("<I", len(values))
        for value in values:
            self.out += bytes((0x03,)) + struct.pack("<f", value)
        return self

    def obj_array(self, count: int) -> "BodWriter":
        self.out += bytes((0x09,)) + struct.pack("<I", count) + bytes(1)
        return self

    def obj_head(self, cls: str, nprops: int, *, tagged: bool = True) -> "BodWriter":
        if tagged:
            self.out += bytes((0x07,))
        self.out += struct.pack("<I", 0) + bytes((0x01,))
        self.string(cls)
        self.out += struct.pack("<I", nprops)
        return self


_VECTOR_PROPS = (("position", "Position"), ("rotation", "Rotation"), ("scale", "Scale"))


def _node(writer: BodWriter, node: dict) -> None:
    children = node.get("children", [])
    props = 1 + sum(key in node for key, _ in _VECTOR_PROPS) + int(bool(children))
    writer.obj_head("Node3D", props)
    writer.string("Name")
    writer.out += bytes((0x0F,))
    writer.string(node["name"])
    for key, label in _VECTOR_PROPS:
        if key in node:
            writer.string(label).floats(node[key])
    if children:
        writer.string("Children").obj_array(len(children))
        for child in children:
            _node(writer, child)


def build_o3d(roots: list[dict], name: str = "actor") -> bytes:
    """Un `.o3d` con la escena *roots* colgando de un Skeleton3D."""
    writer = BodWriter()
    writer.out += bytes((0x01,))
    writer.string("Object3D")
    writer.out += struct.pack("<I", 1)
    writer.string("Skeleton")
    writer.out += bytes((0x07,))
    writer.obj_head("Skeleton3D", 2, tagged=False)
    writer.string("Name")
    writer.out += bytes((0x0F,))
    writer.string(name)
    writer.string("Children").obj_array(len(roots))
    for root in roots:
        _node(writer, root)
    return O3D_MAGIC + bytes(12) + bytes(writer.out)


TREE = [
    {
        "name": "Bone_Root",
        "position": [0.0, 0.0, 10.0],
        "rotation": [0.0, 0.0, 0.0],
        "children": [
            {
                "name": "Bone_Spine",
                "position": [0.0, 5.0, 0.0],
                "children": [{"name": "Bone_Head", "position": [0.0, 3.0, 0.0]}],
            },
            {"name": "Bone_Tail", "position": [0.0, -4.0, 0.0]},
        ],
    },
    {"name": "VFX_Hook"},
]


def test_parse_o3d_reconstruye_el_arbol_en_preorden() -> None:
    skeleton = parse_o3d(build_o3d(TREE))

    assert skeleton.name == "actor"
    assert [b.name for b in skeleton.bones] == [
        "Bone_Root",
        "Bone_Spine",
        "Bone_Head",
        "Bone_Tail",
        "VFX_Hook",
    ]
    assert [b.parent for b in skeleton.bones] == [-1, 0, 1, 0, -1]


def test_el_arbol_pasa_la_validacion_de_la_skill() -> None:
    """Un único padre por nodo y siempre `parent < índice`."""
    skeleton = parse_o3d(build_o3d(TREE))
    assert skeleton.validate() == ()
    assert all(b.parent < i for i, b in enumerate(skeleton.bones))


def test_el_nivel_superior_es_un_bosque() -> None:
    skeleton = parse_o3d(build_o3d(TREE))
    assert [skeleton.bones[i].name for i in skeleton.roots] == [
        "Bone_Root",
        "VFX_Hook",
    ]


def test_position_y_rotation_ausentes_valen_identidad() -> None:
    """Son opcionales: darlas por hechas desincroniza el parser."""
    skeleton = parse_o3d(build_o3d(TREE))
    hook = skeleton.bones[skeleton.index_of("VFX_Hook")]
    assert hook.position == (0.0, 0.0, 0.0)
    assert hook.rotation == (0.0, 0.0, 0.0)


def test_las_matrices_de_mundo_acumulan_por_la_cadena() -> None:
    skeleton = parse_o3d(build_o3d(TREE))
    world = skeleton.world_matrices()
    head = skeleton.index_of("Bone_Head")
    # 10 en Z del root, 5 + 3 en Y de la cadena; sin rotaciones se suman.
    np.testing.assert_allclose(world[head][3, :3], (0.0, 8.0, 10.0), atol=1e-5)


def test_la_ibm_es_la_inversa_de_la_matriz_de_mundo() -> None:
    skeleton = parse_o3d(build_o3d(TREE))
    world = skeleton.world_matrices()
    ibm = skeleton.inverse_bind_matrices()
    product = np.einsum("nij,njk->nik", ibm, world)
    identity = np.broadcast_to(np.eye(4), product.shape)
    np.testing.assert_allclose(product, identity, atol=1e-9)


def test_la_rotacion_son_grados_en_orden_xyz() -> None:
    tree = [{"name": "Bone_Root", "position": [1.0, 0.0, 0.0], "rotation": [0.0, 0.0, 90.0]}]
    skeleton = parse_o3d(build_o3d(tree))
    matrix = skeleton.world_matrices()[0]
    # Giro de 90° en Z: el eje X local acaba sobre el Y global.
    np.testing.assert_allclose(matrix[0, :3], (0.0, 1.0, 0.0), atol=1e-6)


def test_un_archivo_que_no_es_bod_se_rechaza() -> None:
    with pytest.raises(O3dParseError, match="BOD"):
        parse_o3d(b"NOPE" + bytes(64))


def test_bytes_sobrantes_tras_el_objeto_raiz_se_rechazan() -> None:
    """Consumir el archivo entero es la prueba de que la gramática cuadra."""
    with pytest.raises(O3dParseError, match="sobran"):
        parse_o3d(build_o3d(TREE) + b"\x00\x00\x00\x00")


def test_find_y_load_skeleton_localizan_el_o3d_hermano(tmp_path: Path) -> None:
    (tmp_path / "actor.o3d").write_bytes(build_o3d(TREE))
    mesh = tmp_path / "actor.2"
    mesh.write_bytes(b"")

    assert find_o3d(mesh) == tmp_path / "actor.o3d"
    assert load_skeleton(mesh) is not None


def test_load_skeleton_devuelve_none_si_no_hay_o3d(tmp_path: Path) -> None:
    mesh = tmp_path / "suelto.2"
    mesh.write_bytes(b"")
    assert load_skeleton(mesh) is None


def test_load_o3d_lee_desde_disco(tmp_path: Path) -> None:
    path = tmp_path / "actor.o3d"
    path.write_bytes(build_o3d(TREE))
    assert len(load_o3d(path)) == 5


def test_scale_ausente_vale_uno_no_cero() -> None:
    """La identidad de la escala es 1. Un (0,0,0) colapsaría la descendencia."""
    skeleton = parse_o3d(build_o3d(TREE))
    assert all(bone.scale == (1.0, 1.0, 1.0) for bone in skeleton.bones)


def test_scale_se_aplica_a_la_matriz_local() -> None:
    """Los nodos de Darksiders 1 sí traen Scale; los de Deathinitive no."""
    tree = [{"name": "Bone_Root", "position": [0.0, 0.0, 0.0], "scale": [2.0, 3.0, 4.0]}]
    matrix = parse_o3d(build_o3d(tree)).world_matrices()[0]
    np.testing.assert_allclose(np.diag(matrix)[:3], (2.0, 3.0, 4.0), atol=1e-6)


# ---------------------------------------------------------------------------
# Compatibilidad con Darksiders 1
# ---------------------------------------------------------------------------

DS1_SCHEMA = ("Node3D", "Name", "Position", "Rotation", "Scale", "Children")


def _ds1_record(name: bytes = b"Bone_Root", children: int = 4) -> bytes:
    """El registro que el script de Darksiders 1 lee por offsets fijos.

    Un `Node3D` con las cinco propiedades del esquema de Darksiders 1 y todos
    los nombres ya internados, que es como quedan a partir del segundo nodo.
    """
    ids = {name_: i for i, name_ in enumerate(DS1_SCHEMA)}
    out = bytearray(struct.pack("<I", 5))  # cinco propiedades

    def ref(label: str) -> None:
        out.extend(bytes((0x00,)) + struct.pack("<I", ids[label]))

    def floats(values) -> None:
        out.extend(bytes((0x0B,)) + struct.pack("<I", len(values)))
        for value in values:
            out.extend(bytes((0x03,)) + struct.pack("<f", value))

    ref("Name")
    out.append(0x0F)
    out.extend(bytes((0x01,)) + bytes(8))  # strdef: etiqueta + hash
    out.extend(struct.pack("<H", len(name)) + name)
    ref("Position")
    floats([1.0, 2.0, 3.0])
    ref("Rotation")
    floats([10.0, 20.0, 30.0])
    ref("Scale")
    floats([1.0, 1.0, 1.0])
    ref("Children")
    out.extend(bytes((0x09,)) + struct.pack("<I", children) + bytes(1))
    out.extend(bytes((0x07,)) + struct.pack("<I", 99) + bytes((0x01,)))
    ref("Node3D")
    return bytes(out)


def test_el_registro_de_116_bytes_de_darksiders_1_es_este_mismo_contenedor() -> None:
    """El `.o3d` de Darksiders 1 no es un formato distinto: es el mismo BOD.

    El script de referencia salta 19 bytes tras la cadena `Node3D`, lee el
    nombre y asume un registro fijo de 116 bytes. Esos números no son mágicos:
    salen de los tokens del contenedor cuando el nodo trae las cinco
    propiedades y los nombres ya están internados.

        19 = propcount(4) + ref a Name(5) + etiqueta 0x0f(1) + strdef(1) + hash(8)
        97 = 3 x [ref(5) + cabecera de array(5) + 3 x (etiqueta + f32)(15)] + cola(22)
    """
    name = b"Bone_Root"
    record = _ds1_record(name)

    prefix = 19
    assert record[prefix : prefix + 2] == struct.pack("<H", len(name))
    body = prefix + 2 + len(name)
    assert body + 97 == len(record)
    assert prefix + 97 == 116


def test_los_offsets_a_ciegas_de_darksiders_1_caen_donde_deben() -> None:
    """Leyendo el registro como el script de 2014, los valores son correctos."""
    record = _ds1_record()
    offset = 19
    length = struct.unpack_from("<H", record, offset)[0]
    offset += 2
    assert record[offset : offset + length] == b"Bone_Root"
    start = offset + length

    def triple(at: int) -> list[float]:
        values = []
        for step in range(3):
            assert record[at + step * 5] == 0x03  # etiqueta de float
            values.append(struct.unpack_from("<f", record, at + step * 5 + 1)[0])
        return values

    assert triple(start + 10) == [1.0, 2.0, 3.0]  # Position
    assert triple(start + 35) == [10.0, 20.0, 30.0]  # Rotation
    assert triple(start + 60) == [1.0, 1.0, 1.0]  # Scale

    tail = record[start + 75 : start + 97]
    assert len(tail) == 22
    assert tail[6] == 4  # byte bajo del recuento de hijos
    assert tail[11] == 0x07  # etiqueta de objeto: el script exige que sea 7
