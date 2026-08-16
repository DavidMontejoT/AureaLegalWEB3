# Revisión Servidor SectorVe — 154.29.72.136

> Fecha: 2026-08-13 · Solo-lectura (no se modificó nada en el servidor)

---

## Actualización 2026-08-16 — Estado de remediación (runbook en ejecución)

> Timestamp UTC: 2026-08-16T23:52Z · Ejecutor: Hermes (agente) con aprobación de David · Pre-check global OK (host estable load 0.46, disco 69G libres, RAM 7.4G disponible, 14 contenedores Up)

| Fase | Estado | Evidencia (2026-08-16) |
|------|--------|------------------------|
| 1. Prune build cache | ✅ Completada | Build Cache 0 B; disco 16G/89G (19% uso) |
| 2. Backups + cron | ✅ Completada | cron `0 3 * * * /home/deploy/backup-dbs.sh`; 4 bases OK hoy (sectorve 312K, centinela 276K, contralitigator 648K, neo4j 536K) |
| 3. Git SectorVe | ✅ Completada | `main...origin/main` limpio (config prod commiteada) |
| 4a. Permisos .env | ✅ Completada | Todos los `.env` en 600; `.env.prod.bak` de Centinela eliminado |
| 4b. Token TFG | ✅ Completada | Remote `git@github-tfg:DavidMontejoT/tokenizacion-grafo.git` (sin token) |
| 5a. Logs Docker | ✅ Completada | `/etc/docker/daemon.json`: `max-size:10m, max-file:3` |
| 5b. Swap | ⏳ Pendiente | Swap 0 B — Paso 3 del runbook 2026-08-16 |
| 5c. Puertos internos | ⏳ Pendiente | `backend:8000` y `frontend:3000` en 0.0.0.0 — Paso 4, requiere OK + reconfig de Caddy |
| 6. Watchdog | ⏳ Pendiente | Paso 5 opcional — definir modalidad |

### Hallazgos nuevos (2026-08-16)

1. **`contralitigator-api-py-1` healthcheck frágil**: el HEALTHCHECK viene del Dockerfile (`--timeout=5s`, urllib a `localhost:3000/health`), no del compose. Genera falsos negativos intermitentes (3 fails ~23:40Z; luego healthy). El servicio responde 200 OK siempre (logs). Fix: override del healthcheck en `docker-compose.prod.yml` (timeout 15s, start_period 20s) — Paso 2 del runbook.
2. **Caddy → TFG vía `host.docker.internal:8000/3000`** (resuelve 172.17.0.1 = gateway docker0). Caddy NO está conectado a `tokenizaciongrafo_tokenizacion_net`. Bindear TFG a 127.0.0.1 sin reconfigurar Caddy rompe realtokenstate.aurea.legal → el Paso 4 necesita rediseño (conectar Caddy a la red TFG + actualizar reverse_proxy) o mitigación por UFW+watchdog.

---

## 1. Acceso — Funciona

| Dato | Valor |
|------|-------|
| Host / IP | DeploymentALllc · 154.29.72.136 |
| OS | Ubuntu 24.04.4 LTS |
| Usuario | deploy |
| Clave local | `~/.ssh/sectorve_vps` (auth por llave pública, sin contraseña) |
| sudo | deploy tiene sudo **sin password** (`sudo -n` funciona) |
| Uptime | 1 día |
| RAM | 9.7 GB (2 GB usados) |
| Disco | 89 GB · 53 GB usados · 32 GB libres (64%) |
| Swap | **0 B (no configurada)** |

Comando de acceso:

```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136
```

No hacen falta contraseñas de VNC ni resetear nada: la llave pública es la llave de entrada.

---

## 2. Qué hay desplegado

| Proyecto | Stack | Dominio | Path |
|----------|-------|---------|------|
| SectorVe | NestJS + PostGIS + Angular + Caddy + Umami | sectorve.com / api. / stats. | `/home/deploy/SectorVe` |
| Centinela | FastAPI + LangGraph + Next.js + pgvector | centinela.aurea.legal | `/home/deploy/Centinela` |
| Contralitigator | FastAPI-py + Express + SPA | app.aurea.legal | `/home/deploy/contralitigator` |
| TokenizacionGrafo | FastAPI + Neo4j + Next.js/D3 | realtokenstate.aurea.legal | `/home/deploy/TokenizacionGrafo` |
| Aurea Legal (estático) | HTML/CSS/JS | aurea.legal | `/srv/aurealegal` |
| AtomoSalud (estático) | HTML/CSS/JS | atomosalud.com | `/srv/atomosalud` |

- 14 contenedores corriendo, **13 healthy**.
- Docker habilitado al boot (`systemctl is-enabled docker` → enabled).
- Todos los servicios con `restart: unless-stopped`.
- Redes Docker: `sectorve_default`, `contralitigator_default`, `tokenizaciongrafo_tokenizacion_net`.

---

## 3. Datos específicos para deploys (todos en su lugar ✓)

### Variables de entorno por proyecto (nombres — valores verificados como presentes)

**SectorVe** (`/home/deploy/SectorVe/.env.prod`):
`POSTGRES_USER` · `POSTGRES_PASSWORD` · `POSTGRES_DB` · `DATABASE_URL` · `REPORTER_HASH_SECRET` · `PORT` · `SITE_DOMAIN` · `API_DOMAIN` · `STATS_DOMAIN` · `UMAMI_APP_SECRET` · `ADMIN_IMPORT_TOKEN` · `GOOGLE_SHEETS_API_KEY` · `GOOGLE_SHEETS_SPREADSHEET_ID` · `GROQ_API_KEY` · `CONTACT_HASH_SECRET`

**Centinela** (`/home/deploy/Centinela/.env.prod`):
`ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `OPENSANCTIONS_API_KEY` · `POSTGRES_*` · `DATABASE_URL` · `LLM_MODEL` · `EMBEDDING_MODEL` · `CHUNK_SIZE` · `CHUNK_OVERLAP` · `MAX_RAG_RESULTS` · `FUZZY_THRESHOLD` · `API_HOST/PORT` · `CORS_ORIGINS` · `DEMO_CREDENTIALS` · `JWT_SECRET` · `LOG_LEVEL` · `MOCK_SII` · `MOCK_SCREENING` · `INTERNAL_API_SECRET` · `GROQ_API_KEY`

**TokenizacionGrafo** (`/home/deploy/TokenizacionGrafo/.env`):
`DEEPSEEK_API_KEY` · `DEEPSEEK_BASE_URL` · `DEEPSEEK_MODEL` · `NEO4J_URI` · `NEO4J_USER` · `NEO4J_PASSWORD` · `API_CORS_ORIGINS`

**Contralitigator** (`/home/deploy/contralitigator/.env.prod`):
`POSTGRES_*` · `GROQ_API_KEY` · `GROQ_MODEL` · `OPENROUTER_API_KEY` · `OPENROUTER_BASE_URL` · `OR_MODEL_FAST/STRONG/FALLBACK` · `JWT_SECRET` · `WEB_PORT` · `ADMIN_USER` · `ADMIN_PASSWORD` · `COOKIE_SECRET` · `CENTINELA_API_SECRET` · `CENTINELA_API_URL`

### Git auth en el VPS

- Clave `github_deploy` + `~/.ssh/config` mapea `github.com` → por eso **SectorVe** usa `git@github.com:DavidMontejoT/SectorVe.git` y puede hacer pull normalmente.
- **Centinela** y **TokenizacionGrafo** usan remotes HTTPS.
- No hay `~/.git-credentials` ni credential helper configurado.

### Dominios en el Caddyfile (`/home/deploy/SectorVe/Caddyfile`)

`aurea.legal` · `centinela.aurea.legal` · `app.aurea.legal` · `atomosalud.com` · `realtokenstate.aurea.legal` + `sectorve.com` / `api.sectorve.com` / `stats.sectorve.com` (vía env vars).

---

## 4. Hallazgos para "control sano"

### 🔴 CRÍTICO

**No hay backups automáticos.**
Ni cron de `deploy` ni de `root`. Las 4 bases de datos:

| Base | Tamaño |
|------|--------|
| sectorve-db-1 (PostGIS) | 120 MB |
| centinela-centinela-db-1 (pgvector) | 64 MB |
| contralitigator-db-1 (Postgres) | 64 MB |
| tokenizacion-neo4j (Neo4j) | 517 MB |

…solo tienen 3 dumps manuales del 28-30 de junio en `/home/deploy/SectorVe/`. Si cae un volumen Docker, se pierde todo.

### 🟠 ALTO

1. **36.97 GB de build cache reclamable** (`docker builder prune`). Es ~40% del disco usado. `docker system df` reporta Build Cache = 41.99 GB, reclaimable 36.97 GB.
2. **SectorVe tiene cambios sin commitear** en el server:
   - `Caddyfile` (+143 líneas)
   - `apps/trabajo/index.html` (+111 líneas)
   - `docker-compose.prod.yml` (+12 líneas — mounts de aurea.legal y atomosalud, `extra_hosts`, red `contralitigator_default`)
   - `Caddyfile.bak` (untracked)

   Ese estado es **la config real que corre en producción** pero NO está en git. Un `git pull` / `git checkout` lo borraría.
3. **Token de GitHub embebido** en el remote de TokenizacionGrafo (`https://oauth2:gho_...@github.com/...`). Debería usar la clave `github_deploy` que ya existe.

### 🟡 MEDIO

4. **Puertos expuestos a 0.0.0.0**: Neo4j (7474/7687) y TokenizacionGrafo (3000/8000). Hoy UFW los bloquea (solo abre 22/80/443/8089), pero si UFW se apaga quedan expuestos. Recomendado bindear a `127.0.0.1` o red interna.
5. **Logs de Docker sin límite de tamaño** (no medibles sin sudo; revisar con `sudo du -sh /var/lib/docker/containers/*/*-json.log`). Riesgo de llenar disco.
6. **Sin swap** (0 B).
7. **Basura en `/home/deploy`**: `.Destination` y `.Destinationprintlnend` (artefactos de un redirect mal escrito).

---

## 5. Plan de acción sugerido

| # | Acción | Impacto | Riesgo |
|---|--------|---------|--------|
| 1 | `docker builder prune -af` | +37 GB disco | Ninguno (solo caché) |
| 2 | Backup inmediato de las 4 DBs + cron diario con retención | Crítico | Ninguno |
| 3 | Commitear los 3 archivos modificados de SectorVe | Evita pérdida de config | Bajo (revisar diff) |
| 4 | Sacar token de la URL de TFG → usar `github_deploy` | Seguridad | Bajo |
| 5 | `max-size` en logs Docker + swap de 2-4 GB | Estabilidad | Bajo |
| 6 | Bindear puertos internos a localhost | Seguridad | Bajo |

**Recomendación:** empezar por 1 y 2 (prune + backup), seguros y de mayor impacto.

---

## 6. Anexo técnico — comandos de referencia

```bash
# Acceso
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136

# Estado de contenedores
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"

# Disco Docker
docker system df

# Liberar build cache (~37GB)
docker builder prune -af

# Backup manual de una DB (ejemplo SectorVe)
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 \
  "cd /home/deploy/SectorVe && docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T db pg_dump -U sectorve sectorve | gzip" \
  > backup_$(date +%F_%H%M).sql.gz

# Validar Caddyfile antes de reiniciar Caddy
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 \
  "docker run --rm -v /home/deploy/SectorVe/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"

# Git en el VPS (SectorVe usa SSH)
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 "cd /home/deploy/SectorVe && git status -sb && git log --oneline -5"
```

---

### Firma

Revisión realizada el 2026-08-13 con acceso por llave pública (`deploy@154.29.72.136`). Todos los hallazgos verificados con comandos reales en el servidor; ningún valor de credencial fue leído ni expuesto (solo nombres de variables).
