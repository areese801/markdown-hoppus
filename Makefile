.PHONY: help build publish tag lint format test run clean

# markdown-hoppus is a single-package project: distribution `markdown-hoppus`,
# import package `hoppus` (src layout). `uv build` produces the wheel + sdist
# into dist/.
#
# Publishing uses `uv publish`, which reads UV_PUBLISH_TOKEN from the
# environment (see set_creds.sh for the canonical source — it reads the token
# from /.credentials/personal/pypi_api_token, same as the other repos).

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

build: clean ## Build the wheel and sdist into dist/
	uv build --out-dir dist/

publish: build ## Build and upload to PyPI, then tag v<version>
	. ./set_creds.sh && uv publish dist/*
	$(MAKE) tag

tag: ## Create a v<version> git tag from pyproject.toml (pushes if an 'origin' remote exists)
	@VERSION=$$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2); \
	 if git rev-parse "v$$VERSION" >/dev/null 2>&1; then \
	   echo "Tag v$$VERSION already exists - skipping."; \
	 else \
	   git tag -a "v$$VERSION" -m "Release v$$VERSION"; \
	   if git remote get-url origin >/dev/null 2>&1; then \
	     git push origin "v$$VERSION" && echo "Tagged and pushed v$$VERSION."; \
	   else \
	     echo "Tagged v$$VERSION locally (no 'origin' remote to push to)."; \
	   fi; \
	 fi

lint: ## Run ruff check
	uv run ruff check .

format: ## Run ruff format
	uv run ruff format .

test: ## Run the pytest suite
	uv run pytest

run: ## Launch the app (stub notice for now)
	uv run hoppus

clean: ## Remove build artifacts
	rm -rf dist/ build/
	find . -type d -name '*.egg-info' -exec rm -rf {} +
