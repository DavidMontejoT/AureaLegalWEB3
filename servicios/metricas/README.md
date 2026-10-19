# Metricas (visitas y mapa de calor de aurea.legal)

Servicio chiquito, hermano de `leads`: mide cuantas personas entran, de donde llegan, donde
hacen clic, hasta donde bajan, que secciones ven y cuantas llegan a enviar el formulario.
Todo queda en el servidor; no hay Google Analytics ni terceros.

- `app.py` — el servicio (solo biblioteca estandar de Python).
- `m.js` — el script que mide; la pagina lo carga desde `/api/m.js`.
- `panel.html` — el panel, en `/metricas/`, detras de la puerta (puerta-login).
- `test_app.py` — `python3 -m unittest -v` (incluye pruebas por HTTP real).
- `Dockerfile` — imagen minima (`python:3.12-slim`), corre sin privilegios.

## Como funciona

```
pagina (m.js) ──POST /api/m (lotes cada 15 s y al salir)──► metricas ──► SQLite
panel /metricas/ ──forward_auth puerta-login──► metricas ──► resumen + clics
```

| Mide | Como |
|---|---|
| Personas unicas | hash de IP + navegador con una sal **que cambia cada dia** y se borra a los 2 dias |
| Visitas | una por pestaña (id al azar en `sessionStorage`, no es cookie) |
| De donde llegan | dominio del `referrer` (o `utm_source`) |
| Mapa de calor | cada clic se guarda **relativo a su seccion** (`id` o `section-N`), asi aguanta cambios de diseño |
| Scroll | maximo % alcanzado |
| Secciones | tiempo con al menos media pantalla de la seccion a la vista |
| Formulario | primer foco en `#demoForm` = empezo; "Gracias" en `#demoMsg` = envio |

**Privacidad (Ley 1581):** sin cookies, sin IP guardada, sin terceros. No mide si el navegador
pide "no rastrear" (DNT / GPC). De los campos del formulario solo guarda el `name`, nunca lo que
se escribe. Se borra todo a los 13 meses (`RETENCION_DIAS`). Hay que sumar un parrafo a
`/privacidad` contandolo.

**Seguridad:** el panel exige la cabecera `X-Puerta-Usuario`, que pone Caddy con `forward_auth`
(Caddy borra la que mande el cliente). Todo lo que llega de los visitantes (rutas, etiquetas,
referentes) se escapa en el panel y las rutas se limpian para que `//otro.com` no pueda cargar
otro sitio en el iframe del mapa.

## Probar en local

```bash
METRICAS_DATOS=/tmp/metricas METRICAS_PANEL_ABIERTO=1 python3 app.py
```

`METRICAS_PANEL_ABIERTO=1` salta la puerta: **solo en local**.

## Despliegue

```bash
install -d -o 10001 -g 10001 /home/deploy/metricas/datos
docker build -t metricas:1.0.0 /home/deploy/metricas/src
docker run -d --name metricas --restart unless-stopped \
  --network sectorve_default \
  -v /home/deploy/metricas/datos:/datos \
  --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp \
  --memory 128m --pids-limit 64 \
  metricas:1.0.0
```

Caddy (antes del `handle @static`, junto a `/api/demo-request`):

```caddyfile
	handle /api/m* {
		request_body {
			max_size 32KB
		}
		header Cache-Control "no-store"
		reverse_proxy metricas:8082
	}

	handle /metricas* {
		forward_auth puerta-login:8080 {
			uri /verificar
			copy_headers X-Puerta-Usuario
		}
		header X-Robots-Tag "noindex, nofollow"
		reverse_proxy metricas:8082
	}
```

Y en cada pagina que se quiera medir, en el `<head>`:

```html
<script src="/api/m.js" defer></script>
```

Antes de desplegar, revisar en el Caddyfile:
- que la cookie de puerta-login sirva fuera de `/peritaje` (su `Path`), o el panel pedira entrar siempre;
- que el sitio no mande `X-Frame-Options: DENY` ni `frame-ancestors 'none'`, porque el mapa de
  calor muestra la pagina en un iframe del mismo dominio (alcanza con `SAMEORIGIN` / `'self'`).

## Comprobarlo

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://aurea.legal/api/m.js        # 200
curl -s -o /dev/null -w "%{http_code}\n" https://aurea.legal/metricas/       # 302/401 sin sesion
docker logs metricas --tail 5
```

Despues abrir `https://aurea.legal/` en el navegador, hacer clic en algo, y ver la visita en
`https://aurea.legal/metricas/` (entrando por la puerta).
