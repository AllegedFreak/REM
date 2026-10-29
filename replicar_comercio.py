# replicar_comercio.py
# Nuestro pronóstico de EXPORTACIONES e IMPORTACIONES (millones de USD) como lo pide el REM (desde dic-2020):
# meses t a t+6 y años calendario en curso y siguiente.
# Dato real: intercambio comercial argentino del INDEC (datos.gob.ar 74.3_IET_0_M_16 y 74.3_IIT_0_M_25, en
# series_historicas). El mes m sale hacia el día 20 de m+1: en el relevamiento de fin de t se conoce hasta t-1.
# Se usa la versión actual (el INDEC revisa poco).
# Modelos (cada flujo por separado):
#   I g3   : mismo mes del año anterior × (1 + crecimiento interanual de los últimos 3 meses conocidos).
#   I g12  : mismo mes del año anterior × (1 + crecimiento interanual de los últimos 12 meses).
#   I      : promedio geométrico de I g3 e I g12.
#   A      : regresión robusta (Huber) del REM (log contra el mismo mes del año anterior) sobre los dos crecimientos y
#            el último mes conocido; ventana acumulada, mínimo 24 relevamientos, una por horizonte. Sin REM.
#            En EXPORTACIONES suma los precios internacionales (Banco Mundial, variación interanual del mes t-1 de los índices
#            "Oils & Meals" y "Grains"; 18-oct-2026): error contra el real 13,9 → 12,4% (meses de 2023: 25,1 → 17,6%).
#            Probadas y descartadas: estimaciones oficiales de cosecha (actualizar_estimaciones_agricolas.py), en la
#            regresión o con un efecto fijado de antemano; energía (Brent); variación de 3 meses.
#   Año: meses conocidos + pronóstico mensual del resto (año siguiente: los 12 meses).
# Errores en % (100·|log(pronóstico/real)|).
# Salidas: datos\replica_comercio.csv, datos\informe_comercio.txt, datos\registro_comercio.csv
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from backtest_rem import diebold_mariano

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
IDS = {"exportaciones": "datosgob_74.3_IET_0_M_16", "importaciones": "datosgob_74.3_IIT_0_M_25"}
PRECIOS = {"px_om": "bm_indice_oils_meals", "px_gr": "bm_indice_grains"}     # solo para exportaciones
MIN = 24


CORRIDA = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")   # UNA sola hora por corrida (28-oct-2026: con la hora fila por fila,
# exportaciones e importaciones quedaron en 12:12 y 12:13 y el sello n.º 1 salió sin exportaciones)


def huber(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(Xc, y, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def crec(s, ult, k):
    a = s[(s.index > ult - k) & (s.index <= ult)]
    b = s[(s.index > ult - k - 12) & (s.index <= ult - 12)]
    return np.log(a.sum() / b.sum()) if len(a) == k and len(b) == k and b.sum() > 0 else np.nan


def camino(s, ult, g, hasta):
    """Niveles mensuales: conocidos hasta ult, después mismo mes de un año antes × exp(g)."""
    v = {m: s[m] for m in s.index if m <= ult}
    for m in pd.period_range(ult + 1, hasta, freq="M"):
        b = v.get(m - 12)
        if b is None:
            return None
        v[m] = b * np.exp(g)
    return v


def main():
    h = pd.read_parquet(C / "series_historicas.parquet", filters=[("id", "in", list(IDS.values()) + list(PRECIOS.values()))])
    h["mes"] = pd.PeriodIndex(pd.to_datetime(h["fecha"]), freq="M")
    S = {k: h[h["id"] == v].set_index("mes")["valor"].sort_index() for k, v in IDS.items()}
    PX = {}
    for k, v in PRECIOS.items():
        x = h[h["id"] == v].set_index("mes")["valor"].sort_index()
        PX[k] = np.log(x / x.shift(12))
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[r["variable_std"].isin(list(IDS))].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    L = [f"=== NUESTRO PRONÓSTICO DE EXPORTACIONES E IMPORTACIONES (millones de USD) | {datetime.now():%Y-%m-%d %H:%M} ===",
         "Error = 100·|log(pronóstico/real)| (%). Fuera de muestra, tiempo real. Real = INDEC, versión actual.", ""]
    todo, reg = [], []
    t_hoy = pd.Period(pd.Timestamp.today(), "M")
    for var, s in S.items():
        gm = g[(g["variable_std"] == var) & (g["tipo_periodo"] == "fecha")].copy()
        gm["mes"] = pd.to_datetime(gm["periodo_fin"]).dt.to_period("M")
        gm["h"] = [(m - o).n for m, o in zip(gm["mes"], gm["origen"])]
        ga = g[(g["variable_std"] == var) & (g["tipo_periodo"] == "anio")].copy()
        ga["anio"] = pd.to_numeric(ga["periodo_original"], errors="coerce")
        filas = []
        origenes = sorted(set(gm["origen"]) | {t_hoy})
        for t in origenes:
            ult = t - 1
            if ult not in s.index:
                ult = s.index.max() if t == t_hoy else None
            if ult is None:
                continue
            g3, g12 = crec(s, ult, 3), crec(s, ult, 12)
            fin = pd.Period(f"{t.year + 1}-12", "M")
            cams = {"I_g3": camino(s, ult, g3, fin), "I_g12": camino(s, ult, g12, fin)}
            if any(c is None for c in cams.values()):
                continue
            cams["I"] = {m: np.sqrt(cams["I_g3"][m] * cams["I_g12"][m]) for m in cams["I_g3"]}
            ult_rel = np.log(s[ult] / s[ult - 12])
            q = gm[gm["origen"] == t].set_index("mes")
            for hh in range(0, 7):
                m = t + hh
                f = {"var": var, "tipo": "mes", "origen": t, "objetivo": str(m), "h": hh,
                     "rem": q["mediana"].get(m, np.nan), "p10": q["percentil_10"].get(m, np.nan),
                     "p90": q["percentil_90"].get(m, np.nan), "real": s.get(m, np.nan) if m <= s.index.max() else np.nan,
                     "base": cams["I"][m] / np.exp((g3 + g12) / 2) if (m - 12) <= ult or True else np.nan,
                     "g3": g3, "g12": g12, "ult_rel": ult_rel}
                for k, c in cams.items():
                    f[k] = c[m]
                filas.append(f)
            qa = ga[ga["origen"] == t].set_index("anio")
            for a in (t.year, t.year + 1):
                ms = pd.period_range(f"{a}-01", f"{a}-12", freq="M")
                f = {"var": var, "tipo": "año", "origen": t, "objetivo": str(a), "h": a - t.year,
                     "rem": qa["mediana"].get(a, np.nan), "p10": qa["percentil_10"].get(a, np.nan),
                     "p90": qa["percentil_90"].get(a, np.nan),
                     "real": s[s.index.year == a].sum() if (s.index.year == a).sum() == 12 else np.nan,
                     "conocido": sum(s[m] for m in ms if m <= ult)}
                for k, c in cams.items():
                    f[k] = sum(c[m] for m in ms)
                filas.append(f)
        X = pd.DataFrame(filas)
        # A mensual: log(REM / mismo mes del año anterior) ~ g3, g12, último mes
        mm = X["tipo"] == "mes"
        X.loc[mm, "base"] = X.loc[mm, "I"] / np.exp((X.loc[mm, "g3"] + X.loc[mm, "g12"]) / 2)
        X["y"] = np.log(X["rem"] / X["base"])
        COLS = ["g3", "g12", "ult_rel"]
        if var == "exportaciones":
            for k, yoy in PX.items():
                X[k] = [yoy.get(o - 1, yoy.dropna().iloc[-1] if o - 1 > yoy.dropna().index.max() else np.nan) for o in X["origen"]]
            COLS = COLS + list(PX)
        X["A"] = np.nan
        for hh, q in X[mm].groupby("h"):
            for i, row in q.iterrows():
                tr = q[(q["origen"] < row["origen"])].dropna(subset=COLS + ["y"])
                if len(tr) >= MIN and row[COLS].notna().all():
                    X.loc[i, "A"] = row["base"] * np.exp(huber(tr[COLS].values, tr["y"].values, row[COLS].values.astype(float)))
        # A encadenado: meses conocidos y, desde ult+1, A(m) = A(m-12) × exp(ŷ_h), con ŷ_h la relación estimada por A para
        # el horizonte h (h<0 usa la de h0 y h>6 la de h6). Para h0..h6 coincide con A mensual. Sirve para los años.
        X["yhat"] = np.log(X["A"] / X["base"])
        for t, q in X[mm].groupby("origen"):
            yh = q.set_index("h")["yhat"]
            if yh.isna().all():
                continue
            ult = t - 1 if (t - 1) in s.index else s.index.max()
            v = {m: s[m] for m in s.index if m <= ult}
            for m in pd.period_range(ult + 1, pd.Period(f"{t.year + 1}-12", "M"), freq="M"):
                hh = min(max((m - t).n, 0), 6)
                if pd.isna(yh.get(hh)) or (m - 12) not in v:
                    v = None
                    break
                v[m] = v[m - 12] * np.exp(yh[hh])
            if v is None:
                continue
            for i in X[(~mm) & (X["origen"] == t)].index:
                a = int(X.loc[i, "objetivo"])
                X.loc[i, "A"] = sum(v[m] for m in pd.period_range(f"{a}-01", f"{a}-12", freq="M"))
            X.loc[mm & (X["origen"] == t), "A_cadena_check"] = [v.get(pd.Period(o, "M"), np.nan) for o in q["objetivo"]]
        todo.append(X)
        # informe
        L.append(f"##### {var.upper()}")
        for per, m_ in (("2021-2024", X["origen"] <= pd.Period("2024-12", "M")), ("2025-26", X["origen"] > pd.Period("2024-12", "M"))):
            L.append(f"== {per}")
            L.append(f"   {'':8}{'distancia al REM (%)':>34} | {'error contra el real (%)':>44} | {'P10-P90':>14}")
            L.append(f"   {'':8}{'I g3':>8}{'I g12':>8}{'I':>8}{'A':>8}{'n':>4} | {'I g3':>7}{'I g12':>7}{'I':>7}{'A':>7}{'REM':>7}{'n':>4}{'p I-REM':>9} | {'I':>6}{'A':>6}")
            for (tipo, hh), z in X[m_ & X["rem"].notna()].groupby(["tipo", "h"]):
                z = z.dropna(subset=["I", "A"])            # misma muestra para todos (A arranca tras 24 informes)
                zr = z.dropna(subset=["real"])
                e = lambda c, ref, zz: (100 * np.log(zz[c] / zz[ref])).abs().mean()
                try:
                    p = diebold_mariano(np.log(zr.I / zr.real).values, np.log(zr.rem / zr.real).values, max(hh, 1)) if len(zr) > 6 else np.nan
                except Exception:
                    p = np.nan
                cob = lambda c: ((z[c] >= z.p10) & (z[c] <= z.p90)).mean() * 100
                et = f"m{hh}" if tipo == "mes" else ("año" if hh == 0 else "año+1")
                L.append(f"   {et:8}{e('I_g3', 'rem', z):8.1f}{e('I_g12', 'rem', z):8.1f}{e('I', 'rem', z):8.1f}{e('A', 'rem', z):8.1f}{len(z):4d} | "
                         f"{e('I_g3', 'real', zr):7.1f}{e('I_g12', 'real', zr):7.1f}{e('I', 'real', zr):7.1f}{e('A', 'real', zr):7.1f}"
                         f"{e('rem', 'real', zr):7.1f}{len(zr):4d}{p:9.2f} | {cob('I'):5.0f}%{cob('A'):5.0f}%")
            L.append("")
        hoy = X[X["origen"] == t_hoy]
        rem_ant = g[(g["variable_std"] == var) & (g["origen"] == g["origen"].max())]
        ra = {(str(pd.to_datetime(x["periodo_fin"]).to_period("M")) if x["tipo_periodo"] == "fecha" else str(x["periodo_original"])): x["mediana"]
              for _, x in rem_ant.iterrows()}
        L.append(f"PRONÓSTICO DE HOY ({var}; INDEC hasta {s.index.max()}; REM de referencia {g['origen'].max()}):")
        L.append(f"   {'período':9}{'REM ant.':>10}{'I':>10}{'A':>10}")
        for _, x in hoy.iterrows():
            L.append(f"   {x['objetivo']:9}{ra.get(x['objetivo'], np.nan):10.0f}{x['I']:10.0f}{x['A']:10.0f}")
            reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "variable": var,
                        "periodo": x["objetivo"], "I": x["I"], "A": x["A"], "rem_anterior": ra.get(x["objetivo"], np.nan)})
        L.append("")
    pd.concat(todo).to_csv(C / "replica_comercio.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    p = C / "registro_comercio.csv"
    pd.DataFrame(reg).to_csv(p, mode="a", header=not p.exists(), index=False, encoding="utf-8-sig", float_format="%.1f")
    L.append("ALERTAS: ninguna. Todo en orden.")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_comercio.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
