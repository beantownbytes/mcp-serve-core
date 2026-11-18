# Root Makefile for mcp-serve-core. Library only: no binaries, no release artifacts.
# Loops the rust/ and go/ sub-Makefiles. Run `make help` for the target list.

VERSION      := $(shell awk -F'"' '/^version[[:space:]]*=/ { print $$2; exit }' rust/Cargo.toml)
SUBDIRS_ALL  := rust go

.PHONY: help bootstrap test fmt fmt-check lint check version clean

.DEFAULT_GOAL := help

help: ## Print this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
	  /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

bootstrap: ## Bootstrap every sub-project (rust, go).
	@for d in $(SUBDIRS_ALL); do $(MAKE) -C $$d bootstrap || exit $$?; done

test: ## Run the test suite for every sub-project.
	@for d in $(SUBDIRS_ALL); do $(MAKE) -C $$d test || exit $$?; done

fmt: ## Auto-format every sub-project.
	@for d in $(SUBDIRS_ALL); do $(MAKE) -C $$d fmt || exit $$?; done

fmt-check: ## Verify formatting across every sub-project.
	@for d in $(SUBDIRS_ALL); do $(MAKE) -C $$d fmt-check || exit $$?; done

lint: ## Lint every sub-project (warnings as errors).
	@for d in $(SUBDIRS_ALL); do $(MAKE) -C $$d lint || exit $$?; done

check: fmt-check lint test ## fmt-check + lint + test across every sub-project. Pre-push gate.

version: ## Print just the version.
	@echo $(VERSION)

clean: ## Clean every sub-project.
	@for d in $(SUBDIRS_ALL); do $(MAKE) -C $$d clean || exit $$?; done
