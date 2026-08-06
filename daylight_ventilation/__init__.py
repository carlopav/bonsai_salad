# Bonsai Salad — daylight_ventilation tool

import bpy

from .operator import classes as _op_classes
from .ui import DaylightVentilationProperties, classes as _ui_classes

classes = _op_classes + _ui_classes
class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def register():
    class_register()
    bpy.types.Scene.daylight_ventilation = bpy.props.PointerProperty(type=DaylightVentilationProperties)


def unregister():
    class_unregister()
    try:
        del bpy.types.Scene.daylight_ventilation
    except Exception:
        pass
