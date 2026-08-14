# Bonsai Salad — ifc_cleanup tool

import bpy
from .pipe_cleaner import PipeCleaner
from .align_base_to_cursor import AlignBaseToCursor
from .copy_clipping_planes import CopyClippingPlanes
from .split_layers import SplitLayers
from .ui import IfcCleanupProperties, classes as _ui_classes

classes = (PipeCleaner, AlignBaseToCursor, CopyClippingPlanes, SplitLayers) + tuple(_ui_classes)
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
