# Bonsai Salad — daylight_ventilation tool


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
        if not cls.is_loaded:
            cls.data = {"spaces": 0, "unverified": [], "orphans": [], "unmeasured": [], "disagreeing": []}
            cls.is_loaded = True
        return cls.data

    @classmethod
    def store(cls, summary):
        cls.data = {
            "spaces": len(summary.rows),
            "unverified": [row.space for row in summary.rows if not row.verified],
            "orphans": list(summary.orphans),
            "unmeasured": list(summary.unmeasured),
            "disagreeing": list(summary.disagreeing),
        }
        cls.is_loaded = True
