# actualizar_licitaciones.py
# Colocaciones de deuda del Tesoro (Oficina Nacional de Crédito Público, Ministerio de Economía): planillas anuales
# "colocaciones_*.xlsx" de https://www.argentina.gob.ar/economia/finanzas/deudapublica/colocacionesdedeuda
# (desde 2020; la del año en curso se actualiza una vez por mes, con datos preliminares).
# Guarda cada versión distinta de cada planilla en datos\copias\colocaciones\ y arma datos\licitaciones_tesoro.parquet
# con TODAS las colocaciones (bonos y letras), y para las letras/bonos a TASA FIJA en pesos calcula la tasa de corte:
#   - "a descuento" (LETES, LELITES, LEDES): pago al vencimiento = 1000 por cada 1000 de valor nominal;
#   - "tasa efectiva mensual capitalizable X%" (LECAP, BONCAP): pago = 1000 × (1+X)^meses entre emisión y vencimiento;
#   TEM de corte = (pago / precio de emisión)^(30 / días entre liquidación y vencimiento) − 1;  TNA = TEM × 365/30 (como la TAMAR).
# Uso: py -3.13 actualizar_licitaciones.py [--aviso]
import hashlib
import re
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
COPIAS = C / "copias" / "colocaciones"
PAGINA = "https://www.argentina.gob.ar/economia/finanzas/deudapublica/colocacionesdedeuda"
SALIDA = C / "licitaciones_tesoro.parquet"
H = {"User-Agent": "Mozilla/5.0"}


def bajar(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=120).read()


def planillas(informe, alertas):
    """Baja la página, encuentra las planillas y guarda las versiones nuevas. Devuelve la lista de archivos locales a leer."""
    COPIAS.mkdir(parents=True, exist_ok=True)
    try:
        html = bajar(PAGINA).decode("utf-8", errors="ignore")
        links = sorted(set(re.findall(r'href="([^"]*colo[^"]*\.xlsx)"', html)))
    except Exception as e:
        alertas.append(f"L01 No se pudo leer la página de colocaciones ({type(e).__name__}: {e}); se usan las copias guardadas")
        links = []
    for l in links:
        url = l if l.startswith("http") else "https://www.argentina.gob.ar" + l
        nombre = url.rsplit("/", 1)[-1]
        try:
            datos = bajar(url)
        except Exception as e:
            alertas.append(f"L02 No se pudo bajar {nombre} ({type(e).__name__})")
            continue
        h = hashlib.md5(datos).hexdigest()[:8]
        ya = [p for p in COPIAS.glob(nombre.replace(".xlsx", "*.xlsx")) if hashlib.md5(p.read_bytes()).hexdigest()[:8] == h]
        if not ya:
            destino = COPIAS / nombre if not (COPIAS / nombre).exists() else COPIAS / nombre.replace(".xlsx", f"_{datetime.now():%Y%m%d}_{h}.xlsx")
            destino.write_bytes(datos)
            informe.append(f"   Planilla nueva o cambiada: {destino.name}")
    # para cada año, la versión más reciente (la de mayor fecha de corte en el nombre y, si hay varias, la última guardada)
    por_anio = {}
    for p in COPIAS.glob("*.xlsx"):
        m = re.search(r"(\d{2})[-_](\d{2})[-_](\d{2,4})", p.name)
        if not m:
            continue
        a = int(m.group(3)) if len(m.group(3)) == 4 else 2000 + int(m.group(3))
        clave = (a, int(m.group(2)), int(m.group(1)), p.stat().st_mtime)
        if a not in por_anio or clave > por_anio[a][0]:
            por_anio[a] = (clave, p)
    return [v[1] for _, v in sorted(por_anio.items())]


def leer(p):
    x = pd.ExcelFile(p)
    partes = []
    for hoja in x.sheet_names:
        if not re.search(r"^(bonos|letras|colocaciones)$", hoja.strip(), re.I):
            continue                          # sin "Letras ISP" (intra sector público), canjes ni otras operaciones
        crudo = pd.read_excel(x, hoja, header=None)
        fila = crudo.index[crudo.astype(str).apply(lambda r: r.str.contains("Nombre del Instrumento", case=False)).any(axis=1)]
        if not len(fila):
            continue
        d = pd.read_excel(x, hoja, header=int(fila[0]))
        d.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in d.columns]
        col = lambda pat: next((c for c in d.columns if re.search(pat, c, re.I)), None)
        m = {"instrumento": col(r"^Nombre"), "emision": col(r"emisi[oó]n$"), "vencimiento": col(r"^Vencimiento"),
             "cupon": col(r"^Cup[oó]n"), "moneda_origen": col(r"Moneda de Origen"), "liquidacion": col(r"colocaci[oó]n"),
             "vn": col(r"Valor Nominal"), "ve": col(r"Valor Efectivo"), "precio": col(r"Precio")}
        d = d[[v for v in m.values() if v]].rename(columns={v: k for k, v in m.items() if v})
        d = d[d["instrumento"].astype(str).str.contains("/", na=False)]
        d["hoja"] = hoja.strip()
        d["archivo"] = p.name
        partes.append(d)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def meses_entre(a, b):
    """Meses calendario entre emisión y vencimiento + días sobrantes/30 (entre fines de mes: meses justos).
    Validado con el comunicado del 11-sep-2024: S13D4 reapertura, TEM al corte 3,75% (calculado 3,71%)."""
    if pd.isna(a) or pd.isna(b):
        return np.nan
    from dateutil.relativedelta import relativedelta
    r = relativedelta(b, a)
    n = r.years * 12 + r.months
    fin = lambda x: (x + pd.Timedelta(days=1)).day == 1
    return float(n) if fin(a) and fin(b) else n + r.days / 30


def tasas(d):
    for c in ("emision", "vencimiento", "liquidacion"):
        d[c] = pd.to_datetime(d[c], errors="coerce")
    for c in ("vn", "ve", "precio"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    nombre = d["instrumento"].astype(str).str.upper()
    cupon = d["cupon"].astype(str).str.lower()
    pesos_fija = (d["moneda_origen"].astype(str).str.upper().str.strip() == "ARP") & \
                 ~nombre.str.contains("CER|BADLAR|TAMAR|DL|U\\$S|USD|LINK|DUAL|TM20|PASE|LEPASE", regex=True)
    tem_cap = cupon.str.extract(r"capitalizable\s*([\d.,]+)\s*%")[0].str.replace(",", ".").astype(float) / 100
    descuento = cupon.str.contains("descuento") & pesos_fija
    capitaliz = tem_cap.notna() & pesos_fija
    meses = [meses_entre(a, b) for a, b in zip(d["emision"], d["vencimiento"])]
    d["pago_final"] = np.where(descuento, 1000.0, np.where(capitaliz, 1000 * (1 + tem_cap) ** meses, np.nan))
    d["dias"] = (d["vencimiento"] - d["liquidacion"]).dt.days
    d["tasa_fija"] = descuento | capitaliz
    ok = d["tasa_fija"] & (d["precio"] > 0) & (d["dias"] > 0)
    d["tem_corte"] = np.where(ok, (d["pago_final"] / d["precio"]) ** (30 / d["dias"]) - 1, np.nan) * 100
    d["tna_corte"] = d["tem_corte"] * 365 / 30
    d["tipo"] = nombre.str.split("/").str[0].str.strip()
    return d


def main():
    aviso = "--aviso" in sys.argv
    informe = [f"=== LICITACIONES DEL TESORO (colocaciones de deuda) | {datetime.now():%Y-%m-%d %H:%M} ==="]
    alertas = []
    archivos = planillas(informe, alertas)
    d = pd.concat([leer(p) for p in archivos], ignore_index=True)
    d = tasas(d)
    # dato raro: tasa de corte fuera de 0-30% mensual
    raras = d[d["tem_corte"].notna() & ~d["tem_corte"].between(0, 30)]
    if len(raras):
        alertas.append(f"L03 {len(raras)} colocaciones a tasa fija con TEM fuera de 0-30% (se dejan vacías): "
                       + ", ".join(raras["instrumento"].astype(str).head(5)))
        d.loc[raras.index, ["tem_corte", "tna_corte"]] = np.nan
    if SALIDA.exists():                       # nunca perder lo guardado: conservar filas viejas que ya no vienen
        ant = pd.read_parquet(SALIDA)
        clave = ["instrumento", "liquidacion", "vn"]
        falta = ant.merge(d[clave].drop_duplicates(), on=clave, how="left", indicator=True)
        falta = falta[falta["_merge"] == "left_only"].drop(columns="_merge")
        if len(falta):
            alertas.append(f"L04 {len(falta)} colocaciones guardadas ya no vienen en las planillas: se conservan")
            d = pd.concat([d, falta], ignore_index=True)
    d = d.sort_values(["liquidacion", "instrumento"]).reset_index(drop=True)
    d.to_parquet(SALIDA, index=False)
    f = d[d["tna_corte"].notna()]
    informe += [f"Planillas leídas: {', '.join(p.name for p in archivos)}",
                f"Colocaciones guardadas: {len(d):,} (a tasa fija en pesos con tasa calculada: {len(f):,})",
                f"Último dato: liquidación {d['liquidacion'].max():%Y-%m-%d}"]
    ult = f[f["liquidacion"] == f["liquidacion"].max()].sort_values("dias")
    informe.append("Última licitación a tasa fija (TNA de corte, %):")
    for _, x in ult.iterrows():
        informe.append(f"   {x['instrumento']:28} {x['dias']:4.0f} días  TEM {x['tem_corte']:.2f}%  TNA {x['tna_corte']:.2f}%")
    informe.append("ALERTAS: " + ("; ".join(alertas) if alertas else "ninguna. Todo en orden."))
    texto = "\n".join(informe)
    print(texto)
    (C / "informe_licitaciones.txt").write_text(texto, encoding="utf-8")
    if aviso and alertas:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "\n".join(alertas)[:900], "Licitaciones del Tesoro", 0x40)
        except Exception:
            pass
    sys.exit(1 if alertas else 0)


if __name__ == "__main__":
    main()
