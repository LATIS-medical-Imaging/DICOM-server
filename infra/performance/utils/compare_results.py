"""Compare CPU vs GPU benchmark results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def find_latest_summary(directory: Path) -> dict | None:
    """Find and parse the latest k6 summary JSON in a directory."""
    summaries = sorted(directory.glob("k6_summary_*.json"))
    if not summaries:
        return None
    with open(summaries[-1]) as f:
        return json.load(f)


def extract_key_metrics(summary: dict) -> dict:
    """Extract key metrics from a k6 summary."""
    metrics = summary.get("metrics", {})

    http_duration = metrics.get("http_req_duration", {}).get("values", {})
    http_reqs = metrics.get("http_reqs", {}).get("values", {})
    error_rate = metrics.get("error_rate", {}).get("values", {})

    return {
        "avg_latency_ms": http_duration.get("avg", 0),
        "p50_latency_ms": http_duration.get("med", 0),
        "p95_latency_ms": http_duration.get("p(95)", 0),
        "p99_latency_ms": http_duration.get("p(99)", 0),
        "max_latency_ms": http_duration.get("max", 0),
        "throughput_rps": http_reqs.get("rate", 0),
        "total_requests": int(http_reqs.get("count", 0)),
        "error_rate": error_rate.get("rate", 0),
    }


def compare(cpu_dir: Path, gpu_dir: Path) -> dict:
    """Generate a comparison report between CPU and GPU results."""
    cpu_summary = find_latest_summary(cpu_dir)
    gpu_summary = find_latest_summary(gpu_dir)

    result: dict = {
        "cpu": None,
        "gpu": None,
        "comparison": None,
    }

    if cpu_summary:
        result["cpu"] = extract_key_metrics(cpu_summary)

    if gpu_summary:
        result["gpu"] = extract_key_metrics(gpu_summary)

    if result["cpu"] and result["gpu"]:
        cpu = result["cpu"]
        gpu = result["gpu"]
        comparison: dict = {}

        # Latency improvement (negative = GPU is faster)
        if cpu["avg_latency_ms"] > 0:
            comparison["avg_latency_change_pct"] = (
                (gpu["avg_latency_ms"] - cpu["avg_latency_ms"]) / cpu["avg_latency_ms"]
            ) * 100
        if cpu["p95_latency_ms"] > 0:
            comparison["p95_latency_change_pct"] = (
                (gpu["p95_latency_ms"] - cpu["p95_latency_ms"]) / cpu["p95_latency_ms"]
            ) * 100

        # Throughput improvement (positive = GPU is faster)
        if cpu["throughput_rps"] > 0:
            comparison["throughput_change_pct"] = (
                (gpu["throughput_rps"] - cpu["throughput_rps"]) / cpu["throughput_rps"]
            ) * 100

        # Speedup factor
        if gpu["avg_latency_ms"] > 0:
            comparison["latency_speedup"] = cpu["avg_latency_ms"] / gpu["avg_latency_ms"]
        if cpu["throughput_rps"] > 0 and gpu["throughput_rps"] > 0:
            comparison["throughput_speedup"] = gpu["throughput_rps"] / cpu["throughput_rps"]

        result["comparison"] = comparison

    # Load metrics snapshots if available
    for label, directory in [("cpu", cpu_dir), ("gpu", gpu_dir)]:
        metrics_files = sorted(directory.glob("metrics_*.json"))
        if metrics_files:
            with open(metrics_files[-1]) as f:
                result[f"{label}_metrics"] = json.load(f)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare CPU vs GPU benchmark results")
    parser.add_argument("--cpu-dir", required=True, type=Path)
    parser.add_argument("--gpu-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result = compare(args.cpu_dir, args.gpu_dir)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Comparison report saved: {args.output}")

    # Print summary
    comp = result.get("comparison")
    if comp:
        print("\n--- Summary ---")
        speedup = comp.get("latency_speedup", 0)
        if speedup:
            print(f"  Latency speedup (GPU vs CPU): {speedup:.2f}x")
        tp = comp.get("throughput_change_pct", 0)
        if tp:
            direction = "faster" if tp > 0 else "slower"
            print(f"  Throughput change: {abs(tp):.1f}% {direction} with GPU")
    else:
        print("\nInsufficient data for comparison.")


if __name__ == "__main__":
    main()