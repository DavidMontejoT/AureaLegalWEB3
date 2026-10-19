FROM python:3.12-slim

# Sin pip install a proposito: analitica usa solo la biblioteca estandar de Python.

WORKDIR /app
COPY app.py panel.html ./

# Corre como usuario sin privilegios; /datos, montado desde el host, es lo unico que escribe.
RUN useradd --uid 10002 --user-group --home-dir /app --shell /usr/sbin/nologin analitica
USER 10002:10002

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ANALITICA_DATOS=/datos \
    ANALITICA_PUERTO=8082

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/salud', timeout=4)"]

EXPOSE 8082
CMD ["python", "app.py"]
