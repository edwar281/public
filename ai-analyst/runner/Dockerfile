FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg
WORKDIR /srv
COPY runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY runner/runner.py runner/runtime.py ./
COPY data/parquet /data
RUN useradd --uid 10002 --no-create-home runner
USER runner
ENV DATA_DIR=/data RUNNER_REAP=1
EXPOSE 8001
CMD ["uvicorn", "runner:app", "--host", "0.0.0.0", "--port", "8001"]
