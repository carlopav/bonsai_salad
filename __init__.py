bl_info = {
    "name": "Bonsai Salad",
    "author": "carlopav",
    "version": (0, 0, 4),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Bonsai Salad",
    "description": "A collection of miscellaneous scripts and tools",
    "category": "3D View",
}

import bpy
from . import panels
from . import sheets_to_pdf
from . import ifc_dxf


def register():
    sheets_to_pdf.register()
    ifc_dxf.register()
    panels.register()


def unregister():
    panels.unregister()
    ifc_dxf.unregister()
    sheets_to_pdf.unregister()
