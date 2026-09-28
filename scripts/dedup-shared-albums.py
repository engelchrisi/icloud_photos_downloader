#!/usr/bin/env python3
"""
Delete files from changrun-shared that exist in christof-shared with same size.
Reports deletions and space freed.
"""

import os
import sys
from pathlib import Path
from collections import defaultdict

def scan_files(root):
    """Scan dir and build map of filename -> (size, full_path)."""
    files = defaultdict(list)
    root_path = Path(root)

    if not root_path.exists():
        print(f"ERROR: {root} does not exist", file=sys.stderr)
        return files

    for fpath in root_path.rglob('*'):
        if fpath.is_file():
            name = fpath.name
            size = fpath.stat().st_size
            files[name].append((size, fpath))

    return files

def main():
    if len(sys.argv) != 3:
        print("Usage: dedup_shared.py <changrun-shared> <christof-shared>", file=sys.stderr)
        sys.exit(1)

    changrun_root = sys.argv[1]
    christof_root = sys.argv[2]

    print("Scanning changrun-shared...", file=sys.stderr)
    changrun_files = scan_files(changrun_root)

    print("Scanning christof-shared...", file=sys.stderr)
    christof_files = scan_files(christof_root)

    print("\nFinding duplicates (same filename + size)...", file=sys.stderr)

    to_delete = []
    total_space = 0

    for filename, changrun_entries in changrun_files.items():
        if filename not in christof_files:
            continue

        christof_entries = {size: fpath for size, fpath in christof_files[filename]}

        for size, changrun_path in changrun_entries:
            if size in christof_entries:
                christof_path = christof_entries[size]
                to_delete.append((filename, size, changrun_path))
                total_space += size

    # Report
    if to_delete:
        print(f"\nFound {len(to_delete)} duplicate files ({total_space / (1024**3):.2f} GB):\n")
        for fname, size, path in sorted(to_delete):
            print(f"  {fname} ({size:,} bytes)")

        # Do deletion
        print(f"\nDeleting {len(to_delete)} files from changrun-shared...", file=sys.stderr)
        deleted = 0
        freed = 0
        for fname, size, path in to_delete:
            try:
                path.unlink()
                deleted += 1
                freed += size
            except Exception as e:
                print(f"ERROR deleting {path}: {e}", file=sys.stderr)

        print(f"\n✓ Deleted {deleted} files, freed {freed / (1024**3):.2f} GB", file=sys.stderr)
    else:
        print("\nNo duplicates found (no same-size files in both dirs).", file=sys.stderr)
        sys.exit(0)

if __name__ == "__main__":
    main()
