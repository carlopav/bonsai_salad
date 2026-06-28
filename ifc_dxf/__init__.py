# Bonsai Salad — ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import os
import bpy
from .operator import classes as _op_classes, IfcDxfProperties
from .ui import classes as _ui_classes

classes = _op_classes + _ui_classes
class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def get_template_path() -> str | None:
    """Return the absolute path to ifc_dxf_template_metric.dxf.

    The template lives next to bonsai_salad/__init__.py, one level above this
    file (bonsai_salad/ifc_dxf/__init__.py).  This works for both a Blender
    addon installed from ZIP (extracted as a directory) and a dev checkout.
    """
    candidate = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ifc_dxf_template_metric.dxf",
    )
    return candidate if os.path.isfile(candidate) else None


def register():
    class_register()
    bpy.types.Scene.ifc_dxf = bpy.props.PointerProperty(type=IfcDxfProperties)
    tpl = get_template_path()
    if tpl:
        try:
            for scene in bpy.data.scenes:
                if not scene.ifc_dxf.template_path:
                    scene.ifc_dxf.template_path = tpl
        except Exception:
            pass


def unregister():
    class_unregister()
    try:
        del bpy.types.Scene.ifc_dxf
    except Exception:
        pass
