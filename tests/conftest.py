# Test stubs. The repo root is a Blender addon package (bpy/bonsai imports),
# so tests never import it: pure-Python cores are exposed as standalone
# packages here, and pytest.ini keeps collection inside tests/.

import os
import sys
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _stub_package(name, path):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [path]
        pkg.__package__ = name
        sys.modules[name] = pkg


# The repo root is itself a package (addon entry): stub it so pytest's
# Package collector never runs its Blender-only __init__.py.
_stub_package("bonsai_salad", _REPO_ROOT)

# Expose each tool's pure core without running the tool's Blender __init__.
_stub_package("ifc_dxf", os.path.join(_REPO_ROOT, "ifc_dxf"))
_stub_package("ifc_dxf.core", os.path.join(_REPO_ROOT, "ifc_dxf", "core"))
_stub_package("mep", os.path.join(_REPO_ROOT, "mep"))
_stub_package("drawing_diff", os.path.join(_REPO_ROOT, "drawing_diff"))
_stub_package("urban_parameters", os.path.join(_REPO_ROOT, "urban_parameters"))
_stub_package("urban_parameters.core", os.path.join(_REPO_ROOT, "urban_parameters", "core"))
_stub_package("daylight_ventilation", os.path.join(_REPO_ROOT, "daylight_ventilation"))
_stub_package("daylight_ventilation.core", os.path.join(_REPO_ROOT, "daylight_ventilation", "core"))
_stub_package("ifc_cleanup", os.path.join(_REPO_ROOT, "ifc_cleanup"))
