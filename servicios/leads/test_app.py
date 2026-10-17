# -*- coding: utf-8 -*-
"""Tests del servicio de leads. Solo biblioteca estandar: `python3 -m unittest -v`."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import app

VALIDO = {
    "nombre": "Ana Pérez",
    "email": "ana@despacho.co",
    "empresa": "Despacho Pérez",
    "interes": "Debida diligencia",
    "consentimiento": True,
}


class CanalFalso:
    def __init__(self, disponible=True, falla=False):
        self._disponible = disponible
        self.falla = falla
        self.enviados = []

    def disponible(self) -> bool:
        return self._disponible

    def enviar(self, lead: dict) -> None:
        if self.falla:
            raise RuntimeError("canal caido")
        self.enviados.append(lead)


class Reloj:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- validacion

class ValidarTest(unittest.TestCase):
    def test_lead_valido_se_normaliza(self):
        lead = app.validar({**VALIDO, "nombre": "  Ana Pérez  ", "email": " ANA@Despacho.co "})
        self.assertEqual(lead["nombre"], "Ana Pérez")
        self.assertEqual(lead["email"], "ana@despacho.co")
        self.assertEqual(lead["empresa"], "Despacho Pérez")

    def test_opcionales_pueden_faltar(self):
        lead = app.validar({"nombre": "Ana", "email": "a@b.co", "consentimiento": True})
        self.assertEqual(lead["empresa"], "")
        self.assertEqual(lead["interes"], "")

    def test_requiere_nombre_y_email(self):
        for falta in ("nombre", "email"):
            datos = {k: v for k, v in VALIDO.items() if k != falta}
            with self.assertRaises(app.ErrorValidacion):
                app.validar(datos)

    def test_requiere_consentimiento_explicito(self):
        for valor in (None, False, "true", 1):
            datos = {**VALIDO, "consentimiento": valor}
            with self.assertRaises(app.ErrorValidacion, msg=repr(valor)):
                app.validar(datos)

    def test_email_invalido(self):
        for malo in ("ana", "ana@", "@b.co", "a b@c.co", "a@b", "x" * 250 + "@b.co"):
            with self.assertRaises(app.ErrorValidacion, msg=malo):
                app.validar({**VALIDO, "email": malo})

    def test_rechaza_saltos_de_linea(self):
        # cierra la puerta a inyectar cabeceras en el correo (Reply-To, Bcc...)
        for campo in ("nombre", "email", "empresa", "interes"):
            with self.assertRaises(app.ErrorValidacion, msg=campo):
                app.validar({**VALIDO, campo: "Ana\r\nBcc: otro@x.co"})

    def test_largos_maximos(self):
        for campo, maximo in (("nombre", 120), ("empresa", 160), ("interes", 300)):
            app.validar({**VALIDO, campo: "a" * maximo})
            with self.assertRaises(app.ErrorValidacion, msg=campo):
                app.validar({**VALIDO, campo: "a" * (maximo + 1)})

    def test_tipos_no_texto(self):
        with self.assertRaises(app.ErrorValidacion):
            app.validar({**VALIDO, "nombre": ["Ana"]})

    def test_quita_caracteres_de_control(self):
        lead = app.validar({**VALIDO, "nombre": "Ana\x00\x07 Pérez"})
        self.assertEqual(lead["nombre"], "Ana Pérez")


# ---------------------------------------------------------------- limitador

class LimitadorTest(unittest.TestCase):
    def test_limite_por_ip_y_ventana(self):
        reloj = Reloj()
        lim = app.Limitador(por_ip=2, ventana_ip=600, maximo_global=100, ventana_global=3600, reloj=reloj)
        self.assertTrue(lim.permitir("1.1.1.1"))
        self.assertTrue(lim.permitir("1.1.1.1"))
        self.assertFalse(lim.permitir("1.1.1.1"))
        self.assertTrue(lim.permitir("2.2.2.2"))
        reloj.t += 601
        self.assertTrue(lim.permitir("1.1.1.1"))

    def test_limite_global(self):
        lim = app.Limitador(por_ip=10, ventana_ip=600, maximo_global=3, ventana_global=3600, reloj=Reloj())
        self.assertTrue(all(lim.permitir(f"9.9.9.{i}") for i in range(3)))
        self.assertFalse(lim.permitir("8.8.8.8"))


# ---------------------------------------------------------------- almacen y avisos

class AlmacenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.almacen = app.Almacen(Path(self.dir.name) / "leads.db", canales=("telegram", "email"))

    def tearDown(self):
        self.dir.cleanup()

    def test_guarda_lead_y_encola_un_aviso_por_canal(self):
        lead_id, duplicado = self.almacen.guardar(app.validar(VALIDO), "haship", "ua", ahora=100.0)
        self.assertFalse(duplicado)
        self.assertEqual(len(self.almacen.leads()), 1)
        canales = sorted(a["canal"] for a in self.almacen.pendientes(ahora=100.0))
        self.assertEqual(canales, ["email", "telegram"])
        self.assertEqual(self.almacen.leads()[0]["consentimiento_en"], 100.0)

    def test_mismo_email_en_la_ventana_se_guarda_pero_no_avisa_otra_vez(self):
        self.almacen.guardar(app.validar(VALIDO), "h", "ua", ahora=100.0)
        _, duplicado = self.almacen.guardar(app.validar(VALIDO), "h", "ua", ahora=200.0)
        self.assertTrue(duplicado)
        self.assertEqual(len(self.almacen.leads()), 2)
        self.assertEqual(len(self.almacen.pendientes(ahora=200.0)), 2)
        _, duplicado = self.almacen.guardar(app.validar(VALIDO), "h", "ua", ahora=100.0 + 601)
        self.assertFalse(duplicado)

    def test_persiste_entre_instancias(self):
        self.almacen.guardar(app.validar(VALIDO), "h", "ua", ahora=1.0)
        otro = app.Almacen(Path(self.dir.name) / "leads.db", canales=("telegram", "email"))
        self.assertEqual(len(otro.leads()), 1)
        self.assertEqual(len(otro.pendientes(ahora=1.0)), 2)


class ProcesarPendientesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.almacen = app.Almacen(Path(self.dir.name) / "leads.db", canales=("telegram", "email"))
        self.almacen.guardar(app.validar(VALIDO), "h", "ua", ahora=0.0)

    def tearDown(self):
        self.dir.cleanup()

    def test_envia_y_marca_enviado(self):
        tg, mail = CanalFalso(), CanalFalso()
        res = app.procesar_pendientes(self.almacen, {"telegram": tg, "email": mail}, ahora=0.0)
        self.assertEqual(res["enviados"], 2)
        self.assertEqual(tg.enviados[0]["email"], "ana@despacho.co")
        self.assertEqual(self.almacen.pendientes(ahora=10**9), [])

    def test_fallo_reintenta_con_espera_creciente(self):
        tg, mail = CanalFalso(falla=True), CanalFalso()
        app.procesar_pendientes(self.almacen, {"telegram": tg, "email": mail}, ahora=0.0)
        # el email salio; telegram queda esperando su primer reintento
        self.assertEqual(self.almacen.pendientes(ahora=0.0), [])
        self.assertEqual(self.almacen.pendientes(ahora=app.ESPERAS[0] - 1), [])
        [aviso] = self.almacen.pendientes(ahora=app.ESPERAS[0])
        self.assertEqual((aviso["canal"], aviso["intentos"]), ("telegram", 1))
        self.assertIn("canal caido", aviso["ultimo_error"])
        app.procesar_pendientes(self.almacen, {"telegram": tg, "email": mail}, ahora=app.ESPERAS[0])
        self.assertEqual(self.almacen.pendientes(ahora=app.ESPERAS[0] + app.ESPERAS[1] - 1), [])
        tg.falla = False
        app.procesar_pendientes(self.almacen, {"telegram": tg, "email": mail}, ahora=10**6)
        self.assertEqual(len(tg.enviados), 1)
        self.assertEqual(self.almacen.pendientes(ahora=10**9), [])

    def test_canal_sin_configurar_deja_el_aviso_en_cola_sin_gastar_intentos(self):
        tg = CanalFalso(disponible=False)
        res = app.procesar_pendientes(self.almacen, {"telegram": tg, "email": CanalFalso()}, ahora=0.0)
        self.assertEqual(res["en_espera"], 1)
        [aviso] = self.almacen.pendientes(ahora=0.0)
        self.assertEqual((aviso["canal"], aviso["intentos"]), ("telegram", 0))

    def test_se_rinde_tras_el_maximo_de_intentos(self):
        tg = CanalFalso(falla=True)
        ahora = 0.0
        for _ in range(app.MAX_INTENTOS):
            app.procesar_pendientes(self.almacen, {"telegram": tg, "email": CanalFalso()}, ahora=ahora)
            ahora += 10**6
        self.assertEqual(self.almacen.pendientes(ahora=10**12), [])
        self.assertEqual(self.almacen.resumen_avisos()["fallido"], 1)


# ---------------------------------------------------------------- mensajes

class MensajesTest(unittest.TestCase):
    def test_texto_aviso_lleva_los_datos(self):
        texto = app.texto_aviso({**app.validar(VALIDO), "id": 7, "creado_en": 0.0})
        for parte in ("Ana Pérez", "ana@despacho.co", "Despacho Pérez", "Debida diligencia", "#7"):
            self.assertIn(parte, texto)

    def test_correo_responde_al_lead(self):
        lead = {**app.validar(VALIDO), "id": 7, "creado_en": 0.0}
        msj = app.mensaje_correo(lead, remitente="avisos@aurea.legal", destino="yo@gmail.com")
        self.assertEqual(msj["Reply-To"], "ana@despacho.co")
        self.assertEqual(msj["To"], "yo@gmail.com")
        self.assertIn("Ana Pérez", msj["Subject"])
        self.assertEqual(msj.get_content_type(), "text/plain")

    def test_telegram_envia_texto_plano(self):
        llamadas = []
        tg = app.Telegram("TOKEN", "123", enviar_http=lambda url, cuerpo: llamadas.append((url, cuerpo)))
        self.assertTrue(tg.disponible())
        tg.enviar({**app.validar(VALIDO), "id": 1, "creado_en": 0.0})
        url, cuerpo = llamadas[0]
        self.assertTrue(url.endswith("/botTOKEN/sendMessage"))
        datos = json.loads(cuerpo)
        self.assertEqual(datos["chat_id"], "123")
        self.assertNotIn("parse_mode", datos)
        self.assertFalse(app.Telegram("", "", enviar_http=None).disponible())

    def test_correo_sin_config_no_esta_disponible(self):
        self.assertFalse(app.Correo({}).disponible())


# ---------------------------------------------------------------- HTTP, por el camino real

class HttpTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.almacen = app.Almacen(Path(self.dir.name) / "leads.db", canales=("telegram", "email"))
        self.limitador = app.Limitador(por_ip=3, ventana_ip=600, maximo_global=100, ventana_global=3600)
        self.servidor = app.crear_servidor(self.almacen, self.limitador, sal=b"sal", puerto=0)
        self.url = f"http://127.0.0.1:{self.servidor.server_address[1]}"
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def tearDown(self):
        self.servidor.shutdown()
        self.servidor.server_close()
        self.dir.cleanup()

    def post(self, cuerpo, tipo="application/json", ip="203.0.113.5", ruta="/api/demo-request"):
        datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        req = urllib.request.Request(self.url + ruta, data=datos, method="POST",
                                     headers={"Content-Type": tipo, "X-Forwarded-For": ip})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_lead_valido_queda_guardado_antes_de_responder(self):
        estado, cuerpo = self.post(VALIDO)
        self.assertEqual((estado, cuerpo["ok"]), (200, True))
        [lead] = self.almacen.leads()
        self.assertEqual(lead["email"], "ana@despacho.co")
        self.assertNotEqual(lead["ip_hash"], "203.0.113.5")

    def test_respuesta_no_depende_de_que_avisar_funcione(self):
        # el servidor no avisa en linea: guarda y responde; los avisos son del worker
        estado, _ = self.post(VALIDO)
        self.assertEqual(estado, 200)
        self.assertEqual(len(self.almacen.pendientes(ahora=10**12)), 2)

    def test_invalido_400(self):
        estado, cuerpo = self.post({**VALIDO, "email": "malo"})
        self.assertEqual((estado, cuerpo["ok"]), (400, False))
        self.assertEqual(self.almacen.leads(), [])

    def test_sin_consentimiento_400(self):
        estado, _ = self.post({**VALIDO, "consentimiento": False})
        self.assertEqual(estado, 400)

    def test_json_roto_400(self):
        self.assertEqual(self.post(b"{no es json")[0], 400)
        self.assertEqual(self.post(b"[1,2]")[0], 400)

    def test_tipo_de_contenido_415(self):
        self.assertEqual(self.post(VALIDO, tipo="text/plain")[0], 415)

    def test_cuerpo_grande_413(self):
        self.assertEqual(self.post({**VALIDO, "relleno": "x" * (app.MAX_CUERPO + 1)})[0], 413)

    def test_rate_limit_por_ip_real_429(self):
        for i in range(3):
            self.assertEqual(self.post({**VALIDO, "email": f"a{i}@b.co"})[0], 200)
        self.assertEqual(self.post({**VALIDO, "email": "z@b.co"})[0], 429)
        # la IP sale del ultimo X-Forwarded-For (el que pone Caddy), no del primero
        self.assertEqual(self.post({**VALIDO, "email": "y@b.co"}, ip="6.6.6.6, 203.0.113.5")[0], 429)
        self.assertEqual(self.post({**VALIDO, "email": "x@b.co"}, ip="198.51.100.9")[0], 200)

    def test_ruta_desconocida_404(self):
        self.assertEqual(self.post(VALIDO, ruta="/api/otra")[0], 404)

    def test_cliente_que_corta_no_ensucia_el_registro(self):
        # Caddy corta la subida (max_size) mientras leads ya contesto 413: el socket se cierra.
        # Eso es una linea de registro, no un traceback de 20 lineas por cada peticion grande.
        salida = io.StringIO()
        with contextlib.redirect_stderr(salida):
            try:
                raise BrokenPipeError(32, "Broken pipe")
            except BrokenPipeError:
                self.servidor.handle_error(None, ("172.18.0.2", 1))
        lineas = salida.getvalue().strip().splitlines()
        self.assertEqual(len(lineas), 1)
        self.assertEqual(json.loads(lineas[0])["evento"], "conexion_cortada")

    def test_otros_errores_siguen_registrandose(self):
        salida = io.StringIO()
        with contextlib.redirect_stderr(salida):
            try:
                raise KeyError("x")
            except KeyError:
                self.servidor.handle_error(None, ("172.18.0.2", 1))
        self.assertEqual(json.loads(salida.getvalue().strip().splitlines()[-1])["evento"], "error_peticion")
        self.assertIn("KeyError", salida.getvalue())

    def test_salud(self):
        with urllib.request.urlopen(self.url + "/salud", timeout=5) as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
