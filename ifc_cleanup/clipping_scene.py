"""
clipping_scene — the Blender/Bonsai side of clipping, shared by the two
clipping operators: reading an object's frame and writing the clips onto it.
The IFC work itself lives in clipping.py, which knows nothing about bpy.
"""

import numpy as np
import bonsai.core.geometry
import bonsai.tool as tool

from . import clipping


def sync_placement(obj):
    """Writes a viewport move back to IFC before reading the object's frame:
    a stale placement would put the plane in the wrong place."""
    if tool.Ifc.is_moved(obj):
        bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)


def world_matrix(obj):
    return np.array(obj.matrix_world, dtype=np.float64)


def body_of(element):
    return tool.Geometry.get_body_representation(element)


def clip_target(ifc_file, obj, half_spaces, source_matrix, unit_scale):
    """Cuts one object with the half spaces, which are expressed in the frame
    source_matrix maps to the world. Returns how many booleans were created, or
    None when the object cannot take them: no IFC entity, no Body, geometry
    borrowed from its type, or nothing in the Body a boolean can be applied
    to."""
    element = tool.Ifc.get_entity(obj)
    if element is None:
        return None
    representation = body_of(element)
    if representation is None or clipping.has_mapped_geometry(representation):
        return None
    sync_placement(obj)
    matrix = np.linalg.inv(world_matrix(obj)) @ source_matrix
    booleans = clipping.clip_representation(ifc_file, representation, half_spaces, matrix, unit_scale)
    if not booleans:
        return None
    tool.Model.mark_manual_booleans(element, booleans)
    tool.Geometry.reload_representation(obj)
    return len(booleans)
