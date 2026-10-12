"""Piel de Deathinitive: pesos de cada vértice.

Cada malla del `.2` trae **su propia lista de huesos** (la paleta) y los
índices de hueso del vertex buffer apuntan a esa lista, no al esqueleto del
`.o3d`. Usar los índices tal cual produce una malla que se ve bien en pose de
descanso y se retuerce en cuanto se anima, porque el hueso 3 de la cabeza no es
el hueso 3 del esqueleto. La traducción se hace **por nombre**, que es lo único
estable entre los dos archivos, y la hace `plan.py` al crear los grupos de
vértices. En `belial.obp` las seis paletas suman 141 nombres distintos y los
141 resuelven contra los 186 nodos del `.o3d`.

Sobre los pesos: el juego ya los guarda normalizados (`sum = 1.0` con error de
1e-5 en el peor vértice de la malla principal), pero un archivo dañado o una
submalla rígida pueden traer suma cero. Ese caso es el que produce los
*rigging spikes* (vértices estirados al origen), así que `normalise_weights` lo
reconduce al primer hueso de la paleta.
"""

from __future__ import annotations

import numpy as np

WEIGHT_TOLERANCE = 1e-3


def normalise_weights(weights: np.ndarray) -> tuple[np.ndarray, int]:
    """Deja cada vértice con `sum(weights) == 1.0`.

    Devuelve los pesos corregidos y cuántos vértices había que rescatar. Un
    vértice con suma cero se ancla al primer hueso de su paleta: sin eso sale
    disparado al origen y aparece la espina clásica.
    """
    out = np.array(weights, dtype=np.float32, copy=True)
    out[~np.isfinite(out)] = 0.0
    out[out < 0.0] = 0.0
    totals = out.sum(axis=1)

    dead = totals <= 0.0
    rescued = int(dead.sum())
    if rescued:
        out[dead] = 0.0
        out[dead, 0] = 1.0
        totals = out.sum(axis=1)

    out /= totals[:, None]
    return out, rescued


def weights_are_normalised(weights: np.ndarray) -> bool:
    """`sum(weights) == 1.0` en todos los vértices, dentro de tolerancia."""
    if not len(weights):
        return True
    totals = np.asarray(weights, dtype=np.float64).sum(axis=1)
    return bool(np.all(np.abs(totals - 1.0) <= WEIGHT_TOLERANCE))
