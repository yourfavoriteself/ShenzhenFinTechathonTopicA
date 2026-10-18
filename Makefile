PY ?= python3

.PHONY: install test test-pg run replay live stub evidence

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
