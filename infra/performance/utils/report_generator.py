"""Generate benchmark reports from k6 and Locust outputs."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_k6_summary(summary_path: Path) -> dict:
    """Parse a k6 --summary-export JSON file into a normalized report."""
    with open(summary_path) as f:
        raw = json.load(f)

    metrics = raw.get("metrics", {})

    def _extract_metric(name: str) -> dict:
        m = metrics.get(name, {})
        values = m.get("values", {})
        return {
            "avg": values.get("avg", 0),
            "min": values.get("min", 0),
            "max": values.get("max", 0),
            "med": values.get("med", 0),
            "p90": values.get("p(90)", 0),
            "p95": values.get("p(95)", 0),
            "p99": values.get("p(99)", 0),
        }

    http_duration = _extract_metric("http_req_duration")
    http_reqs = metrics.get("http_reqs", {}).get("values", {})
    error_rate = metrics.get("error_rate", {}).get("values", {})

    return {
        "source": "k6",
        "latency_ms": http_duration,
        "throughput_rps": http_reqs.get("rate", 0),
        "total_requests": int(http_reqs.get("count", 0)),
        "error_rate": error_rate.get("rate", 0),
        "iterations": metrics.get("iterations", {}).get("values", {}).get("count", 0),
    }


def parse_locust_csv(stats_csv_path: Path) -> dict:
    """Parse Locust stats CSV into a normalized report."""
    rows = []
    with open(stats_csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return {"source": "locust", "endpoints": []}

    # The last row with Name="Aggregated" is the summary
    aggregated = None
    endpoints = []
    for row in rows:
        if row.get("Name") == "Aggregated":
            aggregated = row
        else:
            endpoints.append({
                "name": row.get("Name", ""),
                "requests": int(row.get("Request Count", 0)),
                "failures": int(row.get("Failure Count", 0)),
                "avg_ms": float(row.get("Average Response Time", 0)),
                "p50_ms": float(row.get("50%", 0)),
                "p95_ms": float(row.get("95%", 0)),
                "p99_ms": float(row.get("99%", 0)),
                "rps": float(row.get("Requests/s", 0)),
            })

    result: dict = {"source": "locust", "endpoints": endpoints}
    if aggregated:
        result["aggregated"] = {
            "total_requests": int(aggregated.get("Request Count", 0)),
            "total_failures": int(aggregated.get("Failure Count", 0)),
            "avg_ms": float(aggregated.get("Average Response Time", 0)),
            "p50_ms": float(aggregated.get("50%", 0)),
            "p95_ms": float(aggregated.get("95%", 0)),
            "p99_ms": float(aggregated.get("99%", 0)),
            "rps": float(aggregated.get("Requests/s", 0)),
        }

    return result


def generate_report(
    results_dir: Path,
    test_type: str,
    profile: str,
    timestamp: str | None = None,
) -> dict:
    """Generate a combined report from all available outputs in a results directory."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    report: dict = {
        "timestamp": timestamp,
        "test_type": test_type,
        "profile": profile,
        "k6": None,
        "locust": None,
        "metrics": None,
    }

    # Find k6 summary
    k6_summaries = sorted(results_dir.glob("k6_summary_*.json"))
    if k6_summaries:
        report["k6"] = parse_k6_summary(k6_summaries[-1])

    # Find Locust CSV
    locust_csvs = sorted(results_dir.glob("locust_stats.csv"))
    if not locust_csvs:
        locust_csvs = sorted(results_dir.parent.glob("locust_stats.csv"))
    if locust_csvs:
        report["locust"] = parse_locust_csv(locust_csvs[-1])

    # Find metrics snapshot
    metrics_files = sorted(results_dir.glob("metrics_*.json"))
    if metrics_files:
        with open(metrics_files[-1]) as f:
            report["metrics"] = json.load(f)

    return report


def save_report(report: dict, output_dir: Path, fmt: str = "json") -> Path:
    """Save report in the requested format."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = report.get("timestamp", "unknown")
    test_type = report.get("test_type", "unknown")
    profile = report.get("profile", "unknown")
    base_name = f"report_{test_type}_{profile}_{ts}"

    if fmt == "json":
        out_path = output_dir / f"{base_name}.json"
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2)
        return out_path

    if fmt == "csv":
        out_path = output_dir / f"{base_name}.csv"
        flat: dict = {
            "timestamp": ts,
            "test_type": test_type,
            "profile": profile,
        }
        k6 = report.get("k6") or {}
        latency = k6.get("latency_ms", {})
        flat["k6_p50_ms"] = latency.get("med", "")
        flat["k6_p95_ms"] = latency.get("p95", "")
        flat["k6_p99_ms"] = latency.get("p99", "")
        flat["k6_rps"] = k6.get("throughput_rps", "")
        flat["k6_error_rate"] = k6.get("error_rate", "")
        flat["k6_total_requests"] = k6.get("total_requests", "")

        locust = report.get("locust") or {}
        agg = locust.get("aggregated", {})
        flat["locust_p50_ms"] = agg.get("p50_ms", "")
        flat["locust_p95_ms"] = agg.get("p95_ms", "")
        flat["locust_p99_ms"] = agg.get("p99_ms", "")
        flat["locust_rps"] = agg.get("rps", "")

        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=flat.keys())
            writer.writeheader()
            writer.writerow(flat)
        return out_path

    if fmt == "markdown":
        out_path = output_dir / f"{base_name}.md"
        lines = [
            f"# Performance Report: {test_type} ({profile})",
            f"**Timestamp:** {ts}",
            "",
        ]
        k6 = report.get("k6") or {}
        latency = k6.get("latency_ms", {})
        if k6:
            lines += [
                "## k6 Results",
                f"- Total requests: {k6.get('total_requests', 'N/A')}",
                f"- Throughput: {k6.get('throughput_rps', 'N/A'):.2f} req/s",
                f"- Error rate: {k6.get('error_rate', 0):.2%}",
                f"- p50 latency: {latency.get('med', 'N/A'):.1f} ms",
                f"- p95 latency: {latency.get('p95', 'N/A'):.1f} ms",
                f"- p99 latency: {latency.get('p99', 'N/A'):.1f} ms",
                "",
            ]
        locust = report.get("locust") or {}
        agg = locust.get("aggregated", {})
        if agg:
            lines += [
                "## Locust Results",
                f"- Total requests: {agg.get('total_requests', 'N/A')}",
                f"- Throughput: {agg.get('rps', 'N/A'):.2f} req/s",
                f"- p50 latency: {agg.get('p50_ms', 'N/A'):.1f} ms",
                f"- p95 latency: {agg.get('p95_ms', 'N/A'):.1f} ms",
                f"- p99 latency: {agg.get('p99_ms', 'N/A'):.1f} ms",
                "",
            ]
        with open(out_path, "w") as f:
            f.write("\n".join(lines))
        return out_path

    raise ValueError(f"Unknown format: {fmt}")


def main() -> None:
    """CLI: python report_generator.py <results_dir> <test_type> <profile> [format]"""
    if len(sys.argv) < 4:
        print("Usage: report_generator.py <results_dir> <test_type> <profile> [json|csv|markdown]")
        sys.exit(1)

    results_dir = Path(sys.argv[1])
    test_type = sys.argv[2]
    profile = sys.argv[3]
    fmt = sys.argv[4] if len(sys.argv) > 4 else "json"

    report = generate_report(results_dir, test_type, profile)
    out_path = save_report(report, results_dir.parent / ".." / "reports", fmt)
    print(f"Report saved: {out_path}")


if __name__ == "__main__":
    main()