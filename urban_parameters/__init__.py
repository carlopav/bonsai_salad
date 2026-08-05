# Bonsai Salad — urban_parameters tool

import bpy

from .operator import classes as _op_classes
from .ui import UrbanParametersProperties, classes as _ui_classes

classes = _op_classes + _ui_classes
class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def register():
    class_register()
    bpy.types.Scene.urban_parameters = bpy.props.PointerProperty(type=UrbanParametersProperties)


def unregister():
    class_unregister()
    try:
        del bpy.types.Scene.urban_parameters
    except Exception:
        pass
