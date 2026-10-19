#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metricas: cuantas personas entran a aurea.legal, de donde vienen y donde hacen clic.

Solo biblioteca estandar, igual que leads: nada que instalar ni actualizar.

    GET  /api/m.js                 el script que mide (lo carga la pagina)
    POST /api/m                    lotes de eventos del script; 204 siempre que no haya error
    GET  /metricas/                panel (solo con la puerta: cabecera X-Puerta-Usuario)
    GET  /metricas/api/resumen     ?dias=N
    GET  /metricas/api/clics       ?dias=N&disp=escritorio|tablet|movil&ruta=/
    GET  /salud

Privacidad (Ley 1581): sin cookies y sin guardar la IP. Cada visitante se cuenta con un hash de
IP + navegador y una sal que cambia cada dia y se borra a los dos dias: se pueden contar personas
unicas por dia, pero nadie (ni nosotros) puede seguir a alguien de un dia a otro ni volver a la IP.
El script no mide si el navegador pide "no rastrear" (DNT / GPC) y nunca lee lo que se escribe
en los campos.

Todo vive en el directorio montado (`METRICAS_DATOS`, por defecto `/datos`): metricas.db (SQLite).
"""

from __future__ import annotations

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
import unicodedata
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RUTA = "/api/m"
RUTA_SCRIPT = "/api/m.js"
PANEL = "/metricas"
MAX_CUERPO = 16 * 1024
MAX_EVENTOS = 50
MAX_TEXTO = 80
MAX_RUTA = 200
RETENCION_DIAS = 395
SESION = re.compile(r"^[0-9a-f]{16,32}$")
DOMINIO_PROPIO = "aurea.legal"
HORA_LOCAL = timezone(timedelta(hours=-5), "COT")
ETAPAS_FORM = ("", "inicio", "enviado")
BOT = re.compile(r"bot|crawl|spider|slurp|headless|preview|facebookexternalhit|curl|wget|python|"
                 r"monitor|lighthouse|pingdom|uptime", re.I)
RAIZ = Path(__file__).resolve().parent


def registrar(evento: str, **datos) -> None:
    """Una linea JSON por evento a stderr (`docker logs`). Nunca datos de visitantes."""
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "evento": evento, **datos}, ensure_ascii=False), file=sys.stderr, flush=True)


def dia_local(ts: float) -> str:
    return datetime.fromtimestamp(ts, HORA_LOCAL).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- normalizacion

class ErrorValidacion(ValueError):
    pass


def _texto(valor, maximo: int = MAX_TEXTO) -> str:
    if not isinstance(valor, str):
        return ""
    valor = "".join(c for c in valor if unicodedata.category(c)[0] != "C")
    return re.sub(r"\s{2,}", " ", valor).strip()[:maximo]


def _ruta(valor) -> str:
    ruta = _texto(valor, 2000).split("?")[0].split("#")[0].replace("\\", "/")
    if not ruta.startswith("/"):
        return "/"
    # "//otro.com" es una URL a otro dominio para el navegador (y el panel la usa en un iframe)
    return re.sub(r"^/+", "/", ruta)[:MAX_RUTA]


def _entero(valor, minimo: int, maximo: int) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return int(min(max(valor, minimo), maximo))


def _fraccion(valor) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor != valor:
        return None
    return round(min(max(float(valor), 0.0), 1.0), 4)


def _evento(e) -> dict | None:
    if not isinstance(e, dict):
        return None
    k = e.get("k")
    ancho = _entero(e.get("w"), 0, 10_000) or 0
    if k == "v":
        return {"k": "v", "p": _ruta(e.get("p")), "r": _texto(e.get("r"), 500),
                "u": _texto(e.get("u"), 40).lower(), "w": ancho}
    if k == "c":
        x, y = _fraccion(e.get("x")), _fraccion(e.get("y"))
        if x is None or y is None:
            return None
        return {"k": "c", "p": _ruta(e.get("p")), "sec": _texto(e.get("sec")) or "pagina",
                "x": x, "y": y, "w": ancho, "l": _texto(e.get("l"))}
    if k == "s":
        d = _entero(e.get("d"), 0, 100)
        return None if d is None else {"k": "s", "p": _ruta(e.get("p")), "d": d}
    if k == "x":
        ms, sec = _entero(e.get("ms"), 0, 3_600_000), _texto(e.get("sec"))
        return None if ms is None or not sec else {"k": "x", "p": _ruta(e.get("p")), "sec": sec, "ms": ms}
    if k == "f":
        return {"k": "f", "e": e["e"]} if e.get("e") in ETAPAS_FORM[1:] else None
    if k == "h":
        return {"k": "h"}
    return None


def validar_lote(datos) -> tuple[str, list[dict]]:
    """Devuelve (sesion, eventos limpios). Los eventos rotos se descartan sin fallar el lote."""
    if not isinstance(datos, dict):
        raise ErrorValidacion("se espera un objeto JSON")
    sid = datos.get("s")
    if not isinstance(sid, str) or not SESION.match(sid):
        raise ErrorValidacion("sesion invalida")
    eventos = datos.get("t")
    if not isinstance(eventos, list):
        raise ErrorValidacion("faltan eventos")
    if len(eventos) > MAX_EVENTOS:
        raise ErrorValidacion("demasiados eventos")
    return sid, [ev for ev in map(_evento, eventos) if ev is not None]


def dispositivo(ancho: int) -> str:
    if ancho and ancho < 768:
        return "movil"
    if ancho and ancho < 1024:
        return "tablet"
    return "escritorio"


RANGOS = {"movil": (1, 767), "tablet": (768, 1023), "escritorio": (1024, 10_000)}


def referente(url: str, utm: str) -> str:
    if utm:
        return "utm:" + re.sub(r"[^a-z0-9_.-]", "", utm.lower())[:40]
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    if not host or "." not in host or host == DOMINIO_PROPIO or host.endswith("." + DOMINIO_PROPIO):
        return "directo"
    for prefijo in ("www.", "lm.", "l.", "m.", "mobile."):
        if host.startswith(prefijo):
            host = host[len(prefijo):]
            break
    return host[:80]


def es_bot(ua: str) -> bool:
    return not ua or bool(BOT.search(ua))


def navegador(ua: str) -> str:
    if "Edg/" in ua:
        return "Edge"
    if "OPR/" in ua:
        return "Opera"
    if "Firefox/" in ua:
        return "Firefox"
    if "Chrome/" in ua or "CriOS/" in ua:
        return "Chrome"
    if "Safari/" in ua:
        return "Safari"
    return "Otro"


# ---------------------------------------------------------------- limitador

class Limitador:
    """Ventana deslizante en memoria por IP (los lotes llegan cada ~15 s por pestaña abierta)."""

    def __init__(self, por_ip=240, ventana_ip=600, reloj=time.monotonic):
        self.por_ip, self.ventana_ip, self.reloj = por_ip, ventana_ip, reloj
        self.ips: dict[str, deque] = {}
        self.candado = threading.Lock()

    def permitir(self, ip: str) -> bool:
        with self.candado:
            ahora = self.reloj()
            for clave in [k for k, v in self.ips.items() if not v or v[-1] <= ahora - self.ventana_ip]:
                del self.ips[clave]
            marcas = self.ips.setdefault(ip, deque())
            while marcas and marcas[0] <= ahora - self.ventana_ip:
                marcas.popleft()
            if len(marcas) >= self.por_ip:
                return False
            marcas.append(ahora)
            return True


# ---------------------------------------------------------------- almacen

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sales (dia TEXT PRIMARY KEY, sal TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sesiones (
    id TEXT PRIMARY KEY,
    dia TEXT NOT NULL,
    visitante TEXT NOT NULL,
    inicio REAL NOT NULL,
    ultimo REAL NOT NULL,
    entrada TEXT NOT NULL,
    referente TEXT NOT NULL,
    dispositivo TEXT NOT NULL,
    navegador TEXT NOT NULL,
    paginas INTEGER NOT NULL DEFAULT 1,
    scroll_max INTEGER NOT NULL DEFAULT 0,
    formulario TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sesiones_inicio ON sesiones(inicio);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion TEXT NOT NULL REFERENCES sesiones(id),
    ts REAL NOT NULL,
    tipo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    seccion TEXT NOT NULL DEFAULT '',
    x REAL,
    y REAL,
    ancho INTEGER NOT NULL DEFAULT 0,
    dato TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON eventos(tipo, ts);
CREATE INDEX IF NOT EXISTS idx_eventos_sesion ON eventos(sesion);
"""


class Almacen:
    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self.candado = threading.Lock()
        with self._conexion() as c:
            c.executescript(ESQUEMA)

    def _conexion(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.ruta, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # -- visitantes anonimos

    def _sal(self, c: sqlite3.Connection, dia: str) -> bytes:
        fila = c.execute("SELECT sal FROM sales WHERE dia = ?", (dia,)).fetchone()
        if fila is None:
            c.execute("INSERT INTO sales (dia, sal) VALUES (?, ?)", (dia, secrets.token_hex(32)))
            ayer = (datetime.strptime(dia, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            c.execute("DELETE FROM sales WHERE dia < ?", (ayer,))
            return self._sal(c, dia)
        return fila["sal"].encode()

    def _visitante(self, c: sqlite3.Connection, ip: str, ua: str, ahora: float) -> str:
        return hmac.new(self._sal(c, dia_local(ahora)), f"{ip}|{ua}".encode(), hashlib.sha256).hexdigest()[:20]

    def visitante(self, ip: str, ua: str, ahora: float) -> str:
        with self.candado, self._conexion() as c:
            return self._visitante(c, ip, ua, ahora)

    def dias_con_sal(self) -> list[str]:
        with self._conexion() as c:
            return [f["dia"] for f in c.execute("SELECT dia FROM sales ORDER BY dia")]

    # -- escritura

    def registrar(self, sid: str, eventos: list[dict], ip: str, ua: str, ahora: float) -> None:
        with self.candado, self._conexion() as c:
            existe = c.execute("SELECT 1 FROM sesiones WHERE id = ?", (sid,)).fetchone() is not None
            for e in eventos:
                k = e["k"]
                if k == "v":
                    if existe:
                        c.execute("UPDATE sesiones SET paginas = paginas + 1, ultimo = ? WHERE id = ?", (ahora, sid))
                    else:
                        c.execute(
                            "INSERT INTO sesiones (id, dia, visitante, inicio, ultimo, entrada, referente,"
                            " dispositivo, navegador) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (sid, dia_local(ahora), self._visitante(c, ip, ua, ahora), ahora, ahora, e["p"],
                             referente(e["r"], e["u"]), dispositivo(e["w"]), navegador(ua)))
                        existe = True
                    c.execute("INSERT INTO eventos (sesion, ts, tipo, ruta, ancho) VALUES (?, ?, 'vista', ?, ?)",
                              (sid, ahora, e["p"], e["w"]))
                    continue
                if not existe:      # sin vista previa no hay a quien sumarle el evento
                    continue
                c.execute("UPDATE sesiones SET ultimo = ? WHERE id = ?", (ahora, sid))
                if k == "c":
                    c.execute("INSERT INTO eventos (sesion, ts, tipo, ruta, seccion, x, y, ancho, dato)"
                              " VALUES (?, ?, 'clic', ?, ?, ?, ?, ?, ?)",
                              (sid, ahora, e["p"], e["sec"], e["x"], e["y"], e["w"], e["l"]))
                elif k == "s":
                    c.execute("UPDATE sesiones SET scroll_max = MAX(scroll_max, ?) WHERE id = ?", (e["d"], sid))
                elif k == "x":
                    c.execute("INSERT INTO eventos (sesion, ts, tipo, ruta, seccion, dato)"
                              " VALUES (?, ?, 'seccion', ?, ?, ?)", (sid, ahora, e["p"], e["sec"], str(e["ms"])))
                elif k == "f":
                    previo = c.execute("SELECT formulario FROM sesiones WHERE id = ?", (sid,)).fetchone()[0]
                    if ETAPAS_FORM.index(e["e"]) > ETAPAS_FORM.index(previo):
                        c.execute("UPDATE sesiones SET formulario = ? WHERE id = ?", (e["e"], sid))

    def purgar(self, antes_de: float) -> int:
        with self.candado, self._conexion() as c:
            viejas = "SELECT id FROM sesiones WHERE ultimo < ?"
            c.execute(f"DELETE FROM eventos WHERE sesion IN ({viejas})", (antes_de,))
            return c.execute("DELETE FROM sesiones WHERE ultimo < ?", (antes_de,)).rowcount

    # -- lectura

    def sesion(self, sid: str) -> dict | None:
        with self._conexion() as c:
            fila = c.execute("SELECT * FROM sesiones WHERE id = ?", (sid,)).fetchone()
            return dict(fila) if fila else None

    def resumen(self, desde: float, hasta: float) -> dict:
        rango = (desde, hasta)
        en_rango = "SELECT id FROM sesiones WHERE inicio BETWEEN ? AND ?"
        with self._conexion() as c:
            def uno(sql, args=rango):
                return c.execute(sql, args).fetchone()[0]

            def lista(sql, args=rango):
                return [dict(f) for f in c.execute(sql, args)]

            sesiones = uno("SELECT COUNT(*) FROM sesiones WHERE inicio BETWEEN ? AND ?")
            rebotes = uno(
                "SELECT COUNT(*) FROM sesiones s WHERE inicio BETWEEN ? AND ? AND paginas = 1"
                " AND scroll_max < 25 AND ultimo - inicio < 10"
                " AND NOT EXISTS (SELECT 1 FROM eventos e WHERE e.sesion = s.id AND e.tipo = 'clic')")
            agrupar = ("SELECT {col} AS nombre, COUNT(*) AS n FROM sesiones WHERE inicio BETWEEN ? AND ?"
                       " GROUP BY {col} ORDER BY n DESC LIMIT 15")
            return {
                "desde": desde, "hasta": hasta,
                "visitantes": uno("SELECT COUNT(DISTINCT visitante) FROM sesiones WHERE inicio BETWEEN ? AND ?"),
                "sesiones": sesiones,
                "vistas": uno(f"SELECT COUNT(*) FROM eventos WHERE tipo = 'vista' AND sesion IN ({en_rango})"),
                "duracion_media_s": round(uno(
                    "SELECT COALESCE(AVG(ultimo - inicio), 0) FROM sesiones WHERE inicio BETWEEN ? AND ?")),
                "rebote_pct": round(100 * rebotes / sesiones) if sesiones else 0,
                "formulario": {
                    "inicio": uno("SELECT COUNT(*) FROM sesiones WHERE inicio BETWEEN ? AND ? AND formulario != ''"),
                    "enviado": uno("SELECT COUNT(*) FROM sesiones WHERE inicio BETWEEN ? AND ?"
                                   " AND formulario = 'enviado'"),
                },
                "por_dia": lista("SELECT dia, COUNT(DISTINCT visitante) AS visitantes, COUNT(*) AS sesiones"
                                 " FROM sesiones WHERE inicio BETWEEN ? AND ? GROUP BY dia ORDER BY dia"),
                "referentes": lista(agrupar.format(col="referente")),
                "dispositivos": lista(agrupar.format(col="dispositivo")),
                "navegadores": lista(agrupar.format(col="navegador")),
                "entradas": lista(agrupar.format(col="entrada")),
                "paginas": lista(f"SELECT ruta AS nombre, COUNT(*) AS n FROM eventos WHERE tipo = 'vista'"
                                 f" AND sesion IN ({en_rango}) GROUP BY ruta ORDER BY n DESC LIMIT 15"),
                "secciones": lista(
                    f"SELECT seccion, COUNT(*) AS sesiones, CAST(AVG(total) AS INTEGER) AS ms_medio FROM ("
                    f" SELECT sesion, seccion, SUM(CAST(dato AS INTEGER)) AS total FROM eventos"
                    f" WHERE tipo = 'seccion' AND sesion IN ({en_rango}) GROUP BY sesion, seccion)"
                    f" GROUP BY seccion ORDER BY sesiones DESC, ms_medio DESC"),
                "scroll": {str(p): uno(f"SELECT COUNT(*) FROM sesiones WHERE inicio BETWEEN ? AND ?"
                                       f" AND scroll_max >= {p}") for p in (25, 50, 75, 100)},
                "clics_top": lista(
                    f"SELECT seccion, dato AS etiqueta, COUNT(*) AS n FROM eventos WHERE tipo = 'clic'"
                    f" AND dato != '' AND sesion IN ({en_rango}) GROUP BY seccion, dato ORDER BY n DESC LIMIT 15"),
            }

    def clics(self, desde: float, hasta: float, disp: str, ruta: str, limite: int = 20_000) -> list[dict]:
        minimo, maximo = RANGOS.get(disp, RANGOS["escritorio"])
        with self._conexion() as c:
            filas = c.execute(
                "SELECT seccion AS sec, x, y FROM eventos WHERE tipo = 'clic' AND ts BETWEEN ? AND ?"
                " AND ruta = ? AND ancho BETWEEN ? AND ? ORDER BY id DESC LIMIT ?",
                (desde, hasta, ruta, minimo, maximo, limite))
            return [dict(f) for f in filas]


# ---------------------------------------------------------------- HTTP

class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        tipo, error, _ = sys.exc_info()
        if tipo is not None and issubclass(tipo, (BrokenPipeError, ConnectionResetError)):
            return
        registrar("error_peticion", error=f"{tipo.__name__ if tipo else '?'}: {error}")


def crear_servidor(almacen: Almacen, limitador: Limitador, puerto: int, panel_abierto: bool = False,
                   reloj=time.time) -> ThreadingHTTPServer:

    class Manejador(BaseHTTPRequestHandler):
        server_version = "metricas"
        sys_version = ""

        def log_message(self, format, *args):
            pass

        def _enviar(self, estado: int, cuerpo: bytes = b"", tipo: str = "", cache: str = "no-store") -> None:
            self.send_response(estado)
            if tipo:
                self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if cuerpo:
                self.wfile.write(cuerpo)

        def _json(self, estado: int, datos: dict) -> None:
            self._enviar(estado, json.dumps(datos, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _ip(self) -> str:
            # Caddy agrega la IP real al final de X-Forwarded-For; lo anterior lo manda el cliente
            return self.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or self.client_address[0]

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/salud":
                almacen.dias_con_sal()
                return self._json(200, {"ok": True})
            if url.path == RUTA_SCRIPT:
                return self._enviar(200, (RAIZ / "m.js").read_bytes(), "text/javascript; charset=utf-8",
                                    cache="public, max-age=3600")
            if url.path == PANEL or url.path.startswith(PANEL + "/"):
                return self._panel(url)
            self._json(404, {"ok": False, "error": "no encontrado"})

        def _panel(self, url) -> None:
            # La puerta (forward_auth de Caddy) pone esta cabecera; sin ella, nadie ve el panel
            if not panel_abierto and not self.headers.get("X-Puerta-Usuario"):
                return self._json(401, {"ok": False, "error": "requiere iniciar sesion"})
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                dias = min(max(int(q.get("dias", "7")), 1), RETENCION_DIAS)
            except ValueError:
                dias = 7
            hasta = reloj()
            desde = hasta - dias * 86_400
            if url.path in (PANEL, PANEL + "/"):
                return self._enviar(200, (RAIZ / "panel.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == PANEL + "/api/resumen":
                return self._json(200, almacen.resumen(desde, hasta))
            if url.path == PANEL + "/api/clics":
                puntos = almacen.clics(desde, hasta, q.get("disp", "escritorio"), _ruta(q.get("ruta", "/")))
                return self._json(200, {"puntos": puntos})
            self._json(404, {"ok": False, "error": "no encontrado"})

        def do_POST(self) -> None:
            if urlparse(self.path).path != RUTA:
                return self._json(404, {"ok": False, "error": "no encontrado"})
            tipo = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if tipo not in ("text/plain", "application/json"):   # sendBeacon manda text/plain
                return self._json(415, {"ok": False, "error": "tipo no soportado"})
            try:
                largo = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                largo = -1
            if largo < 0 or largo > MAX_CUERPO:
                self.close_connection = True
                return self._json(413, {"ok": False, "error": "lote demasiado grande"})
            cuerpo = self.rfile.read(largo)
            ua = self.headers.get("User-Agent", "")
            if es_bot(ua):
                return self._enviar(204)
            if not limitador.permitir(self._ip()):
                return self._json(429, {"ok": False, "error": "demasiadas solicitudes"})
            try:
                sid, eventos = validar_lote(json.loads(cuerpo or b"null"))
            except (ValueError, UnicodeDecodeError) as e:
                mensaje = str(e) if isinstance(e, ErrorValidacion) else "JSON invalido"
                return self._json(400, {"ok": False, "error": mensaje})
            try:
                almacen.registrar(sid, eventos, self._ip(), ua[:300], reloj())
            except sqlite3.Error as e:
                registrar("error_guardando", error=type(e).__name__)
                return self._json(503, {"ok": False, "error": "no se pudo guardar"})
            self._enviar(204)

    return Servidor(("0.0.0.0", puerto), Manejador)


# ---------------------------------------------------------------- arranque

def limpiador(almacen: Almacen, cada: float = 6 * 3600) -> None:
    while True:
        try:
            borradas = almacen.purgar(time.time() - RETENCION_DIAS * 86_400)
            if borradas:
                registrar("purga", sesiones=borradas)
        except sqlite3.Error as e:
            registrar("error_purga", error=type(e).__name__)
        time.sleep(cada)


def main() -> None:
    datos = Path(os.environ.get("METRICAS_DATOS", "/datos"))
    puerto = int(os.environ.get("METRICAS_PUERTO", "8082"))
    abierto = os.environ.get("METRICAS_PANEL_ABIERTO") == "1"     # solo para probar en local
    almacen = Almacen(datos / "metricas.db")
    threading.Thread(target=limpiador, args=(almacen,), daemon=True).start()
    servidor = crear_servidor(almacen, Limitador(), puerto, panel_abierto=abierto)
    registrar("arranque", puerto=puerto, panel_abierto=abierto)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
