FROM python:3.14.8-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY deploy/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock.txt \
    && useradd --system --uid 10001 --no-create-home medinv
COPY backend/src /app/src
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "medinv.entrypoints.core_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
