PY ?= python3

.PHONY: install test run replay live stub evidence

install:
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m playwright install chromium

test:
	GUARDIAN_LLM_MODE=stub $(PY) -m pytest

run replay:
	GUARDIAN_LLM_MODE=replay $(PY) -m uvicorn --factory app.web:create_web_app --port 8000

stub:
	GUARDIAN_LLM_MODE=stub $(PY) -m uvicorn --factory app.web:create_web_app --port 8000

live:
	GUARDIAN_LLM_MODE=live $(PY) -m uvicorn --factory app.web:create_web_app --port 8000

evidence:
	$(PY) -m scripts.budget_report --out evidence/budget_report.json
