"""Traduce lo que leen los parsers a un «plan de importación» para Blender.

Este módulo **no importa `bpy` a propósito**: convierte los objetos de
`formats/` en estructuras de Python planas, y `operators.py` se limita después
a construirlas. Así la parte con lógica se puede probar en la suite normal,
sin Blender de por medio (`tests/test_plan.py`).

## Sistema de coordenadas

El motor de Vigil y Blender usan Z como vertical. El plan conserva por defecto
las coordenadas nativas; `face_forward=True` gira 180° sobre Z, (x,y,z)->(-x,-y,z),
para presentar el frente de estos personajes en la vista frontal de Blender.
No es una reflexión: no intercambia lateralidad ni cambia el winding. El addon
guarda esta elección para deshacerla al exportar al juego. Para sacar OBJ o
GLB se usan los exportadores de Blender, que hacen su propia conversión de ejes
a partir de la escena.

Las matrices del esqueleto sí se transponen. El juego usa convención de fila
(`v' = v*M`, traslación en la fila 3) y `mathutils` usa la de columna
(`M @ v`, traslación en la columna 3).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .formats.skin import normalise_weights

Vector3 = tuple[float, float, float]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]

# Longitud del hueso cuando no se puede deducir de un hijo. Blender descarta los
# huesos de longitud cero, así que nunca puede salir 0.
FALLBACK_BONE_LENGTH = 0.5


@dataclass(slots=True)
class BonePlan:
    """Un hueso listo para `armature.edit_bones`."""

    name: str
    parent: str | None
    matrix: Matrix4
    length: float


@dataclass(slots=True)
class ArmaturePlan:
    name: str
    bones: list[BonePlan] = field(default_factory=list)
    animation_source: dict = field(default_factory=dict)


@dataclass(slots=True)
class MeshPlan:
    """Una malla lista para `mesh.from_pydata` más sus extras."""

    name: str
    source_index: int = 0
    vertices: list[Vector3] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    # Una UV por vértice; el addon la reparte por loop.
    uvs: list[tuple[float, float]] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    # Índice dentro de `materials` para cada cara.
    face_materials: list[int] = field(default_factory=list)
    # nombre de hueso -> [(índice de vértice, peso)]
    weights: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    normals: list[Vector3] = field(default_factory=list)

    @property
    def is_skinned(self) -> bool:
        return bool(self.weights)


@dataclass(slots=True)
class ImportPlan:
    name: str
    armature: ArmaturePlan | None = None
    meshes: list[MeshPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _transpose(matrix) -> Matrix4:
    """Fila -> columna, que es lo que espera `mathutils.Matrix`."""
    return tuple(tuple(float(matrix[row][col]) for row in range(4)) for col in range(4))


def orient_vector(vector, face_forward: bool = False) -> Vector3:
    """Giro exacto e involutivo: también es la conversión de vuelta al juego."""
    x, y, z = vector
    return (-float(x), -float(y), float(z)) if face_forward else (float(x), float(y), float(z))


def build_armature_plan(skeleton, *, face_forward: bool = False) -> ArmaturePlan:
    """Convierte un `formats.skeleton.Skeleton` a plan de armature.

    La longitud de cada hueso se toma de la distancia al primer hijo, que es lo
    que hace que la armadura se vea como una cadena y no como una nube de
    palitos iguales. Las hojas heredan la longitud del padre.
    """
    world = skeleton.world_matrices()
    names = [bone.name for bone in skeleton.bones]

    children: dict[int, list[int]] = {}
    for index, bone in enumerate(skeleton.bones):
        if bone.parent >= 0:
            children.setdefault(bone.parent, []).append(index)

    def head(index: int) -> Vector3:
        row = world[index][3]
        return (float(row[0]), float(row[1]), float(row[2]))

    def distance(a: Vector3, b: Vector3) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    lengths: list[float] = []
    for index in range(len(skeleton.bones)):
        own = head(index)
        kids = children.get(index, [])
        span = max((distance(own, head(kid)) for kid in kids), default=0.0)
        lengths.append(span)
    # Las hojas (y los huesos que coinciden con su hijo) copian al padre.
    for index, bone in enumerate(skeleton.bones):
        if lengths[index] > 1e-6:
            continue
        parent_length = lengths[bone.parent] if bone.parent >= 0 else 0.0
        lengths[index] = parent_length if parent_length > 1e-6 else FALLBACK_BONE_LENGTH

    plan = ArmaturePlan(name=skeleton.name or "Skeleton", animation_source={
        "name": skeleton.name, "bones": [asdict(bone) for bone in skeleton.bones],
        "face_forward": face_forward,
        "source": str(skeleton.path) if skeleton.path else "",
    })
    for index, bone in enumerate(skeleton.bones):
        plan.bones.append(
            BonePlan(
                name=names[index],
                parent=names[bone.parent] if bone.parent >= 0 else None,
                matrix=tuple(
                    tuple(value * (-1 if face_forward and row < 2 else 1) for value in values)
                    for row, values in enumerate(_transpose(world[index]))
                ),
                length=float(lengths[index]),
            )
        )
    return plan


def build_mesh_plan(mesh, index: int, base_name: str, skeleton=None, *, face_forward=False) -> MeshPlan:
    """Convierte una `DcmMesh` a plan de malla, con pesos ya por nombre de hueso."""
    plan = MeshPlan(name=f"{base_name}_{index:02d}", source_index=index)
    plan.vertices = [
        orient_vector(p, face_forward) for p in mesh.positions.tolist()
    ]
    if mesh.normals is not None:
        plan.normals = [orient_vector(n, face_forward) for n in mesh.normals.tolist()]
    plan.faces = [(int(a), int(b), int(c)) for a, b, c in mesh.indices.tolist()]
    # La V viene negada en el archivo; Blender usa el mismo origen que OBJ.
    plan.uvs = [(float(u), -float(v)) for u, v in mesh.uvs.tolist()]

    slots: dict[str, int] = {}
    face_materials = [0] * len(plan.faces)
    for submesh in mesh.submeshes:
        label = submesh.name or f"material_{submesh.material_index}"
        if label not in slots:
            slots[label] = len(slots)
            plan.materials.append(label)
        start = submesh.index_start // 3
        for face in range(start, min(start + submesh.index_count // 3, len(face_materials))):
            face_materials[face] = slots[label]
    if not plan.materials:
        plan.materials.append(plan.name)
    plan.face_materials = face_materials

    if mesh.is_skinned and mesh.bones:
        plan.weights = _weights_by_bone(mesh, skeleton)
    return plan


def _weights_by_bone(mesh, skeleton) -> dict[str, list[tuple[int, float]]]:
    """Agrupa los pesos por **nombre** de hueso.

    Los `JOINTS_0` del `.2` son índices locales a la paleta de la malla, así que
    el nombre es lo único que sirve para hablar con el esqueleto. Los pesos se
    normalizan antes, para que un vértice con suma cero no acabe anclado a la
    nada.
    """
    palette = [bone.name for bone in mesh.bones]

    weights, _ = normalise_weights(mesh.weights)
    joints = mesh.joints

    grouped: dict[str, list[tuple[int, float]]] = {}
    for vertex in range(len(joints)):
        combined = {}
        for slot in range(joints.shape[1]):
            weight = float(weights[vertex, slot])
            if weight <= 0.0:
                continue
            name = palette[int(joints[vertex, slot])]
            combined[name] = combined.get(name, 0.0) + weight
        for name, weight in combined.items():
            grouped.setdefault(name, []).append((vertex, weight))
    return grouped


def build_import_plan(
    parsed,
    skeleton=None,
    *,
    base_name: str = "modelo",
    skeleton_note: str | None = None,
    face_forward: bool = False,
) -> ImportPlan:
    """Plan completo para un `.2`: esqueleto (si lo hay) y sus mallas.

    Las mallas que no pasan las puertas de calidad de `formats/quality.py`
    se descartan y se anotan en `notes`, en lugar de meter geometría rota en
    la escena.
    """
    plan = ImportPlan(name=base_name)
    if skeleton is not None and len(skeleton):
        plan.armature = build_armature_plan(skeleton, face_forward=face_forward)
        problems = skeleton.validate()
        if problems:
            plan.notes.append(f"esqueleto con avisos: {'; '.join(problems)}")

    for index, mesh in enumerate(parsed.meshes):
        health = mesh.health()
        if not health.ok:
            plan.notes.append(
                f"malla {index} descartada: {'; '.join(health.failures)}"
            )
            continue
        plan.meshes.append(build_mesh_plan(mesh, index, base_name, skeleton, face_forward=face_forward))
        # Los del parseo (AABB en otro espacio, por ejemplo) y los de la
        # métrica de calidad van al mismo sitio: la barra de estado.
        for aviso in getattr(mesh, "warnings", ()):
            plan.notes.append(aviso)
        if health.warnings:
            plan.notes.append(f"malla {index}: {'; '.join(health.warnings)}")

    if skeleton is not None:
        missing = _unresolved_bones(parsed, skeleton)
        if missing:
            plan.notes.append(
                f"{len(missing)} huesos de la malla no están en el .o3d: "
                + ", ".join(sorted(missing)[:5])
            )
    elif any(mesh.is_skinned for mesh in plan.meshes):
        # Sin el .o3d los pesos se conservan como grupos de vértices, pero no
        # hay jerarquía que enganchar. Mejor decirlo que dejar al usuario
        # preguntándose dónde está la armadura.
        plan.notes.append(
            (skeleton_note or "sin .o3d al lado")
            + ": la malla entra con sus grupos de vértices pero sin armadura"
        )
    return plan


def _unresolved_bones(parsed, skeleton) -> set[str]:
    known = {bone.name for bone in skeleton.bones}
    used: set[str] = set()
    for mesh in parsed.meshes:
        used.update(bone.name for bone in mesh.bones)
    return used - known
