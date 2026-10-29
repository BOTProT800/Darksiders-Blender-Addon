"""Ejercita la capa `bpy` del addon contra un doble de la API de Blender.

Advertencia sobre el alcance: `tests/_fake_bpy.py` reproduce solo la superficie
de Blender que el addon toca, así que estos tests demuestran que **el addon
ejecuta y cablea bien lo suyo**, no que Blender se comporte como esperamos.
Sirven para cazar erratas, orden de llamadas y cableado —que es lo que se
rompe al escribir a ciegas—, y no sustituyen a abrir Blender.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from io_darksiders import plan
from io_darksiders.formats.mesh import parse_dcm
from io_darksiders.formats.skeleton import parse_o3d
from tests import _fake_bpy
from tests.test_mesh import build_dcm
from tests.test_skeleton import build_o3d

# Los módulos que importan `bpy`. El resto del paquete no lo necesita y se
# queda en `sys.modules` entre tests.
BLENDER_MODULES = ("io_darksiders.operators", "io_darksiders.animation_import")

SCENE = [
    {
        "name": "Bone_Root",
        "position": [0.0, 0.0, 4.0],
        "children": [{"name": "Bone_Tip", "position": [0.0, 2.0, 0.0]}],
    }
]


@pytest.fixture
def addon():
    """Instala el doble de `bpy`, importa los operadores y limpia al terminar."""
    fake = _fake_bpy.install()
    for name in BLENDER_MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module("io_darksiders.operators")
    try:
        yield module, fake
    finally:
        for name in BLENDER_MODULES:
            sys.modules.pop(name, None)
        _fake_bpy.uninstall()


def _blueprint(**kwargs):
    parsed = parse_dcm(build_dcm(**kwargs))
    skeleton = parse_o3d(build_o3d(SCENE))
    return plan.build_import_plan(parsed, skeleton, base_name="actor")


def test_el_addon_se_registra_y_al_quitarlo_olvida_sus_submodulos(addon) -> None:
    """Sin olvidarlos, otra versión instalada sin reiniciar heredaría los viejos."""
    package = importlib.import_module("io_darksiders")
    assert package.bl_info["blender"] >= (4, 2, 0)
    saved = {n: m for n, m in sys.modules.items() if n.startswith("io_darksiders.")}
    try:
        package.register()
        package.unregister()
        assert not [n for n in sys.modules if n.startswith("io_darksiders.")]
    finally:
        sys.modules.update(saved)


def test_build_crea_armadura_y_malla(addon) -> None:
    module, fake = addon
    module._build(fake.context, _blueprint())

    linked = fake.context.collection.objects.linked
    assert [obj.name for obj in linked] == ["actor", "actor_00"]
    armature, mesh_object = linked
    assert len(armature.data.edit_bones) == 2
    assert len(mesh_object.data.polygons) == 2


def test_la_malla_queda_emparentada_con_modificador(addon) -> None:
    module, fake = addon
    module._build(fake.context, _blueprint())
    armature, mesh_object = fake.context.collection.objects.linked

    assert mesh_object.parent is armature
    assert len(mesh_object.modifiers) == 1
    modifier = next(iter(mesh_object.modifiers))
    assert modifier.type == "ARMATURE" and modifier.object is armature


def test_los_huesos_quedan_emparentados_y_con_cuerpo(addon) -> None:
    module, fake = addon
    module._build(fake.context, _blueprint())
    armature = fake.context.collection.objects.linked[0]

    bones = {bone.name: bone for bone in armature.data.edit_bones}
    assert bones["Bone_Tip"].parent is bones["Bone_Root"]
    # Ningún hueso puede acabar con longitud cero: Blender los descartaría.
    assert all(bone.length > 0.0 for bone in bones.values())
    # La cabeza sale de la traslación de la matriz de mundo.
    assert bones["Bone_Root"].head == pytest.approx((0.0, 0.0, 4.0))
    assert bones["Bone_Tip"].head == pytest.approx((0.0, 2.0, 4.0))


def test_se_sale_de_modo_edicion_siempre(addon) -> None:
    module, fake = addon
    module._build(fake.context, _blueprint())

    assert fake.mode_set.calls == ["EDIT", "OBJECT"]


def test_se_sale_de_modo_edicion_aunque_falle_a_medias(addon) -> None:
    """Dejar la escena en edición con una armadura a medias es peor que el error."""
    module, fake = addon
    blueprint = _blueprint()
    # Un padre que no existe revienta el emparentado dentro del bloque `try`.
    blueprint.armature.bones[1].parent = "Bone_Inexistente"

    with pytest.raises(KeyError):
        module._build(fake.context, blueprint)
    assert fake.mode_set.calls[-1] == "OBJECT"


def test_si_mode_set_no_puede_se_reintenta_con_contexto_sobreescrito(addon) -> None:
    """Desde el navegador de archivos `mode_set` puede no cumplir su poll()."""
    module, fake = addon
    fake.mode_set.poll_result = False
    fake.context.window = None
    fake.bpy.context.window_manager.windows = []

    with pytest.raises(module.ModeError, match="vista 3D"):
        module._build(fake.context, _blueprint())


def test_los_grupos_de_vertices_llevan_el_nombre_del_hueso(addon) -> None:
    module, fake = addon
    module._build(fake.context, _blueprint())
    mesh_object = fake.context.collection.objects.linked[1]

    names = {group.name for group in mesh_object.vertex_groups}
    assert names == {"Bone_Root", "Bone_Tip"}
    total = sum(
        weight
        for group in mesh_object.vertex_groups
        for weight in group.weights.values()
    )
    assert total == pytest.approx(len(mesh_object.data.vertices))


def test_los_pesos_se_agrupan_para_no_llamar_a_add_por_vertice(addon) -> None:
    """`add()` acepta varios vértices por peso; agrupar ahorra miles de llamadas."""
    module, fake = addon
    module._build(fake.context, _blueprint())
    mesh_object = fake.context.collection.objects.linked[1]

    for group in mesh_object.vertex_groups:
        distintos = len(set(group.weights.values()))
        assert group.calls == distintos
        assert group.calls < len(group.weights) or len(group.weights) == 1


def test_un_hueso_renombrado_por_blender_arrastra_su_grupo(addon) -> None:
    """Blender trunca a 63 caracteres; el grupo tiene que seguir al nombre real."""
    module, fake = addon
    blueprint = _blueprint()
    largo = "Bone_" + "X" * 80
    blueprint.armature.bones[0].name = largo
    for mesh_plan in blueprint.meshes:
        mesh_plan.weights[largo] = mesh_plan.weights.pop("Bone_Root")
    blueprint.armature.bones[1].parent = largo

    module._build(fake.context, blueprint)
    armature, mesh_object = fake.context.collection.objects.linked

    real = next(iter(armature.data.edit_bones)).name
    assert len(real) == 63
    assert real in {group.name for group in mesh_object.vertex_groups}


def test_las_uv_se_asignan_por_loop_desde_el_vertice(addon) -> None:
    module, fake = addon
    blueprint = _blueprint()
    module._build(fake.context, blueprint)
    mesh = fake.context.collection.objects.linked[1].data

    layer = mesh.uv_layers._layers[0]
    esperado = blueprint.meshes[0].uvs
    for loop in mesh.loops:
        assert layer.data[loop.index].uv == pytest.approx(esperado[loop.vertex_index])


def test_los_materiales_se_comparten_entre_mallas_del_mismo_modelo(addon) -> None:
    module, fake = addon
    blueprint = _blueprint()
    # Dos mallas que declaran el mismo material.
    blueprint.meshes.append(blueprint.meshes[0])
    module._build(fake.context, blueprint)

    assert [m.name for m in fake.data.materials_made] == ["MatA"]


def test_no_se_adoptan_materiales_ya_existentes_del_blend(addon) -> None:
    """Coincidir de nombre con un material del usuario no debe secuestrarlo."""
    module, fake = addon
    llamadas = []
    fake.data.materials.get = lambda name: llamadas.append(name)

    module._build(fake.context, _blueprint())
    assert llamadas == []


def test_una_malla_estatica_no_crea_armadura_ni_grupos(addon) -> None:
    module, fake = addon
    parsed = parse_dcm(build_dcm(skinned=False))
    blueprint = plan.build_import_plan(parsed, None, base_name="props")

    module._build(fake.context, blueprint)
    linked = fake.context.collection.objects.linked

    assert [obj.name for obj in linked] == ["props_00"]
    assert len(linked[0].vertex_groups) == 0
    assert linked[0].parent is None
    assert fake.mode_set.calls == []


class _Panel:
    """Lo justo de `UILayout` para leer qué dibuja el panel de preferencias."""

    def __init__(self):
        self.operators, self.alert = [], False

    def label(self, text="", icon="NONE"):
        pass

    def operator(self, idname, text="", icon="NONE"):
        self.operators.append(idname)

    def box(self):
        return self


def _con_repos(*modulos):
    repos = [SimpleNamespace(module=m, source="USER") for m in modulos]
    return SimpleNamespace(preferences=SimpleNamespace(extensions=SimpleNamespace(repos=repos)))


def test_cada_version_trae_el_boton_de_desinstalar(addon, monkeypatch) -> None:
    """Política de `AGENTS.md` §«Autosuficiente y con botón de desinstalar».

    Instalado como extensión, el panel ofrece desinstalar, y el botón aplaza la
    desinstalación a un temporizador: hacerla dentro del operador desregistraría
    su propia clase mientras se ejecuta.
    """
    module, fake = addon
    assert module.DARKSIDERS_OT_uninstall in module._CLASSES
    monkeypatch.setattr(module, "ADDON", "bl_ext.user_default.io_darksiders")
    context = _con_repos("blender_org", "user_default")

    prefs = module.DarksidersPreferences()
    prefs.layout = _Panel()
    prefs.draw(context)
    assert prefs.layout.operators == [module.DARKSIDERS_OT_uninstall.bl_idname]

    temporizadores, desinstalados = [], []
    fake.bpy.app = SimpleNamespace(timers=SimpleNamespace(
        register=lambda fn, first_interval=0.0: temporizadores.append(fn)))
    fake.bpy.ops.extensions = SimpleNamespace(package_uninstall=lambda **kw: desinstalados.append(kw))

    assert module.DARKSIDERS_OT_uninstall().execute(context) == {"FINISHED"}
    assert desinstalados == []
    temporizadores[0]()
    assert desinstalados == [{"repo_index": 1, "pkg_id": "io_darksiders"}]


def test_las_preferencias_no_guardan_ninguna_ruta(addon) -> None:
    """Hasta la 1.3.0 había una carpeta en las preferencias que nadie sabía para qué era.
    El addon no necesita configurar nada: sus preferencias no tienen propiedades."""
    module, _ = addon
    assert not getattr(module.DarksidersPreferences, "__annotations__", {})


def test_desde_el_codigo_fuente_no_se_ofrece_desinstalar(addon) -> None:
    """Un enlace al repo se borraría siguiéndolo: ahí no hay botón."""
    module, _ = addon
    prefs = module.DarksidersPreferences()
    prefs.layout = _Panel()
    prefs.draw(_con_repos("user_default"))

    assert prefs.layout.operators == []
    assert module.DARKSIDERS_OT_uninstall.poll(_con_repos("user_default")) is False


def test_los_avisos_van_tambien_a_la_consola(addon, capsys) -> None:
    """`report` no escribe en stdout, y ahí es donde se van a buscar.

    En una sesión con interfaz los avisos del panel de informes se pierden al
    siguiente clic; en la consola del sistema quedan y se pueden copiar.
    """
    module, _ = addon

    recogidos = []

    class OperadorFalso(module._DarksidersImportBase):
        def report(self, tipo, mensaje):
            recogidos.append((tipo, mensaje))

    op = OperadorFalso.__new__(OperadorFalso)
    op._say("WARNING", "la AABB no cuadra")

    assert recogidos == [({"WARNING"}, "la AABB no cuadra")]
    assert "[Darksiders] WARNING: la AABB no cuadra" in capsys.readouterr().out
