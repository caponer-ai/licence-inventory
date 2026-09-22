FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies in their own layer: the code changes daily, requirements
# rarely, so the layer cache survives most builds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Not root: if the process inside the container is compromised, it does not
# get write access to the image or to mounted volumes.
RUN useradd --create-home app && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/').status==200 else 1)"

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
