# -*- coding: utf-8 -*-

# Read a Bambu Lab RFID tag using Proxmark3 and add the data to the library.
# Created for https://github.com/Bambu-Research-Group/RFID-Tag-Guide
#
# Usage:
#   python scanTag.py [--library <path>]
#
# Defaults: library is looked up at ../Bambu-Lab-RFID-Library relative to this script.

import os
import re
import sys
import time
import shutil
import argparse
import itertools
import tempfile
import subprocess
from pathlib import Path

from lib import get_proxmark3_location, run_command
from deriveKeys import kdf

# ---------------------------------------------------------------------------
# Locate the library repo and import shared category maps from it
# ---------------------------------------------------------------------------

def _find_library(override=None):
    if override:
        p = Path(override).resolve()
    else:
        p = (Path(__file__).parent.parent / "Bambu-Lab-RFID-Library").resolve()
    if not p.exists():
        print(f"Error: library not found at {p}")
        print("Use --library <path> to specify its location.")
        sys.exit(1)
    return p

# Peek at sys.argv for --library so we can set up the path before argparse runs.
# This lets us import from the Library's categories.py at module level.
_lib_override = next(
    (sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == '--library'),
    None,
)
LIBRARY_ROOT = _find_library(_lib_override)
sys.path.insert(0, str(LIBRARY_ROOT))

from categories import CATEGORY_MAP, MULTI_COLOR_MATERIAL_MAP, resolve_material  # noqa: E402

pm3Location = None
pm3Command   = "bin/pm3"

# ---------------------------------------------------------------------------
# Proxmark3 helpers
# ---------------------------------------------------------------------------

def setup():
    global pm3Location
    pm3Location = get_proxmark3_location()
    if not pm3Location:
        sys.exit(1)


def read_uid():
    """Return the UID string (e.g. 'E4E447D1') from the tag on the reader."""
    output = run_command([pm3Location / pm3Command, "-d", "1", "-c", "hf mf info"])
    if not output:
        return None
    m = re.search(r'\[\+\]\s+UID:\s+((?:[0-9A-Fa-f]{2}\s*)+)', output)
    if not m:
        return None
    return m.group(1).replace(' ', '').strip().upper()


def _poll_uid_silent():
    """
    Try to read a UID once without printing anything.
    Uses subprocess directly so the spinner line isn't overwritten by run_command output.
    Returns UID string or None.
    """
    try:
        result = subprocess.run(
            [str(pm3Location / pm3Command), "-c", "hf mf info"],
            shell=(os.name == 'nt'),
            capture_output=True,
            timeout=12,
        )
        if result.returncode in (0, 1):
            output = result.stdout.decode('utf-8', errors='replace')
            m = re.search(r'\[\+\]\s+UID:\s+((?:[0-9A-Fa-f]{2}\s*)+)', output)
            if m:
                return m.group(1).replace(' ', '').strip().upper()
    except Exception:
        pass
    return None


def wait_for_tag():
    """
    Poll continuously until a tag is placed on the reader.
    Shows a spinner so the user knows the search is active.
    Returns the UID string.
    """
    spinner = itertools.cycle('|/-\\')
    print("Move the spool slowly over the Proxmark3 until the tag is detected.")
    print("(Ctrl+C to cancel)\n")
    try:
        while True:
            print(f"\r  Searching... {next(spinner)}", end='', flush=True)
            uid = _poll_uid_silent()
            if uid:
                print(f"\r  Tag detected! UID: {uid}          ")
                return uid
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(0)


def write_key_file(uid_hex, dest_path):
    """Derive Bambu keys from the UID and write a binary key file."""
    uid_bytes = bytes.fromhex(uid_hex)
    keys_a, keys_b = kdf(uid_bytes)
    with open(dest_path, 'wb') as f:
        for k in keys_a:
            f.write(k)
        for k in keys_b:
            f.write(k)


def dump_tag(uid, key_path, output_base):
    """
    Run hf mf dump and return the path to the resulting .bin file, or None.
    proxmark3 creates <output_base>-dump.bin.

    Note: pm3.bat cd's to <pm3Location>/client/ before running proxmark3.
    The pm3 binary prepends CWD to the -f output path, so absolute temp-dir
    paths end up mangled (e.g. D:\\Proxmark3\\client\\/C:/Users/...).
    We work around this by passing only the bare filename so proxmark3 writes
    into the client/ directory, then moving the file to output_base afterwards.
    """
    kp = str(key_path).replace('\\', '/')
    rel_name = output_base.name          # e.g. "hf-mf-A7E95F2A"
    client_dir = pm3Location / "client"  # where pm3.bat cds to

    output = run_command([pm3Location / pm3Command, "-c",
                          f"hf mf dump --1k --keys {kp} -f {rel_name}"])
    if output:
        print(output)

    # proxmark3 appends -dump.bin to the base name; fall back to plain .bin
    for suffix in ("-dump.bin", ".bin"):
        src = client_dir / f"{rel_name}{suffix}"
        if src.exists():
            dest = Path(str(output_base) + suffix)
            shutil.move(str(src), dest)
            return dest

    return None

# ---------------------------------------------------------------------------
# Library helpers
# ---------------------------------------------------------------------------

def dest_dir(tag_data, color_name, library_root):
    category = CATEGORY_MAP.get(tag_data['filament_type'], tag_data['filament_type'])
    material = resolve_material(tag_data)
    uid      = tag_data['uid']
    return library_root / category / material / color_name / uid

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Read a Bambu Lab RFID tag with Proxmark3 and add it to the library.'
    )
    parser.add_argument(
        '--library', default=None,
        help='Path to Bambu-Lab-RFID-Library (default: ../Bambu-Lab-RFID-Library)'
    )
    args = parser.parse_args()

    # LIBRARY_ROOT and sys.path were already set at module level via the argv
    # peek above; if --library differs, update them now.
    if args.library and Path(args.library).resolve() != LIBRARY_ROOT:
        new_root = _find_library(args.library)
        sys.path[0] = str(new_root)
        globals()['LIBRARY_ROOT'] = new_root

    # Import remaining library modules (categories already imported at top)
    from parse import Tag
    from convert import sync_directory
    from update_readme import run as update_readme

    setup()

    print("--------------------------------------------------------")
    print("RFID Tag Scanner - Bambu Research Group")
    print("--------------------------------------------------------")

    # --- Step 1: locate tag ---
    uid = wait_for_tag()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir     = Path(tmpdir)
        base_name  = f"hf-mf-{uid}"
        key_path   = tmpdir / f"{base_name}-key.bin"
        dump_base  = tmpdir / base_name

        # --- Step 2: derive keys and write key file ---
        print("Deriving Bambu keys from UID...")
        try:
            write_key_file(uid, key_path)
        except Exception as e:
            print(f"Error deriving keys: {e}")
            sys.exit(1)

        # --- Step 3: dump all sectors ---
        print("Dumping tag sectors (this may take a moment)...")
        dump_file = dump_tag(uid, key_path, dump_base)
        if not dump_file:
            print("Error: dump file was not created.")
            print("The tag may not be a Bambu Lab tag (keys are UID-derived),")
            print("or the tag may be out of range.")
            sys.exit(1)

        # --- Step 4: parse the dump ---
        try:
            with open(dump_file, 'rb') as f:
                tag = Tag(dump_file.name, f.read(), fail_on_warn=False)
        except Exception as e:
            print(f"Error parsing dump: {e}")
            sys.exit(1)

        print()
        print("Tag data read successfully:")
        resolved = resolve_material(tag.data)
        raw      = tag.data['detailed_filament_type']
        material_display = (
            f"{resolved} (tag: {raw})" if resolved != raw else resolved
        )
        print(f"  Material:   {material_display} ({tag.data['filament_type']})")
        print(f"  Colors:     {tag.data['filament_color']} ({tag.data['filament_color_count']} color(s))")
        print(f"  Variant ID: {tag.data['variant_id']}")
        print(f"  UID:        {tag.data['uid']}")
        if tag.warnings:
            print("  Warnings:")
            for w in tag.warnings:
                print(f"    - {w}")
        print()

        # --- Step 5: ask for the colour name ---
        print("Enter the colour name for this spool as it appears in the Bambu Lab store.")
        print(f"(Hex colour is {tag.data['filament_color']} — use that as a reference if unsure.)")
        color_name = input("Colour name: ").strip()
        if not color_name:
            print("Cancelled.")
            sys.exit(0)

        # --- Step 6: confirm destination ---
        dst = dest_dir(tag.data, color_name, LIBRARY_ROOT)
        print()
        print(f"Will write to: {dst.relative_to(LIBRARY_ROOT)}")

        if dst.exists() and any(dst.iterdir()):
            print("Warning: destination already exists and contains files.")
            confirm = input("Continue and overwrite? (y/N) ")
            if confirm.lower() not in ('y', 'yes'):
                print("Cancelled.")
                sys.exit(0)

        dst.mkdir(parents=True, exist_ok=True)

        # --- Step 7: copy dump and key files ---
        shutil.copy2(dump_file, dst / f"{base_name}-dump.bin")
        shutil.copy2(key_path,  dst / f"{base_name}-key.bin")
        print(f"Copied dump and key files.")

        # --- Step 8: generate JSON / NFC / additional formats ---
        print("Generating additional formats (JSON, NFC)...")
        try:
            sync_directory(dst)
        except Exception as e:
            print(f"Warning: could not generate additional formats: {e}")

        print()
        print(f"Done! Tag added at:")
        print(f"  {dst.relative_to(LIBRARY_ROOT)}")
        print()

        # --- Step 9: optionally update README ---
        confirm = input("Update README.md to reflect the new entry? (y/N) ")
        if confirm.lower() in ('y', 'yes'):
            update_readme(LIBRARY_ROOT)


if __name__ == "__main__":
    main()
