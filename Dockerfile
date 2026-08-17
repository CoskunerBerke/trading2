# Trading Bot — 7/24 AI trader (paper). Bulutta çalıştırmak için.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt matplotlib websocket-client
COPY . .
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=Europe/Istanbul
# Obsidian kasası ve state konteyner dışına (volume) yazılır
ENV TRADINGBOT_VAULT_PATH=/data/vault
VOLUME ["/data"]
CMD ["sh", "-c", "mkdir -p /data/state /data/vault && ln -sfn /data/state /app/state && python -m tradingbot watch --interval 15 --scan-every 2"]
