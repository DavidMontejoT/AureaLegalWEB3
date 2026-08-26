# Acta Runbook — Producción VPS SectorVe (154.29.72.136)

> Fecha: 2026-08-16 · Runbook Senior (riesgo incremental, evidencia, rollback)
> Ejecutores: Hermes (agente) + David Montejo (aprobaciones y decisiones)
> Base: `revision-servidor-sectorve.md` (2026-08-13) · `estrategia-remediacion-servidor.md`

---

## Resumen ejecutivo

Se ejecutó el runbook de reducción de riesgo operativo sobre el VPS single-node
que aloja SectorVe, Centinela, Contralitigator, TokenizacionGrafo y los sitios
estáticos. Se cerraron 2 hallazgos críticos de la auditoría (exposición real de
puertos Docker a internet — no detectada en la auditoría original — y salud
intermitente del motor de análisis de Contralitigator) y se dejó monitoreo
activo (watchdog + offsite). Durante la ejecución se produjo un incidente
técnico menor (desinstalación colateral de ufw por apt) que fue detectado y
corregido en el acto.

## Estado por paso

| Paso | Resultado | Evidencia clave |
|------|-----------|-----------------|
| 1. Documentación | ✅ Completado | Commit `5f64966` (docs + acta) |
| 2. Healthcheck api-py (Contralitigator) | ✅ Completado | timeout 20s, urllib 15s, interval 30s, retries 3, start_period 30s; validación 10 min (20 muestras): healthy 20/20, público 200 20/20, 0 transiciones a unhealthy, 1 timeout individual (informativo) |
| 3. Swap 2GB | ⏸️ NO implementado (decisión David) | Reevaluación condicional: OOM-kill, RAM sostenida >6GB, o nuevo servicio pesado |
| 4. Bind/seguridad puertos TFG | ✅ Completado (modo C corregido) | Reglas DROP en DOCKER-USER (iface externa detectada → 3000/8000/4173, v4+v6); UFW deny explícito; verificación externa desde Mac: timeout en los 3 puertos, dominios 200 |
| R1. Persistencia DOCKER-USER | ✅ Completado (opción C) | Capa A: `/home/deploy/hardening/ports-block.sh` + unit systemd `hardened-ports.service` (After=docker.service, enabled, activo, idempotente probado). Capa B: watchdog correctivo en `vps-watchdog.py` — simulación real: borrada regla 4173 → restaurada automáticamente + alerta |
| 5. Watchdog | ✅ Completado | Cron Hermes `3e41f81b8ef5` (cada hora, Telegram, solo cambios de estado); script `~/.hermes/scripts/vps-watchdog.py` |
| 6. Offsite backups | ✅ Completado | Cron Hermes `dc8f8c0b8874` (23:00 local diario); 12 dumps en `~/aurealegal-backups/` (primera copia verificada) |

## Hallazgos nuevos (2026-08-16)

1. **CRÍTICO — Puertos Docker expuestos a internet a pesar de UFW activo.**
   `tokenizacion-backend` (8000), `tokenizacion-frontend` (3000) y
   `contralitigator-web` (4173) publicados en 0.0.0.0 eran alcanzables desde
   internet (verificado con curl externo: HTTP 200/404). Causa: el tráfico de
   puertos publicados por Docker circula por PREROUTING/FORWARD (cadenas
   DOCKER), no por INPUT — el `default deny incoming` de UFW no los alcanza.
   **La auditoría del 13-ago afirmaba lo contrario ("UFW los bloquea") — era
   incorrecto.** Mitigación aplicada: reglas DROP en `DOCKER-USER` limitadas a
   `eth0` (no afectan a Caddy, que entra por bridges internas) + deny explícito
   en UFW + verificación externa real.
   Hallazgo adicional de la auditoría: Caddy enruta a TFG vía
   `host.docker.internal` (172.17.0.1) — mejora arquitectónica pendiente:
   conectar Caddy a la red de TFG y usar nombres de servicio.

2. **Healthcheck de api-py frágil (Contralitigator).** El HEALTHCHECK del
   Dockerfile (`--timeout=5s`) generaba falsos negativos. Override en compose:
   timeout 20s / urllib 15s / retries 3 / start_period 30s. El api-py tiene
   latencia intermitente de /health (timeouts individuales ~5% de los checks
   en la ventana validada) cuando procesa jobs OCR/LLM (GIL de Python + uvicorn
   single-worker). No es caída de servicio: healthy 20/20 muestras, público
   200 20/20.
   **Hallazgo técnico abierto:** separar jobs pesados del proceso web
   (uvicorn --workers 2 y/o cola externa) — fuera del alcance de este runbook.

3. **Conflicto ufw ↔ iptables-persistent (empírico).** Instalar
   `iptables-persistent` desinstala `ufw` y viceversa en este sistema
   (verificado dos veces). Se priorizó UFW (firewall de sistema). Consecuencia:
   la persistencia de las reglas DOCKER-USER no puede usar iptables-persistent.
   **PENDIENTE DE DECISIÓN** (ver Riesgos residuales).

## Incidente durante la ejecución

- **Desinstalación colateral de ufw** por `apt-get install iptables-persistent`
  (efecto del conflicto de paquetes). Detectado al fallar el watchdog
  ("command not found"); confirmado con `dpkg` (estado `rc`). Corregido
  reinstalando ufw y activándolo (`ufw enable`); reglas de /etc/ufw intactas
  (incluidos los denies nuevos). Sin impacto en servicios (el tráfico sigue
  por iptables en memoria). Lección: verificar integridad del firewall tras
  cualquier instalación de paquetes de red.

## Riesgos residuales (abiertos)

| # | Riesgo | Severidad | Mitigación actual | Acción recomendada |
|---|--------|-----------|-------------------|--------------------|
| R1 | Reglas DOCKER-USER se pierden en reboot | ✅ MITIGADO | Capa A: unit systemd `hardened-ports.service` (boot, ventana 0) + Capa B: watchdog correctivo (runtime ≤1h + alerta). Simulación real validada 2026-08-17 | Ninguna (monitorear 1 semana) |
| R2 | Caddy → TFG por `host.docker.internal` (dependencia de la IP docker0) | BAJA | Funciona hoy; documentado | Migrar Caddy a red interna TFG (opción A del Paso 4) en ventana coordinada |
| R3 | api-py: latencia /health bajo carga OCR/LLM | BAJA | Healthcheck alineado a 20s | Fix de código: workers adicionales o cola externa |
| R4 | Sin swap | BAJA | RAM con margen (pico 2.8/9.7GB, 0 OOM en 7 días) | Reevaluar si OOM, >6GB sostenido o servicio pesado nuevo |
| R5 | Backups en VPS y Mac no versionados fuera de máquinas locales | BAJA | Offsite diario al Mac (cron activo) | Opcional: añadir copia en otro proveedor (S3/Drive) |
| R6 | Single-node sin HA | ESTRUCTURAL | Backups diarios + offsite | Documentar SLA: RPO 24h, RTO <1h (restauración manual) |

## Próxima reevaluación

- **Inmediata:** decisión de persistencia R1 (script+systemd vs watchdog correctivo vs riesgo aceptado).
- **Semanal (primera semana):** revisar entregas del watchdog (2-3 alertas esperadas: ninguna) y primer ciclo offsite completo.
- **Condicional:** swap si OOM / RAM >6GB sostenido / servicio pesado nuevo. Migración Caddy-TFG en la siguiente ventana de mantenimiento (02:00-04:00 UTC) si David lo aprueba.

## Sign-off

| Rol | Responsable | Firma |
|-----|-------------|-------|
| Técnico | David Montejo | ______________ |
| Negocio | David Montejo | ______________ |

Evidencia completa: salidas de comandos en sesión Hermes del 2026-08-16 (23:47Z – 20:30Z local). Documentos base actualizados: `revision-servidor-sectorve.md`, `estrategia-remediacion-servidor.md`.
