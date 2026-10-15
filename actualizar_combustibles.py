# actualizar_combustibles.py
# Precios de combustibles en surtidor (Secretaría de Energía, Res. 314/2016), guardados en E:\combustibles.
#   - todos los días: baja el archivo de precios vigentes (~9 MB, precio actual de cada estación), guarda una copia
#     comprimida en E:\combustibles\vigentes\ y suma a la base los cambios de precio nuevos
#   - una vez por mes (o con --completo, o si no hay base): baja el histórico completo (~780 MB) para recoger cambios
#     informados tarde; el archivo grande se borra después de procesarlo
# Base: E:\combustibles\precios.parquet (una fila por estación x producto x fecha de vigencia; solo horario diurno)
# Las series mensuales las arma guardar_historicas.py desde esta base (sin descargar nada).
# Uso: py -3.13 actualizar_combustibles.py [--completo]
import argparse
import gzip
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import duckdb

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CARPETA = Path(__file__).resolve().parent / "datos"
INFORME = CARPETA / "informe_combustibles.txt"
DESTINO = Path("E:/combustibles")
BASE = DESTINO / "precios.parquet"
MARCA_COMPLETO = DESTINO / "ultimo_completo.txt"
DIAS_ENTRE_COMPLETOS = 30
URL_HISTORICO = ("http://datos.energia.gob.ar/dataset/1c181390-5045-475e-94dc-410429be4b17/resource/"
                 "f8dda0d5-2a9f-4d34-b79b-4e63de3995df/download/precios-historicos.csv")
URL_VIGENTES = ("http://datos.energia.gob.ar/dataset/1c181390-5045-475e-94dc-410429be4b17/resource/"
                "80ac25de-a44a-4445-9215-090cf55cfda5/download/precios-en-surtidor-resolucin-3142016.csv")


def bajar(url, ruta):
    tmp = ruta.with_suffix(".parcial")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=900) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(16 * 1024 * 1024)
            if not b:
                break
            f.write(b)
    tmp.replace(ruta)


def filas(ruta):
    """Cambios de precio de un CSV de la Secretaría (histórico o vigentes): estación, producto, provincia, fecha, precio.
    Fechas en 3 formatos; se descartan las imposibles (el histórico trae años 1 a 9920)."""
    return f"""
        select idempresa || '|' || coalesce(direccion, '') || '|' || coalesce(localidad, '') as est, trim(producto) as producto,
               upper(trim(provincia)) as provincia,
               coalesce(try_strptime(fecha_vigencia, '%d/%m/%Y %H:%M'), try_strptime(fecha_vigencia, '%d/%m/%Y'),
                        try_strptime(fecha_vigencia, '%Y-%m-%d %H:%M:%S')) as f,
               try_cast(replace(precio, ',', '.') as double) as precio
        from read_csv('{ruta.as_posix()}', header=true, all_varchar=true, ignore_errors=true)
        where idtipohorario = '2'"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--completo", action="store_true", help="bajar también el histórico completo (~780 MB)")
    ap.add_argument("--aviso", action="store_true", help="(lo pasa actualizar_todo.py; sin efecto)")
    args = ap.parse_args()
    hoy = datetime.now()
    informe = [f"=== COMBUSTIBLES EN SURTIDOR | {hoy:%Y-%m-%d %H:%M} ==="]
    codigo = 0
    try:
        if not Path("E:/").exists():
            raise RuntimeError("el disco E: no está conectado (se conservan las series ya guardadas)")
        DESTINO.mkdir(exist_ok=True)
        (DESTINO / "vigentes").mkdir(exist_ok=True)
        completo = args.completo or not BASE.exists() or not MARCA_COMPLETO.exists() or \
            (hoy - datetime.fromisoformat(MARCA_COMPLETO.read_text().strip())).days >= DIAS_ENTRE_COMPLETOS
        fuentes = []
        vig = DESTINO / "vigentes" / f"vigentes_{hoy:%Y-%m-%d}.csv"
        bajar(URL_VIGENTES, vig)
        fuentes.append(vig)
        if completo:
            hist = DESTINO / "historico_tmp.csv"
            bajar(URL_HISTORICO, hist)
            fuentes.append(hist)
            informe.append(f"Histórico completo bajado ({hist.stat().st_size / 1e6:.0f} MB)")
        con = duckdb.connect()
        con.execute(f"set memory_limit='4GB'; set max_temp_directory_size='10GiB'; set temp_directory='{(DESTINO / 'duck_tmp').as_posix()}'")
        nuevas = " union all ".join(filas(r) for r in fuentes)
        previa = f"select est, producto, provincia, f, precio from read_parquet('{BASE.as_posix()}')" if BASE.exists() else None
        antes = con.execute(f"select count(*) from read_parquet('{BASE.as_posix()}')").fetchone()[0] if BASE.exists() else 0
        todo = f"{nuevas} union all {previa}" if previa else nuevas
        tmp = BASE.with_suffix(".nuevo.parquet")
        con.execute(f"""copy (select est, producto, any_value(provincia) as provincia, f, max(precio) as precio from ({todo})
                              where f between timestamp '2016-01-01' and current_timestamp and precio > 0
                              group by est, producto, f order by est, producto, f)
                        to '{tmp.as_posix()}' (format parquet)""")
        n, ult, est = con.execute(f"select count(*), max(f), count(distinct est) from read_parquet('{tmp.as_posix()}')").fetchone()
        con.close()
        if n < antes:
            raise RuntimeError(f"la base nueva tiene menos filas ({n:,}) que la anterior ({antes:,}): no se reemplaza")
        tmp.replace(BASE)
        if completo:
            (DESTINO / "historico_tmp.csv").unlink(missing_ok=True)
            MARCA_COMPLETO.write_text(hoy.isoformat(timespec="seconds"))
        with open(vig, "rb") as fi, gzip.open(vig.with_suffix(".csv.gz"), "wb") as fo:   # copia comprimida (~1 MB por día)
            shutil.copyfileobj(fi, fo)
        vig.unlink()
        shutil.rmtree(DESTINO / "duck_tmp", ignore_errors=True)
        informe.append(f"Cambios de precio guardados: {n:,} ({n - antes:+,} nuevos) | {est:,} estaciones | último cambio {ult:%Y-%m-%d}")
        informe.append(f"Base: {BASE}")
    except Exception as e:
        codigo = 2 if not BASE.exists() else 1
        informe.append(f"ERROR: No se pudo actualizar ({type(e).__name__}: {e})")
    texto = "\n".join(informe)
    print(texto)
    CARPETA.mkdir(exist_ok=True)
    INFORME.write_text(texto, encoding="utf-8")
    sys.exit(codigo)


if __name__ == "__main__":
    main()
