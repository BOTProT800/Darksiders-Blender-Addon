"""Contrato de escritura conservadora: identidad, offsets y fallos seguros."""
import struct
import unittest

import numpy as np

from io_darksiders.formats.mesh import parse_dcm
from io_darksiders.formats.native import (
    DcmExportError,
    MeshEdit,
    MeshSubset,
    NewMesh,
    bounds_matrices,
    deletion_subset,
    patch_dcm,
    prune_dcm,
    rebuild_dcm,
    rig_bounds_matrices,
)
from io_darksiders.formats.skeleton import Bone, Skeleton
from tests.test_mesh import build_dcm

# Malla 0 con piel y tres materiales; malla 1 estática sin cola (60 B/vértice,
# como las 450 del corpus); malla 2 con piel, la última del archivo.
GRID = [(0, 0, 0), (1, 0, 0), (1, 1, 0.5), (0, 1, 0.5),          # MatA
        (2, 0, 0), (3, 0, 0), (3, 1, 1), (2, 1, 1),              # MatB
        (4, 0, 0), (5, 0, 0), (4, 1, 2)]                          # MatC
TRIS_0 = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7), (8, 9, 10)]
MATS_0 = [("MatA", 0, 2), ("MatB", 2, 2), ("MatC", 4, 1)]
QUAD = [(-1.0, -2.0, 0.0), (1.0, -2.0, 0.0), (1.0, 3.0, 4.0), (-1.0, 3.0, 4.0)]
TRIS_Q = [(0, 1, 2), (0, 2, 3)]


def _material(slot, name, first, count, tris):
    span = np.asarray(tris[first:first + count]).ravel()
    lo, hi = int(span.min()), int(span.max())
    return (bytes((255,)) + struct.pack("<H", len(name)) + name.encode("latin1")
            + struct.pack("<6i", slot, 0, hi - lo + 1, lo, 3 * count, 3 * first))


def _skinned_block(positions, tris):
    n = len(positions)
    attrs = b"".join(struct.pack("<2f", 0.1 * i, -0.1 * i) + bytes(8) + bytes((i, 2 * i, 3 * i, 255))
                     for i in range(n))
    # influencias, posición, ?, normal, normal.z repetida, tangente
    verts = b"".join(struct.pack("<I3ff3ff4f", 2, *positions[i], 0.0, 0.0, 0.0, 1.0, 1.0,
                                 1.0, 0.0, 0.0, 1.0) for i in range(n))
    weights = b"".join(struct.pack("<4f", 0.5 + 0.01 * i, 0.5 - 0.01 * i, 0, 0) for i in range(n))
    joints = b"".join(bytes((i % 2, (i + 1) % 2, 0, 0)) for i in range(n))
    return (struct.pack("<2I", 6 * len(tris), 2) + np.asarray(tris, "<u2").tobytes()
            + attrs + verts + weights + joints)


def _static_block(positions, tris):
    n = len(positions)
    attrs = b"".join(bytes(28) + struct.pack("<2f", 0.2 * i, -0.2 * i) + bytes(8) + bytes((9, 9, 9, 255))
                     for i in range(n))
    return (struct.pack("<2I", 6 * len(tris), 2) + np.asarray(tris, "<u2").tobytes()
            + np.asarray(positions, "<f4").tobytes() + attrs)


def build_pruneable() -> bytes:
    specs = [(True, GRID, TRIS_0, MATS_0), (False, QUAD, TRIS_Q, [("Prop", 0, 2)]),
             (True, [(x + 5, y, z) for x, y, z in QUAD], TRIS_Q, [("MatD", 0, 2)])]
    counts = [3 * len(tris) for _, _, tris, _ in specs]
    tables = (struct.pack("<i", 3) + struct.pack("<3i", *counts) + struct.pack("<i", 3)
              + struct.pack("<3i", *[len(p) if s else -len(p) for s, p, _, _ in specs])
              + struct.pack("<i", -3) + struct.pack("<if", 0, 0.0) * 3)
    blocks = [_skinned_block(p, t) if s else _static_block(p, t) for s, p, t, _ in specs]

    def headers(offsets):
        out = b""
        for (skinned, positions, tris, mats), offset in zip(specs, offsets):
            arr = np.asarray(positions, dtype=np.float32)
            bones = b"".join(bytes((255,)) + struct.pack("<H", len(name)) + name.encode()
                             + np.eye(4, dtype="<f4").tobytes() + struct.pack("<2H", 0, 0)
                             for name in ("Bone_Root", "Bone_Tip")) if skinned else b""
            head = ((len(positions), 64, offset, len(mats), 2) if skinned
                    else (len(positions), 60, offset, 0, len(mats)))
            out += (bytes((int(skinned),)) + struct.pack("<5i", *head)
                    + struct.pack("<6f", *arr.min(0), *arr.max(0)) + bones
                    + b"".join(_material(k, *mat, tris) for k, mat in enumerate(mats)))
        return out

    data_offset = 8 + len(tables) + len(headers([0, 0, 0]))
    offsets, cursor = [], 8
    for block in blocks:
        offsets.append(cursor)
        cursor += len(block)
    return struct.pack("<2i", data_offset, 3) + tables + headers(offsets) + b"".join(blocks)


class NativeRoundtripTests(unittest.TestCase):
    def test_unchanged_is_byte_identical_for_both_layouts(self):
        for skinned in (False, True):
            raw = build_dcm(skinned=skinned) + b"unknown trailing payload"
            mesh = parse_dcm(raw).meshes[0]
            self.assertEqual(raw, patch_dcm(raw, {0: MeshEdit(mesh.positions, mesh.uvs)}))

    def test_positions_update_only_known_fields_and_bounds(self):
        raw = build_dcm()
        mesh = parse_dcm(raw).meshes[0]
        edit = MeshEdit(positions=mesh.positions + [0, 0, 2],
                        normals=np.tile([0, 0, 1], (4, 1)),
                        tangents=np.tile([1, 0, 0, 1], (4, 1)))
        result = patch_dcm(raw, {0: edit})
        parsed = parse_dcm(result).meshes[0]
        np.testing.assert_array_equal(parsed.positions, edit.positions)
        self.assertEqual(len(raw), len(result))
        np.testing.assert_array_equal(parsed.indices, mesh.indices)
        np.testing.assert_array_equal(parsed.weights, mesh.weights)
        self.assertEqual(parsed.bones, mesh.bones)
        self.assertEqual(parsed.submeshes, mesh.submeshes)
        self.assertGreaterEqual(parsed.bbox[5], 6)
        self.assertEqual(result[mesh.data_base:mesh.data_base + 20], raw[mesh.data_base:mesh.data_base + 20])

    def test_weight_buffers_and_influence_counts_roundtrip(self):
        raw = build_dcm()
        edit = MeshEdit(joints=np.zeros((4, 4)), weights=np.tile([1, 0, 0, 0], (4, 1)))
        result = parse_dcm(patch_dcm(raw, {0: edit})).meshes[0]
        np.testing.assert_array_equal(result.weights, edit.weights)
        np.testing.assert_array_equal(result.joints, edit.joints)
        np.testing.assert_array_equal(result.influences, [1, 1, 1, 1])

    def test_invalid_weights_and_unknown_mesh_rejected(self):
        raw = build_dcm()
        for edit in [MeshEdit(weights=np.zeros((4, 4))),
                     MeshEdit(weights=np.zeros((4, 4)), joints=np.zeros((4, 4))),
                     MeshEdit(weights=np.tile([1, 0, 0, 0], (4, 1)), joints=np.full((4, 4), 256))]:
            with self.assertRaises(DcmExportError):
                patch_dcm(raw, {0: edit})
        with self.assertRaises(DcmExportError):
            patch_dcm(raw, {1: MeshEdit()})

    def test_geometry_edit_requires_updated_shading(self):
        raw = build_dcm()
        mesh = parse_dcm(raw).meshes[0]
        with self.assertRaisesRegex(DcmExportError, "normales y tangentes"):
            patch_dcm(raw, {0: MeshEdit(positions=mesh.positions + 1)})

    def test_static_edit_rejected_instead_of_stale_shading(self):
        raw = build_dcm(skinned=False)
        mesh = parse_dcm(raw).meshes[0]
        with self.assertRaisesRegex(DcmExportError, "estática"):
            patch_dcm(raw, {0: MeshEdit(positions=mesh.positions + 1)})


def _blender_delete(mesh, vertices=(), faces=()):
    """Lo que deja Blender al borrar vértices (con sus caras) o solo caras."""
    gone = set(vertices)
    kept = [k for k, face in enumerate(mesh.indices.tolist()) if not gone & set(face) and k not in faces]
    ids = np.array([v for v in range(mesh.vertex_count) if v not in gone])
    return ids, [tuple(int(np.searchsorted(ids, v)) for v in mesh.indices[k]) for k in kept]


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.raw = build_pruneable()
        self.parsed = parse_dcm(self.raw)

    def prune(self, index, **deleted):
        mesh = self.parsed.meshes[index]
        subset = deletion_subset(mesh, *_blender_delete(mesh, **deleted))
        return subset, prune_dcm(self.raw, {index: subset})

    def test_fixture_follows_the_corpus_layout(self):
        self.assertEqual([m.mesh_type for m in self.parsed.meshes], [1, 0, 1])
        self.assertEqual([(s.vertex_start, s.vertex_count) for s in self.parsed.meshes[0].submeshes],
                         [(0, 4), (4, 4), (8, 3)])

    def test_nothing_deleted_keeps_the_template(self):
        mesh = self.parsed.meshes[0]
        self.assertIsNone(deletion_subset(mesh, np.arange(mesh.vertex_count), mesh.indices))
        self.assertEqual(prune_dcm(self.raw, {}), self.raw)

    def test_deleting_a_vertex_rewrites_counts_ranges_and_offsets(self):
        subset, result = self.prune(0, vertices=[5])
        np.testing.assert_array_equal(subset.vertices, [0, 1, 2, 3, 4, 6, 7, 8, 9, 10])
        np.testing.assert_array_equal(subset.triangles, [0, 1, 3, 4])
        self.assertEqual(len(result), len(self.raw) - 92 - 6)
        after, before = parse_dcm(result).meshes, self.parsed.meshes
        self.assertEqual((after[0].vertex_count, after[0].triangle_count), (10, 4))
        np.testing.assert_array_equal(after[0].positions, before[0].positions[subset.vertices])
        np.testing.assert_array_equal(after[0].colours, before[0].colours[subset.vertices])
        np.testing.assert_array_equal(after[0].weights, before[0].weights[subset.vertices])
        self.assertEqual([(s.name, s.index_start, s.index_count, s.vertex_start, s.vertex_count)
                          for s in after[0].submeshes],
                         [("MatA", 0, 6, 0, 4), ("MatB", 6, 3, 4, 3), ("MatC", 9, 3, 7, 3)])
        self.assertEqual(after[0].bbox, before[0].bbox)
        for k in (1, 2):  # las mallas siguientes se mueven sin cambiar
            np.testing.assert_array_equal(after[k].positions, before[k].positions)
            np.testing.assert_array_equal(after[k].indices, before[k].indices)
            self.assertEqual(after[k].data_base, before[k].data_base - 98)
        self.assertEqual(result[:8], self.raw[:8])  # data_offset y número de mallas

    def test_vertices_left_without_triangles_are_dropped(self):
        subset, result = self.prune(0, faces=[0])  # «Solo caras»: el 1 queda suelto
        np.testing.assert_array_equal(subset.vertices, [0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(parse_dcm(result).meshes[0].submeshes[0].vertex_count, 3)

    def test_last_mesh_can_shrink(self):
        _, result = self.prune(2, vertices=[1])
        after = parse_dcm(result).meshes
        self.assertEqual((after[2].vertex_count, after[2].triangle_count), (3, 1))
        start, end = struct.unpack_from("<I", self.raw)[0], self.parsed.meshes[2].data_base
        self.assertEqual(result[start:end], self.raw[start:end])  # bloques 0 y 1 intactos

    def test_input_is_not_mutated(self):
        mesh = self.parsed.meshes[0]
        subset = deletion_subset(mesh, *_blender_delete(mesh, vertices=[5]))
        subsets = {0: subset}
        prune_dcm(self.raw, subsets)
        self.assertIs(subsets[0], subset)

    def test_pruned_copy_accepts_regular_edits(self):
        _, pruned = self.prune(0, vertices=[5])
        mesh = parse_dcm(pruned).meshes[0]
        n = mesh.vertex_count
        edit = MeshEdit(positions=mesh.positions + [0, 0, 1], uvs=mesh.uvs,
                        normals=np.tile([0, 0, 1], (n, 1)), tangents=np.tile([1, 0, 0, 1], (n, 1)))
        result = parse_dcm(patch_dcm(pruned, {0: edit})).meshes[0]
        np.testing.assert_array_equal(result.positions, edit.positions)

    def test_emptying_a_material_or_a_mesh_is_rejected(self):
        with self.assertRaisesRegex(DcmExportError, "MatB"):
            self.prune(0, vertices=[4])
        with self.assertRaisesRegex(DcmExportError, "sin triángulos"):
            self.prune(2, vertices=[0, 1, 2, 3])

    def test_static_meshes_are_not_pruned(self):
        with self.assertRaisesRegex(DcmExportError, "estáticas"):
            self.prune(1, vertices=[1])

    def test_unverified_header_variant_is_rejected(self):
        raw = bytearray(self.raw)
        pairs = 12 + 4 * 3 + 4 + 4 * 3 + 4
        struct.pack_into("<if", raw, pairs, 11, 0.5)  # pareja de estática en una malla con piel
        mesh = parse_dcm(bytes(raw)).meshes[0]
        with self.assertRaisesRegex(DcmExportError, "no verificada"):
            prune_dcm(bytes(raw), {0: deletion_subset(mesh, *_blender_delete(mesh, vertices=[5]))})

    def test_only_deletions_are_recognised(self):
        mesh = self.parsed.meshes[0]
        ids, faces = _blender_delete(mesh, vertices=[5])
        cases = {
            "reordenados": (ids[::-1], faces),
            "duplicados": (np.r_[ids, ids[-1]], faces),
            "triángulos nuevos": (ids, faces + [(0, 1, 9)]),
            "no son triángulos": (ids, faces[:-1] + [(7, 8, 9, 6)]),
        }
        for label, (vertex_ids, polygons) in cases.items():
            with self.subTest(label), self.assertRaises(DcmExportError):
                deletion_subset(mesh, vertex_ids, polygons)
        with self.assertRaises(DcmExportError):
            prune_dcm(self.raw, {0: MeshSubset(np.array([0, 2, 1]), np.array([0]))})


def _plain_colours(raw: bytes) -> bytes:
    """El fixture con un solo color por malla, como 1.623 de las 1.624 mallas con piel."""
    out = bytearray(raw)
    for mesh in parse_dcm(raw).meshes:
        if mesh.is_skinned:
            at = mesh.data_base + 8 + mesh.indices.size * 2
            for row in range(mesh.vertex_count):
                out[at + 20 * row + 16:at + 20 * row + 20] = b"\xff\xff\xff\xff"
    return bytes(out)


def _new_mesh(positions, triangles, spans, *, bone=1):
    """Una malla nueva válida: normal +Z, tangente +X y todo el peso en ``bone``."""
    n = len(positions)
    weights = np.zeros((n, 4))
    weights[:, 0] = 1.0
    joints = np.zeros((n, 4))
    joints[:, 0] = bone
    return NewMesh(positions=np.asarray(positions, dtype=np.float32),
                   uvs=np.asarray([(0.1 * i, -0.05 * i) for i in range(n)], dtype=np.float32),
                   normals=np.tile([0.0, 0.0, 1.0], (n, 1)), tangents=np.tile([1.0, 0.0, 0.0, -1.0], (n, 1)),
                   joints=joints, weights=weights, triangles=np.asarray(triangles), spans=np.asarray(spans))


# Tres tramos (MatA, MatB, MatC) con sus vértices propios, desordenados a propósito:
# el escritor tiene que agruparlos por tramo.
NEW_POINTS = [(0, 0, 0), (9, 0, 0), (0, 9, 0), (20, 0, 0), (29, 0, 0), (20, 9, 3),
              (40, 0, 0), (49, 0, 0), (40, 9, 0), (49, 9, 6), (0, 9, 9)]
NEW_TRIS = [(3, 4, 5), (0, 1, 2), (6, 7, 8), (7, 9, 8), (0, 2, 10)]
NEW_SPANS = [1, 0, 2, 2, 0]


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.raw = _plain_colours(build_pruneable())
        self.parsed = parse_dcm(self.raw)

    def test_new_geometry_replaces_the_mesh_and_keeps_the_rest(self):
        new = _new_mesh(NEW_POINTS, NEW_TRIS, NEW_SPANS)
        result = rebuild_dcm(self.raw, {0: new})
        after, before = parse_dcm(result).meshes, self.parsed.meshes
        mesh = after[0]
        self.assertEqual((mesh.vertex_count, mesh.triangle_count), (11, 5))
        # Tramos en el orden de la plantilla, contiguos en índices y en vértices.
        self.assertEqual([(s.name, s.index_start, s.index_count, s.vertex_start, s.vertex_count)
                          for s in mesh.submeshes],
                         [("MatA", 0, 6, 0, 4), ("MatB", 6, 3, 4, 3), ("MatC", 9, 6, 7, 4)])
        np.testing.assert_array_equal(mesh.positions[:4], np.asarray(NEW_POINTS)[[0, 1, 2, 10]])
        np.testing.assert_array_equal(mesh.indices[:2], [[0, 1, 2], [0, 2, 3]])  # sentido de Blender
        self.assertEqual(mesh.bones, before[0].bones)
        np.testing.assert_array_equal(mesh.joints[:, 0], 1)
        np.testing.assert_array_equal(mesh.influences, 1)
        self.assertTrue(mesh.health().ok)
        for k in (1, 2):  # las mallas siguientes se mueven sin cambiar
            np.testing.assert_array_equal(after[k].positions, before[k].positions)
            np.testing.assert_array_equal(after[k].indices, before[k].indices)
            self.assertEqual(after[k].submeshes, before[k].submeshes)
        self.assertEqual(result[:8], self.raw[:8])

    def test_fields_blender_does_not_give_take_the_corpus_values(self):
        result = rebuild_dcm(self.raw, {0: _new_mesh(NEW_POINTS, NEW_TRIS, NEW_SPANS)})
        mesh = parse_dcm(result).meshes[0]
        at = mesh.data_base + 8 + mesh.indices.size * 2
        attrs = np.frombuffer(result, np.uint8, 11 * 20, at).reshape(11, 20)
        vertex = np.frombuffer(result, "<f4", 11 * 13, at + 11 * 20).reshape(11, 13)
        self.assertFalse(attrs[:, 8:16].any())                       # segundo canal a cero
        np.testing.assert_array_equal(attrs[:, 16:20], 255)          # color de la plantilla
        np.testing.assert_array_equal(vertex[:, 4], 1.0)             # +16
        np.testing.assert_array_equal(vertex[:, 8], vertex[:, 7])    # normal.z repetida en +32
        np.testing.assert_array_equal(vertex[:, 12], -1.0)           # signo de bitangente
        np.testing.assert_array_equal(mesh.joints[:, 1:], 0)         # ranuras vacías

    def test_the_box_grows_with_the_new_geometry(self):
        points = np.asarray(NEW_POINTS, dtype=float)
        result = parse_dcm(rebuild_dcm(self.raw, {0: _new_mesh(points, NEW_TRIS, NEW_SPANS)})).meshes[0]
        old = np.asarray(self.parsed.meshes[0].bbox)
        np.testing.assert_allclose(result.bbox[:3], np.minimum(old[:3], points.min(0)))
        np.testing.assert_allclose(result.bbox[3:], np.maximum(old[3:], points.max(0)))

    def test_the_last_mesh_and_a_smaller_mesh(self):
        new = _new_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], [0], bone=0)
        after = parse_dcm(rebuild_dcm(self.raw, {2: new})).meshes
        self.assertEqual((after[2].vertex_count, after[2].triangle_count), (3, 1))
        start, end = struct.unpack_from("<I", self.raw)[0], self.parsed.meshes[2].data_base
        self.assertEqual(rebuild_dcm(self.raw, {2: new})[start:end], self.raw[start:end])

    def test_what_cannot_be_written_is_rejected(self):
        good = dict(positions=NEW_POINTS, triangles=NEW_TRIS, spans=NEW_SPANS)
        shared = _new_mesh(**{**good, "triangles": NEW_TRIS[:4] + [(0, 2, 3)]})  # el 3 en MatA y MatB
        empty = _new_mesh(**{**good, "spans": [1, 0, 1, 1, 0]})                  # MatC sin triángulos
        outside = _new_mesh(**good, bone=2)                                     # la paleta tiene 2
        loose = _new_mesh(**{**good, "triangles": NEW_TRIS[:4], "spans": NEW_SPANS[:4]})
        unnormalised = _new_mesh(**good)
        unnormalised.weights[:, 0] = 0.5
        cases = {"dos materiales": shared, "MatC": empty, "paleta": outside, "sin triángulo": loose,
                 "sin normalizar": unnormalised}
        for message, new in cases.items():
            with self.subTest(message), self.assertRaisesRegex(DcmExportError, message):
                rebuild_dcm(self.raw, {0: new})
        with self.assertRaisesRegex(DcmExportError, "estáticas"):
            rebuild_dcm(self.raw, {1: _new_mesh(NEW_POINTS[:3], [(0, 1, 2)], [0])})

    def test_unknown_or_per_vertex_data_in_the_template_is_not_invented(self):
        new = _new_mesh(NEW_POINTS, NEW_TRIS, NEW_SPANS)
        with self.assertRaisesRegex(DcmExportError, "color por vértice"):
            rebuild_dcm(build_pruneable(), {0: new})
        raw = bytearray(self.raw)
        mesh = self.parsed.meshes[0]
        at = mesh.data_base + 8 + mesh.indices.size * 2
        raw[at + 8:at + 12] = struct.pack("<f", 2.5)
        with self.assertRaisesRegex(DcmExportError, "segundo canal"):
            rebuild_dcm(bytes(raw), {0: new})

    def test_a_box_in_another_space_needs_its_node(self):
        raw = bytearray(self.raw)
        mesh = self.parsed.meshes[2]
        box = np.r_[mesh.positions.min(0), mesh.positions.max(0)] - [0, 0, 10, 0, 0, 10]
        raw[mesh.bbox_offset:mesh.bbox_offset + 24] = box.astype("<f4").tobytes()
        new = _new_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 2)], [(0, 1, 2)], [0], bone=0)
        with self.assertRaisesRegex(DcmExportError, "BBFollowNode"):
            rebuild_dcm(bytes(raw), {2: new})
        node = np.eye(4)
        node[3, 2] = -10.0
        result = parse_dcm(rebuild_dcm(bytes(raw), {2: new}, boxes={2: node})).meshes[2]
        self.assertAlmostEqual(result.bbox[5], max(box[5], 2 - 10), places=5)


class FollowNodeOutsidePaletteTests(unittest.TestCase):
    """La caja de la malla 2 está en `Bone_Junk`, que su paleta no trae (como `Bone_MM_Root`
    en `makermale_elder.2`): solo un esqueleto sabe dónde está ese nodo."""

    DESCRIPTOR = {"Visuals": [{"class": "SkinMeshVisual", "MeshID": 2, "BBFollowNode": "Bone_Junk"}]}

    def setUp(self):
        raw = bytearray(build_pruneable())
        mesh = parse_dcm(bytes(raw)).meshes[2]
        # La caja declarada, en el espacio de un nodo que está 10 unidades más arriba.
        box = np.r_[mesh.positions.min(0), mesh.positions.max(0)] - [0, 0, 10, 0, 0, 10]
        raw[mesh.bbox_offset:mesh.bbox_offset + 24] = box.astype("<f4").tobytes()
        self.raw = bytes(raw)
        self.parsed = parse_dcm(self.raw)

    @staticmethod
    def rig(height):
        return Skeleton("rig", (Bone("Bone_Pelvis", -1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                                Bone("Bone_Junk", 0, (0.0, 0.0, height), (0.0, 0.0, 0.0))))

    def test_fixture_has_the_box_in_another_space(self):
        self.assertTrue(self.parsed.meshes[2].warnings)
        self.assertEqual(bounds_matrices(self.parsed, self.DESCRIPTOR), {})

    def test_node_resolved_from_the_skeleton_that_fits_the_box(self):
        boxes = rig_bounds_matrices(self.parsed, self.DESCRIPTOR, [self.rig(50.0), self.rig(10.0)], [2])
        expected = np.eye(4)
        expected[3, 2] = -10.0
        np.testing.assert_allclose(boxes[2], expected)

    def test_skeleton_that_does_not_fit_the_box_is_not_used(self):
        self.assertEqual(rig_bounds_matrices(self.parsed, self.DESCRIPTOR, [self.rig(50.0)], [2]), {})

    def test_skeletons_are_only_requested_until_resolved(self):
        def candidates():
            yield self.rig(10.0)
            raise AssertionError("pidió otro esqueleto ya resuelta la caja")
        self.assertIn(2, rig_bounds_matrices(self.parsed, self.DESCRIPTOR, candidates(), [2]))
        self.assertEqual(rig_bounds_matrices(self.parsed, self.DESCRIPTOR, candidates(), []), {})

    def test_moving_vertices_updates_the_box_in_the_node_space(self):
        mesh = self.parsed.meshes[2]
        edit = MeshEdit(positions=mesh.positions + [0, 0, 2],
                        normals=np.tile([0, 0, 1], (4, 1)), tangents=np.tile([1, 0, 0, 1], (4, 1)))
        with self.assertRaisesRegex(DcmExportError, "BBFollowNode"):
            patch_dcm(self.raw, {2: edit})
        boxes = rig_bounds_matrices(self.parsed, self.DESCRIPTOR, [self.rig(10.0)], [2])
        patched = parse_dcm(patch_dcm(self.raw, {2: edit}, boxes=boxes)).meshes[2]
        np.testing.assert_array_equal(patched.positions, edit.positions)
        self.assertEqual(patched.bbox[:5], mesh.bbox[:5])
        self.assertAlmostEqual(patched.bbox[5], float(edit.positions[:, 2].max()) - 10, places=5)


if __name__ == "__main__":
    unittest.main()
