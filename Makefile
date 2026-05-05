.PHONY: help test golden verify-datasheets datasheets datasheets-offline baseline ablation reproduce eval full-eval clean

# Default target — quick reminder of what's available.
help:
	@echo "Silicon to Software (S2S) V2 — Makefile targets"
	@echo ""
	@echo "  make test              Run the full pytest suite"
	@echo "  make golden            Run just the golden-scenario regression tests"
	@echo "  make baseline          Deterministic eval — 10 golden scenarios, no LLM"
	@echo "  make ablation          4-config ablation matrix (validator/citation/redteam)"
	@echo "  make reproduce         Deterministic reproducibility self-test"
	@echo "  make verify-datasheets Walk component DB, verify datasheet URLs (needs net)"
	@echo "  make datasheets        Live sweep + write docs/datasheet_sweep_latest.{md,json}"
	@echo "  make datasheets-offline Offline sweep (vendor-whitelist only, safe for CI)"
	@echo "  make eval              Alias for full-eval"
	@echo "  make full-eval         Full deterministic suite — one command for demo prep"
	@echo "  make clean             Remove __pycache__ / .pyc files"

test:
	python -m pytest tests/ -q

golden:
	python -m pytest tests/test_golden.py -v

verify-datasheets:
	python scripts/verify_datasheets.py

# E3 — networked sweep harness. Produces a committable report under docs/.
datasheets:
	python scripts/verify_datasheets.py --report

# CI / air-gap safe: skip the live HTTP probe, rely on the vendor whitelist.
datasheets-offline:
	python scripts/verify_datasheets.py --offline --dry-run --report

baseline:
	python scripts/run_baseline_eval.py

ablation:
	python scripts/run_ablation_matrix.py

reproduce:
	python scripts/reproduce_run.py

# One command the team runs before every demo. Exits 0 on full pass.
eval: full-eval
full-eval:
	python scripts/run_full_eval.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

# 2026-05-02: review queue for low-confidence component specs.
# Lists every spec that landed under confidence 0.85 with the source
# datasheet URL so a human can verify it against the PDF.
review-specs:
	@python -c "from services.datasheet_extractor import list_review_queue; \
import json; queue = list_review_queue(); print(f'{len(queue)} specs awaiting review:'); \
print('\n'.join(f'  [{r[\"confidence\"]:.2f}] {r[\"mpn\"]:<14} (src={r[\"source\"]:<18}) -> {r[\"datasheet_url\"]}' for r in queue))"
