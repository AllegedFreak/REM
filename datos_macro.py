# datos_macro.py
# Módulo común (no se corre solo): arma las series mensuales y anuales para el análisis de dinero, precios y actividad.
#   precios()   -> 3 versiones del IPC mensual (variación %) después de 2006
#   dinero()    -> base monetaria y M2 en pesos (promedio mensual de la diaria, BCRA)
#   tipo_cambio() -> A3500 promedio mensual (BCRA)
#   actividad() -> EMAE oficial, índice alternativo (indicadores físicos) y EMAE con 2008-2015 reemplazado
#   pib_anual() -> PIB nominal y real anual del INDEC
# Las series de datos.gob.ar se bajan del CSV original (la API a veces devuelve meses vacíos que sí tienen dato).
import io
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
API_BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"

NEUQUEN = "196.1_NIVEL_GENERAL_2014_0_13"
SAN_LUIS = "197.1_NIVEL_GENERAL_2014_0_13"
EMAE = "143.3_NO_PR_2004_A_21"
PIB_NOMINAL = "166.1_PPIB_0_0_3"      # millones de $ corrientes, desde 2006
PIB_REAL = "9.1_PP2_2004_A_16"        # millones de $ de 2004
DEFLACTOR = "113.1_IOGP_2004_A_21"    # 2004 = 100
INDICADORES = {   # actividad física, ninguno del INDEC como fuente primaria del dato
    "cemento": "41.3_CP_0_A_16",                 # despachos de cemento (AFCP)
    "acero": "359.3_ACERO_CRUDUDO__11",          # acero crudo (Cámara Argentina del Acero)
    "autos": "41.3_AA_0_A_23",                   # producción de automóviles (ADEFA)
    "electricidad": "367.3_DEMANDA_TOTAL__13",   # demanda de electricidad (CAMMESA)
    "gas_industria": "364.3_INDUSTRIARIA__9",    # consumo de gas de la industria (Secretaría de Energía)
}
CONFIABLE = [("2005-01", "2007-12"), ("2016-01", "2100-12")]   # tramos del EMAE sin cuestionamiento (censura FMI 2013)
VERSIONES_PRECIOS = {
    "oficial": "INDEC oficial todo el período (2007-2015 intervenido)",
    "neuquen": "INDEC hasta 2006-12, Neuquén 2007-01 a 2012-07, IPCBA 2012-08 a 2018-12, INDEC desde 2019",
    "sanluis": "INDEC hasta 2006-12, San Luis 2007-01 a 2012-07, IPCBA 2012-08 a 2018-12, INDEC desde 2019",
    "ipcba2012": "INDEC oficial hasta 2012-07, IPCBA 2012-08 a 2018-12, INDEC desde 2019",
}


def _bajar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def serie_datos_gob(sid):
    """Serie de datos.gob.ar desde el CSV original de su distribución. Índice: período mensual o año."""
    meta = json.loads(_bajar(f"https://apis.datos.gob.ar/series/api/series/?ids={sid}&metadata=full&limit=1&format=json"))["meta"]
    df = pd.read_csv(io.BytesIO(_bajar(meta[1]["distribution"]["downloadURL"])))
    s = pd.Series(pd.to_numeric(df[meta[1]["field"]["title"]], errors="coerce").values, index=pd.to_datetime(df["indice_tiempo"]))
    s = s.dropna().sort_index()
    if meta[0]["frequency"] == "year":
        s.index = s.index.year
    else:
        s.index = s.index.to_period("M")
    return s


def bcra(vid, desde="2002-01-01"):
    filas, offset = [], 0
    while True:
        d = json.loads(_bajar(f"{API_BCRA}/{vid}?desde={desde}&limit=3000&offset={offset}"))
        det = d["results"][0]["detalle"] if d["results"] else []
        filas += [(x["fecha"], x["valor"]) for x in det]
        offset += 3000
        if offset >= d["metadata"]["resultset"]["count"] or not det:
            break
    return pd.Series({pd.Timestamp(f): v for f, v in filas}).sort_index()


def promedio_mensual(s_diaria):
    return s_diaria.groupby(s_diaria.index.to_period("M")).mean()


def desestacionalizar_log(nivel):
    """log del nivel sin el componente estacional (descomposición clásica aditiva sobre el log)."""
    from statsmodels.tsa.seasonal import seasonal_decompose
    lg = np.log(nivel.dropna())
    est = seasonal_decompose(pd.Series(lg.values, index=lg.index.to_timestamp()), model="additive", period=12).seasonal
    est.index = lg.index
    return lg - est


def precios():
    """Variación mensual % del IPC en las 4 versiones. Columnas: oficial, neuquen, ipcba2012, sanluis."""
    oficial = bcra(27, "2002-01-01")
    oficial.index = oficial.index.to_period("M")
    p = pd.read_parquet(CARPETA / "precios_encadenada.parquet")
    p.index = pd.to_datetime(p["mes"]).dt.to_period("M")
    cadena = p["var_mensual"]   # IPCBA 2012-08 a 2018-12, INDEC desde 2019
    provincias = {"neuquen": serie_datos_gob(NEUQUEN), "sanluis": serie_datos_gob(SAN_LUIS)}
    fin = cadena.last_valid_index()
    idx = pd.period_range("2002-01", fin, freq="M")
    out = pd.DataFrame(index=idx)
    out["oficial"] = oficial.reindex(idx)
    for col in ("neuquen", "ipcba2012", "sanluis"):
        out[col] = out["oficial"]
    t = (idx >= pd.Period("2007-01", "M")) & (idx <= pd.Period("2012-07", "M"))
    c = idx >= pd.Period("2012-08", "M")
    for col, niv in provincias.items():
        out.loc[t, col] = ((niv / niv.shift(1) - 1) * 100).reindex(idx[t]).values
        out.loc[c, col] = cadena.reindex(idx[c]).values
    out.loc[c, "ipcba2012"] = cadena.reindex(idx[c]).values
    out = out[["oficial", "neuquen", "ipcba2012", "sanluis"]]
    if out.isna().any().any():
        faltan = out[out.isna().any(axis=1)].index
        raise ValueError(f"faltan variaciones de precios en {', '.join(str(x) for x in faltan)}")
    return out


def indice(var):
    return 100 * (1 + var / 100).cumprod()


def dinero():
    m = pd.read_parquet(CARPETA / "bcra_series_mensual.parquet")
    m = m[m["serie"].isin(["base_monetaria", "m2_pesos"]) & (m["dias"] >= 15)]
    m["periodo"] = pd.to_datetime(m["mes"]).dt.to_period("M")
    return m.pivot(index="periodo", columns="serie", values="promedio").rename(columns={"base_monetaria": "base", "m2_pesos": "m2"})


def tipo_cambio():
    m = pd.read_parquet(CARPETA / "bcra_series_mensual.parquet")
    m = m[(m["serie"] == "tc_mayorista_a3500") & (m["dias"] >= 10)]
    return pd.Series(m["promedio"].values, index=pd.to_datetime(m["mes"]).dt.to_period("M"), name="tc")


def actividad():
    """Devuelve (mensual, informe):
       mensual: emae (oficial), alternativo (indicadores físicos), emae_sin_0815 (EMAE con 2008-2015 reemplazado
                por el alternativo, encadenado por variaciones interanuales) y la predicción interanual del alternativo
       informe: dict con pesos, ajuste y validación fuera de muestra."""
    emae = serie_datos_gob(EMAE)
    ind = pd.DataFrame({k: serie_datos_gob(v) for k, v in INDICADORES.items()})
    ind = ind[ind.index >= pd.Period("2003-01", "M")]
    d12 = lambda s: np.log(s) - np.log(s.shift(12))   # variación interanual en log
    y = d12(emae).rename("emae")
    X = ind.apply(d12)
    datos = pd.concat([y, X], axis=1)
    confiable = np.zeros(len(datos), dtype=bool)
    for a, b in CONFIABLE:
        confiable |= (datos.index >= pd.Period(a, "M")) & (datos.index <= pd.Period(b, "M"))
    ajuste = datos[confiable].dropna()
    mod = sm.OLS(ajuste["emae"], sm.add_constant(ajuste[list(INDICADORES)])).fit()
    pred = mod.predict(sm.add_constant(X.dropna()))

    # validación: estimar solo con 2016+ y predecir 2005-2007 (tramo confiable que el modelo no vio)
    a2 = ajuste[ajuste.index >= pd.Period("2016-01", "M")]
    m2 = sm.OLS(a2["emae"], sm.add_constant(a2[list(INDICADORES)])).fit()
    val = ajuste[ajuste.index <= pd.Period("2007-12", "M")]
    p2 = m2.predict(sm.add_constant(val[list(INDICADORES)]))
    err = (val["emae"] - p2) * 100
    err_ingenuo = (val["emae"] - a2["emae"].mean()) * 100   # predecir siempre el crecimiento medio

    # niveles: se arranca con el EMAE de 2004 y se encadena por variaciones interanuales
    idx = emae.index
    alt = pd.Series(np.nan, index=idx)
    mix = pd.Series(np.nan, index=idx)
    for t in idx:
        if t.year == 2004:
            alt[t] = mix[t] = emae[t]
            continue
        ant = t - 12
        g_alt = pred.get(t, np.nan)
        alt[t] = alt[ant] * np.exp(g_alt)
        en_tramo = pd.Period("2008-01", "M") <= t <= pd.Period("2015-12", "M")
        mix[t] = mix[ant] * np.exp(g_alt if en_tramo else y[t])
    mensual = pd.DataFrame({"emae": emae, "alternativo": alt, "emae_sin_0815": mix, "pred_ia": pred, "emae_ia": y})
    info = {"modelo": mod, "validacion_mae": float(err.abs().mean()), "validacion_ingenuo": float(err_ingenuo.abs().mean()),
            "validacion_sesgo": float(err.mean()), "n_ajuste": len(ajuste)}
    return mensual, info


IPC_DIVISIONES_CSV = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv"   # INDEC directo, al día


def ipc_categorias():
    """IPC nacional del INDEC por categorías y divisiones (índice dic-2016=100), bajado directo del INDEC.
    Columnas: general, nucleo, regulados, estacional, bienes, servicios y divisiones COICOP 01-12.
    El CSV de datos.gob.ar con estas categorías está cortado en mar-2020 (y con un valor corrupto): no usarlo."""
    copia = CARPETA / "copias" / "serie_ipc_divisiones.csv"
    try:
        req = urllib.request.Request(IPC_DIVISIONES_CSV, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            contenido = r.read()
        copia.parent.mkdir(exist_ok=True)
        copia.write_bytes(contenido)
    except Exception:
        if not copia.exists():
            raise
        contenido = copia.read_bytes()
    df = pd.read_csv(io.StringIO(contenido.decode("latin-1")), sep=";", decimal=",")
    df = df[df["Region"] == "Nacional"].copy()
    nombres = {"0": "general", "Núcleo": "nucleo", "Regulados": "regulados", "Estacional": "estacional", "B": "bienes", "S": "servicios"}
    df["serie"] = df["Codigo"].astype(str).map(lambda c: nombres.get(c, f"div_{c}"))
    df["periodo"] = pd.PeriodIndex(df["Periodo"].astype(str), freq="M")
    out = df.pivot_table(index="periodo", columns="serie", values="Indice_IPC", aggfunc="first").sort_index()
    faltan = pd.period_range(out.index.min(), out.index.max(), freq="M").difference(out.index)
    if len(faltan):
        raise ValueError(f"faltan meses en el IPC por categorías del INDEC: {', '.join(str(x) for x in faltan)}")
    return out


SALARIOS_CSV = "https://www.indec.gob.ar/ftp/cuadros/sociedad/indice_salarios.csv"   # desde oct-2015, demora ~2 meses


def salarios():
    """Índice de salarios del INDEC (oct-2016=100). Columnas: privado_registrado, publico, total_registrado,
    no_registrado, total. Guarda una copia en datos/copias. Se publica con ~2 meses de demora."""
    copia = CARPETA / "copias" / "indice_salarios.csv"
    try:
        req = urllib.request.Request(SALARIOS_CSV, headers={"User-Agent": "Mozilla/5.0"})   # sin Accept: JSON (el INDEC da 406)
        with urllib.request.urlopen(req, timeout=120) as r:
            contenido = r.read()
        copia.parent.mkdir(exist_ok=True)
        copia.write_bytes(contenido)
    except Exception:
        if not copia.exists():
            raise
        contenido = copia.read_bytes()   # sin conexión con el INDEC: última copia guardada
    df = pd.read_csv(io.BytesIO(contenido), sep=";", decimal=",", na_values=["NA"])
    df.index = pd.to_datetime(df["periodo"], dayfirst=True).dt.to_period("M")
    return df.drop(columns="periodo").rename(columns={
        "IS_sector_privado_registrado": "privado_registrado", "IS_sector_publico": "publico",
        "IS_total_registrado": "total_registrado", "IS_sector_no_registrado": "no_registrado", "IS_indice_total": "total"}).astype(float)


def pib_anual():
    nominal = serie_datos_gob(PIB_NOMINAL)
    real = serie_datos_gob(PIB_REAL)
    defl = serie_datos_gob(DEFLACTOR)
    df = pd.DataFrame({"pib_nominal": nominal, "pib_real": real, "deflactor": defl})
    df["nominal_desde_real"] = df["pib_real"] * df["deflactor"] / 100
    df["fuente_nominal"] = np.where(df["pib_nominal"].notna(), "INDEC", "real x deflactor")
    df["pib_nominal"] = df["pib_nominal"].fillna(df["nominal_desde_real"])
    return df
