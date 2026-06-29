# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import bpy
from .operator import classes as _op_classes, DxfIfcProperties
from .ui import classes as _ui_classes

classes = _op_classes + _ui_classes
class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def register():
    class_register()
    bpy.types.Scene.dxf_ifc = bpy.props.PointerProperty(type=DxfIfcProperties)


def unregister():
    class_unregister()
    try:
        del bpy.types.Scene.dxf_ifc
    except Exception:
        pass
