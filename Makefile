# hetzner-vps-setup — provision hardened Ubuntu VPSes on Hetzner Cloud.
#
# There is no test suite (verification is an actual provisioning run), so there is
# no `test` target. The previous `all: test` plus a .PHONY list copied from
# personal-finance (statement/data/spreadsheet/streamlit) declared five targets
# that never existed here, and made a bare `make` die with "No rule to make target
# 'test'". The default goal is now `help`, and only the real targets are listed.
#
# Run `make` (or `make help`) for the target list.

# pipefail so a failure anywhere in a pipe fails the recipe. Deliberately NOT
# `.SHELLFLAGS` — macOS ships GNU make 3.81, which silently ignores that variable.
SHELL := /bin/bash -o pipefail
.DEFAULT_GOAL := help

# FILE=<paths> narrows format/lint to a subset (space-separated; quote if >1).
FILE ?=
SRC  := .

.PHONY: help
help:  ## Show this help
	@awk 'BEGIN {FS = ":.*## "} \
	     /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next } \
	     /^[a-zA-Z0-9_.\/-]+:.*## / { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' \
	     $(MAKEFILE_LIST)
	@printf '\nScope format/lint with FILE=<paths>, e.g. make lint FILE=setup-vps.py\n'
	@printf 'First time? Follow README → One-time setup, then: make doctor && make provision\n'

# Check if uv is installed, install it if not
.PHONY: ensure-uv
ensure-uv:  ## Install uv if it is not already on PATH
	@command -v uv >/dev/null 2>&1 || { \
		echo "uv not found, installing..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	}

uv.lock: pyproject.toml | ensure-uv
	uv lock
	@# not all changes to pyproject.toml lead to a change of the uv.lock file
	@# so let's update uv.lock file modification date in any case
	@touch uv.lock

##@ Develop

.PHONY: install
install: uv.lock  ## Sync the virtualenv from uv.lock
	uv sync

.PHONY: format
format: install  ## Format with ruff (FILE=<paths> to scope)
	uv run ruff format $(or $(FILE),$(SRC))
	@#uvx pyproject-fmt pyproject.toml

.PHONY: lint
lint: format  ## Format, then lint + autofix with ruff (FILE=<paths> to scope)
	uv run ruff check --fix $(or $(FILE),$(SRC))

##@ Provision

.PHONY: doctor
doctor: install  ## Preflight check: env, tokens and tooling
	uv run python doctor.py

.PHONY: provision
provision: install  ## Create a new server (interactive)
	uv run setup-vps.py
