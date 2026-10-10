FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=UTC DATA_DIR=/data
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY engine.py discover.py runtime_config.py live_clients.py live_state.py notifications.py scanner.py settings.json ./
CMD ["python", "-u", "scanner.py"]
