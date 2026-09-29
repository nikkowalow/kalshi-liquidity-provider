PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: install test lint typecheck fmt check markets demo demo-live status cancel clean

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
