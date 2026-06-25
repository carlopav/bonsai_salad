bl_info = {
    "name": "Bonsai Salad",
    "author": "carlopav",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Bonsai Salad",
    "description": "A collection of miscellaneous scripts and tools",
    "category": "3D View",
}

import bpy
from . import panels
from .sheets_to_pdf.operator import classes as _sheets_to_pdf_classes


def register():
    for cls in _sheets_to_pdf_classes:
        bpy.utils.register_class(cls)
    panels.register()


def unregister():
    panels.unregister()
    for cls in reversed(_sheets_to_pdf_classes):
        bpy.utils.unregister_class(cls)
