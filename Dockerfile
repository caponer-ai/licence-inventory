FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies in their own layer: the code changes daily, requirements
# rarely, so the layer cache survives most builds.
COPY requirements.txt constraints.txt ./
# Pinned through constraints: a rebuild six months from now installs the
# versions this was tested against, not whatever the ranges resolve to.
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt gunicorn

COPY . .

# The application code stays owned by root and the process runs as app, so
# the process cannot rewrite its own code. An earlier version chowned /app
# to the app user and still claimed the code was read-only, which was simply
# untrue. Only the directories that must be writable are handed over.
RUN useradd --create-home app     && mkdir -p /app/staticfiles     && chown app:app /app/staticfiles
RUN python manage.py collectstatic --noinput
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/').status==200 else 1)"

# One worker by default: the throttle counters live in the local-memory
# cache, so several workers would each count separately and the configured
# limit would silently become limit times workers. Raise it together with a
# shared cache backend, not before.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1"]
