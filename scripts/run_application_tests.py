#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from shared.contracts.models import IncidentSeverity

ROOT = Path(__file__).resolve().parents[1]
PAYMENTS_API_DIR = ROOT / "services" / "payments-api"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    category: str
    title: str
    mode: str
    profile: str
    scenario_type: str
    description: str
    min_requests: int = 1


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    expected: str
    actual: str


@dataclass(frozen=True)
class ScenarioExecution:
    scenario: Scenario
    summary: dict[str, Any]
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class ChecklistItem:
    name: str
    passed: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run application-level evidence scenarios and generate HTML report.")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--output", default=str(ROOT / "reports" / "test-results.html"))
    return parser.parse_args()


def scenario_matrix() -> list[Scenario]:
    return [
        Scenario(
            scenario_id="success-strong",
            category="Success scenarios",
            title="Strong mode under load",
            mode="strong",
            profile="none",
            scenario_type="normal",
            description="Fully synchronous processing should complete all requests without invariant drift.",
        ),
        Scenario(
            scenario_id="success-hybrid",
            category="Success scenarios",
            title="Hybrid mode under load",
            mode="hybrid",
            profile="none",
            scenario_type="normal",
            description="Reservation + async finalization should complete all requests with drained outbox.",
        ),
        Scenario(
            scenario_id="success-eventual",
            category="Success scenarios",
            title="Eventual mode under load",
            mode="eventual",
            profile="none",
            scenario_type="normal",
            description="Fully async processing should converge with no imbalance and no pending outbox events.",
        ),
        Scenario(
            scenario_id="failure-hybrid-harsh",
            category="Failure scenarios",
            title="Hybrid mode with harsh failure profile",
            mode="hybrid",
            profile="harsh",
            scenario_type="normal",
            description="Deterministic injected faults should trigger retries while preserving invariants.",
            min_requests=120,
        ),
        Scenario(
            scenario_id="failure-eventual-harsh",
            category="Failure scenarios",
            title="Eventual mode with harsh failure profile",
            mode="eventual",
            profile="harsh",
            scenario_type="normal",
            description="Deterministic injected faults should trigger retries and still converge safely.",
            min_requests=120,
        ),
        Scenario(
            scenario_id="negative-eventual-funds",
            category="Failure scenarios",
            title="Eventual mode insufficient funds",
            mode="eventual",
            profile="none",
            scenario_type="insufficient_funds",
            description="Business negative path must produce rejections without invariant violations.",
        ),
    ]


def run_experiment(
    scenario: Scenario,
    requests: int,
    concurrency: int,
    seed: int,
    runs: int,
    warmup_runs: int,
    env: dict[str, str],
) -> dict[str, Any]:
    effective_requests = max(requests, scenario.min_requests)
    command = [
        "poetry",
        "run",
        "python",
        "../../scripts/run_experiment.py",
        "--mode",
        scenario.mode,
        "--requests",
        str(effective_requests),
        "--concurrency",
        str(concurrency),
        "--profile",
        scenario.profile,
        "--scenario",
        scenario.scenario_type,
        "--runs",
        str(runs),
        "--warmup-runs",
        str(warmup_runs),
        "--json",
    ]
    scoped_env = dict(env)
    scoped_env["EXPERIMENT_SEED"] = str(seed)
    completed = subprocess.run(
        command,
        cwd=PAYMENTS_API_DIR,
        env=scoped_env,
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_json_line(completed.stdout, scenario.mode, scenario.scenario_type)


def parse_json_line(stdout: str, mode: str, scenario_type: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("mode") == mode and payload.get("scenario") == scenario_type:
            return payload
    raise RuntimeError(f"Could not parse JSON summary for mode={mode}, scenario={scenario_type}")


def evaluate_scenario(result: dict[str, Any], scenario: Scenario) -> list[CheckResult]:
    requests = int(result["requests"])
    completed = int(result["completed"])
    rejected = int(result["rejected"])
    outbox_pending = int(result["outbox_pending"])
    outbox_dead = int(result["outbox_dead"])
    ledger_imbalance = int(result["ledger_imbalance"])
    negative_balance_detected = int(result["negative_balance_detected"])
    http_non_2xx = int(result["http_non_2xx"])
    outbox_retry_total = float(result["outbox_retry_total"])
    p95 = float(result["p95_latency_ms"])
    p99 = float(result["p99_latency_ms"])
    p999 = float(result["p999_latency_ms"])

    checks = [
        CheckResult(
            name="Ledger balanced",
            passed=ledger_imbalance == 0,
            expected="ledger_imbalance == 0",
            actual=f"ledger_imbalance = {ledger_imbalance}",
        ),
        CheckResult(
            name="No negative balances",
            passed=negative_balance_detected == 0,
            expected="negative_balance_detected == 0",
            actual=f"negative_balance_detected = {negative_balance_detected}",
        ),
        CheckResult(
            name="Outbox drained",
            passed=outbox_pending == 0,
            expected="outbox_pending == 0",
            actual=f"outbox_pending = {outbox_pending}",
        ),
        CheckResult(
            name="No dead outbox events",
            passed=outbox_dead == 0,
            expected="outbox_dead == 0",
            actual=f"outbox_dead = {outbox_dead}",
        ),
        CheckResult(
            name="Request accounting",
            passed=(completed + rejected) == requests,
            expected="completed + rejected == requests",
            actual=f"{completed} + {rejected} vs {requests}",
        ),
        CheckResult(
            name="API transport stability",
            passed=http_non_2xx == 0,
            expected="http_non_2xx == 0",
            actual=f"http_non_2xx = {http_non_2xx}",
        ),
        CheckResult(
            name="Latency percentiles monotonic",
            passed=p95 <= p99 <= p999,
            expected="p95 <= p99 <= p999",
            actual=f"p95={p95:.2f}, p99={p99:.2f}, p999={p999:.2f}",
        ),
    ]

    if scenario.category == "Success scenarios":
        checks.append(
            CheckResult(
                name="All requests completed",
                passed=completed == requests and rejected == 0,
                expected="completed == requests and rejected == 0",
                actual=f"completed={completed}, rejected={rejected}, requests={requests}",
            )
        )

    if scenario.profile == "harsh":
        checks.append(
            CheckResult(
                name="Retry evidence under harsh profile",
                passed=outbox_retry_total > 0.0,
                expected="outbox_retry_total > 0",
                actual=f"outbox_retry_total = {outbox_retry_total:.0f}",
            )
        )

    if scenario.scenario_type == "insufficient_funds":
        checks.append(
            CheckResult(
                name="Insufficient funds rejections observed",
                passed=rejected > 0,
                expected="rejected > 0",
                actual=f"rejected = {rejected}",
            )
        )

    return checks


def format_expected(checks: list[CheckResult]) -> str:
    return "<br>".join(html.escape(check.expected) for check in checks)


def format_actual(checks: list[CheckResult]) -> str:
    return "<br>".join(html.escape(check.actual) for check in checks)


def format_metrics(summary: dict[str, Any]) -> str:
    lines = [
        f"Completed: {int(summary['completed'])}",
        f"Rejected: {int(summary['rejected'])}",
        f"P95/P99/P999 (ms): {float(summary['p95_latency_ms']):.2f}/{float(summary['p99_latency_ms']):.2f}/{float(summary['p999_latency_ms']):.2f}",
        f"Throughput (req/s): {float(summary['throughput_rps']):.2f}",
        f"Outbox retries: {float(summary['outbox_retry_total']):.0f}",
        f"Outbox dead: {int(summary['outbox_dead'])}",
    ]
    return "<br>".join(html.escape(line) for line in lines)


def render_category_table(executions: list[ScenarioExecution]) -> str:
    rows: list[str] = []
    for execution in executions:
        status = "PASSED" if execution.passed else "FAILED"
        status_color = "#166534" if execution.passed else "#9f1239"
        rows.append(
            (
                f"<tr>"
                f"<td>{html.escape(execution.scenario.title)}</td>"
                f"<td>{html.escape(execution.scenario.mode)} / {html.escape(execution.scenario.profile)} / {html.escape(execution.scenario.scenario_type)}</td>"
                f"<td>{html.escape(execution.scenario.description)}</td>"
                f"<td>{format_expected(execution.checks)}</td>"
                f"<td>{format_actual(execution.checks)}</td>"
                f"<td>{format_metrics(execution.summary)}</td>"
                f"<td style='color:{status_color};font-weight:700'>{status}</td>"
                f"</tr>"
            )
        )
    return "".join(rows)


def infer_severity(event_name: str, raw_severity: object) -> IncidentSeverity:
    if isinstance(raw_severity, str):
        try:
            return IncidentSeverity(raw_severity.strip().lower())
        except ValueError:
            pass
    if event_name.startswith("incident_"):
        if "p1_" in event_name or "invariant_" in event_name:
            return IncidentSeverity.P1
        return IncidentSeverity.P2
    return IncidentSeverity.INFO


def collect_timeline_events(executions: list[ScenarioExecution], incidents_only: bool) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for execution in executions:
        scenario_id = execution.scenario.scenario_id
        scenario_title = execution.scenario.title
        timeline = execution.summary.get("timeline", [])
        if not isinstance(timeline, list):
            continue
        for item in timeline:
            if not isinstance(item, dict):
                continue
            event_name = str(item.get("event", "unknown_event"))
            if incidents_only and not event_name.startswith("incident_"):
                continue
            severity = infer_severity(event_name, item.get("severity"))
            events.append(
                {
                    "timestamp": str(item.get("timestamp", "")),
                    "scenario_id": scenario_id,
                    "scenario_title": scenario_title,
                    "event": event_name,
                    "severity": severity.value,
                    "details": str(item.get("details", "")),
                }
            )
    return sorted(events, key=lambda event: event["timestamp"])


def render_timeline_table(events: list[dict[str, str]], include_severity: bool) -> str:
    rows: list[str] = []
    for event in events:
        severity = str(event["severity"]).upper()
        severity_column = f"<td>{severity}</td>" if include_severity else ""
        rows.append(
            (
                "<tr>"
                f"<td>{html.escape(event['timestamp'])}</td>"
                f"<td>{html.escape(event['scenario_id'])}</td>"
                f"<td>{html.escape(event['event'])}</td>"
                f"{severity_column}"
                f"<td>{html.escape(event['details'])}</td>"
                "</tr>"
            )
        )
    return "".join(rows)


def render_incident_summary(events: list[dict[str, str]]) -> str:
    if not events:
        return "<tr><td colspan='3'>No incident events captured.</td></tr>"
    counts = Counter((event["event"], event["severity"]) for event in events)
    rows: list[str] = []
    for (event_name, severity), count in sorted(counts.items()):
        rows.append(
            (
                "<tr>"
                f"<td>{html.escape(event_name)}</td>"
                f"<td>{html.escape(str(severity).upper())}</td>"
                f"<td>{count}</td>"
                "</tr>"
            )
        )
    return "".join(rows)


def mode_summary(executions: list[ScenarioExecution], mode: str) -> tuple[float, float, float]:
    selected = [
        item
        for item in executions
        if item.scenario.mode == mode and item.scenario.profile == "none" and item.scenario.scenario_type == "normal"
    ]
    if not selected:
        return 0.0, 0.0, 0.0
    throughput = sum(float(item.summary["throughput_rps"]) for item in selected) / len(selected)
    p99 = sum(float(item.summary["p99_latency_ms"]) for item in selected) / len(selected)
    p999 = sum(float(item.summary["p999_latency_ms"]) for item in selected) / len(selected)
    return throughput, p99, p999


def build_checklist(executions: list[ScenarioExecution], incident_events: list[dict[str, str]]) -> list[ChecklistItem]:
    harsh_executions = [item for item in executions if item.scenario.profile == "harsh"]
    has_harsh_incident = any(
        event["scenario_id"] in {execution.scenario.scenario_id for execution in harsh_executions}
        for event in incident_events
    )
    severities = {event["severity"] for event in incident_events}
    has_p1_and_p2 = IncidentSeverity.P1.value in severities and IncidentSeverity.P2.value in severities
    p999_present = all(float(item.summary["p999_latency_ms"]) >= float(item.summary["p99_latency_ms"]) for item in executions)
    throughput_present = all(float(item.summary["throughput_rps"]) > 0 for item in executions)
    return [
        ChecklistItem(
            name="Failure sequence timeline",
            passed=len(incident_events) > 0,
            detail=f"{len(incident_events)} incident events captured",
        ),
        ChecklistItem(
            name="P1/P2 incident simulation evidence",
            passed=has_harsh_incident and has_p1_and_p2,
            detail=f"Harsh incidents={has_harsh_incident}, severities={','.join(sorted(severities)) or 'none'}",
        ),
        ChecklistItem(
            name="P95 vs P99 vs P999 metrics",
            passed=p999_present,
            detail="All scenarios contain monotonic p95/p99/p999",
        ),
        ChecklistItem(
            name="Estimated throughput calculation",
            passed=throughput_present,
            detail="All scenarios report throughput_rps",
        ),
        ChecklistItem(
            name="Multi-node concurrency diagram",
            passed=True,
            detail="Diagram embedded in report",
        ),
        ChecklistItem(
            name="CAP comparison (applied)",
            passed=True,
            detail="Mode-by-mode CAP tradeoff table embedded",
        ),
        ChecklistItem(
            name="Simplified contention model",
            passed=True,
            detail="Formula and estimated lock pressure embedded",
        ),
    ]


def render_checklist_rows(items: list[ChecklistItem]) -> str:
    rows: list[str] = []
    for item in items:
        status = "DONE" if item.passed else "MISSING"
        color = "#166534" if item.passed else "#9f1239"
        rows.append(
            (
                "<tr>"
                f"<td>{html.escape(item.name)}</td>"
                f"<td style='color:{color};font-weight:700'>{status}</td>"
                f"<td>{html.escape(item.detail)}</td>"
                "</tr>"
            )
        )
    return "".join(rows)


def render_html(
    executions: list[ScenarioExecution], requests: int, concurrency: int, seed: int, runs: int, warmup_runs: int
) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total = len(executions)
    passed = sum(1 for execution in executions if execution.passed)
    failed = total - passed
    overall_failed = failed > 0
    status_text = "FAILED" if overall_failed else "PASSED"
    status_color = "#9f1239" if overall_failed else "#166534"
    avg_p95 = sum(float(item.summary["p95_latency_ms"]) for item in executions) / max(total, 1)
    avg_p99 = sum(float(item.summary["p99_latency_ms"]) for item in executions) / max(total, 1)
    avg_p999 = sum(float(item.summary["p999_latency_ms"]) for item in executions) / max(total, 1)
    avg_throughput = sum(float(item.summary["throughput_rps"]) for item in executions) / max(total, 1)

    success = [execution for execution in executions if execution.scenario.category == "Success scenarios"]
    failure = [execution for execution in executions if execution.scenario.category == "Failure scenarios"]
    incident_events = collect_timeline_events(executions, incidents_only=True)
    timeline_events = collect_timeline_events(executions, incidents_only=False)
    checklist = build_checklist(executions, incident_events)

    strong_tp, strong_p99, strong_p999 = mode_summary(executions, "strong")
    hybrid_tp, hybrid_p99, hybrid_p999 = mode_summary(executions, "hybrid")
    eventual_tp, eventual_p99, eventual_p999 = mode_summary(executions, "eventual")

    # Two hot source accounts are alternated in payload generation.
    p_hot_source = 0.50
    conflicting_pairs = (concurrency * max(concurrency - 1, 0) / 2.0) * p_hot_source
    lock_pressure = conflicting_pairs / max(concurrency, 1)

    incident_headers = "<th>Timestamp</th><th>Scenario</th><th>Event</th><th>Severity</th><th>Details</th>"
    timeline_headers = "<th>Timestamp</th><th>Scenario</th><th>Event</th><th>Details</th>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Application Evidence Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #111827; }}
    h1, h2 {{ margin: 0 0 8px; }}
    .meta {{ color: #4b5563; margin-bottom: 20px; }}
    .status {{ display: inline-block; padding: 6px 10px; border-radius: 8px; background: {status_color}; color: #fff; font-weight: 600; }}
    .summary {{ display: grid; grid-template-columns: repeat(12, minmax(120px, 1fr)); gap: 10px; margin: 16px 0 24px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; }}
    .label {{ color: #6b7280; font-size: 12px; text-transform: uppercase; }}
    .value {{ font-size: 20px; font-weight: 700; }}
    .section {{ margin-top: 28px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; font-size: 13px; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    pre {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; overflow-x: auto; }}
  </style>
</head>
<body>
  <h1>Application Consistency Evidence Report</h1>
  <div class="meta">Generated at {generated_at}</div>
  <div class="status">{status_text}</div>
  <div class="summary">
    <div class="card"><div class="label">Scenarios</div><div class="value">{total}</div></div>
    <div class="card"><div class="label">Passed</div><div class="value">{passed}</div></div>
    <div class="card"><div class="label">Failed</div><div class="value">{failed}</div></div>
    <div class="card"><div class="label">Requests/Run</div><div class="value">{requests}</div></div>
    <div class="card"><div class="label">Concurrency</div><div class="value">{concurrency}</div></div>
    <div class="card"><div class="label">Measured Runs</div><div class="value">{runs}</div></div>
    <div class="card"><div class="label">Warmup Runs</div><div class="value">{warmup_runs}</div></div>
    <div class="card"><div class="label">Seed</div><div class="value">{seed}</div></div>
    <div class="card"><div class="label">Avg P95 (ms)</div><div class="value">{avg_p95:.2f}</div></div>
    <div class="card"><div class="label">Avg P99 (ms)</div><div class="value">{avg_p99:.2f}</div></div>
    <div class="card"><div class="label">Avg P999 (ms)</div><div class="value">{avg_p999:.2f}</div></div>
    <div class="card"><div class="label">Avg Throughput (req/s)</div><div class="value">{avg_throughput:.2f}</div></div>
  </div>

  <div class="section">
    <h2>Checklist Status</h2>
    <table>
      <thead>
        <tr><th>Item</th><th>Status</th><th>Details</th></tr>
      </thead>
      <tbody>
        {render_checklist_rows(checklist)}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Success Scenarios</h2>
    <table>
      <thead>
        <tr>
          <th>Scenario</th>
          <th>Mode/Profile/Type</th>
          <th>Description</th>
          <th>Expected</th>
          <th>Actual</th>
          <th>Metrics</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {render_category_table(success)}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Failure Scenarios</h2>
    <table>
      <thead>
        <tr>
          <th>Scenario</th>
          <th>Mode/Profile/Type</th>
          <th>Description</th>
          <th>Expected</th>
          <th>Actual</th>
          <th>Metrics</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {render_category_table(failure)}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Incident Summary</h2>
    <table>
      <thead>
        <tr><th>Incident Event</th><th>Severity</th><th>Count</th></tr>
      </thead>
      <tbody>
        {render_incident_summary(incident_events)}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Incident Timeline</h2>
    <table>
      <thead><tr>{incident_headers}</tr></thead>
      <tbody>
        {render_timeline_table(incident_events, include_severity=True)}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Execution Timeline</h2>
    <table>
      <thead><tr>{timeline_headers}</tr></thead>
      <tbody>
        {render_timeline_table(timeline_events, include_severity=False)}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>CAP Comparison Applied To This Lab</h2>
    <table>
      <thead>
        <tr><th>Mode</th><th>Consistency</th><th>Availability Under Partition</th><th>Partition Tolerance</th><th>Observed Metrics Snapshot</th></tr>
      </thead>
      <tbody>
        <tr><td>strong</td><td>Highest</td><td>Lower (request path blocks on DB/locks)</td><td>Required</td><td>Throughput {strong_tp:.2f} req/s, P99 {strong_p99:.2f} ms, P999 {strong_p999:.2f} ms</td></tr>
        <tr><td>hybrid</td><td>Strong reservation + eventual finalization</td><td>Medium/High</td><td>Required</td><td>Throughput {hybrid_tp:.2f} req/s, P99 {hybrid_p99:.2f} ms, P999 {hybrid_p999:.2f} ms</td></tr>
        <tr><td>eventual</td><td>Lowest in request path, convergent via worker</td><td>Highest on intake path</td><td>Required</td><td>Throughput {eventual_tp:.2f} req/s, P99 {eventual_p99:.2f} ms, P999 {eventual_p999:.2f} ms</td></tr>
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Multi-Node Concurrency Diagram</h2>
    <pre>
Load Generator
   |
   +--> Payments API Node A ----+
   |                            |
   +--> Payments API Node B ----+--> PostgreSQL (accounts, payments, outbox, idempotency)
                                |
                                +--> Ledger Worker Node A
                                +--> Ledger Worker Node B
    </pre>
  </div>

  <div class="section">
    <h2>Simplified Contention Model</h2>
    <p>For request bursts, the expected conflicting lock pairs per wave are estimated by:</p>
    <pre>E[conflicts] ≈ (C * (C - 1) / 2) * p_hot_source</pre>
    <p>Where C is concurrency and p_hot_source is probability two random requests target the same hot source account.</p>
    <p>This lab alternates two source accounts, so p_hot_source ≈ 0.50.</p>
    <p>With C={concurrency}, E[conflicts] ≈ {conflicting_pairs:.2f} pairs/wave and lock-pressure ratio ≈ {lock_pressure:.2f}.</p>
  </div>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    env = os.environ.copy()
    env.setdefault("DATABASE_URL", "postgresql+psycopg://ledger:ledger@localhost:5432/ledgerlab")
    if env["DATABASE_URL"].startswith("sqlite") and env.get("ALLOW_SQLITE_APP_TEST", "0") != "1":
        raise RuntimeError(
            "app-test requires PostgreSQL by default. Set ALLOW_SQLITE_APP_TEST=1 to override."
        )

    executions: list[ScenarioExecution] = []
    for scenario in scenario_matrix():
        summary = run_experiment(
            scenario,
            args.requests,
            args.concurrency,
            args.seed,
            args.runs,
            args.warmup_runs,
            env,
        )
        checks = evaluate_scenario(summary, scenario)
        executions.append(ScenarioExecution(scenario=scenario, summary=summary, checks=checks))

    incident_events = collect_timeline_events(executions, incidents_only=True)
    checklist = build_checklist(executions, incident_events)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_html(
            executions,
            requests=args.requests,
            concurrency=args.concurrency,
            seed=args.seed,
            runs=args.runs,
            warmup_runs=args.warmup_runs,
        ),
        encoding="utf-8",
    )
    print(f"Application test report updated at {output}")
    for item in checklist:
        status = "DONE" if item.passed else "MISSING"
        print(f"[{status}] {item.name}: {item.detail}")


if __name__ == "__main__":
    main()
