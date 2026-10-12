"""Localizar el rig de una malla entre varios `.o3d`, y dos etiquetas del BOD.

Medido sobre el corpus real local de pruebas (44 archivos `.o3d`):

* **21 son solo visuales**: su objeto raíz no trae ``Skeleton``. `find_o3d`
  empareja por nombre y aterriza justo en ellos, con lo que `load_skeleton`
  devuelve ``None`` y la malla se exporta sin jerarquía sin explicar por qué.
* El rig de verdad suele estar en otro archivo y a veces en **otra carpeta**:
  las mallas de ``characters/skeleton/`` se riggean con
  ``characters/skeleton_common/skeleton_base.o3d``.

De ahí `find_rig`, que puntúa candidatos por huesos cubiertos.

Las etiquetas ``0x04`` (bool) y ``0xff`` (referencia a un objeto ya escrito)
aparecen a mitad de archivo en 7 de los 44, uno de ellos ``death.o3d``, y sin
ellas el parser aborta con "etiqueta de valor desconocida".

Las fixtures usan el mismo ``BodWriter`` que `tests/test_skeleton.py`
para no tener dos codificadores del contenedor que puedan divergir.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from io_darksiders.formats.skeleton import (
    O3D_MAGIC,
    O3dParseError,
    find_o3d,
    find_rig,
    load_o3d,
    parse_o3d,
)
from tests.test_skeleton import BodWriter, _node


def _wrap(writer: BodWriter) -> bytes:
    return O3D_MAGIC + bytes(12) + bytes(writer.out)


def write_rig(path: Path, bones: list[str]) -> Path:
    """Un `.o3d` con un Skeleton3D plano de los huesos dados."""
    writer = BodWriter()
    writer.out += bytes((0x01,))
    writer.string("Object3D")
    writer.out += struct.pack("<I", 1)
    writer.string("Skeleton")
    writer.out += bytes((0x07,))
    writer.obj_head("Skeleton3D", 2, tagged=False)
    writer.string("Name")
    writer.out += bytes((0x0F,))
    writer.string(path.stem)
    writer.string("Children").obj_array(len(bones))
    for bone in bones:
        _node(writer, {"name": bone})

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_wrap(writer))
    return path


def write_visual_only(path: Path) -> Path:
    """Un `.o3d` sin ``Skeleton``: solo visuales, como 21 de los 44 del corpus."""
    writer = BodWriter()
    writer.out += bytes((0x01,))
    writer.string("Object3D")
    writer.out += struct.pack("<I", 1)
    writer.string("Visuals")
    writer.obj_array(0)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_wrap(writer))
    return path


def _o3d_con_propiedad_extra(raw_value: bytes) -> bytes:
    """Un `.o3d` válido cuyo Skeleton3D lleva una propiedad de valor *raw_value*."""
    writer = BodWriter()
    writer.out += bytes((0x01,))
    writer.string("Object3D")
    writer.out += struct.pack("<I", 1)
    writer.string("Skeleton")
    writer.out += bytes((0x07,))
    writer.obj_head("Skeleton3D", 3, tagged=False)
    writer.string("Name")
    writer.out += bytes((0x0F,))
    writer.string("actor")
    writer.string("Extra")
    writer.out += raw_value
    writer.string("Children").obj_array(1)
    _node(writer, {"name": "Bone_Root"})
    return _wrap(writer)


def _mesh(folder: Path, stem: str) -> Path:
    """Una malla vacía: find_rig solo mira su nombre y su carpeta."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.2"
    path.write_bytes(b"")
    return path


# --------------------------------------------------------------------------
# Etiquetas del contenedor que faltaban
# --------------------------------------------------------------------------


def test_una_etiqueta_bool_no_desincroniza_el_arbol() -> None:
    skeleton = parse_o3d(_o3d_con_propiedad_extra(bytes((0x04, 0x01))))
    assert [bone.name for bone in skeleton.bones] == ["Bone_Root"]


def test_una_referencia_a_objeto_no_desincroniza_el_arbol() -> None:
    raw = bytes((0xFF,)) + struct.pack("<I", 7)
    skeleton = parse_o3d(_o3d_con_propiedad_extra(raw))
    assert [bone.name for bone in skeleton.bones] == ["Bone_Root"]


def test_una_etiqueta_de_verdad_desconocida_se_sigue_rechazando() -> None:
    with pytest.raises(O3dParseError, match="etiqueta de valor"):
        parse_o3d(_o3d_con_propiedad_extra(bytes((0x7E, 0x00))))


# --------------------------------------------------------------------------
# find_rig
# --------------------------------------------------------------------------


def test_las_fixtures_se_leen_como_los_archivos_reales(tmp_path: Path) -> None:
    rig = write_rig(tmp_path / "death.o3d", ["Bone_Root", "Bone_Spine"])
    assert [b.name for b in load_o3d(rig).bones] == ["Bone_Root", "Bone_Spine"]

    visual = write_visual_only(tmp_path / "solo_visual.o3d")
    with pytest.raises(O3dParseError, match="Skeleton"):
        load_o3d(visual)


def test_find_rig_ignora_el_o3d_del_mismo_nombre_si_es_solo_visual(
    tmp_path: Path,
) -> None:
    """El caso de makermale_elder: el hermano por nombre no trae huesos."""
    mesh = _mesh(tmp_path / "maker_elder", "makermale_elder")
    write_visual_only(mesh.parent / "makermale_elder.o3d")
    esperado = write_rig(
        mesh.parent / "makermale_elder_base.o3d", ["Bone_A", "Bone_B", "Bone_C"]
    )

    # La regla por nombre se queda con el visual, que no tiene un solo hueso.
    assert find_o3d(mesh) == mesh.parent / "makermale_elder.o3d"

    encontrado, cubiertos = find_rig(mesh, ["Bone_A", "Bone_B", "Bone_C"])
    assert encontrado == esperado
    assert cubiertos == 3


def test_find_rig_cruza_carpetas_hasta_el_common(tmp_path: Path) -> None:
    """El caso de skeleton/: el rig vive en el skeleton_common de al lado."""
    mesh = _mesh(tmp_path / "skeleton", "skeleton_mesh_base")
    write_visual_only(mesh.parent / "skeleton_ragdoll.o3d")
    esperado = write_rig(
        tmp_path / "skeleton_common" / "skeleton_base.o3d",
        ["Bone_Root", "Bone_Spine", "Bone_Head"],
    )

    assert find_o3d(mesh) is None  # no hay ninguno con su mismo nombre

    encontrado, cubiertos = find_rig(mesh, ["Bone_Root", "Bone_Spine", "Bone_Head"])
    assert encontrado == esperado
    assert cubiertos == 3


def test_find_rig_se_queda_con_el_que_mas_huesos_cubre(tmp_path: Path) -> None:
    mesh = _mesh(tmp_path / "personaje", "malla")
    write_rig(mesh.parent / "parcial.o3d", ["Bone_A"])
    completo = write_rig(mesh.parent / "completo.o3d", ["Bone_A", "Bone_B", "Bone_C"])

    encontrado, cubiertos = find_rig(mesh, ["Bone_A", "Bone_B", "Bone_C"])
    assert encontrado == completo
    assert cubiertos == 3


def test_find_rig_informa_de_la_cobertura_parcial(tmp_path: Path) -> None:
    """visual_armleg: hay rig, pero no cubre la paleta entera."""
    mesh = _mesh(tmp_path / "personaje", "malla")
    parcial = write_rig(mesh.parent / "parcial.o3d", ["Bone_A", "Bone_B"])

    encontrado, cubiertos = find_rig(mesh, ["Bone_A", "Bone_B", "Bone_C", "Bone_D"])
    assert encontrado == parcial
    assert cubiertos == 2


def test_find_rig_no_inventa_un_rig_que_no_esta_extraido(tmp_path: Path) -> None:
    """El caso de bone_giant: ningún .o3d del árbol cubre esos huesos."""
    mesh = _mesh(tmp_path / "bone_giant", "bone_giant_mesh_body")
    write_visual_only(mesh.parent / "bone_giant_mesh_body.o3d")
    write_rig(tmp_path / "otro" / "ajeno.o3d", ["Otro_Hueso"])

    assert find_rig(mesh, ["Bone_A", "Bone_B"]) == (None, 0)


def test_find_rig_sin_huesos_no_busca_nada(tmp_path: Path) -> None:
    mesh = _mesh(tmp_path / "personaje", "estatica")
    write_rig(mesh.parent / "hay_rig.o3d", ["Bone_A"])

    assert find_rig(mesh, []) == (None, 0)


def test_find_rig_sobrevive_a_un_o3d_ilegible(tmp_path: Path) -> None:
    """Un candidato corrupto no puede tumbar la búsqueda del bueno."""
    mesh = _mesh(tmp_path / "personaje", "malla")
    (mesh.parent / "corrupto.o3d").write_bytes(O3D_MAGIC + bytes(12) + b"\x7e\x7e")
    bueno = write_rig(mesh.parent / "zbueno.o3d", ["Bone_A", "Bone_B"])

    encontrado, cubiertos = find_rig(mesh, ["Bone_A", "Bone_B"])
    assert encontrado == bueno
    assert cubiertos == 2


def test_find_rig_respeta_el_tope_de_candidatos(tmp_path: Path) -> None:
    mesh = _mesh(tmp_path / "personaje", "malla")
    for i in range(5):
        write_visual_only(mesh.parent / f"visual_{i:02d}.o3d")
    write_rig(mesh.parent / "zrig.o3d", ["Bone_A"])

    # Con el tope por debajo del rig (ordenado el último) no se llega a él.
    assert find_rig(mesh, ["Bone_A"], max_files=3) == (None, 0)
    assert find_rig(mesh, ["Bone_A"], max_files=50)[1] == 1
