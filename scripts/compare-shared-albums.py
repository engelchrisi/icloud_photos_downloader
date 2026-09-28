#!/usr/bin/env python3
"""
Compare shared albums between changrun-shared and christof-shared.
Verifies that albums with the same name are identical (same files, same counts).

Usage:
  python check_shared_albums.py /mnt/media/icloud/changrun-shared /mnt/media/icloud/christof-shared
"""

import json
import sys
import os
from pathlib import Path
from collections import defaultdict


def scan_albums(root: str) -> dict[str, list[Path]]:
    """Scan a root directory and collect files by album (top-level folder)."""
    albums = defaultdict(list)
    root_path = Path(root)

    if not root_path.exists():
        print(f"ERROR: {root} does not exist", file=sys.stderr)
        return albums

    for album_dir in sorted(root_path.iterdir()):
        if not album_dir.is_dir():
            continue

        for year_dir in album_dir.iterdir():
            if not year_dir.is_dir():
                continue

            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue

                for file_path in month_dir.iterdir():
                    if file_path.is_file():
                        albums[album_dir.name].append(file_path)

    return albums


def compare_albums(changrun_albums: dict, christof_albums: dict) -> dict:
    """Compare albums by name and report differences."""
    results = {
        "identical": [],
        "count_mismatch": [],
        "file_mismatch": [],
        "only_in_changrun": [],
        "only_in_christof": [],
    }

    all_names = set(changrun_albums.keys()) | set(christof_albums.keys())

    for name in sorted(all_names):
        changrun_files = set(f.name for f in changrun_albums.get(name, []))
        christof_files = set(f.name for f in christof_albums.get(name, []))
        changrun_count = len(changrun_albums.get(name, []))
        christof_count = len(christof_albums.get(name, []))

        if name not in changrun_albums:
            results["only_in_christof"].append({
                "album": name,
                "christof_count": christof_count,
            })
        elif name not in christof_albums:
            results["only_in_changrun"].append({
                "album": name,
                "changrun_count": changrun_count,
            })
        elif changrun_count != christof_count:
            missing_in_christof = changrun_files - christof_files
            missing_in_changrun = christof_files - changrun_files
            results["count_mismatch"].append({
                "album": name,
                "changrun_count": changrun_count,
                "christof_count": christof_count,
                "missing_in_christof": len(missing_in_christof),
                "missing_in_changrun": len(missing_in_changrun),
            })
        elif changrun_files != christof_files:
            results["file_mismatch"].append({
                "album": name,
                "count": changrun_count,
                "missing_in_christof": list(changrun_files - christof_files)[:5],
                "missing_in_changrun": list(christof_files - changrun_files)[:5],
            })
        else:
            results["identical"].append({
                "album": name,
                "count": changrun_count,
            })

    return results


def main():
    if len(sys.argv) != 3:
        print("Usage: check_shared_albums.py <changrun-shared> <christof-shared>", file=sys.stderr)
        sys.exit(1)

    changrun_root = sys.argv[1]
    christof_root = sys.argv[2]

    print("Scanning changrun-shared...", file=sys.stderr)
    changrun_albums = scan_albums(changrun_root)
    print(f"  Found {len(changrun_albums)} albums", file=sys.stderr)

    print("Scanning christof-shared...", file=sys.stderr)
    christof_albums = scan_albums(christof_root)
    print(f"  Found {len(christof_albums)} albums", file=sys.stderr)

    print("Comparing...", file=sys.stderr)
    results = compare_albums(changrun_albums, christof_albums)

    # Print summary
    print("\n=== Comparison Results ===\n", file=sys.stderr)

    if results["identical"]:
        print(f"✓ Identical ({len(results['identical'])} albums):")
        for item in results["identical"]:
            print(f"  {item['album']}: {item['count']} files")

    if results["only_in_changrun"]:
        print(f"\n⚠ Only in changrun-shared ({len(results['only_in_changrun'])} albums):")
        for item in results["only_in_changrun"]:
            print(f"  {item['album']}: {item['changrun_count']} files")

    if results["only_in_christof"]:
        print(f"\n⚠ Only in christof-shared ({len(results['only_in_christof'])} albums):")
        for item in results["only_in_christof"]:
            print(f"  {item['album']}: {item['christof_count']} files")

    if results["count_mismatch"]:
        print(f"\n✗ Count mismatch ({len(results['count_mismatch'])} albums):")
        for item in results["count_mismatch"]:
            print(f"  {item['album']}: changrun={item['changrun_count']}, christof={item['christof_count']}")

    if results["file_mismatch"]:
        print(f"\n✗ File mismatch ({len(results['file_mismatch'])} albums):")
        for item in results["file_mismatch"]:
            print(f"  {item['album']}: {item['count']} files, but missing {len(item['missing_in_christof'])} in christof and {len(item['missing_in_changrun'])} in changrun")

    # Output JSON for scripting
    print("\n=== JSON Output ===\n")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    # Exit code: 0 if all identical, 1 otherwise
    has_issues = any([
        results["only_in_changrun"],
        results["only_in_christof"],
        results["count_mismatch"],
        results["file_mismatch"],
    ])
    sys.exit(1 if has_issues else 0)


if __name__ == "__main__":
    main()
