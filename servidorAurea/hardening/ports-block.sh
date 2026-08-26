#!/bin/bash
# Hardening de puertos Docker expuestos — reaplica reglas DOCKER-USER.
# Runbook 2026-08-16 · Paso 4 modo C + R1 opción A · Idempotente · Senior-safe.
#
# Ajustes de diseño:
#  - Interfaz externa detectada dinámicamente (default route; fallback primera no-loopback)
#  - ip6tables nunca aborta el script (puede no existir o no aplicar en el host)
#  - Crea la cadena DOCKER-USER y su jump desde FORWARD si Docker no las dejó
#  - set -euo pipefail + verificación explícita por puerto al final
set -euo pipefail

PORTS="3000 8000 4173"

# ── 1. Detectar interfaz externa ──────────────────────────────────────────────
IFACE=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
if [ -z "$IFACE" ]; then
    # Sin ruta por defecto: primera interfaz física no-loopback
    IFACE=$(ip -o link show 2>/dev/null | awk -F': ' '$2 != "lo" {gsub(/@.*/,"",$2); print $2; exit}')
fi
if [ -z "$IFACE" ]; then
    echo "ERROR: no se pudo determinar la interfaz externa. Abortando."
    exit 1
fi
echo "Interfaz externa detectada: $IFACE"

# ── 2. Asegurar cadena DOCKER-USER (por si Docker no la creó aún) ────────────
ensure_chain_v4() {
    iptables -N DOCKER-USER 2>/dev/null || true
    if ! iptables -C FORWARD -j DOCKER-USER 2>/dev/null; then
        iptables -I FORWARD 1 -j DOCKER-USER
    fi
}
ensure_chain_v6() {
    if command -v ip6tables >/dev/null 2>&1; then
        ip6tables -N DOCKER-USER 2>/dev/null || true
        if ! ip6tables -C FORWARD -j DOCKER-USER 2>/dev/null; then
            ip6tables -I FORWARD 1 -j DOCKER-USER 2>/dev/null || true
        fi
    fi
}

# ── 3. Aplicar reglas (idempotente) ───────────────────────────────────────────
apply_v4() { # $1 = puerto
    ensure_chain_v4
    if iptables -C DOCKER-USER -i "$IFACE" -p tcp --dport "$1" -j DROP 2>/dev/null; then
        echo "v4 $1: ya presente"
    else
        iptables -I DOCKER-USER 1 -i "$IFACE" -p tcp --dport "$1" -j DROP
        echo "v4 $1: aplicada"
    fi
}
apply_v6() { # $1 = puerto — nunca aborta el script
    if ! command -v ip6tables >/dev/null 2>&1; then
        echo "v6 $1: ip6tables no disponible (omitido)"
        return 0
    fi
    ensure_chain_v6
    if ip6tables -C DOCKER-USER -i "$IFACE" -p tcp --dport "$1" -j DROP 2>/dev/null; then
        echo "v6 $1: ya presente"
    else
        if ip6tables -I DOCKER-USER 1 -i "$IFACE" -p tcp --dport "$1" -j DROP 2>/dev/null; then
            echo "v6 $1: aplicada"
        else
            echo "v6 $1: ERROR al aplicar (continuando sin abortar)"
        fi
    fi
}

for port in $PORTS; do
    apply_v4 "$port"
    apply_v6 "$port"
done

# ── 4. Verificación final con salida clara ────────────────────────────────────
echo "=== VERIFICACION (puertos bloqueados en $IFACE) ==="
FAIL=0
for port in $PORTS; do
    if iptables -C DOCKER-USER -i "$IFACE" -p tcp --dport "$port" -j DROP 2>/dev/null; then
        echo "OK  v4 $port"
    else
        echo "FALTA v4 $port"
        FAIL=1
    fi
    if command -v ip6tables >/dev/null 2>&1; then
        if ip6tables -C DOCKER-USER -i "$IFACE" -p tcp --dport "$port" -j DROP 2>/dev/null; then
            echo "OK  v6 $port"
        else
            echo "FALTA v6 $port"
            FAIL=1
        fi
    else
        echo "SKIP v6 $port (sin ip6tables en el host)"
    fi
done

if [ "$FAIL" -eq 0 ]; then
    echo "RESULTADO: OK — todas las reglas presentes (v4/v6) en $IFACE"
else
    echo "RESULTADO: ERROR — hay reglas faltantes (revisar arriba)"
    exit 1
fi
