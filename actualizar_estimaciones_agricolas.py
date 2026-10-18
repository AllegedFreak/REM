# actualizar_estimaciones_agricolas.py
# Informes mensuales de "Estimaciones Agrícolas" del Ministerio de Agricultura (Dirección Nacional de Información y Mercados):
# https://www.magyp.gob.ar/sitio/areas/estimaciones/estimaciones/informes/?mes=AAAA-MM
# Baja el PDF del informe mensual de cada mes (desde 2016; solo los que falten) a datos\copias\estimaciones_agricolas\
# y extrae, para trigo, maíz y soja, la superficie implantada y la producción estimada de la campaña en curso y de la
# anterior, con la fecha del informe (lo que se sabía en cada momento: sirve en tiempo real).
# Salida: datos\estimaciones_agricolas.parquet (fecha_informe, cultivo, campania, superficie_ha, produccion_ton,
#         superficie_ant_ha, produccion_ant_ton). Si un informe no tiene producción para un cultivo ("-"), queda vacía.
# Uso: py -3.13 actualizar_estimaciones_agricolas.py [--aviso]
import re
import sys
import time
import urllib.parse
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
COPIAS = C / "copias" / "estimaciones_agricolas"
SITIO = "https://www.magyp.gob.ar"
INDICE = SITIO + "/sitio/areas/estimaciones/estimaciones/informes/?mes={}"
SALIDA = C / "estimaciones_agricolas.parquet"
H = {"User-Agent": "Mozilla/5.0"}
CULTIVOS = {"TRIGO": "trigo", "MAÍZ": "maiz", "MAIZ": "maiz", "SOJA": "soja"}


def bajar(url, intentos=3):
    for i in range(intentos):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=120).read()
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(5)


def descargar(informe, alertas, desde="2016-01"):
    COPIAS.mkdir(parents=True, exist_ok=True)
    nuevos = 0
    hoy = pd.Period(datetime.now(), "M")
    for m in pd.period_range(desde, hoy, freq="M"):
        ya = list(COPIAS.glob(f"{str(m.year)[2:]}{m.month:02d}*Mensual*.pdf"))
        if ya and m < hoy - 1:
            continue
        try:
            html = bajar(INDICE.format(m)).decode("utf-8", errors="ignore")
        except Exception as e:
            alertas.append(f"E01 no se pudo leer el índice de {m} ({type(e).__name__})")
            continue
        for href in re.findall(r'href="([^"]+\.pdf)"', html, re.I):
            nombre = urllib.parse.unquote(href.rsplit("/", 1)[-1])
            if "mensual" not in nombre.lower() or (COPIAS / nombre).exists():
                continue
            try:
                (COPIAS / nombre).write_bytes(bajar(SITIO + urllib.parse.quote(href)))
                nuevos += 1
            except Exception as e:
                alertas.append(f"E02 no se pudo bajar {nombre} ({type(e).__name__})")
        time.sleep(0.5)
    informe.append(f"Informes mensuales nuevos bajados: {nuevos}")


def numero(x):
    x = x.strip()
    if not re.fullmatch(r"[\d.]+", x):
        return np.nan
    return float(x.replace(".", ""))


def fecha_informe(nombre, texto):
    m = re.match(r"(\d{2})(\d{2})(\d{2})", nombre)
    return pd.Timestamp(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else pd.NaT


def leer_pdf(p):
    import pypdf
    try:
        paginas = [pg.extract_text() or "" for pg in pypdf.PdfReader(str(p)).pages]
    except Exception:
        return []
    filas = []
    for txt in paginas:
        t = re.sub(r"[ \t]+", " ", txt)
        cab = re.search(r"^\s*(TRIGO|MA[IÍ]Z|SOJA)\s*$", t, re.M)
        if not cab:
            continue
        cultivo = CULTIVOS[cab.group(1).upper().replace("Í", "Í")] if cab.group(1).upper() in CULTIVOS else CULTIVOS.get(cab.group(1).upper().replace("Í", "I"))
        # campaña en curso = la más nueva de los encabezados de la tabla (antes de "Superficie"), p. ej. "21/22 ... 22/23"
        cabecera = t[:t.find("Superficie")] if "Superficie" in t else t
        pares = [(int(a), int(b)) for a, b in re.findall(r"(?<!\d)(\d{2})/(\d{2})(?!\d)", cabecera) if int(b) == (int(a) + 1) % 100]
        camp = None
        if pares:
            a, b = max(pares)
            camp = (str(2000 + a), f"{b:02d}")
        tok = r"([\d.]+|-)"

        def fila(etiqueta):
            m = re.search(etiqueta + r"[^\d\-]*?" + tok + r"\s+" + tok + r"\s+" + tok, t, re.S)
            return [numero(x) for x in m.groups()] if m else [np.nan] * 3
        sup = fila(r"Superficie\s*\n?\s*implantada\s*\n?\s*\(ha\)")
        prod = fila(r"Producci[oó]n\s*\n?\s*estimada\s*\n?\s*\(ton\)")
        filas.append({"cultivo": cultivo, "campania": f"{camp[0]}/{camp[1]}" if camp else None,
                      "superficie_ant_ha": sup[0], "superficie_ha": sup[2] if pd.notna(sup[2]) else sup[1],
                      "produccion_ant_ton": prod[0], "produccion_ton": prod[2]})
    return filas


def main():
    aviso = "--aviso" in sys.argv
    informe = [f"=== ESTIMACIONES AGRÍCOLAS (Ministerio de Agricultura) | {datetime.now():%Y-%m-%d %H:%M} ==="]
    alertas = []
    descargar(informe, alertas)
    filas = []
    for p in sorted(COPIAS.glob("*.pdf")):
        f = fecha_informe(p.name, "")
        for x in leer_pdf(p):
            filas.append({"fecha_informe": f, "archivo": p.name, **x})
    d = pd.DataFrame(filas).drop_duplicates(["fecha_informe", "cultivo"], keep="first")
    d.to_parquet(SALIDA, index=False)
    informe.append(f"Informes leídos: {d['fecha_informe'].nunique()} ({d['fecha_informe'].min():%Y-%m} a {d['fecha_informe'].max():%Y-%m}); "
                   f"filas {len(d)}; con producción de la campaña en curso: {d['produccion_ton'].notna().sum()}")
    falta = d.groupby("fecha_informe")["cultivo"].nunique()
    if (falta < 3).any():
        informe.append(f"   Informes sin los 3 cultivos (formato distinto o cultivo sin sección ese mes): {(falta < 3).sum()}")
    ult = d[d["fecha_informe"] == d["fecha_informe"].max()]
    for _, x in ult.iterrows():
        informe.append(f"   {x['cultivo']:6} {x['campania']}: producción {x['produccion_ton'] / 1e6:6.1f} Mt (anterior {x['produccion_ant_ton'] / 1e6:5.1f} Mt)")
    informe.append("ALERTAS: " + ("; ".join(alertas) if alertas else "ninguna. Todo en orden."))
    texto = "\n".join(informe)
    print(texto)
    (C / "informe_estimaciones_agricolas.txt").write_text(texto, encoding="utf-8")
    sys.exit(1 if alertas else 0)


if __name__ == "__main__":
    main()
