SHELL := /bin/bash
.DEFAULT_GOAL := all
.PHONY: all install format lint test statement data spreadsheet streamlit ensure-uv

all: test

# Check if uv is installed, install it if not
ensure-uv:
	@command -v uv >/dev/null 2>&1 || { \
		echo "uv not found, installing..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	}

uv.lock: pyproject.toml | ensure-uv
	uv lock
	@# not all changes to pyproject.toml lead to a change of the uv.lock file
	@# so let's update uv.lock file modification date in any case
	@touch uv.lock

install: uv.lock
	uv sync

format: install
	uv run ruff format .
	@#uvx pyproject-fmt pyproject.toml

lint: format
	uv run ruff check --fix .
