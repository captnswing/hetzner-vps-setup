SHELL := /bin/bash
.DEFAULT_GOAL := all
.PHONY: all install format lint test statement data spreadsheet streamlit

all: test

uv.lock: pyproject.toml
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
