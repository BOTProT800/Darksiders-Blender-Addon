"""Edición conservadora de .2 Deathinitive, conservando la plantilla binaria.

No reconstruye campos desconocidos ni contenedores OBP/UPAK. Mantiene paletas,
materiales y el orden de la topología; modifica únicamente buffers verificados.
Una vuelta sin ediciones devuelve exactamente los bytes originales.

Dos ediciones de topología:

* **Borrar** (`prune_dcm`): copia íntegros los registros que sobreviven y
  recalcula solo lo que depende de cuántos quedan.
* **Malla nueva** (`rebuild_dcm`): reescribe todos los vértices y triángulos de
  una malla con piel, conservando su paleta, sus materiales y el resto del
  archivo. Los campos del registro que no salen de Blender llevan el valor que
  tienen en **todo** el corpus (ver su docstring); el único que varía, un
  segundo canal de atributos de significado desconocido, solo se admite a cero.
"""
from __future__ import annotations

import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .mesh import SKINNED_TOTAL_STRIDE, DcmMesh, DcmParseError, parse_dcm


class DcmExportError(ValueError):
    """La edición no se puede representar fielmente en esta variante .2."""


@dataclass
class MeshEdit:
    positions: np.ndarray | None = None
    uvs: np.ndarray | None = None  # V del juego, negada respecto a Blender
    normals: np.ndarray | None = None
    tangents: np.ndarray | None = None  # xyz, signo con UV de Blender (-V juego)
    joints: np.ndarray | None = None
    weights: np.ndarray | None = None


@dataclass
class MeshSubset:
    """Lo que sobrevive de una malla, en índices del original y en orden creciente."""

    vertices: np.ndarray
    triangles: np.ndarray


def deletion_subset(mesh: DcmMesh, vertex_ids, faces) -> MeshSubset | None:
    """Reconoce un borrado en la malla editada; ``None`` si no falta nada.

    ``vertex_ids`` da el vértice original de cada vértice editado y ``faces``
    sus triángulos en índices editados. Blender, al borrar, conserva el orden
    relativo de lo que queda: cualquier otra cosa (añadir, fusionar, reordenar)
    se rechaza. Los vértices que se quedan sin triángulo se descartan también,
    porque ningún `.2` del corpus los tiene.
    """
    n, m = mesh.vertex_count, mesh.triangle_count
    ids = np.asarray(vertex_ids, dtype=np.int64).reshape(-1)
    if ids.size and (ids[0] < 0 or ids[-1] >= n or np.any(np.diff(ids) <= 0)):
        raise DcmExportError("vértices añadidos, duplicados o reordenados: solo se admite borrar")
    original = [tuple(row) for row in mesh.indices.tolist()]
    kept, cursor = [], 0
    for face in faces:
        face = tuple(int(ids[v]) for v in face)
        if len(face) != 3:
            raise DcmExportError("hay caras que no son triángulos: solo se admite borrar")
        while cursor < m and original[cursor] != face:
            cursor += 1
        if cursor == m:
            raise DcmExportError("triángulos nuevos, fusionados o reordenados: solo se admite borrar")
        kept.append(cursor)
        cursor += 1
    if len(kept) == m and ids.size == n:
        return None
    triangles = np.asarray(kept, dtype=np.int64)
    used = np.zeros(n, dtype=bool)
    used[mesh.indices[triangles].ravel()] = True
    idle = np.ones(n, dtype=bool)
    idle[mesh.indices.ravel()] = False  # ya sueltos en el original: se respetan
    return MeshSubset(vertices=ids[used[ids] | idle[ids]], triangles=triangles)


def _increasing(values, limit, label):
    out = np.asarray(values, dtype=np.int64).reshape(-1)
    if out.size and (out[0] < 0 or out[-1] >= limit or np.any(np.diff(out) <= 0)):
        raise DcmExportError(f"{label}: índices fuera de rango, repetidos o desordenados")
    return out


def prune_dcm(source: bytes, subsets: dict[int, MeshSubset]) -> bytes:
    """Devuelve otro .2 sin los vértices y triángulos que faltan en ``subsets``.

    Recalcula recuentos de la cabecera, tramos de material, el bloque de índices
    y el offset de cada malla posterior; el resto de bytes se copia tal cual.
    Antes de tocar nada exige a la plantilla las reglas medidas en el corpus
    (docstring de `mesh`): si no las cumple, no sabemos qué
    significa reescribirla.
    """
    parsed = parse_dcm(source)
    meshes = parsed.meshes
    if any(not isinstance(i, int) or not 0 <= i < len(meshes) for i in subsets):
        raise DcmExportError("índice de malla que no existe en la plantilla")
    if not subsets:
        return source

    def prune(mesh, end, header, index_at, vertex_at, pair_at):
        return _prune_mesh(source, mesh, subsets[mesh.index], end, header, index_at, vertex_at, pair_at)

    output, blocks, validated = _rewrite(source, meshes, {index: prune for index in subsets})
    _verify_pruned(output, meshes, validated, blocks)
    return output


def _rewrite(source, meshes, builders):
    """Cabecera + bloques, con ``builders[i]`` dando el bloque nuevo de la malla i.

    Cada constructor recibe ``(malla, fin de su bloque, cabecera mutable,
    offsets de sus entradas en las tablas de índices, vértices y parejas)`` y
    devuelve ``(bloque, lo validado)``. Las demás mallas se copian tal cual; los
    `head[2]` de todas se recalculan por si cambió el tamaño de alguna anterior.
    """
    data_offset = struct.unpack_from("<I", source, 0)[0]
    index_table = 12
    vertex_table = index_table + 4 * struct.unpack_from("<i", source, 8)[0] + 4
    pair_table = vertex_table + 4 * abs(struct.unpack_from("<i", source, vertex_table - 4)[0]) + 4
    bases = [mesh.data_base for mesh in meshes]
    if bases[0] != data_offset or any(b <= a for a, b in zip(bases, bases[1:])):
        raise DcmExportError("los bloques de malla no siguen el orden verificado")
    ends = bases[1:] + [len(source)]

    output = bytearray(source[:data_offset])
    blocks, validated = [], {}
    for index, mesh in enumerate(meshes):
        if index not in builders:
            blocks.append(source[bases[index]:ends[index]])
            continue
        block, validated[index] = builders[index](
            mesh, ends[index], output,
            index_table + 4 * index, vertex_table + 4 * index, pair_table + 8 * index)
        blocks.append(block)
    cursor = data_offset
    for mesh, block in zip(meshes, blocks):
        # head[2]: base = data_offset + head[2] - 8
        struct.pack_into("<i", output, mesh.bbox_offset - 12, cursor - data_offset + 8)
        output += block
        cursor += len(block)
    return bytes(output), blocks, validated


def _check_skinned_template(source, mesh, end, index_at, vertex_at, pair_at, action):
    """Exige a la malla de la plantilla el layout con piel medido en el corpus."""
    label = f"malla {mesh.index}"
    n, ic = mesh.vertex_count, mesh.indices.size
    if not mesh.is_skinned:
        raise DcmExportError(f"{label}: {action} en mallas estáticas aún no está soportado; "
                             "su registro de vértice no está verificado")
    base = mesh.data_base
    if end - base != 8 + ic * 2 + n * SKINNED_TOTAL_STRIDE:
        raise DcmExportError(f"{label}: el bloque tiene bytes que no sabemos mover")
    head = struct.unpack_from("<5i", source, mesh.bbox_offset - 20)
    if (head[0] != n or struct.unpack_from("<i", source, vertex_at)[0] != n
            or struct.unpack_from("<i", source, index_at)[0] != ic
            or struct.unpack_from("<if", source, pair_at) != (0, 0.0)):
        raise DcmExportError(f"{label}: recuentos de cabecera en una variante no verificada")


def _check_spans(source, mesh):
    """Los tramos de material con la forma del corpus; devuelve el búfer de índices crudo."""
    label = f"malla {mesh.index}"
    ic = mesh.indices.size
    raw = np.frombuffer(source, dtype="<u2", count=ic, offset=mesh.data_base + 8).reshape(-1, 3)
    cursor = 0
    for submesh in mesh.submeshes:
        name = submesh.name or f"material_{submesh.material_index}"
        start, count = submesh.index_start, submesh.index_count
        span = raw.reshape(-1)[start:start + count]
        if (source[submesh.offset] != 255 or start != cursor or count <= 0 or start % 3 or count % 3
                or start + count > ic or submesh.vertex_start != int(span.min())
                or submesh.vertex_count != int(span.max()) - int(span.min()) + 1):
            raise DcmExportError(f"{label}: tramo «{name}» con una forma no verificada")
        cursor = start + count
    if cursor != ic:
        raise DcmExportError(f"{label}: los tramos no cubren el búfer de índices")
    return raw


def _write_span(header, source, submesh, vertex_count, vertex_start, index_count, index_start):
    """Reescribe (vértices, primer vértice, índices, primer índice) de un tramo."""
    fields = submesh.offset + 3 + struct.unpack_from("<H", source, submesh.offset + 1)[0]
    struct.pack_into("<4i", header, fields + 8, vertex_count, vertex_start, index_count, index_start)


def _prune_mesh(source, mesh, subset, end, header, index_at, vertex_at, pair_at):
    """Bloque nuevo de una malla podada; escribe sus campos en ``header``."""
    label = f"malla {mesh.index}"
    n, ic = mesh.vertex_count, mesh.indices.size
    _check_skinned_template(source, mesh, end, index_at, vertex_at, pair_at, "borrar")
    base = mesh.data_base
    vertices = _increasing(subset.vertices, n, f"{label}: vértices")
    triangles = _increasing(subset.triangles, ic // 3, f"{label}: triángulos")
    if not triangles.size:
        raise DcmExportError(f"{label}: se queda sin triángulos; borrar una malla entera no está "
                             "soportado (para ocultarla, colapsa sus vértices)")
    # Tramos: se exigen las reglas del corpus y se recalculan con ellas.
    raw = _check_spans(source, mesh)
    remap = np.full(n, -1, dtype=np.int64)
    remap[vertices] = np.arange(vertices.size)
    kept = remap[raw[triangles]]
    if np.any(kept < 0):
        raise DcmExportError(f"{label}: un triángulo conservado usa un vértice borrado")
    used = np.zeros(vertices.size, dtype=bool)
    used[kept.ravel()] = True
    if not used.all():
        raise DcmExportError(f"{label}: quedarían vértices sin triángulo")

    for submesh in mesh.submeshes:
        name = submesh.name or f"material_{submesh.material_index}"
        start, count = submesh.index_start, submesh.index_count
        inside = (triangles >= start // 3) & (triangles < (start + count) // 3)
        if not inside.any():
            raise DcmExportError(f"{label}: el material «{name}» se queda sin triángulos; "
                                 "vaciar un material no está soportado")
        local = kept[inside]
        _write_span(header, source, submesh, int(local.max()) - int(local.min()) + 1,
                    int(local.min()), 3 * int(inside.sum()),
                    3 * int(np.count_nonzero(triangles < start // 3)))

    count, new_ic = int(vertices.size), int(kept.size)
    struct.pack_into("<i", header, index_at, new_ic)
    struct.pack_into("<i", header, vertex_at, count)
    struct.pack_into("<i", header, mesh.bbox_offset - 20, count)

    def rows(offset, stride):
        table = np.frombuffer(source, dtype=np.uint8, count=n * stride, offset=offset)
        return table.reshape(n, stride)[vertices].tobytes()

    at = base + 8 + ic * 2
    block = struct.pack("<2I", new_ic * 2, 2) + kept.astype("<u2").tobytes()
    for stride in (20, 52, 16, 4):  # atributos, vértices, pesos, huesos
        block += rows(at, stride)
        at += n * stride
    return block, MeshSubset(vertices, triangles)


def _verify_pruned(output, meshes, subsets, blocks):
    """Relee la copia y la compara con lo que debía salir, campo a campo."""
    try:
        result = parse_dcm(output)
    except (DcmParseError, ValueError) as exc:
        raise DcmExportError(f"el .2 podado no supera la relectura: {exc}") from exc
    for index, (before, after) in enumerate(zip(meshes, result.meshes)):
        subset = subsets.get(index)
        if subset is None:
            same = output[after.data_base:after.data_base + len(blocks[index])] == blocks[index]
            if not same or after.submeshes != before.submeshes or after.bbox != before.bbox:
                raise DcmExportError(f"malla {index}: cambió sin haberla editado")
            continue
        keep, triangles = subset.vertices, subset.triangles
        remap = np.full(before.vertex_count, -1, dtype=np.int64)
        remap[keep] = np.arange(keep.size)
        expected = remap[before.indices[triangles]]
        checks = [np.array_equal(after.indices, expected), after.bones == before.bones,
                  after.bbox == before.bbox, len(after.submeshes) == len(before.submeshes)]
        for name in ("positions", "uvs", "normals", "colours", "weights", "joints", "influences"):
            checks.append(np.array_equal(getattr(after, name), getattr(before, name)[keep]))
        for old, new in zip(before.submeshes, after.submeshes):
            first = old.index_start // 3
            where = np.flatnonzero((triangles >= first) & (triangles < first + old.index_count // 3))
            span = after.indices[where]
            checks.append((new.name, new.material_index, new.index_start, new.index_count,
                           new.vertex_start, new.vertex_count)
                          == (old.name, old.material_index, 3 * int(where[0]), 3 * where.size,
                              int(span.min()), int(span.max()) - int(span.min()) + 1))
        if not all(checks):
            raise DcmExportError(f"malla {index}: la copia podada no coincide con el original")
        health = after.health()
        if not health.ok:
            raise DcmExportError(f"malla {index}: tras borrar, {'; '.join(health.failures)}")


@dataclass
class NewMesh:
    """Geometría completa para una malla con piel, sin registros de la plantilla.

    En espacio nativo y convención del juego (V negada), con los triángulos en
    el sentido de Blender: el escritor los invierte, como hace `parse_dcm` al
    leer. ``spans[k]`` es el tramo de la plantilla al que va el triángulo k.
    Cada vértice pertenece a un solo tramo: quien la construye parte los
    compartidos, porque en el corpus los tramos teselan los vértices.
    ``tangents`` es xyz + signo de bitangente.
    """

    positions: np.ndarray
    uvs: np.ndarray
    normals: np.ndarray
    tangents: np.ndarray
    joints: np.ndarray
    weights: np.ndarray
    triangles: np.ndarray
    spans: np.ndarray


# u16 en el búfer de índices.
MAX_VERTICES = 0xFFFF
# Registros con piel (`mesh.py`): atributos de 20 B y vértice de 52 B.
_ATTRIBUTE_RECORD = np.dtype([("uv", "<f4", 2), ("channel", "<f4", 2), ("colour", "u1", 4)])
_VERTEX_RECORD = np.dtype([("influences", "<u4"), ("position", "<f4", 3), ("w", "<f4"),
                           ("normal", "<f4", 3), ("normal_z", "<f4"), ("tangent", "<f4", 4)])


def rebuild_dcm(source: bytes, meshes: dict[int, NewMesh], *, boxes=None) -> bytes:
    """Devuelve otro .2 con la geometría de ``meshes`` en lugar de la de esas mallas.

    De la plantilla se conservan la paleta de huesos (los índices de ``joints``
    son suyos), los materiales y sus nombres, el resto de mallas y todos los
    bytes que no dependen de la geometría. Se recalculan los recuentos, los
    tramos, el `head[2]` de las mallas posteriores y la caja, que se amplía por
    unión en el espacio de ``boxes`` igual que al mover vértices.

    Lo que el registro lleva y Blender no da sale de lo medido en las 1.624
    mallas con piel del corpus (2.283.234 vértices), sin excepciones: el float
    de +16 del vértice vale 1,0; el nº de influencias es el de pesos no nulos,
    que van primero; las ranuras vacías llevan hueso 0 y peso 0. El color se
    copia de la plantilla, y solo si es el mismo en todos sus vértices. Queda
    el segundo canal de atributos (+8..+16): a cero en 1.275 mallas y con
    valores de significado desconocido en 349; solo se reconstruyen las que lo
    tienen a cero, y lo escriben a cero.
    """
    parsed = parse_dcm(source)
    if any(not isinstance(i, int) or not 0 <= i < len(parsed.meshes) for i in meshes):
        raise DcmExportError("índice de malla que no existe en la plantilla")
    if not meshes:
        return source
    expected = {}

    def rebuild(mesh, end, header, index_at, vertex_at, pair_at):
        block, expected[mesh.index] = _rebuild_mesh(source, mesh, meshes[mesh.index], end, header,
                                                   index_at, vertex_at, pair_at, (boxes or {}).get(mesh.index))
        return block, expected[mesh.index]

    output, blocks, _ = _rewrite(source, parsed.meshes, {index: rebuild for index in meshes})
    _verify_rebuilt(output, parsed.meshes, expected, blocks)
    return output


def _rebuild_mesh(source, mesh, new, end, header, index_at, vertex_at, pair_at, box_matrix):
    label = f"malla {mesh.index}"
    _check_skinned_template(source, mesh, end, index_at, vertex_at, pair_at, "reconstruir")
    _check_spans(source, mesh)
    n0 = mesh.vertex_count
    at = mesh.data_base + 8 + mesh.indices.size * 2
    attrs = np.frombuffer(source, dtype=np.uint8, count=n0 * 20, offset=at).reshape(n0, 20)
    if attrs[:, 8:16].any():
        raise DcmExportError(f"{label}: usa el segundo canal de atributos, cuyo significado no se "
                             "conoce; no se pueden inventar sus valores para vértices nuevos")
    colours = np.unique(mesh.colours, axis=0)
    if len(colours) != 1:
        raise DcmExportError(f"{label}: tiene color por vértice; no se puede repartir en vértices nuevos")

    triangles = np.asarray(new.triangles, dtype=np.int64)
    spans = np.asarray(new.spans, dtype=np.int64).reshape(-1)
    n = len(np.asarray(new.positions))
    if triangles.ndim != 2 or triangles.shape[1] != 3 or len(spans) != len(triangles) or not len(triangles):
        raise DcmExportError(f"{label}: triángulos o tramos con dimensiones incorrectas")
    if not 3 <= n <= MAX_VERTICES:
        raise DcmExportError(f"{label}: {n} vértices; el formato admite entre 3 y {MAX_VERTICES}")
    if triangles.min() < 0 or triangles.max() >= n:
        raise DcmExportError(f"{label}: un triángulo usa un vértice que no existe")
    if spans.min() < 0 or spans.max() >= len(mesh.submeshes):
        raise DcmExportError(f"{label}: tramo que no existe en la plantilla")
    positions = _array(new.positions, (n, 3), "posiciones")
    uvs = _array(new.uvs, (n, 2), "UV")
    normals = _array(new.normals, (n, 3), "normales")
    tangents = _array(new.tangents, (n, 4), "tangentes")
    weights = _array(new.weights, (n, 4), "pesos")
    joints = _array(new.joints, (n, 4), "huesos")
    if not np.allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-3):
        raise DcmExportError(f"{label}: normales no unitarias")
    if (not np.allclose(np.linalg.norm(tangents[:, :3], axis=1), 1, atol=1e-3)
            or not np.all(np.isin(tangents[:, 3], (-1.0, 1.0)))
            or np.max(abs(np.sum(normals * tangents[:, :3], axis=1))) > 1e-3):
        raise DcmExportError(f"{label}: base de tangentes inválida")
    live = weights > 0
    if (np.any(weights < 0) or not np.allclose(weights.sum(1), 1, atol=1e-5)
            or np.any(np.diff(live.astype(np.int8), axis=1) > 0)
            or np.any(joints != np.floor(joints)) or np.any(joints < 0)
            or np.any(joints >= min(len(mesh.bones), 256)) or np.any(joints[~live] != 0)):
        raise DcmExportError(f"{label}: pesos sin normalizar, fuera de orden o huesos fuera de la paleta")

    # Cada vértice, en un solo tramo; todos los vértices, en algún triángulo.
    owner = np.full(n, -1, dtype=np.int64)
    for corner in range(3):
        clash = (owner[triangles[:, corner]] >= 0) & (owner[triangles[:, corner]] != spans)
        if clash.any():
            raise DcmExportError(f"{label}: un vértice está en dos materiales; hay que partirlo")
        owner[triangles[:, corner]] = spans
    if np.any(owner < 0):
        raise DcmExportError(f"{label}: hay vértices sin triángulo")

    # Orden del corpus: los tramos en el orden de la plantilla, contiguos en
    # índices y en vértices; dentro de cada uno, por primera aparición.
    order, tri_order = [], []
    for k, submesh in enumerate(mesh.submeshes):
        name = submesh.name or f"material_{submesh.material_index}"
        mine = np.flatnonzero(spans == k)
        if not mine.size:
            raise DcmExportError(f"{label}: el material «{name}» se queda sin triángulos; "
                                 "vaciar un material no está soportado")
        seen = triangles[mine].ravel()
        _, first = np.unique(seen, return_index=True)
        vertices = seen[np.sort(first)]
        index_start = 3 * len(tri_order)
        _write_span(header, source, submesh, vertices.size, len(order), 3 * mine.size, index_start)
        order.extend(vertices.tolist())
        tri_order.extend(mine.tolist())
    order = np.asarray(order, dtype=np.int64)
    remap = np.empty(n, dtype=np.int64)
    remap[order] = np.arange(n)
    faces = remap[triangles[tri_order]]

    struct.pack_into("<i", header, index_at, faces.size)
    struct.pack_into("<i", header, vertex_at, n)
    struct.pack_into("<i", header, mesh.bbox_offset - 20, n)
    header[mesh.bbox_offset:mesh.bbox_offset + 24] = _box(mesh, positions[order], box_matrix).tobytes()

    positions, uvs, normals = positions[order], uvs[order], normals[order]
    tangents, weights, joints = tangents[order], weights[order], joints[order]
    attr = np.zeros(n, dtype=_ATTRIBUTE_RECORD)
    attr["uv"] = uvs
    attr["colour"] = colours[0]
    vertex = np.zeros(n, dtype=_VERTEX_RECORD)
    vertex["influences"] = np.count_nonzero(weights > 0, axis=1)
    vertex["position"] = positions
    vertex["w"] = 1.0
    vertex["normal"] = normals
    vertex["normal_z"] = normals[:, 2]  # el corpus duplica normal.z en +32
    vertex["tangent"] = tangents
    stored = faces[:, ::-1]  # el motor dibuja con el winding invertido
    block = (struct.pack("<2I", stored.size * 2, 2) + stored.astype("<u2").tobytes() + attr.tobytes()
             + vertex.tobytes() + weights.astype("<f4").tobytes() + joints.astype(np.uint8).tobytes())
    expected = {"positions": positions, "uvs": uvs, "normals": normals, "weights": weights,
                "joints": joints.astype(np.uint8), "faces": faces, "colour": colours[0],
                "spans": spans[tri_order]}
    return block, expected


def _box(mesh, positions, matrix):
    """La caja declarada ampliada por la geometría nueva, en el espacio de la caja."""
    if matrix is None:
        if mesh.warnings:
            raise DcmExportError(f"malla {mesh.index}: falta resolver BBFollowNode del .o3d para "
                                 "actualizar la AABB")
        matrix = np.eye(4)
    matrix = np.asarray(matrix, dtype=np.float64)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix[:, 3], [0, 0, 0, 1])):
        raise DcmExportError("matriz de la AABB inválida")
    points = (np.c_[positions, np.ones(len(positions))] @ matrix)[:, :3]
    return np.r_[np.minimum(mesh.bbox[:3], points.min(0)),
                 np.maximum(mesh.bbox[3:], points.max(0))].astype("<f4")


def _verify_rebuilt(output, meshes, expected, blocks):
    """Relee la copia y comprueba campo a campo lo reconstruido y lo intacto."""
    try:
        result = parse_dcm(output)
    except (DcmParseError, ValueError) as exc:
        raise DcmExportError(f"el .2 reconstruido no supera la relectura: {exc}") from exc
    for index, (before, after) in enumerate(zip(meshes, result.meshes)):
        want = expected.get(index)
        if want is None:
            same = output[after.data_base:after.data_base + len(blocks[index])] == blocks[index]
            if not same or after.submeshes != before.submeshes or after.bbox != before.bbox:
                raise DcmExportError(f"malla {index}: cambió sin haberla editado")
            continue
        n = len(want["positions"])
        checks = [after.vertex_count == n, np.array_equal(after.indices, want["faces"]),
                  after.bones == before.bones, len(after.submeshes) == len(before.submeshes),
                  np.array_equal(after.influences, np.count_nonzero(want["weights"] > 0, axis=1)),
                  np.all(after.colours == want["colour"])]
        for name in ("positions", "uvs", "normals", "weights", "joints"):
            checks.append(np.array_equal(getattr(after, name), np.asarray(want[name], dtype=getattr(after, name).dtype)))
        for k, (old, new) in enumerate(zip(before.submeshes, after.submeshes)):
            where = np.flatnonzero(want["spans"] == k)
            span = want["faces"][where]
            checks.append((new.name, new.material_index, new.index_start, new.index_count,
                           new.vertex_start, new.vertex_count)
                          == (old.name, old.material_index, 3 * int(where[0]), 3 * where.size,
                              int(span.min()), int(span.max()) - int(span.min()) + 1))
        if not all(checks):
            raise DcmExportError(f"malla {index}: la copia reconstruida no coincide con la malla pedida")
        health = after.health()
        if not health.ok:
            raise DcmExportError(f"malla {index}: la malla nueva no es sana: {'; '.join(health.failures)}")


def _array(value, shape, label):
    out = np.asarray(value, dtype=np.float32)
    if out.shape != shape or not np.isfinite(out).all():
        raise DcmExportError(f"{label}: dimensiones incorrectas o valores no finitos")
    return out


def _follow_nodes(parsed, descriptor: dict):
    """Pares (malla, BBFollowNode) que declaran para este .2 los visuals del .o3d."""
    for visual in descriptor.get("Visuals", ()):
        if not isinstance(visual, dict):
            continue
        name = visual.get("MeshName")
        if parsed.path and name and name.casefold() != parsed.path.stem.casefold():
            continue
        node = visual.get("BBFollowNode")
        for index in [visual.get("MeshID"), *(visual.get("LODMeshID") or [])]:
            if isinstance(index, int) and 0 <= index < len(parsed.meshes):
                yield index, node


def bounds_matrices(parsed, descriptor: dict) -> dict[int, np.ndarray]:
    """BBFollowNode del .o3d -> espacio de la caja de cada malla del .2."""
    result = {}
    for index, node in _follow_nodes(parsed, descriptor):
        bone = next((b for b in parsed.meshes[index].bones if b.name == node), None)
        if bone is not None:
            matrix = np.asarray(bone.inverse_bind, dtype=np.float64).reshape(4, 4)
            if index in result and not np.allclose(result[index], matrix):
                raise DcmExportError(f"malla {index}: BBFollowNode ambiguo")
            result[index] = matrix
    return result


def rig_bounds_matrices(parsed, descriptor: dict, skeletons, indices) -> dict[int, np.ndarray]:
    """Lo mismo para un BBFollowNode que la paleta de la malla no trae.

    En `makermale_elder.2` la caja de la malla 1 está en `Bone_MM_Root`, que solo
    aparece en `makermale_elder_base.o3d`. La matriz es entonces
    `inverse(world)` del nodo en un esqueleto, la misma que la paleta guarda
    como inverse bind para sus huesos. Como el esqueleto sale de una búsqueda,
    solo se acepta si la geometría de la plantilla, llevada a ese espacio, cabe
    en la caja declarada: el propio archivo hace de oráculo. En el corpus, de
    las 255 mallas en este caso, los `.o3d` de su carpeta resuelven 119 sin
    ninguna ambigüedad y `find_rig` 45 más.

    ``skeletons`` se consume a demanda y se deja de pedir en cuanto todas las
    mallas de ``indices`` tienen matriz: cada esqueleto hay que buscarlo y leerlo.
    """
    nodes: dict[int, set] = {}
    for index, node in _follow_nodes(parsed, descriptor):
        nodes.setdefault(index, set()).add(node)
    pending = {index: next(iter(nodes[index])) for index in indices
               if len(nodes.get(index, ())) == 1 and isinstance(next(iter(nodes[index])), str)}
    result = {}
    candidates = iter(skeletons)
    while pending:
        skeleton = next(candidates, None)
        if skeleton is None:
            break
        world = None
        for index, node in list(pending.items()):
            bone = skeleton.index_of(node)
            if bone is None:
                continue
            world = skeleton.world_matrices() if world is None else world
            try:
                matrix = np.linalg.inv(np.asarray(world[bone], dtype=np.float64))
            except np.linalg.LinAlgError:
                continue
            matrix[:, 3] = (0.0, 0.0, 0.0, 1.0)
            if _box_holds(parsed.meshes[index], matrix):
                result[index] = matrix
                del pending[index]
    return result


def _box_holds(mesh, matrix) -> bool:
    """La geometría de la plantilla, llevada por ``matrix``, cabe en su caja declarada.

    Las cajas vienen acolchadas, así que se pide que quepa y no que coincida.
    La tolerancia es la de `mesh._check_bbox`.
    """
    if not mesh.vertex_count:
        return False
    points = np.c_[np.asarray(mesh.positions, dtype=np.float64), np.ones(mesh.vertex_count)] @ matrix
    declared = np.asarray(mesh.bbox, dtype=np.float64)
    tolerance = max(float(np.abs(declared).max()), 1.0) * 1e-3
    return bool(np.all(points[:, :3].min(0) >= declared[:3] - tolerance)
                and np.all(points[:, :3].max(0) <= declared[3:] + tolerance))


def patch_dcm(source: bytes, edits: dict[int, MeshEdit], *, boxes=None) -> bytes:
    """Devuelve otro .2 de igual tamaño. Valida todo antes de devolver bytes."""
    parsed = parse_dcm(source)
    if any(not isinstance(i, int) or not 0 <= i < len(parsed.meshes) for i in edits):
        raise DcmExportError("índice de malla que no existe en la plantilla")
    output = bytearray(source)
    for index, edit in edits.items():
        mesh = parsed.meshes[index]
        n = mesh.vertex_count
        base = mesh.data_base + 8 + mesh.indices.size * 2
        skinned = mesh.is_skinned
        attrs = base if skinned else base + n * 12
        vertices = base + n * 20 if skinned else base
        positions = mesh.positions if edit.positions is None else _array(edit.positions, (n, 3), "posiciones")
        uvs = mesh.uvs if edit.uvs is None else _array(edit.uvs, (n, 2), "UV")
        moved = not np.array_equal(positions, mesh.positions)
        uv_changed = not np.array_equal(uvs, mesh.uvs)

        def put(offset, values, width, stride, dtype="<f4"):
            view = np.ndarray((n, width), dtype=dtype, buffer=output,
                              offset=offset, strides=(stride, np.dtype(dtype).itemsize))
            view[:] = values

        if moved or uv_changed:
            # La ruta estática tiene otros atributos aún sin verificar. No
            # escribir una posición nueva con normales/tangentes obsoletas.
            if not skinned:
                raise DcmExportError("edición de geometría/UV estática aún no soportada; atributos sin verificar")
            if edit.normals is None or edit.tangents is None:
                raise DcmExportError("una edición de geometría/UV necesita normales y tangentes actualizadas")
            normals = _array(edit.normals, (n, 3), "normales")
            tangents = _array(edit.tangents, (n, 4), "tangentes")
            if not np.allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-3):
                raise DcmExportError("normales no unitarias")
            if (not np.allclose(np.linalg.norm(tangents[:, :3], axis=1), 1, atol=1e-3)
                    or not np.allclose(abs(tangents[:, 3]), 1, atol=1e-3)
                    or np.max(abs(np.sum(normals * tangents[:, :3], axis=1))) > 1e-3):
                raise DcmExportError("base de tangentes inválida")
            # +32 duplica normal.z en el corpus real. No generalizar a otra
            # variante si la plantilla no cumple este layout.
            old = np.ndarray((n, 13), dtype="<f4", buffer=source,
                             offset=vertices, strides=(52, 4))
            if not np.array_equal(old[:, 7], old[:, 8]):
                raise DcmExportError("layout de normales no verificado en esta plantilla")
            put(vertices + 20, normals, 3, 52)
            put(vertices + 32, normals[:, 2:3], 1, 52)
            put(vertices + 36, tangents, 4, 52)
        if moved:
            matrix = (boxes or {}).get(index)
            if matrix is None:
                if mesh.warnings:
                    raise DcmExportError(f"malla {index}: falta resolver BBFollowNode del .o3d para actualizar la AABB")
                matrix = np.eye(4)
            matrix = np.asarray(matrix, dtype=np.float64)
            if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                    or not np.allclose(matrix[:, 3], [0, 0, 0, 1])):
                raise DcmExportError("matriz de la AABB inválida")
            transformed = np.c_[positions, np.ones(n)] @ matrix
            low, high = transformed[:, :3].min(0), transformed[:, :3].max(0)
            # Conserva los márgenes originales (también cubren animación).
            box = np.r_[np.minimum(mesh.bbox[:3], low), np.maximum(mesh.bbox[3:], high)]
            output[mesh.bbox_offset:mesh.bbox_offset + 24] = box.astype("<f4").tobytes()
            put(vertices + 4, positions, 3, 52)
        if uv_changed:
            put(attrs, uvs, 2, 20)

        if (edit.weights is None) != (edit.joints is None):
            raise DcmExportError("pesos e índices de huesos deben escribirse juntos")
        if edit.weights is not None:
            if not skinned:
                raise DcmExportError("la plantilla no tiene skinning")
            weights = _array(edit.weights, (n, 4), "pesos")
            joints = _array(edit.joints, (n, 4), "huesos")
            if (np.any(weights < 0) or not np.allclose(weights.sum(1), 1, atol=1e-5)
                    or np.any(joints != np.floor(joints)) or np.any(joints < 0)
                    or np.any(joints >= min(len(mesh.bones), 256))):
                raise DcmExportError("pesos sin normalizar o huesos fuera de la paleta original")
            if not (np.array_equal(weights, mesh.weights) and np.array_equal(joints, mesh.joints)):
                put(vertices, np.count_nonzero(weights > 0, axis=1)[:, None], 1, 52, "<u4")
                put(vertices + n * 52, weights, 4, 16)
                put(vertices + n * 68, joints, 4, 4, "u1")
    try:
        verified = parse_dcm(bytes(output))
    except (DcmParseError, ValueError) as exc:
        raise DcmExportError(f"el .2 generado no supera la relectura: {exc}") from exc
    for index in edits:
        health = verified.meshes[index].health()
        if not health.ok:
            raise DcmExportError(f"malla {index}: {'; '.join(health.failures)}")
    return bytes(output)


def write_dcm_copy(target: Path, data: bytes, *, source: Path) -> Path:
    """Publica una copia completa; nunca sobreescribe la plantilla original."""
    target, source = Path(target), Path(source)
    if target.suffix.lower() != ".2":
        raise DcmExportError("el destino debe tener extensión .2")
    if target.resolve() == source.resolve() or (target.exists() and os.path.samefile(target, source)):
        raise DcmExportError("elige otra ruta: el original se conserva como plantilla")
    parse_dcm(data)
    fd, temporary = tempfile.mkstemp(prefix=".darksiders-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
