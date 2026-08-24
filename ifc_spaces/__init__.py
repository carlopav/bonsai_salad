# Bonsai Salad — ifc_spaces tool

import bpy

from .operator import classes as _op_classes
from .ui import IfcSpacesProperties, classes as _ui_classes

classes = _op_classes + _ui_classes
class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def register():
    class_register()
    bpy.types.Scene.ifc_spaces = bpy.props.PointerProperty(type=IfcSpacesProperties)


def unregister():
    class_unregister()
    try:
        del bpy.types.Scene.ifc_spaces
    except Exception:
        pass
