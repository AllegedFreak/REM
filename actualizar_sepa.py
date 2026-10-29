# actualizar_sepa.py
# Precios diarios de supermercados (Precios Claros - Base SEPA, Secretaría de Comercio, datos abiertos oficiales):
#   https://datos.produccion.gob.ar/dataset/sepa-precios
# El portal guarda SOLO los últimos 7 días (un ZIP por día de la semana que se pisa cada semana): la historia la
# armamos nosotros bajando todos los días.
#   1) consulta el catálogo del portal y baja los días que todavía no tenemos (o que cambiaron)
#   2) guarda el ZIP original en E:\SEPA\crudos\AAAA\sepa_AAAA-MM-DD.zip (disco de 2 TB)
#   3) resume con DuckDB: precio mediano por producto x cadena x bandera x región del IPC (lista, por unidad, promociones)
#      -> E:\SEPA\resumen\sepa_AAAA-MM-DD.parquet
#   4) controla y avisa: S01 archivo de cadena roto | S02 día que se perdió (ya no está en el portal) |
#      S03 cadena que dejó de informar | S04 muchos precios absurdos | S05 disco E: no disponible |
#      S06 el portal republicó un día con contenido distinto | S07 cadena regular que lleva 14+ días sin datos |
#      S08 falta una cadena que es 25%+ de la canasta en alguna región (hoy Carrefour/INC, ~50% en NOA, NEA y Cuyo)
#   S03 no avisa si la cadena suele faltar ese día de la semana (28-oct-2026: La Agrícola Regional, código 20, casi
#   nunca informa domingos ni lunes). S07 es alerta entre 14 y 90 días sin datos (un solo cartel por cadena) y después
#   queda como línea informativa (28-oct-2026: Libertad, código 16, sin datos desde el 2-sep-2026).
# Región: provincia -> región del IPC. GBA = CABA + sucursales de la provincia de Buenos Aires a menos de 40 km del
# centro porteño (aproximación de los 24 partidos).
# Uso:
#   py -3.13 actualizar_sepa.py              -> baja lo nuevo
#   pythonw.exe actualizar_sepa.py --aviso   -> para el Programador de tareas
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = error
import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATASET = "https://datos.produccion.gob.ar/api/3/action/package_show?id=sepa-precios"
DISCO = Path("E:/SEPA")
CRUDOS = DISCO / "crudos"
RESUMEN = DISCO / "resumen"
TEMPORAL = DISCO / "tmp"
REGISTRO = DISCO / "registro.csv"
BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
INFORME = CARPETA / "informe_sepa.txt"
ALERTAS_CSV = CARPETA / "alertas_sepa.csv"
HISTORIAL = CARPETA / "historial_sepa.csv"
PRECIO_MAX = 1e8
RADIO_GBA_KM = 40
CENTRO = (-34.6037, -58.3816)   # Obelisco
REGIONES = {   # código ISO de provincia -> región del IPC (AR-B se separa entre GBA y Pampeana por distancia)
    "AR-C": "GBA", "AR-B": "Pampeana", "AR-X": "Pampeana", "AR-S": "Pampeana", "AR-E": "Pampeana", "AR-L": "Pampeana",
    "AR-W": "Noreste", "AR-H": "Noreste", "AR-P": "Noreste", "AR-N": "Noreste",
    "AR-Y": "Noroeste", "AR-A": "Noroeste", "AR-T": "Noroeste", "AR-G": "Noroeste", "AR-K": "Noroeste", "AR-F": "Noroeste",
    "AR-M": "Cuyo", "AR-J": "Cuyo", "AR-D": "Cuyo",
    "AR-Q": "Patagonia", "AR-R": "Patagonia", "AR-U": "Patagonia", "AR-Z": "Patagonia", "AR-V": "Patagonia",
}
CONTROLES = {
    "S01": ("archivo_cadena_roto", "El archivo de una cadena vino roto o ilegible: esa cadena falta ese día"),
    "S02": ("dia_perdido", "Un día ya no está en el portal y no lo habíamos bajado (se pierde)"),
    "S03": ("cadena_ausente", "Una cadena que informaba con regularidad (5 de los últimos 7 días) no aparece en el día"),
    "S04": ("precios_absurdos", "Más de 5% de los precios de una cadena son cero, negativos o gigantes"),
    "S05": ("disco_no_disponible", "El disco E: no está conectado: no se pudo guardar nada"),
    "S06": ("dia_republicado", "El portal republicó un día con contenido distinto (se guarda como versión nueva)"),
    "S07": ("cadena_sin_datos", "Una cadena que informaba seguido lleva entre 14 y 90 días sin datos"),
    "S08": ("cadena_grande_ausente", "Falta una cadena que es 25% o más de la canasta del índice en alguna región"),
}
PESO_GRANDE = 25   # S08: % de los ítems de la canasta en una región (28-oct-2026: Carrefour es ~50% en NOA, NEA y Cuyo)


def cadenas_grandes():
    """{cadena: 'Noroeste 50%, ...'} para las que pesan PESO_GRANDE% o más de los ítems de la canasta en alguna región
    (último items_<día>.parquet de indice_sepa.py). Vacío si todavía no hay canasta."""
    ps = sorted((DISCO / "canasta").glob("items_*.parquet"))
    if not ps:
        return {}
    x = pd.read_parquet(ps[-1], columns=["region", "id_comercio"])
    t = pd.crosstab(x["region"], x["id_comercio"].astype(str), normalize="index") * 100
    return {c: ", ".join(f"{r} {v:.0f}%" for r, v in t[c].sort_values(ascending=False).items() if v >= PESO_GRANDE)
            for c in t.columns if t[c].max() >= PESO_GRANDE}
SIN_DATOS_DIAS = (14, 90)   # S07: alerta desde 14 días sin datos; desde 90 solo se informa


def presencias(hasta=None, n=150):
    """{fecha: códigos de cadena con datos} de los últimos n resúmenes (anteriores a 'hasta' si se indica)."""
    import pyarrow.parquet as pq
    ps = sorted(RESUMEN.glob("sepa_*.parquet"))
    if hasta:
        ps = [p for p in ps if p.stem < f"sepa_{hasta}"]
    return {p.stem[5:15]: set(map(str, pq.read_table(p, columns=["id_comercio"]).column(0).unique().to_pylist())) for p in ps[-n:]}


def falta_habitual(cadena, fecha, pres, semanas=8):
    """True si la cadena informó menos de la mitad de las veces ese mismo día de la semana en las últimas 'semanas'."""
    dia = pd.Timestamp(fecha).dayofweek
    mismos = [f for f in sorted(pres) if pd.Timestamp(f).dayofweek == dia][-semanas:]
    return len(mismos) >= 4 and sum(cadena in pres[f] for f in mismos) < len(mismos) / 2


def cadenas_sin_datos(pres, nombres):
    """Cadenas que informaban seguido (la mitad o más de los 60 días antes de su último dato) y llevan 14+ días sin datos."""
    dias = sorted(pres)
    if not dias:
        return []
    hoy = pd.Timestamp(dias[-1])
    out = []
    for c in set().union(*pres.values()):
        ult = max(f for f in dias if c in pres[f])
        sin = (hoy - pd.Timestamp(ult)).days
        if sin < SIN_DATOS_DIAS[0]:
            continue
        ventana = [f for f in dias if pd.Timestamp(ult) - pd.Timedelta(days=60) <= pd.Timestamp(f) <= pd.Timestamp(ult)]
        if len(ventana) >= 10 and sum(c in pres[f] for f in ventana) >= len(ventana) / 2:
            out.append((c, nombres.get(c, "?"), ult, sin))
    return sorted(out, key=lambda x: x[3])


class ErrorSepa(Exception):
    pass


def bajar(url, timeout=900):
    for intento in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if intento == 2:
                raise
            time.sleep(10)


NOMBRES_PROVINCIA = {   # algunas cadenas escriben el nombre en lugar del código ISO
    "buenos aires": "AR-B", "caba": "AR-C", "ciudad autonoma de buenos aires": "AR-C", "capital federal": "AR-C",
    "cordoba": "AR-X", "santa fe": "AR-S", "entre rios": "AR-E", "la pampa": "AR-L", "corrientes": "AR-W", "chaco": "AR-H",
    "formosa": "AR-P", "misiones": "AR-N", "jujuy": "AR-Y", "salta": "AR-A", "tucuman": "AR-T", "santiago del estero": "AR-G",
    "catamarca": "AR-K", "la rioja": "AR-F", "mendoza": "AR-M", "san juan": "AR-J", "san luis": "AR-D", "neuquen": "AR-Q",
    "rio negro": "AR-R", "chubut": "AR-U", "santa cruz": "AR-Z", "tierra del fuego": "AR-V",
}


def region_sql():
    """Región del IPC a partir de la provincia (código ISO o nombre) y, para Buenos Aires o provincia vacía, la distancia
    al centro porteño. Lo que no se puede asignar queda como 'sin región' (no se descarta)."""
    nombres = " ".join(f"when '{k}' then '{v}'" for k, v in NOMBRES_PROVINCIA.items())
    prov = (f"(case when regexp_full_match(upper(provincia), 'AR-[A-Z]') then upper(provincia) "
            f"else (case lower(strip_accents(provincia)) {nombres} else null end) end)")
    casos = " ".join(f"when '{k}' then '{v}'" for k, v in REGIONES.items() if k != "AR-B")
    lat0, lon0 = CENTRO
    cerca = (f"(lat is not null and 111.32 * sqrt(power(lat - ({lat0}), 2) + power((lon - ({lon0})) * cos(radians({lat0})), 2)) "
             f"< {RADIO_GBA_KM})")
    return (f"case when {prov} = 'AR-B' then (case when {cerca} then 'GBA' else 'Pampeana' end) "
            f"when {prov} is null then (case when {cerca} then 'GBA' else 'sin región' end) "
            f"else (case {prov} {casos} else 'sin región' end) end")


def resumir_cadena(con, carpeta):
    """carpeta: archivos productos.csv / sucursales.csv / comercio.csv de una cadena. Devuelve DataFrame resumen."""
    p, s, c = (carpeta / "productos.csv").as_posix(), (carpeta / "sucursales.csv").as_posix(), (carpeta / "comercio.csv").as_posix()
    opciones = "delim='|', header=true, all_varchar=true, ignore_errors=true, null_padding=true"
    num = lambda col: f"try_cast(replace(trim({col}), ',', '.') as double)"
    sql = f"""
    with p as (select * from read_csv('{p}', {opciones})),
         s as (select id_comercio, id_bandera, id_sucursal, trim(sucursales_provincia) as provincia,
                      {num('sucursales_latitud')} as lat, {num('sucursales_longitud')} as lon
               from read_csv('{s}', {opciones})),
         c as (select distinct id_comercio, id_bandera, comercio_bandera_nombre as bandera, comercio_razon_social as razon_social
               from read_csv('{c}', {opciones})),
         x as (select p.id_comercio, p.id_bandera, p.id_sucursal,
                      -- productos_ean NO es el código: es un indicador (1 = id_producto es un código de barras EAN)
                      case when trim(p.productos_ean) = '1' and regexp_full_match(trim(p.id_producto), '[0-9]{{8,14}}')
                           then ltrim(trim(p.id_producto), '0') else 'c' || p.id_comercio || '-' || trim(p.id_producto) end as producto,
                      case when trim(p.productos_ean) = '1' then ltrim(trim(p.id_producto), '0') end as ean,
                      p.productos_descripcion as descripcion, p.productos_marca as marca,
                      {num('p.productos_cantidad_presentacion')} as cantidad, trim(p.productos_unidad_medida_presentacion) as unidad,
                      trim(p.productos_unidad_medida_referencia) as unidad_ref,
                      {num('p.productos_precio_lista')} as lista, {num('p.productos_precio_referencia')} as por_unidad,
                      {num('p.productos_precio_unitario_promo1')} as promo1, {num('p.productos_precio_unitario_promo2')} as promo2,
                      nullif(trim(p.productos_leyenda_promo1), '') as leyenda1, nullif(trim(p.productos_leyenda_promo2), '') as leyenda2,
                      {region_sql()} as region
               from p left join s using (id_comercio, id_bandera, id_sucursal))
    select x.id_comercio, x.id_bandera, any_value(c.bandera) as bandera, any_value(c.razon_social) as razon_social,
           region, producto, any_value(ean) as ean, any_value(descripcion) as descripcion, any_value(marca) as marca,
           any_value(cantidad) as cantidad, any_value(unidad) as unidad, any_value(unidad_ref) as unidad_ref,
           count(*) as n_sucursales,
           median(lista) as precio_lista, median(por_unidad) as precio_por_unidad,
           median(promo1) as precio_promo1, avg(case when promo1 > 0 then 1.0 else 0 end) as pct_promo1,
           median(promo2) as precio_promo2, avg(case when promo2 > 0 then 1.0 else 0 end) as pct_promo2,
           mode(leyenda1) as leyenda_promo1, mode(leyenda2) as leyenda_promo2
    from x left join c using (id_comercio, id_bandera)
    where lista > 0 and lista < {PRECIO_MAX}
    group by x.id_comercio, x.id_bandera, region, producto
    """
    resumen = con.execute(sql).df()
    calidad = con.execute(f"""
        select count(*) as filas,
               sum(case when {num('productos_precio_lista')} is null or {num('productos_precio_lista')} <= 0
                         or {num('productos_precio_lista')} >= {PRECIO_MAX} then 1 else 0 end) as malas
        from read_csv('{p}', {opciones})""").fetchone()
    return resumen, calidad


_CADENAS_CON_DATOS = None


def cadenas_con_datos():
    """id_comercio de las cadenas que alguna vez mandaron datos (según los resúmenes guardados)."""
    global _CADENAS_CON_DATOS
    if _CADENAS_CON_DATOS is None:
        ids = set()
        for p in sorted(RESUMEN.glob("sepa_*.parquet"))[-14:]:
            ids |= set(pd.read_parquet(p, columns=["id_comercio"])["id_comercio"].astype(str))
        _CADENAS_CON_DATOS = ids
    return _CADENAS_CON_DATOS


def procesar_zip(contenido, fecha, informe, alertas):
    import duckdb
    z = zipfile.ZipFile(io.BytesIO(contenido))
    internos = [i for i in z.infolist() if i.filename.lower().endswith(".zip")]
    partes, cadenas, filas_tot = [], [], 0
    con = duckdb.connect()
    for i in internos:
        nombre = i.filename.split("/")[-1]
        tmp = TEMPORAL / nombre.replace(".zip", "")
        try:
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            zipfile.ZipFile(io.BytesIO(z.read(i))).extractall(tmp)
            res, (filas, malas) = resumir_cadena(con, tmp)
            filas_tot += filas or 0
            if filas and malas / filas > 0.05:
                alertas.append({"codigo": "S04", "fecha": fecha, "detalle": f"{nombre}: {malas / filas:.0%} de precios inválidos"})
            if len(res):
                partes.append(res)
                cadenas.append(str(res["id_comercio"].iloc[0]))
        except Exception as e:
            # solo se avisa si la cadena antes sí mandaba datos (hay cadenas que mandan siempre un archivo vacío)
            m = re.search(r"comercio-sepa-(\d+)", nombre)
            cadena = m.group(1) if m else None
            if cadena is None or cadena in cadenas_con_datos():
                alertas.append({"codigo": "S01", "fecha": fecha, "detalle": f"{nombre}: {type(e).__name__}: {str(e)[:120]}"})
            else:
                informe.append(f"   (cadena {cadena}: archivo vacío o ilegible, como siempre; no se avisa)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    con.close()
    if not partes:
        raise ErrorSepa(f"el día {fecha} no trajo ninguna cadena legible")
    df = pd.concat(partes, ignore_index=True)
    df.insert(0, "fecha", pd.Timestamp(fecha).date())
    return df, sorted(set(cadenas)), filas_tot


def reprocesar():
    """Rehace E:\\SEPA\\resumen\\sepa_<día>.parquet desde los ZIP originales de E:\\SEPA\\crudos (no baja nada)."""
    TEMPORAL.mkdir(parents=True, exist_ok=True)
    zips = sorted(CRUDOS.glob("*/sepa_*.zip"))
    print(f"Reprocesando {len(zips)} días desde {CRUDOS}")
    for z in zips:
        fecha = z.stem.replace("sepa_", "")[:10]
        alertas = []
        df, cadenas, filas = procesar_zip(z.read_bytes(), fecha, [], alertas)
        df.to_parquet(RESUMEN / f"sepa_{fecha}.parquet", index=False)
        print(f"  {fecha}: {filas:,} precios, {len(df):,} filas, {len(cadenas)} cadenas" + (f" | {len(alertas)} alertas" if alertas else ""))


def avisar(codigo, alertas, informe):
    if codigo == 0:
        return
    clave = f"{codigo}|" + ",".join(sorted(f"{a['codigo']}:{a['fecha']}" for a in alertas))
    marca = CARPETA / "ultimo_aviso_sepa.txt"
    if marca.exists() and marca.read_text(encoding="utf-8") == clave:
        return
    marca.write_text(clave, encoding="utf-8")
    titulo = "SEPA: hay alertas para revisar" if codigo == 1 else "SEPA: no se pudo actualizar"
    msg = "\n".join(informe[-6:])[:600]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, (0x30 if codigo == 1 else 0x10) | 0x1000)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Baja y resume los precios diarios de supermercados (SEPA)")
    ap.add_argument("--aviso", action="store_true", help="cartel de Windows solo si hay novedades")
    ap.add_argument("--reprocesar", action="store_true",
                    help="rehace todos los resúmenes desde los ZIP originales guardados (cuando cambia el formato del resumen)")
    args = ap.parse_args()
    if args.reprocesar:
        reprocesar()
        return
    informe = [f"=== ACTUALIZACIÓN SEPA (precios de supermercados) | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    codigo, alertas, nuevos = 0, [], []
    try:
        if not Path("E:/").exists():
            alertas.append({"codigo": "S05", "fecha": "", "detalle": "disco E: no conectado"})
            raise ErrorSepa("el disco E: no está conectado; conectarlo y volver a correr (el portal guarda 7 días)")
        for d in (CRUDOS, RESUMEN, TEMPORAL):
            d.mkdir(parents=True, exist_ok=True)
        registro = pd.read_csv(REGISTRO, dtype=str) if REGISTRO.exists() else pd.DataFrame(
            columns=["recurso", "modificado_portal", "fecha_datos", "archivo", "bytes", "sha256", "bajado"])
        catalogo = json.loads(bajar(DATASET, 120))["result"]["resources"]
        zips = [r for r in catalogo if str(r.get("format", "")).upper() == "ZIP"]
        en_portal = []
        for r in zips:
            modif = r.get("last_modified") or r.get("metadata_modified") or ""
            ya = registro[(registro["recurso"] == r["id"]) & (registro["modificado_portal"] == modif)]
            if len(ya):
                en_portal.append(ya.iloc[0]["fecha_datos"])
                continue
            contenido = bajar(r["url"])
            huella = hashlib.sha256(contenido).hexdigest()
            z = zipfile.ZipFile(io.BytesIO(contenido))
            fecha = next((n.split("/")[0] for n in z.namelist() if n[:4].isdigit() and "/" in n), None)
            if not fecha:
                alertas.append({"codigo": "S01", "fecha": r.get("name"), "detalle": "no se pudo leer la fecha del ZIP"})
                continue
            en_portal.append(fecha)
            previo = registro[registro["fecha_datos"] == fecha]
            if len(previo) and huella in set(previo["sha256"]):
                registro.loc[len(registro)] = [r["id"], modif, fecha, previo.iloc[0]["archivo"], str(len(contenido)), huella,
                                               datetime.now().strftime("%Y-%m-%d %H:%M")]
                continue
            destino = CRUDOS / fecha[:4] / f"sepa_{fecha}.zip"
            if len(previo):
                alertas.append({"codigo": "S06", "fecha": fecha, "detalle": "el portal republicó el día con otro contenido"})
                destino = destino.with_name(f"sepa_{fecha}_v{len(previo) + 1}.zip")
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(contenido)
            df, cadenas, filas = procesar_zip(contenido, fecha, informe, alertas)
            salida = RESUMEN / f"sepa_{fecha}.parquet"
            df.to_parquet(salida, index=False)
            registro.loc[len(registro)] = [r["id"], modif, fecha, str(destino), str(len(contenido)), huella,
                                           datetime.now().strftime("%Y-%m-%d %H:%M")]
            nuevos.append((fecha, len(contenido), filas, len(df), len(cadenas), df["producto"].nunique()))
            # cadenas que informaban antes y hoy no. Solo se avisa (S03) si la cadena informó en al menos 5 de los últimos
            # 7 días: hay cadenas intermitentes (Farmacity informa ~1 de cada 7 días) que darían falsas alarmas
            previos = sorted(RESUMEN.glob("sepa_*.parquet"))
            anteriores = [p for p in previos if p.stem < f"sepa_{fecha}"][-7:]
            if anteriores:
                conteo = pd.Series([c for p in anteriores for c in
                                    set(pd.read_parquet(p, columns=["id_comercio"])["id_comercio"].astype(str))]).value_counts()
                faltan = set(conteo.index) - set(cadenas)
                grandes = cadenas_grandes()
                for c in sorted(faltan & set(grandes)):   # S08 antes que S03 y sin excepción por día de la semana
                    alertas.append({"codigo": "S08", "fecha": fecha, "detalle": f"no informó la cadena {c}, que pesa {grandes[c]} de la canasta"})
                faltan -= set(grandes)
                regulares = sorted(c for c in faltan if conteo[c] >= min(5, len(anteriores)))
                intermitentes = sorted(faltan - set(regulares))
                pres = presencias(hasta=fecha, n=70) if regulares else {}
                habituales = [c for c in regulares if falta_habitual(c, fecha, pres)]
                regulares = [c for c in regulares if c not in habituales]
                if habituales:
                    informe.append(f"   ({fecha}: no informaron {habituales}, que suelen faltar ese día de la semana; no se avisa)")
                if regulares:
                    alertas.append({"codigo": "S03", "fecha": fecha, "detalle": f"no informaron las cadenas {regulares}"})
                if intermitentes:
                    informe.append(f"   ({fecha}: tampoco informaron {intermitentes}, que son intermitentes; no se avisa)")
        registro.to_csv(REGISTRO, index=False)
        # días que se perdieron: huecos entre el primer día bajado y hoy que ya no están en el portal
        bajados = sorted(set(registro["fecha_datos"]))
        if bajados:
            todos = pd.date_range(bajados[0], max(bajados)).strftime("%Y-%m-%d")
            perdidos = [f for f in todos if f not in bajados and f not in en_portal]
            for f in perdidos[-10:]:
                alertas.append({"codigo": "S02", "fecha": f, "detalle": "día que no se bajó a tiempo"})
        # cadenas que dejaron de informar (S07). "fecha" = último día con datos: el cartel sale una sola vez por cadena
        try:
            pres = presencias()
            ult_dia = max(pres)
            nombres = {}
            for f in sorted(pres)[::-1]:
                faltan_nombre = {c for c in pres[f]} - set(nombres)
                if faltan_nombre:
                    x = pd.read_parquet(RESUMEN / f"sepa_{f}.parquet", columns=["id_comercio", "razon_social"])
                    x["id_comercio"] = x["id_comercio"].astype(str)
                    nombres.update(x.drop_duplicates("id_comercio").set_index("id_comercio")["razon_social"].to_dict())
            for c, nom, ult, sin in cadenas_sin_datos(pres, nombres):
                if sin <= SIN_DATOS_DIAS[1]:
                    alertas.append({"codigo": "S07", "fecha": ult, "detalle": f"cadena {c} ({nom}): {sin} días sin datos (hasta el {ult_dia})"})
                else:
                    informe.append(f"   (cadena {c}, {nom}: sin datos desde el {ult}, hace {sin} días; ya no se avisa)")
        except Exception as e:
            informe.append(f"   (no se pudo revisar qué cadenas dejaron de informar: {type(e).__name__}: {e})")
        informe.append("")
        if nuevos:
            informe.append(f"{'día':10} {'MB':>6} {'precios':>12} {'filas resumen':>14} {'cadenas':>8} {'productos':>10}")
            for f, b, fil, res, cad, prod in sorted(nuevos):
                informe.append(f"{f:10} {b / 1e6:6.0f} {fil:12,} {res:14,} {cad:8} {prod:10,}")
        else:
            informe.append("Sin días nuevos: el portal no publicó nada que no tengamos.")
        informe.append(f"Días guardados: {len(bajados)} ({bajados[0] if bajados else '-'} a {bajados[-1] if bajados else '-'}). "
                       f"Crudos en {CRUDOS}, resúmenes en {RESUMEN}.")
        informe.append("")
        informe.append("Controles:")
        for cod, (nom, desc) in CONTROLES.items():
            informe.append(f"  {cod} {nom}: {sum(a['codigo'] == cod for a in alertas)} casos  ({desc})")
        informe.append("")
        informe.append("=" * 60)
        if alertas:
            codigo = 1
            pd.DataFrame(alertas).to_csv(ALERTAS_CSV, index=False, encoding="utf-8-sig")
            informe.append(f"ALERTAS: {len(alertas)} para revisar (detalle en datos\\alertas_sepa.csv)")
            for a in alertas[:12]:
                informe.append(f"  - {a['codigo']} {CONTROLES[a['codigo']][0]} | {a['fecha']} | {a['detalle']}")
        else:
            informe.append("ALERTAS: ninguna. Todo en orden.")
            if ALERTAS_CSV.exists():
                ALERTAS_CSV.unlink()
    except ErrorSepa as e:
        informe += ["", "=" * 60, f"ERROR: {e}"]
        codigo = 2
    except Exception as e:
        informe += ["", "=" * 60, f"FALLA INESPERADA: {type(e).__name__}: {e}"]
        codigo = 2
    pd.DataFrame([{"fecha_corrida": datetime.now().strftime("%Y-%m-%d %H:%M"), "dias_nuevos": len(nuevos),
                   "alertas": len(alertas), "codigo": codigo}]).to_csv(HISTORIAL, mode="a", header=not HISTORIAL.exists(),
                                                                      index=False, encoding="utf-8-sig")
    texto = "\n".join(informe)
    print(texto)
    CARPETA.mkdir(exist_ok=True)
    INFORME.write_text(texto, encoding="utf-8")
    if args.aviso:
        avisar(codigo, alertas, informe)
    sys.exit(codigo)


if __name__ == "__main__":
    main()
