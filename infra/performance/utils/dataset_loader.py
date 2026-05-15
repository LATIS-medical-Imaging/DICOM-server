"""Dataset discovery and sampling utilities for performance tests."""

from __future__ import annotations

import random
from pathlib import Path


def discover_dicoms(dataset_path: str | Path, max_files: int = 0) -> list[Path]:
    """Recursively find .dcm files in a dataset directory.

    Args:
        dataset_path: Root directory to search.
        max_files: Maximum files to return (0 = unlimited).

    Returns:
        Sorted list of Path objects to .dcm files.
    """
    root = Path(dataset_path)
    if not root.exists():
        return []

    files = sorted(root.rglob("*.dcm"))
    if max_files > 0:
        files = files[:max_files]
    return files


def sample_dicoms(
    dataset_path: str | Path,
    count: int = 10,
    seed: int | None = None,
) -> list[Path]:
    """Randomly sample .dcm files from a dataset.

    Args:
        dataset_path: Root directory to search.
        count: Number of files to sample.
        seed: Random seed for reproducibility.

    Returns:
        List of sampled Path objects.
    """
    all_files = discover_dicoms(dataset_path)
    if not all_files:
        return []

    if seed is not None:
        random.seed(seed)

    return random.sample(all_files, min(count, len(all_files)))


def dataset_stats(dataset_path: str | Path) -> dict:
    """Get basic statistics about the dataset."""
    files = discover_dicoms(dataset_path)
    if not files:
        return {"total_files": 0, "total_size_bytes": 0}

    sizes = [f.stat().st_size for f in files]
    return {
        "total_files": len(files),
        "total_size_bytes": sum(sizes),
        "avg_size_bytes": sum(sizes) // len(sizes),
        "min_size_bytes": min(sizes),
        "max_size_bytes": max(sizes),
        "total_size_gb": round(sum(sizes) / (1024**3), 2),
    }


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "/home/{$HOME}/PycharmProjects/micro-informed-vit/data"
    stats = dataset_stats(path)
    print(json.dumps(stats, indent=2))