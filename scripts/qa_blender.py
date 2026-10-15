"""QA real del addon: ejecutar con blender --background --factory-startup
--python-exit-code 1 --python scripts/qa_darksiders_blender.py --
--assets-root <Darksiders/media> --output-dir <directorio nuevo> [--addon-parent DIR].

Lee originales; escribe copias, una escena y una vista frontal en output-dir.
--addon-parent permite probar el ZIP extraído, sin cargar los parsers del repo.
"""
import argparse
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--addon-parent", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.addon_parent))
    import io_darksiders as addon

    addon.register()
    from io_darksiders import operators, roundtrip

    modules = operators.modules()
    parser_root = Path(modules["mesh"].__file__).resolve().parent
    results = {"blender": bpy.app.version_string, "parser_root": str(parser_root), "cases": []}
    cases = ["items/armor/base_glovesa.2", "items/armor/abyssala01_gloves.2",
             "characters/belial_dlc90/belial.2"]

    def clear():
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)

    for relative in cases:
        source = args.assets_root / relative
        for forward in (False, True):
            clear()
            assert bpy.ops.import_scene.darksiders_dcm(filepath=str(source), face_forward=forward) == {"FINISHED"}
            meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
            bpy.context.view_layer.update()
            target = args.output_dir / f"{source.stem}_{forward}.2"
            assert bpy.ops.export_scene.darksiders_dcm(filepath=str(target)) == {"FINISHED"}
            assert target.read_bytes() == source.read_bytes(), f"no-op distinto: {relative}, {forward}"
            results["cases"].append({"file": relative, "forward": forward, "byte_identical": True,
                                     "vertices": sum(len(obj.data.vertices) for obj in meshes),
                                     "triangles": sum(len(obj.data.polygons) for obj in meshes)})

    # Edición real de un vértice, UV y pesos; releer .2 y comprobar el giro inverso.
    clear()
    source = args.assets_root / cases[0]
    assert bpy.ops.import_scene.darksiders_dcm(filepath=str(source)) == {"FINISHED"}
    obj = bpy.context.view_layer.objects.active
    original = modules["mesh"].load_dcm(source).meshes[obj["anansi_mesh_index"]]
    obj.data.vertices[0].co.x += 0.125
    for loop in obj.data.loops:
        if loop.vertex_index == 0:
            obj.data.uv_layers.active.data[loop.index].uv.x += 0.001
    for group in obj.vertex_groups:
        group.remove([0])
    obj.vertex_groups[0].add([0], 1.0, "REPLACE")
    obj.data.update()
    target = args.output_dir / "base_glovesa_edited.2"
    roundtrip.export_asset(bpy.context, target, modules)
    edited = modules["mesh"].load_dcm(target).meshes[original.index]
    assert abs(edited.positions[0, 0] - (original.positions[0, 0] - 0.125)) < 1e-6
    assert abs(edited.uvs[0, 0] - (original.uvs[0, 0] + 0.001)) < 1e-6
    assert np.count_nonzero(edited.weights[0]) == 1
    np.testing.assert_allclose(edited.weights.sum(1), 1, atol=1e-5)
    results["edited"] = {"positions": True, "uvs": True, "weights": True}

    def must_reject(label, destination):
        try:
            roundtrip.export_asset(bpy.context, destination, modules)
        except (modules["native"].DcmExportError, ValueError):
            results.setdefault("rejected", []).append(label)
        else:
            raise AssertionError(f"debía rechazar: {label}")

    must_reject("overwrite source", source)
    obj.data.uv_layers.active.data[0].uv.x += 0.2
    must_reject("new UV seam", args.output_dir / "invalid_uv.2")
    obj.data.uv_layers.active.data[0].uv.x -= 0.2
    obj.data.attributes["anansi_vertex_id"].data[0].value = 99
    must_reject("reordered vertices", args.output_dir / "invalid_topology.2")
    assert not (args.output_dir / "invalid_uv.2").exists()
    assert not (args.output_dir / "invalid_topology.2").exists()

    # Borrado con el operador real de Blender, que es quien decide qué pasa con
    # anansi_vertex_id y con el orden; se combina con una edición de posición.
    results["deleted"] = []
    for relative in (cases[0], cases[2]):
        clear()
        source = args.assets_root / relative
        assert bpy.ops.import_scene.darksiders_dcm(filepath=str(source)) == {"FINISHED"}
        obj = bpy.context.view_layer.objects.active
        original = modules["mesh"].load_dcm(source).meshes[obj["anansi_mesh_index"]]
        _edit(obj, _patch(obj, len(obj.data.vertices) // 10), lambda: bpy.ops.mesh.delete(type="VERT"))
        # «Solo caras» sobre el abanico de un vértice: ese vértice queda suelto.
        _edit(obj, _fan(obj, obj.data.polygons[len(obj.data.polygons) // 3].vertices[0]),
              lambda: bpy.ops.mesh.delete(type="ONLY_FACE"), faces=True)
        obj.data.vertices[0].co.z += 0.25
        obj.data.update()
        target = args.output_dir / f"{source.stem}_deleted.2"
        roundtrip.export_asset(bpy.context, target, modules)
        edited = modules["mesh"].load_dcm(target).meshes[original.index]
        assert edited.triangle_count == len(obj.data.polygons) < original.triangle_count
        assert edited.vertex_count < len(obj.data.vertices) <= original.vertex_count  # el suelto no viaja
        ids = np.zeros(len(obj.data.vertices), dtype=np.int32)
        obj.data.attributes["anansi_vertex_id"].data.foreach_get("value", ids)
        assert np.all(np.diff(ids) > 0), "Blender no conservó el orden de los supervivientes"
        used = sorted({ids[v] for p in obj.data.polygons for v in p.vertices})
        np.testing.assert_array_equal(edited.uvs, original.uvs[used])
        np.testing.assert_array_equal(edited.weights, original.weights[used])
        assert edited.health().ok
        clear()
        assert bpy.ops.import_scene.darksiders_dcm(filepath=str(target)) == {"FINISHED"}
        results["deleted"].append({"file": relative, "vertices": [original.vertex_count, edited.vertex_count],
                                   "triangles": [original.triangle_count, edited.triangle_count],
                                   "bytes": [source.stat().st_size, target.stat().st_size],
                                   "reimported": True})
    source = args.assets_root / cases[0]
    for label, operation in [("merged vertices", lambda: bpy.ops.mesh.merge(type="CENTER")),
                             ("subdivided edge", lambda: bpy.ops.mesh.subdivide())]:
        clear()
        assert bpy.ops.import_scene.darksiders_dcm(filepath=str(source)) == {"FINISHED"}
        obj = bpy.context.view_layer.objects.active
        _edit(obj, [list(obj.data.polygons[0].vertices)], operation, faces=True)
        must_reject(label, args.output_dir / "invalid_topology.2")
    assert not (args.output_dir / "invalid_topology.2").exists()

    # UV dobladas de origen: en makermale_elder.2 hay vértices cuyas caras tienen
    # la UV en sentidos opuestos, y el .2 guarda un solo signo de bitangente.
    # Antes rechazaban cualquier edición del modelo; ahora conservan el signo.
    clear()
    folded = args.assets_root / "characters/maker_elder/makermale_elder.2"
    assert bpy.ops.import_scene.darksiders_dcm(filepath=str(folded)) == {"FINISHED"}
    obj = next(o for o in bpy.context.scene.objects if o.get("anansi_mesh_index") == 1)
    bpy.context.view_layer.objects.active = obj
    original = modules["mesh"].load_dcm(folded).meshes[1]
    mesh = obj.data
    mesh.calc_tangents(uvmap=mesh.uv_layers.active.name)
    signs = [set() for _ in mesh.vertices]
    for loop in mesh.loops:
        signs[loop.vertex_index].add(loop.bitangent_sign)
    mesh.free_tangents()
    mixed = [i for i, values in enumerate(signs) if len(values) > 1]
    assert mixed, "la muestra ya no trae UV dobladas"
    # Un vértice sin vecinos doblados: la edición no cambia el pliegue de nadie.
    near_mixed = {v for p in mesh.polygons if set(p.vertices) & set(mixed) for v in p.vertices}
    chosen = next(i for i in range(len(mesh.vertices)) if i not in near_mixed)
    for loop, uv in zip(mesh.loops, mesh.uv_layers.active.data):
        if loop.vertex_index == chosen:
            uv.uv.x += 0.001
    target = args.output_dir / "makermale_elder_uv.2"
    roundtrip.export_asset(bpy.context, target, modules)
    edited = modules["mesh"].load_dcm(target).meshes[1]
    assert np.flatnonzero(np.any(edited.uvs != original.uvs, axis=1)).tolist() == [chosen]
    np.testing.assert_array_equal(edited.positions, original.positions)
    assert edited.submeshes == original.submeshes
    before, after = _handedness(folded, original), _handedness(target, edited)
    np.testing.assert_array_equal(after[mixed], before[mixed])
    # El signo es el del juego salvo donde la edición lo invierte: lejos de ella no cambia.
    assert set(np.flatnonzero(after != before).tolist()) <= _around(original, [chosen])
    clear()
    assert bpy.ops.import_scene.darksiders_dcm(filepath=str(target)) == {"FINISHED"}
    results["folded_uv"] = {"file": "characters/maker_elder/makermale_elder.2", "folded_vertices": len(mixed),
                            "edited_vertex": chosen, "signs_kept": True,
                            "sign_changes": int(np.count_nonzero(after != before)), "reimported": True}

    # BBFollowNode fuera de la paleta: las cajas de ese modelo están en
    # Bone_MM_Junk y Bone_MM_Root, que solo trae makermale_elder_base.o3d. Mover
    # vértices tiene que resolverlo igual con esa armadura en la escena o sin ella.
    rig_path = folded.with_name("makermale_elder_base.o3d")
    rig = modules["skeleton"].load_o3d(rig_path)
    world = rig.world_matrices()
    copies = []
    for options in ({}, {"skeleton_path": str(rig_path)}):
        clear()
        assert bpy.ops.import_scene.darksiders_dcm(filepath=str(folded), **options) == {"FINISHED"}
        for obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
            # El vértice más alto, para que salga de la caja de la malla 1, que es justa.
            max(obj.data.vertices, key=lambda v: v.co.z).co.z += 2.0
            obj.data.update()
            bpy.context.view_layer.objects.active = obj
        target = args.output_dir / f"makermale_elder_moved_{len(copies)}.2"
        roundtrip.export_asset(bpy.context, target, modules)
        copies.append(_split_tangents(target.read_bytes(), modules["mesh"].load_dcm(target).meshes))
    # calc_tangents varía en el último bit entre dos exportaciones iguales;
    # todo lo demás tiene que salir idéntico.
    assert copies[0][0] == copies[1][0], "la copia depende de si hay armadura en la escena"
    np.testing.assert_allclose(copies[0][1], copies[1][1], rtol=0, atol=1e-6)
    sources = modules["mesh"].load_dcm(folded).meshes
    for mesh, node in zip(modules["mesh"].load_dcm(target).meshes, ("Bone_MM_Junk", "Bone_MM_Root")):
        before = sources[mesh.index]
        moved = np.flatnonzero(np.any(mesh.positions != before.positions, axis=1))
        assert moved.size == 1
        changed = np.flatnonzero(_handedness(target, mesh) != _handedness(folded, before))
        assert set(changed.tolist()) <= _around(before, moved), "cambió el signo lejos de lo editado"
        points = (np.c_[mesh.positions, np.ones(mesh.vertex_count)] @ np.linalg.inv(world[rig.index_of(node)]))[:, :3]
        box, old = np.asarray(mesh.bbox), np.asarray(before.bbox)
        assert np.all(box[:3] <= old[:3]) and np.all(box[3:] >= old[3:]), "se perdieron los márgenes de la caja"
        assert np.all(points.min(0) >= box[:3] - 1e-3) and np.all(points.max(0) <= box[3:] + 1e-3)
        grown = int(np.count_nonzero(box != old))
        assert grown or mesh.index == 0, "la caja de la malla 1 no creció"
    results["box_outside_palette"] = {"file": "characters/maker_elder/makermale_elder.2",
                                      "nodes": ["Bone_MM_Junk", "Bone_MM_Root"], "rig": rig_path.name,
                                      "same_with_and_without_armature": True, "mesh_1_box_grew": True}

    # Espejar en U la UV de un material entero invierte el signo de esos vértices,
    # y solo de esos: el resto conserva el del juego.
    clear()
    assert bpy.ops.import_scene.darksiders_dcm(filepath=str(folded)) == {"FINISHED"}
    obj = next(o for o in bpy.context.scene.objects if o.get("anansi_mesh_index") == 1)
    bpy.context.view_layer.objects.active = obj
    original = sources[1]
    # Por su tramo en el .2: al reimportar, Blender renombra el material a elder_a.001.
    beard = next(s for s in original.submeshes if s.name == "elder_a")
    uv_data = obj.data.uv_layers.active.data
    for polygon in obj.data.polygons[beard.index_start // 3:(beard.index_start + beard.index_count) // 3]:
        for index in polygon.loop_indices:
            uv_data[index].uv.x = 1.0 - uv_data[index].uv.x
    target = args.output_dir / "makermale_elder_mirrored.2"
    roundtrip.export_asset(bpy.context, target, modules)
    mirrored = modules["mesh"].load_dcm(target).meshes[1]
    rows = np.zeros(original.vertex_count, dtype=bool)
    rows[beard.vertex_start:beard.vertex_start + beard.vertex_count] = True
    before, after = _handedness(folded, original), _handedness(target, mirrored)
    np.testing.assert_array_equal(after[rows], -before[rows])
    np.testing.assert_array_equal(after[~rows], before[~rows])
    np.testing.assert_array_equal(mirrored.positions, original.positions)
    results["mirrored_uv"] = {"material": "elder_a", "flipped": int(rows.sum()), "kept": int((~rows).sum())}

    # La misma rotación en malla y rig. No aplica la pose al exportar.
    clear()
    rig = args.assets_root / "characters/death/death.o3d"
    assert bpy.ops.import_scene.darksiders_dcm(filepath=str(source), skeleton_path=str(rig)) == {"FINISHED"}
    target = args.output_dir / "base_glovesa_rig.2"
    roundtrip.export_asset(bpy.context, target, modules)
    assert target.read_bytes() == source.read_bytes()
    results["external_rig_byte_identical"] = True
    bpy.context.view_layer.update()
    for mesh_obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        evaluated = mesh_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        difference = max((a.co - b.co).length for a, b in zip(mesh_obj.data.vertices, evaluated.data.vertices))
        assert difference < 1e-3, difference
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output_dir / "gloves_with_rig.blend"))
    bpy.ops.wm.open_mainfile(filepath=str(args.output_dir / "gloves_with_rig.blend"))
    target = args.output_dir / "base_glovesa_reopened.2"
    roundtrip.export_asset(bpy.context, target, modules)
    assert target.read_bytes() == source.read_bytes()
    results["reopened_byte_identical"] = True

    # QA visual: mismos guantes, coordenadas nativas a la izquierda y giro a la derecha.
    clear()
    for forward, shift in [(False, -25), (True, 25)]:
        bpy.ops.import_scene.darksiders_dcm(filepath=str(source), face_forward=forward, import_skeleton=False)
        for mesh_obj in [o for o in bpy.context.scene.objects if o.type == "MESH" and abs(o.location.x) < 1e-5]:
            mesh_obj.location.x = shift
    scene = bpy.context.scene
    camera_data = bpy.data.cameras.new("Front")
    camera = bpy.data.objects.new("Front", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0, -150, 34)
    camera.rotation_euler = (Vector((0, 0, 34)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 94
    scene.camera = camera
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.62, 0.68, 0.76)
    scene.display.shading.show_cavity = True
    scene.render.resolution_x, scene.render.resolution_y = 1200, 480
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(args.output_dir / "front_native_left_blender_right.png")
    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output_dir / "orientation_comparison.blend"))
    (args.output_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("DARKSIDERS_QA_OK " + json.dumps(results))


def _handedness(path, mesh):
    """Signo de bitangente guardado (@48 del registro de vértice con piel)."""
    n = mesh.vertex_count
    at = mesh.data_base + 8 + mesh.indices.size * 2 + n * 20
    return np.ndarray((n,), dtype="<f4", buffer=path.read_bytes(), offset=at + 48, strides=(52,)).copy()


def _around(mesh, vertices):
    """Los vértices y sus vecinos por cara, en filas del .2."""
    touching = np.any(np.isin(mesh.indices, list(vertices)), axis=1)
    return set(np.unique(mesh.indices[touching]).tolist())


def _split_tangents(data, meshes):
    """Los bytes del .2 con las tangentes xyz a cero, y esas tangentes aparte."""
    data, tangents = bytearray(data), []
    for mesh in meshes:
        if not mesh.is_skinned:
            continue
        n = mesh.vertex_count
        at = mesh.data_base + 8 + mesh.indices.size * 2 + n * 20
        view = np.ndarray((n, 3), dtype="<f4", buffer=data, offset=at + 36, strides=(52, 4))
        tangents.append(view.copy())
        view[:] = 0
    return bytes(data), np.concatenate(tangents)


def _patch(obj, size):
    """Zona conexa de vértices alrededor del primero, para borrar."""
    neighbours = {i: set() for i in range(len(obj.data.vertices))}
    for edge in obj.data.edges:
        a, b = edge.vertices
        neighbours[a].add(b)
        neighbours[b].add(a)
    seed = len(obj.data.vertices) // 2
    region, frontier = {seed}, [seed]
    while frontier and len(region) < size:
        frontier = [w for v in frontier for w in neighbours[v] if w not in region]
        region.update(frontier[:size - len(region)])
    return [sorted(region)]


def _fan(obj, center):
    """El vértice y sus vecinos: en modo cara selecciona justo su abanico."""
    around = {center}
    for polygon in obj.data.polygons:
        if center in polygon.vertices:
            around.update(polygon.vertices)
    return [sorted(around)]


def _edit(obj, groups, operation, *, faces=False):
    """Selecciona en modo edición y ejecuta un operador de malla de Blender."""
    import bmesh

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (not faces, False, faces)
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    chosen = {i for group in groups for i in group}
    for vertex in bm.verts:
        vertex.select = vertex.index in chosen
    bm.select_flush(True)
    bmesh.update_edit_mesh(obj.data)
    assert operation() == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")


if __name__ == "__main__":
    main()
