PY ?= python3

.PHONY: install test test-pg run replay live stub evidence anchor docs diagram video

install:
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m playwright install chromium

test:
	GUARDIAN_LLM_MODE=stub $(PY) -m pytest

# PostgreSQL tests reset the public schema of the database they are given: use an empty one
test-pg:
	GUARDIAN_LLM_MODE=stub GUARDIAN_TEST_PG=$${GUARDIAN_TEST_PG:?set GUARDIAN_TEST_PG to an empty database} \
		$(PY) -m pytest tests/integration

run replay:
	GUARDIAN_LLM_MODE=replay $(PY) -m uvicorn --factory app.web:create_web_app --port 8000

stub:
	GUARDIAN_LLM_MODE=stub $(PY) -m uvicorn --factory app.web:create_web_app --port 8000

live:
	GUARDIAN_LLM_MODE=live $(PY) -m uvicorn --factory app.web:create_web_app --port 8000

evidence:
	$(PY) -m scripts.budget_report --out evidence/budget_report.json
	$(PY) -m scripts.risk_graph_report --out evidence/risk_graph.json
	GUARDIAN_LLM_MODE=stub $(PY) -m scripts.redteam_report --out evidence/redteam.json
	GUARDIAN_LLM_MODE=stub $(PY) -m scripts.eval_report --out evidence/eval_report.json
	GUARDIAN_LLM_MODE=stub $(PY) -m scripts.cost_report --out evidence/costs.json
	GUARDIAN_LLM_MODE=stub $(PY) -m scripts.demo_run --out evidence

# Needs Foundry (forge, cast, anvil). Local devnet by default; --network sepolia with GUARDIAN_ANCHOR_RPC and ~/.sepolia_key
anchor:
	GUARDIAN_LLM_MODE=stub $(PY) -m scripts.anchor_journal

# Submission documents: PDFs (pandoc + Chromium) and the presentation (pptxgenjs)
docs:
	$(PY) docs/build.py docs/technical_documentation.md docs/security_report.src.md
	node docs/deck.js
	$(PY) docs/brand.py

diagram:
	$(PY) docs/diagrams/architecture.py
	xvfb-run -a drawio -x -f pdf --crop -o /tmp/architecture.pdf docs/diagrams/architecture.drawio
	pdftoppm -r 150 -png -singlefile /tmp/architecture.pdf docs/diagrams/architecture

# Demo video recorded from the UI (Playwright + ffmpeg); also refreshes the screenshots in docs/media.
# guardian_demo.mp4 (no captions), guardian_demo_subtitled.mp4, guardian_demo.srt, guardian_demo.zh.srt, docs/video_script.md
video:
	GUARDIAN_LLM_MODE=stub $(PY) -m scripts.record_demo --out docs/media
	$(PY) docs/brand.py
	$(PY) docs/brand.py --clips
