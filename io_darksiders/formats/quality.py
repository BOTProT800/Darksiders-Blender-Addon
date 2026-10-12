"""Puertas de calidad de una malla ya decodificada.

Es la parte de `services/common/mesh_quality.py` de ANANSI que usan los
parsers de Darksiders, copiada el 2026-10-10 cuando el addon se independizó de
ANANSI (el mismo autor y la misma licencia MIT). Solo se quitaron las funciones
que aquí nadie llama: detección de tiras, decodificación de index buffers y el
detector de anomalías de UV por material.

Contrato de `calculate_mesh_health`: los problemas de **geometría** (vértices
no finitos, AABB degenerada o kilométrica, índices fuera de rango, malla
triturada) son ``failures`` → la malla no entra en la escena ni se escribe. Los
problemas de **UV** son ``warnings``: una malla sin UV utilizable sigue siendo
geometría válida.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

Face = tuple[int, int, int]

# max_edge / diagonal_AABB está acotado a 1.0 por construcción (dos vértices
# dentro de la caja no pueden separarse más que su diagonal): el umbral útil
# vive en el rango bajo.
MAX_EDGE_RATIO = 0.45
# Una malla sana tiene alguna cara larga suelta; una triturada las tiene a
# miles. Lo que decide es la *proporción* de caras spike.
SPIKE_FACE_WARN = 0.02
SPIKE_FACE_FAIL = 0.25
# Con pocas caras la AABB la definen esas mismas caras, así que max_edge/diag
# tiende a 1 por construcción (un cubo de 12 tris o un triángulo suelto darían
# 100% de "spikes"). Por debajo de este recuento la métrica solo avisa.
MIN_FACES_FOR_SPIKE_GATE = 64
# Por encima tampoco basta la proporción: una malla sana de caras grandes (un
# tablero de quads, unos peldaños) la supera igual. Lo que la separa de una
# malla triturada es la **topología**, y hacen falta dos señales a la vez
# (`_spike_topology`):
#
# * La arista larga de cada cara spike la **comparte** otra cara (la diagonal
#   de un quad, el radio de un abanico). Con índices al azar no la comparte
#   casi ninguna.
# * El área total de las caras no pasa de unas pocas veces la superficie de la
#   AABB. Un IB corrido o un stride equivocado dejan la topología por índice,
#   pero sus caras cruzan la caja y el área se dispara.
#
# Umbrales medidos en ANANSI sobre 1515 mallas reales de otro motor; con pocas
# caras el área de la basura también es pequeña, así que el tope escala con las
# caras hasta `SPIKE_AREA_MAX`.
SPIKE_SHARED_MIN = 0.85
SPIKE_AREA_MAX = 2.0
SPIKE_AREA_PER_FACE = 0.0125
# Soldadura por posición para contar aristas compartidas: las costuras de UV y
# de normal duplican vértices, y sin soldar dos caras pegadas no comparten nada.
SPIKE_WELD = 1e-4
# Escala-invariante: arista más larga contra la mediana.
EDGE_OUTLIER_WARN = 8.0
MIN_DIAGONAL = 1e-6
MAX_DIAGONAL = 1e6
# Fracción de vértices que ninguna cara referencia. Alta = stride o base
# probablemente desalineados. El caso canónico (stride a la mitad → se leen el
# doble de vértices) cae *exactamente* en 0.5, así que la comparación es >=.
UNREFERENCED_WARN = 0.5


@dataclass(frozen=True)
class MeshHealth:
    """Métricas y veredicto de una malla ya decodificada."""

    vertices: int = 0
    faces: int = 0
    aabb_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    aabb_max: tuple[float, float, float] = (0.0, 0.0, 0.0)
    diagonal: float = 0.0
    spike_ratio: float = 0.0
    edge_outlier: float = 0.0
    spike_faces: int = 0
    spike_face_ratio: float = 0.0
    degenerate_faces: int = 0
    out_of_range_faces: int = 0
    unreferenced_vertices: int = 0
    uv_score: float = -1.0
    uv_in01: float = 0.0
    uv_range: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True cuando la geometría es utilizable (los avisos no bloquean)."""
        return not self.failures

    def summary(self) -> str:
        parts = [
            f"{'OK' if self.ok else 'FALLA'} v={self.vertices} f={self.faces}",
            f"diag={self.diagonal:.4g}",
            f"spike={self.spike_ratio:.2f} ({self.spike_face_ratio:.1%} caras)",
            f"uv={self.uv_score:.2f} in01={self.uv_in01:.0%}",
        ]
        if self.failures:
            parts.append("falla: " + ", ".join(self.failures))
        if self.warnings:
            parts.append("aviso: " + ", ".join(self.warnings))
        return "  ".join(parts)


def _spike_topology(
    verts: np.ndarray, tri: np.ndarray, edges: np.ndarray, spike: np.ndarray, diagonal: float
) -> tuple[float, float]:
    """``(compartidas, área)`` de las caras spike (ver `SPIKE_SHARED_MIN`).

    *Compartidas*: fracción de caras spike cuya arista más larga aparece en otra
    cara, con los vértices soldados por posición a `SPIKE_WELD` de la diagonal.
    *Área*: área total de las caras / superficie de la AABB. En una malla plana
    la AABB tiene grosor cero y la superficie es la de sus dos caras.
    """
    lo = verts.min(axis=0)
    ext = verts.max(axis=0) - lo
    cells = np.round((verts - lo) / (diagonal * SPIKE_WELD)).astype(np.int64)
    _unique, weld = np.unique(cells, axis=0, return_inverse=True)
    w = weld.reshape(-1)[tri]
    a = np.minimum(w, np.roll(w, -1, axis=1))
    b = np.maximum(w, np.roll(w, -1, axis=1))
    # El orden de `np.roll` casa con el de `_face_edge_lengths`: arista k de
    # (k, k+1). La clave entera identifica la arista sin importar la cara.
    keys = a * (int(w.max()) + 1) + b
    _uniq, inverse, counts = np.unique(keys.reshape(-1), return_inverse=True, return_counts=True)
    shared = (counts[inverse] >= 2).reshape(keys.shape)
    longest = edges.argmax(axis=1)
    rows = np.flatnonzero(spike)
    fraction = float(shared[rows, longest[rows]].mean()) if len(rows) else 1.0
    p0, p1, p2 = verts[tri[:, 0]], verts[tri[:, 1]], verts[tri[:, 2]]
    area = 0.5 * float(np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1).sum())
    box = 2.0 * float(ext[0] * ext[1] + ext[1] * ext[2] + ext[0] * ext[2])
    return fraction, (area / box if box > 0 else float("inf"))


def _face_edge_lengths(verts: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Longitud de las 3 aristas por cara, en forma ``(F, 3)``."""
    if len(tri) == 0:
        return np.zeros((0, 3))
    p0, p1, p2 = verts[tri[:, 0]], verts[tri[:, 1]], verts[tri[:, 2]]
    return np.stack(
        (
            np.linalg.norm(p1 - p0, axis=1),
            np.linalg.norm(p2 - p1, axis=1),
            np.linalg.norm(p0 - p2, axis=1),
        ),
        axis=1,
    )


def score_uvs(
    uvs: np.ndarray | Sequence[Sequence[float]] | None,
) -> tuple[float, dict[str, float]]:
    """Puntúa un set de UVs. Negativo = inservible.

    Mide lo que distingue un atlas real de ruido: rango (mayoría en [0,1]),
    dispersión (span no colapsado) y variabilidad (no todos los vértices en el
    mismo texel).
    """
    stats: dict[str, float] = {
        "uv_in01": 0.0,
        "uv_in24": 0.0,
        "uv_unique": 0.0,
        "uv_span_u": 0.0,
        "uv_span_v": 0.0,
        "uv_finite": 0.0,
    }
    arr = np.asarray(uvs, dtype=np.float64) if uvs is not None else np.zeros((0, 2))
    if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) < 3:
        return -1.0, stats

    finite = np.isfinite(arr).all(axis=1)
    stats["uv_finite"] = float(finite.mean())
    if not finite.all():
        return -1.0, stats

    uu, vv = arr[:, 0], arr[:, 1]
    span_u = float(uu.max() - uu.min())
    span_v = float(vv.max() - vv.min())
    stats["uv_span_u"] = span_u
    stats["uv_span_v"] = span_v
    in01 = float(
        ((uu >= -0.1) & (uu <= 1.1) & (vv >= -0.1) & (vv <= 1.1)).mean()
    )
    in24 = float(((uu >= -2.0) & (uu <= 2.0) & (vv >= -2.0) & (vv <= 2.0)).mean())
    quantized = np.unique(np.floor(arr * 256.0).astype(np.int64), axis=0)
    unique = float(len(quantized) / len(arr))
    stats["uv_in01"] = in01
    stats["uv_in24"] = in24
    stats["uv_unique"] = unique

    if span_u < 0.05 or span_v < 0.05:
        return -1.0, stats  # todo colapsado en una línea o en un punto
    if unique < 0.04:
        return -1.0, stats  # el mismo texel repetido: no es un mapeo
    if in01 < 0.45 and in24 < 0.75:
        return -1.0, stats  # atlas disparado fuera de rango
    return in01 * 3.0 + in24 + unique * 2.0, stats


def calculate_mesh_health(
    verts: np.ndarray | Sequence[Sequence[float]],
    faces: Sequence[Face] | np.ndarray,
    uvs: np.ndarray | Sequence[Sequence[float]] | None = None,
    *,
    max_edge_ratio: float = MAX_EDGE_RATIO,
    spike_face_warn: float = SPIKE_FACE_WARN,
    spike_face_fail: float = SPIKE_FACE_FAIL,
    min_diagonal: float = MIN_DIAGONAL,
    max_diagonal: float = MAX_DIAGONAL,
) -> MeshHealth:
    """Aplica las puertas de calidad obligatorias antes de usar la malla.

    Ver el contrato en el docstring del módulo: geometría rota → ``failures``,
    UVs rotas → ``warnings``.
    """
    failures: list[str] = []
    warns: list[str] = []

    varr = np.asarray(verts, dtype=np.float64)
    if varr.ndim != 2 or varr.shape[1] != 3 or len(varr) < 3:
        return MeshHealth(
            vertices=int(len(varr)) if varr.ndim == 2 else 0,
            failures=("vértices insuficientes o con forma inválida",),
        )

    nvert = len(varr)
    finite = np.isfinite(varr).all(axis=1)
    if not finite.all():
        failures.append(f"{int((~finite).sum())} vértices no finitos (NaN/Inf)")
    if not finite.any():
        return MeshHealth(vertices=nvert, failures=tuple(failures))

    good = varr[finite]
    lo = good.min(axis=0)
    hi = good.max(axis=0)
    extent = hi - lo
    diagonal = float(np.linalg.norm(extent))
    aabb_min = (float(lo[0]), float(lo[1]), float(lo[2]))
    aabb_max = (float(hi[0]), float(hi[1]), float(hi[2]))

    if diagonal < min_diagonal:
        failures.append("AABB degenerada (todos los vértices coinciden)")
    elif diagonal > max_diagonal:
        failures.append(f"AABB kilométrica (diag={diagonal:.4g})")
    elif float(extent.min()) < diagonal * 1e-4:
        # Puede ser un plano/decal legítimo, así que solo avisamos.
        warns.append("AABB plana en un eje")

    tri = np.asarray(faces, dtype=np.int64).reshape(-1, 3) if len(faces) else np.zeros(
        (0, 3), dtype=np.int64
    )
    if len(tri) == 0:
        failures.append("sin triángulos")
        return MeshHealth(
            vertices=nvert,
            aabb_min=aabb_min,
            aabb_max=aabb_max,
            diagonal=diagonal,
            failures=tuple(failures),
            warnings=tuple(warns),
        )

    in_range = (tri >= 0).all(axis=1) & (tri < nvert).all(axis=1)
    out_of_range = int((~in_range).sum())
    if out_of_range:
        failures.append(f"{out_of_range} caras con índices fuera de rango")
    degenerate = int(
        (
            (tri[:, 0] == tri[:, 1])
            | (tri[:, 1] == tri[:, 2])
            | (tri[:, 0] == tri[:, 2])
        ).sum()
    )
    if degenerate:
        warns.append(f"{degenerate} caras degeneradas")

    usable = tri[in_range]
    unreferenced = nvert - int(len(np.unique(usable))) if len(usable) else nvert
    if unreferenced >= nvert * UNREFERENCED_WARN:
        warns.append(
            f"{unreferenced}/{nvert} vértices sin referenciar (¿stride o base?)"
        )

    spike_ratio = 0.0
    edge_outlier = 0.0
    spike_faces = 0
    spike_face_ratio = 0.0
    if len(usable) and diagonal >= min_diagonal:
        # Los no finitos ya fallaron arriba; aquí solo evitamos que un Inf
        # convierta todas las métricas de aristas en basura.
        vclean = np.nan_to_num(varr, nan=0.0, posinf=0.0, neginf=0.0)
        edges = _face_edge_lengths(vclean, usable)
        per_face = edges.max(axis=1)
        max_edge = float(per_face.max())
        median_edge = float(np.median(edges))
        spike_ratio = max_edge / diagonal
        edge_outlier = max_edge / median_edge if median_edge > 1e-12 else 0.0
        spike = per_face > max_edge_ratio * diagonal
        spike_faces = int(spike.sum())
        spike_face_ratio = spike_faces / len(usable)

        gated = spike_face_ratio > spike_face_fail and len(usable) >= MIN_FACES_FOR_SPIKE_GATE
        shared, area = (
            _spike_topology(vclean, usable, edges, spike, diagonal) if gated else (0.0, 0.0)
        )
        area_max = min(SPIKE_AREA_MAX, SPIKE_AREA_PER_FACE * len(usable))
        if gated and (shared < SPIKE_SHARED_MIN or area > area_max):
            failures.append(
                f"geometría triturada: {spike_face_ratio:.1%} de caras spike"
            )
        elif gated:
            warns.append(
                f"spikes {spike_face_ratio:.1%} en caras que comparten su arista larga "
                f"({shared:.0%}) con área {area:.2f}x la AABB: caras grandes, no triturada"
            )
        elif spike_face_ratio > spike_face_fail:
            warns.append(
                f"spikes {spike_face_ratio:.1%} (métrica no concluyente con "
                f"{len(usable)} caras)"
            )
        elif spike_face_ratio > spike_face_warn:
            warns.append(f"{spike_faces} caras spike ({spike_face_ratio:.1%})")
        elif edge_outlier > EDGE_OUTLIER_WARN:
            warns.append(f"arista máxima {edge_outlier:.1f}× la mediana")

    uv_score, uv_stats = score_uvs(uvs)
    uv_range = (0.0, 0.0, 0.0, 0.0)
    if uvs is not None:
        uarr = np.asarray(uvs, dtype=np.float64)
        if uarr.ndim == 2 and uarr.shape[1] == 2 and len(uarr) and np.isfinite(uarr).all():
            uv_range = (
                float(uarr[:, 0].min()),
                float(uarr[:, 0].max()),
                float(uarr[:, 1].min()),
                float(uarr[:, 1].max()),
            )
        if uv_score < 0:
            warns.append("UVs inservibles según la métrica de rango")

    return MeshHealth(
        vertices=nvert,
        faces=int(len(tri)),
        aabb_min=aabb_min,
        aabb_max=aabb_max,
        diagonal=diagonal,
        spike_ratio=spike_ratio,
        edge_outlier=edge_outlier,
        spike_faces=spike_faces,
        spike_face_ratio=spike_face_ratio,
        degenerate_faces=degenerate,
        out_of_range_faces=out_of_range,
        unreferenced_vertices=unreferenced,
        uv_score=uv_score,
        uv_in01=uv_stats.get("uv_in01", 0.0),
        uv_range=uv_range,
        failures=tuple(failures),
        warnings=tuple(warns),
    )
