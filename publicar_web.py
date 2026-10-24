# publicar_web.py
# Arma el sitio estático "Datos Con Método" en rem\web\actual\ a partir de lo que ya está en datos\.
#   - Plantilla (HTML, CSS, JS, librerías) en rem\sitio\ (no se toca).
#   - Datos para la página en web\actual\data\: resumen.json (pronósticos de hoy, semáforo, historial contra el REM),
#     fuentes.json (estado de cada fuente), catalogo.json + s\<id>.json (una serie por archivo, para descargar en .xlsx).
#   - Tablas completas en web\actual\descargas\*.xlsx (nuestros pronósticos registrados, historial, REM, licitaciones...).
# Se arma primero en web\_nuevo y recién al final reemplaza a web\actual (el sitio nunca se ve a medio actualizar).
# La máquina virtual (VirtualBox, Apache) comparte SOLO la carpeta rem\web, en modo lectura; en Ubuntu el sitio
# www.datosconmetodo.com tiene DocumentRoot /media/sf_web/actual. Datos propios: CC BY 4.0 (Marcos Cavanagh).
# NO se publican: scripts, CLAUDE.md, datos\copias, Precios del Central (se bajó con usuario) ni los regulados de prensa.
# Futuros del CEM, MAG y Mercado Central se publican con su aclaración (decisión de Marcos, 23-oct-2026).
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
        et = lambda x: (f"Fin de {x['objetivo'][:4]} (dic)" if x["h"] in (100, 101) else "Próx. 12 meses" if x["h"] == 12 else x["objetivo"])
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
    # orden de las tarjetas en el sitio (Marcos, 24-oct-2026: importaciones donde estaba el resultado primario, y viceversa)
    orden = ["inflacion", "dolar", "pib", "tasa", "importaciones", "exportaciones", "fiscal"]
    return sorted(tarjetas, key=lambda t: orden.index(t["clave"]) if t["clave"] in orden else 99)


# ---------------------------------------------------------------- 1b) los datos de hoy (último valor de las variables clave)
def indicadores():
    out = []

    def agregar(clave, titulo, valor, unidad, fecha, detalle, fuente, dec=2):
        if valor is None or pd.isna(valor):
            return
        out.append({"clave": clave, "titulo": titulo, "valor": float(valor), "unidad": unidad, "fecha": fecha, "detalle": detalle,
                    "fuente": fuente, "dec": dec})

    try:                                                    # inflación (INDEC)
        i = pd.read_parquet(C / "ipc_indec.parquet").sort_values("mes")
        u = i.iloc[-1]
        agregar("inflacion", "Inflación", u["ipc_general_indice_var_mensual"], "% mensual", str(u["mes"])[:7],
                f"Núcleo {u['ipc_nucleo_indice_var_mensual']:.1f}% · interanual {u['ipc_general_indice_var_ia']:.1f}%".replace(".", ","), "INDEC", 1)
    except Exception:
        pass
    try:                                                    # dólar mayorista y TAMAR (BCRA)
        b = pd.read_parquet(C / "bcra_series.parquet")
        b["fecha"] = pd.to_datetime(b["fecha"])
        tc = b[b["serie"] == "tc_mayorista_a3500"].sort_values("fecha")
        u = tc.iloc[-1]
        ant = tc[tc["fecha"] < u["fecha"].replace(day=1)]
        var = (u["valor"] / ant["valor"].iloc[-1] - 1) * 100 if len(ant) else None
        agregar("dolar", "Dólar mayorista (A3500)", u["valor"], "$ por USD", u["fecha"].strftime("%Y-%m-%d"),
                (f"{var:+.1f}% en el mes".replace(".", ",") if var is not None else ""), "BCRA", 2)
        ta = b[b["serie"] == "tamar_tna"].sort_values("fecha").iloc[-1]
        agregar("tamar", "Tasa TAMAR", ta["valor"], "TNA %", ta["fecha"].strftime("%Y-%m-%d"), "Plazos fijos de $1.000 millones o más", "BCRA", 1)
        d = pd.read_parquet(C / "dolares.parquet")
        d["fecha"] = pd.to_datetime(d["fecha"])
        for serie, clave, titulo in (("ccl", "ccl", "Dólar CCL"), ("mep", "mep", "Dólar MEP")):
            q = d[d["serie"] == serie].sort_values("fecha")
            if len(q):
                uc = q.iloc[-1]
                of = tc[tc["fecha"] <= uc["fecha"]].iloc[-1]["valor"]
                agregar(clave, titulo, uc["venta"], "$ por USD", uc["fecha"].strftime("%Y-%m-%d"),
                        f"Brecha con el oficial {(uc['venta'] / of - 1) * 100:.1f}%".replace(".", ","), "ArgentinaDatos", 2)
    except Exception:
        pass
    try:                                                    # PIB (último trimestre publicado) y EMAE
        v = pd.read_parquet(C / "pib_desest_versiones.parquet")
        v = v[v["version"].astype(str) == v["version"].astype(str).max()].dropna(subset=["pib"]).sort_values(["anio", "trim"])
        g = (v["pib"].iloc[-1] / v["pib"].iloc[-2] - 1) * 100
        e = pd.read_parquet(C / "emae.parquet").sort_values("mes")
        em = (e["desest"].iloc[-1] / e["desest"].iloc[-2] - 1) * 100
        agregar("pib", "PIB trimestral", g, "% trimestral s.e.", f"{int(v['anio'].iloc[-1])}T{int(v['trim'].iloc[-1])}",
                f"EMAE {MESES_LARGOS[int(str(e['mes'].iloc[-1])[5:7]) - 1][:3]}-{str(e['mes'].iloc[-1])[:4]}: "
                + f"{em:+.1f}".replace(".", ",") + "% mensual s.e.", "INDEC", 2)
    except Exception:
        pass
    try:                                                    # súper (índice SEPA propio, mes en curso)
        t, _ = ultimo_registro("termometro")
        if len(t):
            agregar("super", "Precios en supermercados", t.iloc[0]["termometro_pct"], "% en el mes", str(t.iloc[0]["relevamiento"]),
                    "Lo que va del mes contra el mes anterior (SEPA)", "Datos Con Método sobre SEPA", 2)
    except Exception:
        pass
    try:                                                    # resultado primario acumulado del año (IMIG)
        imig = sorted((C / "copias" / "imig").glob("imig_*.csv"), key=lambda p: p.stat().st_mtime)[-1]
        m = pd.read_csv(imig)
        m["mes"] = pd.to_datetime(m["indice_tiempo"])
        a = m["mes"].max().year
        acum = m.loc[m["mes"].dt.year == a, "resultado_primario"].sum() / 1e6
        agregar("fiscal", "Resultado primario", acum, "billones de $", m["mes"].max().strftime("%Y-%m"),
                f"Acumulado ene-{MESES_LARGOS[m['mes'].max().month - 1][:3]} {a}", "Secretaría de Hacienda", 1)
    except Exception:
        pass
    orden = ["inflacion", "dolar", "ccl", "mep", "pib", "super", "fiscal", "tamar"]
    return sorted(out, key=lambda x: orden.index(x["clave"]) if x["clave"] in orden else 99)


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


# ---------------------------------------------------------------- textos de aclaración (se muestran en Fuentes)
ALERTAS_GLOBALES = []
MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
NOTA_MAG = ("Datos públicos del Mercado Agroganadero de Cañuelas, publicados en su sitio y gestionado por la empresa "
            "Mercado Agroganadero S.A. Publicamos el precio promedio mensual por categoría ($ por kg vivo) armado con sus "
            "datos. Si el Mercado lo solicita, retiramos la serie.")
NOTA_MERCADO_CENTRAL = ("Precios diarios publicados por la Corporación del Mercado Central de Buenos Aires (CMCBA). "
                        "Faltan ene-2022 a dic-2025: el Mercado Central no tiene publicadas esas planillas en su sitio. "
                        "Las solicitamos y a la fecha no recibimos respuesta. Parte de 2017-2022 proviene de copias del "
                        "archivo de internet (web.archive.org) de las planillas oficiales que ya no están en su sitio.")
NOTA_SEPA = ("El portal oficial guarda solo los últimos 7 días. La historia está repartida en varios archivos (ver "
             "'Precios de supermercados: datos crudos' en Descargas). Pedimos la historia completa a la Secretaría de "
             "Comercio (14-oct-2026) y a fuentes académicas que pueden tener el archivo completo; a la fecha no recibimos respuesta.")


def fecha_larga(f):
    t = pd.Timestamp(f)
    return f"{t.day} de {MESES_LARGOS[t.month - 1]} de {t.year}"


def nota_a3():
    """Aclaración de los futuros del CEM + verificación mensual de si A3 publicó términos de uso (aviso W02 si aparecen).
    Registro de cada verificación en datos\\verificacion_terminos_a3.csv."""
    import re
    import urllib.request
    reg = C / "verificacion_terminos_a3.csv"
    hist = pd.read_csv(reg, encoding="utf-8-sig") if reg.exists() else pd.DataFrame(columns=["fecha", "terminos_encontrados", "detalle"])
    ultima = pd.Timestamp(hist["fecha"].max()) if len(hist) else None
    if ultima is None or (HOY - ultima.normalize()).days >= 30:
        hallados, errores = [], []
        for url in ("https://www.a3mercados.com.ar", "https://cem.matbarofex.com.ar"):
            try:
                html = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode("utf-8", "ignore")
                hallados += re.findall(r'href="([^"]*(?:termino|término|condiciones|aviso-legal|licencia|uso-de-datos|market-data-policy)[^"]*)"', html, re.I)
            except Exception as e:
                errores.append(f"{url}: {type(e).__name__}")
        fila = {"fecha": HOY.strftime("%Y-%m-%d"), "terminos_encontrados": bool(hallados),
                "detalle": (" | ".join(hallados + [f"ERROR {x}" for x in errores]))[:500]}
        hist = pd.concat([hist, pd.DataFrame([fila])], ignore_index=True)
        hist.to_csv(reg, index=False, encoding="utf-8-sig")
        if hallados:
            ALERTAS_GLOBALES.append("W02 A3/CEM publicó algo parecido a términos de uso: revisar antes de seguir publicando los futuros "
                                    f"({fila['detalle'][:200]})")
    u = hist.iloc[-1]
    return ("A3 Mercados (ex Matba Rofex) publica estos datos en su API pública, sin registro. "
            f"Al {fecha_larga(u['fecha'])} no existen términos de uso ni licencia publicados por A3 para estos datos. "
            "El 15-oct-2026 le pedimos a A3 permiso para republicarlos y a la fecha no recibimos respuesta. "
            "Los publicamos citando la fuente; si A3 lo solicita, los retiramos.")


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


# Cada fuente agrupa sus series (las del catálogo con esa "fuente") y sus tablas completas. Datos de cada fuente:
#   conjunto = fila de datos\fuentes.csv (condiciones), institucion (para la cita), acceso (cómo se cita el acceso:
#   nombre de la API; si no hay, la URL de cada serie), landing (página de la fuente, nunca un archivo).
INDEC = "Instituto Nacional de Estadística y Censos (INDEC)"
BCRA = "Banco Central de la República Argentina"
INFO_FUENTES = {
    "BCRA API v4.0": dict(conjunto="Series BCRA (API)", institucion=BCRA, nombre="Banco Central: API Principales Variables v4.0",
                          acceso="API Principales Variables v4.0", landing="https://www.bcra.gob.ar",
                          archivos=["bcra_series.parquet", "series_historicas.parquet"]),
    "BCRA (archivo ITCRM)": dict(conjunto="Tipo de cambio real multilateral", institucion=BCRA,
                                 nombre="Banco Central: tipo de cambio real multilateral (ITCRM)", landing="https://www.bcra.gob.ar"),
    "Banco Mundial (Pink Sheet)": dict(conjunto="Commodities", institucion="Banco Mundial",
                                       nombre="Banco Mundial: precios internacionales de commodities (Pink Sheet)",
                                       acceso="Commodity Price Data (Pink Sheet)", landing="https://www.worldbank.org/en/research/commodity-markets"),
    "INDEC": dict(conjunto="IPC INDEC", institucion=INDEC, nombre="INDEC: IPC, EMAE, PIB, precios mayoristas, salarios y más",
                  landing="https://www.indec.gob.ar",
                  archivos=["ipc_indec.parquet", "emae.parquet", "pib_desest_versiones.parquet", "series_historicas.parquet"]),
    "IDECBA (Ciudad de Buenos Aires)": dict(conjunto="IPCBA", institucion="Instituto de Estadística y Censos de la Ciudad de Buenos Aires (IDECBA)",
                                            nombre="Ciudad de Buenos Aires: IPCBA (IDECBA)", landing="https://www.estadisticaciudad.gob.ar",
                                            archivos=["ipcba.parquet"]),
    "Secretaría de Energía (precios en surtidor)": dict(conjunto="Combustibles", institucion="Secretaría de Energía",
                                                        nombre="Secretaría de Energía: precios de combustibles en surtidor (mediana mensual, elaboración propia)",
                                                        landing="https://datos.gob.ar/dataset/energia-precios-surtidor---resolucion-3142016"),
    "UTDT (Centro de Investigación en Finanzas)": dict(conjunto="Expectativas y confianza UTDT",
                                                       institucion="Universidad Torcuato Di Tella, Centro de Investigación en Finanzas",
                                                       nombre="UTDT: expectativas de inflación y confianza del consumidor", landing="https://www.utdt.edu/cif"),
    "UTDT Escuela de Gobierno (vía argentinadatos.com)": dict(conjunto="Confianza en el Gobierno", institucion="Universidad Torcuato Di Tella, Escuela de Gobierno",
                                                              nombre="UTDT: índice de confianza en el gobierno (vía ArgentinaDatos)", landing="https://www.utdt.edu"),
    "ArgentinaDatos (https://argentinadatos.com)": dict(conjunto="Dólares paralelos", institucion="ArgentinaDatos", nombre="Dólar CCL, MEP y blue (ArgentinaDatos)",
                                                        landing="https://argentinadatos.com", archivos=["dolares.parquet"]),
    "Datos Con Método (elaboración propia sobre IDECBA e INDEC)": dict(conjunto=None, institucion="Datos Con Método",
                                                                        nombre="Datos Con Método: IPC encadenado (IPCBA hasta 2018 + INDEC)",
                                                                        acceso="www.datosconmetodo.com", landing=None, archivos=["precios_encadenada.parquet"]),
    "Datos Con Método (elaboración propia sobre SEPA, Secretaría de Comercio)": dict(
        conjunto="SEPA diario", institucion="Datos Con Método", nombre="Precios de supermercados (SEPA): índice propio y canasta del INDEC",
        acceso="www.datosconmetodo.com", landing="https://datos.produccion.gob.ar/dataset/sepa-precios",
        archivos=["sepa_indice_diario.parquet"], nota="NOTA_SEPA", extra=["canasta", "sepa_crudos"]),
    "Mercado Agroganadero de Cañuelas (Mercado Agroganadero S.A.)": dict(conjunto="Hacienda MAG Cañuelas", institucion="Mercado Agroganadero S.A.",
                                                                          nombre="Hacienda: Mercado Agroganadero de Cañuelas",
                                                                          landing="https://www.mercadoagroganadero.com.ar", archivos=["mag_precios.parquet"],
                                                                          nota="NOTA_MAG"),
    "Corporación del Mercado Central de Buenos Aires (CMCBA)": dict(conjunto="Mercado Central (precios mayoristas)",
                                                                     institucion="Corporación del Mercado Central de Buenos Aires",
                                                                     nombre="Mercado Central: frutas y hortalizas", landing="https://mercadocentral.gob.ar",
                                                                     archivos=["mercado_central.parquet"], nota="NOTA_MERCADO_CENTRAL"),
    "REM": dict(conjunto="REM BCRA", institucion=BCRA, nombre="Banco Central: Relevamiento de Expectativas de Mercado (REM)",
                landing="https://www.bcra.gob.ar", acceso="Relevamiento de Expectativas de Mercado (REM)",
                archivos=["rem_normalizada.parquet"]),
    "LICITACIONES": dict(conjunto="Colocaciones de deuda del Tesoro (licitaciones)", institucion="Ministerio de Economía, Oficina Nacional de Crédito Público",
                         nombre="Licitaciones del Tesoro (colocaciones de deuda)",
                         landing="https://www.argentina.gob.ar/economia/finanzas/deudapublica/colocacionesdedeuda",
                         acceso="Colocaciones de deuda", archivos=["licitaciones_tesoro.parquet"]),
    "AGRO": dict(conjunto="Estimaciones agrícolas (informes mensuales)", institucion="Secretaría de Agricultura, Ganadería y Pesca",
                 nombre="Estimaciones agrícolas (trigo, maíz, soja)", landing="https://www.magyp.gob.ar/sitio/areas/estimaciones/",
                 acceso="Estimaciones Agrícolas, informe mensual", archivos=["estimaciones_agricolas.parquet"]),
    "CEM": dict(conjunto="Dólar futuro (oficial)", institucion="A3 Mercados, Centro de Estadísticas de Mercado (CEM)", nombre="Futuros de dólar (CEM, A3 Mercados)",
                landing="https://cem.matbarofex.com.ar", acceso="API del Centro de Estadísticas de Mercado", archivos=["dolar_futuro_cem.parquet"],
                nota="NOTA_A3", licencia="Sin licencia publicada por el productor"),
}
# fuentes que solo tienen tablas (o nada para descargar)
SOLO_TABLAS = {
    "METAS": dict(conjunto="Metas fiscales (armado propio)", institucion="Datos Con Método", nombre="Metas fiscales 2016-2027 (Presupuesto y FMI, armado propio)",
                  landing=None, frec="A", archivos=["metas_fiscales.csv"], manual=True),
    "DCM": dict(conjunto=None, institucion="Datos Con Método", nombre="Datos Con Método: nuestros pronósticos y su historial", landing=None,
                frec="D", archivos=["registro_inflacion.csv"], ultimo_csv=("registro_inflacion.csv", "fecha_corrida")),
    "REGULADOS": dict(conjunto="Precios regulados anunciados", institucion="Entes reguladores y empresas (notas de prensa)",
                      nombre="Calendario de precios regulados (notas de prensa)", landing=None, frec="M", archivos=["regulados_anunciados.csv"],
                      manual=True, sin_descarga=True),
}
# a qué fuente va cada tabla completa
TABLA_FUENTE = {"comparacion_pronosticos": "DCM", "registro_corridas": "DCM", "rem_bcra_normalizado": "REM", "licitaciones_tesoro": "LICITACIONES",
                "metas_fiscales": "METAS", "estimaciones_agricolas": "AGRO", "futuros_dolar_cem": "CEM",
                "mercado_central_diario": "Corporación del Mercado Central de Buenos Aires (CMCBA)",
                "hacienda_mag_canuelas": "Mercado Agroganadero de Cañuelas (Mercado Agroganadero S.A.)", "ponderaciones_ipc": "INDEC"}


def fecha_ar(f):
    return pd.Timestamp(f).strftime("%d/%m/%Y") if f else None


def fuentes(cat, fichas, canasta, crudos):
    """Lista unificada de fuentes; agrega a cada serie de `cat` las columnas fuente_id y cita. Devuelve (fuentes, cat)."""
    meta = pd.read_csv(C / "fuentes.csv", encoding="utf-8-sig").set_index("conjunto")
    notas = {"NOTA_SEPA": NOTA_SEPA, "NOTA_MAG": NOTA_MAG, "NOTA_MERCADO_CENTRAL": NOTA_MERCADO_CENTRAL}
    filas = []
    cat = cat.copy()
    cat["fuente_id"] = cat["fuente"]
    cat["cita"] = None

    def ficha_base(clave, info, ultimo, ultimo_tip, frec, n_series, historica=False, estados=None):
        m = dict(meta.loc[info["conjunto"]]) if info.get("conjunto") in meta.index else {}
        if ultimo is not None and not pd.isna(ultimo) and pd.Timestamp(ultimo) > HOY:
            ultimo = HOY
        col, txt = estado(ultimo_tip if ultimo_tip is not None and not pd.isna(ultimo_tip) else ultimo, frec, historica)
        if estados is not None and len(estados):          # estado típico de sus series vigentes (cada una con su frecuencia)
            rango = {"verde": 0, "amarillo": 1, "rojo": 2}
            r = int(pd.Series([rango[e] for e in estados if e in rango]).median().round()) if any(e in rango for e in estados) else None
            if r is not None:
                col = ["verde", "amarillo", "rojo"][r]
                txt = {"verde": "Al día", "amarillo": "Algo atrasada", "rojo": "Atrasada"}[col]
        if info.get("manual"):
            col, txt = "gris", "Se actualiza a mano"
        actualizado = mtime(*[C / a for a in info.get("archivos", ["series_historicas.parquet"])])
        nota = info.get("nota")
        nota = nota_a3() if nota == "NOTA_A3" else notas.get(nota, nota)
        inst = info["institucion"]
        acceso = info.get("acceso") or "<dirección de la serie>"
        tipica = None
        if ultimo_tip is not None and not pd.isna(ultimo_tip) and ultimo is not None and not pd.isna(ultimo):
            if pd.Timestamp(ultimo_tip) < pd.Timestamp(ultimo) - pd.Timedelta(days=20):
                tipica = pd.Timestamp(ultimo_tip).strftime("%Y-%m-%d")
        return {"id": clave, "nombre": info["nombre"], "institucion": inst,
                "via": m.get("intermediario") if isinstance(m.get("intermediario"), str) else None,
                "url": info.get("landing"), "condiciones": m.get("condiciones_uso"), "licencia": info.get("licencia"), "nota": nota,
                "ultimo_dato": ultimo, "dato_tipico": tipica, "frecuencia": frec, "actualizado": actualizado, "estado": col,
                "estado_txt": txt, "n_series": n_series, "descarga": not info.get("sin_descarga"), "tablas": [], "extra": {},
                "cita": f"{inst}, <nombre de la serie>, {acceso}, consultado el {fecha_ar(actualizado[:10]) if actualizado else '<fecha>'}"}

    for clave, q in cat.groupby("fuente"):
        if clave.startswith("datos.gob.ar ("):
            inst = clave[len("datos.gob.ar ("):-1]
            info = dict(conjunto="Series históricas (varias)", institucion=inst, nombre=f"{inst} (vía datos.gob.ar)", landing="https://datos.gob.ar")
        else:
            info = INFO_FUENTES.get(clave, dict(conjunto=None, institucion=clave, nombre=clave, landing=None))
        vivas = q[q["estado"] != "gris"]
        ref = vivas if len(vivas) else q
        hasta = pd.to_datetime(ref["hasta"])
        ok = hasta[hasta <= HOY]
        ult = ok.max() if len(ok) else hasta.max()
        tip = ok.median() if len(ok) else ult
        f = ficha_base(clave, info, ult, tip, ref["frecuencia"].mode().iloc[0], len(q), historica=not len(vivas),
                       estados=vivas["estado"].tolist() if len(vivas) else None)
        if clave.startswith("datos.gob.ar ("):
            f["via"] = "datos.gob.ar (Subsecretaría de Programación Macroeconómica)"
        for e in info.get("extra", []):
            f["extra"][e] = canasta if e == "canasta" else crudos
        consultado = fecha_ar(f["actualizado"][:10]) if f["actualizado"] else "<fecha>"
        acceso = info.get("acceso")
        cat.loc[q.index, "cita"] = [f"{f['institucion']}, {n}, {acceso or (u if isinstance(u, str) and u else 'www.datosconmetodo.com')}, consultado el {consultado}"
                                    for n, u in zip(q["nombre"], q["url"])]
        filas.append(f)
    for clave, info in SOLO_TABLAS.items():
        ult = maximo(*info["ultimo"]) if info.get("ultimo") else None
        if info.get("ultimo_csv") and (C / info["ultimo_csv"][0]).exists():
            ult = pd.Timestamp(str(pd.read_csv(C / info["ultimo_csv"][0], encoding="utf-8-sig")[info["ultimo_csv"][1]].max())[:10])
        filas.append(ficha_base(clave, info, ult, None, info["frec"], 0))
    por_id = {f["id"]: f for f in filas}
    for t in fichas:
        destino = TABLA_FUENTE.get(t["base"])
        if destino in por_id:
            por_id[destino]["tablas"].append(t)
    filas.sort(key=lambda f: (f["institucion"] != "Datos Con Método", -(f["n_series"] + 50 * len(f["tablas"])), f["nombre"]))
    return filas, cat


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
    # g) hacienda MAG Cañuelas: precio promedio mensual por categoría ($/kg vivo), mes completo
    mg = pd.read_parquet(C / "mag_precios.parquet")
    mg = mg[mg["tramo"] == "mes"]
    for (cat_, sub), q in mg.groupby(["categoria", "subcategoria"]):
        id_ = "".join(ch if ch.isalnum() else "_" for ch in f"mag_{cat_}_{sub}".lower())
        escribir(id_, f"Hacienda, {cat_.title()} {sub}: precio promedio ($ por kg vivo)",
                 "Mercado Agroganadero de Cañuelas (Mercado Agroganadero S.A.)", "M", "$ por kg vivo", "https://www.mercadoagroganadero.com.ar/dll/hacienda1.dll/haciinfo000002",
                 pd.PeriodIndex(q["mes"], freq="M").to_timestamp(), q["promedio"], "mag")
    # h) Mercado Central: precio modal promedio del mes por especie ($ por kg), con los días que publicó el Mercado
    mc = pd.read_parquet(C / "mercado_central.parquet", columns=["fecha", "tipo", "especie", "modal_kg", "promedio_especie"])
    mc = mc[mc["promedio_especie"].fillna(False).astype(bool) & (mc["modal_kg"] > 0)]
    mc["mes"] = pd.to_datetime(mc["fecha"]).dt.to_period("M")
    for (tipo, esp), q in mc.groupby(["tipo", "especie"]):
        m_ = q.groupby("mes")["modal_kg"].mean()
        if len(m_) < 6:
            continue
        id_ = "mcba_" + "".join(ch if ch.isalnum() else "_" for ch in f"{tipo}_{esp}".lower())
        escribir(id_, f"Mercado Central, {str(esp).title()} ({'hortaliza' if tipo == 'H' else 'fruta'}): precio modal promedio ($ por kg)",
                 "Corporación del Mercado Central de Buenos Aires (CMCBA)", "M", "$ por kg", "https://mercadocentral.gob.ar/informaci%C3%B3n/precios-mayoristas",
                 m_.index.to_timestamp(), m_.values, "mercado_central")
    # i) REM del BCRA: una serie por variable y horizonte (mediana de cada relevamiento, fecha = mes del relevamiento)
    VAR_REM = {"ipc_general": "inflación (IPC general)", "ipc_nucleo": "inflación núcleo", "tipo_cambio": "dólar mayorista",
               "pib": "PIB", "desocupacion": "desocupación", "exportaciones": "exportaciones", "importaciones": "importaciones",
               "resultado_primario": "resultado primario del SPN", "tasa_tamar": "tasa TAMAR", "tasa_badlar": "tasa BADLAR",
               "tasa_leliq": "tasa LELIQ", "politica_lebac": "tasa de política (LEBAC)", "politica_leliq": "tasa de política (LELIQ)",
               "politica_pase_7_dias": "tasa de política (pases a 7 días)"}
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    r["o"] = pd.to_datetime(r["mes_relevamiento"]).dt.to_period("M")
    r["fecha_rel"] = r["o"].dt.to_timestamp()
    partes = []
    f_ = r[r["tipo_periodo"] == "fecha"].copy()
    f_["h"] = [(m - o).n for m, o in zip(pd.to_datetime(f_["periodo_fin"]).dt.to_period("M"), f_["o"])]
    f_ = f_[f_["h"].between(0, 6)]
    f_["hor"] = ["mes del relevamiento" if h == 0 else f"a {h} {'mes' if h == 1 else 'meses'}" for h in f_["h"]]
    f_["clave"] = "m" + f_["h"].astype(str)
    partes.append(f_)
    a_ = r[r["tipo_periodo"] == "anio"].copy()
    a_["pos"] = pd.to_numeric(a_["periodo_original"], errors="coerce") - a_["o"].dt.year
    a_ = a_.dropna(subset=["pos"])
    a_["hor"] = a_["pos"].map({0: "año en curso", 1: "año siguiente", 2: "dentro de 2 años", 3: "dentro de 3 años"})
    a_["clave"] = "a" + a_["pos"].astype(int).astype(str)
    partes.append(a_.dropna(subset=["hor"]))
    p_ = r[r["tipo_periodo"] == "proximos_meses"].copy()
    p_["hor"] = p_["periodo_original"].str.lower().str.replace("próx.", "próximos", regex=False)
    p_["clave"] = "p" + p_["periodo_original"].str.extract(r"(\d+)")[0]
    partes.append(p_)
    t_ = r[r["tipo_periodo"] == "trimestre"].copy()

    def trim(sv):
        import re
        m = re.search(r"([IV]+)-(\d\d)", str(sv))
        return pd.Period(f"20{m.group(2)}Q{ {'I': 1, 'II': 2, 'III': 3, 'IV': 4}[m.group(1)] }", "Q") if m else None
    t_["q"] = t_["periodo_original"].map(trim)
    t_ = t_.dropna(subset=["q"])
    t_["k"] = [(q - o.asfreq("Q")).n for q, o in zip(t_["q"], t_["o"])]
    t_["hor"] = t_["k"].map({-1: "trimestre anterior", 0: "trimestre en curso", 1: "trimestre siguiente", 2: "dentro de 2 trimestres"})
    t_["clave"] = "t" + t_["k"].astype(str)
    partes.append(t_.dropna(subset=["hor"]))
    rem = pd.concat(partes, ignore_index=True)
    for (var, uni, clave, hor), q in rem.groupby(["variable_std", "unidad", "clave", "hor"]):
        s_ = q.groupby("fecha_rel")["mediana"].mean()
        if len(s_) < 3:
            continue
        uid = "".join(ch if ch.isalnum() else "_" for ch in str(uni).lower())[:20]
        escribir(f"rem_{var}_{clave}_{uid}", f"REM, {VAR_REM.get(var, var)}, mediana: {hor} ({uni})", "REM", "M", str(uni),
                 "https://www.bcra.gob.ar/PublicacionesEstadisticas/Relevamiento_Expectativas_de_Mercado.asp", s_.index, s_.values, "rem")
    # j) estimaciones agrícolas: por cultivo, según cada informe mensual
    e = pd.read_parquet(C / "estimaciones_agricolas.parquet")
    e["fecha"] = pd.to_datetime(e["fecha_informe"]).dt.to_period("M").dt.to_timestamp()
    nomc = {"trigo": "Trigo", "maiz": "Maíz", "soja": "Soja"}
    for cul, q in e.groupby("cultivo"):
        q = q.drop_duplicates("fecha", keep="last")
        for col, et, uni in (("produccion_ton", "producción estimada de la campaña en curso", "toneladas"),
                             ("produccion_ant_ton", "producción de la campaña anterior", "toneladas"),
                             ("superficie_ha", "superficie implantada de la campaña en curso", "hectáreas")):
            escribir(f"agro_{cul}_{col}", f"{nomc.get(cul, cul)}: {et}, según cada informe mensual ({uni})", "AGRO", "M", uni,
                     "https://www.magyp.gob.ar/sitio/areas/estimaciones/estimaciones/informes/", q["fecha"], q[col], "agro")
    # k) futuros de dólar: contrato que vence en el mes de la fecha + k (plazo constante), ajuste y tasa implícita
    fu = pd.read_parquet(C / "dolar_futuro_cem.parquet")
    fu = fu[fu["optionType"].isna() & fu["symbol"].str.fullmatch(r"DLR\d{6}")].copy()
    fu["fecha"] = pd.to_datetime(fu["fecha"])
    fu["venc"] = [pd.Period(f"{s_[-4:]}-{s_[3:5]}", "M") for s_ in fu["symbol"]]
    fu["k"] = [(v - f.to_period("M")).n for v, f in zip(fu["venc"], fu["fecha"])]
    for k in range(0, 7):
        q = fu[fu["k"] == k]
        nom = "contrato que vence este mes" if k == 0 else f"contrato a {k} {'mes' if k == 1 else 'meses'} (vence en el mes de la fecha + {k})"
        escribir(f"cem_dolar_futuro_k{k}_ajuste", f"Dólar futuro, {nom}: precio de ajuste ($ por USD)", "CEM", "D", "$ por USD",
                 "https://cem.matbarofex.com.ar", q["fecha"], q["settlement"], "cem")
        escribir(f"cem_dolar_futuro_k{k}_tasa", f"Dólar futuro, {nom}: tasa implícita (TNA %)", "CEM", "D", "TNA %",
                 "https://cem.matbarofex.com.ar", q["fecha"], q["impliedRate"], "cem")
    # l) licitaciones: tasa de corte mediana de cada licitación, letras a tasa fija en pesos
    li = pd.read_parquet(C / "licitaciones_tesoro.parquet")
    li = li[li["tna_corte"].notna() & li["liquidacion"].notna()]
    for nom, cond, cid in (("letras cortas (hasta 120 días)", li["dias"] <= 120, "corta"), ("letras largas (más de 150 días)", li["dias"] > 150, "larga")):
        q = li[cond].groupby("liquidacion")["tna_corte"].median()
        escribir(f"licitaciones_tna_corte_{cid}", f"Licitaciones del Tesoro: tasa de corte de {nom}, mediana de cada licitación (TNA %)",
                 "LICITACIONES", "M", "TNA %", "https://www.argentina.gob.ar/economia/finanzas/deudapublica/colocacionesdedeuda",
                 q.index, q.values, "licitaciones")
    cat = pd.DataFrame(filas)
    # una sola fuente para todo lo que bajamos directo del INDEC y para la API del BCRA
    cat["fuente"] = cat["fuente"].replace({"INDEC (archivo)": "INDEC", "INDEC (IPC, directo)": "INDEC", "INDEC (EMAE)": "INDEC",
                                           "INDEC (cuentas nacionales)": "INDEC", "BCRA API v4.0 (actualización diaria)": "BCRA API v4.0"})
    cat = cat.sort_values(["fuente", "nombre"]).reset_index(drop=True)
    return cat


# ---------------------------------------------------------------- 5) tablas completas: ZIP siempre; XLSX si <= 100.000 filas; Parquet si más
UMBRAL_FILAS = 100_000
CITA_DCM = "Datos Con Método (www.datosconmetodo.com), Marcos Cavanagh. Datos propios: licencia CC BY 4.0."


def publicar_tabla(destino, base, partes, titulo, desc, fuente, leeme=None):
    """partes = {nombre: DataFrame}. Escribe base.zip (un CSV por parte + LEEME.txt) y, según el total de filas,
    base.xlsx (una hoja por parte) o base.parquet (una parte) / base_<nombre>.parquet (varias). Devuelve la ficha para la web."""
    import zipfile
    destino.mkdir(parents=True, exist_ok=True)
    filas = int(sum(len(d) for d in partes.values()))
    texto = leeme or (f"{titulo} - Datos Con Método\n\n{desc}\n\nFuente: {fuente}\nPublicación: {CITA_DCM}\n"
                      f"Generado: {datetime.now():%Y-%m-%d %H:%M}\nArchivos: "
                      + ", ".join(f"{n}.csv ({len(d):,} filas)" for n, d in partes.items()) + "\n")
    archivos = []
    with zipfile.ZipFile(destino / f"{base}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in partes.items():
            z.writestr(f"{n}.csv", d.to_csv(index=False).encode("utf-8-sig"))   # con BOM: Excel respeta los acentos
        z.writestr("LEEME.txt", texto.encode("utf-8"))
    archivos.append({"formato": "ZIP", "archivo": f"{base}.zip"})
    if filas <= UMBRAL_FILAS:
        with pd.ExcelWriter(destino / f"{base}.xlsx") as w:
            for n, d in partes.items():
                d.to_excel(w, sheet_name=n[:31], index=False)
        archivos.append({"formato": "XLSX", "archivo": f"{base}.xlsx"})
    else:
        for n, d in partes.items():
            nombre = f"{base}.parquet" if len(partes) == 1 else f"{base}_{n}.parquet"
            d.to_parquet(destino / nombre, index=False)
            archivos.append({"formato": "Parquet", "archivo": nombre})
    for a in archivos:
        a["peso_kb"] = round((destino / a["archivo"]).stat().st_size / 1024)
    return {"base": base, "titulo": titulo, "desc": desc, "fuente": fuente, "filas": filas, "grande": filas > UMBRAL_FILAS, "archivos": archivos}


def tablas(destino):
    t = []
    regs = {}
    for nombre, hoja in (("inflacion", "inflacion_I"), ("pronosticos", "inflacion_REM_A_y_B"), ("tc", "dolar"), ("pib", "pib"),
                         ("tasa", "tasa"), ("fiscal", "resultado_primario"), ("comercio", "comercio_exterior"),
                         ("alertas", "semaforo"), ("termometro", "termometro_super")):
        p = C / f"registro_{nombre}.csv"
        if p.exists():
            regs[hoja] = pd.read_csv(p, encoding="utf-8-sig")
    t.append(publicar_tabla(destino, "comparacion_pronosticos", {"comparacion": tabla_comparacion()},
                            "Comparación de pronósticos: nuestro, REM y lo que pasó",
                            "Una fila por pronóstico (variable, horizonte, período e informe): la mediana del REM, el nuestro, el dato real, "
                            "las diferencias entre los tres y dónde cae el nuestro entre las consultoras del REM (y, cuando ya hay dato real, "
                            "en qué puesto estimado de acierto quedaría). Antes de oct-2026: reconstrucción en tiempo real; desde oct-2026: "
                            "registrado antes de cada REM.",
                            "Datos Con Método (CC BY 4.0); REM: BCRA; datos reales: INDEC, BCRA y Ministerio de Economía",
                            leeme=LEEME_COMPARACION))
    t.append(publicar_tabla(destino, "registro_corridas", regs, "Registro de cada corrida (auditoría)",
                            "Cada vez que el sistema corre (dos veces por día desde el 15-oct-2026) guarda su pronóstico con fecha y hora. "
                            "Sirve para verificar que el pronóstico se registró antes de que saliera el REM. Para comparar, usá la tabla de arriba.",
                            "Datos Con Método (CC BY 4.0)"))
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    cols = [c for c in ["mes_relevamiento", "variable_std", "unidad", "tipo_periodo", "periodo_original", "periodo_fin", "horizonte_meses",
                        "mediana", "promedio", "percentil_10", "percentil_25", "percentil_75", "percentil_90", "cantidad_de_participantes",
                        "correccion_aplicada"] if c in r.columns]
    t.append(publicar_tabla(destino, "rem_bcra_normalizado", {"rem": r[cols]}, "REM del BCRA, en formato largo",
                            "Todas las variables y horizontes del Relevamiento de Expectativas de Mercado desde 2016, normalizados.",
                            "Banco Central de la República Argentina (BCRA), Relevamiento de Expectativas de Mercado"))
    l = pd.read_parquet(C / "licitaciones_tesoro.parquet").drop(columns=["archivo", "hoja"], errors="ignore")
    t.append(publicar_tabla(destino, "licitaciones_tesoro", {"colocaciones": l}, "Licitaciones del Tesoro",
                            "Colocaciones de deuda desde 2020 y la tasa de corte calculada para las letras a tasa fija.",
                            "Ministerio de Economía, Oficina Nacional de Crédito Público (CC BY 4.0); tasa de corte: Datos Con Método"))
    t.append(publicar_tabla(destino, "metas_fiscales", {"metas": pd.read_csv(C / "metas_fiscales.csv")}, "Metas fiscales 2016-2027",
                            "Meta de resultado primario vigente en cada fecha (Presupuestos, FMI, anuncios), con la fuente de cada dato.",
                            "Elaboración de Datos Con Método con datos del Ministerio de Economía y del FMI (una fuente por fila)"))
    t.append(publicar_tabla(destino, "estimaciones_agricolas",
                            {"estimaciones": pd.read_parquet(C / "estimaciones_agricolas.parquet").drop(columns=["archivo"], errors="ignore")},
                            "Estimaciones agrícolas, informe por informe",
                            "Superficie y producción estimada de trigo, maíz y soja según cada informe mensual (desde 2018).",
                            "Secretaría de Agricultura, Ganadería y Pesca, Estimaciones Agrícolas (informes mensuales)"))
    f = pd.read_parquet(C / "dolar_futuro_cem.parquet")
    f = f[f["optionType"].isna() & f["symbol"].str.fullmatch(r"DLR\d{6}")]
    f = f.rename(columns={"symbol": "contrato", "settlement": "ajuste", "close": "cierre", "volume": "volumen",
                          "openInterest": "interes_abierto", "impliedRate": "tasa_implicita_tna"})
    t.append(publicar_tabla(destino, "futuros_dolar_cem",
                            {"futuros": f[["fecha", "contrato", "ajuste", "cierre", "volumen", "interes_abierto", "tasa_implicita_tna"]].sort_values(["fecha", "contrato"])},
                            "Futuros de dólar (CEM, A3 Mercados)",
                            "Ajuste, cierre, volumen, interés abierto y tasa implícita de cada contrato de dólar futuro, por día, desde 2020. "
                            "Sin licencia publicada por el productor: ver la aclaración en Fuentes.",
                            "A3 Mercados (ex Matba Rofex), Centro de Estadísticas de Mercado (CEM). Sin licencia publicada por el productor."))
    t.append(publicar_tabla(destino, "mercado_central_diario",
                            {"mercado_central": pd.read_parquet(C / "mercado_central.parquet").drop(columns=["archivo"], errors="ignore")},
                            "Mercado Central: precios diarios",
                            "Planillas diarias de frutas y hortalizas (2017 a hoy, sin ene-2022 a dic-2025): precio máximo, modal y mínimo por especie, "
                            "variedad, procedencia y envase.",
                            "Corporación del Mercado Central de Buenos Aires (CMCBA); 2017-2022 en parte desde copias de web.archive.org"))
    t.append(publicar_tabla(destino, "hacienda_mag_canuelas", {"hacienda": pd.read_parquet(C / "mag_precios.parquet")},
                            "Hacienda: Mercado Agroganadero de Cañuelas",
                            "Precios mensuales por categoría y subcategoría (mínimo, máximo, promedio, mediana, cabezas, kilos), mes completo y hasta el día 25.",
                            "Mercado Agroganadero de Cañuelas (Mercado Agroganadero S.A.), datos públicos de su sitio"))
    t.append(publicar_tabla(destino, "ponderaciones_ipc", {"ponderaciones": pd.read_csv(C / "ponderaciones_ipc.csv", encoding="utf-8-sig")},
                            "Ponderaciones del IPC", "Peso de cada división, grupo y clase en el IPC, por región, y peso de cada región (base dic-2016).",
                            "Instituto Nacional de Estadística y Censos (INDEC), ponderadores del IPC"))
    return t


# ---------------------------------------------------------------- 5c) comparación: Datos Con Método vs REM vs lo que pasó
PROB_REM = [0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0]
COL_REM = ["minimo", "percentil_10", "percentil_25", "mediana", "percentil_75", "percentil_90", "maximo"]


def ubicar(x, cuant):
    """Percentil estimado de x dentro de la distribución de las consultoras (interpolando entre mínimo, P10, P25, P50,
    P75, P90 y máximo). None si x está afuera del rango (mínimo-máximo)."""
    if x is None or pd.isna(x) or any(pd.isna(c) for c in cuant):
        return None, None
    if x < cuant[0]:
        return None, "afuera: más bajo que todas las consultoras"
    if x > cuant[-1]:
        return None, "afuera: más alto que todas las consultoras"
    c = np.maximum.accumulate(np.asarray(cuant, float))
    p = float(np.interp(x, c, PROB_REM)) if c[-1] > c[0] else 0.5
    return round(100 * p), "dentro del rango"


def acierto(nuestro, real, cuant):
    """Share estimado de consultoras cuyo pronóstico quedó MÁS LEJOS del dato real que el nuestro (distribución
    interpolada entre los percentiles publicados)."""
    if any(pd.isna(v) for v in [nuestro, real, *cuant]):
        return None
    c = np.maximum.accumulate(np.asarray(cuant, float))
    grilla = np.interp(np.linspace(0, 1, 201), PROB_REM, c)
    return round(100 * float(np.mean(np.abs(grilla - real) > abs(nuestro - real))))


def tabla_comparacion():
    """Una fila por pronóstico (variable x horizonte x período x informe), con REM, nuestro, real, diferencias y posición."""
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    r["o"] = pd.to_datetime(r["mes_relevamiento"]).dt.to_period("M").astype(str)

    def clave_rem(x):
        if x["tipo_periodo"] == "fecha":
            return str(pd.Timestamp(x["periodo_fin"]).to_period("M"))
        if x["tipo_periodo"] == "trimestre":
            import re
            m = re.search(r"([IV]+)-(\d\d)", str(x["periodo_original"]))
            return f"20{m.group(2)}Q{ {'I': 1, 'II': 2, 'III': 3, 'IV': 4}[m.group(1)] }" if m else None
        return str(x["periodo_original"])
    r["k"] = r.apply(clave_rem, axis=1)
    rem = r.groupby(["variable_std", "unidad", "tipo_periodo", "o", "k"])[COL_REM + ["cantidad_de_participantes"]].mean()

    filas = []

    def agregar(d, var_std, unidad, nombre, nuestro, tipo, k, horizonte, periodo):
        for (_, x), kk, hh, pp in zip(d.iterrows(), k, horizonte, periodo):
            filas.append({"var_std": var_std, "unidad_rem": unidad, "tipo_periodo": tipo if isinstance(tipo, str) else tipo(x),
                          "o": str(x["origen"]), "k": kk, "variable": nombre, "horizonte": hh, "periodo": pp,
                          "rem": x["rem"], "nuestro": x[nuestro], "real": x["real"]})

    # inflación (nuestro pronóstico de la inflación, I)
    d = pd.read_csv(C / "replica_rem.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    pe = pd.read_parquet(C / "precios_encadenada.parquet")
    ind = pd.Series(pe["indice"].values, index=pd.to_datetime(pe["mes"]).dt.to_period("M"))
    m = d[d["H"].str.fullmatch(r"m\d")].copy()
    m["h"] = m["H"].str[1].astype(int)
    agregar(m, "ipc_general", "var. % mensual", "Inflación mensual (IPC)", "I", "fecha", [str(o + h) for o, h in zip(m["origen"], m["h"])],
            ["mes del informe" if h == 0 else f"a {h} {'mes' if h == 1 else 'meses'}" for h in m["h"]], [str(o + h) for o, h in zip(m["origen"], m["h"])])
    a = d[d["H"] == "12m"].copy()
    a["real"] = [(ind.get(o + 12, np.nan) / ind.get(o, np.nan) - 1) * 100 for o in a["origen"]]
    agregar(a, "ipc_general", "var. % i.a.", "Inflación próximos 12 meses", "I", "proximos_meses", ["Próx. 12 meses"] * len(a),
            ["próximos 12 meses"] * len(a), [f"{o + 1} a {o + 12}" for o in a["origen"]])
    # dólar
    d = pd.read_csv(C / "replica_tc.csv", encoding="utf-8-sig")
    d["origen"] = pd.PeriodIndex(d["origen"], freq="M")
    q = d[d["h"].between(1, 6)]
    agregar(q, "tipo_cambio", "$/USD", "Dólar mayorista (promedio del mes)", "A_dolar", "fecha", q["obj"].astype(str).tolist(),
            [f"a {h} {'mes' if h == 1 else 'meses'}" for h in q["h"]], q["obj"].astype(str).tolist())
    q = d[d["h"].isin([100, 101])]
    agregar(q, "tipo_cambio", "$/USD", "Dólar mayorista (diciembre)", "A_dolar", "anio", [str(int(x[:4])) for x in q["obj"].astype(str)],
            ["diciembre del año en curso" if h == 100 else "diciembre del año siguiente" for h in q["h"]], q["obj"].astype(str).tolist())
    # PIB
    d = pd.read_csv(C / "replica_pib.csv", encoding="utf-8-sig")
    agregar(d, "pib", "var. % trim. s.e.", "PIB trimestral (s.e.)", "I_pib", "trimestre", d["Q"].astype(str).tolist(),
            [{-1: "trimestre anterior", 0: "trimestre en curso", 1: "trimestre siguiente", 2: "dentro de 2 trimestres"}.get(k, str(k)) for k in d["k"]],
            d["Q"].astype(str).str.replace("Q", " T").tolist())
    # tasa (BADLAR hasta nov-2024, TAMAR desde dic-2024)
    d = pd.read_csv(C / "replica_tasa.csv", encoding="utf-8-sig")
    for serie, std, nom in (("badlar_tna", "tasa_badlar", "Tasa BADLAR"), ("tamar_tna", "tasa_tamar", "Tasa TAMAR")):
        q = d[d["serie"] == serie]
        agregar(q, std, "TNA", nom, "A", "fecha", q["mes"].astype(str).tolist(), [f"a {h} {'mes' if h == 1 else 'meses'}" for h in q["h"]],
                q["mes"].astype(str).tolist())
    # resultado primario
    d = pd.read_csv(C / "replica_fiscal.csv", encoding="utf-8-sig")
    agregar(d, "resultado_primario", "miles de millones $", "Resultado primario (miles de millones de $)", "I", "anio", d["anio"].astype(int).astype(str).tolist(),
            ["año en curso" if p == 0 else "año siguiente" for p in d["pos"]], d["anio"].astype(int).astype(str).tolist())
    # comercio
    d = pd.read_csv(C / "replica_comercio.csv", encoding="utf-8-sig")
    for var, nom in (("exportaciones", "Exportaciones (millones de USD)"), ("importaciones", "Importaciones (millones de USD)")):
        q = d[(d["var"] == var) & (d["tipo"] == "mes")]
        agregar(q, var, "millones de USD", nom, "A", "fecha", q["objetivo"].astype(str).tolist(),
                ["mes del informe" if h == 0 else f"a {h} {'mes' if h == 1 else 'meses'}" for h in q["h"]], q["objetivo"].astype(str).tolist())
        q = d[(d["var"] == var) & (d["tipo"] == "año")]
        agregar(q, var, "millones de USD", nom, "A", "anio", q["objetivo"].astype(str).tolist(),
                ["año en curso" if h == 0 else "año siguiente" for h in q["h"]], q["objetivo"].astype(str).tolist())
    t = pd.DataFrame(filas)
    t = t[t["nuestro"].notna() & t["rem"].notna()]
    # distribución de las consultoras de ese informe
    claves = list(zip(t["var_std"], t["unidad_rem"], t["tipo_periodo"], t["o"], t["k"]))
    dist = rem.reindex(claves)
    for c in COL_REM + ["cantidad_de_participantes"]:
        t[c] = dist[c].values
    ub = [ubicar(x, cu) for x, cu in zip(t["nuestro"], t[COL_REM].values)]
    t["percentil"] = [u[0] for u in ub]
    t["ubicacion"] = [u[1] for u in ub]
    t["ganamos_a"] = [acierto(n, re_, cu) for n, re_, cu in zip(t["nuestro"], t["real"], t[COL_REM].values)]
    n = t["cantidad_de_participantes"]
    t["puesto"] = [None if pd.isna(g) or pd.isna(k) else f"≈ {int(round((1 - g / 100) * k)) + 1} de {int(k) + 1}" for g, k in zip(t["ganamos_a"], n)]
    t["tipo"] = np.where(t["o"] >= "2026-10", "registrado antes del REM", "reconstrucción en tiempo real")
    out = pd.DataFrame({
        "variable": t["variable"], "horizonte": t["horizonte"], "periodo_pronosticado": t["periodo"], "fecha_del_pronostico": t["o"],
        "tipo": t["tipo"], "rem_mediana": t["rem"].round(4), "rem_minimo": t["minimo"].round(4), "rem_p10": t["percentil_10"].round(4),
        "rem_p25": t["percentil_25"].round(4), "rem_p75": t["percentil_75"].round(4), "rem_p90": t["percentil_90"].round(4),
        "rem_maximo": t["maximo"].round(4), "datos_con_metodo": t["nuestro"].round(4), "real": t["real"].round(4),
        "dif_dcm_menos_rem": (t["nuestro"] - t["rem"]).round(4), "dif_rem_menos_real": (t["rem"] - t["real"]).round(4),
        "dif_dcm_menos_real": (t["nuestro"] - t["real"]).round(4), "consultoras": n,
        "ubicacion_de_nuestro_pronostico": t["ubicacion"], "percentil_estimado": t["percentil"],
        "mas_cerca_del_real_que_el_pct_de_consultoras": t["ganamos_a"], "puesto_estimado_por_acierto": t["puesto"]})
    return out.sort_values(["variable", "fecha_del_pronostico", "periodo_pronosticado"]).reset_index(drop=True)


LEEME_COMPARACION = """Comparación de pronósticos: Datos Con Método, REM del BCRA y lo que pasó - www.datosconmetodo.com

Una fila por pronóstico: variable x horizonte x período pronosticado x informe del REM.
  fecha_del_pronostico      mes del informe del REM (nuestro pronóstico usa solo datos publicados hasta ese momento)
  tipo                      "reconstrucción en tiempo real" (antes de oct-2026: el pronóstico se armó después, pero solo con
                            datos publicados hasta esa fecha) o "registrado antes del REM" (desde oct-2026: guardado con fecha
                            y hora antes de que saliera el REM)
  rem_mediana, datos_con_metodo, real   en la unidad de la variable; real vacío si todavía no se conoce
  rem_minimo, rem_p10, rem_p25, rem_p75, rem_p90, rem_maximo   distribución de las consultoras de ese informe del REM
  dif_*                     diferencias en la misma unidad (puntos porcentuales en inflación, PIB y tasas)
  consultoras               cantidad de participantes de ese informe del REM
  ubicacion_de_nuestro_pronostico, percentil_estimado   dónde cae nuestro pronóstico entre los de las consultoras.
                            "Afuera" es exacto (se compara con el mínimo y el máximo que publica el BCRA); el percentil se
                            ESTIMA interpolando entre el mínimo, los percentiles 10, 25, 50, 75 y 90 y el máximo.
  mas_cerca_del_real_que_el_pct_de_consultoras, puesto_estimado_por_acierto   cuando ya hay dato real: porcentaje
                            estimado de consultoras cuyo pronóstico quedó más lejos del dato que el nuestro, y el puesto que
                            eso implica (1 = el más cercano). ESTIMADO: el BCRA no publica el pronóstico de cada consultora.
Modelos: inflación = I (inflación núcleo, expectativas de los hogares, dólar esperado); dólar = futuros ajustados por el
descuento típico del REM; PIB = EMAE + tendencia; tasa = tasa actual + futuros; resultado primario = ejecución + meta
oficial; comercio exterior = mismo mes del año anterior x crecimiento reciente (+ precios de granos en exportaciones).
Fuentes: REM, BCRA; datos reales: INDEC, BCRA, Ministerio de Economía. Elaboración: Datos Con Método (CC BY 4.0).
"""


# ---------------------------------------------------------------- 5a) canasta INDEC en supermercados, últimos 12 meses
CACHE = C / "web_cache" / "canasta"
LEEME_CANASTA = """Precios de supermercados (SEPA) para la canasta del IPC - Datos Con Método (www.datosconmetodo.com)

Un archivo por mes: canasta_supermercados_AAAA-MM (ZIP con el CSV y este LEEME; también en Parquet).
Una fila por día x región del IPC x cadena x bandera x producto, para los productos de supermercado que corresponden a
alguna de las variedades de la canasta de precios promedio del INDEC.

Columnas
  fecha                 día (AAAA-MM-DD)
  region                región del IPC (GBA = CABA y sucursales a menos de 40 km del Obelisco; resto según la provincia)
  variedad_indec        variedad de la canasta del INDEC a la que asignamos el producto
  unidad_indec          unidad en la que el INDEC informa el precio de esa variedad (p. ej. 1 kg, 1 l, 500 g)
  comercio_id, bandera_id, cadena, bandera   cadena y bandera (identificadores y nombres del SEPA)
  producto_sepa_id      código del producto (EAN sin ceros a la izquierda, o código interno de la cadena)
  descripcion           descripción del producto según la cadena
  n_sucursales          sucursales de esa región que informaron el producto ese día
  precio_producto       precio mediano entre esas sucursales, por unidad de producto ($)
  precio_unidad_indec   el mismo precio llevado a la unidad del INDEC ($)
  con_promo             1 si se usó un precio de promoción general (sin tarjeta, banco, socios, ni cantidad mínima)

Criterio de promociones (como el INDEC, Metodología 32): se usa el precio de promoción solo si lo obtiene cualquiera
comprando una unidad al contado; si no, el precio de lista.
Cada mes tiene más de 1 millón de filas: no entra en una hoja de Excel (tope 1.048.576). Usá el CSV con Power Query,
o el Parquet con Python, R, Power BI o DuckDB.

Fuente: Secretaría de Comercio, Ministerio de Economía de la Nación, Sistema Electrónico de Publicidad de Precios
Argentinos (SEPA), https://datos.produccion.gob.ar/dataset/sepa-precios (licencia CC BY 4.0).
Hasta el 7-10-2026, a partir de las copias diarias del dataset oficial del proyecto abierto preciazo
(https://github.com/catdevnull/preciazo); desde el 8-10-2026, descarga propia del portal oficial.
Elaboración (selección de productos, asignación a la canasta, regiones y precio mediano): Datos Con Método. CC BY 4.0.
Citar: Secretaría de Comercio (SEPA); copias de preciazo; elaboración de Datos Con Método (www.datosconmetodo.com).
"""


def canasta_ultimo_anio(destino):
    """Archivos mensuales de la canasta (últimos 12 meses), en ZIP (CSV + LEEME) y, si pasan las 100.000 filas, Parquet.
    Los meses cerrados se arman una sola vez (caché en datos\\web_cache\\canasta, en Parquet); el mes en curso y el
    anterior se rehacen en cada corrida."""
    import duckdb
    items = Path("E:/SEPA/canasta")
    CACHE.mkdir(parents=True, exist_ok=True)
    destino.mkdir(parents=True, exist_ok=True)
    disponibles = sorted({p.name[6:13] for p in items.glob("items_*.parquet")}) if items.exists() else []
    actual = pd.Period(HOY, "M")
    meses = [str(actual - i) for i in range(11, -1, -1)]
    mapa = pd.read_csv(C / "sepa_canasta_mapeo.csv", encoding="utf-8-sig")
    mapa["unidad_indec"] = mapa["tamano"].map(lambda x: f"{x:g}") + " " + mapa["unidad_base"].astype(str)
    nombres = None
    con = duckdb.connect()
    con.execute("set memory_limit='3GB'")
    salida = []
    for mes in meses:
        cache = CACHE / f"canasta_supermercados_{mes}.parquet"
        if mes in disponibles and (not cache.exists() or mes in (str(actual), str(actual - 1))):
            if nombres is None:                 # nombres de cadena y bandera (último resumen SEPA)
                r = sorted(Path("E:/SEPA/resumen").glob("sepa_*.parquet"))[-1]
                nombres = pd.read_parquet(r, columns=["id_comercio", "id_bandera", "razon_social", "bandera"]).drop_duplicates(["id_comercio", "id_bandera"])
                con.register("nombres", nombres)
                con.register("mapa", mapa[["clave", "descripcion", "unidad_indec", "factor"]])
            glob_ = (items / f"items_{mes}-*.parquet").as_posix()
            con.execute(f"""copy (
                select strftime(cast(i.fecha as date), '%Y-%m-%d') as fecha, i.region, i.producto_indec as variedad_indec, m.unidad_indec,
                       try_cast(split_part(i.item, '|', 1) as integer) as comercio_id, try_cast(split_part(i.item, '|', 3) as integer) as bandera_id,
                       n.razon_social as cadena, n.bandera, split_part(i.item, '|', 2) as producto_sepa_id, m.descripcion, i.n_sucursales,
                       round(i.precio_efectivo / m.factor, 2) as precio_producto, round(i.precio_efectivo, 2) as precio_unidad_indec,
                       cast(i.con_promo as integer) as con_promo
                from read_parquet('{glob_}') i
                left join mapa m on m.clave = split_part(i.item, '|', 1) || '|' || split_part(i.item, '|', 2)
                left join nombres n on n.id_comercio = split_part(i.item, '|', 1) and n.id_bandera = split_part(i.item, '|', 3)
                order by fecha, region, variedad_indec, comercio_id, bandera_id, producto_sepa_id)
                to '{cache.as_posix()}' (format parquet)""")
        if cache.exists():
            d = pd.read_parquet(cache)
            ficha = publicar_tabla(destino, f"canasta_supermercados_{mes}", {f"canasta_supermercados_{mes}": d},
                                   mes, "", "Secretaría de Comercio (SEPA), vía preciazo y descarga propia; elaboración Datos Con Método",
                                   leeme=LEEME_CANASTA)
            ficha.update({"mes": mes, "dias": int(d["fecha"].nunique())})
            for a in ficha["archivos"]:
                a["archivo"] = f"canasta/{a['archivo']}"
            salida.append(ficha)
        else:
            salida.append({"mes": mes, "dias": 0, "filas": 0, "archivos": []})
    con.close()
    (destino / "LEEME_canasta.txt").write_text(LEEME_CANASTA, encoding="utf-8")
    return salida


# ---------------------------------------------------------------- 5b) dónde está la historia de SEPA
def peso_gb(carpeta):
    p = Path(carpeta)
    if not p.exists():
        return None
    return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9, 1)


def sepa_crudos():
    base = Path("E:/SEPA")
    nuestros = sorted((base / "crudos").rglob("sepa_*.zip")) if (base / "crudos").exists() else []
    desde = nuestros[0].stem.replace("sepa_", "") if nuestros else None
    hasta = nuestros[-1].stem.replace("sepa_", "") if nuestros else None
    gb = lambda c, defecto: str(peso_gb(base / c) or defecto).replace(".", ",")
    return [
        {"periodo": "Últimos 7 días", "quien": "Secretaría de Comercio (portal oficial de datos abiertos)", "peso": "≈0,3 GB por día",
         "url": "https://datos.produccion.gob.ar/dataset/sepa-precios", "licencia": "CC BY 4.0",
         "nota": "Un archivo por día de la semana; se reemplaza cada semana."},
        {"periodo": "may-2016 a mar-2018", "quien": "Zenodo (DOI 10.5281/zenodo.6568295): Ministerio de Desarrollo Productivo, D. Daruich, J. Kozlowski y L. Vilhuber",
         "peso": f"{gb('historico_zenodo', 41.7)} GB", "url": "https://doi.org/10.5281/zenodo.6568295", "licencia": "Ver el registro en Zenodo",
         "nota": "Solo códigos y precios: sin descripción de productos ni ubicación de sucursales."},
        {"periodo": "mar-2018 a ago-2024", "quien": "No disponible públicamente", "peso": "—", "url": None, "licencia": "—",
         "nota": "Lo pedimos a la Secretaría de Comercio y a fuentes académicas que pueden tener el archivo completo (octubre de 2026); a la fecha no recibimos respuesta."},
        {"periodo": "ago-2024 a oct-2026", "quien": "Proyecto abierto preciazo (copia del dataset oficial cada 2 horas)",
         "peso": f"{gb('historico_preciazo', 40.2)} GB", "url": "https://github.com/catdevnull/sepa-precios-metadata",
         "licencia": "Datos oficiales CC BY 4.0, recomprimidos por un tercero", "nota": "Faltan mar-2026 a jun-2026 y algunos días sueltos."},
        {"periodo": f"{desde} a {hasta}" if desde else "desde oct-2026", "quien": "Datos Con Método (archivo propio de los ZIP oficiales)",
         "peso": f"{gb('crudos', 5.2)} GB (crece ≈110 GB por año)", "url": None, "licencia": "CC BY 4.0 (de origen)",
         "nota": "Todavía no lo publicamos por tamaño: queda para cuando tengamos hosting profesional. El índice de supermercados que armamos con estos datos sí está en Descargas."},
    ]


# ---------------------------------------------------------------- 5d) pronósticos sellados (emitir_pronostico.py)
def sellos(destino):
    """Copia cada sello (PDF, PNG, CSV y sus .ots) al sitio y devuelve la lista para la página."""
    reg_p = C / "sellos" / "registro_sellos.csv"
    if not reg_p.exists():
        return []
    reg = pd.read_csv(reg_p, encoding="utf-8-sig", dtype=str).fillna("")
    out = []
    for _, x in reg.iterrows():
        origen = C / "sellos" / x["relevamiento"]
        dest = destino / x["relevamiento"]
        dest.mkdir(parents=True, exist_ok=True)
        archivos = {}
        for clave, nombre in (("pdf", x["pdf"]), ("png", x["pdf"].replace(".pdf", ".png")), ("csv", x["csv"]),
                              ("ots_pdf", x["ots_pdf"]), ("ots_csv", x["ots_csv"])):
            if nombre and (origen / nombre).exists():
                shutil.copy2(origen / nombre, dest / nombre)
                archivos[clave] = f"sellos/{x['relevamiento']}/{nombre}"
        out.append({**{k: x[k] for k in ("n", "relevamiento", "registrado", "sha256_pdf", "sha256_csv", "bitcoin", "wayback_pdf",
                                         "post_url", "post_rem_url", "post_real_url")}, "archivos": archivos})
    return sorted(out, key=lambda y: int(y["n"]), reverse=True)


# ---------------------------------------------------------------- 5e) HTML legible sin JavaScript + vista previa al compartir
MES3 = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _num(v, d):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    t = f"{abs(v):,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("−" if v < 0 else "") + t


def _per(x):
    x = str(x)
    if len(x) == 7 and x[4] == "-":
        return f"{MES3[int(x[5:]) - 1]}-{x[:4]}"
    if len(x) == 10 and x[4] == "-" and x[7] == "-":
        return f"{x[8:]}/{x[5:7]}/{x[:4]}"
    import re
    return re.sub(r"^(\d{4}) ?[QT](\d)$", lambda m: f"{m.group(1)} T{m.group(2)}", x)


def _esc(x):
    import html
    return html.escape(str(x), quote=True)


FMT_P = {"inflacion": (2, "", "%"), "dolar": (2, "$", ""), "pib": (2, "", "%"), "tasa": (2, "", "%"), "fiscal": (1, "B$ ", ""),
         "exportaciones": (0, "US$ ", ""), "importaciones": (0, "US$ ", "")}


def _fp(clave, v):
    d, pre, suf = FMT_P.get(clave, (2, "", ""))
    t = _num(v, d)
    if t == "—":
        return t
    return ("−" + pre + t[1:] if t.startswith("−") else pre + t) + suf


def html_estatico(resumen):
    """Mismo contenido que dibuja app.js, escrito en el HTML: lo leen buscadores, scrapers y lectores sin JavaScript.
    Con JavaScript activo se oculta (clase "js" en <html>) y se ve la versión interactiva."""
    L = ['<section id="en-texto" class="sin-js envoltura" aria-label="Resumen en texto">',
         f'<p>Actualizado: {_esc(resumen["generado"])}. '
         'Datos en formato abierto: <a href="data/resumen.json">resumen.json</a> · <a href="data/catalogo.json">catalogo.json</a> · '
         '<a href="data/fuentes.json">fuentes.json</a>.</p>', "<h2>Los datos de hoy</h2>", "<ul>"]
    for x in resumen.get("indicadores") or []:
        pre = {"dolar": "$", "ccl": "$", "mep": "$", "fiscal": "B$ "}.get(x["clave"], "")
        suf = "%" if str(x["unidad"]).startswith("%") or x["clave"] == "tamar" else ""
        uni = "TNA" if x["clave"] == "tamar" else str(x["unidad"]).lstrip("% ") if str(x["unidad"]).startswith("%") else x["unidad"]
        det = f' · {_esc(str(x["detalle"]).rstrip("."))}' if x.get("detalle") else ""
        L.append(f'<li><strong>{_esc(x["titulo"])}</strong> ({_esc(_per(x["fecha"]))}): {pre}{_num(x["valor"], x.get("dec", 2))}{suf} '
                 f'{_esc(uni)}{det}. Fuente: {_esc(x["fuente"])}.</li>')
    L += ["</ul>", "<h2>Pronósticos próximos 12 meses</h2>",
          f'<p>Nuestro pronóstico para el próximo informe del REM y la mediana del último REM publicado ({_per(resumen["rem_ultimo"])}).</p>']
    for t in resumen.get("pronosticos") or []:
        L.append(f'<h3>{_esc(t["titulo"])}</h3><p>{_esc(t["unidad"])}. Modelo: {_esc(t["modelo"])}.</p>')
        L.append("<table><thead><tr><th>Período</th><th>Nuestro</th><th>REM anterior</th></tr></thead><tbody>")
        for f in t["filas"]:
            L.append(f'<tr><td>{_esc(_per(f["periodo"]))}</td><td>{_fp(t["clave"], f["nuestro"])}</td><td>{_fp(t["clave"], f["rem"])}</td></tr>')
        L.append("</tbody></table>")
    if resumen.get("mes_en_curso"):
        L.append("<h2>Pronósticos mes en curso</h2><ul>")
        for x in resumen["mes_en_curso"]:
            L.append(f"<li><strong>{_esc(x['titulo'])}</strong> ({_esc(x['periodo'])}): {x['valor']} {_esc(x['unidad'])}. {_esc(x['nota'])}</li>")
        L.append("</ul><p><strong>Importante:</strong> estos datos son estimaciones a partir de modelos nuevos sin verificar; úselos bajo su propia responsabilidad.</p>")
    for clave, tit in (("ultimo_rem", "Último REM publicado vs. estimaciones de Datos Con Método"), ("ultimos_reales", "Datos reales vs. Datos Con Método vs. REM (mes anterior)")):
        b = resumen.get(clave)
        if b:
            L.append(f"<h2>{tit}</h2><p>{_esc(b['titular'])}</p><ul>")
            for f in b["filas"]:
                real = f" Dato real: {f['real']}." if "real" in f else ""
                L.append(f"<li><strong>{_esc(f['titulo'])}</strong>:{real} REM {f['rem']} · nosotros {f['nuestro']}. {_esc(f['texto'])}</li>")
            L.append("</ul>")
    L += ['<p>Más: <a href="#historial">resultados de los últimos meses</a> · <a href="#sellos">verificación de que las predicciones no fueron modificadas</a> · '
          '<a href="#datos">series para descargar</a>.</p>', "</section>"]
    return "\n".join(L)


def frase_portada(resumen):
    """Una línea con los pronósticos principales, para la descripción y la vista previa."""
    P = {t["clave"]: t for t in resumen.get("pronosticos") or []}
    partes = []
    if "inflacion" in P:
        f = [x for x in P["inflacion"]["filas"] if x["nuestro"] is not None]
        if f:
            partes.append(f'inflación de {_per(f[0]["periodo"])} {_num(f[0]["nuestro"], 2)}%')
        a = [x for x in P["inflacion"]["filas"] if str(x["periodo"]).startswith("Próx. 12")]
        if a:
            partes.append(f'próximos 12 meses {_num(a[0]["nuestro"], 1)}%')
    if "dolar" in P:
        a = [x for x in P["dolar"]["filas"] if str(x["periodo"]).startswith("Fin de")]
        if a:
            partes.append(f'dólar de dic-{str(a[0]["periodo"])[7:11]} ${_num(a[0]["nuestro"], 0)}')
    return " · ".join(partes)


def vista_previa(resumen, destino):
    """Imagen 1200x630 para LinkedIn y WhatsApp (og:image). Si Edge falla, el sitio sigue sin imagen."""
    try:
        from emitir_pronostico import edge
    except Exception:
        return False
    P = {t["clave"]: t for t in resumen.get("pronosticos") or []}
    filas = []
    for clave, nombre in (("inflacion", "Inflación"), ("dolar", "Dólar mayorista"), ("pib", "PIB trimestral"), ("tasa", "Tasa TAMAR")):
        if clave in P:
            f = next((x for x in P[clave]["filas"] if x["nuestro"] is not None), None)
            if f:
                filas.append(f'<tr><td>{nombre}</td><td class="p">{_esc(_per(f["periodo"]))}</td><td class="n">{_fp(clave, f["nuestro"])}</td>'
                             f'<td class="r">{_fp(clave, f["rem"])}</td></tr>')
    css = ("body{margin:0;width:1200px;height:630px;background:#fcfcfb;font-family:'IBM Plex Sans','Segoe UI',Arial,sans-serif;color:#0b0b0b;position:relative}"
           ".c{padding:52px 64px}.m{display:flex;align-items:center;gap:16px;font-size:34px;font-weight:600}.m b{border-bottom:5px solid #2a78d6}"
           "h1{font-size:44px;margin:30px 0 6px;letter-spacing:-.5px}.s{color:#52514e;font-size:24px;margin:0 0 22px}"
           "table{border-collapse:collapse;font-size:28px;width:100%}td,th{padding:8px 0;border-bottom:1px solid #e1e0d9;text-align:left}"
           "th{font-size:20px;color:#6f6d68;font-weight:500}.n,.r{text-align:right;font-variant-numeric:tabular-nums}.n{color:#2a78d6;font-weight:600}"
           ".r{color:#c4552a}.p{color:#52514e;font-size:22px}.pie{position:absolute;bottom:30px;left:64px;font-size:22px;color:#6f6d68}")
    logo = ('<svg width="48" height="48" viewBox="0 0 32 32"><rect x="1.5" y="1.5" width="29" height="29" rx="6" fill="none" stroke="#0b0b0b" stroke-width="2"/>'
            '<rect x="8" y="17" width="3.5" height="7" rx="1"/><rect x="14.25" y="12" width="3.5" height="12" rx="1"/>'
            '<rect x="20.5" y="15" width="3.5" height="9" rx="1"/><circle cx="22.25" cy="8.5" r="2.25" fill="#2a78d6"/></svg>')
    html_ = ('<!doctype html><html><head><meta charset="utf-8"><style>' + css + '</style></head><body><div class="c">'
             f'<div class="m">{logo}<span>Datos Con <b>Método</b></span></div>'
             '<h1>Pronósticos de la economía argentina</h1>'
             f'<p class="s">Hechos con datos públicos y comparados con el REM del BCRA · {_esc(_per(resumen["generado"][:10]))}</p>'
             '<table><tr><th></th><th></th><th class="n">Nuestro</th><th class="r">REM ant.</th></tr>' + "".join(filas) + '</table></div>'
             '<div class="pie">www.datosconmetodo.com</div></body></html>')
    tmp = destino.parent / "_vista_previa.html"
    tmp.write_text(html_, encoding="utf-8")
    try:
        edge(tmp, destino, pdf=False, ancho=1200, alto=630)
        return True
    except Exception:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def completar_index(resumen):
    """Escribe en index.html el resumen en texto, la descripción con números y las etiquetas de vista previa."""
    import re
    idx = NUEVO / "index.html"
    s = idx.read_text(encoding="utf-8")
    frase = frase_portada(resumen)
    desc = ("Pronósticos de la economía argentina hechos con datos públicos y método abierto, comparados con el REM del BCRA"
            + (f": {frase}" if frase else "") + ". Series para descargar.")
    hay_img = vista_previa(resumen, NUEVO / "vista_previa.png")
    s = s.replace("<head>", '<head>\n<script>document.documentElement.classList.add("js")</script>', 1)
    # versión en el nombre de app.js y estilo.css: Cloudflare y los navegadores guardan copias (hasta 4 h) y, sin esto,
    # un cambio de diseño o de textos tarda horas en verse. Cada corrida pide los archivos nuevos.
    ver = datetime.now().strftime("%Y%m%d%H%M")
    s = s.replace('<script src="app.js"></script>', f'<script src="app.js?v={ver}"></script>', 1)
    s = s.replace('<link rel="stylesheet" href="estilo.css">', f'<link rel="stylesheet" href="estilo.css?v={ver}">', 1)
    s = re.sub(r'<meta name="description" content="[^"]*">', lambda m: f'<meta name="description" content="{_esc(desc)}">', s, count=1)
    s = re.sub(r'<meta property="og:description" content="[^"]*">', lambda m: f'<meta property="og:description" content="{_esc(desc)}">', s, count=1)
    extra = ['<meta property="og:site_name" content="Datos Con Método">', '<meta property="og:locale" content="es_AR">',
             '<meta name="twitter:card" content="summary_large_image">', '<meta name="twitter:title" content="Datos Con Método">',
             f'<meta name="twitter:description" content="{_esc(desc)}">']
    if hay_img:
        v = datetime.now().strftime("%Y%m%d%H%M")         # cambia en cada corrida: LinkedIn y WhatsApp no muestran una imagen vieja
        img = f"https://www.datosconmetodo.com/vista_previa.png?v={v}"
        extra += [f'<meta property="og:image" content="{img}">', '<meta property="og:image:width" content="1200">',
                  '<meta property="og:image:height" content="630">',
                  '<meta property="og:image:alt" content="Pronósticos de Datos Con Método comparados con el REM del BCRA">',
                  f'<meta name="twitter:image" content="{img}">']
    ld = {"@context": "https://schema.org", "@type": "Dataset", "name": "Datos Con Método: pronósticos y series de la economía argentina",
          "description": desc, "url": "https://www.datosconmetodo.com/", "license": "https://creativecommons.org/licenses/by/4.0/",
          "creator": {"@type": "Person", "name": "Marcos Cavanagh", "url": "https://www.linkedin.com/in/marcoscav"},
          "dateModified": resumen["generado"][:10], "inLanguage": "es", "isAccessibleForFree": True,
          "distribution": [{"@type": "DataDownload", "encodingFormat": "application/json",
                            "contentUrl": f"https://www.datosconmetodo.com/data/{n}.json"} for n in ("resumen", "catalogo", "fuentes")]}
    extra.append(f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>')
    s = s.replace('<meta name="author" content="Marcos Cavanagh">', '<meta name="author" content="Marcos Cavanagh">\n' + "\n".join(extra), 1)
    s = s.replace('<main id="contenido">', '<main id="contenido">\n' + html_estatico(resumen), 1)
    idx.write_text(s, encoding="utf-8")
    return hay_img


# ---------------------------------------------------------------- 5f) semáforo de datos: ¿están al día los datos que usan los pronósticos?
# (decisión de Marcos, 24-oct-2026: reemplaza en el sitio al semáforo de alertas de mercado, que sigue en informe_general.txt)
# Cada dato tiene su plazo: días máximos entre el comienzo del último período publicado y hoy, según cuándo se publica.
# Diarios: 6 días (fines de semana largos). IPC, IMIG, UTDT: el dato del mes t sale a mediados de t+1 (75 días).
# Comercio (ICA): ~20 de t+1 (85). Pink Sheet: comienzos de t+1 (70). EMAE: ~50 días después de cerrar el mes (125).
# PIB trimestral: ~80 días después de cerrar el trimestre; el de un trimestre sigue siendo el último hasta que sale el
# siguiente, ~260 días después del comienzo del trimestre (270). El plazo es el máximo justo antes de la publicación siguiente.
INSUMOS = [
    ("indec_ipc_general_indice_var_mensual", "IPC, nivel general (INDEC)", 75),
    ("indec_ipc_nucleo_indice_var_mensual", "IPC, núcleo (INDEC)", 75),
    ("utdt_ei_promedio", "Expectativas de inflación de los hogares, promedio (UTDT)", 75),
    ("utdt_ei_mediana", "Expectativas de inflación de los hogares, mediana (UTDT)", 75),
    ("dcm_sepa_diario_nacional", "Precios de supermercados (SEPA)", 6),
    ("bcra_api_tc_mayorista_a3500", "Dólar mayorista A3500 (BCRA)", 6),
    ("cem_dolar_futuro_k1_ajuste", "Dólar futuro a 1 mes (CEM)", 6),
    ("cem_dolar_futuro_k6_ajuste", "Dólar futuro a 6 meses (CEM)", 6),
    ("bcra_api_tamar_tna", "Tasa TAMAR (BCRA)", 6),
    ("indec_emae_desest", "EMAE desestacionalizado (INDEC)", 125),
    ("indec_pib_desest_pib", "PIB trimestral desestacionalizado (INDEC)", 270),
    ("datosgob_452.3_RESULTADO_RIO_0_M_18_54", "Resultado primario, IMIG (Secretaría de Hacienda)", 80),
    ("datosgob_74.3_IET_0_M_16", "Exportaciones (INDEC)", 85),
    ("datosgob_74.3_IIT_0_M_25", "Importaciones (INDEC)", 85),
    ("bm_indice_grains", "Precios de granos (Banco Mundial)", 70),
    ("bm_indice_oils_meals", "Precios de aceites y harinas (Banco Mundial)", 70),
]


def semaforo_datos(cat):
    """Porcentaje de los datos que usan los pronósticos que están al día. Verde >= 90%, amarillo >= 75%, rojo < 75%."""
    hoy = pd.Timestamp.now().normalize()
    por_id = cat.set_index("id")["hasta"].to_dict()
    filas = []
    for sid, nombre, dias in INSUMOS:
        hasta = pd.to_datetime(por_id.get(sid), errors="coerce")
        edad = None if pd.isna(hasta) else int((hoy - hasta).days)
        filas.append({"id": sid, "nombre": nombre, "hasta": None if pd.isna(hasta) else str(hasta.date()),
                      "plazo_dias": dias, "al_dia": edad is not None and edad <= dias})
    n = sum(f["al_dia"] for f in filas)
    pct = 100 * n / len(filas)
    color = "verde" if pct >= 90 else "amarillo" if pct >= 75 else "rojo"
    return {"color": color, "pct": round(pct, 1), "al_dia": n, "total": len(filas),
            "atrasados": [f["nombre"] for f in filas if not f["al_dia"]], "detalle": filas}


# ---------------------------------------------------------------- 5g) el relato de la portada: último REM vs nosotros y últimos datos reales
# (pedido de Marcos, 24-oct-2026: Variables de hoy -> Pronóstico para el próximo REM -> REM nuevo vs nuestras estimaciones
#  -> Datos reales nuevos vs nuestras predicciones y las del REM). Sale de la tabla de comparación (tabla_comparacion()).
MESES_L = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
CLAVE_VAR = {"Inflación mensual (IPC)": "inflacion", "Inflación próximos 12 meses": "inflacion",
             "Dólar mayorista (promedio del mes)": "dolar", "Dólar mayorista (diciembre)": "dolar", "PIB trimestral (s.e.)": "pib",
             "Tasa TAMAR": "tasa", "Resultado primario (miles de millones de $)": "fiscal",
             "Exportaciones (millones de USD)": "comercio", "Importaciones (millones de USD)": "comercio"}
FUENTE_REAL = {"inflacion": "INDEC", "dolar": "BCRA", "pib": "INDEC", "tasa": "BCRA", "fiscal": "Secretaría de Hacienda", "comercio": "INDEC"}
SEL_REM = [("Inflación mensual (IPC)", "mes del informe"), ("Inflación mensual (IPC)", "a 1 mes"),
           ("Inflación próximos 12 meses", "próximos 12 meses"), ("Dólar mayorista (promedio del mes)", "a 1 mes"),
           ("Dólar mayorista (diciembre)", "diciembre del año en curso"), ("PIB trimestral (s.e.)", "trimestre en curso"),
           ("Tasa TAMAR", "a 1 mes"), ("Resultado primario (miles de millones de $)", "año en curso"),
           ("Exportaciones (millones de USD)", "año en curso"), ("Importaciones (millones de USD)", "año en curso")]
SEL_REAL = ["Inflación mensual (IPC)", "Dólar mayorista (promedio del mes)", "PIB trimestral (s.e.)", "Tasa TAMAR",
            "Exportaciones (millones de USD)", "Importaciones (millones de USD)"]


def _mes_largo(p):
    p = str(p)
    return f"{MESES_L[int(p[5:7]) - 1]} de {p[:4]}" if len(p) >= 7 and p[4] == "-" else p


def _valor(clave, v):
    if v is None or pd.isna(v):
        return "—"
    if clave in ("inflacion", "pib", "tasa"):
        return _num(v, 2) + "%"
    if clave == "dolar":
        return ("−$" if v < 0 else "$") + _num(abs(v), 0)
    if clave == "fiscal":
        return ("−B$ " if v < 0 else "B$ ") + _num(abs(v) / 1000, 1)
    return "US$ " + _num(v, 0) + " M"


def _dif(clave, d):
    if d is None or pd.isna(d):
        return ""
    lado = "más" if d > 0 else "menos"
    a = abs(d)
    if clave in ("inflacion", "pib", "tasa"):
        return "lo mismo" if a < 0.005 else f"{_num(a, 2)} puntos {lado}"
    if clave == "dolar":
        return "lo mismo" if a < 0.5 else f"${_num(a, 0)} {lado}"
    if clave == "fiscal":
        return "lo mismo" if a < 50 else f"{_num(a / 1000, 1)} billones de $ {lado}"
    return "lo mismo" if a < 0.5 else f"US$ {_num(a, 0)} millones {lado}"


def _titulo(var, horiz, per):
    per = str(per)
    if var == "Inflación mensual (IPC)":
        return f"Inflación de {_mes_largo(per)}"
    if var == "Inflación próximos 12 meses":
        return "Inflación de los próximos 12 meses"
    if var == "Dólar mayorista (promedio del mes)":
        return f"Dólar mayorista, promedio de {_mes_largo(per)}"
    if var == "Dólar mayorista (diciembre)":
        return f"Dólar mayorista, diciembre de {per[:4]}"
    if var == "PIB trimestral (s.e.)":
        return f"PIB del {per[-1]}.º trimestre de {per[:4]}"
    if var == "Tasa TAMAR":
        return f"Tasa TAMAR, promedio de {_mes_largo(per)}"
    if var.startswith("Resultado primario"):
        return f"Resultado primario de {per}"
    nombre = "Exportaciones" if var.startswith("Exportaciones") else "Importaciones"
    return f"{nombre} de {_mes_largo(per) if '-' in per else per}"


def _rango(x):
    return {k: (None if pd.isna(x[c]) else float(x[c])) for k, c in
            (("min", "rem_minimo"), ("p10", "rem_p10"), ("p50", "rem_mediana"), ("p90", "rem_p90"), ("max", "rem_maximo"))}


def _ubicacion(x):
    u = str(x["ubicacion_de_nuestro_pronostico"])
    p = x["percentil_estimado"]
    if u.startswith("afuera"):
        return "afuera", ("Quedó por debajo de todas las consultoras." if "bajo" in u else "Quedó por encima de todas las consultoras.")
    if pd.isna(p):
        return "sin_dato", ""
    p = int(p)
    if 10 <= p <= 90:
        return "central", f"En la franja del medio: el {p}% de las consultoras pronosticó menos que DCM*."
    if p < 10:
        return "extremo", ("Entre los más bajos: ninguna consultora pronosticó menos que DCM*." if p == 0 else
                           f"Entre los más bajos: solo el {p}% de las consultoras pronosticó menos que DCM*.")
    return "extremo", ("Entre los más altos: ninguna consultora pronosticó más que DCM*." if p == 100 else
                       f"Entre los más altos: solo el {100 - p}% de las consultoras pronosticó más que DCM*.")


def historias(t, rem_ultimo):
    """Devuelve (último REM vs nosotros, últimos datos reales vs nosotros y el REM), listos para la portada."""
    # ---- REM nuevo vs nuestras estimaciones
    u = t[t["fecha_del_pronostico"] == str(rem_ultimo)]
    filas = []
    for var, hor in SEL_REM:
        q = u[(u["variable"] == var) & (u["horizonte"] == hor)]
        if not len(q):
            continue
        x = q.iloc[0]
        clave = CLAVE_VAR[var]
        cat_u, txt_u = _ubicacion(x)
        d = x["datos_con_metodo"] - x["rem_mediana"]
        filas.append({"titulo": _titulo(var, hor, x["periodo_pronosticado"]), "clave": clave,
                      "rem": _valor(clave, x["rem_mediana"]), "nuestro": _valor(clave, x["datos_con_metodo"]),
                      "texto": f"DCM*: {_dif(clave, d)} que la mediana del REM. {txt_u}",
                      "ubicacion": cat_u, "rango": _rango(x), "v_nuestro": float(x["datos_con_metodo"]),
                      "consultoras": None if pd.isna(x["consultoras"]) else int(x["consultoras"])})
    rem_blk = None
    if filas:
        n = len(filas)
        dentro = sum(f["ubicacion"] in ("central", "extremo") for f in filas)
        central = sum(f["ubicacion"] == "central" for f in filas)
        k = int(pd.Series([f["consultoras"] for f in filas if f["consultoras"]]).median())
        tipo = u["tipo"].iloc[0]
        # texto de Marcos (24-oct-2026); si alguna estimación queda afuera, lo dice
        titular = (f"El BCRA publicó el REM de {_mes_largo(rem_ultimo)}, con las respuestas de unos {k} participantes. "
                   + (f"Las estimaciones de Datos Con Método quedaron todas entre las pronosticadas por las consultoras ({dentro} de {n})."
                      if dentro == n else
                      f"{dentro} de {n} estimaciones de Datos Con Método quedaron entre las pronosticadas por las consultoras; "
                      f"{n - dentro} quedaron por debajo o por encima de todas."))
        nota = ("NOTA TRANSITORIA: este informe es anterior a que empezáramos a registrar el pronóstico. Nuestros números son una reconstrucción hecha "
                "solo con los datos que se conocían en ese momento. Desde el REM de octubre de 2026, el pronóstico queda registrado y "
                "sellado antes de que el BCRA publique el REM." if tipo.startswith("reconstrucción") else
                "Nuestro pronóstico quedó registrado y sellado antes de que el BCRA publicara el REM.")
        rem_blk = {"relevamiento": str(rem_ultimo), "titular": titular, "nota": nota, "filas": filas}
    # ---- datos reales nuevos vs nuestras predicciones y las del REM
    mes_actual = str(pd.Timestamp.now().to_period("M"))
    reales = []
    for var in SEL_REAL:
        q = t[(t["variable"] == var) & t["real"].notna()].copy()
        per = q["periodo_pronosticado"].astype(str)
        mensual = per.str.fullmatch(r"\d{4}-\d{2}")
        if var != "PIB trimestral (s.e.)":
            q = q[mensual & (per < mes_actual)]          # solo meses cerrados (el promedio del mes en curso todavía no es el dato)
        if not len(q):
            continue
        ultimo = q["periodo_pronosticado"].astype(str).max()
        q = q[q["periodo_pronosticado"].astype(str) == ultimo].sort_values("fecha_del_pronostico")
        x = q.iloc[-1]                                     # el pronóstico más reciente antes del dato
        clave = CLAVE_VAR[var]
        e_n, e_r = abs(x["datos_con_metodo"] - x["real"]), abs(x["rem_mediana"] - x["real"])
        tol = {"inflacion": 0.005, "pib": 0.005, "tasa": 0.005, "dolar": 0.5}.get(clave, 0.5)
        gana = "empate" if abs(e_n - e_r) < tol else ("nosotros" if e_n < e_r else "rem")
        frase = {"nosotros": "Estuvo más cerca DCM*.", "rem": "Estuvo más cerca el REM.", "empate": "Empate: DCM* y el REM quedaron a la misma distancia."}[gana]
        extra = ""
        if not pd.isna(x["mas_cerca_del_real_que_el_pct_de_consultoras"]):
            pu = str(x["puesto_estimado_por_acierto"]).replace("≈ ", "")
            extra = (f" El pronóstico de DCM* quedó más cerca del dato que el {int(x['mas_cerca_del_real_que_el_pct_de_consultoras'])}% "
                     f"de las consultoras (puesto aproximado: {pu}).")
        reales.append({"titulo": _titulo(var, x["horizonte"], ultimo), "clave": clave, "fuente": FUENTE_REAL[clave],
                       "real": _valor(clave, x["real"]), "rem": _valor(clave, x["rem_mediana"]), "nuestro": _valor(clave, x["datos_con_metodo"]),
                       "gana": gana, "texto": frase + extra,
                       "cuando": f"Pronósticos del REM de {_mes_largo(x['fecha_del_pronostico'])}"
                                 + (" (reconstrucción)" if str(x["tipo"]).startswith("reconstrucción") else ""),
                       "rango": _rango(x), "v_nuestro": float(x["datos_con_metodo"]), "v_real": float(x["real"])})
    real_blk = None
    if reales:
        a = sum(r["gana"] == "rem" for r in reales)
        b = sum(r["gana"] == "nosotros" for r in reales)
        real_blk = {"titular": f"De las {len(reales)} variables con dato nuevo, Datos Con Método estuvo más cerca en {b} y el REM en {a}"
                               + (f" ({len(reales) - a - b} empate)" if len(reales) - a - b == 1 else
                                  f" ({len(reales) - a - b} empates)" if len(reales) - a - b > 1 else "") + "."
                               # cierre de Marcos (24-oct-2026), cuando el REM estuvo más cerca en más variables
                               + (" Hay espacio para mejorar." if a > b else ""),
                    "filas": reales}
    return rem_blk, real_blk


# ---------------------------------------------------------------- 5h) pronósticos del mes en curso (pedido de Marcos, 24-oct-2026)
# Una caja por variable del REM, en el orden de "Variables de hoy". Inflación, PIB, resultado primario y comercio: el primer
# período de nuestro pronóstico registrado. Dólar y TAMAR (el REM no pide el mes en curso): estimación nueva del promedio
# del mes = días publicados + días hábiles que faltan (dólar: en línea recta hasta el futuro de fin de mes del CEM;
# TAMAR: igual a la última tasa publicada). Modelos sin verificar: la página lo advierte.
def _promedio_mes(serie, hoy):
    b = pd.read_parquet(C / "bcra_series.parquet", columns=["fecha", "serie", "valor"])
    b = b[b["serie"] == serie].dropna(subset=["valor"])
    b["fecha"] = pd.to_datetime(b["fecha"])
    mes = hoy.to_period("M")
    m = b[b["fecha"].dt.to_period("M") == mes].sort_values("fecha")
    if not len(m):
        return None
    ultimo = m["fecha"].iloc[-1]
    faltan = pd.bdate_range(ultimo + pd.Timedelta(days=1), mes.end_time.normalize())
    return m, ultimo, faltan


def mes_en_curso(tarjetas):
    hoy = pd.Timestamp.now().normalize()
    mes = str(hoy.to_period("M"))
    nombre_mes = _mes_largo(mes).replace(" de ", " ").capitalize()
    P = {t["clave"]: t for t in tarjetas}
    out = []

    def primera(clave, per=None):
        t = P.get(clave)
        if not t:
            return None
        for f in t["filas"]:
            if f["nuestro"] is not None and not pd.isna(f["nuestro"]) and (per is None or str(f["periodo"]) == per):
                return f
        return None

    f = primera("inflacion", mes)
    if f:
        out.append({"clave": "inflacion", "titulo": "Inflación", "periodo": nombre_mes, "valor": _num(f["nuestro"], 2) + "%",
                    "unidad": "mensual (IPC nacional)", "nota": "Incluye los precios de supermercados del mes en curso."})
    # dólar: promedio del mes en curso
    r = _promedio_mes("tc_mayorista_a3500", hoy)
    if r:
        m, ultimo, faltan = r
        obs = list(m["valor"])
        nota = f"Promedio de {len(obs)} días con dato (hasta el {ultimo:%d/%m})"
        if len(faltan):
            f_ = pd.read_parquet(C / "dolar_futuro_cem.parquet", columns=["symbol", "settlement", "fecha", "optionType"])
            simbolo = f"DLR{hoy:%m%Y}"
            f_ = f_[(f_["symbol"] == simbolo) & (f_["optionType"].isna() | (f_["optionType"] == ""))].sort_values("fecha")
            if len(f_):
                fin = float(f_["settlement"].iloc[-1])
                paso = (fin - obs[-1]) / len(faltan)
                obs += [obs[-1] + paso * (i + 1) for i in range(len(faltan))]
                nota += f" y {len(faltan)} días hábiles que faltan, en línea recta hasta el dólar futuro de fin de mes (${_num(fin, 0)})."
            else:
                obs += [obs[-1]] * len(faltan)
                nota += f" y {len(faltan)} días hábiles que faltan, iguales al último dato."
        else:
            nota += "."
        out.append({"clave": "dolar", "titulo": "Dólar mayorista (A3500)", "periodo": nombre_mes, "valor": "$" + _num(sum(obs) / len(obs), 2),
                    "unidad": "$ por USD, promedio del mes", "nota": nota})
    f = primera("pib")
    if f:
        q = str(f["periodo"])
        out.append({"clave": "pib", "titulo": "PIB trimestral", "periodo": q.replace("Q", " T"), "valor": _num(f["nuestro"], 2) + "%",
                    "unidad": "contra el trimestre anterior, s.e.", "nota": "Último trimestre todavía sin dato oficial del INDEC."})
    # precios en supermercados, proyectado a fin de mes: los días que faltan siguen el ritmo de las últimas dos semanas
    # (pendiente diaria en logaritmos); variación = promedio del mes completo contra el promedio del mes anterior
    try:
        d = pd.read_parquet(C / "sepa_indice_diario.parquet")
        d = d[(d["region"] == "Nacional") & (d["nivel"] == "total")].copy()
        d["fecha"] = pd.to_datetime(d["fecha"])
        d = d.set_index("fecha")["indice"].sort_index()
        p_m = hoy.to_period("M")
        act, ant = d[d.index.to_period("M") == p_m], d[d.index.to_period("M") == p_m - 1]
        if len(act) >= 7 and len(ant) >= 15:
            u14 = act[act.index > act.index[-1] - pd.Timedelta(days=14)]
            x = (u14.index - u14.index[0]).days.values.astype(float)
            pend = float(np.polyfit(x, np.log(u14.values), 1)[0]) if len(u14) >= 5 else 0.0
            faltan = pd.date_range(act.index[-1] + pd.Timedelta(days=1), p_m.end_time.normalize(), freq="D")
            proy = [float(act.iloc[-1]) * np.exp(pend * (i + 1)) for i in range(len(faltan))]
            var = (np.mean(list(act.values) + proy) / ant.mean() - 1) * 100
            out.append({"clave": "super", "titulo": "Precios en supermercados", "periodo": nombre_mes, "valor": _num(var, 2) + "%",
                        "unidad": "en el mes, proyectado a fin de mes",
                        "nota": f"Promedio del mes contra el del mes anterior (SEPA). Datos hasta el {act.index[-1]:%d/%m}; los "
                                f"{len(faltan)} días que faltan siguen el ritmo de las últimas dos semanas "
                                f"({_num((np.exp(pend * 7) - 1) * 100, 2)}% por semana)."})
    except Exception:
        pass
    f = primera("fiscal")
    if f:
        out.append({"clave": "fiscal", "titulo": "Resultado primario", "periodo": f"Año {f['periodo']}",
                    "valor": ("−B$ " if f["nuestro"] < 0 else "B$ ") + _num(abs(f["nuestro"]), 1), "unidad": "billones de $",
                    "nota": "El REM pide el año completo, no el mes."})
    r = _promedio_mes("tamar_tna", hoy)
    if r:
        m, ultimo, faltan = r
        obs = list(m["valor"]) + [float(m["valor"].iloc[-1])] * len(faltan)
        nota = (f"Promedio de {len(m)} días con dato (hasta el {ultimo:%d/%m})"
                + (f" y {len(faltan)} días hábiles que faltan, iguales a la última tasa ({_num(m['valor'].iloc[-1], 2)}%)." if len(faltan) else "."))
        out.append({"clave": "tamar", "titulo": "Tasa TAMAR", "periodo": nombre_mes, "valor": _num(sum(obs) / len(obs), 2) + "%",
                    "unidad": "TNA, promedio del mes", "nota": nota})
    for clave, tit in (("exportaciones", "Exportaciones"), ("importaciones", "Importaciones")):
        f = primera(clave, mes)
        if f:
            out.append({"clave": clave, "titulo": tit, "periodo": nombre_mes, "valor": "US$ " + _num(f["nuestro"], 0) + " M",
                        "unidad": "millones de USD", "nota": ""})
    return out


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
               "pronosticos": pronosticos(), "historial": historial(), "indicadores": indicadores(),
               "conteo": {"series": len(cat), "datos": int(cat["n"].sum()), "fuentes": cat["fuente"].nunique(),
                          "series_al_dia": int((cat["estado"] == "verde").sum()), "series_vigentes": int((cat["estado"] != "gris").sum()),
                          "series_historicas": int((cat["estado"] == "gris").sum())}}
    fichas = tablas(NUEVO / "descargas")
    crudos = sepa_crudos()
    canasta = canasta_ultimo_anio(NUEVO / "descargas" / "canasta")
    fu, cat = fuentes(cat, fichas, canasta, crudos)
    resumen["sellos"] = sellos(NUEVO / "sellos")
    resumen["semaforo_datos"] = semaforo_datos(cat)
    resumen["mes_en_curso"] = mes_en_curso(resumen["pronosticos"])
    resumen["ultimo_rem"], resumen["ultimos_reales"] = historias(tabla_comparacion(), resumen["rem_ultimo"])
    sd = resumen["semaforo_datos"]
    L.append(f"Semáforo de datos: {sd['color'].upper()} ({sd['al_dia']} de {sd['total']} datos de los pronósticos al día, {sd['pct']}%)"
             + (f"; atrasados: {', '.join(sd['atrasados'])}" if sd["atrasados"] else ""))
    guardar_json(data / "resumen.json", resumen)
    if not completar_index(resumen):
        L.append("Aviso W03: no se pudo generar la imagen de vista previa (vista_previa.png); el sitio se publica sin ella.")
    guardar_json(data / "fuentes.json", fu)
    guardar_json(data / "catalogo.json", cat.drop(columns=["origen"], errors="ignore").to_dict("records"))
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
        # informativo (W01): pasa cuando la VM o el explorador tienen abierta la carpeta; el sitio queda igual de completo
        L.append(f"Nota W01: la carpeta estaba en uso ({type(e).__name__}); se copió encima y se borró lo que sobraba")
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
    alertas += ALERTAS_GLOBALES
    L.append("ALERTAS: " + ("; ".join(alertas) if alertas else "ninguna. Todo en orden."))
    texto = "\n".join(L)
    print(texto)
    (C / "informe_web.txt").write_text(texto, encoding="utf-8")
    if aviso and alertas:                                  # cartel de Windows (p. ej. W02: A3 publicó términos de uso)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "\n\n".join(alertas)[:900], "Datos Con Método: sitio web", 0x30)
        except Exception:
            pass
    sys.exit(1 if alertas else 0)


if __name__ == "__main__":
    main()
