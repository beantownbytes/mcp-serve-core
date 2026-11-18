RUST_TOOLCHAIN := 1.97.1
CARGO_HOME     ?= $(HOME)/.cargo
CARGO          := PATH=$(CARGO_HOME)/bin:$$PATH cargo

.PHONY: help bootstrap test fmt fmt-check lint check clean

.DEFAULT_GOAL := help

help: ## Print this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
	  /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

bootstrap: ## Install rustup + rustfmt + clippy. Idempotent.
	@if command -v apt-get >/dev/null; then \
	  apt-get update; \
	  apt-get install -y --no-install-recommends curl ca-certificates git build-essential pkg-config; \
	fi
	@if ! command -v rustup >/dev/null && [ ! -x $(CARGO_HOME)/bin/rustup ]; then \
	  curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain $(RUST_TOOLCHAIN) --profile minimal --component rustfmt --component clippy; \
	fi
	@echo "$(CARGO_HOME)/bin" >> $${GITHUB_PATH:-/dev/null}
	@PATH=$(CARGO_HOME)/bin:$$PATH rustup component add rustfmt clippy
	@PATH=$(CARGO_HOME)/bin:$$PATH rustc --version

test: ## Run cargo test with every feature.
	$(CARGO) test --all-features

fmt: ## cargo fmt --all.
	$(CARGO) fmt --all

fmt-check: ## cargo fmt --check (non-zero on drift).
	$(CARGO) fmt --all -- --check

lint: ## cargo clippy with warnings as errors.
	$(CARGO) clippy --all-targets --all-features -- -D warnings

check: fmt-check lint test ## fmt-check + lint + test.

clean: ## Remove target/.
	$(CARGO) clean
