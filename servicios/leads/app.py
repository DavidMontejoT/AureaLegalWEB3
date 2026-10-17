#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Leads: recibe el formulario de aurea.legal, lo guarda y avisa.

Solo biblioteca estandar, a proposito: no suma dependencias a la imagen.

    POST /api/demo-request   {nombre, email, empresa?, interes?, consentimiento: true}
                             200 guardado | 400 invalido | 413 grande | 415 no JSON | 429 limite
    GET  /salud              200 si el proceso y la base responden

Regla de oro: **primero se guarda, despues se avisa**. El POST responde en cuanto el lead
esta en disco; los avisos (Telegram y correo) los manda un hilo aparte desde una cola en la
misma base, con reintentos que sobreviven a reinicios. Un canal caido o sin configurar nunca
pierde un lead: el aviso espera.

Todo vive FUERA del repositorio, en el directorio montado (`LEADS_DATOS`, por defecto `/datos`):

    leads.db   SQLite: tablas `leads` y `avisos`
    sal        con la que se hashea la IP (se crea sola); la IP en claro no se guarda
"""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import re
import secrets
import smtplib
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUTA = "/api/demo-request"
MAX_CUERPO = 8 * 1024
LARGOS = {"nombre": 120, "empresa": 160, "interes": 300}
MAX_EMAIL = 254
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VENTANA_DUPLICADO = 600
CANALES = ("telegram", "email")
ESPERAS = (60, 300, 1800, 7200, 21600)   # segundos antes de cada reintento; el ultimo se repite
MAX_INTENTOS = 12
HORA_LOCAL = timezone(timedelta(hours=-5), "COT")


def registrar(evento: str, **datos) -> None:
    """Una linea JSON por evento a stderr (`docker logs`). Nunca datos del lead."""
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "evento": evento, **datos}, ensure_ascii=False), file=sys.stderr, flush=True)


# ---------------------------------------------------------------- validacion

class ErrorValidacion(ValueError):
    pass


def _texto(datos: dict, campo: str, maximo: int, requerido: bool) -> str:
    valor = datos.get(campo, "")
    if valor is None:
        valor = ""
    if not isinstance(valor, str):
        raise ErrorValidacion(f"{campo}: debe ser texto")
    if "\r" in valor or "\n" in valor:
        raise ErrorValidacion(f"{campo}: no admite saltos de linea")
    valor = "".join(c for c in valor if unicodedata.category(c) != "Cc")
    valor = re.sub(r"\s{2,}", " ", valor).strip()
    if requerido and not valor:
        raise ErrorValidacion(f"{campo}: es requerido")
    if len(valor) > maximo:
        raise ErrorValidacion(f"{campo}: maximo {maximo} caracteres")
    return valor


def validar(datos: dict) -> dict:
    """Devuelve el lead limpio o levanta ErrorValidacion con un mensaje para el usuario."""
    if datos.get("consentimiento") is not True:
        raise ErrorValidacion("consentimiento: se requiere la autorizacion de tratamiento de datos")
    email = _texto(datos, "email", MAX_EMAIL, requerido=True).lower()
    if not EMAIL.match(email):
        raise ErrorValidacion("email: formato invalido")
    return {
        "nombre": _texto(datos, "nombre", LARGOS["nombre"], requerido=True),
        "email": email,
        "empresa": _texto(datos, "empresa", LARGOS["empresa"], requerido=False),
        "interes": _texto(datos, "interes", LARGOS["interes"], requerido=False),
    }


# ---------------------------------------------------------------- limitador

class Limitador:
    """Ventanas deslizantes en memoria: N por IP y un techo global."""

    def __init__(self, por_ip=5, ventana_ip=600, maximo_global=100, ventana_global=3600, reloj=time.monotonic):
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
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creado_en REAL NOT NULL,
    nombre TEXT NOT NULL,
    email TEXT NOT NULL,
    empresa TEXT NOT NULL,
    interes TEXT NOT NULL,
    consentimiento_en REAL NOT NULL,
    ip_hash TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    duplicado INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email, creado_en);
CREATE TABLE IF NOT EXISTS avisos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    canal TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    intentos INTEGER NOT NULL DEFAULT 0,
    proximo_en REAL NOT NULL,
    ultimo_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_avisos_cola ON avisos(estado, proximo_en);
"""


class Almacen:
    def __init__(self, ruta: Path, canales=CANALES):
        self.ruta = Path(ruta)
        self.canales = tuple(canales)
        self.candado = threading.Lock()
        with self._conexion() as c:
            c.executescript(ESQUEMA)

    def _conexion(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.ruta, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")
        return c

    def guardar(self, lead: dict, ip_hash: str, user_agent: str, ahora: float) -> tuple[int, bool]:
        """Inserta el lead (y sus avisos, si no es duplicado) en una sola transaccion."""
        with self.candado, self._conexion() as c:
            previo = c.execute(
                "SELECT 1 FROM leads WHERE email = ? AND duplicado = 0 AND creado_en > ?",
                (lead["email"], ahora - VENTANA_DUPLICADO),
            ).fetchone()
            duplicado = previo is not None
            cur = c.execute(
                "INSERT INTO leads (creado_en, nombre, email, empresa, interes, consentimiento_en,"
                " ip_hash, user_agent, duplicado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ahora, lead["nombre"], lead["email"], lead["empresa"], lead["interes"], ahora,
                 ip_hash, user_agent[:300], int(duplicado)),
            )
            lead_id = cur.lastrowid
            if not duplicado:
                c.executemany("INSERT INTO avisos (lead_id, canal, proximo_en) VALUES (?, ?, ?)",
                              [(lead_id, canal, ahora) for canal in self.canales])
        return lead_id, duplicado

    def leads(self) -> list[dict]:
        with self._conexion() as c:
            return [dict(f) for f in c.execute("SELECT * FROM leads ORDER BY id")]

    def pendientes(self, ahora: float) -> list[dict]:
        with self._conexion() as c:
            filas = c.execute(
                "SELECT a.id AS aviso_id, a.canal, a.intentos, a.ultimo_error, l.*"
                " FROM avisos a JOIN leads l ON l.id = a.lead_id"
                " WHERE a.estado = 'pendiente' AND a.proximo_en <= ? ORDER BY a.id",
                (ahora,),
            )
            return [dict(f) for f in filas]

    def marcar_enviado(self, aviso_id: int) -> None:
        with self.candado, self._conexion() as c:
            c.execute("UPDATE avisos SET estado = 'enviado', ultimo_error = '' WHERE id = ?", (aviso_id,))

    def marcar_fallo(self, aviso_id: int, intentos_previos: int, error: str, ahora: float) -> None:
        intentos = intentos_previos + 1
        estado = "fallido" if intentos >= MAX_INTENTOS else "pendiente"
        espera = ESPERAS[min(intentos - 1, len(ESPERAS) - 1)]
        with self.candado, self._conexion() as c:
            c.execute("UPDATE avisos SET intentos = ?, estado = ?, proximo_en = ?, ultimo_error = ?"
                      " WHERE id = ?", (intentos, estado, ahora + espera, error[:500], aviso_id))

    def resumen_avisos(self) -> dict[str, int]:
        resumen = {"pendiente": 0, "enviado": 0, "fallido": 0}
        with self._conexion() as c:
            for estado, n in c.execute("SELECT estado, COUNT(*) FROM avisos GROUP BY estado"):
                resumen[estado] = n
        return resumen


def procesar_pendientes(almacen: Almacen, canales: dict, ahora: float) -> dict[str, int]:
    """Una pasada por la cola. Un canal sin configurar no gasta intentos: el aviso espera."""
    res = {"enviados": 0, "fallos": 0, "en_espera": 0}
    for aviso in almacen.pendientes(ahora):
        canal = canales.get(aviso["canal"])
        if canal is None or not canal.disponible():
            res["en_espera"] += 1
            continue
        try:
            canal.enviar(aviso)
        except Exception as e:  # cualquier fallo del canal se reintenta, no tumba el hilo
            almacen.marcar_fallo(aviso["aviso_id"], aviso["intentos"], f"{type(e).__name__}: {e}", ahora)
            registrar("aviso_fallo", canal=aviso["canal"], lead=aviso["id"], intento=aviso["intentos"] + 1,
                      error=type(e).__name__)
            res["fallos"] += 1
        else:
            almacen.marcar_enviado(aviso["aviso_id"])
            registrar("aviso_enviado", canal=aviso["canal"], lead=aviso["id"])
            res["enviados"] += 1
    return res


# ---------------------------------------------------------------- mensajes y canales

def _fecha(ts: float) -> str:
    return datetime.fromtimestamp(ts, HORA_LOCAL).strftime("%Y-%m-%d %H:%M (hora Colombia)")


def texto_aviso(lead: dict) -> str:
    return "\n".join([
        f"Nuevo lead #{lead['id']} — aurea.legal",
        "",
        f"Nombre: {lead['nombre']}",
        f"Empresa: {lead['empresa'] or '—'}",
        f"Email: {lead['email']}",
        f"Interés: {lead['interes'] or '—'}",
        f"Fecha: {_fecha(lead['creado_en'])}",
    ])


def mensaje_correo(lead: dict, remitente: str, destino: str) -> EmailMessage:
    msj = EmailMessage()
    empresa = f" ({lead['empresa']})" if lead["empresa"] else ""
    msj["Subject"] = f"Nuevo lead: {lead['nombre']}{empresa}"
    msj["From"] = remitente
    msj["To"] = destino
    msj["Reply-To"] = lead["email"]
    msj.set_content(texto_aviso(lead) + "\n\nResponde este correo para escribirle directo al lead.\n")
    return msj


def _post_json(url: str, cuerpo: bytes) -> None:
    req = urllib.request.Request(url, data=cuerpo, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        respuesta = json.loads(r.read() or b"{}")
    if not respuesta.get("ok"):
        raise RuntimeError(f"telegram respondio {respuesta.get('error_code')}")


class Telegram:
    def __init__(self, token: str, chat_id: str, enviar_http=_post_json):
        self.token, self.chat_id, self.enviar_http = token, chat_id, enviar_http

    def disponible(self) -> bool:
        return bool(self.token and self.chat_id)

    def enviar(self, lead: dict) -> None:
        # texto plano a proposito: sin parse_mode, el nombre del lead no puede meter formato ni links
        cuerpo = json.dumps({"chat_id": self.chat_id, "text": texto_aviso(lead),
                             "disable_web_page_preview": True}).encode()
        self.enviar_http(f"https://api.telegram.org/bot{self.token}/sendMessage", cuerpo)


class Correo:
    def __init__(self, config: dict):
        self.config = config

    def disponible(self) -> bool:
        return all(self.config.get(k) for k in ("host", "remitente", "destino"))

    def enviar(self, lead: dict) -> None:
        c = self.config
        msj = mensaje_correo(lead, remitente=c["remitente"], destino=c["destino"])
        with smtplib.SMTP(c["host"], int(c.get("puerto") or 587), timeout=20) as smtp:
            smtp.starttls()
            if c.get("usuario"):
                smtp.login(c["usuario"], c["clave"])
            smtp.send_message(msj)


# ---------------------------------------------------------------- HTTP

def hash_ip(sal: bytes, ip: str) -> str:
    return hmac.new(sal, ip.encode(), hashlib.sha256).hexdigest()[:32]


def crear_servidor(almacen: Almacen, limitador: Limitador, sal: bytes, puerto: int,
                   al_guardar=lambda: None) -> ThreadingHTTPServer:

    class Manejador(BaseHTTPRequestHandler):
        server_version = "leads"
        sys_version = ""

        def log_message(self, format, *args):  # el registro propio ya cuenta lo importante
            pass

        def _json(self, estado: int, datos: dict) -> None:
            cuerpo = json.dumps(datos, ensure_ascii=False).encode()
            self.send_response(estado)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _ip(self) -> str:
            # Caddy agrega la IP real al final de X-Forwarded-For; lo anterior lo manda el cliente
            reenviado = self.headers.get("X-Forwarded-For", "")
            return reenviado.split(",")[-1].strip() or self.client_address[0]

        def do_GET(self) -> None:
            if self.path.split("?")[0] == "/salud":
                almacen.resumen_avisos()
                return self._json(200, {"ok": True})
            self._json(404, {"ok": False, "error": "no encontrado"})

        def do_POST(self) -> None:
            if self.path.split("?")[0] != RUTA:
                return self._json(404, {"ok": False, "error": "no encontrado"})
            tipo = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if tipo != "application/json":
                return self._json(415, {"ok": False, "error": "se espera application/json"})
            try:
                largo = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                largo = -1
            if largo < 0 or largo > MAX_CUERPO:
                self.close_connection = True
                return self._json(413, {"ok": False, "error": "solicitud demasiado grande"})
            ip = self._ip()
            if not limitador.permitir(ip):
                registrar("limite", ip=hash_ip(sal, ip))
                return self._json(429, {"ok": False, "error": "demasiadas solicitudes, intenta en unos minutos"})
            try:
                datos = json.loads(self.rfile.read(largo) or b"null")
                if not isinstance(datos, dict):
                    raise ErrorValidacion("se espera un objeto JSON")
                lead = validar(datos)
            except (ValueError, UnicodeDecodeError) as e:
                mensaje = str(e) if isinstance(e, ErrorValidacion) else "JSON invalido"
                return self._json(400, {"ok": False, "error": mensaje})
            try:
                lead_id, duplicado = almacen.guardar(lead, hash_ip(sal, ip),
                                                     self.headers.get("User-Agent", ""), time.time())
            except sqlite3.Error as e:
                registrar("error_guardando", error=type(e).__name__)
                return self._json(503, {"ok": False, "error": "no pudimos guardar, intenta de nuevo"})
            registrar("lead", id=lead_id, duplicado=duplicado)
            al_guardar()
            self._json(200, {"ok": True})

    return ThreadingHTTPServer(("0.0.0.0", puerto), Manejador)


# ---------------------------------------------------------------- arranque

def leer_sal(directorio: Path) -> bytes:
    ruta = directorio / "sal"
    if not ruta.exists():
        ruta.write_text(secrets.token_hex(32))
        ruta.chmod(0o600)
    return ruta.read_text().strip().encode()


def canales_desde_entorno() -> dict:
    e = os.environ.get
    return {
        "telegram": Telegram(e("LEADS_TELEGRAM_TOKEN", ""), e("LEADS_TELEGRAM_CHAT_ID", "")),
        "email": Correo({k: e(f"LEADS_SMTP_{k.upper()}", "")
                         for k in ("host", "puerto", "usuario", "clave", "remitente", "destino")}),
    }


def trabajador(almacen: Almacen, canales: dict, despertar: threading.Event, cada: float = 30.0) -> None:
    while True:
        try:
            procesar_pendientes(almacen, canales, time.time())
        except Exception as e:  # la cola vive en la base: el proximo ciclo la retoma
            registrar("error_trabajador", error=f"{type(e).__name__}: {e}")
        despertar.wait(cada)
        despertar.clear()


def main() -> None:
    datos = Path(os.environ.get("LEADS_DATOS", "/datos"))
    puerto = int(os.environ.get("LEADS_PUERTO", "8081"))
    almacen = Almacen(datos / "leads.db")
    canales = canales_desde_entorno()
    despertar = threading.Event()
    threading.Thread(target=trabajador, args=(almacen, canales, despertar), daemon=True).start()
    servidor = crear_servidor(almacen, Limitador(), leer_sal(datos), puerto, al_guardar=despertar.set)
    registrar("arranque", puerto=puerto, canales={k: c.disponible() for k, c in canales.items()})
    servidor.serve_forever()


if __name__ == "__main__":
    main()
