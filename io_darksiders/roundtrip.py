"""Metadatos persistentes y exportación de una importación .2 de Blender.

Cada vértice recuerda su índice en la plantilla (`anansi_vertex_id`), y Blender
conserva ese atributo al borrar. Así se reconoce un borrado: primero se poda la
plantilla (`native.prune_dcm`) y después se aplican las ediciones sobre la copia
podada, cuyos vértices ya coinciden uno a uno con los de la escena. Se rechazan
los cambios que necesitarían inventar registros o formatos todavía desconocidos.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .formats.skin import normalise_weights
from .plan import orient_vector

# Prefijo de las propiedades que se guardan en el .blend. Se llama así porque
# el addon nació dentro de ANANSI, y se conserva para que las escenas
# importadas con las versiones 1.x sigan pudiendo exportar.
PREFIX = "anansi_"


def rig_state(obj):
    return {bone.name: {"parent": bone.parent.name if bone.parent else None,
                        "matrix": [list(row) for row in bone.matrix_local]}
            for bone in obj.data.bones}


def remember(obj, *, path, digest, instance, index, face_forward, group_names):
    obj[PREFIX + "source"] = str(Path(path).resolve())
    obj[PREFIX + "sha256"] = digest
    obj[PREFIX + "instance"] = instance
    obj[PREFIX + "mesh_index"] = index
    obj[PREFIX + "face_forward"] = face_forward
    obj[PREFIX + "groups"] = json.dumps(group_names)
    ids = obj.data.attributes.new(name=PREFIX + "vertex_id", type="INT", domain="POINT")
    ids.data.foreach_set("value", list(range(len(obj.data.vertices))))


def _weights(obj, original, native, slot):
    n, palette = original.vertex_count, [bone.name for bone in original.bones]
    names = json.loads(obj[PREFIX + "groups"])
    by_group = {group.index: palette.index(name) for name, group_name in names.items()
                if name in palette for group in obj.vertex_groups if group.name == group_name}
    dense = np.zeros((n, len(palette)), dtype=np.float32)
    for vertex in obj.data.vertices:
        row = slot[vertex.index]
        if row < 0:
            continue  # se quedó sin triángulos y no viaja a la copia
        for entry in vertex.groups:
            if entry.weight <= 0:
                continue
            if entry.group not in by_group:
                raise native.DcmExportError("hay pesos en grupos ajenos a la paleta original")
            dense[row, by_group[entry.group]] += entry.weight
    weights, _ = normalise_weights(original.weights)
    expected = np.zeros_like(dense)
    for slot in range(4):
        np.add.at(expected, (np.arange(n), original.joints[:, slot]), weights[:, slot])
    if np.allclose(dense, expected, atol=1e-7, rtol=1e-6):
        return None, None  # conserva redondeos, slots vacíos y orden originales
    counts = (dense > 0).sum(1)
    if np.any(counts == 0) or np.any(counts > 4) or not np.isfinite(dense).all():
        raise native.DcmExportError("cada vértice necesita entre 1 y 4 influencias finitas")
    joints = np.zeros((n, 4), dtype=np.uint8)
    result = np.zeros((n, 4), dtype=np.float32)
    for vertex in range(n):
        used = np.flatnonzero(dense[vertex] > 0)
        if np.any(used > 255):
            raise native.DcmExportError("la paleta excede los índices u8 del formato")
        joints[vertex, :len(used)] = used
        result[vertex, :len(used)] = dense[vertex, used] / dense[vertex, used].sum()
    return joints, result


def _shading(obj, original, source, everywhere, slot, *, moved, native):
    """Calcula normales y tangentes en una copia temporal, sin editar la escena.

    ``everywhere`` trae la posición nativa de cada vértice de la escena y
    ``slot`` su fila en la plantilla (-1 si no viaja); el resultado va en filas
    de la plantilla.
    """
    import bpy

    mesh = obj.data.copy()
    try:
        # Espacio nativo: ya deshicimos el giro de presentación y horneamos
        # matrix_world. La UV sigue en convención Blender para tangent.w.
        for vertex, point in zip(mesh.vertices, everywhere):
            vertex.co = point
        if moved:
            mesh.normals_split_custom_set_from_vertices([(0, 0, 0)] * len(mesh.vertices))
        else:
            custom = np.tile([0.0, 0.0, 1.0], (len(mesh.vertices), 1))
            custom[slot >= 0] = original.normals[slot[slot >= 0]]
            mesh.normals_split_custom_set_from_vertices(custom.tolist())
        mesh.update()
        mesh.calc_tangents(uvmap=mesh.uv_layers.active.name)
        n = original.vertex_count
        normals = np.zeros((n, 3), dtype=np.float64)
        tangents = np.zeros_like(normals)
        signs = [set() for _ in range(n)]
        for loop, normal in zip(mesh.loops, mesh.corner_normals):
            i = slot[loop.vertex_index]
            normals[i] += normal.vector
            tangents[i] += loop.tangent
            if loop.bitangent_sign:
                signs[i].add(int(loop.bitangent_sign))
        at = original.data_base + 8 + original.indices.size * 2 + n * 20
        old_tangents = np.ndarray((n, 4), dtype="<f4", buffer=source, offset=at + 36, strides=(52, 4))
        handedness = []
        for i, values in enumerate(signs):
            if len(values) > 1:
                raise native.DcmExportError("UV espejadas comparten un vértice: requieren dividir la topología")
            if not values:
                normals[i] = original.normals[i]
                tangents[i] = old_tangents[i, :3]
            handedness.append(next(iter(values), old_tangents[i, 3]))
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
        tangents -= normals * np.sum(normals * tangents, axis=1, keepdims=True)
        tangents /= np.maximum(np.linalg.norm(tangents, axis=1, keepdims=True), 1e-30)
        return normals, np.c_[tangents, handedness]
    finally:
        bpy.data.meshes.remove(mesh)


def export_asset(context, target, modules):
    native = modules["native"]
    active = context.view_layer.objects.active
    if active is None or not active.get(PREFIX + "instance"):
        raise native.DcmExportError("activa una malla importada con esta versión del addon")
    source_path = Path(active[PREFIX + "source"])
    source = source_path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest != active[PREFIX + "sha256"]:
        raise native.DcmExportError("el archivo original cambió desde la importación; vuelve a importarlo")
    parsed = modules["mesh"].parse_dcm(source, path=source_path)
    instance = active[PREFIX + "instance"]
    objects = [obj for obj in context.scene.objects if obj.type == "MESH"
               and obj.get(PREFIX + "instance") == instance]
    indices = [obj.get(PREFIX + "mesh_index") for obj in objects]
    if sorted(indices) != list(range(len(parsed.meshes))):
        raise native.DcmExportError("faltan mallas o hay duplicados de esta importación; se necesita el modelo completo")
    # Fase 1: qué queda de cada malla. slot[i] es la fila en la plantilla del
    # vértice i de la escena, o -1 si se quedó sin triángulos y no viaja.
    subsets, slots = {}, {}
    for obj in objects:
        if obj.get(PREFIX + "sha256") != digest or obj.get(PREFIX + "source") != str(source_path):
            raise native.DcmExportError("las mallas no comparten la misma plantilla")
        original = parsed.meshes[obj[PREFIX + "mesh_index"]]
        ids = _vertex_ids(obj, native)
        try:
            subset = native.deletion_subset(original, ids, [p.vertices for p in obj.data.polygons])
        except native.DcmExportError as exc:
            raise native.DcmExportError(f"{obj.name}: {exc}") from exc
        rows = np.arange(ids.size) if subset is None else np.searchsorted(ids, subset.vertices)
        slot = np.full(ids.size, -1, dtype=np.int64)
        slot[rows] = np.arange(rows.size)
        slots[original.index] = slot
        if subset is not None:
            subsets[original.index] = subset
    # Fase 2: la plantilla podada coincide vértice a vértice con la escena.
    template = source
    if subsets:
        template = native.prune_dcm(source, subsets)
        parsed = modules["mesh"].parse_dcm(template, path=source_path)
    edits = {}
    for obj in objects:
        original = parsed.meshes[obj[PREFIX + "mesh_index"]]
        mesh = obj.data
        slot = slots[original.index]
        rows = np.flatnonzero(slot >= 0)
        if obj.matrix_world.to_3x3().determinant() <= 0:
            raise native.DcmExportError("transformación reflejada o colapsada: aplica una transformación de determinante positivo")
        for modifier in obj.modifiers:
            if modifier.type != "ARMATURE" and (modifier.show_viewport or modifier.show_render):
                raise native.DcmExportError("hay modificadores activos sin aplicar; esta exportación usa la malla base")
            if modifier.type == "ARMATURE" and modifier.object:
                rig = modifier.object
                stored = rig.get(PREFIX + "rest")
                current = rig_state(rig)
                rest = json.loads(stored) if stored else {}
                if (set(current) != set(rest) or any(
                        current[k]["parent"] != rest[k]["parent"] or not np.allclose(
                            current[k]["matrix"], rest[k]["matrix"], atol=1e-5, rtol=0)
                        for k in current)):
                    raise native.DcmExportError("el esqueleto fue editado; no está implementada la escritura de .o3d")
                if (not np.allclose(np.asarray(rig.matrix_world), np.eye(4), atol=1e-6)
                        or any(not np.allclose(np.asarray(b.matrix_basis), np.eye(4), atol=1e-6)
                               for b in rig.pose.bones)):
                    raise native.DcmExportError("exporta en pose de descanso, sin transformar la armadura")
        expected = modules["plan"].build_mesh_plan(original, original.index, "original")
        if [p.material_index for p in mesh.polygons] != expected.face_materials:
            raise native.DcmExportError("asignación de materiales modificada; conserva los tramos originales")
        forward = bool(obj[PREFIX + "face_forward"])
        everywhere = np.asarray([orient_vector(obj.matrix_world @ v.co, forward) for v in mesh.vertices],
                                dtype=np.float32).reshape(-1, 3)
        positions = everywhere[rows]
        uvs = original.uvs.copy()
        if mesh.uv_layers.active is None:
            raise native.DcmExportError("falta el mapa UV original")
        seen = {}
        for loop, uv in zip(mesh.loops, mesh.uv_layers.active.data):
            point = (float(uv.uv[0]), -float(uv.uv[1]))
            row = slot[loop.vertex_index]
            if row in seen and seen[row] != point:
                raise native.DcmExportError("una costura UV nueva necesita dividir vértices; topología no soportada")
            seen[row] = point
            uvs[row] = point
        moved = not np.array_equal(positions, original.positions)
        changed_uv = not np.array_equal(uvs, original.uvs)
        normals = tangents = None
        if moved or changed_uv:
            if not original.is_skinned:
                raise native.DcmExportError("edición de mallas estáticas aún no soportada")
            normals, tangents = _shading(obj, original, template, everywhere, slot, moved=moved,
                                         native=native)
        joints = weights = None
        if original.is_skinned:
            joints, weights = _weights(obj, original, native, slot)
        edits[original.index] = native.MeshEdit(positions, uvs, normals, tangents, joints, weights)
    boxes = {}
    o3d = modules["skeleton"].find_o3d(source_path)
    if o3d:
        boxes = native.bounds_matrices(parsed, modules["skeleton"].parse_object3d(o3d.read_bytes()))
    output = native.patch_dcm(template, edits, boxes=boxes)
    return native.write_dcm_copy(Path(target), output, source=source_path)


def _vertex_ids(obj, native):
    """Índice en la plantilla de cada vértice de la escena (sobrevive al borrado)."""
    ids = obj.data.attributes.get(PREFIX + "vertex_id")
    if ids is None or ids.domain != "POINT" or ids.data_type != "INT":
        raise native.DcmExportError(f"{obj.name}: faltan los identificadores de vértice; vuelve a importar el modelo")
    values = np.zeros(len(obj.data.vertices), dtype=np.int32)
    ids.data.foreach_get("value", values)
    return values.astype(np.int64)
