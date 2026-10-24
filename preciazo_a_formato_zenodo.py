# preciazo_a_formato_zenodo.py
# Convierte los días de SEPA archivados por preciazo (E:\SEPA\historico_preciazo\sepa_<día>.tar.zst) para publicarlos en
# Zenodo, CONSERVANDO TODA la información del archivo oficial:
#   precios_AAAA-MM-DD.csv.gz   MISMO formato que el registro original de Zenodo 2016-2018 (DOI 10.5281/zenodo.6568295):
#       comercio_id, bandera_id, sucursal_id, producto_sepa_id, precio_lista, precio_unitario_promo1,
#       precio_unitario_promo2, precio_fecha
#       (precio_fecha = número de día de MATLAB, 16-08-2017 = 736923; vacíos "NaN"; enteros sin ".0"; producto_sepa_id sin
#        ceros a la izquierda si es numérico)
#   detalle_AAAA-MM-DD.csv.gz   (el original no lo tenía) mismas claves + precio por unidad de referencia, cantidad y unidad
#       de referencia y las dos leyendas de promoción
#   productos_AAAA-MM.csv.gz, sucursales_AAAA-MM.csv.gz, comercios_AAAA-MM.csv.gz   catálogos del mes (todas las columnas
#       oficiales; la última versión vista en el mes)
# Si un día trae la misma cadena más de una vez, se usa la última descarga. Las cadenas que mandaron un mensaje de error en
# lugar del csv se saltean y se informan en el LEEME.
# Salida: E:\SEPA\para_zenodo\preciazo_AAAA-MM\  = un registro de Zenodo por mes (≈67 archivos y ≈4 GB; límite 100 y 50 GB)
#   + LEEME.txt y manifiesto.csv (archivo, bytes, md5).
# No toca los originales. Se puede cortar y retomar (saltea los días ya convertidos).
# Uso: py -3.13 preciazo_a_formato_zenodo.py [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--max N] [--solo-cierre]
import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

from importar_preciazo import ORIGEN, extraer

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DESTINO = Path("E:/SEPA/para_zenodo")
TEMPORAL = Path("E:/SEPA/tmp_zenodo")
OPC = "delim='|', header=true, all_varchar=true, ignore_errors=true, null_padding=true"
NUM = "try_cast(replace(trim({}), ',', '.') as double)"
# como el registro original: enteros sin ".0" (16, no 16.0) y decimales en su forma más corta (5.6)
TXT = ("case when {x} is null or isnan({x}) then null when {x} = round({x}) and abs({x}) < 1e15 "
       "then cast(cast({x} as bigint) as varchar) else cast({x} as varchar) end")
CLAVES = """try_cast(trim(id_comercio) as integer) as comercio_id, try_cast(trim(id_bandera) as integer) as bandera_id,
            try_cast(trim(id_sucursal) as integer) as sucursal_id,
            case when regexp_full_match(trim(id_producto), '[0-9]+') then coalesce(nullif(ltrim(trim(id_producto), '0'), ''), '0')
                 else trim(id_producto) end as producto_sepa_id"""
SALTEADAS = []          # cadenas con archivo roto (mensaje de error en lugar de datos)


def registro(fecha):
    return DESTINO / f"preciazo_{str(fecha)[:7]}"


def datenum(fecha):
    return pd.Timestamp(fecha).date().toordinal() + 366


def a_parquet(con, consulta, destino):
    con.execute(f"copy ({consulta}) to '{destino.as_posix()}' (format parquet)")


def convertir_dia(archivo, fecha, con):
    reg = registro(fecha)
    reg.mkdir(parents=True, exist_ok=True)
    tmp = TEMPORAL / fecha
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    cat = TEMPORAL / "catalogos" / fecha[:7]
    cat.mkdir(parents=True, exist_ok=True)
    try:
        carpetas = extraer(archivo, tmp)
        partes = []
        for cadena, c in sorted(carpetas.items()):
            p = c / "productos.csv"
            if not p.exists() or p.stat().st_size == 0:
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:   # algunas cadenas mandan un mensaje de error en vez del csv
                cab = fh.readline()
            if "id_producto" not in cab or "productos_precio_lista" not in cab:
                SALTEADAS.append(f"{fecha} cadena {cadena}")
                continue
            partes.append(f"""
              select {CLAVES},
                     {TXT.format(x=NUM.format('productos_precio_lista'))} as precio_lista,
                     {TXT.format(x=NUM.format('productos_precio_unitario_promo1'))} as precio_unitario_promo1,
                     {TXT.format(x=NUM.format('productos_precio_unitario_promo2'))} as precio_unitario_promo2,
                     {datenum(fecha)} as precio_fecha,
                     {TXT.format(x=NUM.format('productos_precio_referencia'))} as precio_referencia,
                     trim(productos_cantidad_referencia) as cantidad_referencia,
                     trim(productos_unidad_medida_referencia) as unidad_medida_referencia,
                     nullif(trim(productos_leyenda_promo1), '') as leyenda_promo1,
                     nullif(trim(productos_leyenda_promo2), '') as leyenda_promo2
              from read_csv('{p.as_posix()}', {OPC})
              where id_producto is not null""")
            # catálogos del día (todas las columnas oficiales menos los precios), para armar los mensuales
            # el catálogo va por cadena y producto (la descripción no cambia de una sucursal a otra)
            a_parquet(con, f"""select distinct try_cast(trim(id_comercio) as integer) as comercio_id,
                                      case when regexp_full_match(trim(id_producto), '[0-9]+')
                                           then coalesce(nullif(ltrim(trim(id_producto), '0'), ''), '0') else trim(id_producto) end as producto_sepa_id,
                                      * exclude (id_comercio, id_bandera, id_sucursal, id_producto,
                                      productos_precio_lista, productos_precio_referencia, productos_precio_unitario_promo1,
                                      productos_leyenda_promo1, productos_precio_unitario_promo2, productos_leyenda_promo2),
                                      '{fecha}' as visto
                               from read_csv('{p.as_posix()}', {OPC}) where id_producto is not null""",
                      cat / f"productos_{fecha}_{cadena}.parquet")
            for tipo in ("sucursales", "comercio"):
                f = c / f"{tipo}.csv"
                if f.exists() and f.stat().st_size > 0:
                    try:
                        # sin la línea final de "última actualización" de los archivos oficiales (fila sin id)
                        a_parquet(con, f"select *, '{fecha}' as visto from read_csv('{f.as_posix()}', {OPC}) "
                                       f"where regexp_full_match(trim(id_comercio), '[0-9]+')",
                                  cat / f"{tipo}_{fecha}_{cadena}.parquet")
                    except Exception:
                        pass                                   # catálogo roto: no impide guardar los precios
        if not partes:
            raise RuntimeError("ninguna cadena legible")
        con.execute("create or replace temp table dia as " + " union all ".join(partes))
        orden = "order by comercio_id, bandera_id, sucursal_id, producto_sepa_id"
        for nombre, cols, nulo in (
                ("precios", "comercio_id, bandera_id, sucursal_id, producto_sepa_id, precio_lista, precio_unitario_promo1, "
                            "precio_unitario_promo2, precio_fecha", "NaN"),
                ("detalle", "comercio_id, bandera_id, sucursal_id, producto_sepa_id, precio_referencia, cantidad_referencia, "
                            "unidad_medida_referencia, leyenda_promo1, leyenda_promo2", "")):
            final = reg / f"{nombre}_{fecha}.csv.gz"
            parcial = final.with_suffix(".tmp")
            con.execute(f"copy (select {cols} from dia {orden}) to '{parcial.as_posix()}' "
                        f"(header, delimiter ',', nullstr '{nulo}', compression gzip)")
            parcial.replace(final)
        n = con.execute("select count(*) from dia").fetchone()[0]
        con.execute("drop table dia")
        peso = sum((reg / f"{x}_{fecha}.csv.gz").stat().st_size for x in ("precios", "detalle"))
        return len(carpetas), n, peso
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def catalogos_mensuales(con):
    """Une los catálogos diarios de cada mes (la última versión vista) en productos_, sucursales_ y comercios_AAAA-MM.csv.gz."""
    base = TEMPORAL / "catalogos"
    if not base.exists():
        return
    for mes in sorted(p.name for p in base.iterdir() if p.is_dir()):
        reg = registro(mes)
        reg.mkdir(parents=True, exist_ok=True)
        for tipo, salida, clave in (("productos", "productos", "comercio_id, producto_sepa_id"),
                                    ("sucursales", "sucursales", "id_comercio, id_bandera, id_sucursal"),
                                    ("comercio", "comercios", "id_comercio, id_bandera")):
            arch = list((base / mes).glob(f"{tipo}_*.parquet"))
            if not arch:
                continue
            lista = ", ".join(f"'{a.as_posix()}'" for a in arch)
            try:
                con.execute(f"""copy (select * exclude (rn) from (
                                        select *, row_number() over (partition by {clave} order by visto desc) as rn
                                        from read_parquet([{lista}], union_by_name=true)) where rn = 1 order by {clave})
                                to '{(reg / f"{salida}_{mes}.csv.gz").as_posix()}' (header, delimiter ',', compression gzip)""")
            except Exception as e:
                print(f"  catálogo {salida} {mes}: ERROR {type(e).__name__}: {str(e)[:120]}", flush=True)


LEEME = """SEPA - Precios de supermercados (Argentina), {periodo}
Formato compatible con "Precios Claros (2016-05 - 2018-03)", Zenodo DOI 10.5281/zenodo.6568295, más archivos
adicionales con toda la información del archivo oficial que aquel registro no incluía.

Origen de los datos: Sistema Electrónico de Publicidad de Precios Argentinos (SEPA), Secretaría de Comercio,
Ministerio de Economía de la Nación (Resolución 12/2016). Portal oficial: https://datos.produccion.gob.ar/dataset/sepa-precios
Licencia de origen: Creative Commons Atribución 4.0 (CC BY 4.0).
Copia de los archivos diarios oficiales: proyecto abierto preciazo (https://github.com/catdevnull/preciazo), que archiva el
dataset oficial varias veces por día. Conversión a este formato y publicación: Datos Con Método (www.datosconmetodo.com),
Marcos Cavanagh. No se modificó ningún precio ni texto: solo se seleccionaron y renombraron columnas y se cambió el formato.

Archivos
  precios_AAAA-MM-DD.csv.gz   IGUAL al registro original: una fila por cadena x bandera x sucursal x producto, ese día.
    comercio_id, bandera_id, sucursal_id  identificadores de cadena, bandera y sucursal (los del SEPA)
    producto_sepa_id   código del producto (EAN sin ceros a la izquierda; si no es EAN, código interno de la cadena)
    precio_lista       precio de lista ($)
    precio_unitario_promo1, precio_unitario_promo2   precio unitario con promoción ($), si hay
    precio_fecha       día, como número de día de MATLAB (datenum): 736923 = 16-08-2017 (igual que el registro original)
    Vacíos: NaN.
  detalle_AAAA-MM-DD.csv.gz   (adicional) mismas claves que precios_ + precio_referencia (precio por unidad de referencia,
    p. ej. por kg o litro), cantidad_referencia, unidad_medida_referencia, leyenda_promo1 y leyenda_promo2 (texto de cada
    promoción tal como lo informa la cadena). Vacíos: celda vacía.
  productos_AAAA-MM.csv.gz    (adicional) catálogo de productos del mes, una fila por cadena (comercio_id) y producto:
    todas las columnas oficiales del archivo de productos salvo los precios (descripción, marca, EAN, presentación, etc.),
    la última versión vista en el mes.
  sucursales_AAAA-MM.csv.gz   (adicional) todas las columnas oficiales del archivo de sucursales (dirección, provincia,
    localidad, coordenadas, horarios, etc.), la última versión vista en el mes.
  comercios_AAAA-MM.csv.gz    (adicional) todas las columnas oficiales del archivo de comercios (razón social, CUIT,
    nombre de cada bandera, etc.), la última versión vista en el mes.
  manifiesto.csv              tamaño y md5 de cada archivo.

Si una cadena se descargó más de una vez en el día, se usa la última descarga. Días sin archivo: no hubo copia ese día.
Días incluidos en este registro: {dias} ({desde} a {hasta}). Días faltantes dentro del mes: {faltan}.
Citar: Secretaría de Comercio (SEPA), vía preciazo; formato y publicación: Datos Con Método.
"""


def cerrar_registros():
    """Escribe LEEME.txt y manifiesto.csv en cada carpeta de registro."""
    sal = DESTINO / "cadenas_salteadas.txt"
    todas_rotas = sal.read_text(encoding="utf-8").splitlines() if sal.exists() else []
    for reg in sorted(p for p in DESTINO.glob("preciazo_*") if p.is_dir()):
        dias = sorted(p.name[8:18] for p in reg.glob("precios_*.csv.gz"))
        if not dias:
            continue
        mes = pd.Period(dias[0][:7], "M")
        todos = [d.strftime("%Y-%m-%d") for d in pd.date_range(mes.start_time, mes.end_time.normalize())]
        faltan = [d for d in todos if d not in set(dias)]
        rotas = sorted({x for x in todas_rotas if x[:10] in set(dias)})
        texto = LEEME.format(periodo=f"{['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'][mes.month - 1]} de {mes.year}",
                             dias=len(dias), desde=dias[0], hasta=dias[-1], faltan=", ".join(faltan) if faltan else "ninguno")
        texto += ("Cadenas que ese día mandaron un archivo de error en lugar de sus precios (no incluidas): "
                  + (", ".join(rotas) if rotas else "ninguna") + ".\n")
        (reg / "LEEME.txt").write_text(texto, encoding="utf-8")
        filas = []
        for f in sorted(reg.iterdir()):
            if f.name == "manifiesto.csv" or not f.is_file() or f.suffix == ".tmp":
                continue
            h = hashlib.md5()
            with open(f, "rb") as fh:
                for bloque in iter(lambda: fh.read(1 << 20), b""):
                    h.update(bloque)
            filas.append({"archivo": f.name, "bytes": f.stat().st_size, "md5": h.hexdigest()})
        pd.DataFrame(filas).to_csv(reg / "manifiesto.csv", index=False)
        print(f"  {reg.name}: {len(filas) + 1} archivos, {sum(x['bytes'] for x in filas) / 1e9:.1f} GB, {len(dias)} días", flush=True)


def main():
    import duckdb
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2000-01-01")
    ap.add_argument("--hasta", default="2100-01-01")
    ap.add_argument("--max", type=int, default=10_000)
    ap.add_argument("--solo-cierre", action="store_true", help="solo catálogos mensuales, LEEME y manifiesto")
    a = ap.parse_args()
    DESTINO.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("set memory_limit='4GB'")
    con.execute(f"set temp_directory='{(TEMPORAL / 'duckdb').as_posix()}'")
    con.execute("set max_temp_directory_size='20GB'")
    if not a.solo_cierre:
        dias = [(p, p.name.replace("sepa_", "").replace(".tar.zst", "")) for p in sorted(ORIGEN.glob("sepa_*.tar.zst"))]
        hechos = {p.name[8:18] for p in DESTINO.rglob("detalle_*.csv.gz")}      # convertidos con el formato completo
        pend = [(p, f) for p, f in dias if a.desde <= f <= a.hasta and f not in hechos][: a.max]
        print(f"Días a convertir: {len(pend)} (ya convertidos: {len(hechos)})", flush=True)
        for i, (p, f) in enumerate(pend, 1):
            t0 = time.time()
            try:
                cad, n, peso = convertir_dia(p, f, con)
                print(f"  [{i}/{len(pend)}] {f}: {n:,} filas, {cad} cadenas, {peso / 1e6:.0f} MB, {time.time() - t0:.0f} s", flush=True)
            except Exception as e:
                print(f"  [{i}/{len(pend)}] {f}: ERROR {type(e).__name__}: {str(e)[:150]}", flush=True)
            if SALTEADAS:
                with open(DESTINO / "cadenas_salteadas.txt", "a", encoding="utf-8") as fh:
                    fh.write("\n".join(SALTEADAS) + "\n")
                SALTEADAS.clear()
    catalogos_mensuales(con)
    cerrar_registros()


if __name__ == "__main__":
    main()
