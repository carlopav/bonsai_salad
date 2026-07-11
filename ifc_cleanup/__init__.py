# Bonsai Salad — ifc_cleanup tool

import bpy
from .ui import IfcCleanupProperties, classes as _ui_classes

# PipeCleaner subclasses bonsai's tool.Ifc.Operator: only importable in Blender.
try:
    from .pipe_cleaner import PipeCleaner
    classes = (PipeCleaner,) + tuple(_ui_classes)
except (ImportError, AttributeError):
    classes = tuple(_ui_classes)
class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def register():
    class_register()
    bpy.types.Scene.ifc_cleanup = bpy.props.PointerProperty(type=IfcCleanupProperties)


def unregister():
    class_unregister()
    try:
        del bpy.types.Scene.ifc_cleanup
    except Exception:
        pass
