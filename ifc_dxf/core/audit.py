"""Optional post-export DXF compliance check via ezdxf's Auditor.

This is a convenience, structural check -- not a substitute for a full
CAD-application audit (BricsCAD/AutoCAD/ODA catch geometry-level issues, e.g.
hatch-boundary duplicate vertices, that ezdxf's Auditor does not). ezdxf's
Auditor validates DXF structure: handle/owner integrity, table references,
broken pointers, and similar. `doc.audit()` repairs what it can in memory;
`fixes` are problems found-and-repaired, `errors` are problems it could not
repair. Either being non-empty means the written file was not fully compliant.
"""

import ezdxf


def audit_dxf_file(path):
    """Audit an exported DXF file with ezdxf's structural Auditor.

    Re-reads the file from disk (auditing the actual artifact a CAD app would
    open, not the in-memory doc) and returns ``(is_clean, report)``:

    - ``is_clean``: True when the Auditor found no errors and no fixes.
    - ``report``: human-readable multi-line string (empty when clean),
      suitable for copying to the clipboard.
    """
    doc = ezdxf.readfile(path)
    auditor = doc.audit()
    errors = list(auditor.errors)
    fixes = list(auditor.fixes)

    if not errors and not fixes:
        return True, ""

    lines = [f"ezdxf audit report for: {path}", ""]
    if errors:
        lines.append(f"Errors ({len(errors)}) -- not auto-repairable:")
        for e in errors:
            lines.append(f"  [{e.code}] {e.message}")
        lines.append("")
    if fixes:
        lines.append(f"Fixes ({len(fixes)}) -- problems found and repaired on load:")
        for f in fixes:
            lines.append(f"  [{f.code}] {f.message}")
        lines.append("")
    lines.append(
        "Note: ezdxf's Auditor is a structural check only. For a full "
        "compliance audit (e.g. hatch-boundary geometry) open the file in "
        "BricsCAD/AutoCAD and run AUDIT."
    )
    return False, "\n".join(lines)
