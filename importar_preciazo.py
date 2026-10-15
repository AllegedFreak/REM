# importar_preciazo.py
# Resume los días históricos de SEPA archivados por el proyecto preciazo (E:\SEPA\historico_preciazo\sepa_<día>.tar.zst,
# bajados con bajar_preciazo.py) con el MISMO resumen que actualizar_sepa.py: precio mediano por producto x cadena x
# bandera x región del IPC -> E:\SEPA\resumen\sepa_<día>.parquet. Así indice_sepa.py los usa igual que los días propios.
# - No pisa días que ya tienen resumen (los bajados por nosotros mandan).
# - Si un día trae la misma cadena más de una vez (varias descargas en el día), usa la última.
# - Registro: E:\SEPA\historico_preciazo\importados.csv (día, cadenas, precios, filas, segundos, error).
# Uso: py -3.13 importar_preciazo.py [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--max N]
import argparse
import io
import re
import zipfile
import shutil
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from actualizar_sepa import RESUMEN, resumir_cadena

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ORIGEN = Path("E:/SEPA/historico_preciazo")
TEMPORAL = Path("E:/SEPA/tmp_preciazo")
REGISTRO = ORIGEN / "importados.csv"
PATRON = re.compile(r"comercio-sepa-(\d+)_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")


def extraer(archivo, destino):
    """Descomprime el .tar.zst en destino (solo los csv de cada cadena). Devuelve {cadena: carpeta} con la última
    descarga del día de cada cadena."""
    import zstandard
    carpetas = {}
    with open(archivo, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as r, tarfile.open(fileobj=r, mode="r|") as t:
        for m in t:
            partes = Path(m.name).parts
            # algunos días (ene-feb 2025) traen cada cadena como un ZIP propio en lugar de una carpeta con los csv
            if m.isfile() and partes[-1].endswith(".zip"):
                mm = PATRON.search(partes[-1])
                if not mm:
                    continue
                carpeta = destino / partes[-1][:-4]
                carpeta.mkdir(parents=True, exist_ok=True)
                try:
                    with t.extractfile(m) as src:
                        zipfile.ZipFile(io.BytesIO(src.read())).extractall(carpeta)
                except zipfile.BadZipFile:
                    continue
                for csv in list(carpeta.rglob("*.csv")):          # por si el zip trae una subcarpeta
                    if csv.parent != carpeta:
                        shutil.move(str(csv), carpeta / csv.name)
                sello = mm.group(2) + mm.group(3)
                if mm.group(1) not in carpetas or sello > carpetas[mm.group(1)][0]:
                    carpetas[mm.group(1)] = (sello, carpeta)
                continue
            if not m.isfile() or len(partes) < 2 or not partes[-1].endswith(".csv"):
                continue
            mm = PATRON.search(partes[-2])
            if not mm:
                continue
            carpeta = destino / partes[-2]
            carpeta.mkdir(parents=True, exist_ok=True)
            with t.extractfile(m) as src, open(carpeta / partes[-1], "wb") as dst:
                shutil.copyfileobj(src, dst)
            clave = mm.group(1)
            sello = mm.group(2) + mm.group(3)
            if clave not in carpetas or sello > carpetas[clave][0]:
                carpetas[clave] = (sello, carpeta)
    return {k: v[1] for k, v in carpetas.items()}


def importar(archivo, fecha):
    import duckdb
    tmp = TEMPORAL / fecha
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        carpetas = extraer(archivo, tmp)
        con = duckdb.connect()
        con.execute("set memory_limit='4GB'")
        partes, filas_tot, errores = [], 0, []
        for cadena, carpeta in sorted(carpetas.items()):
            if not (carpeta / "productos.csv").exists() or (carpeta / "productos.csv").stat().st_size == 0:
                continue
            try:
                res, (filas, _) = resumir_cadena(con, carpeta)
                filas_tot += filas or 0
                if len(res):
                    partes.append(res)
            except Exception as e:
                errores.append(f"cadena {cadena}: {type(e).__name__}")
        con.close()
        if not partes:
            raise RuntimeError("ninguna cadena legible")
        df = pd.concat(partes, ignore_index=True)
        df.insert(0, "fecha", pd.Timestamp(fecha).date())
        df.to_parquet(RESUMEN / f"sepa_{fecha}.parquet", index=False)
        return df["id_comercio"].nunique(), filas_tot, len(df), "; ".join(errores)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2000-01-01")
    ap.add_argument("--hasta", default="2100-01-01")
    ap.add_argument("--max", type=int, default=10_000)
    a = ap.parse_args()
    hechos = {p.stem.replace("sepa_", "") for p in RESUMEN.glob("sepa_*.parquet")}
    pend = [(p, p.name.replace("sepa_", "").replace(".tar.zst", "")) for p in sorted(ORIGEN.glob("sepa_*.tar.zst"))]
    pend = [(p, f) for p, f in pend if a.desde <= f <= a.hasta and f not in hechos][: a.max]
    print(f"Días a importar: {len(pend)} (ya con resumen: {len(hechos)})")
    for i, (p, fecha) in enumerate(pend, 1):
        t0 = time.time()
        try:
            n_cad, precios, filas, err = importar(p, fecha)
            fila = {"dia": fecha, "cadenas": n_cad, "precios": precios, "filas": filas, "error": err}
        except Exception as e:
            fila = {"dia": fecha, "cadenas": 0, "precios": 0, "filas": 0, "error": f"{type(e).__name__}: {str(e)[:150]}"}
        fila["segundos"] = round(time.time() - t0)
        fila["hora"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        pd.DataFrame([fila]).to_csv(REGISTRO, mode="a", header=not REGISTRO.exists(), index=False, encoding="utf-8-sig")
        print(f"  [{i}/{len(pend)}] {fecha}: {fila['precios']:,} precios, {fila['cadenas']} cadenas, {fila['segundos']} s"
              + (f" | {fila['error']}" if fila["error"] else ""), flush=True)


if __name__ == "__main__":
    main()
