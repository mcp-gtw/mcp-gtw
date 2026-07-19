.DEFAULT_GOAL := help
.PHONY: help install lint format test coverage version build run

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install the environment with dev dependencies
	uv sync --extra dev

lint: ## Check linting and formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Apply formatting and safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

test: ## Run the test suite
	uv run pytest -q

coverage: ## Run the test suite with the coverage gate
	uv run pytest -q --cov --cov-report=term-missing --cov-report=xml

version: ## Set the release version, e.g. make version v=1.2.3
	@echo "$(v)" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$$' || { echo "usage: make version v=X.Y.Z"; exit 1; }
	@sed -i.bak -E 's/^version = ".*"/version = "$(v)"/' pyproject.toml && rm -f pyproject.toml.bak
	@echo "version set to $(v)"

build: ## Build the wheel and sdist for PyPI
	uv build

run: ## Serve the bare generic gateway on 127.0.0.1:8000
	uv run python -m mcp_gtw.main
