# Next steps

1) Bootstrap dependencies and local virtualenvs
- bash scripts/bootstrap.sh

2) Start infra
- make up

3) Prepare schema and seed accounts
- make migrate

4) Run quality gates
- make test
- make coverage

5) Generate application evidence report
- make app-test REQUESTS=1000 CONCURRENCY=50 RUNS=3 WARMUP_RUNS=1
- report includes scenario matrix, incident timeline, P95/P99/P999, throughput, CAP table, and contention model

6) Run API and worker in separate terminals
- make up-payments-api
- make up-ledger-worker

7) Jaeger UI
- http://localhost:16686

8) Prometheus
- http://localhost:9090

9) SonarCloud
- push to `main` or open a PR to trigger `.github/workflows/sonar.yml`
- ensure branch protection requires SonarCloud status check
