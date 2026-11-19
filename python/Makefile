UV := PATH="$$HOME/.local/bin:$$PATH" uv

.PHONY: help bootstrap test fmt fmt-check lint check clean

.DEFAULT_GOAL := help

help: ## Print this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
	  /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

bootstrap: ## Install uv when missing, then the dev environment. Idempotent.
	@if ! PATH="$$HOME/.local/bin:$$PATH" command -v uv >/dev/null; then \
	  curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh | sh; \
	  echo "$$HOME/.local/bin" >> "$${GITHUB_PATH:-/dev/null}"; \
	fi
	$(UV) sync

test: ## Run pytest.
	$(UV) run pytest

fmt: ## ruff format.
	$(UV) run ruff format .

fmt-check: ## Fail when ruff format would change a file.
	$(UV) run ruff format --check .

lint: ## ruff check + mypy --strict.
	$(UV) run ruff check .
	$(UV) run mypy --strict

check: fmt-check lint test ## fmt-check + lint + test.

clean: ## Remove the virtualenv and tool caches.
	rm -rf .venv .mypy_cache .ruff_cache .pytest_cache dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
