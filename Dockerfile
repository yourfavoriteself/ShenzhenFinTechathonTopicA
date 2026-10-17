FROM python:3.14.4-slim
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt \
 && python -m playwright install --with-deps chromium
COPY . .
ENV GUARDIAN_LLM_MODE=replay
EXPOSE 8000
# A broken image never ships: the test suite runs at build time.
RUN python -m pytest
CMD ["uvicorn", "guardian.api:app", "--host", "0.0.0.0", "--port", "8000"]
