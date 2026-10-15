# regresiones_2016.py
# Reproduce las regresiones en NIVELES de Marcos (Stata, ~2016) con datos actuales y las compara con
# las mismas regresiones en logaritmos y en variaciones mensuales.
#   - "regress ipcemp totalconcuasi": índice de precios empalmado contra base monetaria + cuasimonedas
#   - IPC contra CER y contra CVS (Coeficiente de Variación Salarial del INDEC, web del INDEC)
# Precios (tres versiones):
#   oficial            = IPC del INDEC según el BCRA (id 27, inflación mensual encadenada; 2007-2015 intervenido)
#   empalmado San Luis = INDEC hasta 2006-12, IPC San Luis 2007-01 a 2012-07, IPCBA 2012-08 a 2018-12, INDEC desde 2019-01
#                        (aproxima el ipcemp de Marcos, que usaba el índice de Graciela Bevacqua desde 2007)
#   empalmado Neuquén  = igual, con el IPC de Neuquén en 2007-2012
# Dinero: base monetaria + cuasimonedas (BCRA id 73, desde ene-2003), base sola (id 15), billetes y monedas en
#   poder del público (id 17); promedio mensual de la diaria
# CER: BCRA id 30, promedio mensual de la diaria
# Muestras: 2002-12 a 2015-09 (como Stata: 153 meses con la base con cuasimonedas, 154 con CER), 2002-12 a 2007-12
#   (60 / 61 meses) y 2002-12 a la fecha
# Salida: datos/informe_regresiones_2016.txt
# Uso: py -3.13 regresiones_2016.py
import io
import json
import sys
import urllib.request
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.stattools import durbin_watson
from statsmodels.tsa.seasonal import seasonal_decompose
from statsmodels.tsa.stattools import coint

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API_BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
# IPC provinciales para 2007-2012 (INDEC intervenido). San Luis es el que mejor reproduce el ipcemp de Marcos
# (índice de Graciela Bevacqua, no disponible para descargar).
PROVINCIAS = {"San Luis": "197.1_NIVEL_GENERAL_2014_0_13", "Neuquén": "196.1_NIVEL_GENERAL_2014_0_13"}
# Desde 2002-12: así los conteos coinciden con Stata (base con cuasimonedas empieza en 2003-01 -> 153 y 60 meses;
# precios y CER desde 2002-12 -> 154 y 61)
MUESTRAS = [("2002-12 a 2015-09 (como Stata)", "2002-12", "2015-09"),
            ("2002-12 a 2007-12 (muestra corta)", "2002-12", "2007-12"),
            ("2002-12 a la fecha", "2002-12", "2100-12")]

CVS_XLS = "https://www.indec.gob.ar/ftp/cuadros/sociedad/serie_cvs_2012.xls"   # CVS del INDEC, oct-2001 a oct-2015

# Capturas de Stata de Marcos (datos\datosnerds.zip): y, x, muestra corta?, n, coeficiente, R²
STATA = [
    ("ipcemp", "base + cuasimonedas", False, 153, 0.0024488, 0.9853),
    ("ipcemp", "billetes", False, 153, 0.0034605, 0.9837),
    ("ipcemp", "CER", False, 154, 514.2634, 0.6246),
    ("ipcemp", "CVS índice", False, 154, 5.492006, 0.9975),
    ("CVS índice", "base", False, 154, 0.0004455, 0.9908),
    ("base + cuasimonedas", "CVS índice", False, 154, 2217.857, 0.9908),
    ("base + cuasimonedas", "CVS priv. registrado", False, 154, 2232.104, 0.9933),
    ("base + cuasimonedas", "CVS priv. no registrado", False, 154, 1968.016, 0.9869),
    ("base + cuasimonedas", "CVS público", False, 154, 2419.961, 0.9756),
    ("ipcemp", "base + cuasimonedas", True, 60, 0.001634, 0.9591),
    ("ipcemp", "base", True, 60, 0.0014998, 0.9370),
    ("ipcemp", "billetes", True, 60, 0.0018885, 0.9409),
    ("ipcemp", "CER", True, 61, 124.269, 0.9490),
    ("ipcemp", "CVS índice", True, 61, 3.942518, 0.9722),
    ("ipcemp", "CVS priv. registrado", True, 61, 3.531861, 0.9428),
    ("ipcemp", "CVS priv. no registrado", True, 61, 5.165778, 0.9774),
    ("ipcemp", "CVS público", True, 61, 4.042469, 0.9816),
    ("CVS índice", "base", True, 60, 0.0003793, 0.9657),
]

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
INFORME = CARPETA / "informe_regresiones_2016.txt"


def pedir(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def bcra(vid, desde="2002-01-01"):
    filas, offset = [], 0
    while True:
        d = pedir(f"{API_BCRA}/{vid}?desde={desde}&limit=3000&offset={offset}")
        det = d["results"][0]["detalle"] if d["results"] else []
        filas += [(x["fecha"], x["valor"]) for x in det]
        offset += 3000
        if offset >= d["metadata"]["resultset"]["count"] or not det:
            break
    s = pd.Series({pd.Timestamp(f): v for f, v in filas}).sort_index()
    return s


def mensual(s_diaria):
    m = s_diaria.groupby(s_diaria.index.to_period("M")).mean()
    return m


def leer_cvs():
    """CVS del INDEC (base abril 2012 = 100, empalmado): nivel general y sectores. Guarda una copia en datos/copias."""
    copia = CARPETA / "copias" / "serie_cvs_2012.xls"
    if not copia.exists():
        req = urllib.request.Request(CVS_XLS, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as r:
            contenido = r.read()
        copia.parent.mkdir(exist_ok=True)
        copia.write_bytes(contenido)
    h = pd.read_excel(copia, sheet_name="Serie histórica CVS", header=None)
    h = h[pd.to_datetime(h[0], errors="coerce").notna()]
    h.index = pd.to_datetime(h[0]).dt.to_period("M")
    return pd.DataFrame({"priv_reg": h[1], "priv_noreg": h[2], "publico": h[3], "general": h[4]}).astype(float)


def provincia_csv(sid):
    """IPC provincial de datos.gob.ar, bajado del CSV original: la API a veces devuelve meses vacíos que sí tienen dato."""
    meta = pedir(f"https://apis.datos.gob.ar/series/api/series/?ids={sid}&metadata=full&limit=1&format=json")["meta"][1]
    req = urllib.request.Request(meta["distribution"]["downloadURL"], headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        df = pd.read_csv(io.BytesIO(r.read()))
    return pd.Series(pd.to_numeric(df[meta["field"]["title"]], errors="coerce").values,
                     index=pd.to_datetime(df["indice_tiempo"]).dt.to_period("M")).sort_index()


def indice_desde_var(var, inicio, fin):
    v = var[(var.index >= inicio) & (var.index <= fin)]
    if v.isna().any():
        raise ValueError(f"faltan variaciones de precios en {', '.join(str(x) for x in v.index[v.isna()])}")
    return 100 * (1 + v / 100).cumprod() / (1 + v.iloc[0] / 100)   # = 100 en el mes inicial


def regresion(y, x, forma):
    d = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    mod = sm.OLS(d["y"], sm.add_constant(d["x"])).fit()   # como "regress" de Stata (errores comunes)
    res = {"n": len(d), "coef": mod.params["x"], "t": mod.tvalues["x"], "r2": mod.rsquared, "dw": durbin_watson(mod.resid)}
    res["coint_p"] = coint(d["y"], d["x"], trend="c")[1] if forma in ("niveles", "logaritmos") else np.nan
    return res


def main():
    # ---------- datos ----------
    infl_oficial = bcra(27, "2001-12-01")
    infl_oficial.index = infl_oficial.index.to_period("M")
    base_cuasi = mensual(bcra(73))
    base_sola = mensual(bcra(15))
    billetes = mensual(bcra(17))
    cer = mensual(bcra(30))
    p = pd.read_parquet(CARPETA / "precios_encadenada.parquet")
    p.index = pd.to_datetime(p["mes"]).dt.to_period("M")
    inicio = pd.Period("2002-12", "M")
    ultimo = min(p.index.max(), base_cuasi.index.max(), cer.index.max())
    precios = {"oficial": indice_desde_var(infl_oficial, inicio, ultimo)}
    for prov, sid in PROVINCIAS.items():   # INDEC hasta 2006-12, provincia 2007-01 a 2012-07, después la serie encadenada
        niv = provincia_csv(sid)
        var = infl_oficial.copy()
        tramo = (var.index >= pd.Period("2007-01", "M")) & (var.index <= pd.Period("2012-07", "M"))
        var[tramo] = ((niv / niv.shift(1) - 1) * 100).reindex(var.index[tramo])
        cadena = var.index >= pd.Period("2012-08", "M")
        var[cadena] = p["var_mensual"].reindex(var.index[cadena])
        precios[f"empalmado {prov}"] = indice_desde_var(var, inicio, ultimo)

    def var_desestacionalizada(nivel):   # solo para la versión en variaciones de los agregados monetarios
        lg = np.log(nivel[nivel.index >= pd.Period("2003-01", "M")])
        est = seasonal_decompose(pd.Series(lg.values, index=lg.index.to_timestamp()), period=12).seasonal
        est.index = lg.index
        return (lg - est).diff() * 100

    def formas(y_nivel, x_nivel, x_var=None):
        return {"niveles": (y_nivel, x_nivel),
                "logaritmos": (np.log(y_nivel), np.log(x_nivel)),
                "variaciones": ((y_nivel / y_nivel.shift(1) - 1) * 100,
                                x_var if x_var is not None else (x_nivel / x_nivel.shift(1) - 1) * 100)}

    dinero = {"base + cuasimonedas": base_cuasi, "base (sin cuasimonedas)": base_sola, "billetes en poder del público": billetes}
    casos = []
    for dn, dv in dinero.items():
        for pn, pr in precios.items():
            casos.append((f"IPC {pn} vs {dn}", formas(pr, dv, var_desestacionalizada(dv))))
    for pn, pr in precios.items():
        casos.append((f"IPC {pn} vs CER", formas(pr, cer)))

    L = ["=== REGRESIONES TIPO 2016: NIVELES vs LOGARITMOS vs VARIACIONES ===",
         f"Datos hasta {ultimo}. Precios: oficial = INDEC (BCRA id 27); empalmado San Luis / Neuquén = INDEC hasta 2006-12, "
         "IPC provincial 2007-01 a 2012-07, IPCBA 2012-08 a 2018-12, INDEC desde 2019-01.",
         "Base + cuasimonedas: BCRA id 73 (millones de $, promedio mensual). CER: BCRA id 30 (promedio mensual).",
         "Variaciones: % mensual; la base va desestacionalizada.",
         "DW = Durbin-Watson. Regla de Granger-Newbold: si R² > DW, sospechar regresión espuria.",
         "Cointegración (Engle-Granger): p < 0,05 = la relación en niveles/logaritmos es de largo plazo y no espuria.", ""]
    L.append(f"{'regresión':38} {'muestra':34} {'forma':12} {'n':>4} {'coef':>11} {'t':>7} {'R²':>6} {'DW':>5} {'coint p':>8}")
    for nombre, fs in casos:
        for mn, d0, d1 in MUESTRAS:
            for forma, (y, x) in fs.items():
                idx = (y.index >= pd.Period(d0, "M")) & (y.index <= pd.Period(d1, "M"))
                r = regresion(y[idx], x, forma)
                cp = "" if np.isnan(r["coint_p"]) else f"{r['coint_p']:.3f}"
                L.append(f"{nombre:38} {mn:34} {forma:12} {r['n']:4} {r['coef']:11.4g} {r['t']:7.1f} {r['r2']:6.3f} {r['dw']:5.2f} {cp:>8}")
            L.append("")
    L.append("Nota: el coeficiente en niveles depende de las unidades (índice y millones de $), por eso no se compara con el de Stata; el R² sí.")

    # ---------- comparación directa con las capturas de Stata (niveles) ----------
    cvs = leer_cvs()
    var_x = {"base + cuasimonedas": base_cuasi, "base": base_sola, "billetes": billetes, "CER": cer,
             "CVS índice": cvs["general"], "CVS priv. registrado": cvs["priv_reg"],
             "CVS priv. no registrado": cvs["priv_noreg"], "CVS público": cvs["publico"]}
    L += ["", "=== COMPARACIÓN CON LAS CAPTURAS DE STATA (regresiones en niveles) ===",
          "Muestras: con IPC 2002-12 a 2015-09 (larga) / 2002-12 a 2007-12 (corta); sin IPC, 2003-01 a 2015-10 / 2003-01 a 2007-12",
          "(así cierran los conteos de Stata: los agregados monetarios empiezan en 2003-01 y el CVS termina en 2015-10).",
          "Sin IPC el coeficiente es comparable (mismas unidades si Stata usaba millones de $); con IPC solo el R².", ""]
    L.append(f"{'regresión (y ~ x)':44} {'muestra':6} | {'Stata: n':>8} {'coef':>10} {'R²':>6} | " +
             " | ".join(f"{pn[:16]:>16}: n   R²   " for pn in precios))
    for y_nom, x_nom, corta, n_st, coef_st, r2_st in STATA:
        if y_nom == "ipcemp":
            d0, d1 = ("2002-12", "2007-12") if corta else ("2002-12", "2015-09")
        else:
            d0, d1 = ("2003-01", "2007-12") if corta else ("2003-01", "2015-10")
        rango = lambda s: s[(s.index >= pd.Period(d0, "M")) & (s.index <= pd.Period(d1, "M"))]
        x = rango(var_x[x_nom])
        if y_nom == "ipcemp":
            res = [regresion(rango(pr), x, "") for pr in precios.values()]
            ours = " | ".join(f"{'':>10}{r['n']:4} {r['r2']:6.3f} " for r in res)
        else:
            r = regresion(rango(var_x[y_nom]), x, "")
            ours = f"n={r['n']}  coef={r['coef']:.4g}  R²={r['r2']:.4f}"
        L.append(f"{y_nom + ' ~ ' + x_nom:44} {'corta' if corta else 'larga':6} | {n_st:8} {coef_st:10.4g} {r2_st:6.4f} | {ours}")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
