"""Normales y tangentes de la exportación: la parte de `roundtrip.py` sin `bpy`.

`_shading` necesita Blender para `calc_tangents`; lo que decide qué normal,
tangente y signo viaja a cada fila del `.2` es `_tangent_frames`, y eso se
prueba aquí con loops sintéticos.
"""

import numpy as np

from io_darksiders.roundtrip import _lengyel_signs, _tangent_frames

UP = (0.0, 0.0, 1.0)
# Un quad en el plano XY con la UV alineada (u = x, v = y en Blender; el .2
# guarda la V negada). Las caras van en el winding de Blender, como las da el
# parser. La fila 4 no está en ninguna cara.
POSITIONS = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (5, 5, 5)], dtype=np.float32)
UVS = np.array([(0, 0), (1, 0), (1, -1), (0, -1), (0, 0)], dtype=np.float32)
FACES = np.array([(0, 1, 2), (0, 2, 3)])
OLD_NORMALS = np.array([UP, UP, UP, UP, (1.0, 0.0, 0.0)])
MIRRORED = UVS * (-1, 1)

# Los loops de los dos triángulos, todos con la tangente de Blender en +X.
ROWS = [0, 1, 2, 0, 2, 3]
CORNERS = [UP] * 6
TANGENTS = [(1, 0, 0)] * 6


def old_tangents(sign):
    frames = np.tile([1.0, 0.0, 0.0, sign], (5, 1))
    frames[4] = (0, 1, 0, 1)
    return frames.astype(np.float32)


def frames(old, after=UVS, signs=(1.0,) * 6, tangents=TANGENTS):
    return _tangent_frames(ROWS, CORNERS, tangents, signs, OLD_NORMALS, old, FACES,
                           (POSITIONS, UVS), (POSITIONS, after))


def test_lengyel_signs_follow_the_uv_orientation():
    np.testing.assert_array_equal(_lengyel_signs(POSITIONS, UVS, FACES, OLD_NORMALS)[:4], 1)
    np.testing.assert_array_equal(_lengyel_signs(POSITIONS, MIRRORED, FACES, OLD_NORMALS)[:4], -1)


def test_untouched_vertices_keep_the_stored_sign():
    # Aunque Blender y Lengyel digan +1: el juego guardó -1 y nada cambió.
    _, result = frames(old_tangents(-1.0))
    np.testing.assert_array_equal(result[:4, 3], -1)


def test_mirrored_uv_flips_the_stored_sign():
    _, result = frames(old_tangents(-1.0), after=MIRRORED)
    np.testing.assert_array_equal(result[:4, 3], 1)


def test_folded_vertex_takes_the_tangent_from_its_side():
    # Fila 0 con loops de los dos signos: sumarlos todos daría +X, y el lado del
    # signo guardado (-1) apunta a -X.
    signs = (1.0, 1.0, 1.0, -1.0, 1.0, 1.0)
    tangents = [(1, 0, 0), (1, 0, 0), (1, 0, 0), (-1, 0, 0), (1, 0, 0), (1, 0, 0)]
    # Una tercera vez la fila 0, del lado +1, para que el total apunte a +X.
    rows, corners = ROWS + [0], CORNERS + [UP]
    _, result = _tangent_frames(rows, corners, tangents + [(1, 0, 0)], signs + (1.0,), OLD_NORMALS,
                                old_tangents(-1.0), FACES, (POSITIONS, UVS), (POSITIONS, UVS))
    np.testing.assert_allclose(result[0], (-1, 0, 0, -1))
    np.testing.assert_allclose(result[1], (1, 0, 0, -1))


def test_rows_without_loops_keep_the_template():
    normals, result = frames(old_tangents(1.0))
    np.testing.assert_allclose(normals[4], (1, 0, 0))
    np.testing.assert_allclose(result[4], (0, 1, 0, 1))
