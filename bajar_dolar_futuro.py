# bajar_dolar_futuro.py
# Curva de futuros de dólar (contratos DLR, Matba-Rofex/A3) en fechas pasadas, desde sinelefantesblancos.com.ar: su página
# "Dólar futuro" consulta los cierres oficiales de A3 para cualquier fecha desde el 02-01-2020 (formulario con respuesta JSON).
# Tercero (no hay fuente oficial gratuita con historia: BYMADATA es paga; ver CLAUDE.md). Se guarda cada respuesta tal cual.
#   - fechas: por cada mes, el último día hábil (calendario A3500 del BCRA) hasta el día 25 (mismo corte que usa el modelo A
#     para el dólar) y el último día hábil del mes (cerca del relevamiento del REM)
#   - una consulta a la vez, con pausa (es un sitio personal: no cargarlo)
#   - se puede retomar: saltea las fechas ya guardadas
# Salida: datos\dolar_futuro\AAAA-MM-DD.json (crudo) y datos\dolar_futuro.parquet (fecha, corte, especie, vencimiento,
#         plazo_dias, cierre, dolar_spot, tasa_implicita_pct, tna, tea)
# Uso: py -3.13 bajar_dolar_futuro.py [--desde AAAA-MM]
import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
CRUDOS = CARPETA / "dolar_futuro"
SALIDA = CARPETA / "dolar_futuro.parquet"
URL = "https://www.sinelefantesblancos.com.ar/inversiones/dolar-futuro"
PAUSA = 4            # segundos entre consultas
DIA_CORTE = 25


def sesion():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (investigación propia; consultas espaciadas)")]
    h = op.open(URL, timeout=120).read().decode("utf-8", "replace")
    return op, re.search(r'name="csrf_token" value="([^"]+)"', h).group(1)


def consultar(op, tok, fecha):
    data = urllib.parse.urlencode({"csrf_token": tok, "primerafecha": fecha, "button": "Calcular"}).encode()
    req = urllib.request.Request(URL, data=data, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
                                                           "Referer": URL})
    return json.loads(op.open(req, timeout=400).read())


def fechas_objetivo(desde):
    """Último día hábil hasta el 25 y último día hábil de cada mes, según los días con dato del A3500 (BCRA)."""
    d = pd.read_parquet(CARPETA / "bcra_series.parquet")
    dias = pd.to_datetime(d.loc[d["serie"] == "tc_mayorista_a3500", "fecha"]).sort_values().drop_duplicates()
    dias = dias[dias >= pd.Timestamp(desde)]
    hoy = pd.Timestamp.today().normalize()
    out = []
    for mes, g in dias.groupby(dias.dt.to_period("M")):
        h25 = g[g.dt.day <= DIA_CORTE]
        if len(h25):
            out.append((h25.max().strftime("%Y-%m-%d"), "dia25"))
        if mes < hoy.to_period("M"):
            out.append((g.max().strftime("%Y-%m-%d"), "fin_de_mes"))
    return out


def armar_parquet():
    filas = []
    for f in sorted(CRUDOS.glob("*.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        if not j.get("ok"):
            continue
        for r in j.get("resultados", []):
            filas.append({"fecha": pd.Timestamp(j["fecha"]), "corte": j.get("_corte"), "especie": r["especie"],
                          "vencimiento": pd.to_datetime(r["vto_display"], dayfirst=True), "plazo_dias": r["plazo"],
                          "cierre": r["cierre"], "dolar_spot": j.get("dolar"),
                          "tasa_implicita_pct": r["tasaimplicita"] if r.get("tasas_disponibles") else None,
                          "tna": r["tna"] if r.get("tasas_disponibles") else None,
                          "tea": r["tea"] if r.get("tasas_disponibles") else None})
    df = pd.DataFrame(filas)
    if len(df):
        df.to_parquet(SALIDA, index=False)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2020-01")
    ap.add_argument("--aviso", action="store_true")
    args = ap.parse_args()
    CRUDOS.mkdir(parents=True, exist_ok=True)
    objetivos = [(f, c) for f, c in fechas_objetivo(args.desde + "-01") if not (CRUDOS / f"{f}.json").exists()]
    print(f"Fechas a consultar: {len(objetivos)} (pausa de {PAUSA} s entre consultas)", flush=True)
    op, tok, errores = None, None, []
    for i, (fecha, corte) in enumerate(objetivos, 1):
        for intento in range(3):
            try:
                if op is None:
                    op, tok = sesion()
                j = consultar(op, tok, fecha)
                j["_corte"] = corte
                (CRUDOS / f"{fecha}.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
                if not j.get("ok"):
                    errores.append((fecha, j.get("mensaje")))
                break
            except Exception as e:
                op = None          # renovar sesión y código de seguridad
                time.sleep(20 * (intento + 1))
                if intento == 2:
                    errores.append((fecha, f"{type(e).__name__}: {e}"))
        if i % 10 == 0 or i == len(objetivos):
            print(f"  {i}/{len(objetivos)} ({fecha})", flush=True)
        time.sleep(PAUSA)
    df = armar_parquet()
    print(f"Listo. {df['fecha'].nunique() if len(df) else 0} fechas, {len(df)} filas (contrato x fecha) en {SALIDA}")
    if errores:
        print(f"Sin datos o con error: {len(errores)}: {errores[:10]}")
    sys.exit(1 if errores else 0)


if __name__ == "__main__":
    main()
