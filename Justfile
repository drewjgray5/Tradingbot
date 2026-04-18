# TradingBot Justfile (cross-platform alternative to Makefile)
#
# Install just: https://github.com/casey/just
# Usage: `just <target>`. Run `just` with no args to list all recipes.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

py := env_var_or_default("PY", "python")
skill := "schwab_skill"

default:
    @just --list

# Install python deps
install:
    cd {{skill}}; {{py}} -m pip install -r requirements.txt

# Lint with ruff
lint:
    cd {{skill}}; {{py}} -m ruff check .

# Format with ruff (rewrites files)
fmt:
    cd {{skill}}; {{py}} -m ruff format .

# Run mypy (best effort)
typecheck:
    cd {{skill}}; {{py}} -m mypy --ignore-missing-imports .

# pytest -q
test:
    cd {{skill}}; {{py}} -m pytest -q

# Strict full validation pipeline
validate:
    cd {{skill}}; {{py}} scripts/validate_all.py --profile local --strict

# Parallel validation with baseline-delta report
validate-fast:
    cd {{skill}}; {{py}} scripts/validate_all.py --profile local --max-parallel 4 --baseline validation_artifacts/baseline.json

# Run local webapp
webapp:
    cd {{skill}}; {{py}} -m uvicorn webapp.main:app --host 127.0.0.1 --port 8001

# Run SaaS webapp
saas-web:
    cd {{skill}}; {{py}} -m uvicorn webapp.main_saas:app --host 127.0.0.1 --port 8000

# One-off scan via CLI
scan:
    cd {{skill}}; {{py}} -m signal_scanner

# Apply retention to validation_artifacts/
prune-artifacts:
    cd {{skill}}; {{py}} scripts/prune_validation_artifacts.py --keep 5

# Promote a strategy or model: writes an entry to scripts/promotion_ledger.jsonl
promote target reason:
    cd {{skill}}; {{py}} scripts/promotion_ledger.py append --target {{target}} --reason "{{reason}}"

# Show recent promotion ledger entries
promotions:
    cd {{skill}}; {{py}} scripts/promotion_ledger.py tail --n 20
