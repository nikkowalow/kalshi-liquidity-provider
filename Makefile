PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: install test lint typecheck fmt check markets rewards dashboard dashboard-dev dashboard-api demo demo-live status cancel clean

install:            ## create venv and install with dev deps
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -U pip
	$(BIN)/pip install -e '.[dev]'

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

typecheck:
	$(BIN)/mypy

fmt:
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

check:              ## verify demo credentials
	$(BIN)/klp check -c config/demo.yaml

markets:            ## preview demo market selection
	$(BIN)/klp markets -c config/demo.yaml

rewards:            ## estimate $/day from live production incentive programs (no key needed)
	$(BIN)/klp rewards -c config/prod.yaml

dashboard:          ## build the React dashboard and serve it: http://127.0.0.1:8050
	cd dashboard && npm install --silent && npm run build
	$(BIN)/python dashboard/server.py --runs runs --port 8050

dashboard-dev:      ## hot-reload UI dev: run `make dashboard-api` too, open http://localhost:5173
	cd dashboard && npm install --silent && npm run dev

dashboard-api:      ## journal API only (for dashboard-dev)
	$(BIN)/python dashboard/server.py --runs runs --port 8050

demo:               ## run against demo, dry-run
	$(BIN)/klp run -c config/demo.yaml

demo-live:          ## run against demo, placing (mock-money) orders
	$(BIN)/klp run -c config/demo.yaml --live

status:
	$(BIN)/klp status -c config/demo.yaml

cancel:
	$(BIN)/klp cancel -c config/demo.yaml

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
