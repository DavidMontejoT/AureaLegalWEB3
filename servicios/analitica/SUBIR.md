# Subir la analítica a producción (a mano)

Servidor: `154.29.72.67` (alias `ssh vps`, entra como root).
Todo se hace desde tu Mac, parado en esta carpeta:

```bash
cd ~/Downloads/work/AureaLegalWEB3/servicios/analitica
```

| Archivo | Va a | Para qué |
|---|---|---|
| `app.py`, `panel.html`, `Dockerfile` | `/home/deploy/analitica/src/` | el servicio (contenedor `analitica`) |
| `analitica.js` | `/srv/aurealegal/analitica.js` | el script que mide, lo carga la página |

---

## 1. Copiar los archivos

```bash
ssh vps 'install -d /home/deploy/analitica/src && install -d -o 10002 -g 10002 /home/deploy/analitica/datos'
scp app.py panel.html Dockerfile vps:/home/deploy/analitica/src/
scp analitica.js vps:/srv/aurealegal/analitica.js
```

## 2. La clave del panel (la eliges tú; no la pegues en ningún chat)

```bash
ssh vps
install -m 600 /dev/null /home/deploy/analitica/.env
nano /home/deploy/analitica/.env
```

Escribe una sola línea y guarda (Ctrl+O, Enter, Ctrl+X):

```
ANALITICA_CLAVE=una-clave-larga-que-solo-tu-sepas
```

El usuario del panel es `aurea`.

## 3. Construir y arrancar (sigues dentro de `ssh vps`)

```bash
docker build -t analitica:1.0.0 /home/deploy/analitica/src
docker run -d --name analitica --restart unless-stopped \
  --network sectorve_default \
  -v /home/deploy/analitica/datos:/datos \
  --env-file /home/deploy/analitica/.env \
  --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp \
  --memory 128m --pids-limit 64 \
  analitica:1.0.0
docker logs analitica --tail 3
```

Debe decir `"evento": "arranque"` con `"panel": true`.

## 4. Caddy

Primero la copia (sigues en el servidor):

```bash
cp /home/deploy/caddy/Caddyfile /home/deploy/caddy/Caddyfile.antes-analitica-$(date +%F)
nano /home/deploy/caddy/Caddyfile
```

**4a.** Busca el bloque `handle /api/demo-request { ... }` y pega esto **justo debajo** (antes de `@static`):

```caddyfile
	# analitica: eventos del rastreador y panel (servicio `analitica`, codigo en AureaLegalWEB3/servicios/analitica)
	handle /api/evento {
		request_body {
			max_size 16KB
		}
		header Cache-Control "no-store"
		reverse_proxy analitica:8082
	}

	# /analitica y /analitica/... (NO /analitica.js, que es un archivo estatico)
	@analitica path /analitica /analitica/*
	handle @analitica {
		header X-Robots-Tag "noindex, nofollow"
		reverse_proxy analitica:8082
	}
```

**4b.** Busca el bloque final `# todo lo demás: HTML fresco + cabeceras de seguridad` / `handle {` y pega esto **justo antes** de él.
Es la única excepción al `X-Frame-Options: DENY`: solo la vista `?mapa-de-calor=1` (la que usa el panel) y solo dentro de tu propio dominio:

```caddyfile
	# vista para el mapa de calor del panel: se deja mostrar en un iframe SOLO del mismo dominio
	@mapa query mapa-de-calor=1
	handle @mapa {
		header {
			Cache-Control "no-store"
			Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
			X-Content-Type-Options "nosniff"
			X-Frame-Options "SAMEORIGIN"
			X-Robots-Tag "noindex, nofollow"
			Referrer-Policy "strict-origin-when-cross-origin"
			Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-src https://www.youtube.com https://www.youtube-nocookie.com; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'"
		}
		try_files {path} {path}.html /index.html
		file_server
	}
```

Valida y recarga (sin reiniciar):

```bash
docker exec aurea-caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec aurea-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Si `validate` da error: **no recargues**, revisa lo pegado (tabs y llaves) o vuelve atrás:

```bash
cp /home/deploy/caddy/Caddyfile.antes-analitica-$(date +%F) /home/deploy/caddy/Caddyfile
```

## 5. Poner el script en la página

**Opción A — publicar también el NUEVO ESTILO (glass azul + animaciones).**
`AureaLegalWEB3/index-glass.html` ya trae el script de analítica puesto. Desde tu Mac:

```bash
ssh vps 'cp /srv/aurealegal/index.html /srv/aurealegal/index.html.antes-glass-$(date +%F)'
scp ~/Downloads/work/AureaLegalWEB3/index-glass.html vps:/srv/aurealegal/index.html
```

Volver al diseño anterior: `ssh vps 'cp /srv/aurealegal/index.html.antes-glass-AAAA-MM-DD /srv/aurealegal/index.html'`
(con la fecha de la copia). Si usas la opción A, **salta la opción B**.

**Opción B — dejar el diseño actual y solo sumar el script:**

```bash
cp /srv/aurealegal/index.html /srv/aurealegal/index.html.antes-analitica-$(date +%F)
sed -i 's#</head>#<script src="/analitica.js" defer></script>\n</head>#' /srv/aurealegal/index.html
grep -n "analitica.js" /srv/aurealegal/index.html
```

El `grep` debe mostrar una línea. (Repite con `privacidad.html`, `recursos.html`, etc. si también quieres medirlas.)

## 6. Comprobar

Desde tu Mac:

```bash
curl -s -o /dev/null -w "home %{http_code}\n" https://aurea.legal/
curl -s -o /dev/null -w "script %{http_code}\n" https://aurea.legal/analitica.js
curl -s -o /dev/null -w "panel sin clave %{http_code}\n" https://aurea.legal/analitica
curl -sI "https://aurea.legal/?mapa-de-calor=1" | grep -i x-frame
```

Esperado: `home 200`, `script 200`, `panel sin clave 401`, `X-Frame-Options: SAMEORIGIN`.

Después abre `https://aurea.legal/` en el navegador, haz clic en un par de cosas, cierra la pestaña
y entra a `https://aurea.legal/analitica` (usuario `aurea` + tu clave): debe aparecer tu visita.

---

## Si algo sale mal (volver atrás todo)

```bash
cp /home/deploy/caddy/Caddyfile.antes-analitica-$(date +%F) /home/deploy/caddy/Caddyfile
docker exec aurea-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
cp /srv/aurealegal/index.html.antes-analitica-$(date +%F) /srv/aurealegal/index.html
docker stop analitica
```

## Pendiente

- **Privacidad**: sumar un párrafo a `/privacidad` contando que se miden visitas de forma anónima, sin cookies.

(Resuelto en la versión actual del paquete: el embudo detecta solo el envío del formulario, sin tocar
`index.html`, y las rutas se limpian para que el mapa nunca cargue otra web.)
