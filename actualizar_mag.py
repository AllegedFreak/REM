# actualizar_mag.py
# Precios de hacienda del Mercado Agroganadero de Cañuelas (MAG, ex Liniers): https://www.mercadoagroganadero.com.ar
# Consulta pública "Precios por categoría" (formulario con fecha inicial y final, sin usuario). Por cada mes desde
# 2017 se piden dos períodos: el mes completo y del 1 al 25 (lo que se conoce al cerrar el REM). Cada respuesta se
# guarda tal cual en datos\copias\mag\ y se resume en datos\mag_precios.parquet: período, categoría y subcategoría,
# precio mínimo, máximo, promedio y mediana ($/kg vivo), cabezas, importe y kilos.
# El MAG es una empresa privada (el propio mercado); el Ministerio de Agricultura publica resúmenes semanales en PDF y el
# INDEC el IPIM de ganado (mensual, con un mes de rezago). Ver datos\fuentes.csv.
# Solo pide lo que falta (y vuelve a pedir el mes en curso y el anterior). Un pedido cada 3 segundos.
# Uso: py -3.13 actualizar_mag.py [--desde AAAA-MM]
import argparse
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from io import StringIO
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
CRUDOS = CARPETA / "copias" / "mag"
SALIDA = CARPETA / "mag_precios.parquet"
INFORME = CARPETA / "informe_mag.txt"
URL = "https://www.mercadoagroganadero.com.ar/dll/hacienda1.dll/haciinfo000002"
H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"}
CATEGORIAS = ("NOVILLOS", "NOVILLITOS", "VAQUILLONAS", "VACAS", "TOROS", "MEJ")


def pedir(ini, fin):
    datos = urllib.parse.urlencode({"ID": "", "CP": "", "FLASH": "", "USUARIO": "SIN IDENTIFICAR", "OPCIONMENU": "",
                                    "OPCIONSUBMENU": "", "txtFechaIni": ini.strftime("%d/%m/%Y"),
                                    "txtFechaFin": fin.strftime("%d/%m/%Y")}).encode()
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, data=datos, headers=H), timeout=120) as r:
                return r.read().decode("latin-1", "replace")
        except Exception:
            if i == 2:
                raise
            time.sleep(20)


def num(x):
    s = str(x).replace("$", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


P = r"(\d[\d\.]*,\d{3})"            # precio $/kg: 307,110 o 4.512,500
I = r"(\d[\d\.]*)"                   # entero con punto de miles: 3.167
FILA = re.compile(rf"([A-Za-zÁÉÍÓÚÑáéíóúñ][^$]*?)\s+{P}\s+{P}\s+{P}\s+{P}\s+{I}\s+\$(\d[\d\.]*,\d{{2}})\s+{I}\s+{I}")


def parsear(html):
    """Filas de la tabla de precios por categoría (se lee el texto: 'NOVILLOS Mest.EyB 431/460 240,000 335,000 ...')."""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&nbsp;", " ")
    i = t.find("Kgs Kgs")
    t = t[i + 7:] if i >= 0 else t
    t = t.split("NOTA:")[0]
    p_ = lambda s: float(s.replace(".", "").replace(",", "."))
    i_ = lambda s: float(s.replace(".", ""))
    filas, cat = [], None
    for m in FILA.finditer(t):
        nombre = m.group(1).strip()
        primera = nombre.split(" ")[0].upper()
        if primera in CATEGORIAS:
            cat, sub = primera, nombre[len(primera):].strip()
        else:
            sub = nombre
        if cat is None:
            continue
        filas.append({"categoria": cat, "subcategoria": sub, "minimo": p_(m.group(2)), "maximo": p_(m.group(3)),
                      "promedio": p_(m.group(4)), "mediana": p_(m.group(5)), "cabezas": i_(m.group(6)),
                      "importe": p_(m.group(7)), "kgs": i_(m.group(8)), "kg_prom": i_(m.group(9))})
    return pd.DataFrame(filas).drop_duplicates()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2017-01")
    ap.add_argument("--aviso", action="store_true")   # lo pasa actualizar_todo.py a todos los pasos (sin esto, ERROR desde el 17-oct-2026)
    a = ap.parse_args()
    CRUDOS.mkdir(parents=True, exist_ok=True)
    hoy = date.today()
    meses = pd.period_range(a.desde, pd.Period(hoy, "M"), freq="M")
    L = [f"=== MAG Cañuelas (precios de hacienda) | {datetime.now():%Y-%m-%d %H:%M} ==="]
    nuevos, partes = 0, []
    for m in meses:
        for tramo, fin_dia in (("mes", m.days_in_month), ("hasta25", 25)):
            ini, fin = date(m.year, m.month, 1), date(m.year, m.month, min(fin_dia, m.days_in_month))
            if fin > hoy:
                fin = hoy
            archivo = CRUDOS / f"mag_{m}_{tramo}.html"
            reciente = m >= pd.Period(hoy, "M") - 1
            if not archivo.exists() or reciente:
                try:
                    archivo.write_text(pedir(ini, fin), encoding="utf-8")
                    nuevos += 1
                    time.sleep(3)
                except Exception as e:
                    L.append(f"   {m} {tramo}: no se pudo bajar ({type(e).__name__}: {e})")
                    continue
            d = parsear(archivo.read_text(encoding="utf-8"))
            if d.empty:
                continue
            d.insert(0, "tramo", tramo)
            d.insert(0, "mes", str(m))
            partes.append(d)
    df = pd.concat(partes, ignore_index=True)
    df.to_parquet(SALIDA, index=False)
    L.append(f"Pedidos nuevos: {nuevos}. Base: datos\\{SALIDA.name} ({len(df):,} filas, {df['mes'].nunique()} meses, "
             f"{df['mes'].min()} a {df['mes'].max()})")
    tot = df[df.tramo == "mes"].groupby(["mes", "categoria"]).apply(lambda q: (q.promedio * q.kgs).sum() / q.kgs.sum()).unstack()
    L.append("Precio promedio ponderado por kilo, últimos 6 meses ($/kg vivo):")
    L.append(tot.tail(6).round(0).to_string())
    L.append("ALERTAS: ninguna. Todo en orden." if not any("no se pudo" in x for x in L) else "ALERTAS: hubo meses sin bajar (ver arriba).")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
