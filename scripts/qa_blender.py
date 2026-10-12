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
