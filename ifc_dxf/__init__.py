# Bonsai Salad — ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import os

try:
    import bpy
    from .operator import classes as _op_classes, IfcDxfProperties
    from .ui import classes as _ui_classes
except ModuleNotFoundError as exc:
    # Outside Blender (`python -m ifc_dxf`, see __main__.py) there is no UI to
    # register: only ifc_dxf.core is usable. Any other missing module is a
    # real error and must surface.
    if exc.name not in ("bpy", "bonsai"):
        raise
    bpy = None

if bpy is not None:
    classes = _op_classes + _ui_classes
    class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def get_template_path() -> str | None:
    """Return the absolute path to ifc_dxf_template_metric.dxf."""
    candidate = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "templates",
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
