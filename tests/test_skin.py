"""Pesos de Deathinitive: normalización antes de crear los grupos de vértices.

El fallo que cubre este archivo es el de los pesos con suma cero, que estiran
el vértice al origen (*rigging spike*). La traducción de la paleta local por
nombre de hueso la prueba `tests/test_plan.py`.
"""

from __future__ import annotations

import numpy as np

from io_darksiders.formats.mesh import parse_dcm
from io_darksiders.formats.skin import normalise_weights, weights_are_normalised
from tests.test_mesh import build_dcm


def test_los_pesos_del_juego_ya_suman_uno() -> None:
    mesh = parse_dcm(build_dcm()).meshes[0]
    assert weights_are_normalised(mesh.weights)
    fixed, rescued = normalise_weights(mesh.weights)
    assert rescued == 0
    np.testing.assert_allclose(fixed.sum(axis=1), 1.0, atol=1e-6)


def test_pesos_con_suma_cero_se_anclan_al_primer_hueso() -> None:
    """Sin esto el vértice sale disparado al origen: la espina clásica."""
    weights = np.array([[0.0, 0.0, 0.0, 0.0], [0.5, 0.5, 0.0, 0.0]], dtype=np.float32)
    fixed, rescued = normalise_weights(weights)

    assert rescued == 1
    np.testing.assert_allclose(fixed[0], (1.0, 0.0, 0.0, 0.0))
    np.testing.assert_allclose(fixed.sum(axis=1), 1.0)


def test_pesos_sin_normalizar_se_reescalan() -> None:
    weights = np.array([[2.0, 2.0, 0.0, 0.0]], dtype=np.float32)
    fixed, rescued = normalise_weights(weights)

    assert rescued == 0
    np.testing.assert_allclose(fixed[0], (0.5, 0.5, 0.0, 0.0))


def test_pesos_no_finitos_o_negativos_se_sanean() -> None:
    weights = np.array([[np.nan, -1.0, 3.0, 1.0]], dtype=np.float32)
    fixed, _ = normalise_weights(weights)

    assert np.isfinite(fixed).all()
    assert (fixed >= 0).all()
    np.testing.assert_allclose(fixed.sum(), 1.0)


def test_weights_are_normalised_detecta_una_suma_que_no_es_uno() -> None:
    assert not weights_are_normalised(np.array([[0.5, 0.2, 0.0, 0.0]]))
    assert weights_are_normalised(np.zeros((0, 4)))
