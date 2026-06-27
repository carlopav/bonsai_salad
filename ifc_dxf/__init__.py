# Bonsai Salad — ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import os
import bpy
from .operator import classes as _op_classes, IfcDxfProperties
from .ui import classes as _ui_classes

classes = _op_classes + _ui_classes
class_register, class_unregister = bpy.utils.register_classes_factory(classes)

_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_TEMPLATE = os.path.join(_ADDON_DIR, "ifc_dxf_template.dxf")


def register():
    class_register()
    bpy.types.Scene.ifc_dxf = bpy.props.PointerProperty(type=IfcDxfProperties)
    # Set default template path on first registration if file exists
    import bpy as _bpy
    try:
        for scene in _bpy.data.scenes:
            if not scene.ifc_dxf.template_path:
                scene.ifc_dxf.template_path = _DEFAULT_TEMPLATE
    except Exception:
        pass


def unregister():
    class_unregister()
    try:
        del bpy.types.Scene.ifc_dxf
    except Exception:
        pass
