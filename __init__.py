bl_info = {
    "name": "Bonsai Salad",
    "author": "carlopav",
    "version": (0, 0, 7),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Bonsai Salad",
    "description": "A collection of miscellaneous scripts and tools",
    "category": "3D View",
}

import bpy
from . import panels
from . import sheets_to_pdf
from . import ifc_dxf
from . import dxf_ifc
from . import mep
from . import ifc_cleanup
from . import drawing_diff


def register():
    # The category panels first: a tool panel's bl_parent_id must already exist.
    panels.register()
    sheets_to_pdf.register()
    ifc_dxf.register()
    dxf_ifc.register()
    mep.register()
    ifc_cleanup.register()
    drawing_diff.register()


def unregister():
    drawing_diff.unregister()
    ifc_cleanup.unregister()
    mep.unregister()
    dxf_ifc.unregister()
    ifc_dxf.unregister()
    sheets_to_pdf.unregister()
    panels.unregister()
