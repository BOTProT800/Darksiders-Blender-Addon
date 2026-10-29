"""ANM -> editable Actions, with source/rest metadata persisted in the blend."""
import hashlib
import json
import math
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix

# Igual que `roundtrip.PREFIX`: el nombre es el de las versiones 1.x y se
# conserva para que sus rigs sigan valiendo.
METADATA = "anansi_animation_rig"
MAX_KEYS = 3_000_000


def remember_rig(obj, blueprint, names):
    if not blueprint.animation_source:
        return
    source = dict(blueprint.animation_source)
    source["names"] = names
    source["rest"] = {bone.name: [list(row) for row in bone.matrix_local]
                      for bone in obj.data.bones}
    obj[METADATA] = json.dumps(source)


def active_rig(context):
    active = context.view_layer.objects.active
    if active is not None and active.type == "ARMATURE":
        return active
    if active is not None and active.type == "MESH":
        rigs = {m.object for m in active.modifiers if m.type == "ARMATURE" and m.object}
        if len(rigs) == 1:
            return rigs.pop()
    raise ValueError("Activa una armadura importada con este addon o una malla vinculada a ella")


def _source(rig, modules):
    if METADATA not in rig:
        raise ValueError("Este rig no guarda hashes ANM: reimporta el modelo o el esqueleto con este addon")
    source = json.loads(rig[METADATA])
    skeleton = modules["skeleton"].Skeleton(source["name"], tuple(
        modules["skeleton"].Bone(**bone) for bone in source["bones"]))
    if skeleton.validate():
        raise ValueError("La jerarquía original del rig no es válida")
    names = source["names"]
    if set(names) != {bone.name for bone in skeleton.bones}:
        raise ValueError("El mapa de nombres del rig está incompleto")
    if any(names[b.name] not in rig.data.bones for b in skeleton.bones):
        raise ValueError("Faltan huesos originales o se renombraron: reimporta el rig")
    for bone in skeleton.bones:
        actual = rig.data.bones[names[bone.name]]
        parent = actual.parent.name if actual.parent else None
        expected_parent = names[skeleton.bones[bone.parent].name] if bone.parent >= 0 else None
        if parent != expected_parent:
            raise ValueError(f"Cambió el padre de {bone.name}; no se puede aplicar ANM con seguridad")
        if np.max(abs(np.array(actual.matrix_local) - np.array(source["rest"][actual.name]))) > 0.0001:
            raise ValueError(f"Cambió la pose de descanso de {bone.name}; reimporta el rig")
        if actual.inherit_scale != 'FULL' or not actual.use_inherit_rotation or not actual.use_local_location:
            raise ValueError(f"Cambió la herencia de transformaciones de {bone.name}; reimporta el rig")
    if rig.data.pose_position != 'POSE':
        raise ValueError("Activa 'Pose Position' en la armadura para reproducir animaciones")
    if any(b.constraints for b in rig.pose.bones) or rig.constraints:
        raise ValueError("El rig tiene restricciones: usa un rig importado sin restricciones para ANM")
    if rig.animation_data and (rig.animation_data.drivers or any(
            not t.mute for t in rig.animation_data.nla_tracks)):
        raise ValueError("El rig tiene drivers o pistas NLA activas; podrían alterar el clip")
    return source, skeleton, names


def _fcurves(action, rig):
    if bpy.app.version >= (4, 4, 0):
        slot = action.slots.new(id_type="OBJECT", name=rig.name)
        layer = action.layers.new("ANM")
        strip = layer.strips.new(type="KEYFRAME")
        return strip.channelbags.new(slot).fcurves, slot
    return action.fcurves, None


def _curve(collection, path, component, frames, values):
    curve = collection.new(path, index=component)
    curve.keyframe_points.add(len(frames))
    coordinates = np.column_stack((frames, values)).ravel()
    curve.keyframe_points.foreach_set("co", coordinates)
    for point in curve.keyframe_points:
        point.interpolation = "LINEAR"
    curve.update()


def import_clip(context, rig, path, modules, *, allow_partial=False, start_frame=1):
    path = Path(path)
    raw = path.read_bytes()
    clip = modules["animation"].parse_anm(raw, path)
    source, skeleton, names = _source(rig, modules)
    bound, missing = modules["animation"].bind_tracks(clip, skeleton)
    if not bound:
        raise ValueError("Ninguna pista coincide con los hashes/nombres de este esqueleto")
    if missing and not allow_partial:
        raise ValueError(f"{len(missing)}/{len(clip.tracks)} pistas no tienen hueso en este rig. "
                         "Comprueba el .o3d; para una importación parcial activa 'Permitir pistas sin hueso'")
    notes = []
    if missing:
        notes.append(f"Importación parcial: {len(bound)}/{len(clip.tracks)} pistas asociadas; "
                     f"sin hueso: {', '.join(missing)}")
    if clip.flags & 2:
        notes.append("El clip tiene el flag de cabecera 0x2; su función en el motor no está validada")
    scalar_count = sum(t.scalar is not None for t in bound.values())
    if scalar_count:
        notes.append(f"{scalar_count} pistas escalares conservadas como propiedad ANM Scalar; "
                     "su efecto visual en el motor no está validado")
    if any(t.weight != 32767 for t in clip.tracks):
        notes.append("Pesos de mezcla conservados en metadatos; se reproduce el clip aislado sin mezclar capas")
    # Half source frames approximate native SLERP using editable Blender quaternion
    # curves. Every source key and integer source frame is represented exactly.
    times = np.arange(0, clip.frame_count - 1 + 0.25, 0.5)
    if (len(times) * len(bound) + 2 * (len(skeleton.bones) - len(bound))) * 10 > MAX_KEYS:
        raise ValueError("El clip supera el límite de 3 millones de claves por acción")
    fps = context.scene.render.fps / context.scene.render.fps_base
    frames = start_frame + times * fps / clip.fps
    rest_inverse = []
    for bone in skeleton.bones:
        actual = rig.data.bones[names[bone.name]]
        rest = actual.matrix_local.copy()
        if actual.parent:
            rest = actual.parent.matrix_local.inverted() @ rest
        rest_inverse.append(rest.inverted())
    orientation = Matrix.Diagonal((-1., -1., 1., 1.)) if source["face_forward"] else Matrix.Identity(4)
    # Prepare all transforms before touching the user's active action or pose.
    channels = {i: [] for i in range(len(skeleton.bones))}
    for time_index, time in enumerate(times):
        matrices = modules["animation"].sample_local(skeleton, bound, float(time))
        for index, bone in enumerate(skeleton.bones):
            if index not in bound and time_index not in (0, len(times) - 1):
                continue
            local = Matrix(matrices[index])
            if bone.parent < 0:
                local = orientation @ local
            basis = rest_inverse[index] @ local
            position, rotation, scale = basis.decompose()
            if channels[index] and np.dot(channels[index][-1][3:7], rotation) < 0:
                rotation.negate()
            if max(abs((Matrix.LocRotScale(position, rotation, scale) - basis)[r][c])
                   for r in range(4) for c in range(4)) > 0.003:
                raise ValueError(f"{bone.name}: transformación con cizalla no representable en Blender")
            channels[index].append((*position, *rotation, *scale))
    action = bpy.data.actions.new(clip.name or path.stem)
    try:
        curves, slot = _fcurves(action, rig)
        for index, values in channels.items():
            pose = rig.pose.bones[names[skeleton.bones[index].name]]
            values = np.asarray(values)
            own_frames = frames if index in bound else frames[[0, -1]] if len(frames) > 1 else frames
            for prop, first, width in (("location", 0, 3), ("rotation_quaternion", 3, 4), ("scale", 7, 3)):
                for component in range(width):
                    _curve(curves, pose.path_from_id(prop), component, own_frames, values[:, first + component])
            track = bound.get(index)
            if track and track.scalar is not None:
                _curve(curves, pose.path_from_id() + '["ANM Scalar"]', 0,
                       start_frame + track.scalar.frames * fps / clip.fps, track.scalar.values[:, 0])
        action.use_fake_user = True
        action.use_frame_range = True
        action.frame_start = float(frames[0])
        action.frame_end = float(frames[-1])
        action["anansi_source"] = str(path.resolve())
        action["anansi_source_sha256"] = hashlib.sha256(raw).hexdigest()
        action["anansi_source_fps"] = clip.fps
        action["anansi_scene_fps"] = fps
        action["anansi_loop"] = clip.loop
        action["anansi_header_flags"] = clip.flags
        action["anansi_status"] = "suspicious" if notes else "written"
        action["anansi_unmatched"] = json.dumps(missing)
        action["anansi_track_weights"] = json.dumps({t.identifier: t.weight for t in clip.tracks})
        action["anansi_notes"] = json.dumps(notes, ensure_ascii=False)
    except Exception:
        bpy.data.actions.remove(action)
        raise
    rig.animation_data_create()
    previous = rig.animation_data.action
    if previous:
        previous.use_fake_user = True
    for pose in rig.pose.bones:
        pose.rotation_mode = "QUATERNION"
    for index, track in bound.items():
        if track.scalar is not None:
            rig.pose.bones[names[skeleton.bones[index].name]]["ANM Scalar"] = float(track.scalar.values[0, 0])
    rig.animation_data.action = action
    if slot is not None:
        rig.animation_data.action_slot = slot
    context.scene.frame_start = int(start_frame)
    context.scene.frame_end = max(int(start_frame), math.ceil(float(frames[-1])))
    context.scene.frame_set(int(start_frame))
    return action, notes
