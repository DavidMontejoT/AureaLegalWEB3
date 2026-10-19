FROM python:3.12-slim

# Sin pip install a proposito: metricas usa solo la biblioteca estandar de Python (igual que leads).

WORKDIR /app
COPY app.py m.js panel.html ./

# Corre sin privilegios; el codigo queda de root (solo lectura para el proceso) y /datos,
# montado desde el host, es lo unico que necesita escribir.
RUN useradd --uid 10001 --user-group --home-dir /app --shell /usr/sbin/nologin metricas
USER 10001:10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    METRICAS_DATOS=/datos \
    METRICAS_PUERTO=8082

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/salud', timeout=4)"]

EXPOSE 8082
CMD ["python", "app.py"]
