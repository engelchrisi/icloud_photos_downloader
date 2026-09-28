#!/bin/bash
#
# Clean up empty log files and empty month folders from iCloud imports.
# Empty = 0 files downloaded for that month.
#
# Usage: cleanup_empty.sh /mnt/media/icloud/christof
#        cleanup_empty.sh /mnt/media/icloud/changrun
#

set -uo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <icloud-folder>" >&2
    echo "  e.g. $0 /mnt/media/icloud/christof" >&2
    exit 1
fi

root="$1"

if [ ! -d "$root" ]; then
    echo "ERROR: $root does not exist" >&2
    exit 1
fi

echo "=== Cleaning up empty logs and folders in $root ==="

# Find log files with 0 downloads and delete them
echo "Finding empty log files (0 downloads)..."
empty_logs=0
find "$root" -name "*.log" -type f | while read log; do
    # Check if log contains "downloaded in this run: 0"
    if grep -q "downloaded in this run: 0" "$log" 2>/dev/null; then
        echo "  Deleting: $(basename "$log")"
        rm "$log"
        ((empty_logs++))
    fi
done

# Find and delete empty month folders (no files except .log)
echo "Finding and deleting empty month folders..."
empty_dirs=0
find "$root" -type d -name "[0-9][0-9][0-9][0-9]-[0-9][0-9]" | while read dir; do
    # Count non-log files in the directory
    file_count=$(find "$dir" -type f ! -name "*.log" 2>/dev/null | wc -l)
    if [ "$file_count" -eq 0 ]; then
        echo "  Deleting: $dir"
        rmdir "$dir" 2>/dev/null || true
        ((empty_dirs++))
    fi
done

# Find and delete empty year folders (no subdirectories)
echo "Finding and deleting empty year folders..."
empty_years=0
find "$root" -mindepth 1 -maxdepth 1 -type d -name "[0-9][0-9][0-9][0-9]" | while read year_dir; do
    dir_count=$(find "$year_dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
    if [ "$dir_count" -eq 0 ]; then
        # Only delete if year folder has no files either
        file_count=$(find "$year_dir" -type f 2>/dev/null | wc -l)
        if [ "$file_count" -eq 0 ]; then
            echo "  Deleting: $year_dir"
            rmdir "$year_dir" 2>/dev/null || true
            ((empty_years++))
        fi
    fi
done

echo "=== Cleanup complete ==="
echo "Media files remaining in $root: $(find "$root" -type f ! -name "*.log" | wc -l)"
