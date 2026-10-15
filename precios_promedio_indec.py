# precios_promedio_indec.py
# Lee la planilla oficial del INDEC "Precios al consumidor de una selección de productos" (sh_ipc_precios_promedio.xls):
# ~90 productos con su unidad, precio promedio mensual desde jun-2017, hojas Nacional y GBA.
# La planilla viene "ancha" (años y meses en columnas): se pasa a formato largo.
# Salida: datos/precios_promedio_indec.parquet (region, producto, unidad, mes, precio)
# Uso: py -3.13 precios_promedio_indec.py
import sys
import urllib.request
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
COPIA = CARPETA / "copias" / "sh_ipc_precios_promedio.xls"
URL = "https://www.indec.gob.ar/ftp/cuadros/economia/sh_ipc_precios_promedio.xls"
SALIDA = CARPETA / "precios_promedio_indec.parquet"
MESES = {m: i for i, m in enumerate(["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
                                     "Octubre", "Noviembre", "Diciembre"], 1)}


def leer_hoja(h, hoja):
    """Dos formatos: hoja 'Nacional' = Región | Producto | Unidad | meses (14 productos x 6 regiones);
    hoja 'GBA' = Variedad | Unidad | meses (~60 variedades, solo GBA)."""
    fila_enc = h.index[h.apply(lambda r: r.astype(str).str.strip().isin(["Productos seleccionados", "Variedad"]).any(), axis=1)][0]
    enc = h.loc[fila_enc].astype(str).str.strip()
    col_prod = enc[enc.isin(["Productos seleccionados", "Variedad"])].index[0]
    col_unidad = col_prod + 1
    col_region = 0 if enc.get(0) == "Región" else None
    anios = h.loc[fila_enc].copy()
    meses = h.loc[fila_enc + 1]
    anio_actual, columnas = None, {}
    for c in h.columns[col_unidad + 1:]:
        a = str(anios[c])
        if a and a != "nan":
            digitos = "".join(ch for ch in a if ch.isdigit())
            anio_actual = int(digitos) if digitos else anio_actual
        m = str(meses[c]).strip()
        if m in MESES and anio_actual:
            columnas[c] = pd.Period(f"{anio_actual}-{MESES[m]:02d}", "M")
    datos = h.loc[fila_enc + 2:]
    datos = datos[datos[col_prod].notna() & datos[col_unidad].notna()]
    filas = []
    for _, r in datos.iterrows():
        region = str(r[col_region]).strip() if col_region is not None else "GBA"
        for c, per in columnas.items():
            v = pd.to_numeric(r[c], errors="coerce")
            if pd.notna(v):
                filas.append({"hoja": hoja, "region": region, "producto": str(r[col_prod]).strip(),
                              "unidad": str(r[col_unidad]).strip(), "mes": per.to_timestamp(how="end").date(), "precio": float(v)})
    return pd.DataFrame(filas)


def main():
    COPIA.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            COPIA.write_bytes(r.read())
    except Exception:
        if not COPIA.exists():
            raise
    hojas = pd.read_excel(COPIA, sheet_name=None, header=None)
    df = pd.concat([leer_hoja(h, nombre) for nombre, h in hojas.items()], ignore_index=True)
    df.to_parquet(SALIDA, index=False)
    ult = df["mes"].max()
    for hoja, d in df.groupby("hoja"):
        print(f"\n== hoja {hoja}: {d['producto'].nunique()} productos, regiones {sorted(d['region'].unique())}, {d['mes'].min()} a {d['mes'].max()}")
        t = d[d["mes"] == ult].pivot_table(index=["producto", "unidad"], columns="region", values="precio").round(2)
        print(t.to_string())
    print(f"\nGuardado: {SALIDA}")


if __name__ == "__main__":
    main()
