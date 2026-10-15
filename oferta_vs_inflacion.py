# oferta_vs_inflacion.py
# ¿El crecimiento de la oferta monetaria anticipa la inflación?
#   A) Versión simple tipo "regresión de 2016": variación de la base contra variación del índice, mismo mes,
#      en pesos (millones de $ contra puntos del índice) y en % (mensual e interanual)
#   B) Rezagos de 1 a 12 meses: inflación del mes t contra crecimiento monetario del mes t-L
#      - agregados: base monetaria, billetes y monedas en poder del público, M2 en pesos (promedio mensual de la diaria)
#      - crecimiento mensual y de 3 meses, DESESTACIONALIZADO (el aguinaldo infla diciembre y julio)
#      - dos regresiones por rezago: simple (inflación ~ dinero) y con la inflación del mes anterior como control
#        (¿el dinero agrega algo a lo que ya dice la inercia?)
#      - por etapas: 2012-08 a 2016-12, 2017-01 a 2023-12, 2024-01 en adelante, y todo el período
#      - inflación general con el tramo IPCBA (serie encadenada) y sin él (solo INDEC, desde 2017); núcleo (INDEC)
# Salidas: datos/informe_oferta_monetaria.txt y datos/oferta_inflacion_rezagos.csv (tabla completa)
# Uso: py -3.13 oferta_vs_inflacion.py
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.seasonal import seasonal_decompose

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

AGREGADOS = {"base_monetaria": "Base monetaria", "billetes_publico": "Billetes y monedas", "m2_pesos": "M2 en pesos"}
INFLACIONES = {"general_con": "IPC general, con tramo IPCBA", "general_sin": "IPC general, solo INDEC",
               "nucleo": "IPC núcleo (INDEC)"}
ETAPAS = [("2012-08 a 2016-12", "2012-08", "2016-12"), ("2017-01 a 2023-12", "2017-01", "2023-12"),
          ("2024-01 en adelante", "2024-01", "2100-12"), ("Todo", "2012-08", "2100-12")]
REZAGOS = range(1, 13)
DESDE_DESESTAC = "2004-01"   # se estima la estacionalidad desde acá (evita la crisis de 2002)
LAGS_HAC = 6                 # errores estándar robustos (Newey-West)

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
INFORME = CARPETA / "informe_oferta_monetaria.txt"
TABLA = CARPETA / "oferta_inflacion_rezagos.csv"


def mensual_por_periodo(df, col_mes):
    df = df.copy()
    df["periodo"] = pd.to_datetime(df[col_mes]).dt.to_period("M")
    return df.set_index("periodo")


def regresion(y, X):
    datos = pd.concat([y, X], axis=1).dropna()
    if len(datos) < 20:
        return None
    mod = sm.OLS(datos.iloc[:, 0], sm.add_constant(datos.iloc[:, 1:])).fit(cov_type="HAC", cov_kwds={"maxlags": LAGS_HAC})
    return mod, len(datos)


def main():
    # ---------- datos ----------
    m = pd.read_parquet(CARPETA / "bcra_series_mensual.parquet")
    m = m[m["serie"].isin(AGREGADOS) & (m["dias"] >= 15)]          # descarta meses incompletos
    nivel = mensual_por_periodo(m, "mes").reset_index().pivot(index="periodo", columns="serie", values="promedio")
    p = mensual_por_periodo(pd.read_parquet(CARPETA / "precios_encadenada.parquet"), "mes")
    ipc = mensual_por_periodo(pd.read_parquet(CARPETA / "ipc_indec.parquet"), "mes")
    infl = pd.DataFrame({"general_con": p["var_mensual"], "general_sin": p["var_indec"],
                         "nucleo": ipc["ipc_nucleo_indice_var_mensual"]})
    ultimo = infl["general_con"].last_valid_index()
    nivel = nivel[nivel.index <= ultimo]

    # crecimiento desestacionalizado (en % aproximado: 100 x diferencia de logaritmos)
    g1, g3, sin_desest, factores = {}, {}, {}, {}
    for s in AGREGADOS:
        lg = np.log(nivel[s].dropna())
        lg = lg[lg.index >= pd.Period(DESDE_DESESTAC, "M")]
        estac = seasonal_decompose(pd.Series(lg.values, index=lg.index.to_timestamp()), model="additive", period=12).seasonal
        estac.index = lg.index
        sa = lg - estac
        g1[s] = sa.diff() * 100
        g3[s] = (sa - sa.shift(3)) * 100
        sin_desest[s] = lg.diff() * 100
        factores[s] = estac.groupby(estac.index.month).first() * 100

    # ---------- A) regresión simple tipo 2016 (mismo mes) ----------
    L = ["=== OFERTA MONETARIA E INFLACIÓN ===", f"Datos hasta {ultimo}. Agregados: promedio mensual de la serie diaria del BCRA.", ""]
    L.append("Estacionalidad estimada (efecto del mes sobre el nivel, en %; se quita antes de medir el crecimiento):")
    for s, nombre in AGREGADOS.items():
        L.append(f"   {nombre:20} " + " ".join(f"{mes}:{v:+.1f}" for mes, v in factores[s].items()))
    L.append("")
    L.append("A) Versión simple tipo 2016: variación de la base contra variación del índice, MISMO mes (con tramo IPCBA)")
    indice = p["indice"]
    base = nivel["base_monetaria"]
    variantes = {
        "en pesos (Δ base en millones de $ vs Δ índice en puntos)": (indice.diff(), base.diff()),
        "en % mensual, sin desestacionalizar": (infl["general_con"], sin_desest["base_monetaria"]),
        "en % mensual, desestacionalizada": (infl["general_con"], g1["base_monetaria"]),
        "en % interanual": ((indice / indice.shift(12) - 1) * 100, (base / base.shift(12) - 1) * 100),
    }
    L.append(f"   {'variante':58} {'etapa':20} {'meses':>5} {'pendiente':>10} {'t':>6} {'R²':>5}")
    for nombre, (yv, xv) in variantes.items():
        for et, d0, d1 in ETAPAS:
            idx = (yv.index >= pd.Period(d0, "M")) & (yv.index <= pd.Period(d1, "M"))
            r = regresion(yv[idx], xv.rename("x"))
            if r:
                mod, nn = r
                L.append(f"   {nombre:58} {et:20} {nn:5} {mod.params['x']:10.4g} {mod.tvalues['x']:6.1f} {mod.rsquared:5.2f}")
        nombre = ""
    L.append("")

    # ---------- B) rezagos ----------
    filas = []
    for yk in INFLACIONES:
        y = infl[yk]
        for s in AGREGADOS:
            for tipo, g in (("mensual", g1[s]), ("3 meses", g3[s])):
                for et, d0, d1 in ETAPAS:
                    idx = (y.index >= pd.Period(d0, "M")) & (y.index <= pd.Period(d1, "M"))
                    ye = y[idx]
                    for lag in REZAGOS:
                        x = g.shift(lag).rename("dinero")
                        r1 = regresion(ye, x)
                        r2 = regresion(ye, pd.concat([y.shift(1).rename("infl_ant"), x], axis=1))
                        if not r1 or not r2:
                            continue
                        filas.append({"inflacion": yk, "agregado": s, "crecimiento": tipo, "etapa": et, "rezago": lag,
                                      "meses": r1[1], "b_simple": r1[0].params["dinero"], "t_simple": r1[0].tvalues["dinero"],
                                      "r2_simple": r1[0].rsquared, "b_con_inercia": r2[0].params["dinero"],
                                      "t_con_inercia": r2[0].tvalues["dinero"], "inercia": r2[0].params["infl_ant"]})
    t = pd.DataFrame(filas)
    t.to_csv(TABLA, index=False, encoding="utf-8-sig", float_format="%.4f")

    L.append("B) Rezagos de 1 a 12 meses: inflación del mes t contra crecimiento monetario desestacionalizado del mes t-L")
    L.append("   'con inercia' = controlando por la inflación del mes anterior. Significativo: |t| > 2 (≈ 5%).")
    L.append("   De 12 rezagos, por azar se esperan ~0,6 significativos: 1 aislado no prueba nada.")
    for yk, ynom in INFLACIONES.items():
        for tipo in ("mensual", "3 meses"):
            L.append("")
            L.append(f"   --- {ynom} | crecimiento {tipo} ---")
            L.append(f"   {'agregado':20} {'etapa':20} {'meses':>5} | {'mejor rezago (simple)':>22} {'R²':>5} | "
                     f"{'mejor con inercia':>18} {'t':>5} | rezagos signif. con inercia (+ / -)")
            for s, snom in AGREGADOS.items():
                for et, _, _ in ETAPAS:
                    q = t[(t.inflacion == yk) & (t.agregado == s) & (t.crecimiento == tipo) & (t.etapa == et)]
                    if q.empty:
                        continue
                    a = q.loc[q.r2_simple.idxmax()]
                    b = q.loc[q.t_con_inercia.idxmax()]
                    pos = list(q.loc[q.t_con_inercia > 2, "rezago"])
                    neg = list(q.loc[q.t_con_inercia < -2, "rezago"])
                    L.append(f"   {snom:20} {et:20} {int(q.meses.max()):5} | L={int(a.rezago):2} b={a.b_simple:+.3f} {'':8}{a.r2_simple:5.2f} | "
                             f"L={int(b.rezago):2} b={b.b_con_inercia:+.3f} {b.t_con_inercia:5.1f} | "
                             f"{pos if pos else '-'} / {neg if neg else '-'}")
    L.append("")
    L.append(f"Tabla completa (cada rezago): datos\\{TABLA.name}")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
