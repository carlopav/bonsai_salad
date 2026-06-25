# Bonsai - OpenBIM 5D Blender Add-on based on Bonsai
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
#
# This file is part of Bonsai5D+.  GNU GPL v3 or later.

import bpy
from .operator import classes as _op_classes
from .ui import classes as _ui_classes

classes = _op_classes + _ui_classes
class_register, class_unregister = bpy.utils.register_classes_factory(classes)


def register():
    class_register()


def unregister():
    class_unregister()
