PYTHON ?= python3
VENV_PYTHON := .venv/bin/python
DATASET ?= career_quest_dataset.zip

.PHONY: help setup-dev prepare-data audit check

help:
	@echo 'make setup-dev   — install artifact QA dependency and AGENTS commit hook'
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
	$(PYTHON) -m unittest discover -s tests -v
	$(VENV_PYTHON) scripts/check_contracts.py
