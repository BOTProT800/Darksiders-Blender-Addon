"""Operadores, menús y preferencias del addon: todo lo que habla con Blender.

Código original. **No** es una actualización de los scripts de Blender 2.49 de
Szkaradek123: esos no declaran licencia y no se derivan. Aquí solo se llama a
`formats/`, que es implementación propia validada contra archivos reales del
juego.

Qué aporta frente a aquellos scripts, además de correr en Blender actual:

* el `.o3d` se lee como el contenedor etiquetado que es, así que aguanta nodos
  que omiten `Position`/`Rotation` (Deathinitive lo hace y el lector de offsets
  fijos se desincroniza ahí);
* los pesos se normalizan antes de crear los grupos de vértices;
* las mallas que no pasan las puertas de calidad se descartan con su motivo, en
  vez de meter geometría rota en la escena.
"""

# OJO: aquí NO se puede usar `from __future__ import annotations`. Blender lee
# `__annotations__` al registrar las clases y, con las anotaciones diferidas,
# las propiedades llegan como cadenas y el registro falla.

import hashlib
import json
import traceback
import uuid
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy.types import AddonPreferences, Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix

from . import plan, roundtrip
from .formats import animation, mesh, native, obp, skeleton, skin

# El paquete del addon: `bl_ext.<repositorio>.io_darksiders` instalado como
# extensión, `io_darksiders` cargado desde el código fuente.
ADDON = __package__


def modules() -> dict:
    """Los módulos que necesitan `roundtrip` y `animation_import`, por nombre."""
    return {
        "mesh": mesh,
        "native": native,
        "obp": obp,
        "skeleton": skeleton,
        "skin": skin,
        "animation": animation,
        "plan": plan,
    }


def _extension_identity(context):
    """``(repo_index, pkg_id)`` si el addon está instalado como extensión.

    Las extensiones se importan como `bl_ext.<repositorio>.<id>`; el código
    fuente cargado desde el repositorio no tiene esa forma y devuelve ``None``.
    """
    parts = (ADDON or "").split(".")
    if len(parts) != 3 or parts[0] != "bl_ext":
        return None
    for index, repo in enumerate(context.preferences.extensions.repos):
        if repo.module == parts[1] and getattr(repo, "source", "USER") != "SYSTEM":
            return index, parts[2]
    return None


def _uninstall_later(repo_index: int, pkg_id: str):
    """Desinstala fuera del operador que lo pidió.

    Desinstalar desactiva el addon y desregistra sus clases, incluida la del
    botón; hacerlo mientras ese operador se ejecuta es buscarse un cierre.
    """
    def run():
        try:
            bpy.ops.extensions.package_uninstall(repo_index=repo_index, pkg_id=pkg_id)
        except RuntimeError as exc:
            print(f"[Darksiders] ERROR: no se pudo desinstalar: {exc}")
        else:
            print(f"[Darksiders] INFO: {pkg_id} desinstalado")
        return None  # una sola vez

    return run


class DARKSIDERS_OT_uninstall(Operator):
    """Desactiva el addon y borra sus archivos de Blender. Se puede volver a instalar desde el zip"""

    bl_idname = "preferences.darksiders_uninstall"
    bl_label = "Desinstalar Darksiders"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return _extension_identity(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Desinstalar Darksiders",
            message="Se desactiva y se borra el addon. Tus .blend no cambian.",
            confirm_text="Desinstalar", icon="WARNING")

    def execute(self, context):
        identity = _extension_identity(context)
        if identity is None:
            self.report({"ERROR"}, "Este addon no está instalado como extensión")
            return {"CANCELLED"}
        bpy.app.timers.register(_uninstall_later(*identity), first_interval=0.0)
        return {"FINISHED"}


class DarksidersPreferences(AddonPreferences):
    bl_idname = ADDON

    def draw(self, context):
        layout = self.layout
        layout.label(text="Autosuficiente: lleva dentro todo lo que necesita", icon="CHECKMARK")
        if _extension_identity(context) is not None:
            layout.operator(DARKSIDERS_OT_uninstall.bl_idname, text="Desinstalar addon", icon="TRASH")
        else:
            layout.label(text="Cargado desde el código fuente: no se desinstala desde aquí", icon="INFO")


# ---------------------------------------------------------------------------
# Contexto y modo
# ---------------------------------------------------------------------------


class ModeError(RuntimeError):
    """No se pudo entrar en modo edición para construir la armadura."""


def _leave_edit_mode(context) -> None:
    """Devuelve la escena a modo objeto si algún otro objeto estaba en edición.

    Un import lanzado con el usuario en modo edición fallaría a media
    construcción, así que se normaliza antes de tocar nada.
    """
    active = context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")


def _set_mode(context, obj, mode: str) -> None:
    """Activa *obj* y entra en *mode*, con contexto sobreescrito si hace falta.

    Los operadores de importación se ejecutan desde el navegador de archivos y
    ahí `object.mode_set` puede no cumplir su `poll()`. Cuando pasa, se
    reintenta sobreescribiendo el contexto con una ventana y un área válidas.
    """
    context.view_layer.objects.active = obj
    try:
        obj.select_set(True)
    except RuntimeError:
        # El objeto no está en la view layer activa: no es fatal para mode_set.
        pass

    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode=mode)
        return

    window = getattr(context, "window", None) or next(
        iter(bpy.context.window_manager.windows), None
    )
    if window is not None:
        screen = window.screen
        area = next(
            (a for a in screen.areas if a.type == "VIEW_3D"),
            next(iter(screen.areas), None),
        )
        override = {"window": window, "screen": screen}
        if area is not None:
            override["area"] = area
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                override["region"] = region
        with context.temp_override(**override):
            if bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode=mode)
                return

    raise ModeError(
        "Blender no permite cambiar a modo edición desde este contexto. "
        "Abre una vista 3D y vuelve a importar."
    )


# ---------------------------------------------------------------------------
# Operadores
# ---------------------------------------------------------------------------


class _DarksidersImportBase(Operator, ImportHelper):
    """Base común: selección múltiple y fallos reportados con sentido."""

    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH")

    def _selected_paths(self) -> list:
        if self.files and self.directory:
            chosen = [Path(self.directory) / item.name for item in self.files if item.name]
            if chosen:
                return chosen
        return [Path(self.filepath)] if self.filepath else []

    def _say(self, level: str, message: str) -> None:
        """Reporta a la UI y **además** escribe en la consola del sistema.

        `Operator.report` llena el panel de informes y la línea de estado, pero
        no escribe en stdout: en una sesión con interfaz los avisos solo se ven
        desplegando el informe, y se pierden en cuanto se hace otra cosa. Con
        `print` quedan en `Window > Toggle System Console`, que es donde se van
        a buscar cuando algo ha ido mal y hay que copiarlo o pegarlo.

        En segundo plano (`--background`) Blender ya vuelca los reports a la
        consola, así que ahí cada mensaje sale dos veces. Es un precio pequeño
        frente a perderlos en la sesión con interfaz, que es la que se usa.
        """
        print(f"[Darksiders] {level}: {message}")
        self.report({level}, message)

    def _fail(self, exc: Exception) -> set:
        # El traceback va a la consola; en la UI solo el mensaje, que es lo que
        # cabe en una línea de estado.
        traceback.print_exc()
        self._say("ERROR", f"{type(exc).__name__}: {exc}")
        return {"CANCELLED"}


class DARKSIDERS_OT_import_dcm(_DarksidersImportBase):
    """Importa geometría .2 de Darksiders, con su esqueleto si está al lado"""

    bl_idname = "import_scene.darksiders_dcm"
    bl_label = "Importar geometría Darksiders (.2)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".2"
    filter_glob: StringProperty(default="*.2", options={"HIDDEN"})

    face_forward: BoolProperty(
        name="Frente para Blender (giro 180° Z)",
        description="Gira malla y esqueleto para ver el frente; se deshace al exportar .2",
        default=True,
    )
    skeleton_path: StringProperty(
        name="Esqueleto externo (.o3d)",
        description="Opcional: .o3d del personaje para piezas de equipo sin esqueleto propio",
        subtype="FILE_PATH", default="",
    )

    import_skeleton: BoolProperty(
        name="Importar esqueleto",
        description="Busca el .o3d con el mismo nombre y crea la armadura",
        default=True,
    )
    connect_bones: BoolProperty(
        name="Conectar huesos",
        description=(
            "Une cada hueso a su padre cuando coinciden en el espacio. Queda "
            "más limpio, pero mueve la cabeza de algunos huesos"
        ),
        default=False,
    )

    def execute(self, context):
        try:
            _leave_edit_mode(context)
        except Exception as exc:  # noqa: BLE001 - se reporta tal cual
            return self._fail(exc)

        created = 0
        for path in self._selected_paths():
            try:
                raw = path.read_bytes()
                parsed = mesh.parse_dcm(raw, path=path)
                rig = None
                skeleton_note = None
                if not self.import_skeleton:
                    skeleton_note = "importación de esqueleto desactivada"
                if self.import_skeleton:
                    rig = (skeleton.load_o3d(Path(self.skeleton_path))
                           if self.skeleton_path else skeleton.load_skeleton(path))
                    if rig is None:
                        # `load_skeleton` devuelve None tanto si no hay archivo
                        # como si lo hay y no sirve. Son cosas distintas y el
                        # usuario merece saber cuál le ha tocado: las piezas de
                        # equipo traen un `.o3d` que describe visuals (tela,
                        # nodo de acople) y no lleva esqueleto ninguno.
                        vecino = skeleton.find_o3d(path)
                        skeleton_note = (
                            f"{vecino.name} no trae esqueleto"
                            if vecino is not None
                            else "sin .o3d al lado"
                        )
                blueprint = plan.build_import_plan(
                    parsed,
                    rig,
                    base_name=path.stem,
                    skeleton_note=skeleton_note,
                    face_forward=self.face_forward,
                )
                if not blueprint.meshes:
                    raise ValueError("ninguna malla superó la validación: " + "; ".join(blueprint.notes))
                _build(context, blueprint, connect=self.connect_bones, source_path=path,
                       source_hash=hashlib.sha256(raw).hexdigest(), face_forward=self.face_forward)
            except ModeError as exc:
                return self._fail(exc)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._say("WARNING", f"{path.name}: {exc}")
                continue
            created += 1
            for note in blueprint.notes:
                self._say("WARNING", f"{path.name}: {note}")

        if not created:
            self._say("ERROR", "No se pudo importar ningún archivo")
            return {"CANCELLED"}
        self._say("INFO", f"{created} archivo(s) importados")
        return {"FINISHED"}


class DARKSIDERS_OT_import_o3d(_DarksidersImportBase):
    """Importa solo el esqueleto de un .o3d de Darksiders 1 o 2"""

    bl_idname = "import_scene.darksiders_o3d"
    bl_label = "Importar esqueleto Darksiders (.o3d)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".o3d"
    filter_glob: StringProperty(default="*.o3d", options={"HIDDEN"})

    connect_bones: BoolProperty(name="Conectar huesos", default=False)
    face_forward: BoolProperty(name="Frente para Blender (giro 180° Z)", default=True)

    def execute(self, context):
        try:
            _leave_edit_mode(context)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)

        created = 0
        for path in self._selected_paths():
            try:
                rig = skeleton.load_o3d(path)
                blueprint = plan.ImportPlan(
                    name=path.stem, armature=plan.build_armature_plan(rig, face_forward=self.face_forward)
                )
                _build(context, blueprint, connect=self.connect_bones)
            except ModeError as exc:
                return self._fail(exc)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._say("WARNING", f"{path.name}: {exc}")
                continue
            created += 1

        if not created:
            self._say("ERROR", "No se pudo importar ningún esqueleto")
            return {"CANCELLED"}
        self._say("INFO", f"{created} esqueleto(s) importados")
        return {"FINISHED"}


class DARKSIDERS_OT_import_anm(_DarksidersImportBase):
    """Importa clips ANM sobre la armadura activa importada con este addon"""

    bl_idname = "import_scene.darksiders_anm"
    bl_label = "Importar animación Darksiders (.anm)"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={"HIDDEN"})
    allow_partial: BoolProperty(
        name="Permitir pistas sin hueso",
        description="Crea una acción parcial con advertencias si faltan nodos; comprueba que el rig es correcto",
        default=False,
    )

    def execute(self, context):
        from . import animation_import
        try:
            rig = animation_import.active_rig(context)
            _leave_edit_mode(context)
        except Exception as exc:  # noqa: BLE001
            return self._fail(exc)
        created = 0
        for path in self._selected_paths():
            try:
                action, notes = animation_import.import_clip(
                    context, rig, path, modules(), allow_partial=self.allow_partial)
                created += 1
                for note in notes:
                    self._say("WARNING", f"{path.name}: {note}")
                self._say("INFO", f"Acción {action.name}: {action.frame_end - action.frame_start:.2f} "
                          f"fotogramas a {action['anansi_scene_fps']:g} FPS de escena")
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._say("WARNING", f"{path.name}: {exc}")
        if not created:
            self._say("ERROR", "No se creó ninguna acción; consulta el motivo en la consola")
            return {"CANCELLED"}
        self._say("INFO", f"{created} acción(es) importadas; la última queda activa")
        return {"FINISHED"}


class DARKSIDERS_OT_unpack_obp(_DarksidersImportBase):
    """Saca de un .obp sus modelos (.2), esqueletos (.o3d) y animaciones (.anm)"""

    bl_idname = "import_scene.darksiders_obp"
    bl_label = "Desempaquetar contenedor Darksiders (.obp)"
    bl_options = {"REGISTER"}

    filename_ext = ".obp"
    filter_glob: StringProperty(default="*.obp", options={"HIDDEN"})

    def execute(self, context):
        total = 0
        target = None
        for path in self._selected_paths():
            try:
                target = path.parent / f"{path.stem}_extraido"
                result = obp.extract_obp(path, target)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._say("WARNING", f"{path.name}: {exc}")
                continue
            total += len(result.written)
            self._say("WARNING" if result.failed else "INFO", result.summary())

        if not total:
            self._say("ERROR", "No se extrajo ningún modelo, esqueleto ni animación")
            return {"CANCELLED"}
        self._say("INFO", f"{total} recursos en {target}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Construcción en la escena
# ---------------------------------------------------------------------------


def _build(context, blueprint, *, connect: bool = False, source_path=None,
           source_hash="", face_forward=False):
    """Construye el plan en la escena y devuelve el objeto raíz."""
    collection = context.collection
    armature_object = None
    bone_names = {}
    # Los materiales se comparten entre las mallas de **este** modelo, pero no
    # se reutilizan los que ya hubiera en el .blend: coincidir de nombre con un
    # material del usuario y adoptarlo sería una sorpresa desagradable.
    materials = {}
    instance = uuid.uuid4().hex

    if blueprint.armature is not None:
        armature_object, bone_names = _build_armature(
            context, collection, blueprint.armature, connect=connect
        )
        if source_path:
            armature_object[roundtrip.PREFIX + "rest"] = json.dumps(roundtrip.rig_state(armature_object))

    for mesh_plan in blueprint.meshes:
        mesh_object = _build_mesh(collection, mesh_plan, bone_names, materials)
        if source_path:
            roundtrip.remember(mesh_object, path=source_path, digest=source_hash,
                               instance=instance, index=mesh_plan.source_index,
                               face_forward=face_forward,
                               group_names={name: group.name for name, group in
                                            zip(mesh_plan.weights, mesh_object.vertex_groups)})
            mesh_object.select_set(True)
            context.view_layer.objects.active = mesh_object
        if armature_object is not None and mesh_plan.is_skinned:
            mesh_object.parent = armature_object
            mesh_object.matrix_parent_inverse = armature_object.matrix_world.inverted()
            modifier = mesh_object.modifiers.new("Armature", "ARMATURE")
            modifier.object = armature_object
    return armature_object


def _build_armature(context, collection, armature_plan, *, connect: bool):
    data = bpy.data.armatures.new(armature_plan.name)
    obj = bpy.data.objects.new(armature_plan.name, data)
    collection.objects.link(obj)

    previous = context.view_layer.objects.active
    _set_mode(context, obj, "EDIT")
    try:
        # Blender puede renombrar un hueso si el nombre colisiona o pasa de 63
        # caracteres, así que se guarda el nombre real que acabó teniendo.
        edit_bones = {}
        for bone in armature_plan.bones:
            edit = data.edit_bones.new(bone.name)
            # Un hueso de longitud cero se descarta al salir de modo edición,
            # así que primero se le da cuerpo y luego se orienta.
            edit.head = (0.0, 0.0, 0.0)
            edit.tail = (0.0, bone.length, 0.0)
            edit_bones[bone.name] = edit

        for bone in armature_plan.bones:
            if bone.parent:
                edit_bones[bone.name].parent = edit_bones[bone.parent]

        for bone in armature_plan.bones:
            edit = edit_bones[bone.name]
            edit.matrix = Matrix(bone.matrix)
            edit.length = bone.length

        if connect:
            for bone in armature_plan.bones:
                edit = edit_bones[bone.name]
                parent = edit.parent
                if parent is not None and (parent.tail - edit.head).length < 1e-4:
                    edit.use_connect = True

        names = {name: edit.name for name, edit in edit_bones.items()}
    finally:
        # Salir de edición es obligatorio aunque algo haya fallado: dejar la
        # escena en modo edición con una armadura a medias es peor que el error.
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")
        if previous is not None:
            context.view_layer.objects.active = previous
    if armature_plan.animation_source:
        from . import animation_import
        animation_import.remember_rig(obj, armature_plan, names)
    return obj, names


def _build_mesh(collection, mesh_plan, bone_names, materials):
    mesh_data = bpy.data.meshes.new(mesh_plan.name)
    mesh_data.from_pydata(mesh_plan.vertices, [], mesh_plan.faces)
    mesh_data.validate(verbose=False)

    for label in mesh_plan.materials:
        material = materials.get(label)
        if material is None:
            material = bpy.data.materials.new(label)
            material.use_nodes = True
            materials[label] = material
        mesh_data.materials.append(material)

    # `validate()` puede haber tirado caras; si las cuentas no cuadran no se
    # reparten materiales por cara, porque el índice ya no significaría lo mismo.
    intact = len(mesh_data.polygons) == len(mesh_plan.faces)
    if intact and len(mesh_plan.materials) > 1:
        for polygon, slot in zip(mesh_data.polygons, mesh_plan.face_materials):
            polygon.material_index = slot

    if mesh_plan.uvs:
        uv_layer = mesh_data.uv_layers.new(name="UVMap")
        limit = len(mesh_plan.uvs)
        for loop in mesh_data.loops:
            if loop.vertex_index < limit:
                uv_layer.data[loop.index].uv = mesh_plan.uvs[loop.vertex_index]

    mesh_data.update()
    if mesh_plan.normals:
        for polygon in mesh_data.polygons:
            polygon.use_smooth = True
        mesh_data.normals_split_custom_set_from_vertices(mesh_plan.normals)
    obj = bpy.data.objects.new(mesh_plan.name, mesh_data)
    collection.objects.link(obj)

    vertex_count = len(mesh_data.vertices)
    for bone, entries in mesh_plan.weights.items():
        group = obj.vertex_groups.new(name=bone_names.get(bone, bone))
        # `add()` acepta una lista de vértices por peso, así que agrupar por
        # valor convierte decenas de miles de llamadas en unas pocas. Se agrupa
        # por el float exacto: no se redondea nada.
        buckets = {}
        for vertex, weight in entries:
            if vertex < vertex_count:
                buckets.setdefault(weight, []).append(vertex)
        for weight, indices in buckets.items():
            group.add(indices, weight, "REPLACE")
    return obj


class DARKSIDERS_OT_export_dcm(Operator, ExportHelper):
    """Exporta una copia .2 del modelo importado activo; admite borrar y, en mallas con piel, geometría nueva"""

    bl_idname = "export_scene.darksiders_dcm"
    bl_label = "Exportar copia Darksiders (.2)"
    filename_ext = ".2"
    filter_glob: StringProperty(default="*.2", options={"HIDDEN"})

    def invoke(self, context, event):
        active = context.view_layer.objects.active
        source = active.get(roundtrip.PREFIX + "source") if active is not None else None
        if not source or not active.get(roundtrip.PREFIX + "instance"):
            self.report({"ERROR"}, "Activa una malla importada con este addon")
            return {"CANCELLED"}
        # Conserva la carpeta anterior, pero usa el nombre del recurso activo.
        folder = Path(self.filepath or context.blend_data.filepath).parent
        self.filepath = str(folder / Path(source).name)
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        self.layout.label(text="Modelo completo de la malla activa")
        self.layout.label(text="Borrar conserva los registros originales")
        self.layout.label(text="Geometría nueva (piel): se reescribe la malla")
        self.layout.label(text="Conservar esqueleto, paleta y materiales originales")
        active = context.view_layer.objects.active
        source = active.get(roundtrip.PREFIX + "source") if active is not None else None
        if source:
            self.layout.label(text=f"Nombre original: {Path(source).name}")

    def execute(self, context):
        try:
            _leave_edit_mode(context)
            notices = []
            target = roundtrip.export_asset(context, self.filepath, modules(), notices)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            print(f"[Darksiders] ERROR: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for level, message in notices:
            self.report({level}, message)
            print(f"[Darksiders] {level}: {message}")
        self.report({"INFO"}, f"Copia .2 exportada: {target.name}")
        print(f"[Darksiders] INFO: Copia .2 exportada: {target}")
        source_name = Path(context.view_layer.objects.active[roundtrip.PREFIX + "source"]).name
        if target.name.casefold() != source_name.casefold():
            message = f"Para reemplazar el recurso original conserva su nombre: {source_name}"
            self.report({"WARNING"}, message)
            print(f"[Darksiders] WARNING: {message}")
        return {"FINISHED"}


def _menu_export(self, context):
    self.layout.operator(DARKSIDERS_OT_export_dcm.bl_idname, text="Darksiders copia del modelo (.2)")


def _menu_import(self, context):
    self.layout.operator(DARKSIDERS_OT_import_anm.bl_idname, text="Darksiders animación (.anm)")
    self.layout.operator(
        DARKSIDERS_OT_import_dcm.bl_idname, text="Darksiders geometría (.2)"
    )
    self.layout.operator(
        DARKSIDERS_OT_import_o3d.bl_idname, text="Darksiders esqueleto (.o3d)"
    )
    self.layout.operator(
        DARKSIDERS_OT_unpack_obp.bl_idname, text="Darksiders contenedor (.obp)"
    )


_CLASSES = (
    DARKSIDERS_OT_uninstall,
    DarksidersPreferences,
    DARKSIDERS_OT_import_dcm,
    DARKSIDERS_OT_import_o3d,
    DARKSIDERS_OT_import_anm,
    DARKSIDERS_OT_unpack_obp,
    DARKSIDERS_OT_export_dcm,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
