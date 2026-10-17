PY ?= python3

.PHONY: install test run replay live

install:
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m playwright install chromium

test:
	GUARDIAN_LLM_MODE=stub $(PY) -m pytest

run replay:
	GUARDIAN_LLM_MODE=replay $(PY) -m uvicorn guardian.api:app --port 8000

live:
	GUARDIAN_LLM_MODE=live $(PY) -m uvicorn guardian.api:app --port 8000
