FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SENSEMAKER_HOSTED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app
COPY requirements.txt requirements-hosted.txt ./
RUN python -m pip install -r requirements-hosted.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy application code explicitly; local credentials and browser sessions stay out.
COPY dashboard.py ./
COPY hosted.py ./
COPY modules ./modules
COPY .streamlit/config.toml ./.streamlit/config.toml

# Railway supplies PORT at runtime; 8080 is the local container default.
EXPOSE 8080
CMD ["python", "hosted.py"]
