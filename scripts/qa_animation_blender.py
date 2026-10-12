"""Run with Blender --background --factory-startup --python-exit-code 1 --python
this_script -- --assets-root MEDIA --output-dir NEW_DIR [--addon-parent DIR].
Real Actions, rest/binding rejection, frame/FPS evaluation, reload and renders.
Game assets stay local; output directories are never overwritten.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--addon-parent', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--expect-packaged', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.addon_parent))
    import io_darksiders as addon
    addon.register()
    from io_darksiders import animation_import as ai
    from io_darksiders import operators, roundtrip
    modules = operators.modules()
    parser_root = Path(modules['animation'].__file__).resolve().parent
    if args.expect_packaged:
        # El zip extraído: los parsers tienen que salir de su propia carpeta.
        assert parser_root.is_relative_to(args.addon_parent.resolve()), parser_root
    result = {'blender': bpy.app.version_string, 'parser_root': str(parser_root), 'cases': []}
    cases = [('characters/belial_dlc90/belial.2', '', 'Run_F.anm'),
             ('characters/belial_dlc90/belial.2', '', 'Idle.anm'),
             ('characters/belial_dlc90/belial.2', '', 'Atk_TridentStrike_Strike.anm'),
             ('characters/death/death_head.2', 'characters/death/death.o3d', 'D_Dash_F.anm'),
             ('characters/death/death_head.2', 'characters/death/death.o3d', 'Death_Visemes.anm')]
    for number, (mesh_name, external, clip_name) in enumerate(cases):
        for forward in (False, True):
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete(use_global=False)
            scene = bpy.context.scene
            scene.render.fps = 60 if number == 1 else 24
            scene.render.fps_base = 1.001 if number == 1 else 1.0
            source = args.assets_root / mesh_name
            assert bpy.ops.import_scene.darksiders_dcm(
                filepath=str(source), face_forward=forward,
                skeleton_path=str(args.assets_root / external) if external else '') == {'FINISHED'}
            rig = ai.active_rig(bpy.context)
            metadata, skeleton, names = ai._source(rig, modules)
            path = source.parent / clip_name
            clip = modules['animation'].load_anm(path)
            bound, missing = modules['animation'].bind_tracks(clip, skeleton)
            old_actions = set(bpy.data.actions)
            try:
                ai.import_clip(bpy.context, rig, path, modules)
            except ValueError as exc:
                assert missing and 'pistas no tienen hueso' in str(exc)
                assert set(bpy.data.actions) == old_actions
            else:
                assert not missing
            action, notes = ai.import_clip(bpy.context, rig, path, modules, allow_partial=True)
            assert action.use_fake_user
            assert action['anansi_status'] == ('suspicious' if notes else 'written')
            if number == 0 and forward:
                try:
                    roundtrip.export_asset(bpy.context, args.output_dir / 'posed_should_fail.2', modules)
                except ValueError as exc:
                    assert 'pose de descanso' in str(exc)
                    result['posed_native_export_rejected'] = True
                else:
                    raise AssertionError('animated pose exported as native model')
                assert not (args.output_dir / 'posed_should_fail.2').exists()
            fps = scene.render.fps / scene.render.fps_base
            assert abs((action.frame_end-action.frame_start)/fps - clip.duration) < 1e-5
            position_error = rotation_error = matrix_error = 0.
            for time in sorted(set([0., .25, 1., (clip.frame_count-1)/2, (clip.frame_count-1)/2+.25, float(clip.frame_count-1)])):
                time = min(time, clip.frame_count-1)
                frame = 1 + time * fps / clip.fps
                scene.frame_set(math.floor(frame), subframe=frame % 1)
                expected = modules['animation'].sample_world(skeleton, bound, time, face_forward=forward)
                for index, bone in enumerate(skeleton.bones):
                    actual = rig.pose.bones[names[bone.name]].matrix
                    matrix_error = max(matrix_error, float(np.max(abs(np.array(actual)-expected[index]))))
                    position_error = max(position_error, float(np.linalg.norm(np.array(actual)[:3,3]-expected[index,:3,3])))
                    angle = float(actual.to_quaternion().rotation_difference(Matrix(expected[index]).to_quaternion()).angle)
                    rotation_error = max(rotation_error, min(angle, abs(2*math.pi-angle)))
            record = {'mesh':mesh_name,'clip':clip_name,'forward':forward,'scene_fps':fps,
                      'bound_tracks':len(bound),'unmatched_tracks':len(missing),'position_error':position_error,
                      'rotation_error_radians':rotation_error,'matrix_error':matrix_error}
            result['cases'].append(record)
            print('ANM_CASE '+json.dumps(record),flush=True)
            # Guard large errors; detailed tolerances are recorded in the report.
            assert position_error < .05 and matrix_error < .05, record
            assert min(rotation_error, abs(rotation_error - 2*math.pi)) < .003, record
            before = action
            second, _ = ai.import_clip(bpy.context, rig, path, modules, allow_partial=True)
            assert before != second and before.use_fake_user and before in bpy.data.actions.values()
            # Failure must retain the active action and action inventory.
            snapshot = set(bpy.data.actions)
            bad = args.output_dir / 'truncated.anm'
            bad.write_bytes(path.read_bytes()[:30])
            try:
                ai.import_clip(bpy.context, rig, bad, modules, allow_partial=True)
            except ValueError:
                assert rig.animation_data.action == second and set(bpy.data.actions) == snapshot
            else:
                raise AssertionError('truncated clip accepted')
            if number == 1 and forward:
                bone = next(iter(rig.data.bones))
                original_inheritance = bone.inherit_scale
                bone.inherit_scale = 'NONE'
                try:
                    ai.import_clip(bpy.context, rig, path, modules, allow_partial=True)
                except ValueError as exc:
                    assert 'herencia' in str(exc)
                    assert set(bpy.data.actions) == snapshot and rig.animation_data.action == second
                    result['changed_inheritance_rejected'] = True
                else:
                    raise AssertionError('changed inheritance accepted')
                finally:
                    bone.inherit_scale = original_inheritance
            if forward and number in (0, 2, 3):
                render_sequence(scene, rig, clip, fps, args.output_dir, f'{number}_{path.stem}')
            if forward and number == 0:
                blend = args.output_dir / 'belial_animation.blend'
                rig_name = rig.name
                bpy.ops.wm.save_as_mainfile(filepath=str(blend))
                bpy.ops.wm.open_mainfile(filepath=str(blend))
                rig = bpy.data.objects[rig_name]
                ai.import_clip(bpy.context, rig, path, modules, allow_partial=True)
                result['reopened_import'] = True
                result['previous_actions_preserved'] = True
    (args.output_dir / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('DARKSIDERS_ANIMATION_QA_OK '+json.dumps(result),flush=True)


def render_sequence(scene, rig, clip, fps, folder, label):
    frames = [1 + fraction * clip.duration * fps for fraction in (0, .33, .66, 1)]
    points = []
    for frame in frames:
        scene.frame_set(math.floor(frame), subframe=frame % 1)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj in scene.objects:
            if obj.type == 'MESH':
                ev = obj.evaluated_get(depsgraph)
                points.extend(ev.matrix_world @ Vector(corner) for corner in ev.bound_box)
    low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    center = (low + high) / 2
    span = (high-low).length
    data = bpy.data.cameras.new('QA Camera')
    camera = bpy.data.objects.new('QA Camera', data)
    scene.collection.objects.link(camera)
    camera.location = center + Vector((.6, -1, .25)).normalized() * span * 2
    camera.rotation_euler = (center-camera.location).to_track_quat('-Z','Y').to_euler()
    data.type, data.ortho_scale = 'ORTHO', span*1.1
    scene.camera = camera
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'SINGLE'
    scene.display.shading.single_color = (.62,.68,.76)
    scene.display.shading.show_cavity = True
    scene.render.resolution_x, scene.render.resolution_y = 700, 700
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    for index, frame in enumerate(frames):
        scene.frame_set(math.floor(frame), subframe=frame % 1)
        scene.render.filepath = str(folder / f'{label}_{index}.png')
        bpy.ops.render.render(write_still=True)


if __name__ == '__main__':
    main()
