# actualizar_actividad.py
# Actividad económica oficial del INDEC, para pronosticar el PIB como el REM:
#   1) PIB trimestral DESESTACIONALIZADO (oferta y demanda globales, millones de pesos de 2004): el INDEC publica un
#      archivo por cada publicación trimestral (sh_oferta_demanda_desest_MM_AA.xls, desde jun-2016). Se bajan y guardan
#      TODAS las versiones (el PIB se revisa: el REM se evalúa contra el primer dato publicado de cada trimestre).
#      -> datos\copias\pib_desest\ (archivos originales) y datos\pib_desest_versiones.parquet
#         (version = mes de publicación, anio, trimestre, pib, importaciones, consumo privado, consumo público, FBCF, exportaciones)
#   2) EMAE mensual (sh_emae_mensual_base2004.xls): original, desestacionalizado y tendencia-ciclo. El INDEC pisa el
#      archivo en cada publicación: se guarda una copia cada vez que cambia (datos\copias\emae\) para armar nuestras propias
#      versiones hacia adelante. -> datos\emae.parquet (mes, original, desest, tendencia) y datos\emae_versiones.parquet
# Uso: py -3.13 actualizar_actividad.py
import hashlib
import io
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
C = BASE / "datos"
URL = "https://www.indec.gob.ar/ftp/cuadros/economia/"
H = {"User-Agent": "Mozilla/5.0"}
TRIM = {"I": 1, "II": 2, "III": 3, "IV": 4}
MESES = {m: i for i, m in enumerate(["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
                                     "Octubre", "Noviembre", "Diciembre"], 1)}


def bajar(nombre):
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + nombre, headers=H), timeout=90) as r:
            b = r.read()
        return b if len(b) > 40000 else None          # el INDEC devuelve una página de ~37 KB cuando el archivo no existe
    except Exception:
        return None


def leer_pib(b):
    d = pd.read_excel(io.BytesIO(b), sheet_name=0, header=None)
    cols = ["anio", "trim", "pib", "importaciones", "consumo_privado", "consumo_publico", "fbcf", "exportaciones"]
    # las columnas se ubican por su título (los archivos viejos tienen una columna vacía de más)
    fila = next(i for i in range(min(15, len(d))) if any(str(v).strip() == "PIB" for v in d.iloc[i]))
    tit = [str(v).strip().lower() for v in d.iloc[fila]]
    buscar = {"pib": "pib", "importaciones": "importaciones", "consumo_privado": "consumo privado",
              "consumo_publico": "consumo público", "fbcf": "fbcf", "exportaciones": "exportaciones"}
    idx = {k: next((j for j, t in enumerate(tit) if t.startswith(v)), None) for k, v in buscar.items()}
    d = pd.DataFrame({"anio": d.iloc[:, 0], "trim": d.iloc[:, 1],
                      **{k: (d.iloc[:, j] if j is not None else np.nan) for k, j in idx.items()}})
    d["anio"] = pd.to_numeric(d["anio"], errors="coerce").ffill()
    d = d[d["trim"].astype(str).str.strip().isin(TRIM)].copy()
    d["trim"] = d["trim"].astype(str).str.strip().map(TRIM)
    for c in cols[2:]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["pib"])


def leer_emae(b):
    d = pd.read_excel(io.BytesIO(b), sheet_name=0, header=None).iloc[:, :7]
    d.columns = ["anio", "mes", "original", "var_ia", "desest", "var_desest", "tendencia"]
    d["anio"] = pd.to_numeric(d["anio"], errors="coerce").ffill()
    d = d[d["mes"].astype(str).str.strip().isin(MESES)].copy()
    d["mes"] = [pd.Period(f"{int(a)}-{MESES[m.strip()]:02d}", "M") for a, m in zip(d["anio"], d["mes"])]
    for c in ("original", "desest", "tendencia"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d[["mes", "original", "desest", "tendencia"]].dropna(subset=["original"])


def main():
    L = [f"=== ACTIVIDAD (INDEC): PIB desestacionalizado y EMAE | {datetime.now():%Y-%m-%d %H:%M} ==="]
    cp = C / "copias" / "pib_desest"
    cp.mkdir(parents=True, exist_ok=True)
    hoy = pd.Period(datetime.now(), "M")
    nuevas = 0
    for y in range(2016, hoy.year + 1):
        for m in (3, 6, 9, 12):
            v = pd.Period(f"{y}-{m:02d}", "M")
            if v > hoy:
                continue
            n = f"sh_oferta_demanda_desest_{m:02d}_{str(y)[2:]}.xls"
            if (cp / n).exists() and v < hoy - 3:
                continue
            b = bajar(n)
            if b and (not (cp / n).exists() or (cp / n).read_bytes() != b):
                (cp / n).write_bytes(b)
                nuevas += 1
    filas = []
    for p in sorted(cp.glob("sh_oferta_demanda_desest_*.xls")):
        mm, yy = p.stem.split("_")[-2:]
        try:
            d = leer_pib(p.read_bytes())
        except Exception as e:
            L.append(f"   no se pudo leer {p.name}: {type(e).__name__}")
            continue
        d.insert(0, "version", str(pd.Period(f"20{yy}-{mm}", "M")))
        filas.append(d)
    V = pd.concat(filas, ignore_index=True)
    V.to_parquet(C / "pib_desest_versiones.parquet", index=False)
    ult = V[V["version"] == V["version"].max()].sort_values(["anio", "trim"])
    q = ult["pib"].pct_change() * 100
    L.append(f"PIB desestacionalizado: {V['version'].nunique()} versiones ({V['version'].min()} a {V['version'].max()}); nuevas o cambiadas: {nuevas}")
    L.append(f"   Último trimestre: {int(ult['anio'].iloc[-1])}-T{int(ult['trim'].iloc[-1])}: {q.iloc[-1]:+.2f}% contra el trimestre anterior")
    # EMAE
    ce = C / "copias" / "emae"
    ce.mkdir(parents=True, exist_ok=True)
    b = bajar("sh_emae_mensual_base2004.xls")
    if b is None:
        L.append("ALERTAS: no se pudo bajar el EMAE (se usa la última copia).")
        prev = sorted(ce.glob("emae_*.xls"))
        b = prev[-1].read_bytes() if prev else None
    else:
        h = hashlib.sha1(b).hexdigest()[:10]
        if not any(p.read_bytes() == b for p in ce.glob("emae_*.xls")):
            (ce / f"emae_{datetime.now():%Y-%m-%d}_{h}.xls").write_bytes(b)
            L.append("   EMAE: versión nueva guardada en datos\\copias\\emae")
    if b:
        E = leer_emae(b)
        E["mes"] = E["mes"].astype(str)
        E.to_parquet(C / "emae.parquet", index=False)
        vers = []
        for p in sorted(ce.glob("emae_*.xls")):
            x = leer_emae(p.read_bytes())
            x.insert(0, "version", p.stem.split("_")[1])
            x["mes"] = x["mes"].astype(str)
            vers.append(x)
        pd.concat(vers).to_parquet(C / "emae_versiones.parquet", index=False)
        L.append(f"EMAE: {E['mes'].min()} a {E['mes'].max()}; último mes desestacionalizado {E['desest'].pct_change().iloc[-1] * 100:+.2f}% "
                 f"contra el mes anterior; versiones guardadas: {len(vers)}")
    L.append("ALERTAS: ninguna. Todo en orden." if not any(x.startswith("ALERTAS") for x in L) else "")
    texto = "\n".join(x for x in L if x)
    print(texto)
    (C / "informe_actividad.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
