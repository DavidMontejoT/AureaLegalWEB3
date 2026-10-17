#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lista los leads y el estado de sus avisos. Solo lectura.

    docker exec leads python listar.py            # ultimos 20
    docker exec leads python listar.py 100        # ultimos 100
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HORA_LOCAL = timezone(timedelta(hours=-5), "COT")


def main() -> None:
    limite = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    ruta = Path(os.environ.get("LEADS_DATOS", "/datos")) / "leads.db"
    c = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    filas = c.execute(
        "SELECT l.id, l.creado_en, l.nombre, l.empresa, l.email, l.interes, l.duplicado,"
        " group_concat(a.canal || ':' || a.estado, ' ') FROM leads l"
        " LEFT JOIN avisos a ON a.lead_id = l.id GROUP BY l.id ORDER BY l.id DESC LIMIT ?",
        (limite,),
    ).fetchall()
    for id_, ts, nombre, empresa, email, interes, duplicado, avisos in reversed(filas):
        fecha = datetime.fromtimestamp(ts, HORA_LOCAL).strftime("%Y-%m-%d %H:%M")
        marca = " (duplicado)" if duplicado else ""
        print(f"#{id_}  {fecha}  {nombre} <{email}>  {empresa or '—'}  | {interes or '—'}{marca}  [{avisos or '-'}]")
    pendientes = c.execute("SELECT estado, COUNT(*) FROM avisos GROUP BY estado").fetchall()
    print("avisos:", dict(pendientes) or "{}")


if __name__ == "__main__":
    main()
