SHELL := /bin/bash

PAYMENTS_API_DIR := services/payments-api
LEDGER_WORKER_DIR := services/ledger-worker
SHARED_DIR := shared
REPORTS_DIR := reports
APP_REPORT := $(REPORTS_DIR)/test-results.html

.DEFAULT_GOAL := help

help:
	@echo "Targets:"
	@echo "  make install    Install shared + both services"
	@echo "  make up         Start infra stack"
	@echo "  make down       Stop infra stack"
	@echo "  make test       Run tests for both services + root integration folders"
	@echo "  make app-test   Run application evidence scenarios and overwrite reports/test-results.html"
	@echo "                  Defaults: REQUESTS=1000 CONCURRENCY=50 SEED=42 RUNS=3 WARMUP_RUNS=1"
	@echo "  make coverage   Coverage report with fail-under=89 for both services"
	@echo "  make lint       Run ruff on both services + shared"
	@echo "  make typecheck  Run mypy strict for shared + both services"
	@echo "  make stress     Run stress tests"
	@echo "  make migrate    Create schema and seed accounts"
	@echo "  make experiment MODE=hybrid REQUESTS=200 CONCURRENCY=20 PROFILE=none RUNS=3 WARMUP_RUNS=1"

install: install-shared install-payments-api install-ledger-worker

up: up-infra

down: down-infra

test:
	cd $(PAYMENTS_API_DIR) && poetry run pytest -q
	cd $(LEDGER_WORKER_DIR) && poetry run pytest -q
	cd $(PAYMENTS_API_DIR) && poetry run pytest ../../tests/unit ../../tests/integration -q
	$(MAKE) typecheck

app-test:
	rm -f $(APP_REPORT)
	rm -rf $(REPORTS_DIR)/junit
	cd $(PAYMENTS_API_DIR) && poetry run python ../../scripts/run_application_tests.py --output ../../$(APP_REPORT) --requests $${REQUESTS:-1000} --concurrency $${CONCURRENCY:-50} --seed $${SEED:-42} --runs $${RUNS:-3} --warmup-runs $${WARMUP_RUNS:-1}

coverage: coverage-payments-api coverage-ledger-worker

lint:
	cd $(SHARED_DIR) && poetry run ruff check .
	cd $(PAYMENTS_API_DIR) && poetry run ruff check .
	cd $(LEDGER_WORKER_DIR) && poetry run ruff check .

typecheck:
	cd $(SHARED_DIR) && poetry run mypy src/shared
	cd $(PAYMENTS_API_DIR) && poetry run mypy payments_api tests
	cd $(LEDGER_WORKER_DIR) && poetry run mypy ledger_worker tests

stress:
	cd $(PAYMENTS_API_DIR) && poetry run pytest ../../tests/stress -q

migrate:
	bash scripts/migrate.sh

experiment:
	cd $(PAYMENTS_API_DIR) && poetry run python ../../scripts/run_experiment.py --mode $${MODE:-hybrid} --requests $${REQUESTS:-200} --concurrency $${CONCURRENCY:-20} --profile $${PROFILE:-none} --runs $${RUNS:-3} --warmup-runs $${WARMUP_RUNS:-1}

up-infra:
	docker compose -f infra/docker-compose.yml up -d

down-infra:
	docker compose -f infra/docker-compose.yml down

install-shared:
	cd $(SHARED_DIR) && poetry install

install-payments-api:
	cd $(PAYMENTS_API_DIR) && poetry install

up-payments-api:
	cd $(PAYMENTS_API_DIR) && poetry run uvicorn payments_api.main:app --host 0.0.0.0 --port 8000

test-payments-api:
	cd $(PAYMENTS_API_DIR) && poetry run pytest -q

coverage-payments-api:
	cd $(PAYMENTS_API_DIR) && poetry run pytest -q --cov=payments_api --cov-report=term-missing --cov-fail-under=89

install-ledger-worker:
	cd $(LEDGER_WORKER_DIR) && poetry install

up-ledger-worker:
	cd $(LEDGER_WORKER_DIR) && poetry run python -m ledger_worker.main

test-ledger-worker:
	cd $(LEDGER_WORKER_DIR) && poetry run pytest -q

coverage-ledger-worker:
	cd $(LEDGER_WORKER_DIR) && poetry run pytest -q --cov=ledger_worker --cov-report=term-missing --cov-fail-under=89
