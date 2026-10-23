# publicar_web.py
# Arma el sitio estático "Datos Con Método" en rem\web\actual\ a partir de lo que ya está en datos\.
#   - Plantilla (HTML, CSS, JS, librerías) en rem\sitio\ (no se toca).
#   - Datos para la página en web\actual\data\: resumen.json (pronósticos de hoy, semáforo, historial contra el REM),
#     fuentes.json (estado de cada fuente), catalogo.json + s\<id>.json (una serie por archivo, para descargar en .xlsx).
#   - Tablas completas en web\actual\descargas\*.xlsx (nuestros pronósticos registrados, historial, REM, licitaciones...).
# Se arma primero en web\_nuevo y recién al final reemplaza a web\actual (el sitio nunca se ve a medio actualizar).
# La máquina virtual (VirtualBox, Apache) comparte SOLO la carpeta rem\web, en modo lectura; en Ubuntu el sitio
# www.datosconmetodo.com tiene DocumentRoot /media/sf_web/actual. Datos propios: CC BY 4.0 (Marcos Cavanagh).
# NO se publican: scripts, CLAUDE.md, datos\copias, ni las fuentes que no se pueden republicar en bloque
# (futuros del CEM/A3, hacienda MAG, Precios del Central, regulados de prensa): figuran en Fuentes, sin descarga.
# Uso: py -3.13 publicar_web.py [--aviso]
import json
import math
import shutil
import sys
import time
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
PLANTILLA = BASE / "sitio"
WEB = BASE / "web"
NUEVO = WEB / "_nuevo"
ACTUAL = WEB / "actual"
HOY = pd.Timestamp.today().normalize()
CORTE = pd.Period("2025-01", "M")          # "régimen actual" para mostrar 2025-26 por separado


# ---------------------------------------------------------------- utilidades
def limpio(x):
    """Pasa NaN/inf a None y numpy a tipos nativos, para JSON."""
    if isinstance(x, dict):
        return {k: limpio(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [limpio(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if (x is None or math.isnan(x) or math.isinf(x)) else round(float(x), 6)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.strftime("%Y-%m-%d")
    if isinstance(x, pd.Period):
        return str(x)
    if x is pd.NaT:
        return None
    return x


def guardar_json(ruta, obj):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(limpio(obj), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def ultimo_registro(nombre):
    """Filas de la última corrida del último relevamiento de datos\\registro_<nombre>.csv."""
    p = C / f"registro_{nombre}.csv"
    if not p.exists():
        return pd.DataFrame(), None
    d = pd.read_csv(p, encoding="utf-8-sig")
    d = d[d["relevamiento"] == d["relevamiento"].max()]
    u = d["fecha_corrida"].max()
    return d[d["fecha_corrida"] == u].copy(), u


def rem_ultimo():
    r = pd.read_parquet(C / "rem_normalizada.parquet", columns=["mes_relevamiento"])
    return pd.Timestamp(r["mes_relevamiento"].max()).to_period("M")


# ---------------------------------------------------------------- 1) pronósticos de hoy
def pronosticos():
    tarjetas = []
    # inflación: nuestro pronóstico de la inflación (I) y el REM anterior por mes objetivo
    i, f_i = ultimo_registro("inflacion")
    a, _ = ultimo_registro("pronosticos")
    if len(i):
        ra = a.set_index("H")["rem_anterior"] if len(a) else pd.Series(dtype=float)
        filas = []
        for _, x in i.iterrows():
            et = "Próx. 12 meses" if x["H"] == "12m" else x["objetivo"]
            filas.append({"periodo": et, "nuestro": x["pronostico_inflacion"], "rem": ra.get(x["H"], np.nan)})
        tarjetas.append({"clave": "inflacion", "titulo": "Inflación", "unidad": "% mensual (12 meses: % acumulado)",
                         "modelo": "Inflación núcleo reciente + expectativas de los hogares (UTDT) + dólar esperado; en el mes en curso, precios de supermercados", "filas": filas, "corrida": f_i,
                         "relevamiento": i["relevamiento"].iloc[0]})
    # tipo de cambio
    t, f_t = ultimo_registro("tc")
    if len(t):
        et = lambda x: (f"Dic-{x['objetivo'][:4]}" if x["h"] in (100, 101) else x["objetivo"])
        filas = [{"periodo": et(x), "nuestro": x["A_dolar"], "rem": x["rem_anterior"]} for _, x in t.iterrows()]
        tarjetas.append({"clave": "dolar", "titulo": "Dólar mayorista (A3500)", "unidad": "$ por USD, promedio del mes",
                         "modelo": "Futuros del CEM ajustados por el descuento típico del REM", "filas": filas,
                         "corrida": f_t, "relevamiento": t["relevamiento"].iloc[0]})
    # PIB
    p, f_p = ultimo_registro("pib")
    if len(p):
        filas = [{"periodo": x["trimestre"].replace("Q", " T"), "nuestro": x["I_pib"], "rem": x["rem_anterior"]} for _, x in p.iterrows()]
        tarjetas.append({"clave": "pib", "titulo": "PIB trimestral", "unidad": "% contra el trimestre anterior, desestacionalizado",
                         "modelo": "EMAE publicado + tendencia para los meses que faltan", "filas": filas,
                         "corrida": f_p, "relevamiento": p["relevamiento"].iloc[0]})
    # tasa
    s, f_s = ultimo_registro("tasa")
    if len(s):
        filas = [{"periodo": x["mes"], "nuestro": x["A"], "rem": x["rem_anterior"]} for _, x in s.iterrows()]
        tarjetas.append({"clave": "tasa", "titulo": "Tasa TAMAR", "unidad": "TNA %, promedio del mes",
                         "modelo": "Tasa actual + señal de los futuros de dólar", "filas": filas,
                         "corrida": f_s, "relevamiento": s["relevamiento"].iloc[0]})
    # resultado primario
    fi, f_f = ultimo_registro("fiscal")
    if len(fi):
        filas = [{"periodo": str(int(x["anio"])), "nuestro": x["I"] / 1000, "rem": x["rem_anterior"] / 1000} for _, x in fi.iterrows()]
        tarjetas.append({"clave": "fiscal", "titulo": "Resultado primario (SPN)", "unidad": "billones de $, año calendario",
                         "modelo": "Ejecución publicada + meta oficial para los meses que faltan", "filas": filas,
                         "corrida": f_f, "relevamiento": fi["relevamiento"].iloc[0]})
    # comercio
    co, f_c = ultimo_registro("comercio")
    for var, tit in (("exportaciones", "Exportaciones"), ("importaciones", "Importaciones")):
        q = co[co["variable"] == var] if len(co) else co
        if len(q):
            filas = [{"periodo": str(x["periodo"]), "nuestro": x["A"], "rem": x["rem_anterior"]} for _, x in q.iterrows()]
            tarjetas.append({"clave": var, "titulo": tit, "unidad": "millones de USD", "modelo": "Mismo mes del año anterior × "
                             "crecimiento reciente" + (" + precios de granos" if var == "exportaciones" else ""),
                             "filas": filas, "corrida": f_c, "relevamiento": q["relevamiento"].iloc[0]})
    return tarjetas


# ---------------------------------------------------------------- 2) historial contra el REM y contra lo que pasó
def metricas(d, n, r, y, error):
    """Error medio del nuestro y del REM sobre la misma muestra, y % de veces que el nuestro estuvo más cerca."""
    d = d.dropna(subset=[n, r, y])
    if not len(d):
        return None
    if error == "pct":
        en, er = (100 * np.log(d[n] / d[y])).abs(), (100 * np.log(d[r] / d[y])).abs()
    else:
        en, er = (d[n] - d[y]).abs(), (d[r] - d[y]).abs()
    return {"n": len(d), "nuestro": en.mean(), "rem": er.mean(), "gana_pct": 100 * (en < er).mean()}


def bloque(d, origen, n, r, y, error, etiqueta_h, hs, h_graf, x_col, unidad, titulo, nota, clave):
    """Tabla de errores por horizonte (régimen actual y toda la muestra) + serie para el gráfico."""
    filas = []
    for h in hs:
        q = d[d[etiqueta_h] == h]
        filas.append({"h": h, "actual": metricas(q[q[origen] >= CORTE], n, r, y, error), "todo": metricas(q, n, r, y, error)})
    g = d[d[etiqueta_h] == h_graf].dropna(subset=[y]).groupby(x_col)[[y, r, n]].mean().reset_index().sort_values(x_col)
    graf = {"x": [str(v) for v in g[x_col]], "real": g[y].tolist(), "rem": g[r].tolist(), "nuestro": g[n].tolist()}
    return {"clave": clave, "titulo": titulo, "unidad": unidad, "error": error, "filas": filas, "h_grafico": str(h_graf),
            "grafico": graf, "nota": nota}


def historial():
    out = []
    # inflación (I contra el IPC real), horizontes m1, m3, m6 y 12 meses
    d = pd.read_csv(C / "replica_rem.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    # real de "próximos 12 meses" (no viene en replica_rem): variación del índice encadenado entre t y t+12
    pe = pd.read_parquet(C / "precios_encadenada.parquet")
    ind = pd.Series(pe["indice"].values, index=pd.to_datetime(pe["mes"]).dt.to_period("M"))
    doce = d["H"] == "12m"
    d.loc[doce, "real"] = [(ind.get(o + 12, np.nan) / ind.get(o, np.nan) - 1) * 100 for o in d.loc[doce, "origen"]]
    d["hn"] = d["H"].str.extract(r"m(\d)")[0].astype(float)
    d["objetivo"] = [str(o + int(h)) if pd.notna(h) else None for o, h in zip(d["origen"], d["hn"])]
    out.append(bloque(d, "origen", "I", "rem", "real", "abs", "H", ["m1", "m3", "m6", "12m"], "m3", "objetivo",
                      "puntos porcentuales", "Inflación", "Gráfico: inflación de cada mes, pronosticada 3 meses antes.", "inflacion"))
    # dólar
    d = pd.read_csv(C / "replica_tc.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    d = d[d["h"].isin([1, 3, 6, 12])]
    out.append(bloque(d, "origen", "A_dolar", "rem", "real", "pct", "h", [1, 3, 6, 12], 3, "obj", "% de error",
                      "Dólar mayorista", "Gráfico: dólar promedio de cada mes, pronosticado 3 meses antes. Desde 2020 (futuros del CEM).", "dolar"))
    # PIB (trimestre en curso y siguiente)
    d = pd.read_csv(C / "replica_pib.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    out.append(bloque(d, "origen", "I_pib", "rem", "real", "abs", "k", [-1, 0, 1], 0, "Q", "puntos porcentuales",
                      "PIB trimestral", "Real = primer dato que publicó el INDEC. Gráfico: trimestre en curso (promedio de los informes de ese trimestre).", "pib"))
    # tasa
    d = pd.read_csv(C / "replica_tasa.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    out.append(bloque(d, "origen", "A", "rem", "real", "abs", "h", [1, 3, 6], 3, "mes", "puntos de TNA",
                      "Tasa (BADLAR hasta nov-2024, TAMAR desde dic-2024)", "Gráfico: tasa de cada mes, pronosticada 3 meses antes.", "tasa"))
    # resultado primario (% del PIB)
    d = pd.read_csv(C / "replica_fiscal.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    d["x"] = d["origen"].astype(str)
    out.append(bloque(d, "origen", "I_pib", "rem_pib", "real_pib", "abs", "pos", [0, 1], 0, "x", "puntos del PIB",
                      "Resultado primario", "Gráfico: pronóstico del año en curso en cada informe (en % del PIB) y el resultado final del año.", "fiscal"))
    # comercio
    d = pd.read_csv(C / "replica_comercio.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    for var, tit in (("exportaciones", "Exportaciones"), ("importaciones", "Importaciones")):
        q = d[(d["var"] == var)].copy()
        q["hh"] = np.where(q["tipo"] == "mes", "m" + q["h"].astype(str), np.where(q["h"] == 0, "año", "año+1"))
        out.append(bloque(q, "origen", "A", "rem", "real", "pct", "hh", ["m1", "m3", "m6", "año"], "m3", "objetivo", "% de error",
                          tit, "Gráfico: valor de cada mes, pronosticado 3 meses antes. El REM pregunta comercio exterior desde dic-2020.", var))
    return out


# ---------------------------------------------------------------- 3) fuentes y su estado
FREC_TOL = {"D": (5, 12), "M": (75, 120), "T": (130, 200), "A": (430, 560)}   # días desde el fin del último período: verde / amarillo


def fin_periodo(fecha, frec):
    t = pd.Timestamp(fecha)
    if frec == "M":
        return t + pd.offsets.MonthEnd(0)
    if frec == "T":
        return t + pd.offsets.QuarterEnd(0)
    if frec == "A":
        return t + pd.offsets.YearEnd(0)
    return t


def estado(ultimo, frec, historica=False):
    if historica:
        return "gris", "Histórica (no se actualiza)"
    if ultimo is None or pd.isna(ultimo):
        return "rojo", "Sin datos"
    dias = (HOY - fin_periodo(ultimo, frec)).days
    v, a = FREC_TOL.get(frec, (75, 120))
    if dias <= v:
        return "verde", "Al día"
    if dias <= a:
        return "amarillo", "Algo atrasada"
    return "rojo", "Atrasada"


def frec_norm(f):
    f = str(f).lower()
    return {"d": "D", "m": "M", "month": "M", "quarter": "T", "q": "T", "year": "A", "a": "A"}.get(f, "M")


def mtime(*rutas):
    t = [Path(r).stat().st_mtime for r in rutas if Path(r).exists()]
    return datetime.fromtimestamp(max(t)).strftime("%Y-%m-%d %H:%M") if t else None


def maximo(archivo, col, **kw):
    p = C / archivo if not Path(archivo).is_absolute() else Path(archivo)
    if not p.exists():
        return None
    try:
        s = pd.read_parquet(p, columns=[col], **kw)[col]
    except Exception:
        return None
    s = s.dropna()
    if not len(s):
        return None
    return pd.Timestamp(str(s.max())[:10]) if not isinstance(s.max(), pd.Period) else s.max().start_time


def fuentes(catalogo):
    meta = pd.read_csv(C / "fuentes.csv", encoding="utf-8-sig").set_index("conjunto")
    filas = []

    def agregar(conjunto, nombre, ultimo, frec, archivos, descarga, historica=False, manual=False, extra=None):
        m = dict(meta.loc[conjunto]) if conjunto in meta.index else {}
        if conjunto == "Series históricas (varias)":          # fila genérica del registro: el productor va en el nombre
            m["productor_original"] = m["intermediario"] = None
        if ultimo is not None and not pd.isna(ultimo) and pd.Timestamp(ultimo) > HOY:
            ultimo = HOY                                       # series con fechas futuras ya anunciadas (p. ej. banda cambiaria)
        col, txt = estado(ultimo, frec, historica)
        if manual:
            col, txt = "gris", "Se actualiza a mano"
        filas.append({"nombre": nombre, "productor": m.get("productor_original"), "intermediario": m.get("intermediario"),
                      "url": m.get("url"), "cita": m.get("cita_sugerida"), "condiciones": m.get("condiciones_uso"),
                      "ultimo_dato": ultimo, "frecuencia": frec, "actualizado": mtime(*archivos), "estado": col,
                      "estado_txt": txt, "descarga": descarga, **(extra or {})})

    agregar("REM BCRA", "Relevamiento de Expectativas de Mercado (REM)", maximo("rem_normalizada.parquet", "mes_relevamiento"), "M",
            [C / "rem_normalizada.parquet"], True)
    agregar("Series BCRA (API)", "BCRA: tipo de cambio, tasas y dinero (API, diaria)", maximo("bcra_series.parquet", "fecha"), "D",
            [C / "bcra_series.parquet"], True)
    agregar("IPC INDEC", "IPC nacional (INDEC)", maximo("ipc_indec.parquet", "mes"), "M", [C / "ipc_indec.parquet"], True)
    agregar("IPCBA", "IPC de la Ciudad de Buenos Aires (IPCBA)", maximo("ipcba.parquet", "mes"), "M", [C / "ipcba.parquet"], True)
    agregar("PIB y EMAE (INDEC)", "EMAE mensual y PIB trimestral (INDEC)", maximo("emae.parquet", "mes"), "M",
            [C / "emae.parquet", C / "pib_desest_versiones.parquet"], True)
    agregar("Dólares paralelos", "Dólar CCL, MEP y blue", maximo("dolares.parquet", "fecha"), "D", [C / "dolares.parquet"], True)
    imig = sorted((C / "copias" / "imig").glob("imig_*.csv"), key=lambda p: p.stat().st_mtime)
    ult_imig = pd.Timestamp(pd.read_csv(imig[-1])["indice_tiempo"].max()) if imig else None
    agregar("IMIG (ingresos y gastos del Sector Público Nacional)", "Ingresos y gastos del Sector Público Nacional (IMIG)", ult_imig, "M",
            imig[-1:] or [], True)
    agregar("Colocaciones de deuda del Tesoro (licitaciones)", "Licitaciones del Tesoro (colocaciones de deuda)",
            maximo("licitaciones_tesoro.parquet", "liquidacion"), "M", [C / "licitaciones_tesoro.parquet"], True)
    agregar("Estimaciones agrícolas (informes mensuales)", "Estimaciones agrícolas (trigo, maíz, soja)",
            maximo("estimaciones_agricolas.parquet", "fecha_informe"), "M", [C / "estimaciones_agricolas.parquet"], True)
    agregar("SEPA diario", "Precios de supermercados (SEPA) e índice propio", maximo("sepa_indice_diario.parquet", "fecha"), "D",
            [C / "sepa_indice_diario.parquet"], True)
    agregar("Mercado Central (precios mayoristas)", "Mercado Central: frutas y hortalizas", maximo("mercado_central.parquet", "fecha"), "D",
            [C / "mercado_central.parquet"], False)
    agregar("Metas fiscales (armado propio)", "Metas fiscales (Presupuesto y FMI, armado propio)", None, "A",
            [C / "metas_fiscales.csv"], True, manual=True)
    agregar("Ponderaciones IPC", "Ponderaciones del IPC (INDEC)", None, "A", [C / "ponderaciones_ipc.csv"], False, historica=True)
    # series históricas: una fila por fuente del catálogo
    for fu, q in catalogo[catalogo["origen"] == "historicas"].groupby("fuente"):
        vivas = q[q["estado"] != "gris"]
        ref = vivas if len(vivas) else q
        hasta = pd.to_datetime(ref["hasta"])
        ult = hasta[hasta <= HOY].max() if (hasta <= HOY).any() else hasta.max()
        frec = ref["frecuencia"].mode().iloc[0]
        conjunto = ("Commodities" if "Banco Mundial" in fu else "Tipo de cambio real multilateral" if "ITCRM" in fu
                    else "Combustibles" if "Energía (precios en surtidor)" in fu else "Confianza en el Gobierno" if "Escuela de Gobierno" in fu
                    else "Expectativas y confianza UTDT" if "UTDT" in fu else "Series históricas (varias)")
        agregar(conjunto, fu, ult, frec, [C / "series_historicas.parquet"], True, historica=not len(vivas),
                extra={"n_series": len(q), "url": q["url"].dropna().iloc[0] if q["url"].notna().any() else meta.loc[conjunto].get("url") if conjunto in meta.index else None})
    # sin descarga (condiciones de uso): figuran con su estado
    agregar("Dólar futuro (oficial)", "Futuros de dólar (CEM, A3 Mercados)", maximo("dolar_futuro_cem.parquet", "fecha"), "D",
            [C / "dolar_futuro_cem.parquet"], False)
    agregar("Hacienda MAG Cañuelas", "Hacienda (Mercado Agroganadero de Cañuelas)", maximo("mag_precios.parquet", "mes"), "M",
            [C / "mag_precios.parquet"], False)
    agregar("Precios regulados anunciados", "Calendario de precios regulados (notas de prensa)", None, "M",
            [C / "regulados_anunciados.csv"], False, manual=True)
    agregar("SEPA 2016-2018", "Historia SEPA 2016-2018 (Zenodo)", None, "D", [], False, historica=True)
    agregar("SEPA 2024-2026", "Historia SEPA 2024-2026 (preciazo)", None, "D", [], False, historica=True)
    return filas


# ---------------------------------------------------------------- 4) series para descargar
def catalogo_y_series(destino):
    filas = []

    def escribir(id_, nombre, fuente, frec, unidad, url, fechas, valores, origen):
        s = pd.Series(np.asarray(valores, dtype=float), index=pd.to_datetime(fechas)).dropna().sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if not len(s):
            return
        frec = frec_norm(frec)
        hasta = s.index.max()
        hist = (HOY - fin_periodo(hasta, frec)).days > 3 * 365
        col, txt = estado(hasta, frec, hist)
        guardar_json(destino / "s" / f"{id_}.json", {"id": id_, "d": [d.strftime("%Y-%m-%d") for d in s.index], "v": s.round(6).tolist()})
        filas.append({"id": id_, "nombre": nombre, "fuente": fuente, "frecuencia": frec, "unidad": unidad, "url": url,
                      "desde": s.index.min().strftime("%Y-%m-%d"), "hasta": hasta.strftime("%Y-%m-%d"), "n": len(s),
                      "estado": col, "estado_txt": txt, "origen": origen})

    # a) series históricas (catálogo propio, una por id)
    h = pd.read_parquet(C / "series_historicas.parquet")
    cat = pd.read_csv(C / "series_historicas_catalogo.csv", encoding="utf-8-sig").drop_duplicates("id").set_index("id")
    for id_, q in h.groupby("id"):
        m = cat.loc[id_] if id_ in cat.index else {}
        escribir(id_, m.get("nombre", q["nombre"].iloc[0]), m.get("fuente", q["fuente"].iloc[0]), m.get("frecuencia", q["frecuencia"].iloc[0]),
                 m.get("unidad") if isinstance(m.get("unidad"), str) else "", m.get("url"), q["fecha"], q["valor"], "historicas")
    # b) BCRA API (diaria)
    nombres = {"badlar_tna": "Tasa BADLAR bancos privados (TNA, %)", "tamar_tna": "Tasa TAMAR bancos privados (TNA, %)",
               "base_monetaria": "Base monetaria (millones de $)", "billetes_publico": "Billetes y monedas en poder del público (millones de $)",
               "m2_pesos": "M2 en pesos (millones de $)", "plazo_fijo_30d_tna": "Tasa de plazo fijo a 30 días (TNA, %)",
               "tc_mayorista_a3500": "Dólar mayorista Com. A3500 ($ por USD)", "tc_minorista_bna": "Dólar minorista Banco Nación ($ por USD)",
               "politica_monetaria_tna": "Tasa de política monetaria (TNA, %)", "lebac_leliq_1m_tna": "Tasa LEBAC/LELIQ a 1 mes (TNA, %)",
               "pase_pasivo_1d_tna": "Tasa de pases pasivos a 1 día (TNA, %)", "pase_pasivo_7d_tna": "Tasa de pases pasivos a 7 días (TNA, %)"}
    b = pd.read_parquet(C / "bcra_series.parquet")
    for (s, idv), q in b.groupby(["serie", "id_variable"]):
        escribir(f"bcra_api_{s}", nombres.get(s, s), "BCRA API v4.0 (actualización diaria)", "D", "",
                 f"https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/{idv}", q["fecha"], q["valor"], "bcra")
    # c) IPC nacional, IPCBA y serie encadenada
    i = pd.read_parquet(C / "ipc_indec.parquet")
    for col, nom in (("ipc_general_indice", "IPC nacional, nivel general (índice dic-2016=100)"),
                     ("ipc_general_indice_var_mensual", "IPC nacional, nivel general (var. % mensual)"),
                     ("ipc_nucleo_indice", "IPC nacional, núcleo (índice dic-2016=100)"),
                     ("ipc_nucleo_indice_var_mensual", "IPC nacional, núcleo (var. % mensual)")):
        escribir(f"indec_{col}", nom, "INDEC (IPC, directo)", "M", "", "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv",
                 i["mes"], i[col], "ipc")
    c = pd.read_parquet(C / "ipcba.parquet")
    for col, nom in (("ipcba_indice", "IPCBA, nivel general (índice 2021=100)"), ("ipcba_var_mensual", "IPCBA, nivel general (var. % mensual)")):
        escribir(f"idecba_{col}", nom, "IDECBA (Ciudad de Buenos Aires)", "M", "",
                 "https://www.estadisticaciudad.gob.ar/eyc/categoria-banco-datos/series-empalmadas/", c["mes"], c[col], "ipc")
    e = pd.read_parquet(C / "precios_encadenada.parquet")
    escribir("dcm_ipc_encadenado_var", "IPC encadenado: IPCBA hasta dic-2018 e INDEC desde ene-2019 (var. % mensual)",
             "Datos Con Método (elaboración propia sobre IDECBA e INDEC)", "M", "", None, e["mes"], e["var_mensual"], "propia")
    # d) EMAE y PIB (última versión)
    m = pd.read_parquet(C / "emae.parquet")
    for col, nom in (("original", "EMAE, serie original (2004=100)"), ("desest", "EMAE, desestacionalizado (2004=100)"),
                     ("tendencia", "EMAE, tendencia-ciclo (2004=100)")):
        escribir(f"indec_emae_{col}", nom, "INDEC (EMAE)", "M", "", "https://www.indec.gob.ar/ftp/cuadros/economia/sh_emae_mensual_base2004.xls",
                 pd.PeriodIndex(m["mes"], freq="M").to_timestamp(), m[col], "actividad")
    v = pd.read_parquet(C / "pib_desest_versiones.parquet")
    v = v[v["version"].astype(str) == v["version"].astype(str).max()].dropna(subset=["anio", "trim"])
    fechas = [pd.Period(f"{int(a)}Q{int(t)}", "Q").start_time for a, t in zip(v["anio"], v["trim"])]
    for col, nom in (("pib", "PIB"), ("consumo_privado", "Consumo privado"), ("consumo_publico", "Consumo público"),
                     ("fbcf", "Inversión (FBCF)"), ("exportaciones", "Exportaciones (cuentas nacionales)"), ("importaciones", "Importaciones (cuentas nacionales)")):
        escribir(f"indec_pib_desest_{col}", f"{nom}, desestacionalizado (millones de $ de 2004)", "INDEC (cuentas nacionales)", "T", "",
                 "https://www.indec.gob.ar/ftp/cuadros/economia/sh_oferta_demanda_desest_MM_AA.xls", fechas, v[col], "actividad")
    # e) dólares paralelos (venta)
    d = pd.read_parquet(C / "dolares.parquet")
    for s, q in d.groupby("serie"):
        escribir(f"argentinadatos_dolar_{s}", f"Dólar {s.upper() if s != 'blue' else 'blue'} (venta, $ por USD)",
                 "ArgentinaDatos (https://argentinadatos.com)", "D", "", "https://argentinadatos.com", q["fecha"], q["venta"], "dolares")
    # f) índice SEPA propio
    sm = pd.read_parquet(C / "sepa_indice_mensual.parquet")
    for reg, q in sm.groupby("region"):
        escribir(f"dcm_sepa_mensual_{reg.lower().replace(' ', '_')}", f"Índice de supermercados (SEPA), {reg}: var. % mensual",
                 "Datos Con Método (elaboración propia sobre SEPA, Secretaría de Comercio)", "M", "", None,
                 pd.PeriodIndex(q["mes"], freq="M").to_timestamp(), q["variacion_pct"], "propia")
    sd = pd.read_parquet(C / "sepa_indice_diario.parquet")
    sd = sd[(sd["nivel"] == "total")]
    for reg, q in sd.groupby("region"):
        escribir(f"dcm_sepa_diario_{reg.lower().replace(' ', '_')}", f"Índice de supermercados (SEPA), {reg}: nivel diario",
                 "Datos Con Método (elaboración propia sobre SEPA, Secretaría de Comercio)", "D", "", None, q["fecha"], q["indice"], "propia")
    cat = pd.DataFrame(filas).sort_values(["fuente", "nombre"]).reset_index(drop=True)
    guardar_json(destino / "catalogo.json", cat.to_dict("records"))
    return cat


# ---------------------------------------------------------------- 5) tablas completas (.xlsx)
def tablas(destino):
    destino.mkdir(parents=True, exist_ok=True)
    hechas = []
    with pd.ExcelWriter(destino / "nuestros_pronosticos.xlsx") as w:
        for nombre, hoja in (("inflacion", "Inflación (I)"), ("pronosticos", "Inflación REM (A y B)"), ("tc", "Dólar"), ("pib", "PIB"),
                             ("tasa", "Tasa"), ("fiscal", "Resultado primario"), ("comercio", "Comercio exterior"),
                             ("alertas", "Semáforo"), ("termometro", "Termómetro súper")):
            p = C / f"registro_{nombre}.csv"
            if p.exists():
                pd.read_csv(p, encoding="utf-8-sig").to_excel(w, sheet_name=hoja[:31], index=False)
    hechas.append({"archivo": "nuestros_pronosticos.xlsx", "titulo": "Nuestros pronósticos registrados",
                   "desc": "Cada corrida diaria desde el 15-oct-2026, con fecha y hora: la prueba hacia adelante. Nunca se borra."})
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    cols = [c for c in ["mes_relevamiento", "variable_std", "unidad", "tipo_periodo", "periodo_original", "periodo_fin", "horizonte_meses",
                        "mediana", "promedio", "percentil_10", "percentil_25", "percentil_75", "percentil_90", "cantidad_de_participantes",
                        "correccion_aplicada"] if c in r.columns]
    r[cols].to_excel(destino / "rem_bcra_normalizado.xlsx", index=False)
    hechas.append({"archivo": "rem_bcra_normalizado.xlsx", "titulo": "REM del BCRA, en formato largo",
                   "desc": "Todas las variables y horizontes del Relevamiento de Expectativas de Mercado desde 2016, normalizados. Fuente: BCRA."})
    l = pd.read_parquet(C / "licitaciones_tesoro.parquet").drop(columns=["archivo", "hoja"], errors="ignore")
    l.to_excel(destino / "licitaciones_tesoro.xlsx", index=False)
    hechas.append({"archivo": "licitaciones_tesoro.xlsx", "titulo": "Licitaciones del Tesoro",
                   "desc": "Colocaciones de deuda desde 2020 (Oficina Nacional de Crédito Público) y la tasa de corte calculada para las letras a tasa fija."})
    pd.read_csv(C / "metas_fiscales.csv").to_excel(destino / "metas_fiscales.xlsx", index=False)
    hechas.append({"archivo": "metas_fiscales.xlsx", "titulo": "Metas fiscales 2016-2027",
                   "desc": "Meta de resultado primario vigente en cada fecha (Presupuestos, FMI, anuncios), con la fuente de cada dato."})
    pd.read_parquet(C / "estimaciones_agricolas.parquet").drop(columns=["archivo"], errors="ignore").to_excel(destino / "estimaciones_agricolas.xlsx", index=False)
    hechas.append({"archivo": "estimaciones_agricolas.xlsx", "titulo": "Estimaciones agrícolas, informe por informe",
                   "desc": "Superficie y producción estimada de trigo, maíz y soja según cada informe mensual del Ministerio de Agricultura (desde 2018)."})
    with pd.ExcelWriter(destino / "historial_contra_el_rem.xlsx") as w:
        for f, hoja, cols in (("replica_rem", "Inflación", ["origen", "H", "real", "rem", "I"]),
                              ("replica_tc", "Dólar", ["origen", "h", "obj", "real", "rem", "A_dolar"]),
                              ("replica_pib", "PIB", ["origen", "Q", "k", "real", "rem", "I_pib"]),
                              ("replica_tasa", "Tasa", ["origen", "mes", "h", "serie", "real", "rem", "A"]),
                              ("replica_fiscal", "Resultado primario", ["origen", "anio", "pos", "real", "rem", "I", "real_pib", "rem_pib", "I_pib"]),
                              ("replica_comercio", "Comercio exterior", ["var", "tipo", "origen", "objetivo", "h", "real", "rem", "A"])):
            d = pd.read_csv(C / f"{f}.csv", encoding="utf-8-sig")
            d[[c for c in cols if c in d.columns]].to_excel(w, sheet_name=hoja, index=False)
    hechas.append({"archivo": "historial_contra_el_rem.xlsx", "titulo": "Historial: nuestro, REM y lo que pasó",
                   "desc": "Antes de oct-2026 son reconstrucciones en tiempo real (solo con datos publicados hasta cada fecha), no pronósticos publicados entonces."})
    for x in hechas:
        x["peso_kb"] = round((destino / x["archivo"]).stat().st_size / 1024)
    return hechas


# ---------------------------------------------------------------- 6) armar y publicar
def main():
    t0 = time.time()
    aviso = "--aviso" in sys.argv
    L = [f"=== SITIO WEB 'Datos Con Método' | {datetime.now():%Y-%m-%d %H:%M} ==="]
    alertas = []
    if NUEVO.exists():
        shutil.rmtree(NUEVO)
    shutil.copytree(PLANTILLA, NUEVO)
    data = NUEVO / "data"
    cat = catalogo_y_series(data)
    sem, _ = ultimo_registro("alertas")
    ter, _ = ultimo_registro("termometro")
    resumen = {"generado": datetime.now().strftime("%Y-%m-%d %H:%M"), "rem_ultimo": str(rem_ultimo()),
               "semaforo": sem.iloc[0][["semaforo", "senales_en_alerta", "cuales", "relevamiento", "fecha_corrida"]].to_dict() if len(sem) else None,
               "termometro": ter.iloc[0][["termometro_pct", "A_m0", "relevamiento", "fecha_corrida"]].to_dict() if len(ter) else None,
               "pronosticos": pronosticos(), "historial": historial(),
               "conteo": {"series": len(cat), "datos": int(cat["n"].sum()), "fuentes": cat["fuente"].nunique()}}
    resumen["tablas"] = tablas(NUEVO / "descargas")
    guardar_json(data / "resumen.json", resumen)
    fu = fuentes(cat)
    guardar_json(data / "fuentes.json", fu)
    # publicar: reemplazo de la carpeta "actual" (si la VM la tiene abierta y no se puede renombrar, se copia encima)
    viejo = WEB / "_viejo"
    if viejo.exists():
        shutil.rmtree(viejo, ignore_errors=True)
    try:
        if ACTUAL.exists():
            ACTUAL.rename(viejo)
        NUEVO.rename(ACTUAL)
        shutil.rmtree(viejo, ignore_errors=True)
    except OSError as e:
        alertas.append(f"W01 no se pudo reemplazar la carpeta de una vez ({type(e).__name__}); se copió encima")
        shutil.copytree(NUEVO, ACTUAL, dirs_exist_ok=True)
        nuevos = {p.relative_to(NUEVO) for p in NUEVO.rglob("*") if p.is_file()}
        for p in [p for p in ACTUAL.rglob("*") if p.is_file() and p.relative_to(ACTUAL) not in nuevos]:
            p.unlink(missing_ok=True)                          # archivos que ya no corresponden (series que salieron)
        shutil.rmtree(NUEVO, ignore_errors=True)
    peso = sum(p.stat().st_size for p in ACTUAL.rglob("*") if p.is_file()) / 1e6
    est = pd.Series([f["estado"] for f in fu]).value_counts().to_dict()
    L += [f"Sitio publicado en {ACTUAL} ({peso:.0f} MB, {time.time() - t0:.0f} s)",
          f"Series para descargar: {len(cat):,} ({int(cat['n'].sum()):,} datos)",
          f"Fuentes: {len(fu)} (verde {est.get('verde', 0)}, amarillo {est.get('amarillo', 0)}, rojo {est.get('rojo', 0)}, gris {est.get('gris', 0)})",
          "Fuentes atrasadas: " + (", ".join(f["nombre"] for f in fu if f["estado"] == "rojo") or "ninguna")]
    L.append("ALERTAS: " + ("; ".join(alertas) if alertas else "ninguna. Todo en orden."))
    texto = "\n".join(L)
    print(texto)
    (C / "informe_web.txt").write_text(texto, encoding="utf-8")
    sys.exit(1 if alertas else 0)


if __name__ == "__main__":
    main()
