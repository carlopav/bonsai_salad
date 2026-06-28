# Stub module for bpy, used when running tests outside of Blender.
# In Blender, the real bpy is already in sys.modules and this file is never loaded.
import types as _types


def _noop(*args, **kwargs):
    return None


def _noop_class_factory(classes):
    return _noop, _noop


class _Panel:
    pass


class _Operator:
    pass


class _PropertyGroup:
    pass


class _Scene:
    pass


types = _types.SimpleNamespace(
    Panel=_Panel,
    Operator=_Operator,
    PropertyGroup=_PropertyGroup,
    Scene=_Scene,
)

props = _types.SimpleNamespace(
    StringProperty=lambda **kw: None,
    EnumProperty=lambda **kw: None,
    BoolProperty=lambda **kw: None,
    IntProperty=lambda **kw: None,
    FloatProperty=lambda **kw: None,
    PointerProperty=lambda **kw: None,
)

utils = _types.SimpleNamespace(
    register_class=_noop,
    unregister_class=_noop,
    register_classes_factory=_noop_class_factory,
)

data = _types.SimpleNamespace(filepath="", scenes=[])
path = _types.SimpleNamespace(abspath=lambda p: p)
