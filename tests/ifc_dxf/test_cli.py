"""Command-line entry point: python -m ifc_dxf."""

import os
import subprocess
import sys

import ezdxf
import pytest

from ifc_dxf.__main__ import main

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIXTURE = os.path.join(os.path.dirname(__file__), "files", "test_ifc_01.ifc")


def test_list_runs_headless():
    """`python -m ifc_dxf` runs the package's real __init__.py (not the
    conftest stub), which must load without Blender/Bonsai."""
    result = subprocess.run(
        [sys.executable, "-m", "ifc_dxf", _FIXTURE, "--list"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "MY STOREY PLAN" in result.stdout


@pytest.mark.parametrize("pipeline", ["accurate", "approximate"])
def test_export_selected_drawing(pipeline, tmp_path):
    out = tmp_path / "plan.dxf"
    assert main([_FIXTURE, "-d", "MY STOREY PLAN", "-o", str(out),
                 "--pipeline", pipeline]) == 0
    assert len(list(ezdxf.readfile(out).modelspace())) > 0


def test_export_to_directory_names_file_after_drawing(tmp_path):
    assert main([_FIXTURE, "-o", str(tmp_path)]) == 0
    assert (tmp_path / "MY STOREY PLAN.dxf").is_file()


def test_unknown_drawing_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([_FIXTURE, "-d", "NO SUCH DRAWING", "-o", str(tmp_path)])
    assert exc.value.code == 2
