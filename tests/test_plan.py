"""Plan de importación del addon de Blender (la parte que no necesita `bpy`).

`io_darksiders/plan.py` es deliberadamente independiente de Blender para poder
probarlo aquí; `operators.py`, con `bpy`, solo construye lo que este plan describe.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from io_darksiders import plan
from io_darksiders.formats.mesh import parse_dcm
from io_darksiders.formats.skeleton import parse_o3d
from tests.test_mesh import build_dcm
from tests.test_skeleton import build_o3d

ROOT = Path(__file__).resolve().parents[1]

SCENE = [
    {
        "name": "Bone_Root",
        "position": [0.0, 0.0, 4.0],
        "children": [{"name": "Bone_Tip", "position": [0.0, 2.0, 0.0]}],
    },
    {"name": "Bone_Other"},
]


def _plan(**kwargs):
    parsed = parse_dcm(build_dcm(**kwargs))
    skeleton = parse_o3d(build_o3d(SCENE))
    return plan.build_import_plan(parsed, skeleton, base_name="actor"), parsed


def test_el_plan_trae_malla_y_armadura() -> None:
    blueprint, _ = _plan()

    assert blueprint.armature is not None
    assert [b.name for b in blueprint.armature.bones] == [
        "Bone_Root",
        "Bone_Tip",
        "Bone_Other",
    ]
    assert len(blueprint.meshes) == 1
    assert blueprint.meshes[0].name == "actor_00"


def test_no_se_remapean_los_ejes() -> None:
    """Motor y Blender son Z-up diestros: las posiciones pasan tal cual."""
    blueprint, parsed = _plan()
    original = parsed.meshes[0].positions.tolist()

    assert blueprint.meshes[0].vertices == [tuple(v) for v in original]


def test_la_v_se_invierte_como_en_obj() -> None:
    blueprint, parsed = _plan()
    source = parsed.meshes[0].uvs.tolist()

    assert blueprint.meshes[0].uvs[1][1] == pytest.approx(-source[1][1])
    assert blueprint.meshes[0].uvs[1][0] == pytest.approx(source[1][0])


def test_las_matrices_van_transpuestas_para_mathutils() -> None:
    """El juego usa convención de fila y `mathutils` la de columna."""
    blueprint, _ = _plan()
    skeleton = parse_o3d(build_o3d(SCENE))
    world = skeleton.world_matrices()

    root = blueprint.armature.bones[0]
    np.testing.assert_allclose(np.array(root.matrix), world[0].T, atol=1e-9)
    # La traslación acaba en la última columna, no en la última fila.
    np.testing.assert_allclose([row[3] for row in root.matrix[:3]], (0.0, 0.0, 4.0))


def test_ningun_hueso_tiene_longitud_cero() -> None:
    """Blender descarta al salir de modo edición los huesos de longitud cero."""
    blueprint, _ = _plan()
    assert all(bone.length > 0.0 for bone in blueprint.armature.bones)


def test_la_longitud_sale_de_la_distancia_al_hijo() -> None:
    blueprint, _ = _plan()
    by_name = {bone.name: bone for bone in blueprint.armature.bones}

    assert by_name["Bone_Root"].length == pytest.approx(2.0)
    # Una hoja hereda la longitud del padre en vez de quedarse sin cuerpo.
    assert by_name["Bone_Tip"].length == pytest.approx(2.0)
    # Una raíz sin hijos cae en el valor de reserva.
    assert by_name["Bone_Other"].length == pytest.approx(plan.FALLBACK_BONE_LENGTH)


def test_los_pesos_se_agrupan_por_nombre_de_hueso() -> None:
    """Los JOINTS_0 son índices locales; el nombre es lo único compartido."""
    parsed = parse_dcm(build_dcm(bones=("Bone_Root", "Bone_Tip")))
    skeleton = parse_o3d(build_o3d(SCENE))
    blueprint = plan.build_import_plan(parsed, skeleton, base_name="actor")

    weights = blueprint.meshes[0].weights
    assert set(weights) == {"Bone_Root", "Bone_Tip"}
    total = sum(w for entries in weights.values() for _, w in entries)
    assert total == pytest.approx(len(blueprint.meshes[0].vertices))


def test_un_hueso_que_no_esta_en_el_o3d_se_reporta() -> None:
    parsed = parse_dcm(build_dcm(bones=("Bone_Root", "Bone_Fantasma")))
    skeleton = parse_o3d(build_o3d(SCENE))
    blueprint = plan.build_import_plan(parsed, skeleton, base_name="actor")

    assert any("Bone_Fantasma" in note for note in blueprint.notes)
    assert "Bone_Fantasma" in blueprint.meshes[0].weights  # se conserva para exportar


def test_giro_frontal_afecta_malla_normales_y_armadura_sin_reflejar():
    parsed = parse_dcm(build_dcm())
    parsed.meshes[0].normals[:] = (1, 0, 0)
    skeleton = parse_o3d(build_o3d(SCENE))
    blueprint = plan.build_import_plan(parsed, skeleton, face_forward=True)
    rotation = np.diag([-1, -1, 1, 1])
    np.testing.assert_array_equal(blueprint.meshes[0].vertices,
                                  parsed.meshes[0].positions * [-1, -1, 1])
    np.testing.assert_array_equal(blueprint.meshes[0].normals, [[-1, 0, 0]] * 4)
    np.testing.assert_array_equal(blueprint.meshes[0].faces, parsed.meshes[0].indices)
    for bone, world in zip(blueprint.armature.bones, skeleton.world_matrices()):
        np.testing.assert_allclose(bone.matrix, rotation @ world.T)
    assert np.linalg.det(rotation) == 1
    for point in parsed.meshes[0].positions:
        assert plan.orient_vector(plan.orient_vector(point, True), True) == tuple(point)


def test_pesos_repetidos_del_mismo_hueso_se_suman():
    parsed = parse_dcm(build_dcm())
    parsed.meshes[0].joints[:] = 0
    weights = plan.build_import_plan(parsed).meshes[0].weights["Bone_Root"]
    assert weights == [(i, 1.0) for i in range(4)]


def test_una_malla_estatica_no_lleva_grupos_de_vertices() -> None:
    parsed = parse_dcm(build_dcm(skinned=False))
    blueprint = plan.build_import_plan(parsed, None, base_name="props")

    assert blueprint.armature is None
    assert not blueprint.meshes[0].is_skinned
    # Sin piel no procede avisar de que falta la armadura. (El quad de la
    # fixture sí dispara el falso positivo de «spikes» con 2 caras, que la
    # propia métrica marca como no concluyente.)
    assert not any("sin .o3d" in note for note in blueprint.notes)


def test_una_malla_con_piel_sin_o3d_avisa_de_que_no_hay_armadura() -> None:
    """Los pesos se conservan como grupos; que falte la armadura hay que decirlo."""
    parsed = parse_dcm(build_dcm())
    blueprint = plan.build_import_plan(parsed, None, base_name="actor")

    assert blueprint.armature is None
    assert blueprint.meshes[0].is_skinned
    assert any("sin .o3d" in note for note in blueprint.notes)


def test_cada_submalla_aporta_su_material() -> None:
    blueprint, _ = _plan()
    mesh = blueprint.meshes[0]

    assert mesh.materials == ["MatA"]
    assert len(mesh.face_materials) == len(mesh.faces)


def test_el_plan_y_los_parsers_se_importan_sin_blender() -> None:
    """El paquete no importa `bpy` hasta registrar: si lo hiciera, ni esta suite
    ni los scripts de corpus podrían usar los parsers fuera de Blender."""
    code = (
        "import sys; import io_darksiders.plan, io_darksiders.formats.mesh, "
        "io_darksiders.formats.native, io_darksiders.formats.obp, "
        "io_darksiders.formats.animation; "
        "assert 'bpy' not in sys.modules, 'bpy importado'"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
