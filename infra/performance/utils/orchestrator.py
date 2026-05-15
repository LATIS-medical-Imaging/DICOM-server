"""Python orchestrator for performance benchmarks.

Usage:
    python orchestrator.py --test load --profile cpu
    python orchestrator.py --test stress --profile gpu
    python orchestrator.py --test all --profile cpu
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PERF_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = PERF_DIR / "docker-compose.perf.yml"
ENV_FILE = PERF_DIR / ".env.perf"
SCRIPTS_DIR = PERF_DIR / "scripts"
RESULTS_DIR = PERF_DIR / "results"
REPORTS_DIR = PERF_DIR / "reports"

TEST_SCRIPTS = {
    "load": "run_load_test.sh",
    "stress": "run_stress_test.sh",
    "soak": "run_soak_test.sh",
    "spike": "run_spike_test.sh",
    "gpu": "run_gpu_benchmark.sh",
}


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def run_cmd(cmd: list[str], check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run a shell command, streaming output."""
    log(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, timeout=timeout)


def compose(*args: str) -> list[str]:
    """Build a docker compose command."""
    return [
        "docker", "compose",
        "-f", str(COMPOSE_FILE),
        "--env-file", str(ENV_FILE),
        *args,
    ]


def wait_for_api(url: str = "http://localhost:8000", max_retries: int = 60) -> bool:
    """Poll the API healthcheck endpoint."""
    import urllib.request
    import urllib.error

    log(f"  Waiting for API at {url}/api/v1/health/live ...")
    for i in range(1, max_retries + 1):
        try:
            urllib.request.urlopen(f"{url}/api/v1/health/live", timeout=5)
            log(f"  API ready (attempt {i})")
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(3)
    log("  ERROR: API did not become ready")
    return False


def start_stack(profile: str) -> None:
    """Start the performance stack with the given profile."""
    log(f"Starting stack (profile: {profile})...")
    run_cmd(compose("--profile", profile, "up", "-d", "--build"), check=False)


def stop_stack(profile: str) -> None:
    """Stop the performance stack."""
    log("Stopping stack...")
    run_cmd(compose("--profile", profile, "down", "-v"), check=False)


def run_test(test_type: str, profile: str) -> bool:
    """Run a single test type."""
    if test_type == "gpu":
        script = SCRIPTS_DIR / TEST_SCRIPTS["gpu"]
        run_cmd(["bash", str(script)], check=False)
        return True

    script = SCRIPTS_DIR / TEST_SCRIPTS.get(test_type, "")
    if not script.exists():
        log(f"Unknown test type: {test_type}")
        return False

    run_cmd(["bash", str(script), profile], check=False)
    return True


def generate_reports(test_type: str, profile: str) -> None:
    """Generate reports for a completed test."""
    from report_generator import generate_report, save_report

    results_subdir = RESULTS_DIR / test_type
    if not results_subdir.exists():
        return

    report = generate_report(results_subdir, test_type, profile)
    for fmt in ("json", "csv", "markdown"):
        try:
            out = save_report(report, REPORTS_DIR, fmt)
            log(f"  Report: {out}")
        except Exception as e:
            log(f"  Report generation failed ({fmt}): {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DICOM Server Performance Orchestrator")
    parser.add_argument(
        "--test",
        choices=["load", "stress", "soak", "spike", "gpu", "all"],
        default="load",
        help="Test type to run",
    )
    parser.add_argument(
        "--profile",
        choices=["cpu", "gpu"],
        default="cpu",
        help="Docker compose profile",
    )
    parser.add_argument(
        "--no-teardown",
        action="store_true",
        help="Keep stack running after tests",
    )
    parser.add_argument(
        "--reports-only",
        action="store_true",
        help="Only generate reports from existing results",
    )
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.reports_only:
        tests = list(TEST_SCRIPTS.keys()) if args.test == "all" else [args.test]
        for t in tests:
            generate_reports(t, args.profile)
        return

    log("=== DICOM Server Performance Orchestrator ===")
    log(f"Test: {args.test} | Profile: {args.profile}")

    tests = list(TEST_SCRIPTS.keys()) if args.test == "all" else [args.test]

    for test_type in tests:
        log(f"\n{'=' * 50}")
        log(f"Running: {test_type}")
        log(f"{'=' * 50}")

        success = run_test(test_type, args.profile)
        if success:
            try:
                generate_reports(test_type, args.profile)
            except Exception as e:
                log(f"Report generation error: {e}")

    if not args.no_teardown:
        stop_stack(args.profile)

    log("\n=== All tests complete ===")
    log(f"Results: {RESULTS_DIR}")
    log(f"Reports: {REPORTS_DIR}")


if __name__ == "__main__":
    main()