"""Darksiders para Blender: modelos, esqueletos y animaciones.

Importa la geometría `.2`, los esqueletos `.o3d` y los clips `.anm` de
Darksiders II: Deathinitive Edition (y el `.o3d` de Darksiders 1), y exporta
copias editadas del `.2` sobre el original.

Este módulo **no importa `bpy`** a propósito. Todo lo que habla con Blender vive
en `operators.py` y se carga al registrar; así `formats/` y `plan.py` se pueden
importar en la suite de tests, sin Blender de por medio.

Instalación y uso: `README.md` en la raíz del repositorio.
"""

import sys

bl_info = {
    "name": "Darksiders",
    "author": "BOTProT800",
    "version": (2, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > Import / Export > Darksiders",
    "description": "Importa modelos, esqueletos y animaciones de Darksiders; exporta ediciones .2",
    "category": "Import-Export",
}


def register():
    from . import operators

    operators.register()


def unregister():
    from . import operators

    operators.unregister()
    forget_submodules()


def forget_submodules() -> list[str]:
    """Saca de `sys.modules` los submódulos del addon.

    Blender recarga el paquete al volver a activarlo, pero no sus submódulos:
    sin esto, instalar otra versión sin reiniciar Blender seguiría usando los
    parsers y operadores de la anterior.
    """
    prefix = __name__ + "."
    gone = [name for name in sys.modules if name.startswith(prefix)]
    for name in gone:
        del sys.modules[name]
    return gone
