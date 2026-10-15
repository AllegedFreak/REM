# actualizar_mercado_central.py
# Precios mayoristas DIARIOS de frutas y hortalizas (y pescados 2016-2020) del Mercado Central de Buenos Aires (MCBA,
# Departamento de Estadísticas y Precios): https://mercadocentral.gob.ar/información/precios-mayoristas
# La página oficial solo muestra el año en curso. Los archivos de años anteriores se toman del servidor oficial si
# siguen ahí y, si no, de la copia guardada por el archivo de Internet (Wayback Machine, web.archive.org). La lista de
# archivos históricos salió de las 70 versiones archivadas de la página (datos\mercado_central_archivos_wayback.json).
# Cada ZIP (a veces con ZIPs adentro) trae una planilla por día hábil (RHddmmaa.XLS hortalizas, RFddmmaa.XLS frutas):
# especie, variedad, procedencia, envase, kg, calidad, tamaño, grado y precios máximo/modal/mínimo por bulto y por kg.
# Se guardan SOLO las planillas diarias (E:\mercado_central\diarios\...). Algunos ZIPs oficiales traen además archivos
# ajenos (páginas guardadas, capturas): se descartan sin abrirlos.
# Salida: datos\mercado_central.parquet (una fila por producto y día) | registro E:\mercado_central\registro.csv
# Uso: py -3.13 actualizar_mercado_central.py   (la primera vez baja ~200 archivos; después, solo el año en curso)
#      --reintentar: vuelve a buscar los archivos que la vez anterior no estaban disponibles
# Cobertura (15-oct-2026): 2017 a 2021 completos, parte de 2022 y 2026. Jul-2022 a dic-2025 NO están en el servidor
# oficial ni en el archivo de Internet (pedirlos al Mercado Central).
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
DISCO = Path("E:/mercado_central")
DIARIOS = DISCO / "diarios"
REGISTRO = DISCO / "registro.csv"
SALIDA = CARPETA / "mercado_central.parquet"
INFORME = CARPETA / "informe_mercado_central.txt"
PAGINA = "https://mercadocentral.gob.ar/informaci%C3%B3n/precios-mayoristas"
DIR_ARCH = "https://mercadocentral.gob.ar/sites/default/files/precios_mayoristas/"
HISTORICOS = CARPETA / "mercado_central_archivos_wayback.json"
UA = {"User-Agent": "Mozilla/5.0"}
COL_FECHA = re.compile(r"^M[AOI](\d{6})$")


def bajar(url, timeout=180):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == 2:
                raise
        except Exception:
            if i == 2:
                raise
        time.sleep(5 * (i + 1))


def wayback(nombre):
    """Última copia completa (HTTP 200) del archivo en el archivo de Internet."""
    url = DIR_ARCH + nombre
    # el nombre ya viene codificado (%20): se decodifica para la consulta, que vuelve a codificar
    cdx = bajar("http://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(
        {"url": urllib.parse.unquote(url).replace("https://", ""), "output": "json", "fl": "timestamp", "filter": "statuscode:200"}))
    filas = json.loads(cdx) if cdx else []
    if len(filas) < 2:
        return None, None
    ts = filas[-1][0]
    return bajar(f"http://web.archive.org/web/{ts}id_/{url}", timeout=300), f"web.archive.org/web/{ts}/{url}"


def tipo_de(nombre):
    n = nombre.upper()
    return "H" if ("HORT" in n or "HOTAL" in n) else "F" if "FRUT" in n else "P" if "PESC" in n else "otros"


def planillas(contenido, nombre, salida):
    """Recorre el ZIP (y los ZIP de adentro) y devuelve [(tipo, nombre, bytes)] de las planillas diarias .xls."""
    try:
        z = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile:
        return salida
    for i in z.infolist():
        base = i.filename.rsplit("/", 1)[-1]
        if base.lower().endswith(".zip"):
            planillas(z.read(i), base, salida)
        elif base.lower().endswith(".xls"):                     # todo lo demás (páginas, imágenes, accesos) se ignora
            t = tipo_de(base) if tipo_de(base) != "otros" else ({"RH": "H", "RF": "F"}.get(base[:2].upper()) or tipo_de(nombre))
            salida.append((t, base, z.read(i)))
    return salida


def leer_planilla(b):
    """DataFrame de una planilla diaria (o None si no tiene el formato esperado)."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            x = pd.read_excel(io.BytesIO(b), engine="xlrd")
        except Exception:
            return None
    x.columns = [str(c).strip().upper() for c in x.columns]
    fechas = {COL_FECHA.match(c).group(1) for c in x.columns if COL_FECHA.match(c)}
    if "ESP" not in x.columns or len(fechas) != 1:
        return None
    f = fechas.pop()
    fecha = pd.to_datetime(f, format="%d%m%y", errors="coerce")
    if pd.isna(fecha):
        return None
    ren = {f"MA{f}": "max_bulto", f"MO{f}": "modal_bulto", f"MI{f}": "min_bulto", "MAPK": "max_kg", "MOPK": "modal_kg",
           "MIPK": "min_kg", "ESP": "especie", "VAR": "variedad", "PROC": "procedencia", "ENV": "envase", "KG": "kg",
           "CAL": "calidad", "TAM": "tamano", "GRADO": "grado"}
    x = x.rename(columns=ren)
    x = x[[c for c in ren.values() if c in x.columns]].copy()
    x.insert(0, "fecha", fecha.date())
    for c in ("especie", "variedad", "procedencia", "envase", "calidad", "tamano", "grado"):
        if c in x.columns:
            x[c] = x[c].astype("string").str.strip()
    x["promedio_especie"] = x.get("variedad", pd.Series(dtype="string")).fillna("").str.upper().str.startswith("PROM")
    return x


def archivos_actuales():
    html = bajar(PAGINA)
    if not html:
        return []
    return sorted(set(re.findall(r'precios_mayoristas/([^"\'<>\s]+\.zip)', html.decode("utf-8", "replace"))))


def main():
    DIARIOS.mkdir(parents=True, exist_ok=True)
    L = [f"=== MERCADO CENTRAL (precios mayoristas diarios) | {datetime.now():%Y-%m-%d %H:%M} ==="]
    reg = pd.read_csv(REGISTRO, encoding="utf-8-sig") if REGISTRO.exists() else pd.DataFrame(columns=["archivo", "origen", "sha1", "planillas", "fecha"])
    # ya bajados, o buscados y no disponibles (estos se reintentan solo con --reintentar)
    hechos = set(reg.loc[(reg["planillas"] > 0) | ((reg["origen"] == "no disponible") & ("--reintentar" not in sys.argv)), "archivo"]) \
        if len(reg) else set()
    actuales = archivos_actuales()
    historicos = sorted(json.loads(HISTORICOS.read_text())) if HISTORICOS.exists() else []
    mes = datetime.now()
    # el año en curso se vuelve a bajar siempre (los ZIP del mes en curso se completan día a día)
    pendientes = [a for a in dict.fromkeys(historicos + actuales) if a not in hechos or a in actuales]
    alertas, nuevas = [], 0
    for a in pendientes:
        contenido, origen = bajar(DIR_ARCH + a), "oficial"
        if contenido is None:
            if a in actuales:
                alertas.append(f"M01 archivo listado en la página oficial pero no se pudo bajar: {a}")
                continue
            contenido, origen = wayback(a)
            time.sleep(2)
        if contenido is None:
            reg.loc[len(reg)] = [a, "no disponible", "", 0, f"{mes:%Y-%m-%d %H:%M}"]
            continue
        sha = hashlib.sha1(contenido).hexdigest()
        if len(reg) and ((reg["archivo"] == a) & (reg["sha1"] == sha)).any():
            continue                                             # mismo contenido que la vez anterior
        n = 0
        for t, nombre, b in planillas(contenido, a, []):
            m = re.search(r"(\d{2})(\d{2})(\d{2})", nombre)
            anio = f"20{m.group(3)}" if m else "sin_fecha"
            destino = DIARIOS / t / anio / nombre.upper()
            destino.parent.mkdir(parents=True, exist_ok=True)
            if not destino.exists() or destino.read_bytes() != b:
                destino.write_bytes(b)
                nuevas += 1
            n += 1
        reg.loc[len(reg)] = [a, origen, sha, n, f"{mes:%Y-%m-%d %H:%M}"]
        print(f"  {a}: {n} planillas ({origen})", flush=True)
    reg.to_csv(REGISTRO, index=False, encoding="utf-8-sig")

    # base: todas las planillas guardadas (si un día aparece dos veces, se usa la última versión guardada)
    filas = []
    for p in sorted(DIARIOS.rglob("*.XLS")):
        x = leer_planilla(p.read_bytes())
        if x is None:
            continue
        x.insert(1, "tipo", p.parent.parent.name)
        x["archivo"] = p.name
        filas.append(x)
    df = pd.concat(filas, ignore_index=True)
    df = df.sort_values(["tipo", "fecha"])
    ultimos = df.groupby(["tipo", "fecha"])["archivo"].transform("last")
    df = df[df["archivo"] == ultimos]
    df.to_parquet(SALIDA, index=False)
    dias = df.groupby("tipo")["fecha"].agg(["min", "max", "nunique"])
    L.append(f"Archivos revisados: {len(pendientes)}; planillas nuevas o cambiadas: {nuevas}")
    for t, r in dias.iterrows():
        L.append(f"  {dict(H='Hortalizas', F='Frutas', P='Pescados').get(t, t)}: {r['nunique']} días, {r['min']} a {r['max']}")
    meses = pd.to_datetime(df["fecha"]).dt.to_period("M")
    todos = pd.period_range(meses.min(), meses.max(), freq="M")
    faltan = [str(m) for m in todos if m not in set(meses[df["tipo"] == "H"])]
    L.append(f"Meses sin datos de hortalizas: {', '.join(faltan) if faltan else 'ninguno'}")
    L.append(f"Base: datos\\{SALIDA.name} ({len(df):,} filas)")
    L += ["ALERTAS: " + "; ".join(alertas)] if alertas else ["ALERTAS: ninguna. Todo en orden."]
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")
    sys.exit(1 if alertas else 0)


if __name__ == "__main__":
    main()
