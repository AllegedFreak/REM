# actualizar_futuros.py
# Futuros de dólar (contratos DLR) de Matba-Rofex/A3, desde la API pública de su Centro de Estadísticas de Mercado (CEM):
# https://apicem.matbarofex.com.ar/api/v2/closing-prices (la fuente oficial; sin usuario). Hay datos desde ene-2020.
# Por día y contrato: precio de ajuste, apertura, máximo, mínimo, cierre, cierre anterior, variación, volumen, cantidad de
# operaciones, interés abierto (y su cambio) y tasa implícita. Incluye las opciones sobre dólar (calls y puts con su precio de
# ejercicio): se guarda todo lo que entrega la fuente, por si deja de estar disponible.
#   - la primera vez baja todo desde 2020; después, solo los días nuevos (y repite los últimos 10 días por si hubo correcciones)
#   - nunca borra: si la fuente no responde, conserva lo guardado y avisa
# Salida: datos\dolar_futuro_cem.parquet; informe datos\informe_futuros.txt
# Uso: py -3.13 actualizar_futuros.py [--desde AAAA-MM-DD]
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
SALIDA = CARPETA / "dolar_futuro_cem.parquet"
INFORME = CARPETA / "informe_futuros.txt"
API = "https://apicem.matbarofex.com.ar/api/v2/closing-prices"
INICIO = "2020-01-01"
DIAS_REPASO = 10


def bajar_tramo(desde, hasta):
    filas, pagina = [], 1
    while True:
        q = urllib.parse.urlencode({"product": "DLR", "from": desde, "to": hasta, "page": pagina, "pageSize": 500})
        for intento in range(4):
            try:
                r = urllib.request.urlopen(urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "Mozilla/5.0",
                                                                                          "Accept": "application/json"}), timeout=300)
                j = json.loads(r.read())
                break
            except Exception:
                if intento == 3:
                    raise
                time.sleep(15 * (intento + 1))
        filas += j.get("data", [])
        total = j.get("totalEntries") or 0
        tam = j.get("pageSize") or len(j.get("data", [])) or 1
        if pagina * tam >= total or not j.get("data"):
            return filas
        pagina += 1
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=None)
    ap.add_argument("--aviso", action="store_true")
    args = ap.parse_args()
    informe = [f"=== FUTUROS DE DÓLAR (CEM Matba-Rofex/A3) | {datetime.now():%Y-%m-%d %H:%M} ==="]
    previo = pd.read_parquet(SALIDA) if SALIDA.exists() else pd.DataFrame()
    if args.desde:
        desde = pd.Timestamp(args.desde)
    elif len(previo):
        desde = pd.to_datetime(previo["fecha"]).max() - pd.Timedelta(days=DIAS_REPASO)
    else:
        desde = pd.Timestamp(INICIO)
    hoy = pd.Timestamp.today().normalize()
    codigo = 0
    try:
        filas = []
        for ini in pd.date_range(desde.to_period("M").to_timestamp(), hoy, freq="MS"):   # de a un mes
            fin = min(ini + pd.offsets.MonthEnd(0), hoy)
            filas += bajar_tramo(max(ini, desde).strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d"))
            time.sleep(1)
        nuevo = pd.DataFrame(filas)
        if len(nuevo):
            nuevo["fecha"] = pd.to_datetime(nuevo["dateTime"]).dt.tz_localize(None).dt.normalize()
            nuevo = nuevo.drop(columns=["dateTime"])
        todo = pd.concat([previo, nuevo], ignore_index=True) if len(previo) else nuevo
        todo = todo.drop_duplicates(["fecha", "symbol"], keep="last").sort_values(["fecha", "symbol"])
        if len(todo) < len(previo):
            raise RuntimeError("la base nueva tiene menos filas que la anterior: no se reemplaza")
        todo.to_parquet(SALIDA, index=False)
        ult = todo[(todo["fecha"] == todo["fecha"].max()) & todo["symbol"].str.fullmatch(r"DLR\d{6}")].copy()
        ult = ult.assign(v=pd.to_datetime(ult["symbol"].str[3:], format="%m%Y")).sort_values("v")   # solo futuros, por vencimiento
        informe.append(f"Futuros guardados: {len(todo):,} filas (contrato x día), {todo['fecha'].nunique()} días, "
                       f"{todo['fecha'].min():%Y-%m-%d} a {todo['fecha'].max():%Y-%m-%d} ({len(nuevo):,} filas bajadas ahora)")
        informe.append(f"Curva del {todo['fecha'].max():%Y-%m-%d}: " + " | ".join(
            f"{s}: {a:,.1f} ({ti:.1f}%)" for s, a, ti in zip(ult["symbol"], ult["settlement"], ult["impliedRate"].fillna(0))))
    except Exception as e:
        codigo = 1 if len(previo) else 2
        informe.append(f"ERROR: No se pudo actualizar ({type(e).__name__}: {e}); se conservan los datos guardados.")
    texto = "\n".join(informe)
    print(texto)
    CARPETA.mkdir(exist_ok=True)
    INFORME.write_text(texto, encoding="utf-8")
    sys.exit(codigo)


if __name__ == "__main__":
    main()
