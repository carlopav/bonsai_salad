"""IFC identity carried into DXF as XDATA (appid ``IFC_DXF``).

The DXF-native equivalent of Bonsai's SVG semantic attributes (``ifc:guid`` +
IFC class on every ``<g>``): selecting any exported entity in a CAD
application recovers the originating IFC element(s).

Layout on each entity:

    1001  IFC_DXF
    1000  <IfcClass>                 (e.g. "IfcWall")
    1000  <GlobalId>                 one per contributing element -- fused
    1000  <GlobalId>                 geometry (unioned wall outlines/hatches)
    ...                              legitimately lists several

Shared type BLOCKs additionally store the *type*'s GlobalId in the block
description (group code 4); the INSERT's XDATA holds the instance GlobalId.
"""

XDATA_APPID = "IFC_DXF"


def ensure_xdata_appid(doc):
    """Register the IFC_DXF APPID table entry (idempotent).

    Must exist before entities carry XDATA under it: ezdxf's Auditor strips
    XDATA whose appid is not in the APPID table.
    """
    if XDATA_APPID not in doc.appids:
        doc.appids.new(XDATA_APPID)


def set_ifc_xdata(entity, ifc_class, gids):
    """Attach IFC identity to a DXF entity.

    gids: a single GlobalId string or an iterable of them (fused geometry).
    No-op when no GlobalId is available.
    """
    if isinstance(gids, str):
        gids = [gids]
    gids = [g for g in (gids or []) if g]
    if not gids:
        return
    entity.set_xdata(XDATA_APPID,
                     [(1000, ifc_class or "")] + [(1000, g) for g in gids])
