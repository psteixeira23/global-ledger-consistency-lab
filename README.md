# global-ledger-consistency-lab

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=bugs)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=coverage)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)
[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)
[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)
[![Vulnerabilities](https://sonarcloud.io/api/project_badges/measure?project=psteixeira23_global-ledger-consistency-lab&metric=vulnerabilities)](https://sonarcloud.io/summary/new_code?id=psteixeira23_global-ledger-consistency-lab)

Comparative engineering lab for distributed financial ledger consistency.

This repository compares strong, hybrid, and eventual consistency under controlled load, deterministic failures, and financial invariants.

## Tech stack
- Python 3.13
- FastAPI
- PostgreSQL
- Outbox Pattern
- Async worker
- OpenTelemetry + Jaeger
- Prometheus
- Docker Compose
- Coverage target per service: `>= 89%`

## Architecture
- `services/payments-api`
  - Synchronous payment intake
  - Idempotency handling
  - Mode-dependent transactional behavior (`strong|hybrid|eventual`)
  - Outbox write (for async modes)
- `services/ledger-worker`
  - Outbox polling with retry/backoff
  - Mode-dependent event processing
  - Reconciliation loop
  - Deterministic failure injection
- `shared/src/shared`
  - Contracts/enums
  - SQLAlchemy ORM models shared across services

## Architectural style and design patterns
This project follows a **Clean Architecture style** with explicit layers:
- `api` (HTTP adapters)
- `use_cases` / `services` (application orchestration)
- `repositories` (data-access abstraction)
- `db` (infrastructure and persistence wiring)
- `shared` (cross-service contracts and ORM schema)

This structure was chosen to keep business behavior testable and framework-agnostic, which is critical for consistency experiments and deterministic failure scenarios.

Adopted patterns:
- **Repository Pattern**
  - Encapsulates SQLAlchemy queries behind repository classes.
  - Why: isolates storage concerns and keeps use cases focused on business rules.
- **Unit of Work style (transaction boundary per use case/event)**
  - Explicit `session.begin()` scopes are used in API and worker flows.
  - Why: guarantees atomic state transitions for financial operations.
- **Outbox Pattern**
  - Async flows persist domain state + outbox event in the same transaction.
  - Why: prevents lost events and supports reliable async processing.
- **Idempotency Key Pattern**
  - Request hash + key storage ensures safe retries and conflict detection.
  - Why: payment APIs must be resilient to duplicated client/network retries.
- **Retry with Exponential Backoff**
  - Worker retries transient failures and marks events dead after max attempts.
  - Why: improves resilience under partial failures while bounding retries.
- **Reconciliation Loop**
  - Periodic checks validate ledger balance and negative balances.
  - Why: adds runtime invariant verification and drift detection.
- **Policy-by-mode (enum-driven strategy selection)**
  - `CONSISTENCY_MODE` routes execution to strong/hybrid/eventual behavior through explicit mode strategies.
  - Why: keeps the experiment switch explicit while preserving deterministic behavior per mode.
- **Strategy Pattern (mode execution)**
  - `payments_api/use_cases/mode_strategies.py` and `ledger_worker/services/mode_strategies.py` encapsulate mode-specific rules.
  - Why: isolates consistency policies, reducing conditional sprawl and making behavior comparisons cleaner in tests.
- **Message Catalog Pattern (enum-based messages)**
  - `shared/src/shared/contracts/messages.py` centralizes domain/worker messages.
  - Why: removes duplicated literals, keeps error semantics consistent, and simplifies future i18n or message evolution.

Design note:
- Account `version` fields are present for optimistic locking evolution, while current consistency control relies primarily on transactional locks and ordering.

### Pattern map
| Pattern | Where in code | Why |
|---|---|---|
| Clean Architecture style | `services/payments-api/payments_api/{api,use_cases,repositories,db}` and `services/ledger-worker/ledger_worker/{services,repositories,db}` | Keeps transport, orchestration, and persistence concerns separated for testability and controlled experiments. |
| Repository Pattern | `services/payments-api/payments_api/repositories/*.py`, `services/ledger-worker/ledger_worker/repositories/*.py` | Centralizes query logic and prevents persistence details from leaking into use cases. |
| Unit of Work style (transaction boundary) | `services/payments-api/payments_api/use_cases/create_payment.py`, `services/ledger-worker/ledger_worker/services/processor.py` | Ensures each payment state transition is committed atomically. |
| Outbox Pattern | `shared/src/shared/db/orm_models.py` (`OutboxEventORM`), `services/payments-api/payments_api/use_cases/create_payment.py`, `services/ledger-worker/ledger_worker/repositories/outbox_repository.py` | Guarantees async processing reliability by persisting event intent in the same transaction as domain state. |
| Idempotency Key Pattern | `shared/src/shared/db/orm_models.py` (`IdempotencyKeyORM`), `services/payments-api/payments_api/repositories/idempotency_repository.py`, `services/payments-api/payments_api/use_cases/create_payment.py` | Protects payment endpoint against duplicated requests and network retries. |
| Retry + Exponential Backoff | `services/ledger-worker/ledger_worker/services/processor.py` (`_handle_transient_failure`) | Handles transient failures while bounding retries and moving terminal failures to dead state. |
| Reconciliation Loop | `services/ledger-worker/ledger_worker/services/reconciliation.py`, `services/ledger-worker/ledger_worker/main.py` | Continuously verifies financial invariants and detects ledger drift. |
| Policy-by-mode (consistency strategy selection) | `shared/src/shared/contracts/models.py` (`ConsistencyMode`), `services/payments-api/payments_api/use_cases/create_payment.py`, `services/ledger-worker/ledger_worker/services/processor.py` | Allows deterministic behavior switching across strong, hybrid, and eventual modes for comparative studies. |
| Strategy Pattern (mode execution) | `services/payments-api/payments_api/use_cases/mode_strategies.py`, `services/ledger-worker/ledger_worker/services/mode_strategies.py` | Keeps each consistency mode explicit and independently testable. |
| Message Catalog Pattern | `shared/src/shared/contracts/messages.py`, `services/payments-api/payments_api/use_cases/create_payment.py`, `services/ledger-worker/ledger_worker/services/{processor,mode_strategies,failure_injector}.py` | Centralizes operational/domain messages and avoids message drift across services. |
| Deterministic Failure Injection | `services/ledger-worker/ledger_worker/services/failure_injector.py` | Reproducible failure behavior using fixed seed and profile presets. |
| Processing lease recovery | `services/ledger-worker/ledger_worker/repositories/outbox_repository.py` | Recovers stuck `PROCESSING` events after worker crashes/timeouts. |

## Scripts
- `scripts/bootstrap.sh`
  - Validates local toolchain (`poetry`, `docker`, `make`)
  - Configures in-project virtualenvs for all components
  - Installs dependencies using `make install`
- `scripts/migrate.sh`
  - Runs schema setup/seed through `payments_api.db.migrate`
- `scripts/run_experiment.py`
  - Executes single experiment mode with optional warmup/measured runs
  - Exposes `p50/p95/p99/p999`, throughput, consistency counters, and execution timeline
- `scripts/run_application_tests.py`
  - Executes success + failure scenario matrix
  - Regenerates a single HTML evidence report (`reports/test-results.html`) with:
    - Scenario pass/fail matrix
    - Incident summary and timeline (P1/P2-like events)
    - `p95/p99/p999` and throughput evidence
    - CAP tradeoff table by consistency mode
    - Multi-node concurrency diagram
    - Simplified contention model
- `scripts/NEXT_STEPS.md`
  - Concise operational runbook aligned with current targets

## Consistency modes
- `CONSISTENCY_MODE=strong`
  - Debit/credit + ledger entries are committed during request processing.
- `CONSISTENCY_MODE=hybrid` (default)
  - Funds are reserved synchronously; final ledger application is async in worker.
- `CONSISTENCY_MODE=eventual`
  - Payment request is accepted first; full balance/ledger mutation happens async.

## Financial invariants
- No negative balances
- No double application for same payment
- Idempotency required
- Debit/Credit conservation
- Per-account ordering through transactional locks and worker processing

## Deterministic failure profiles
- `EXPERIMENT_SEED=42`
- `FAIL_PROFILE=none|mild|harsh`
- `none`
  - No injected failures
- `mild`
  - 2% DB delay
  - 1% worker exception
- `harsh`
  - 10% DB delay
  - 5% worker exception
  - 5% redis-failure simulation

## API endpoints
- `POST /v1/payments`
- `GET /health`
- `GET /metrics`
- `GET /internal/stats`

## Quickstart
1. Install dependencies
```bash
make install
```
Optional one-shot bootstrap:
```bash
bash scripts/bootstrap.sh
```

2. Start infrastructure
```bash
make up
```

3. Run migrations and seed data
```bash
make migrate
```

4. Start services (separate terminals)
```bash
make up-payments-api
```
```bash
make up-ledger-worker
```

5. Run tests and coverage
```bash
make test
```
`make test` includes strict `mypy` for `shared`, `payments-api`, and `ledger-worker`.
```bash
make coverage
```

Run application-level scenario tests and generate HTML report:
```bash
make app-test
```
`app-test` executes an evidence matrix with:
- Success scenarios (`strong`, `hybrid`, `eventual`) under load
- Failure-injection scenarios (`harsh` profile)
- Negative business path (`eventual` with insufficient funds)
- Incident timeline extraction from measured runs
- CAP and contention analysis sections inside the HTML report

Evidence guardrails in app scenarios:
- `harsh` scenarios enforce a minimum request volume internally to reliably expose retry evidence.
- `insufficient_funds` scenario uses per-request amount above seeded account balance to guarantee rejection evidence.

Default evidence load for `app-test`:
- `REQUESTS=1000`
- `CONCURRENCY=50`
- `SEED=42`
- `RUNS=3`
- `WARMUP_RUNS=1`

Optional override:
```bash
make app-test REQUESTS=1200 CONCURRENCY=60 SEED=42 RUNS=5 WARMUP_RUNS=1
```

`make app-test` always regenerates a single consolidated HTML report at:
- `reports/test-results.html`

The report is overwritten on each run (no historical report files are kept).
By default, `make app-test` uses PostgreSQL at `postgresql+psycopg://ledger:ledger@localhost:5432/ledgerlab`.
Override with `DATABASE_URL` when needed.
If `DATABASE_URL` points to SQLite, `app-test` fails fast by default to avoid misleading consistency results.
Set `ALLOW_SQLITE_APP_TEST=1` only for local smoke/debug runs.

## Experiment runner
From repository root:

```bash
make experiment MODE=strong REQUESTS=200 CONCURRENCY=20 PROFILE=none
make experiment MODE=hybrid REQUESTS=200 CONCURRENCY=20 PROFILE=none
make experiment MODE=eventual REQUESTS=200 CONCURRENCY=20 PROFILE=none
```

Equivalent direct call:

```bash
cd services/payments-api
poetry run python ../../scripts/run_experiment.py \
  --mode hybrid \
  --requests 200 \
  --concurrency 20 \
  --profile none \
  --runs 3 \
  --warmup-runs 1
```
Experiment output includes aggregate throughput, latency distribution (`p50`, `p95`, `p99`, `p999`), and run timeline events.

## Evidence model in `reports/test-results.html`
- Checklist status for:
  - Failure sequence timeline
  - P1/P2 incident simulation
  - `p95/p99/p999` percentile evidence
  - Throughput estimation
  - Multi-node concurrency diagram
  - CAP comparison
  - Simplified contention model
- Incident evidence:
  - Incident event counts by type
  - Chronological incident timeline
  - Full execution timeline (milestones + incidents)
- Incident severity taxonomy:
  - `info`: operational milestones (run started, migration done, load started, etc.)
  - `p2`: recoverable/business incidents (for example deterministic rejection evidence)
  - `p1`: critical consistency-risk incidents (dependency instability or invariant drift signals)
- CAP comparison applied to this lab:
  - `strong`: strongest consistency, lower availability under partition/lock pressure
  - `hybrid`: balanced consistency/availability using sync reserve + async finalization
  - `eventual`: highest intake availability, convergence delegated to worker
- Simplified contention model:
  - Uses `E[conflicts] ≈ (C * (C - 1) / 2) * p_hot_source`
  - In this lab, payload generation alternates hot source accounts, so `p_hot_source ≈ 0.50`
  - Report computes estimated lock-pressure from runtime `CONCURRENCY`

## Observability
- Jaeger UI: `http://localhost:16686`
- Prometheus UI: `http://localhost:9090`

Useful checks:

```bash
curl -s http://localhost:16686/api/services
curl -s http://localhost:8000/metrics
curl -s http://localhost:8001/metrics
```

## SonarCloud
- Workflow: `.github/workflows/sonar.yml`
- Project config: `sonar-project.properties`
- Required GitHub Actions secrets:
  - `SONAR_TOKEN`
  - `SONAR_HOST_URL` (`https://sonarcloud.io`)

What the workflow does:
- Installs dependencies with Poetry for `shared`, `payments-api`, and `ledger-worker`
- Runs service tests with XML coverage reports
- Executes SonarCloud scan on `push` and `pull_request` for `main`

Manual GitHub step still required:
- In branch protection rules for `main`, require the SonarCloud status check before merge.

## Environment variables
- `CONSISTENCY_MODE=strong|hybrid|eventual`
- `EXPERIMENT_SEED=42`
- `FAIL_PROFILE=none|mild|harsh`
- `DATABASE_URL` (optional override)
- `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://localhost:4317`)
- `OUTBOX_PROCESSING_TIMEOUT_SECONDS` (default `30`)
- `MIGRATE_RECREATE_SCHEMA` (default `1`, drops/recreates schema in lab mode)

## Notes
- API idempotency persistence is atomic with payment/outbox transaction scope.
- Worker retries use deterministic fault decisions by `seed + event_id + attempt`.
- Automated tests use shared contract enums/message catalog to avoid drift between code and assertions.
- Benchmark conclusions should be taken from PostgreSQL runs, not SQLite fallback.
- The project is intentionally explicit and didactic rather than framework-heavy.

## License
MIT. See `LICENSE`.
