"""Doble de `bpy` / `mathutils` para ejercitar el addon de Blender sin Blender.

**Qué prueba y qué no.** Esto no valida la semántica de Blender: reproduce solo
la superficie de la API que usa `io_darksiders/operators.py`, así que
un test verde demuestra que el código *se ejecuta y cablea bien lo suyo*
—nombres de hueso renombrados, grupos de vértices, reparto de materiales,
índices de UV—, no que Blender haga después lo que esperamos. La validación de
verdad exige abrir Blender.

Se registra en `sys.modules` antes de importar el addon, que es la única forma
de que sus `import bpy` de nivel de módulo resuelvan.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace


class Vector(tuple):
    """Lo justo: resta y longitud, que es lo que usa `connect_bones`."""

    def __new__(cls, values=(0.0, 0.0, 0.0)):
        return super().__new__(cls, (float(v) for v in values))

    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self, other))

    @property
    def length(self) -> float:
        return sum(v * v for v in self) ** 0.5


class Matrix:
    """Matriz 4x4 por filas, con la inversa que hace falta para el emparentado."""

    def __init__(self, rows=None):
        if rows is None:
            rows = [[float(r == c) for c in range(4)] for r in range(4)]
        self.rows = [[float(v) for v in row] for row in rows]

    def __getitem__(self, index):
        return self.rows[index]

    def __iter__(self):
        return iter(self.rows)

    @property
    def translation(self) -> Vector:
        return Vector(self.rows[i][3] for i in range(3))

    def inverted(self) -> "Matrix":
        # Solo se invierten transformaciones afines de objeto, y en este addon
        # siempre son la identidad; basta con una inversa de rígido/afín simple.
        import numpy as np

        return Matrix(np.linalg.inv(np.array(self.rows)).tolist())


class EditBone:
    def __init__(self, name: str):
        self.name = name
        self._head = Vector()
        self._tail = Vector((0.0, 1.0, 0.0))
        self.parent = None
        self.use_connect = False
        self._matrix = Matrix()

    # Blender convierte a Vector cualquier tupla que se asigne a head/tail; sin
    # esta coerción el doble no reproduce ese detalle y la resta revienta.
    @property
    def head(self) -> Vector:
        return self._head

    @head.setter
    def head(self, value) -> None:
        self._head = Vector(value)

    @property
    def tail(self) -> Vector:
        return self._tail

    @tail.setter
    def tail(self, value) -> None:
        self._tail = Vector(value)

    @property
    def matrix(self) -> Matrix:
        return self._matrix

    @property
    def matrix_local(self) -> Matrix:
        return self._matrix

    @matrix.setter
    def matrix(self, value: Matrix) -> None:
        self._matrix = value
        # Blender recoloca cabeza y cola conservando la longitud actual.
        length = self.length or 1.0
        self.head = value.translation
        axis = Vector(value[i][1] for i in range(3))  # eje Y local del hueso
        self.tail = Vector(h + a * length for h, a in zip(self.head, axis))

    @property
    def length(self) -> float:
        return (self.tail - self.head).length

    @length.setter
    def length(self, value: float) -> None:
        current = self.length
        if current <= 0.0:
            self.tail = Vector((self.head[0], self.head[1] + value, self.head[2]))
            return
        scale = value / current
        self.tail = Vector(
            h + (t - h) * scale for h, t in zip(self.head, self.tail)
        )


class EditBones:
    def __init__(self):
        self._bones: list[EditBone] = []
        self._names: set[str] = set()

    def new(self, name: str) -> EditBone:
        # Blender trunca a 63 caracteres y desambigua con sufijo numérico.
        name = name[:63]
        candidate, counter = name, 0
        while candidate in self._names:
            counter += 1
            candidate = f"{name[:59]}.{counter:03d}"
        self._names.add(candidate)
        bone = EditBone(candidate)
        self._bones.append(bone)
        return bone

    def __iter__(self):
        return iter(self._bones)

    def __len__(self):
        return len(self._bones)


class Armature:
    def __init__(self, name: str):
        self.name = name
        self.edit_bones = EditBones()
        self.bones = self.edit_bones


class Polygon:
    def __init__(self, index: int, vertices):
        self.index = index
        self.vertices = vertices
        self.material_index = 0


class Loop:
    def __init__(self, index: int, vertex_index: int):
        self.index = index
        self.vertex_index = vertex_index


class UVLoop:
    def __init__(self):
        self.uv = (0.0, 0.0)


class UVLayer:
    def __init__(self, name: str, loops: int):
        self.name = name
        self.data = [UVLoop() for _ in range(loops)]


class UVLayers:
    def __init__(self, mesh):
        self._mesh = mesh
        self._layers: list[UVLayer] = []

    def new(self, name: str = "UVMap") -> UVLayer:
        layer = UVLayer(name, len(self._mesh.loops))
        self._layers.append(layer)
        return layer

    def __len__(self):
        return len(self._layers)


class Mesh:
    def __init__(self, name: str):
        self.name = name
        self.vertices: list = []
        self.polygons: list[Polygon] = []
        self.loops: list[Loop] = []
        self.materials: list = []
        self.uv_layers = UVLayers(self)
        self.validated = False
        self.updated = False

    def from_pydata(self, vertices, edges, faces):
        self.vertices = [SimpleNamespace(index=i, co=v) for i, v in enumerate(vertices)]
        self.polygons = [Polygon(i, tuple(f)) for i, f in enumerate(faces)]
        self.loops = []
        for face in faces:
            for vertex in face:
                self.loops.append(Loop(len(self.loops), vertex))

    def normals_split_custom_set_from_vertices(self, normals):
        self.custom_normals = list(normals)

    def validate(self, verbose: bool = False) -> bool:
        self.validated = True
        return False

    def update(self) -> None:
        self.updated = True


class VertexGroup:
    def __init__(self, name: str):
        self.name = name
        self.weights: dict[int, float] = {}
        self.calls = 0

    def add(self, indices, weight, mode):
        self.calls += 1
        for index in indices:
            self.weights[index] = weight


class VertexGroups:
    def __init__(self):
        self._groups: list[VertexGroup] = []

    def new(self, name: str) -> VertexGroup:
        group = VertexGroup(name)
        self._groups.append(group)
        return group

    def __iter__(self):
        return iter(self._groups)

    def __len__(self):
        return len(self._groups)

    def __getitem__(self, key):
        if isinstance(key, str):
            return next(g for g in self._groups if g.name == key)
        return self._groups[key]


class Modifier:
    def __init__(self, name: str, kind: str):
        self.name = name
        self.type = kind
        self.object = None


class Modifiers:
    def __init__(self):
        self._items: list[Modifier] = []

    def new(self, name: str, kind: str) -> Modifier:
        modifier = Modifier(name, kind)
        self._items.append(modifier)
        return modifier

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class Object:
    def __init__(self, name: str, data):
        self.name = name
        self.data = data
        self.parent = None
        self.matrix_parent_inverse = Matrix()
        self.matrix_world = Matrix()
        self.modifiers = Modifiers()
        self.vertex_groups = VertexGroups()
        self.mode = "OBJECT"
        self.selected = False
        self._properties = {}

    def __setitem__(self, key, value):
        self._properties[key] = value

    def __getitem__(self, key):
        return self._properties[key]

    def get(self, key, default=None):
        return self._properties.get(key, default)

    def select_set(self, value: bool) -> None:
        self.selected = value


class _Collection:
    def __init__(self):
        self.objects = SimpleNamespace(linked=[], link=self._link)

    def _link(self, obj):
        self.objects.linked.append(obj)


class _Data:
    def __init__(self):
        self.armatures = SimpleNamespace(new=lambda name: Armature(name))
        self.meshes = SimpleNamespace(new=lambda name: Mesh(name))
        self.materials_made: list = []
        self.objects = SimpleNamespace(new=lambda name, data: Object(name, data))
        self.materials = SimpleNamespace(new=self._material, get=lambda name: None)

    def _material(self, name):
        material = SimpleNamespace(name=name, use_nodes=False)
        self.materials_made.append(material)
        return material


class ModeSetOp:
    """`bpy.ops.object.mode_set` con `poll()` conmutable, como en Blender."""

    def __init__(self):
        self.poll_result = True
        self.calls: list[str] = []
        self.active_getter = lambda: None

    def poll(self) -> bool:
        return self.poll_result

    def __call__(self, mode="OBJECT"):
        if not self.poll_result:
            raise RuntimeError("contexto incorrecto para mode_set")
        self.calls.append(mode)
        active = self.active_getter()
        if active is not None:
            active.mode = mode


def install():
    """Registra los módulos falsos y devuelve el espacio de nombres de control."""
    bpy = ModuleType("bpy")
    data = _Data()
    mode_set = ModeSetOp()

    class _Ctx:
        def __init__(self):
            self.collection = _Collection()
            self.view_layer = SimpleNamespace(objects=SimpleNamespace(active=None))
            self.window = None
            self.preferences = SimpleNamespace(addons={})

        def temp_override(self, **kwargs):
            class _Guard:
                def __enter__(inner):
                    mode_set.poll_result = True
                    return inner

                def __exit__(inner, *exc):
                    return False

            return _Guard()

    context = _Ctx()
    mode_set.active_getter = lambda: context.view_layer.objects.active

    bpy.data = data
    bpy.context = SimpleNamespace(
        window_manager=SimpleNamespace(windows=[]),
        preferences=context.preferences,
    )
    bpy.ops = SimpleNamespace(object=SimpleNamespace(mode_set=mode_set))
    bpy.utils = SimpleNamespace(
        register_class=lambda cls: None, unregister_class=lambda cls: None
    )

    def _prop(**kwargs):
        return kwargs

    props = ModuleType("bpy.props")
    for name in (
        "BoolProperty",
        "CollectionProperty",
        "StringProperty",
        "EnumProperty",
        "FloatProperty",
    ):
        setattr(props, name, _prop)

    types = ModuleType("bpy.types")
    types.AddonPreferences = type("AddonPreferences", (), {})
    types.Operator = type("Operator", (), {})
    types.OperatorFileListElement = type("OperatorFileListElement", (), {})
    types.TOPBAR_MT_file_import = SimpleNamespace(
        append=lambda fn: None, remove=lambda fn: None
    )
    types.TOPBAR_MT_file_export = SimpleNamespace(append=lambda fn: None, remove=lambda fn: None)

    bpy.props = props
    bpy.types = types

    io_utils = ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    io_utils.ExportHelper = type("ExportHelper", (), {})
    bpy_extras = ModuleType("bpy_extras")
    bpy_extras.io_utils = io_utils

    mathutils = ModuleType("mathutils")
    mathutils.Matrix = Matrix
    mathutils.Vector = Vector

    sys.modules.update(
        {
            "bpy": bpy,
            "bpy.props": props,
            "bpy.types": types,
            "bpy_extras": bpy_extras,
            "bpy_extras.io_utils": io_utils,
            "mathutils": mathutils,
        }
    )
    return SimpleNamespace(bpy=bpy, context=context, data=data, mode_set=mode_set)


def uninstall():
    for name in (
        "bpy",
        "bpy.props",
        "bpy.types",
        "bpy_extras",
        "bpy_extras.io_utils",
        "mathutils",
    ):
        sys.modules.pop(name, None)
