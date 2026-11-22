# StockScope India — Django web + embedded scheduler
FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIMEZONE=Asia/Kolkata \
    PORT=8001 \
    RUN_EMBEDDED_SCHEDULER=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data artifacts data/tradingagents/cache data/tradingagents/memory artifacts/research

EXPOSE 8001
CMD ["python", "manage.py", "run", "--port", "8001"]
