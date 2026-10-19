# -*- coding: utf-8 -*-
"""Tests del servicio de metricas. Solo biblioteca estandar: `python3 -m unittest -v`."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import app

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"
DIA = 1_790_000_000.0          # un instante fijo cualquiera
SID = "a1b2c3d4e5f6a7b8"


def vista(**extra):
    return {"k": "v", "p": "/", "r": "https://www.google.com/search?q=x", "w": 1440, "h": 900, **extra}


class Reloj:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- normalizacion

class LoteTest(unittest.TestCase):
    def test_lote_valido(self):
        sid, eventos = app.validar_lote({"s": SID, "t": [vista(), {"k": "c", "p": "/", "sec": "hero",
                                                                   "x": 0.5, "y": 0.25, "w": 1440, "l": "Agenda"}]})
        self.assertEqual(sid, SID)
        self.assertEqual([e["k"] for e in eventos], ["v", "c"])

    def test_sesion_invalida(self):
        for s in (None, "", "corta", "x" * 40, "zzzzzzzzzzzzzzzz", 123):
            with self.assertRaises(app.ErrorValidacion):
                app.validar_lote({"s": s, "t": [vista()]})

    def test_demasiados_eventos(self):
        with self.assertRaises(app.ErrorValidacion):
            app.validar_lote({"s": SID, "t": [vista()] * (app.MAX_EVENTOS + 1)})

    def test_descarta_eventos_desconocidos_o_rotos(self):
        _, eventos = app.validar_lote({"s": SID, "t": [{"k": "zz"}, "texto", {"k": "c", "x": "a", "y": 1},
                                                       {"k": "c", "p": "/", "x": 2, "y": -1, "w": 390}]})
        self.assertEqual(len(eventos), 1)
        self.assertEqual((eventos[0]["x"], eventos[0]["y"]), (1.0, 0.0))   # se acota a [0, 1]

    def test_recorta_textos_y_ruta(self):
        _, (e,) = app.validar_lote({"s": SID, "t": [{"k": "c", "p": "/a?token=secreto#x", "sec": "s" * 99,
                                                     "x": .1, "y": .1, "w": 800, "l": "L" * 500}]})
        self.assertEqual(e["p"], "/a")                 # sin query: no guardamos parametros
        self.assertLessEqual(len(e["sec"]), app.MAX_TEXTO)
        self.assertLessEqual(len(e["l"]), app.MAX_TEXTO)

    def test_ruta_no_puede_apuntar_a_otro_sitio(self):
        # el panel usa la ruta como src del iframe: "//otro.com" seria una URL a otro dominio
        for sucia, limpia in (("//evil.com/x", "/evil.com/x"), ("/\\evil.com", "/evil.com"),
                              ("https://evil.com", "/"), ("/ok/../x", "/ok/../x")):
            _, (e,) = app.validar_lote({"s": SID, "t": [vista(p=sucia)]})
            self.assertEqual(e["p"], limpia)

    def test_formulario_solo_estados_conocidos(self):
        _, eventos = app.validar_lote({"s": SID, "t": [{"k": "f", "e": "inicio"}, {"k": "f", "e": "hackeo"}]})
        self.assertEqual([e["e"] for e in eventos], ["inicio"])


class ClasificarTest(unittest.TestCase):
    def test_dispositivo_por_ancho(self):
        self.assertEqual(app.dispositivo(390), "movil")
        self.assertEqual(app.dispositivo(820), "tablet")
        self.assertEqual(app.dispositivo(1440), "escritorio")

    def test_referente(self):
        self.assertEqual(app.referente("https://www.google.com/search?q=x", ""), "google.com")
        self.assertEqual(app.referente("https://l.instagram.com/?u=1", ""), "instagram.com")
        self.assertEqual(app.referente("https://aurea.legal/recursos", ""), "directo")
        self.assertEqual(app.referente("", ""), "directo")
        self.assertEqual(app.referente("basura", ""), "directo")
        self.assertEqual(app.referente("https://www.google.com/", "linkedin"), "utm:linkedin")

    def test_bots(self):
        self.assertTrue(app.es_bot("Googlebot/2.1 (+http://www.google.com/bot.html)"))
        self.assertTrue(app.es_bot("Mozilla/5.0 HeadlessChrome/120"))
        self.assertTrue(app.es_bot(""))
        self.assertFalse(app.es_bot(UA))

    def test_navegador(self):
        self.assertEqual(app.navegador(UA), "Chrome")
        self.assertEqual(app.navegador("Mozilla/5.0 (iPhone) Version/18.0 Mobile Safari/604.1"), "Safari")
        self.assertEqual(app.navegador("Mozilla/5.0 Firefox/131.0"), "Firefox")
        self.assertEqual(app.navegador("Mozilla/5.0 Chrome/141 Edg/141"), "Edge")


# ---------------------------------------------------------------- almacen

class AlmacenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.alm = app.Almacen(Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.tmp.cleanup()

    def registrar(self, eventos, sid=SID, ip="1.2.3.4", ua=UA, ahora=DIA):
        _, limpios = app.validar_lote({"s": sid, "t": eventos})   # el almacen recibe eventos ya validados
        self.alm.registrar(sid, limpios, ip, ua, ahora)

    def test_visitante_anonimo_rota_cada_dia(self):
        a = self.alm.visitante("1.2.3.4", UA, DIA)
        self.assertEqual(a, self.alm.visitante("1.2.3.4", UA, DIA + 60))
        self.assertNotEqual(a, self.alm.visitante("1.2.3.4", UA, DIA + 86_400))
        self.assertNotEqual(a, self.alm.visitante("5.6.7.8", UA, DIA))
        self.assertNotIn("1.2.3.4", a)

    def test_sales_viejas_se_borran(self):
        self.alm.visitante("1.2.3.4", UA, DIA)
        self.alm.visitante("1.2.3.4", UA, DIA + 3 * 86_400)
        self.assertEqual(self.alm.dias_con_sal(), [app.dia_local(DIA + 3 * 86_400)])

    def test_sesion_se_crea_con_la_vista_y_acumula(self):
        self.registrar([vista()])
        self.registrar([{"k": "s", "p": "/", "d": 40}, {"k": "s", "p": "/", "d": 25}], ahora=DIA + 30)
        self.registrar([vista(p="/recursos", r="https://aurea.legal/")], ahora=DIA + 50)
        s = self.alm.sesion(SID)
        self.assertEqual(s["entrada"], "/")
        self.assertEqual(s["referente"], "google.com")
        self.assertEqual(s["dispositivo"], "escritorio")
        self.assertEqual(s["paginas"], 2)
        self.assertEqual(s["scroll_max"], 40)            # se queda con el maximo
        self.assertEqual(s["ultimo"] - s["inicio"], 50)

    def test_eventos_sin_vista_previa_no_crean_sesion(self):
        self.registrar([{"k": "c", "p": "/", "sec": "hero", "x": .5, "y": .5, "w": 1440, "l": ""}])
        self.assertIsNone(self.alm.sesion(SID))

    def test_formulario_solo_avanza(self):
        self.registrar([vista(), {"k": "f", "e": "enviado"}])
        self.registrar([{"k": "f", "e": "inicio"}], ahora=DIA + 5)
        self.assertEqual(self.alm.sesion(SID)["formulario"], "enviado")

    def test_resumen(self):
        self.registrar([vista(), {"k": "c", "p": "/", "sec": "hero", "x": .5, "y": .5, "w": 1440, "l": "Agenda"},
                        {"k": "x", "p": "/", "sec": "hero", "ms": 4000}, {"k": "s", "p": "/", "d": 80},
                        {"k": "f", "e": "inicio"}])
        self.registrar([vista(r="", w=390)], sid="b" * 16, ip="9.9.9.9", ahora=DIA + 10)
        r = self.alm.resumen(desde=DIA - 1, hasta=DIA + 100)
        self.assertEqual(r["visitantes"], 2)
        self.assertEqual(r["sesiones"], 2)
        self.assertEqual(r["vistas"], 2)
        self.assertEqual(r["formulario"], {"inicio": 1, "enviado": 0})
        self.assertEqual({f["nombre"]: f["n"] for f in r["referentes"]}, {"google.com": 1, "directo": 1})
        self.assertEqual({f["nombre"]: f["n"] for f in r["dispositivos"]}, {"escritorio": 1, "movil": 1})
        self.assertEqual(r["secciones"][0]["seccion"], "hero")
        self.assertEqual(r["scroll"]["75"], 1)
        self.assertEqual(r["clics_top"][0]["etiqueta"], "Agenda")
        self.assertEqual(r["rebote_pct"], 50)            # la sesion movil no hizo nada mas

    def test_clics_filtrados(self):
        self.registrar([vista(), {"k": "c", "p": "/", "sec": "hero", "x": .1, "y": .2, "w": 1440, "l": ""},
                        {"k": "c", "p": "/", "sec": "hero", "x": .3, "y": .4, "w": 390, "l": ""}])
        puntos = self.alm.clics(desde=DIA - 1, hasta=DIA + 1, disp="escritorio", ruta="/")
        self.assertEqual(puntos, [{"sec": "hero", "x": .1, "y": .2}])

    def test_purga(self):
        self.registrar([vista()])
        self.alm.purgar(antes_de=DIA + 1)
        self.assertIsNone(self.alm.sesion(SID))


class LimitadorTest(unittest.TestCase):
    def test_limite_por_ip(self):
        reloj = Reloj()
        lim = app.Limitador(por_ip=2, ventana_ip=60, reloj=reloj)
        self.assertTrue(lim.permitir("a"))
        self.assertTrue(lim.permitir("a"))
        self.assertFalse(lim.permitir("a"))
        self.assertTrue(lim.permitir("b"))
        reloj.t += 61
        self.assertTrue(lim.permitir("a"))


# ---------------------------------------------------------------- HTTP

class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.alm = app.Almacen(Path(self.tmp.name) / "m.db")
        self.srv = app.crear_servidor(self.alm, app.Limitador(por_ip=3), 0, panel_abierto=False)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def pedir(self, ruta, cuerpo=None, tipo="text/plain", ua=UA, cabeceras=None):
        datos = cuerpo if isinstance(cuerpo, (bytes, type(None))) else json.dumps(cuerpo).encode()
        req = urllib.request.Request(self.base + ruta, data=datos, method="POST" if datos is not None else "GET",
                                     headers={"Content-Type": tipo, "User-Agent": ua, **(cabeceras or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_recibe_lote(self):
        estado, _, _ = self.pedir(app.RUTA, {"s": SID, "t": [vista()]})
        self.assertEqual(estado, 204)
        self.assertIsNotNone(self.alm.sesion(SID))

    def test_acepta_json_tambien(self):
        estado, _, _ = self.pedir(app.RUTA, {"s": SID, "t": [vista()]}, tipo="application/json")
        self.assertEqual(estado, 204)

    def test_bot_se_ignora_sin_error(self):
        estado, _, _ = self.pedir(app.RUTA, {"s": SID, "t": [vista()]}, ua="Googlebot/2.1")
        self.assertEqual(estado, 204)
        self.assertIsNone(self.alm.sesion(SID))

    def test_rechazos(self):
        self.assertEqual(self.pedir(app.RUTA, b"{}", tipo="image/png")[0], 415)
        self.assertEqual(self.pedir(app.RUTA, b"x" * (app.MAX_CUERPO + 1))[0], 413)
        self.assertEqual(self.pedir(app.RUTA, b"no es json")[0], 400)

    def test_limite(self):
        for _ in range(3):
            self.pedir(app.RUTA, {"s": SID, "t": [vista()]})
        self.assertEqual(self.pedir(app.RUTA, {"s": SID, "t": [vista()]})[0], 429)

    def test_script_publico(self):
        estado, cuerpo, cab = self.pedir(app.RUTA_SCRIPT)
        self.assertEqual(estado, 200)
        self.assertIn("javascript", cab["Content-Type"])
        self.assertIn(app.RUTA.encode(), cuerpo)

    def test_panel_exige_puerta(self):
        self.assertEqual(self.pedir("/metricas/")[0], 401)
        self.assertEqual(self.pedir("/metricas/api/resumen?dias=7")[0], 401)
        estado, cuerpo, _ = self.pedir("/metricas/api/resumen?dias=7", cabeceras={"X-Puerta-Usuario": "david"})
        self.assertEqual(estado, 200)
        self.assertIn("visitantes", json.loads(cuerpo))

    def test_panel_html(self):
        estado, cuerpo, cab = self.pedir("/metricas/", cabeceras={"X-Puerta-Usuario": "david"})
        self.assertEqual(estado, 200)
        self.assertIn("text/html", cab["Content-Type"])

    def test_clics_api(self):
        self.pedir(app.RUTA, {"s": SID, "t": [vista(), {"k": "c", "p": "/", "sec": "hero", "x": .5, "y": .5,
                                                        "w": 1440, "l": ""}]})
        estado, cuerpo, _ = self.pedir("/metricas/api/clics?dias=7&disp=escritorio&ruta=/",
                                       cabeceras={"X-Puerta-Usuario": "david"})
        self.assertEqual(estado, 200)
        self.assertEqual(len(json.loads(cuerpo)["puntos"]), 1)

    def test_salud(self):
        self.assertEqual(self.pedir("/salud")[0], 200)


if __name__ == "__main__":
    unittest.main()
