FROM python:3.12-slim

# Sin pip install a proposito: leads usa solo la biblioteca estandar de Python.
# Eso lo deja chico, sin dependencias que actualizar y sin red al construir.

WORKDIR /app
COPY app.py listar.py ./

# Corre como usuario sin privilegios; el codigo queda de root (solo lectura para el
# proceso) y /datos, montado desde el host, es lo unico que necesita escribir.
RUN useradd --uid 10001 --user-group --home-dir /app --shell /usr/sbin/nologin leads
USER 10001:10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LEADS_DATOS=/datos \
    LEADS_PUERTO=8081

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/salud', timeout=4)"]

EXPOSE 8081
CMD ["python", "app.py"]
