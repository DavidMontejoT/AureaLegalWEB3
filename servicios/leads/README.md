# Leads (formulario de aurea.legal)

Servicio chiquito que atiende `POST /api/demo-request` del formulario de la home: **guarda el
lead y despues avisa** por Telegram y por correo.

Reemplaza al endpoint que vivia en el api-py de Contralitigator (servidor .136), que no se migro
al .67 el 06-10-2026: desde entonces el formulario respondia 405 y el lead no llegaba a ningun lado.

- `app.py` — el servicio (solo biblioteca estandar de Python, sin dependencias).
- `listar.py` — lista los leads y el estado de sus avisos (solo lectura).
- `test_app.py` — `python3 -m unittest -v` (incluye pruebas por HTTP real contra el servicio).
- `Dockerfile` — imagen minima (`python:3.12-slim`), corre sin privilegios.
- `.env.example` — las variables de Telegram y correo.

## Como funciona

```
POST /api/demo-request
  → valida (JSON, ≤ 8 KB, campos, consentimiento, rate limit por IP)
  → INSERT en SQLite          ← el lead ya esta a salvo
  → 200 {"ok": true}
  → hilo aparte: avisa por Telegram y correo
       ├─ sale → aviso "enviado"
       └─ falla → reintenta (1m, 5m, 30m, 2h, 6h…; hasta 12 intentos), sobrevive a reinicios
```

| Respuesta | Cuando |
|---|---|
| 200 | guardado (tambien si es duplicado: mismo email en < 10 min se guarda pero no avisa otra vez) |
| 400 | falta nombre/email, email invalido, sin `consentimiento: true`, saltos de linea, largos |
| 413 | cuerpo de mas de 8 KB |
| 415 | no es `application/json` |
| 429 | mas de 5 por IP en 10 min, o mas de 100 en total por hora |
| 503 | no se pudo escribir la base |

El front ya trata cualquier no-200 como error y ofrece **Reintentar** y **WhatsApp**: el lead
nunca se pierde en silencio.

**Consentimiento (Ley 1581):** el formulario exige la casilla de autorizacion de tratamiento de
datos; sin `consentimiento: true` no se guarda nada, y con el se guarda la fecha (`consentimiento_en`).

**La IP** sale del ultimo valor de `X-Forwarded-For` (el que pone Caddy) y se guarda **hasheada**
con una sal propia: sirve contra abuso, no para identificar a nadie.

## Datos (fuera del repo)

`/home/deploy/leads/datos/` montado en `/datos`:

| Archivo | Que es |
|---|---|
| `leads.db` | SQLite (WAL): tablas `leads` y `avisos` |
| `sal` | con la que se hashea la IP (se crea sola) |

## Despliegue

```bash
install -d -o 10001 -g 10001 /home/deploy/leads/datos
install -m 600 /dev/null /home/deploy/leads/.env      # completar a mano desde .env.example
docker build -t leads:1.0.0 /home/deploy/leads/src
docker run -d --name leads --restart unless-stopped \
  --network sectorve_default \
  -v /home/deploy/leads/datos:/datos \
  --env-file /home/deploy/leads/.env \
  --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp \
  --memory 128m --pids-limit 64 \
  leads:1.0.0
```

Caddy (antes del `handle @static`):

```caddyfile
	handle /api/demo-request {
		request_body {
			max_size 16KB
		}
		reverse_proxy leads:8081
	}
```

## Comprobarlo (por el camino real)

```bash
curl -s -X POST https://aurea.legal/api/demo-request -H 'Content-Type: application/json' \
  -d '{"nombre":"PRUEBA despliegue","email":"prueba@aurea.legal","interes":"prueba","consentimiento":true}'
docker exec leads python listar.py
```

Debe responder `{"ok": true}`, aparecer en `listar.py` y llegar a Telegram y al correo.

## Lo que no hace

- No tiene vista web de leads: Telegram y el correo traen todo el dato, y `listar.py` por SSH.
- No importa los leads viejos de Contralitigator (dump del 27-09 en `~/aurealegal-backups/`).
- No esta en el backup offsite todavia: hay que sumar `leads.db` al script de respaldo.
