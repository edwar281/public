FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/app ./api/app
COPY web ./web
RUN useradd --uid 10001 --no-create-home app && mkdir /cache && chown app /cache
USER app
ENV WEB_DIR=/srv/web CACHE_PATH=/cache/presets.json
EXPOSE 8000
CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
