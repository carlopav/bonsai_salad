#!/usr/bin/env python3
"""Build ifc_dxf Rust crate and copy the resulting .pyd into ifc_dxf/.

Usage:
    python build_ifc_dxf.py            # release build (default)
    python build_ifc_dxf.py --dev      # unoptimised, faster compile
"""

import argparse
import glob
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
CRATE_DIR = os.path.join(ROOT, "ifc_dxf_rs")
WHEELS_DIR = os.path.join(CRATE_DIR, "target", "wheels")
DEST_DIR = os.path.join(ROOT, "ifc_dxf")


def run(args, cwd):
    result = subprocess.run(args, cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def latest_wheel():
    wheels = glob.glob(os.path.join(WHEELS_DIR, "ifc_dxf-*.whl"))
    if not wheels:
        print("ERROR: no wheel found in", WHEELS_DIR)
        sys.exit(1)
    return max(wheels, key=os.path.getmtime)


def extract_pyd(wheel_path, dest_dir):
    with zipfile.ZipFile(wheel_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".pyd") or n.endswith(".so")]
        if not names:
            print("ERROR: no .pyd/.so found in wheel", wheel_path)
            sys.exit(1)
        name = names[0]
        basename = os.path.basename(name)
        out_path = os.path.join(dest_dir, basename)
        with zf.open(name) as src, open(out_path, "wb") as dst:
            dst.write(src.read())
        return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="unoptimised dev build")
    args = parser.parse_args()

    maturin_cmd = [sys.executable, "-m", "maturin", "build"]
    if not args.dev:
        maturin_cmd.append("--release")

    print(f"Building {'dev' if args.dev else 'release'}...")
    run(maturin_cmd, cwd=CRATE_DIR)

    wheel = latest_wheel()
    print(f"Wheel: {os.path.relpath(wheel, ROOT)}")

    out = extract_pyd(wheel, DEST_DIR)
    print(f"Copied: {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
