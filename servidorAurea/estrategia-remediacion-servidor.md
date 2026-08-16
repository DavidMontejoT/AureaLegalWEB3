# Estrategia de Remediación — Servidor SectorVe (154.29.72.136)

> Autor: auditoría del 2026-08-13 · Basada en `revision-servidor-sectorve.md`
> Estado: EN EJECUCIÓN — Runbook Senior 2026-08-16T23:52Z (Hermes + aprobación de David)
> Gate: Pasos 1-3 ejecutar ya · Paso 4 espera OK explícito · Paso 5 definir modalidad

## Log de avance (runbook 2026-08-16)

| Fecha (UTC) | Paso | Resultado | Evidencia |
|-------------|------|-----------|-----------|
| 2026-08-16T23:52 | Pre-check global | ✅ OK | host estable, disco 69G libres, 14 contenedores Up, endpoints 200 |
| 2026-08-16T23:52 | Backups de compose | ✅ | `docker-compose.prod.yml.bak.20260816T235213Z` (contralitigator), `docker-compose.yml.bak.20260816T235201Z` (TFG) |
| 2026-08-16T23:55 | Paso 1 Docs | ✅ | Este archivo + `revision-servidor-sectorve.md` actualizados |
| — | Paso 2 Healthcheck api-py | ⏳ | Pendiente de ejecutar |
| — | Paso 3 Swap 2GB | ⏳ | Pendiente de ejecutar |
| — | Paso 4 Bind puertos TFG | ⏳ | **Requiere OK de David** — hallazgo: Caddy usa `host.docker.internal` (172.17.0.1), no está en red TFG; bind a 127.0.0.1 rompe el dominio sin reconfig de Caddy |
| — | Paso 5 Watchdog | ⏳ | Definir modalidad: log-only vs Telegram |

### Notas técnicas del runbook

- Contralitigator usa `docker-compose.prod.yml` (no `docker-compose.yml`).
- `api-py` NO tiene healthcheck en compose; viene del Dockerfile con `--timeout=5s` → falsos negativos. Override en compose es suficiente (sin rebuild).
- TFG: servicios `backend` (8000) y `frontend` (3000) sin bind; `neo4j` ya en 127.0.0.1.
- Redes: `tokenizaciongrafo_tokenizacion_net` (TFG: neo4j/backend/frontend), Caddy en `contralitigator_default` + `sectorve_default`.

---

## Principios de ejecución

1. **Reversibilidad**: cada fase tiene rollback documentado antes de ejecutarse.
2. **Snapshot primero**: nunca se toca una config sin antes guardar su estado actual.
3. **Sin asumir credenciales**: los valores de claves no se leen ni se regeneran sin aprobación explícita de David.
4. **Evidencia**: cada fase termina con un comando de verificación (salida real).
5. **Fase por fase**: se valida y confirma una antes de pasar a la siguiente.

---

## Hallazgo adicional (no estaba en el MD de revisión)

Al analizar los permisos se detectó un problema de higiene de secretos:

| Archivo | Permisos | Riesgo |
|---------|----------|--------|
| `/home/deploy/Centinela/.env` | 644 (mundo lee) | ⚠️ claves API legibles |
| `/home/deploy/Centinela/.env.prod` | 644 (mundo lee) | ⚠️ claves API legibles |
| `/home/deploy/Centinela/.env.prod.bak` | 644 (mundo lee) | ⚠️ **claves viejas** legibles |
| `/home/deploy/TokenizacionGrafo/.env` | 664 | ⚠️ DeepSeek + Neo4j creds |
| `/home/deploy/contralitigator/.env` | 664 | ⚠️ |
| `/home/deploy/contralitigator/.env.prod` | 664 | ⚠️ |
| `/home/deploy/SectorVe/.env.prod` | 600 | ✅ correcto |
| `/home/deploy/atomosalud-admin/.env` | 600 | ✅ correcto |

Mitigante: `/home/deploy` está en `750`, así que hoy un usuario sin permisos no llega a atravesarlo. Pero es defensa-en-profundidad: los `.env` con secretos deben ser `600` y el `.env.prod.bak` (claves viejas) debe eliminarse.

---

## Resumen de fases

| Fase | Qué hace | Riesgo | Tiempo estimado |
|------|----------|--------|-----------------|
| 0 | Snapshot de seguridad (config actual) | Ninguno | 2 min |
| 1 | Liberar 37 GB de build cache | Ninguno (solo caché) | 1 min |
| 2 | Backup inmediato + cron diario | Ninguno | 15 min |
| 3 | Reconciliar git de SectorVe | Bajo (revisar diff) | 15 min |
| 4 | Hardening de credenciales (chmod + token TFG) | Bajo | 10 min |
| 5 | Hardening runtime (logs, swap, puertos) | Bajo-Medio | 20 min |
| 6 | Monitoreo continuo (watchdog) | Ninguno | 10 min |

---

## Fase 0 — Snapshot de seguridad (pre-requisito)

Guardar copia local del estado de configuración ANTES de cualquier cambio, para poder revertir.

```bash
# Localmente (Mac), crear carpeta de trabajo
mkdir -p ~/Downloads/work/AureaLegalWEB3/servidorAurea/snapshot-$(date +%F)

# 1. Caddyfile actual
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 \
  'docker exec sectorve-caddy-1 cat /etc/caddy/Caddyfile' \
  > snapshot-*/Caddyfile.running

# 2. docker-compose.prod.yml de SectorVe (el modificado sin commitear)
scp -i ~/.ssh/sectorve_vps \
  deploy@154.29.72.136:/home/deploy/SectorVe/docker-compose.prod.yml snapshot-*/

# 3. diff de lo no commiteado (para decidir en Fase 3)
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 \
  'cd /home/deploy/SectorVe && git diff > /tmp/sectorve-uncommitted.diff && git status -sb' \
  && scp -i ~/.ssh/sectorve_vps deploy@154.29.72.136:/tmp/sectorve-uncommitted.diff snapshot-*/

# 4. Lista de volúmenes (para plan de backup)
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 'docker volume ls'
```

Verificación: la carpeta `snapshot-*` contiene `Caddyfile.running`, `docker-compose.prod.yml` y `sectorve-uncommitted.diff`.

---

## Fase 1 — Liberar build cache (37 GB)

`docker system df` reporta 41.99 GB de Build Cache, de los cuales 36.97 GB son reclamables. Es caché de builds anteriores — no afecta a nada en runtime.

```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 'docker builder prune -af'
```

Verificación:
```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 'docker system df && df -h /'
# Esperado: Build Cache ~0, / con ~68-69G libres (antes 32G)
```

Rollback: no aplica (no se borra nada que se pueda restaurar).

---

## Fase 2 — Backup de bases + cron diario

### 2a. Backup inmediato (manual)

Postgres (SectorVe, Centinela, Contralitigator) vía `pg_dump` leyendo user/db del propio `.env.prod`. Neo4j (517 MB) vía snapshot del volumen.

```bash
# Postgres — SectorVe
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 \
  "cd /home/deploy/SectorVe && docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T db pg_dump -U sectorve sectorve | gzip" \
  > ~/Downloads/work/AureaLegalWEB3/servidorAurea/backup-sectorve-$(date +%F).sql.gz

# Postgres — Centinela y Contralitigator (mismo patrón, leer user/db del .env.prod)
# ... pg_dump -U <POSTGRES_USER> <POSTGRES_DB>

# Neo4j — snapshot del volumen (sin downtime, consistencia aceptable para grafo de bajo write)
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 \
  'docker run --rm -v tokenizaciongrafo_neo4j_data:/data -v /home/deploy/backups:/backup alpine \
   tar czf /backup/neo4j-$(date +%F).tar.gz -C /data .'
```

> Nota Neo4j: `neo4j-admin database dump` en Community Edition exige detener la BD (downtime). La alternativa sin downtime es el tar del volumen; para un grafo de bajo write es suficiente. Si David prefiere consistencia total, se hace un stop/start de ~10 s en horas valle. **Decisión de David.**

### 2b. Script de backup + cron

Crear `/home/deploy/backup-dbs.sh` (lee credenciales de los `.env.prod`, sin hardcodear secretos) y cron diario con retención de 14 días:

```bash
# En el VPS
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 'mkdir -p /home/deploy/backups'
# (escribir el script con write_file local → scp, para evitar heredoc/quoting issues)

# cron: diario a las 03:00 UTC (hora valle), retención 14 días
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 \
  '(crontab -l 2>/dev/null; echo "0 3 * * * /home/deploy/backup-dbs.sh >> /home/deploy/backups/backup.log 2>&1") | crontab -'
```

Verificación:
```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 'crontab -l && ls -la /home/deploy/backups/'
# Además: probar restauración de 1 dump en local (gunzip -c | wc -l > 100)
```

---

## Fase 3 — Reconciliar git de SectorVe

El server tiene 3 archivos modificados sin commitear que representan la **config real de producción**:

- `Caddyfile` (+143) — bloques de aurea.legal, atomosalud, contralitigator
- `apps/trabajo/index.html` (+111) — página bolsa de trabajo
- `docker-compose.prod.yml` (+12) — mounts estáticos, `extra_hosts`, red externa
- `Caddyfile.bak` (untracked) — basura

Pasos:
1. Revisar el diff (ya extraído en Fase 0) para confirmar qué es intencional vs accidental.
2. Commitear lo intencional con mensaje descriptivo.
3. Hacer `git push origin main` (usa `git@github.com` → clave `github_deploy`, funciona).
4. Borrar `Caddyfile.bak` (y `.Destination`, `.Destinationprintlnend`).

```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 '
  cd /home/deploy/SectorVe &&
  git add Caddyfile apps/trabajo/index.html docker-compose.prod.yml &&
  git commit -m "chore(deploy): sincroniza config de produccion (statics aurea/atomosalud, red contralitigator, extra_hosts)" &&
  git push origin main &&
  rm -f Caddyfile.bak &&
  git status -sb'
```

Verificación: `git status -sb` → limpio y `origin/main` actualizado.

Rollback: el push se puede revertir con `git revert`; el estado previo está en el snapshot de Fase 0.

⚠️ Requiere aprobación de David sobre el contenido del diff antes del commit.

---

## Fase 4 — Hardening de credenciales

### 4a. Permisos 600 en todos los `.env` con secretos

```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 '
  chmod 600 /home/deploy/Centinela/.env /home/deploy/Centinela/.env.prod \
            /home/deploy/TokenizacionGrafo/.env \
            /home/deploy/contralitigator/.env /home/deploy/contralitigator/.env.prod &&
  rm -f /home/deploy/Centinela/.env.prod.bak &&
  ls -la /home/deploy/*/.env* | grep -vE "\.example"'
```

Verificación: todos los `.env` en `600` (o `-rw-------`).

### 4b. Token de GitHub en URL de TokenizacionGrafo

El remote es `https://oauth2:gho_...@github.com/DavidMontejoT/tokenizacion-grafo.git`. El token está en texto plano en `.git/config`.

```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 '
  cd /home/deploy/TokenizacionGrafo &&
  git remote set-url origin git@github.com:DavidMontejoT/tokenizacion-grafo.git &&
  git remote -v'
```

Verificación: remote sin `oauth2:` → usa la clave `github_deploy` ya configurada.

⚠️ **Requiere acción de David en GitHub**: revocar el token `gho_...` (está expuesto). Yo no puedo revocarlo sin su cuenta. Alternativa: si David tiene `gh` CLI configurado, lo revocamos juntos.

---

## Fase 5 — Hardening runtime

### 5a. Límite de tamaño en logs Docker

Vía `/etc/docker/daemon.json` (global, una sola edición):

```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 '
  sudo tee /etc/docker/daemon.json >/dev/null <<EOF
{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}
EOF
  sudo systemctl restart docker'
```

> `systemctl restart docker` reinicia TODOS los contenedores (unos segundos de downtime). Todos tienen `restart: unless-stopped`, así que vuelven solos. Hacer en hora valle.

Verificación:
```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 'docker ps --format "{{.Names}}: {{.Status}}"'
# Todos Up
```

### 5b. Swap de 2 GB

```bash
ssh -i ~/.ssh/sectorve_vps deploy@154.29.72.136 '
  sudo fallocate -l 2G /swapfile &&
  sudo chmod 600 /swapfile &&
  sudo mkswap /swapfile &&
  sudo swapon /swapfile &&
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab'
```

Verificación: `free -h` → Swap con 2 GB.

### 5c. Bindear puertos internos a localhost (opcional, bajo riesgo hoy)

Neo4j (7474/7687) y TokenizacionGrafo (3000/8000) están en `0.0.0.0`. Hoy UFW los bloquea. Cambiar el bind en los compose a `127.0.0.1:PORT:PORT` y `up -d`. Requiere editar `docker-compose.yml` de TokenizacionGrafo.

---

## Fase 6 — Monitoreo continuo (watchdog)

Cron local (Mac) o en VPS que alerta por disco/health. Ejemplo de watchdog simple:

```bash
# En el VPS, cada hora: si disco > 85% o algún contenedor unhealthy, loguear
df -h / | awk 'NR==2 {gsub("%",""); if ($5 > 85) print "DISCO ALTO: " $5 "%"}'
docker ps --format '{{.Names}} {{.Status}}' | grep -v healthy | grep -v 'Up'
```

Se puede automatizar con el cron de Hermes (`cronjob`) con `deliver` a Telegram para que David reciba la alerta.

---

## Orden recomendado de ejecución

```
0 → 1 → 2 → 3 → 4 → 5 → 6
```

- **Fase 0-1-2** se pueden hacer de corrido (todo seguro, alto impacto): snapshot + prune + backup.
- **Fase 3 y 4b** requieren aprobación de David (revisar diff y revocar token).
- **Fase 4a** es trivial y segura, se puede colar con la 1-2.
- **Fase 5a** implica reinicio de Docker → coordinar hora.
- **Fase 6** es opcional pero cierra el ciclo de "control sano".

---

## Dependencias y notas

- **Fase 3 antes que Fase 5c**: el `docker-compose.prod.yml` no commiteado incluye los mounts; conviene reconciliar git antes de seguir editando el compose (evita más divergencia).
- **Fase 2 antes que todo lo destructivo**: garantiza que cualquier error posterior tenga restauración.
- **Neo4j**: decisión de David entre snapshot sin downtime vs dump con stop breve.
- **Token TFG**: la rotación real (revocar en GitHub) es acción de David; yo solo cambio el remote.
