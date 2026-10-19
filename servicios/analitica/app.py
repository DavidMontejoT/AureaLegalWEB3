#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analitica: cuenta visitas de aurea.legal y arma el mapa de calor, sin terceros ni cookies.

Solo biblioteca estandar, igual que leads: no suma dependencias a la imagen.

    POST /api/evento                lote de eventos del rastreador de la pagina
                                    204 recibido | 400 invalido | 413 grande | 429 limite
    GET  /analitica                 panel (usuario y clave por HTTP Basic)
    GET  /analitica/api/resumen     ?dias=30          cifras, series y embudo de secciones
    GET  /analitica/api/clics       ?dias=30&disp=escritorio&ruta=/   puntos del mapa de calor
    GET  /salud                     200 si el proceso y la base responden

Privacidad (Ley 1581): no hay cookies ni identificadores guardados en el navegador. Un
visitante se reconoce solo dentro del mismo dia, con un hash de IP + navegador + una sal que
cambia cada dia; la IP en claro nunca se guarda y al dia siguiente el hash ya no se puede
relacionar. El rastreador respeta "Do Not Track" y "Global Privacy Control".

Todo vive FUERA del repositorio, en el directorio montado (`ANALITICA_DATOS`, por defecto `/datos`):

    analitica.db   SQLite: tablas `vistas`, `eventos` y `clics`
    sal            base de la sal diaria (se crea sola)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

RUTA_EVENTOS = "/api/evento"
MAX_CUERPO = 16 * 1024
MAX_EVENTOS = 60
HORA_LOCAL = timezone(timedelta(hours=-5), "COT")
DISPOSITIVOS = ("escritorio", "tablet", "movil")
TIPOS = {"scroll", "seccion", "clic", "form"}
BOTS = re.compile(r"bot|crawl|spider|slurp|headless|preview|lighthouse|curl|wget|python|httpclient|facebookexternalhit",
                  re.I)
PANEL = Path(__file__).with_name("panel.html")
LEADS_DB = Path(os.environ.get("LEADS_DB", "/leads/leads.db"))


def registrar(evento: str, **datos) -> None:
    """Una linea JSON por evento a stderr (`docker logs`)."""
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "evento": evento, **datos}, ensure_ascii=False), file=sys.stderr, flush=True)


def dia_local(ts: float) -> str:
    return datetime.fromtimestamp(ts, HORA_LOCAL).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- limpieza de datos del cliente

class ErrorValidacion(ValueError):
    pass


def _corto(valor, maximo: int) -> str:
    if not isinstance(valor, str):
        return ""
    valor = "".join(c for c in valor if c.isprintable())
    return re.sub(r"\s{2,}", " ", valor).strip()[:maximo]


def _numero(valor, minimo: float, maximo: float) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if valor != valor:  # NaN
        return None
    return float(min(max(valor, minimo), maximo))


def limpiar_ruta(valor) -> str:
    ruta = _corto(valor, 200) or "/"
    ruta = urlsplit(ruta).path or "/"
    # Solo caracteres de ruta normales y una sola barra al inicio: "//otro.sitio" o "/\\otro.sitio"
    # harian que el mapa de calor del panel cargue una web ajena.
    ruta = re.sub(r"[^A-Za-z0-9/._~%-]", "", ruta.replace("\\", "/"))
    ruta = "/" + ruta.lstrip("/")
    if ruta.endswith("/index.html"):
        ruta = ruta[: -len("index.html")]
    return ruta


def limpiar_referente(valor, propio: str = "") -> str:
    """Solo el dominio de procedencia; nada de rutas ni parametros que puedan traer datos."""
    texto = _corto(valor, 500)
    if not texto:
        return ""
    dominio = (urlsplit(texto).hostname or "").lower()
    if dominio.startswith("www."):
        dominio = dominio[4:]
    if not dominio or (propio and dominio == propio):
        return ""
    return dominio[:100]


def dispositivo(ancho) -> str:
    n = _numero(ancho, 0, 10000) or 0
    return "movil" if n < 768 else "tablet" if n < 1100 else "escritorio"


def validar_lote(datos) -> dict:
    """Devuelve el lote limpio o levanta ErrorValidacion. Lo que no se entiende se descarta."""
    if not isinstance(datos, dict):
        raise ErrorValidacion("se espera un objeto JSON")
    eventos = datos.get("e", [])
    if not isinstance(eventos, list) or len(eventos) > MAX_EVENTOS:
        raise ErrorValidacion("e: lista de eventos invalida")
    carga = _corto(datos.get("c"), 24)
    if not re.fullmatch(r"[A-Za-z0-9]{6,24}", carga or ""):
        raise ErrorValidacion("c: identificador de carga invalido")
    lote = {
        "carga": carga,
        "ruta": limpiar_ruta(datos.get("p")),
        "disp": dispositivo(datos.get("w")),
        "ancho": int(_numero(datos.get("w"), 0, 10000) or 0),
        "vista": datos.get("v") is True,
        "referente": datos.get("r"),
        "utm": _corto(datos.get("u"), 60).lower(),
        "eventos": [],
        "clics": [],
    }
    for ev in eventos:
        if not isinstance(ev, dict) or ev.get("t") not in TIPOS:
            continue
        tipo = ev["t"]
        if tipo == "clic":
            x, y, alto = _numero(ev.get("x"), 0, 1), _numero(ev.get("y"), 0, 200000), _numero(ev.get("h"), 1, 200000)
            if x is None or y is None or alto is None:
                continue
            lote["clics"].append({"x": round(x, 4), "y": int(y), "alto": int(alto),
                                  "objetivo": _corto(ev.get("o"), 60)})
        else:
            nombre = _corto(ev.get("n"), 40)
            valor = _numero(ev.get("v"), 0, 86400)
            if tipo == "scroll":
                nombre, valor = "", _numero(ev.get("v"), 0, 100)
            if tipo != "scroll" and not nombre:
                continue
            lote["eventos"].append({"tipo": tipo, "nombre": nombre, "valor": valor if valor is not None else 0})
    return lote


# ---------------------------------------------------------------- limitador

class Limitador:
    """Ventanas deslizantes en memoria: N por IP y un techo global."""

    def __init__(self, por_ip=120, ventana_ip=600, maximo_global=20000, ventana_global=3600, reloj=time.monotonic):
        self.por_ip, self.ventana_ip = por_ip, ventana_ip
        self.maximo_global, self.ventana_global = maximo_global, ventana_global
        self.reloj = reloj
        self.ips: dict[str, deque] = {}
        self.todos: deque = deque()
        self.candado = threading.Lock()

    def permitir(self, ip: str) -> bool:
        with self.candado:
            ahora = self.reloj()
            while self.todos and self.todos[0] <= ahora - self.ventana_global:
                self.todos.popleft()
            for clave in [k for k, v in self.ips.items() if not v or v[-1] <= ahora - self.ventana_ip]:
                del self.ips[clave]
            marcas = self.ips.setdefault(ip, deque())
            while marcas and marcas[0] <= ahora - self.ventana_ip:
                marcas.popleft()
            if len(marcas) >= self.por_ip or len(self.todos) >= self.maximo_global:
                return False
            marcas.append(ahora)
            self.todos.append(ahora)
            return True


# ---------------------------------------------------------------- almacen

ESQUEMA = """
CREATE TABLE IF NOT EXISTS vistas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, dia TEXT NOT NULL, visitante TEXT NOT NULL, carga TEXT NOT NULL,
    ruta TEXT NOT NULL, referente TEXT NOT NULL, utm TEXT NOT NULL, disp TEXT NOT NULL, ancho INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vistas_dia ON vistas(dia);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vistas_carga ON vistas(carga);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, dia TEXT NOT NULL, visitante TEXT NOT NULL, carga TEXT NOT NULL,
    ruta TEXT NOT NULL, tipo TEXT NOT NULL, nombre TEXT NOT NULL, valor REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eventos_dia ON eventos(dia, tipo);
CREATE TABLE IF NOT EXISTS clics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, dia TEXT NOT NULL, carga TEXT NOT NULL, ruta TEXT NOT NULL, disp TEXT NOT NULL,
    x REAL NOT NULL, y INTEGER NOT NULL, alto INTEGER NOT NULL, objetivo TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clics_dia ON clics(dia, ruta, disp);
"""


class Almacen:
    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self.candado = threading.Lock()
        with self._conexion() as c:
            c.executescript(ESQUEMA)

    @contextmanager
    def _conexion(self):
        """Transaccion (commit o rollback) y la conexion siempre se cierra."""
        c = sqlite3.connect(self.ruta, timeout=10)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            with c:
                yield c
        finally:
            c.close()

    def guardar(self, lote: dict, visitante: str, referente: str, ahora: float) -> None:
        dia = dia_local(ahora)
        with self.candado, self._conexion() as c:
            if lote["vista"]:
                c.execute("INSERT OR IGNORE INTO vistas (ts, dia, visitante, carga, ruta, referente, utm, disp, ancho)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (ahora, dia, visitante, lote["carga"], lote["ruta"], referente, lote["utm"],
                           lote["disp"], lote["ancho"]))
            c.executemany("INSERT INTO eventos (ts, dia, visitante, carga, ruta, tipo, nombre, valor)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          [(ahora, dia, visitante, lote["carga"], lote["ruta"], e["tipo"], e["nombre"], e["valor"])
                           for e in lote["eventos"]])
            c.executemany("INSERT INTO clics (ts, dia, carga, ruta, disp, x, y, alto, objetivo)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          [(ahora, dia, lote["carga"], lote["ruta"], lote["disp"], k["x"], k["y"], k["alto"],
                            k["objetivo"]) for k in lote["clics"]])

    def purgar(self, dias: int, ahora: float) -> int:
        limite = dia_local(ahora - dias * 86400)
        with self.candado, self._conexion() as c:
            return sum(c.execute(f"DELETE FROM {t} WHERE dia < ?", (limite,)).rowcount
                       for t in ("vistas", "eventos", "clics"))

    # ---- consultas del panel

    def resumen(self, dias: int, ahora: float) -> dict:
        desde = dia_local(ahora - (dias - 1) * 86400)
        with self._conexion() as c:
            def uno(sql, *p):
                return c.execute(sql, (desde, *p)).fetchone()[0] or 0

            # visitantes: unicos por dia, sumados (el hash cambia cada dia a proposito)
            visitantes = uno("SELECT COUNT(*) FROM (SELECT DISTINCT dia, visitante FROM vistas WHERE dia >= ?)")
            vistas = uno("SELECT COUNT(*) FROM vistas WHERE dia >= ?")
            scroll = uno("SELECT AVG(m) FROM (SELECT MAX(valor) m FROM eventos WHERE dia >= ? AND tipo='scroll'"
                         " GROUP BY carga)")
            envios = uno("SELECT COUNT(DISTINCT carga) FROM eventos WHERE dia >= ? AND tipo='form' AND nombre='envio_ok'")
            inicios = uno("SELECT COUNT(DISTINCT carga) FROM eventos WHERE dia >= ? AND tipo='form' AND nombre='inicio'")
            tiempo = uno("SELECT AVG(s) FROM (SELECT SUM(valor) s FROM eventos WHERE dia >= ? AND tipo='seccion'"
                         " GROUP BY carga)")

            serie = {d: {"visitantes": 0, "vistas": 0} for d in
                     (dia_local(ahora - i * 86400) for i in range(dias - 1, -1, -1))}
            for f in c.execute("SELECT dia, COUNT(DISTINCT visitante) v, COUNT(*) n FROM vistas WHERE dia >= ?"
                               " GROUP BY dia", (desde,)):
                if f["dia"] in serie:
                    serie[f["dia"]] = {"visitantes": f["v"], "vistas": f["n"]}

            secciones = [dict(f) for f in c.execute(
                "SELECT nombre, COUNT(DISTINCT carga) cargas, AVG(valor) segundos, MIN(id) orden"
                " FROM eventos WHERE dia >= ? AND tipo='seccion' AND valor >= 0.5 GROUP BY nombre ORDER BY orden",
                (desde,))]
            for s in secciones:
                s["porcentaje"] = round(100 * s["cargas"] / vistas, 1) if vistas else 0
                s["segundos"] = round(s["segundos"] or 0, 1)
                del s["orden"]

            def top(sql, n=8):
                return [dict(f) for f in c.execute(sql + f" LIMIT {n}", (desde,))]

            return {
                "dias": dias,
                "cifras": {
                    "visitantes": visitantes, "vistas": vistas,
                    "scroll_promedio": round(scroll, 1), "tiempo_promedio": round(tiempo, 1),
                    "formularios_iniciados": inicios, "solicitudes": envios,
                    "conversion": round(100 * envios / visitantes, 2) if visitantes else 0,
                },
                "serie": [{"dia": d, **v} for d, v in serie.items()],
                "secciones": secciones,
                "procedencia": top("SELECT CASE WHEN referente='' THEN 'Directo' ELSE referente END nombre,"
                                   " COUNT(*) n FROM vistas WHERE dia >= ? GROUP BY nombre ORDER BY n DESC"),
                "campanas": top("SELECT utm nombre, COUNT(*) n FROM vistas WHERE dia >= ? AND utm != ''"
                                " GROUP BY utm ORDER BY n DESC"),
                "dispositivos": top("SELECT disp nombre, COUNT(*) n FROM vistas WHERE dia >= ? GROUP BY disp"
                                    " ORDER BY n DESC"),
                "paginas": top("SELECT ruta nombre, COUNT(*) n FROM vistas WHERE dia >= ? GROUP BY ruta ORDER BY n DESC"),
                "clics": top("SELECT objetivo nombre, COUNT(*) n FROM clics WHERE dia >= ? AND objetivo != ''"
                             " GROUP BY objetivo ORDER BY n DESC", 10),
            }

    def clics(self, dias: int, disp: str, ruta: str, ahora: float, limite: int = 20000) -> list[list]:
        desde = dia_local(ahora - (dias - 1) * 86400)
        with self._conexion() as c:
            return [[f["x"], f["y"], f["alto"]] for f in c.execute(
                "SELECT x, y, alto FROM clics WHERE dia >= ? AND disp = ? AND ruta = ? ORDER BY id DESC LIMIT ?",
                (desde, disp, ruta, limite))]


# ---------------------------------------------------------------- HTTP

class SalDiaria:
    """hmac(sal, dia): el mismo visitante da el mismo hash solo dentro del dia."""

    def __init__(self, sal: bytes):
        self.sal = sal

    def visitante(self, ip: str, agente: str, ahora: float) -> str:
        sal_dia = hmac.new(self.sal, dia_local(ahora).encode(), hashlib.sha256).digest()
        return hmac.new(sal_dia, f"{ip}|{agente}".encode(), hashlib.sha256).hexdigest()[:20]


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        tipo, error, _ = sys.exc_info()
        if tipo is not None and issubclass(tipo, (BrokenPipeError, ConnectionResetError)):
            registrar("conexion_cortada", error=tipo.__name__)
        else:
            registrar("error_peticion", error=f"{tipo.__name__ if tipo else '?'}: {error}")


def _entero(qs: dict, clave: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        return min(max(int(qs.get(clave, [defecto])[0]), minimo), maximo)
    except ValueError:
        return defecto


def leer_leads(limite: int) -> list:
    """Lee los leads del servicio `leads` (solo lectura) para mostrarlos en el panel."""
    if not LEADS_DB.exists():
        return []
    c = None
    try:
        c = sqlite3.connect(f"file:{LEADS_DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        filas = c.execute(
            "SELECT l.id, l.creado_en, l.nombre, l.empresa, l.email, l.interes, l.duplicado,"
            " group_concat(a.canal || ':' || a.estado, ' ') AS avisos"
            " FROM leads l LEFT JOIN avisos a ON a.lead_id = l.id"
            " GROUP BY l.id ORDER BY l.id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(r) for r in filas]
    except sqlite3.Error:
        return []
    finally:
        if c:
            c.close()


def crear_servidor(almacen: Almacen, limitador: Limitador, sal: SalDiaria, puerto: int, clave: str,
                   dominio: str = "aurea.legal", filtrar_bots: bool = True,
                   usuario: str = "aurea") -> ThreadingHTTPServer:

    class Manejador(BaseHTTPRequestHandler):
        server_version = "analitica"
        sys_version = ""

        def log_message(self, format, *args):
            pass

        def _enviar(self, estado: int, cuerpo: bytes, tipo: str, extra: dict | None = None) -> None:
            self.send_response(estado)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(cuerpo)

        def _json(self, estado: int, datos: dict | list) -> None:
            self._enviar(estado, json.dumps(datos, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _ip(self) -> str:
            reenviado = self.headers.get("X-Forwarded-For", "")
            return reenviado.split(",")[-1].strip() or self.client_address[0]

        def _autorizado(self) -> bool:
            enviado = self.headers.get("Authorization", "")
            if not clave or not enviado.startswith("Basic "):
                return False
            try:
                u, _, p = base64.b64decode(enviado[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                return False
            return hmac.compare_digest(u.encode(), usuario.encode()) & hmac.compare_digest(p.encode(), clave.encode())

        def do_GET(self) -> None:
            partes = urlsplit(self.path)
            ruta, qs = partes.path.rstrip("/") or "/", parse_qs(partes.query)
            if ruta == "/salud":
                almacen.clics(1, "escritorio", "/", time.time(), 1)
                return self._json(200, {"ok": True})
            if not ruta.startswith("/analitica"):
                return self._json(404, {"ok": False, "error": "no encontrado"})
            if not clave:
                return self._json(503, {"ok": False, "error": "falta configurar ANALITICA_CLAVE"})
            if not self._autorizado():
                return self._enviar(401, b"Se requiere usuario y clave.", "text/plain; charset=utf-8",
                                    {"WWW-Authenticate": 'Basic realm="Analitica aurea.legal", charset="UTF-8"'})
            ahora = time.time()
            if ruta == "/analitica":
                return self._enviar(200, PANEL.read_bytes(), "text/html; charset=utf-8",
                                    {"X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
            if ruta == "/analitica/api/resumen":
                return self._json(200, almacen.resumen(_entero(qs, "dias", 30, 1, 400), ahora))
            if ruta == "/analitica/api/clics":
                disp = qs.get("disp", ["escritorio"])[0]
                if disp not in DISPOSITIVOS:
                    return self._json(400, {"ok": False, "error": "disp invalido"})
                return self._json(200, {"puntos": almacen.clics(_entero(qs, "dias", 30, 1, 400), disp,
                                                                limpiar_ruta(qs.get("ruta", ["/"])[0]), ahora)})
            if ruta == "/analitica/api/leads":
                return self._json(200, {"leads": leer_leads(_entero(qs, "limite", 50, 1, 500))})
            self._json(404, {"ok": False, "error": "no encontrado"})

        do_HEAD = do_GET

        def do_POST(self) -> None:
            if urlsplit(self.path).path != RUTA_EVENTOS:
                return self._json(404, {"ok": False, "error": "no encontrado"})
            # sendBeacon manda text/plain; se aceptan los dos
            tipo = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if tipo not in ("application/json", "text/plain"):
                return self._json(415, {"ok": False, "error": "se espera JSON"})
            try:
                largo = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                largo = -1
            if largo < 0 or largo > MAX_CUERPO:
                self.close_connection = True
                return self._json(413, {"ok": False, "error": "solicitud demasiado grande"})
            cuerpo = self.rfile.read(largo)
            agente = self.headers.get("User-Agent", "")
            if filtrar_bots and (not agente or BOTS.search(agente)):
                return self._enviar(204, b"", "text/plain")
            ip = self._ip()
            if not limitador.permitir(ip):
                return self._json(429, {"ok": False, "error": "demasiadas solicitudes"})
            try:
                lote = validar_lote(json.loads(cuerpo or b"null"))
            except (ValueError, UnicodeDecodeError) as e:
                mensaje = str(e) if isinstance(e, ErrorValidacion) else "JSON invalido"
                return self._json(400, {"ok": False, "error": mensaje})
            ahora = time.time()
            try:
                almacen.guardar(lote, sal.visitante(ip, agente, ahora), limpiar_referente(lote["referente"], dominio),
                                ahora)
            except sqlite3.Error as e:
                registrar("error_guardando", error=type(e).__name__)
                return self._json(503, {"ok": False, "error": "no se pudo guardar"})
            self._enviar(204, b"", "text/plain")

    return Servidor(("0.0.0.0", puerto), Manejador)


# ---------------------------------------------------------------- arranque

def leer_sal(directorio: Path) -> bytes:
    ruta = directorio / "sal"
    if not ruta.exists():
        ruta.write_text(secrets.token_hex(32))
        ruta.chmod(0o600)
    return ruta.read_text().strip().encode()


def purgador(almacen: Almacen, dias: int, cada: float = 6 * 3600) -> None:
    while True:
        try:
            borradas = almacen.purgar(dias, time.time())
            if borradas:
                registrar("purga", filas=borradas, dias=dias)
        except sqlite3.Error as e:
            registrar("error_purga", error=type(e).__name__)
        time.sleep(cada)


def main() -> None:
    e = os.environ.get
    datos = Path(e("ANALITICA_DATOS", "/datos"))
    puerto = int(e("ANALITICA_PUERTO", "8082"))
    clave = e("ANALITICA_CLAVE", "")
    retencion = int(e("ANALITICA_RETENCION_DIAS", "395"))
    almacen = Almacen(datos / "analitica.db")
    threading.Thread(target=purgador, args=(almacen, retencion), daemon=True).start()
    servidor = crear_servidor(almacen, Limitador(), SalDiaria(leer_sal(datos)), puerto, clave,
                              dominio=e("ANALITICA_DOMINIO", "aurea.legal"),
                              filtrar_bots=e("ANALITICA_FILTRAR_BOTS", "1") != "0")
    registrar("arranque", puerto=puerto, panel=bool(clave), retencion_dias=retencion)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
