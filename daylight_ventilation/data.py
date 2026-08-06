# Bonsai Salad — daylight_ventilation tool

from bonsai import tool


class Summary:
    """What the last calculation found, kept so the panel can show it without
    redoing the geometry on every redraw. The probe that finds a disagreement is
    far too expensive to run from draw()."""

    is_loaded = False
    data = {}

    @classmethod
    def refresh(cls):
        cls.is_loaded = False

    @classmethod
    def load(cls):
        if not cls.is_loaded or cls.is_stale():
            cls.data = {
                "path": tool.Ifc.get_path(),
                "spaces": 0,
                "unverified": [],
                "withheld": [],
                "orphans": [],
                "unmeasurable": [],
                "disagreeing": [],
            }
            cls.is_loaded = True
        return cls.data

    @classmethod
    def is_stale(cls):
        """Bonsai purges its caches on every IFC change (handler.refresh_ui_data),
        and a module that is not one of Bonsai's own is not in that list: without
        this the panel would keep answering for the file open before, and
        Seleziona would resolve its entity handles against another file."""
        return cls.data.get("path") != tool.Ifc.get_path()

    @classmethod
    def store(cls, summary):
        """withheld and unverified are opposites, not degrees of the same thing:
        a room whose verdict was withheld was never checked, one that is
        unverified was and failed."""
        cls.data = {
            "path": tool.Ifc.get_path(),
            "spaces": len(summary.rows),
            "unverified": [row.space for row in summary.rows if not row.unmeasured_fillings and not row.verified],
            "withheld": [row.space for row in summary.rows if row.unmeasured_fillings],
            "orphans": list(summary.orphans),
            "unmeasurable": list(summary.unmeasurable),
            "disagreeing": list(summary.disagreeing),
        }
        cls.is_loaded = True
