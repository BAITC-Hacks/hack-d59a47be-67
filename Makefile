PYTHON ?= python3.12
VENV_PYTHON := .venv/bin/python
DATASET ?= career_quest_dataset.zip

.PHONY: help setup-dev prepare-data audit check dev

help:
	@echo 'make setup-dev   — create Python 3.12 venv, install pinned deps and AGENTS hook'
	@echo 'make prepare-data DATASET=career_quest_dataset.zip — extract local kit'
	@echo 'make audit       — reproduce dataset audit (requires data/raw)'
	@echo 'make check       — synthetic tests and AI contract fixture checks'

setup-dev:
	$(PYTHON) -m venv .venv
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	git config core.hooksPath .githooks

prepare-data:
	$(PYTHON) scripts/prepare_dataset.py "$(DATASET)"

audit:
	@$(PYTHON) scripts/audit_dataset.py data/raw

check:
	$(VENV_PYTHON) -m unittest discover -s tests -v
	$(VENV_PYTHON) -m pytest backend/tests -q
	$(VENV_PYTHON) scripts/check_contracts.py
	$(VENV_PYTHON) -m ruff check backend scripts/smoke.py scripts/check_contracts.py scripts/verify_integration.py scripts/check_container.py

dev:
	scripts/dev
