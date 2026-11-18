GO_VERSION   := 1.27.3
GOROOT_LOCAL := /usr/local/go
GO           := PATH=$(GOROOT_LOCAL)/bin:$$PATH go

.PHONY: help bootstrap test fmt fmt-check lint check clean

.DEFAULT_GOAL := help

help: ## Print this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
	  /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

bootstrap: ## Install the Go toolchain. Idempotent.
	@if command -v apt-get >/dev/null; then \
	  apt-get update; \
	  apt-get install -y --no-install-recommends curl ca-certificates git build-essential; \
	fi
	@if ! PATH=$(GOROOT_LOCAL)/bin:$$PATH command -v go >/dev/null || [ "$$($(GO) env GOVERSION 2>/dev/null)" != "go$(GO_VERSION)" ]; then \
	  curl -fsSL https://go.dev/dl/go$(GO_VERSION).linux-amd64.tar.gz | tar -C /usr/local -xz; \
	fi
	@echo "$(GOROOT_LOCAL)/bin" >> $${GITHUB_PATH:-/dev/null}
	@$(GO) version

test: ## Run go test.
	$(GO) test ./...

fmt: ## gofmt -w.
	$(GO) fmt ./...

fmt-check: ## Fail when gofmt would change a file.
	@out="$$(PATH=$(GOROOT_LOCAL)/bin:$$PATH gofmt -l .)"; \
	if [ -n "$$out" ]; then echo "gofmt drift:"; echo "$$out"; exit 1; fi

lint: ## go vet.
	$(GO) vet ./...

check: fmt-check lint test ## fmt-check + lint + test.

clean: ## Clear the Go test cache.
	$(GO) clean -testcache
