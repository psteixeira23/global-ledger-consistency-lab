#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from shared.contracts.models import IncidentSeverity

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "payments-api"
WORKER_DIR = ROOT / "services" / "ledger-worker"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class RunResult:
    statuses: list[int]
    latencies_ms: list[float]
    stats: dict[str, int]
    metrics: dict[str, float]
    elapsed_seconds: float
    timeline: list[dict[str, str]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run consistency experiment")
    parser.add_argument("--mode", choices=["strong", "hybrid", "eventual"], required=True)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--profile", choices=["none", "mild", "harsh"], default="none")
    parser.add_argument("--scenario", choices=["normal", "insufficient_funds"], default="normal")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def run_migrations(env: dict[str, str]) -> None:
    subprocess.run(["bash", "scripts/migrate.sh"], cwd=ROOT, env=env, check=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_timeline_event(
    timeline: list[dict[str, str]],
    event: str,
    details: str,
    severity: IncidentSeverity = IncidentSeverity.INFO,
) -> None:
    timeline.append(
        {
            "timestamp": utc_now_iso(),
            "event": event,
            "severity": severity.value,
            "details": details,
        }
    )


def start_processes(env: dict[str, str]) -> tuple[subprocess.Popen[bytes], subprocess.Popen[bytes]]:
    api = subprocess.Popen(
        ["poetry", "run", "uvicorn", "payments_api.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    worker = subprocess.Popen(
        ["poetry", "run", "python", "-m", "ledger_worker.main"],
        cwd=WORKER_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return api, worker


async def wait_for_health(base_url: str, timeout_seconds: float = 20.0) -> None:
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() - started < timeout_seconds:
            try:
                response = await client.get(f"{base_url}/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    raise RuntimeError("payments-api did not become healthy in time")


def payload_for(index: int, mode: str, scenario: str, run_label: str) -> dict[str, Any]:
    source = "acc-001" if index % 2 == 0 else "acc-003"
    destination = "acc-002" if index % 2 == 0 else "acc-004"
    if scenario == "insufficient_funds" and mode == "eventual":
        amount = 1_250_000
    else:
        amount = 100
    return {
        "idempotency_key": f"exp-{run_label}-{mode}-{index:06d}",
        "source_account_id": source,
        "destination_account_id": destination,
        "amount_cents": amount,
        "method": "pix",
    }


async def run_load(
    base_url: str,
    requests: int,
    concurrency: int,
    mode: str,
    scenario: str,
    run_label: str,
) -> tuple[list[int], list[float]]:
    statuses: list[int] = []
    latencies_ms: list[float] = []
    semaphore = asyncio.Semaphore(concurrency)

    async def fire(index: int, client: httpx.AsyncClient) -> None:
        payload = payload_for(index, mode, scenario, run_label)
        async with semaphore:
            started = time.perf_counter()
            response = await client.post(f"{base_url}/v1/payments", json=payload)
            elapsed = (time.perf_counter() - started) * 1000.0
            statuses.append(response.status_code)
            latencies_ms.append(elapsed)

    async with httpx.AsyncClient(timeout=10.0) as client:
        await asyncio.gather(*(fire(i, client) for i in range(requests)))
    return statuses, latencies_ms


async def wait_outbox_drained(base_url: str, timeout_seconds: float = 60.0) -> dict[str, int]:
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() - started < timeout_seconds:
            response = await client.get(f"{base_url}/internal/stats")
            response.raise_for_status()
            stats = response.json()
            if int(stats["outbox_pending"]) == 0:
                return {
                    "completed": int(stats["completed"]),
                    "rejected": int(stats["rejected"]),
                    "outbox_pending": int(stats["outbox_pending"]),
                    "outbox_dead": int(stats["outbox_dead"]),
                    "ledger_imbalance": int(stats["ledger_imbalance"]),
                    "negative_balance_detected": int(stats["negative_balance_detected"]),
                }
            await asyncio.sleep(0.5)
    raise RuntimeError("outbox was not drained in time")


def metric_value(metrics_text: str, metric_name: str) -> float:
    pattern = re.compile(rf"^{re.escape(metric_name)}\s+([-+]?[0-9]*\.?[0-9]+)$")
    for line in metrics_text.splitlines():
        matched = pattern.match(line.strip())
        if matched:
            return float(matched.group(1))
    return 0.0


async def collect_metrics(base_url: str) -> dict[str, float]:
    worker_port = int(os.getenv("LEDGER_WORKER_METRICS_PORT", "8001"))
    worker_metrics_url = f"http://127.0.0.1:{worker_port}/metrics"
    async with httpx.AsyncClient(timeout=2.0) as client:
        api_metrics_response = await client.get(f"{base_url}/metrics")
        worker_metrics_response = await client.get(worker_metrics_url)
    api_metrics = api_metrics_response.text
    worker_metrics = worker_metrics_response.text
    return {
        "payments_received_total": metric_value(api_metrics, "payments_received_total"),
        "payments_processed_total_api": metric_value(api_metrics, "payments_processed_total"),
        "idempotency_replay_total": metric_value(api_metrics, "idempotency_replay_total"),
        "optimistic_lock_conflict_total_api": metric_value(api_metrics, "optimistic_lock_conflict_total"),
        "payments_processed_total_worker": metric_value(worker_metrics, "payments_processed_total"),
        "outbox_retry_total": metric_value(worker_metrics, "outbox_retry_total"),
        "ledger_imbalance_total_metric": metric_value(worker_metrics, "ledger_imbalance_total"),
        "negative_balance_detected_total_metric": metric_value(worker_metrics, "negative_balance_detected_total"),
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    position = (pct / 100.0) * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def terminate(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


async def run_single_experiment(args: argparse.Namespace, env: dict[str, str], run_label: str) -> RunResult:
    timeline: list[dict[str, str]] = []
    add_timeline_event(
        timeline,
        "run_started",
        f"label={run_label} mode={args.mode} profile={args.profile} requests={args.requests} concurrency={args.concurrency}",
    )
    run_migrations(env)
    add_timeline_event(timeline, "migration_completed", "schema recreated and seed accounts reset")
    api_process, worker_process = start_processes(env)
    add_timeline_event(
        timeline,
        "services_started",
        f"payments_api_pid={api_process.pid} ledger_worker_pid={worker_process.pid}",
    )
    started = time.perf_counter()
    try:
        await wait_for_health(args.base_url)
        add_timeline_event(timeline, "api_healthy", f"base_url={args.base_url}")
        add_timeline_event(timeline, "load_started", "sending payment requests")
        statuses, latencies_ms = await run_load(
            args.base_url,
            args.requests,
            args.concurrency,
            args.mode,
            args.scenario,
            run_label=run_label,
        )
        non_2xx = len([status for status in statuses if status < 200 or status >= 300])
        add_timeline_event(
            timeline,
            "load_completed",
            f"sent={len(statuses)} non_2xx={non_2xx}",
        )
        stats = await wait_outbox_drained(args.base_url)
        add_timeline_event(
            timeline,
            "outbox_drained",
            f"pending={stats['outbox_pending']} dead={stats['outbox_dead']} completed={stats['completed']} rejected={stats['rejected']}",
        )
        metrics = await collect_metrics(args.base_url)
        retries = int(metrics["outbox_retry_total"])
        if retries > 0:
            add_timeline_event(
                timeline,
                "incident_dependency_instability",
                f"retry_events={retries} under profile={args.profile}",
                severity=IncidentSeverity.P1,
            )
        if stats["rejected"] > 0:
            add_timeline_event(
                timeline,
                "incident_business_rejections",
                f"rejected={stats['rejected']} scenario={args.scenario}",
                severity=IncidentSeverity.P2,
            )
        if stats["ledger_imbalance"] != 0:
            add_timeline_event(
                timeline,
                "incident_invariant_imbalance",
                f"ledger_imbalance={stats['ledger_imbalance']}",
                severity=IncidentSeverity.P1,
            )
        if stats["negative_balance_detected"] != 0:
            add_timeline_event(
                timeline,
                "incident_invariant_negative_balance",
                f"negative_balance_detected={stats['negative_balance_detected']}",
                severity=IncidentSeverity.P1,
            )
    finally:
        terminate(api_process)
        terminate(worker_process)
        add_timeline_event(timeline, "services_terminated", "payments-api and ledger-worker stopped")
    elapsed_seconds = time.perf_counter() - started
    return RunResult(
        statuses=statuses,
        latencies_ms=latencies_ms,
        stats=stats,
        metrics=metrics,
        elapsed_seconds=elapsed_seconds,
        timeline=timeline,
    )


def aggregate_results(args: argparse.Namespace, runs: list[RunResult]) -> dict[str, Any]:
    if not runs:
        raise RuntimeError("no measured runs collected")
    all_statuses = [status for item in runs for status in item.statuses]
    all_latencies = [latency for item in runs for latency in item.latencies_ms]
    requests_total = args.requests * len(runs)
    completed_total = sum(item.stats["completed"] for item in runs)
    rejected_total = sum(item.stats["rejected"] for item in runs)
    outbox_pending_total = sum(item.stats["outbox_pending"] for item in runs)
    outbox_dead_total = sum(item.stats["outbox_dead"] for item in runs)
    ledger_imbalance_total = sum(item.stats["ledger_imbalance"] for item in runs)
    negative_balance_total = sum(item.stats["negative_balance_detected"] for item in runs)
    http_non_2xx = len([status for status in all_statuses if status < 200 or status >= 300])
    total_elapsed = sum(item.elapsed_seconds for item in runs)
    throughput_rps = requests_total / total_elapsed if total_elapsed > 0 else 0.0
    merged_timeline = [event for item in runs for event in item.timeline]

    summary: dict[str, Any] = {
        "mode": args.mode,
        "requests": requests_total,
        "requests_per_run": args.requests,
        "concurrency": args.concurrency,
        "profile": args.profile,
        "scenario": args.scenario,
        "runs": len(runs),
        "warmup_runs": args.warmup_runs,
        "completed": completed_total,
        "rejected": rejected_total,
        "outbox_pending": outbox_pending_total,
        "outbox_dead": outbox_dead_total,
        "ledger_imbalance": ledger_imbalance_total,
        "negative_balance_detected": negative_balance_total,
        "avg_latency_ms": round(statistics.mean(all_latencies), 2) if all_latencies else 0.0,
        "p50_latency_ms": round(percentile(all_latencies, 50), 2),
        "p95_latency_ms": round(percentile(all_latencies, 95), 2),
        "p99_latency_ms": round(percentile(all_latencies, 99), 2),
        "p999_latency_ms": round(percentile(all_latencies, 99.9), 2),
        "throughput_rps": round(throughput_rps, 2),
        "http_non_2xx": http_non_2xx,
        "payments_received_total": float(sum(item.metrics["payments_received_total"] for item in runs)),
        "payments_processed_total_api": float(sum(item.metrics["payments_processed_total_api"] for item in runs)),
        "idempotency_replay_total": float(sum(item.metrics["idempotency_replay_total"] for item in runs)),
        "optimistic_lock_conflict_total_api": float(
            sum(item.metrics["optimistic_lock_conflict_total_api"] for item in runs)
        ),
        "payments_processed_total_worker": float(
            sum(item.metrics["payments_processed_total_worker"] for item in runs)
        ),
        "outbox_retry_total": float(sum(item.metrics["outbox_retry_total"] for item in runs)),
        "ledger_imbalance_total_metric": float(
            sum(item.metrics["ledger_imbalance_total_metric"] for item in runs)
        ),
        "negative_balance_detected_total_metric": float(
            sum(item.metrics["negative_balance_detected_total_metric"] for item in runs)
        ),
        "timeline": merged_timeline,
    }
    return summary


def print_human_summary(summary: dict[str, Any]) -> None:
    print(f"Mode: {summary['mode']}")
    print(f"Runs: {summary['runs']} (warmup excluded: {summary['warmup_runs']})")
    print(f"Requests: {summary['requests']} (per run: {summary['requests_per_run']})")
    print(f"Completed: {summary['completed']}")
    print(f"Rejected: {summary['rejected']}")
    print(f"Outbox Dead: {summary['outbox_dead']}")
    print(f"Ledger Imbalance: {summary['ledger_imbalance']}")
    print(f"P50 Latency: {summary['p50_latency_ms']:.2f} ms")
    print(f"P95 Latency: {summary['p95_latency_ms']:.2f} ms")
    print(f"P99 Latency: {summary['p99_latency_ms']:.2f} ms")
    print(f"P999 Latency: {summary['p999_latency_ms']:.2f} ms")
    print(f"Throughput: {summary['throughput_rps']:.2f} req/s")


async def async_main() -> int:
    args = parse_args()
    env = os.environ.copy()
    env["CONSISTENCY_MODE"] = args.mode
    env["FAIL_PROFILE"] = args.profile
    env.setdefault("EXPERIMENT_SEED", "42")
    env.setdefault("DATABASE_URL", "postgresql+psycopg://ledger:ledger@localhost:5432/ledgerlab")
    env.setdefault("PAYMENTS_API_OTEL_SERVICE_NAME", "payments-api")
    env.setdefault("LEDGER_WORKER_OTEL_SERVICE_NAME", "ledger-worker")
    env.setdefault("MIGRATE_RECREATE_SCHEMA", "1")
    if env["DATABASE_URL"].startswith("sqlite"):
        print("warning: sqlite is not recommended for benchmark conclusions", file=sys.stderr)

    measured_runs: list[RunResult] = []
    total_runs = args.warmup_runs + args.runs
    for index in range(total_runs):
        run_result = await run_single_experiment(args, env, run_label=f"run-{index:03d}")
        if index >= args.warmup_runs:
            measured_runs.append(run_result)

    summary = aggregate_results(args, measured_runs)
    if args.json_output:
        print(json.dumps(summary, sort_keys=True))
    else:
        print_human_summary(summary)
    return 0


def main() -> None:
    code = asyncio.run(async_main())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
