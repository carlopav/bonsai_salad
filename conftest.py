# Root conftest.py — must be loaded by pytest before any addon package __init__.py.
#
# The repo root is a Blender addon package (has __init__.py that imports bpy).
# pytest's Package collector always imports __init__.py during setup, so we stub
# bpy and all Blender-dependent imports here before that happens.

import os
import sys
import types


def _stub_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _noop(*args, **kwargs):
    return None


def _noop_class_factory(classes):
    return _noop, _noop


# --- bpy stub ----------------------------------------------------------------
_bpy_types = types.SimpleNamespace(
    Panel=type("Panel", (), {}),
    Operator=type("Operator", (), {}),
    PropertyGroup=type("PropertyGroup", (), {}),
    Scene=type("Scene", (), {}),
)
_bpy_props = types.SimpleNamespace(
    StringProperty=lambda **kw: None,
    EnumProperty=lambda **kw: None,
    BoolProperty=lambda **kw: None,
    IntProperty=lambda **kw: None,
    FloatProperty=lambda **kw: None,
    PointerProperty=lambda **kw: None,
)
_bpy_utils = types.SimpleNamespace(
    register_class=_noop,
    unregister_class=_noop,
    register_classes_factory=_noop_class_factory,
)
_bpy_data = types.SimpleNamespace(filepath="", scenes=[])
_bpy_path = types.SimpleNamespace(abspath=lambda p: p)

bpy_mod = _stub_module(
    "bpy",
    types=_bpy_types,
    props=_bpy_props,
    utils=_bpy_utils,
    data=_bpy_data,
    path=_bpy_path,
)

# --- bonsai stub -------------------------------------------------------------
_stub_module("bonsai")
_stub_module("bonsai.tool")

# --- ifc_dxf package stub (prevent ifc_dxf/__init__.py from running) ---------
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.join(_REPO_ROOT, "ifc_dxf", "core")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "ifc_dxf" not in sys.modules:
    _pkg = types.ModuleType("ifc_dxf")
    _pkg.__path__ = [os.path.join(_REPO_ROOT, "ifc_dxf")]
    _pkg.__package__ = "ifc_dxf"
    sys.modules["ifc_dxf"] = _pkg

if "ifc_dxf.core" not in sys.modules:
    _core = types.ModuleType("ifc_dxf.core")
    _core.__path__ = [_CORE_DIR]
    _core.__package__ = "ifc_dxf.core"
    sys.modules["ifc_dxf.core"] = _core
