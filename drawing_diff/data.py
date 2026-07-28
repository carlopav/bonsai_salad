# Bonsai Salad — drawing_diff tool

from bonsai import tool

from .core import ifc_link


def refresh():
    """Called whenever the comparisons in the IFC change, the way Bonsai's own
    modules purge their caches (bim/module/drawing/data.py:33)."""
    DrawingDiffData.is_loaded = False
    SourceDocuments.is_loaded = False


class DrawingDiffData:
    """What the panel needs before the list is loaded: how many comparisons the
    IFC holds. Counted once and cached, so an open panel does not query the IFC
    on every redraw (bim/module/drawing/data.py:102)."""

    data = {}
    is_loaded = False

    @classmethod
    def load(cls):
        cls.data = {
            "has_ifc": tool.Ifc.get() is not None,
            "path": tool.Ifc.get_path(),
            "total_comparisons": cls.total_comparisons(),
        }
        cls.is_loaded = True

    @classmethod
    def is_stale(cls):
        """Bonsai purges its caches on every IFC change (handler.refresh_ui_data),
        and a module that is not one of Bonsai's own is not in that list: without
        this the count would keep answering for the file open before."""
        return cls.data.get("path") != tool.Ifc.get_path()

    @classmethod
    def total_comparisons(cls):
        ifc_file = tool.Ifc.get()
        if ifc_file is None:
            return 0
        return len(ifc_link.comparisons(ifc_file))


class SourceDocuments:
    """The documents the two lists offer. Blender keeps no reference to what an
    EnumProperty callback hands back, and items built on the fly come out
    corrupted: they live here instead, as Bonsai's own get_titleblocks does
    (bim/module/drawing/prop.py:256), and are reloaded whenever the IFC
    changes."""

    data = {}
    is_loaded = False

    @classmethod
    def load(cls):
        cls.data = {"documents": cls.documents()}
        cls.is_loaded = True

    @classmethod
    def documents(cls):
        ifc_file = tool.Ifc.get()
        if ifc_file is None:
            return []
        return [
            (str(document.id()), document.Name or "Unnamed", f"{document.Scope}: {ifc_link.own_location(document)}")
            for document in ifc_link.svg_documents(ifc_file)
        ]

    @classmethod
    def ids(cls):
        return {item[0] for item in cls.data.get("documents", ())}
