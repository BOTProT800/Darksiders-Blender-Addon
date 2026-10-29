"""Metadatos persistentes y exportación de una importación .2 de Blender.

Cada vértice recuerda su índice en la plantilla (`anansi_vertex_id`), y Blender
conserva ese atributo al borrar. Así se reconoce un borrado: primero se poda la
plantilla (`native.prune_dcm`) y después se aplican las ediciones sobre la copia
podada, cuyos vértices ya coinciden uno a uno con los de la escena.

Lo que no es un borrado (vértices o caras nuevas, fusionar, subdividir, una
costura UV nueva, otro reparto de materiales, o la malla de otro modelo unida a
esta) reescribe la malla entera desde Blender (`native.rebuild_dcm`), si tiene
piel: conserva su paleta y sus materiales y avisa de lo que no cabe en ellos.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from .formats.skin import normalise_weights
from .plan import orient_vector

# Prefijo de las propiedades que se guardan en el .blend. Es el de las
# versiones 1.x y se conserva para que las escenas importadas con ellas sigan
# pudiendo exportar.
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


def _shading(obj, original, source, everywhere, slot, positions, uvs, *, moved):
    """Calcula normales y tangentes en una copia temporal, sin editar la escena.

    ``everywhere`` trae la posición nativa de cada vértice de la escena y
    ``slot`` su fila en la plantilla (-1 si no viaja); ``positions`` y ``uvs``
    son la edición ya en filas de la plantilla, igual que el resultado.
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
        rows, corner, tangent, sign = [], [], [], []
        for loop, normal in zip(mesh.loops, mesh.corner_normals):
            rows.append(slot[loop.vertex_index])
            corner.append(tuple(normal.vector))
            tangent.append(tuple(loop.tangent))
            sign.append(loop.bitangent_sign)
        at = original.data_base + 8 + original.indices.size * 2 + n * 20
        old_tangents = np.ndarray((n, 4), dtype="<f4", buffer=source, offset=at + 36, strides=(52, 4))
        return _tangent_frames(rows, corner, tangent, sign, original.normals, old_tangents, original.indices,
                               (original.positions, original.uvs), (positions, uvs))
    finally:
        bpy.data.meshes.remove(mesh)


def _tangent_frames(rows, corner_normals, loop_tangents, loop_signs, old_normals, old_tangents,
                    faces, before, after):
    """Una normal y una tangente con signo por fila de la plantilla, desde los loops.

    La normal y la tangente salen de Blender. El signo no: el de `calc_tangents`
    (orientación de la UV en cada cara) discrepa del que guarda el juego en el
    1,2 % de los vértices, y el de Lengyel (`_lengyel_signs`) lo reproduce en el
    99,86 %. Se parte del signo guardado y solo se invierte donde la edición
    invierte el de Lengyel: ``before`` y ``after`` son (posiciones, UV) de la
    plantilla y de la edición, y ``faces`` sus triángulos en filas. Donde la
    edición no toca nada, el signo del juego queda tal cual.

    Si las caras de un vértice tienen la UV en sentidos opuestos (triángulos
    doblados, que los assets traen de origen: 79 vértices en
    `makermale_elder.2`), Blender da una tangente por lado. Se promedian solo
    las del lado del signo elegido, porque las del otro pueden apuntar al revés
    y anularla. Las filas sin loops conservan normal y tangente.
    """
    n = len(old_normals)
    rows = np.asarray(rows, dtype=np.int64).reshape(-1)
    corner_normals = np.asarray(corner_normals, dtype=np.float64).reshape(-1, 3)
    loop_tangents = np.asarray(loop_tangents, dtype=np.float64).reshape(-1, 3)
    loop_signs = np.asarray(loop_signs, dtype=np.float64).reshape(-1)
    old = np.asarray(old_tangents, dtype=np.float64)

    normals = np.zeros((n, 3))
    np.add.at(normals, rows, corner_normals)
    tangents = np.zeros((n, 3))
    np.add.at(tangents, rows, loop_tangents)
    looped = np.zeros(n, dtype=bool)
    looped[rows] = True
    normals[~looped] = np.asarray(old_normals, dtype=np.float64)[~looped]
    tangents[~looped] = old[~looped, :3]
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)

    handedness = (np.sign(old[:, 3]) * _lengyel_signs(*after, faces, normals)
                  * _lengyel_signs(*before, faces, normals))
    by_sign, present = {}, {}
    for value in (-1.0, 1.0):
        chosen = loop_signs == value
        by_sign[value] = np.zeros((n, 3))
        np.add.at(by_sign[value], rows[chosen], loop_tangents[chosen])
        present[value] = np.zeros(n, dtype=bool)
        present[value][rows[chosen]] = True
    both = present[1.0] & present[-1.0]
    tangents[both] = np.where((handedness[both] > 0)[:, None], by_sign[1.0][both], by_sign[-1.0][both])

    tangents -= normals * np.sum(normals * tangents, axis=1, keepdims=True)
    tangents /= np.maximum(np.linalg.norm(tangents, axis=1, keepdims=True), 1e-30)
    return normals, np.c_[tangents, handedness]


def _lengyel_signs(positions, uvs, faces, normals):
    """Signo de bitangente por vértice, como lo calcula la herramienta del juego.

    Método de Lengyel: la tangente y la bitangente de cada cara, sin normalizar,
    se suman en sus vértices, y el signo es el de `dot(N × T, B)`. Reproduce
    2.280.043 de los 2.283.234 signos de las mallas con piel del corpus, pliegues
    de UV incluidos. ``uvs`` va en la convención del juego (V negada), como en
    el `.2`; aquí se pasa a la de Blender, que es en la que cuadra.
    """
    points = np.asarray(positions, dtype=np.float64)
    uv = np.asarray(uvs, dtype=np.float64) * (1.0, -1.0)
    faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    d1, d2 = uv[faces[:, 1]] - uv[faces[:, 0]], uv[faces[:, 2]] - uv[faces[:, 0]]
    e1, e2 = points[faces[:, 1]] - points[faces[:, 0]], points[faces[:, 2]] - points[faces[:, 0]]
    area = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    scale = np.divide(1.0, area, out=np.zeros_like(area), where=area != 0)[:, None]
    face_t = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * scale
    face_b = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * scale
    t = np.zeros((len(points), 3))
    b = np.zeros_like(t)
    for corner in range(3):
        np.add.at(t, faces[:, corner], face_t)
        np.add.at(b, faces[:, corner], face_b)
    return np.where(np.sum(np.cross(normals, t) * b, axis=1) < 0, -1.0, 1.0)


def export_asset(context, target, modules, notices=None):
    """Escribe la copia `.2` del modelo de la malla activa y devuelve su ruta.

    ``notices`` (una lista) recibe ``(nivel, mensaje)`` de lo que conviene saber
    aunque la exportación salga: mallas reconstruidas, pesos atados a la paleta,
    caras pasadas a un material de la plantilla.
    """
    native = modules["native"]
    notices = [] if notices is None else notices
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
    # vértice i de la escena, o -1 si se quedó sin triángulos y no viaja. Lo
    # que no es un borrado se reconstruye entero (`rebuilt`).
    subsets, slots, rebuilt = {}, {}, {}
    for obj in objects:
        if obj.get(PREFIX + "sha256") != digest or obj.get(PREFIX + "source") != str(source_path):
            raise native.DcmExportError("las mallas no comparten la misma plantilla")
        original = parsed.meshes[obj[PREFIX + "mesh_index"]]
        ids = _vertex_ids(obj, native)
        subset, reason = _classify(obj, original, ids, native, modules["plan"])
        if reason is not None:
            if not original.is_skinned:
                raise native.DcmExportError(f"{obj.name}: {reason}; las mallas estáticas solo admiten "
                                            "cambios sin tocar la topología")
            rebuilt[original.index] = (obj, reason)
            continue
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
    news = {}
    for index, (obj, reason) in rebuilt.items():
        _check_object(obj, native)
        original = parsed.meshes[index]
        try:
            news[index] = _new_mesh(obj, original, native, notices)
        except native.DcmExportError as exc:
            raise native.DcmExportError(f"{obj.name}: {exc}") from exc
        notices.extend(_size_notices(obj.name, reason, original, news[index]))
    edits = {}
    for obj in objects:
        original = parsed.meshes[obj[PREFIX + "mesh_index"]]
        if original.index in rebuilt:
            continue
        mesh = obj.data
        slot = slots[original.index]
        rows = np.flatnonzero(slot >= 0)
        _check_object(obj, native)
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
            normals, tangents = _shading(obj, original, template, everywhere, slot, positions, uvs, moved=moved)
        joints = weights = None
        if original.is_skinned:
            joints, weights = _weights(obj, original, native, slot)
        edits[original.index] = native.MeshEdit(positions, uvs, normals, tangents, joints, weights)
    boxes = {}
    o3d = modules["skeleton"].find_o3d(source_path)
    if o3d:
        descriptor = modules["skeleton"].parse_object3d(o3d.read_bytes())
        boxes = native.bounds_matrices(parsed, descriptor)
        # Solo una malla movida o nueva necesita su caja, y solo si está en otro espacio.
        moved = [index for index, edit in edits.items()
                 if not np.array_equal(edit.positions, parsed.meshes[index].positions)]
        pending = [index for index in [*moved, *news] if index not in boxes and parsed.meshes[index].warnings]
        if pending:
            rigs = _box_rigs(objects, source_path, parsed, pending, modules)
            boxes.update(native.rig_bounds_matrices(parsed, descriptor, rigs, pending))
    if news:
        template = native.rebuild_dcm(template, news, boxes=boxes)
    output = native.patch_dcm(template, edits, boxes=boxes)
    return native.write_dcm_copy(Path(target), output, source=source_path)


def _classify(obj, original, ids, native, plan):
    """``(subconjunto, None)`` si la malla es la plantilla con borrados; ``(None, motivo)`` si no.

    El subconjunto es ``None`` cuando no falta nada. Además del borrado, el
    camino de la plantilla exige los materiales de cada cara superviviente y
    una UV por vértice: una costura nueva necesita partir vértices.
    """
    try:
        subset = native.deletion_subset(original, ids, [p.vertices for p in obj.data.polygons])
    except native.DcmExportError as exc:
        return None, str(exc).split(":")[0]
    kept = np.arange(original.triangle_count) if subset is None else subset.triangles
    expected = np.asarray(plan.build_mesh_plan(original, original.index, "original").face_materials,
                          dtype=np.int64)[kept]
    if [p.material_index for p in obj.data.polygons] != expected.tolist():
        return None, "asignación de materiales modificada"
    layer = obj.data.uv_layers.active
    if layer is not None:
        seen = {}
        for loop, uv in zip(obj.data.loops, layer.data):
            point = (float(uv.uv[0]), float(uv.uv[1]))
            if seen.setdefault(loop.vertex_index, point) != point:
                return None, "costura UV nueva"
    return subset, None


def _check_object(obj, native):
    """Transformación, modificadores y armadura que la exportación sabe deshacer."""
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


def _new_mesh(obj, original, native, notices):
    """La malla de la escena entera como `native.NewMesh`, en espacio nativo."""
    import bpy

    mesh = obj.data
    if not len(mesh.polygons):
        raise native.DcmExportError("la malla no tiene caras; vaciar una malla no está soportado")
    if any(len(polygon.vertices) != 3 for polygon in mesh.polygons):
        raise native.DcmExportError("hay caras que no son triángulos: triangula la malla (Ctrl+T) antes de exportar")
    if mesh.uv_layers.active is None:
        raise native.DcmExportError("falta el mapa UV")
    face_spans = _face_spans(obj, original, native, notices)
    joints, weights = _vertex_weights(obj, original, native, notices)
    forward = bool(obj[PREFIX + "face_forward"])
    everywhere = np.asarray([orient_vector(obj.matrix_world @ v.co, forward) for v in mesh.vertices],
                            dtype=np.float64).reshape(-1, 3)
    copy = mesh.copy()
    try:
        # Espacio nativo; las normales personalizadas (las del juego, si la malla
        # viene de otra importación) se conservan: se exporta lo que se ve.
        for vertex, point in zip(copy.vertices, everywhere):
            vertex.co = point
        copy.update()
        copy.calc_tangents(uvmap=copy.uv_layers.active.name)
        uv_data = copy.uv_layers.active.data
        loops = [index for polygon in copy.polygons for index in polygon.loop_indices]
        loop_vertex = [copy.loops[index].vertex_index for index in loops]
        loop_uv = [(float(uv_data[index].uv[0]), -float(uv_data[index].uv[1])) for index in loops]
        loop_normal = [tuple(copy.corner_normals[index].vector) for index in loops]
        loop_tangent = [tuple(copy.loops[index].tangent) for index in loops]
        loop_sign = [copy.loops[index].bitangent_sign for index in loops]
    finally:
        bpy.data.meshes.remove(copy)
    return assemble_new_mesh(native, everywhere, loop_vertex, face_spans, loop_uv, loop_normal,
                             loop_tangent, loop_sign, joints, weights)


def assemble_new_mesh(native, positions, loop_vertex, face_spans, loop_uv, loop_normal,
                      loop_tangent, loop_sign, joints, weights):
    """Loops de Blender (tres por cara, en orden) -> `NewMesh` con vértices partidos.

    El `.2` guarda una UV, una normal y un material por vértice, así que cada
    combinación distinta de (vértice, tramo, UV, normal) es un vértice del
    archivo, como hace el juego en sus costuras. El signo de la bitangente es el
    de Lengyel, el método con el que se reproducen el 99,86 % de los del juego;
    la tangente se promedia con los loops de ese mismo signo, como en
    `_tangent_frames`, para que una UV doblada no la anule.
    """
    positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    loop_vertex = np.asarray(loop_vertex, dtype=np.int64).reshape(-1)
    loop_uv = np.asarray(loop_uv, dtype=np.float64).reshape(-1, 2)
    loop_normal = np.asarray(loop_normal, dtype=np.float64).reshape(-1, 3)
    loop_tangent = np.asarray(loop_tangent, dtype=np.float64).reshape(-1, 3)
    loop_sign = np.asarray(loop_sign, dtype=np.float64).reshape(-1)
    face_spans = np.asarray(face_spans, dtype=np.int64).reshape(-1)
    loop_span = np.repeat(face_spans, 3)
    if len(loop_vertex) != 3 * len(face_spans):
        raise native.DcmExportError("hay caras que no son triángulos")
    unit = loop_normal / np.maximum(np.linalg.norm(loop_normal, axis=1, keepdims=True), 1e-30)
    keys = np.c_[loop_vertex, loop_span, np.round(loop_uv * 1e6), np.round(unit * 1e4)].astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    count = len(first)
    vertex = loop_vertex[first]
    uvs = loop_uv[first]
    normals = np.zeros((count, 3))
    np.add.at(normals, inverse, unit)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
    triangles = inverse.reshape(-1, 3)
    points = positions[vertex]
    signs = _lengyel_signs(points, uvs, triangles, normals)
    tangents = np.zeros((count, 3))
    chosen = loop_sign == signs[inverse]
    np.add.at(tangents, inverse[chosen], loop_tangent[chosen])
    lonely = np.linalg.norm(tangents, axis=1) < 1e-12
    if lonely.any():  # ningún loop con ese signo: vale cualquiera de los suyos
        np.add.at(tangents, inverse[lonely[inverse]], loop_tangent[lonely[inverse]])
    tangents -= normals * np.sum(normals * tangents, axis=1, keepdims=True)
    flat = np.linalg.norm(tangents, axis=1) < 1e-9
    if flat.any():  # sin dirección útil (UV degenerada): una perpendicular cualquiera
        helper = np.where(np.abs(normals[flat, :1]) < 0.9, [[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]])
        tangents[flat] = np.cross(normals[flat], helper)
    tangents /= np.linalg.norm(tangents, axis=1, keepdims=True)
    return native.NewMesh(positions=points.astype(np.float32), uvs=uvs.astype(np.float32),
                          normals=normals, tangents=np.c_[tangents, signs],
                          joints=np.asarray(joints)[vertex], weights=np.asarray(weights)[vertex],
                          triangles=triangles, spans=face_spans)


def _face_spans(obj, original, native, notices):
    """El tramo de la plantilla de cada cara, por el material de su ranura.

    Las ranuras de la importación van primero y en el orden de los tramos; lo
    que Blender añade al unir otra malla va detrás. Se comparan por nombre sin
    el sufijo `.001` que pone Blender si el nombre ya existía. Si la plantilla
    tiene un solo tramo, las caras con un material ajeno van a él (se avisa):
    no hay otro sitio donde pueda ir. Con varios, se pide asignarlas.
    """
    spans = {}
    for k, submesh in enumerate(original.submeshes):
        spans.setdefault(submesh.name or f"material_{submesh.material_index}", []).append(k)
    labels = list(spans)
    by_slot = {}
    for index, slot in enumerate(obj.material_slots):
        name = re.sub(r"\.\d{3}$", "", slot.material.name) if slot.material else None
        if index < len(labels) and name == labels[index]:
            by_slot[index] = labels[index]
    result, foreign = [], {}
    for polygon in obj.data.polygons:
        label = by_slot.get(polygon.material_index)
        if label is None:
            slot = obj.material_slots[polygon.material_index] if polygon.material_index < len(obj.material_slots) else None
            name = slot.material.name if slot and slot.material else "(sin material)"
            foreign[name] = foreign.get(name, 0) + 1
            result.append(-1)
            continue
        if len(spans[label]) > 1:
            raise native.DcmExportError(f"el material «{label}» tiene {len(spans[label])} tramos en la "
                                        "plantilla; no se sabe a cuál va cada cara nueva")
        result.append(spans[label][0])
    if foreign:
        listed = ", ".join(f"{name} ({count})" for name, count in sorted(foreign.items()))
        if len(original.submeshes) != 1:
            raise native.DcmExportError(f"caras con materiales que no son de la plantilla: {listed}; "
                                        f"asígnales uno de: {', '.join(labels)}")
        notices.append(("WARNING", f"{obj.name}: las caras con {listed} usan el material de la plantilla, "
                                   f"«{labels[0]}», y su textura"))
        result = [0 if span < 0 else span for span in result]
    return result


def _vertex_weights(obj, original, native, notices):
    """``(huesos, pesos)`` por vértice de Blender, en índices de la paleta de la plantilla.

    Un grupo vale si es el que la importación creó para un hueso de la paleta o
    si se llama como uno (al unir otra malla, Blender junta los grupos de igual
    nombre). La paleta no se amplía: cambiaría la cabecera, y el cargador del
    juego solo encaja una cabecera del tamaño original. Con un solo hueso en la
    paleta, toda la malla va rígida a él y se avisa de lo descartado.
    """
    palette = [bone.name for bone in original.bones]
    imported = json.loads(obj[PREFIX + "groups"])
    owner = {group_name: bone for bone, group_name in imported.items() if bone in palette}
    by_group, foreign = {}, set()
    for group in obj.vertex_groups:
        bone = owner.get(group.name, group.name if group.name in palette else None)
        if bone is None:
            foreign.add(group.index)
        else:
            by_group[group.index] = palette.index(bone)
    n = len(obj.data.vertices)
    dense = np.zeros((n, max(len(palette), 1)), dtype=np.float64)
    dropped = set()
    for vertex in obj.data.vertices:
        for entry in vertex.groups:
            if entry.weight <= 0:
                continue
            if entry.group in foreign:
                dropped.add(obj.vertex_groups[entry.group].name)
            else:
                dense[vertex.index, by_group[entry.group]] += entry.weight
    joints = np.zeros((n, 4), dtype=np.uint8)
    weights = np.zeros((n, 4), dtype=np.float32)
    if len(palette) == 1:
        weights[:, 0] = 1.0
        if dropped or not np.all(dense[:, 0] > 0):
            lost = f"; no caben {', '.join(sorted(dropped))}" if dropped else ""
            notices.append(("WARNING", f"{obj.name}: toda la malla va rígida a {palette[0]}, el único hueso "
                                       f"de su paleta{lost}"))
        return joints, weights
    if dropped:
        raise native.DcmExportError(f"pesos en huesos que no están en la paleta de esta malla: "
                                    f"{', '.join(sorted(dropped))}; pásalos a alguno de: {', '.join(palette)}")
    counts = (dense > 0).sum(1)
    if np.any(counts == 0):
        raise native.DcmExportError(f"{int(np.count_nonzero(counts == 0))} vértices sin peso en ningún hueso")
    if np.any(counts > 4) or np.any(np.flatnonzero(dense.any(0)) > 255):
        raise native.DcmExportError("cada vértice admite como mucho 4 huesos de los 256 primeros de la paleta")
    for row in range(n):
        used = np.flatnonzero(dense[row] > 0)
        used = used[np.argsort(-dense[row, used], kind="stable")]
        joints[row, :len(used)] = used
        weights[row, :len(used)] = dense[row, used] / dense[row, used].sum()
    return joints, weights


def _size_notices(name, reason, original, new):
    """Tamaño de la malla nueva contra el original, tramo a tramo.

    El cargador del juego (Darksiders2DLL `override_unchecked`, 0.9.0) encaja la
    malla nueva en las lecturas del original y rechaza un tramo con más
    vértices o triángulos que el suyo. Es su contrato a 2026-10-17, no del
    formato: el `.2` sale válido igual.
    """
    triangles = np.asarray(new.triangles)
    out = [("INFO", f"{name}: malla nueva ({reason}): {len(new.positions)} vértices y {len(triangles)} "
                    f"triángulos; el original tenía {original.vertex_count} y {original.triangle_count}")]
    for k, submesh in enumerate(original.submeshes):
        mine = np.asarray(new.spans) == k
        vertices = len(np.unique(triangles[mine]))
        faces = int(mine.sum())
        if vertices > submesh.vertex_count or faces > submesh.index_count // 3:
            out.append(("WARNING", f"{name}: el material «{submesh.name}» queda con {vertices} vértices y "
                                   f"{faces} triángulos, más que los {submesh.vertex_count} y "
                                   f"{submesh.index_count // 3} del original; Darksiders2DLL 0.9.0 solo "
                                   "carga mallas que quepan en el original"))
    return out


def _box_rigs(objects, source_path, parsed, pending, modules):
    """Esqueletos donde buscar un BBFollowNode que la paleta no trae, del más fiable al menos.

    Primero el que se eligió al importar, luego los `.o3d` de la carpeta del `.2`
    y, si con eso no basta, el que más huesos de la paleta cubre (`find_rig`).
    Es un generador: `native.rig_bounds_matrices` deja de pedir en cuanto
    resuelve, y así `find_rig`, que recorre carpetas, casi nunca llega a correr.
    """
    from .animation_import import METADATA

    skeleton = modules["skeleton"]

    def paths():
        for obj in objects:
            for modifier in obj.modifiers:
                rig = modifier.object if modifier.type == "ARMATURE" else None
                if rig is not None and METADATA in rig:
                    source = json.loads(rig[METADATA]).get("source")
                    if source:
                        yield Path(source)
        yield from sorted(path for path in source_path.parent.iterdir() if path.suffix.casefold() == ".o3d")
        names = sorted({bone.name for index in pending for bone in parsed.meshes[index].bones})
        found, _ = skeleton.find_rig(source_path, names)
        if found is not None:
            yield found

    seen = set()
    for path in paths():
        key = path.resolve()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            yield skeleton.load_o3d(path)
        except (ValueError, OSError):
            continue  # un .o3d sin Skeleton (solo visuals) o ilegible no es candidato


def _vertex_ids(obj, native):
    """Índice en la plantilla de cada vértice de la escena (sobrevive al borrado)."""
    ids = obj.data.attributes.get(PREFIX + "vertex_id")
    if ids is None or ids.domain != "POINT" or ids.data_type != "INT":
        raise native.DcmExportError(f"{obj.name}: faltan los identificadores de vértice; vuelve a importar el modelo")
    values = np.zeros(len(obj.data.vertices), dtype=np.int32)
    ids.data.foreach_get("value", values)
    return values.astype(np.int64)
