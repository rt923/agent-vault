# helper-api v0.1 - minimal FastAPI image
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HELPER_MODELS_DIR=/models
ENV HELPER_PORT=8799
ENV HELPER_DATA_SOURCE=local

RUN mkdir -p /models

EXPOSE 8799

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8799/health').status==200 else 1)"

CMD ["sh", "-c", "python main.py"]
