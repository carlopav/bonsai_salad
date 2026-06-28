#!/usr/bin/env python3
"""
ifc_dxf integration test / CLI entry point.

All logic lives in ifc_dxf/core/.  This file imports directly from the core
modules (bypassing ifc_dxf/__init__.py which requires bpy) so that it can run
without Blender.

Usage:
    python test_ifc_dxf.py                      # export drawing 0
    python test_ifc_dxf.py --drawing 1          # export drawing 1
    python test_ifc_dxf.py --list               # list available drawings
    python test_ifc_dxf.py --ifc path/to/file.ifc
    python test_ifc_dxf.py --out /tmp/out.dxf

Requires:
    - ifcopenshell installed in the Python running this script
    - ezdxf installed (pip install ezdxf)

Optional (for PNG/PDF preview):
    - matplotlib installed (pip install matplotlib)
"""

import sys
import os
import importlib.util

# ---------------------------------------------------------------------------
# Bootstrap: add repo root to sys.path and load ifc_dxf.core without
# triggering ifc_dxf/__init__.py (which requires bpy).
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_CORE_DIR = os.path.join(_REPO_ROOT, "ifc_dxf", "core")


def _load_core_module(name):
    """Import ifc_dxf.core.<name> directly from the file, bypassing ifc_dxf/__init__.py."""
    full_name = f"ifc_dxf.core.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(
        full_name,
        os.path.join(_CORE_DIR, f"{name}.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so that intra-package relative imports work
    sys.modules[full_name] = mod
    # Also register the parent package stubs so relative imports resolve
    _ensure_core_package()
    spec.loader.exec_module(mod)
    return mod


def _ensure_core_package():
    """Register ifc_dxf.core as a package in sys.modules without running ifc_dxf/__init__."""
    import types

    if "ifc_dxf" not in sys.modules:
        pkg = types.ModuleType("ifc_dxf")
        pkg.__path__ = [os.path.join(_REPO_ROOT, "ifc_dxf")]
        pkg.__package__ = "ifc_dxf"
        pkg.__spec__ = None
        sys.modules["ifc_dxf"] = pkg

    if "ifc_dxf.core" not in sys.modules:
        core_pkg = types.ModuleType("ifc_dxf.core")
        core_pkg.__path__ = [_CORE_DIR]
        core_pkg.__package__ = "ifc_dxf.core"
        core_pkg.__spec__ = None
        sys.modules["ifc_dxf.core"] = core_pkg


_ensure_core_package()

# Pre-load all core modules in dependency order so relative imports succeed
for _mod_name in ("camera", "ifc_query", "geometry", "dxf_template", "annotations", "writer", "cli"):
    _load_core_module(_mod_name)

from ifc_dxf.core.cli import main  # noqa: E402  (must come after bootstrap)

if __name__ == "__main__":
    main()
