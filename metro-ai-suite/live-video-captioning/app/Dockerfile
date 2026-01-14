FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv==0.4.23
COPY pyproject.toml ./
COPY main.py ./
COPY backend ./backend
COPY ui ./ui
RUN uv pip install --no-cache --system .

# Download Chart.js during build for airgapped runtime
RUN apt-get update && apt-get install -y curl && \
    mkdir -p ui/js/vendor && \
    curl -o ui/js/vendor/chart.js https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js && \
    apt-get remove -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

EXPOSE 4173

CMD ["uv", "run", "--no-sync", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "4173"]
