FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# run as an unprivileged user
RUN useradd --create-home appuser
USER appuser

ENV LOGSTREAM_WARMUP=500 LOGSTREAM_SPIKE_K=4
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "logstream.api:app", "--host", "0.0.0.0", "--port", "8000"]
