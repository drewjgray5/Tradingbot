# TradingBot Makefile
#
# Use this for the common dev/validation/run commands. Works on PowerShell
# via `make` (install via `choco install make`) and on POSIX shells.
#
# Targets:
#   make help                -- show this help
#   make install             -- pip install -r requirements.txt
#   make lint                -- ruff check
#   make fmt                 -- ruff format
#   make typecheck           -- mypy (best effort)
#   make test                -- pytest -q
#   make validate            -- full validation pipeline (strict)
#   make validate-fast       -- parallel validation with cached baseline
#   make webapp              -- run the local webapp on :8001
#   make saas-web            -- run the SaaS webapp on :8000
#   make scan                -- one-off scan via the CLI
#   make wiki-lint           -- audit /wiki for broken links and orphans
#   make prune-artifacts     -- enforce retention on validation_artifacts/
#
# All Python invocations assume `python` is the current venv interpreter.

PY ?= python
PIP ?= $(PY) -m pip

.PHONY: help install lint fmt typecheck test validate validate-fast webapp saas-web scan wiki-lint prune-artifacts

help:
	@echo "TradingBot make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | awk 'BEGIN{FS=":.*?## "}{printf "  %-20s %s\n", $$1, $$2}'

install: ## Install python deps
	cd schwab_skill && $(PIP) install -r requirements.txt

lint: ## Run ruff lint
	cd schwab_skill && $(PY) -m ruff check .

fmt: ## Run ruff format (rewrites files)
	cd schwab_skill && $(PY) -m ruff format .

typecheck: ## Run mypy (best effort, non-blocking)
	cd schwab_skill && $(PY) -m mypy --ignore-missing-imports . || true

test: ## pytest -q
	cd schwab_skill && $(PY) -m pytest -q

validate: ## Strict full validation pipeline
	cd schwab_skill && $(PY) scripts/validate_all.py --profile local --strict

validate-fast: ## Parallel validation pipeline with baseline-delta report
	cd schwab_skill && $(PY) scripts/validate_all.py --profile local --max-parallel 4 --baseline validation_artifacts/baseline.json

webapp: ## Run local webapp at http://127.0.0.1:8001
	cd schwab_skill && $(PY) -m uvicorn webapp.main:app --host 127.0.0.1 --port 8001

saas-web: ## Run SaaS webapp at http://127.0.0.1:8000
	cd schwab_skill && $(PY) -m uvicorn webapp.main_saas:app --host 127.0.0.1 --port 8000

scan: ## Run one-off scan via CLI
	cd schwab_skill && $(PY) -m signal_scanner

wiki-lint: ## Lint the wiki for broken links and orphans
	$(PY) - <<'PY'
	from pathlib import Path
	import re
	root = Path('wiki')
	pages = {p.stem: p for p in root.glob('*.md') if p.name != 'index.md'}
	idx_text = (root / 'index.md').read_text(encoding='utf-8') if (root / 'index.md').exists() else ''
	idx_refs = set(re.findall(r"\[\[([^\]]+)\]\]", idx_text))
	broken, orphans = [], []
	for stem, path in pages.items():
	    text = path.read_text(encoding='utf-8')
	    for ref in re.findall(r"\[\[([^\]]+)\]\]", text):
	        if ref not in pages and ref != 'index':
	            broken.append((path.name, ref))
	    if stem not in idx_refs:
	        orphans.append(path.name)
	print(f"Broken links: {len(broken)}")
	for f, ref in broken[:50]:
	    print(f"  {f} -> [[{ref}]]")
	print(f"Orphan pages: {len(orphans)}")
	for o in orphans[:50]:
	    print(f"  {o}")
	PY

prune-artifacts: ## Apply retention to validation_artifacts/
	cd schwab_skill && $(PY) scripts/prune_validation_artifacts.py --keep 5
