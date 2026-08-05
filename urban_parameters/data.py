# Bonsai Salad — urban_parameters tool

from bonsai import tool

from .core import quantities


class ZoneTypes:
    """The ObjectTypes the dropdown offers. Blender keeps no reference to what
    an EnumProperty callback hands back, and items built on the fly come out
    corrupted: they live here instead, as Bonsai's own get_titleblocks does
    (bim/module/drawing/prop.py:256). Rebuilt only when the file's types
    actually change, so an open panel is not rebuilding a list on every
    redraw."""

    names = None
    items = []

    @classmethod
    def load(cls):
        ifc_file = tool.Ifc.get()
        names = quantities.object_types(ifc_file) if ifc_file else []
        if names != cls.names:
            cls.names = names
            cls.items = [(name, name, f"The spatial zones whose ObjectType is {name}") for name in names]
        return cls.items
