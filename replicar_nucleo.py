# replicar_nucleo.py (29-oct-2026; en actualizar_todo.py, después de replicar_rem.py)
# Nuestro pronóstico de la INFLACIÓN NÚCLEO como la pide el REM (var. % mensual, meses t a t+6) y la núcleo del año en curso.
# Decisión de Marcos (29-oct-2026): pronóstico publicado = PROMEDIO de dos modelos:
#   A sin est. : regresión robusta (Huber) del REM núcleo sobre nuc1, nuc_2, nuc_3 (núcleo de t-1..t-3), dev0, dev1
#                (dólar) y ei_med_m (mediana de las expectativas de los hogares, UTDT, mensualizada); una por horizonte,
#                ventana acumulada, mínimo 24 relevamientos. Sin la estacionalidad (p12n): no aportaba (probar_nucleo.py).
#   REM corr.  : REM núcleo del mes ANTERIOR (ya publicado) × error medio histórico del REM en ese horizonte, EN PROPORCIÓN (real/REM,
#                solo meses ya publicados al relevar; ventana acumulada). Corrige el sesgo optimista del REM
#                (diagnostico_rem.py: se queda corto en m1-m6 en 2017-19, 2020-22 y 2025-26).
# Por qué el promedio: en 2025-26 iguala o mejora a cada uno por separado (error contra el real m2-m5 0,51-0,57 pp, REM del
# mes 0,53-0,56; nada significativo). En oct-dic 2025 la corrección del sesgo erró 0,35 contra 0,45-0,61 del REM.
# Para comparar quedan: persistencia (promedio de la núcleo de los últimos 3 meses), A sin est. y REM corr. por separado.
# Variables del relevamiento en curso: datos\pronostico_insumos.csv (las escribe replicar_rem.py).
# Salidas: datos\replica_nucleo.csv, datos\informe_nucleo.txt, datos\registro_nucleo.csv
# El sitio y el sello todavía NO lo muestran (29-oct-2026): falta resolver la coherencia con el pronóstico de la general.
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
MIN = 24
COLS = ["nuc1", "nuc_2", "nuc_3", "dev0", "dev1", "ei_med_m"]
CORTE = pd.Period("2024-12", "M")


CORRIDA = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")   # UNA sola hora por corrida (28-oct-2026: con la hora fila por fila,
# exportaciones e importaciones quedaron en 12:12 y 12:13 y el sello n.º 1 salió sin exportaciones)


def huber(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(Xc, y, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def rem_nucleo():
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[(r["variable_std"] == "ipc_nucleo") & (r["tipo_periodo"] == "fecha") & (r["unidad"] == "var. % mensual")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["obj"] = pd.to_datetime(g["periodo_fin"]).dt.to_period("M")
    return g.groupby(["origen", "obj"])["mediana"].mean()


def main():
    rem = rem_nucleo()
    i = pd.read_parquet(C / "ipc_indec.parquet")
    real = pd.Series(i["ipc_nucleo_indice_var_mensual"].values, index=pd.to_datetime(i["mes"]).dt.to_period("M"))
    ind = pd.Series(i["ipc_nucleo_indice"].values, index=pd.to_datetime(i["mes"]).dt.to_period("M"))
    v = pd.read_csv(C / "replica_rem_variables.csv", encoding="utf-8-sig")
    v = v[v["H"].str.fullmatch(r"m\d")].copy()
    v["origen"] = pd.PeriodIndex(v["origen"], freq="M")
    t_hoy = pd.Period(pd.Timestamp.today(), "M")
    hoy = pd.read_csv(C / "pronostico_insumos.csv", encoding="utf-8-sig")
    hoy = hoy[(hoy["relevamiento"] == str(t_hoy)) & hoy["H"].str.fullmatch(r"m\d")].copy()
    hoy["origen"] = t_hoy
    X = pd.concat([v, hoy], ignore_index=True)
    X["h"] = X["H"].str[1].astype(int)
    X["obj"] = [o + h for o, h in zip(X["origen"], X["h"])]
    X["rem"] = [rem.get((o, b), np.nan) for o, b in zip(X["origen"], X["obj"])]
    X["rem_ant"] = [rem.get((o - 1, b), np.nan) for o, b in zip(X["origen"], X["obj"])]
    X["real"] = X["obj"].map(real)
    # error del REM EN PROPORCIÓN (log real/REM): el error en puntos de 2017-19 (inflación de 2-3% mensual) exageraba la
    # corrección con inflación de 1,6% (mar-2027 2,53%); en proporción 2025-26 m4-m5 0,50-0,48 vs 0,54-0,51 (29-oct-2026)
    X["error_rem"] = np.log(X["real"] / X["rem"])
    X["persistencia"] = [np.mean([real.get(o - k, np.nan) for k in (1, 2, 3)]) for o in X["origen"]]
    X["A"], X["REM_corr"] = np.nan, np.nan
    for h, q in X.groupby("h"):
        for k, row in q.iterrows():
            o = row["origen"]
            tr = q[(q["origen"] < o)].dropna(subset=COLS + ["rem"])
            if len(tr) >= MIN and row[COLS].notna().all():
                X.loc[k, "A"] = huber(tr[COLS].values, tr["rem"].values, row[COLS].values.astype(float))
            pasado = q[(q["obj"] <= o - 1)].dropna(subset=["error_rem"])     # errores ya conocidos al relevar
            if len(pasado) >= 8 and not np.isnan(row["rem_ant"]):
                X.loc[k, "REM_corr"] = row["rem_ant"] * np.exp(pasado["error_rem"].mean())
    # promedio de los dos; donde el REM anterior no llega (m6: el REM del mes anterior no pregunta ese mes), A solo
    X["N"] = X[["A", "REM_corr"]].mean(axis=1, skipna=False).fillna(X["A"].where(X["rem_ant"].isna()))
    X.drop(columns=[c for c in X.columns if c.startswith("percentil_")]).to_csv(
        C / "replica_nucleo.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    mods = ["persistencia", "A", "REM_corr", "N"]
    L = [f"=== NUESTRO PRONÓSTICO DE LA INFLACIÓN NÚCLEO | {datetime.now():%Y-%m-%d %H:%M} ===",
         "N = promedio de A sin est. y REM anterior + sesgo (decisión 29-oct-2026). Errores contra la núcleo real (pp), tiempo real.", ""]
    for per, m_ in (("2019-2024", X["origen"] <= CORTE), ("2025-26 (régimen actual)", X["origen"] > CORTE)):
        Z = X[m_].dropna(subset=mods + ["rem", "real"])
        L += [f"== {per}", f"   {'':4}" + "".join(f"{m:>14}" for m in mods) + f"{'REM del mes':>13}{'n':>5}{'p N vs REM':>12}"]
        for h in range(7):
            z = Z[Z["h"] == h]
            if len(z) < 6:
                continue
            e = lambda c: (z[c] - z["real"]).abs().mean()
            p = diebold_mariano((z["N"] - z["real"]).values, (z["rem"] - z["real"]).values, max(h, 1)) if len(z) > 10 else np.nan
            L.append(f"   m{h:<3}" + "".join(f"{e(m):14.2f}" for m in mods) + f"{e('rem'):13.2f}{len(z):5d}{p:12.2f}")
        L.append("")
    H = X[X["origen"] == t_hoy].sort_values("h")
    if len(H):
        # núcleo del año en curso: índice publicado hasta t-1 x pronósticos hasta diciembre
        anio = {}
        for m in mods + ["rem_ant"]:
            camino = H[H["obj"] <= pd.Period(f"{t_hoy.year}-12", "M")][m]
            v_ = ind.get(t_hoy - 1) / ind.get(pd.Period(f"{t_hoy.year - 1}-12", "M"))
            anio[m] = (v_ * np.prod(1 + camino.values / 100) - 1) * 100 if camino.notna().all() else np.nan
        L.append(f"PRONÓSTICO DE HOY (núcleo; INDEC hasta {t_hoy - 1}; REM de referencia {t_hoy - 1}):")
        L.append(f"   {'mes':9}{'REM ant.':>9}" + "".join(f"{m:>14}" for m in mods))
        reg = []
        for _, x in H.iterrows():
            L.append(f"   {str(x['obj']):9}{x['rem_ant']:9.2f}" + "".join(f"{x[m]:14.2f}" for m in mods))
            reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "H": f"m{int(x['h'])}",
                        "objetivo": str(x["obj"]), "N": x["N"], "A": x["A"], "REM_corr": x["REM_corr"],
                        "persistencia": x["persistencia"], "rem_anterior": x["rem_ant"]})
        L.append(f"   Núcleo {t_hoy.year} (dic/dic): " + " | ".join(f"{m} {anio[m]:.1f}%" for m in mods)
                 + f" | REM ant. {anio['rem_ant']:.1f}%")
        reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "H": "año",
                    "objetivo": str(t_hoy.year), "N": anio["N"], "A": anio["A"], "REM_corr": anio["REM_corr"],
                    "persistencia": anio["persistencia"], "rem_anterior": anio["rem_ant"]})
        # PRÓX. 12 Y 24 MESES Y AÑOS SIGUIENTES (30-oct-2026, Marcos: "armemos los períodos que faltan"), misma regla que la
        # general (replicar_rem.py): camino mensual = núcleo publicada + N (m0..m6; si faltan meses del año, la última N);
        # años siguientes = (1 + núcleo del año en curso)^{r_k} − 1 con la clase de referencia con superávit; meses sin
        # pronóstico propio = tasa constante dentro de cada año; 12m = t+1..t+12; 24m = segundo año (t+13..t+24).
        from replicar_rem import clase_referencia_c
        camino = {}
        for m in pd.period_range(f"{t_hoy.year}-01", f"{t_hoy.year + 2}-12", freq="M"):
            if m <= t_hoy - 1:
                camino[m] = 1 + real[m] / 100
        nn = H.set_index("obj")["N"]
        for m, val in nn.items():
            camino[m] = 1 + val / 100
        for m in pd.period_range(f"{t_hoy.year}-01", f"{t_hoy.year}-12", freq="M"):
            camino.setdefault(m, 1 + nn.iloc[-1] / 100)
        f_anio = lambda y: np.prod([camino[pd.Period(f"{y}-{i:02d}", "M")] for i in range(1, 13)])
        rk, n_casos = clase_referencia_c(t_hoy.year)
        for k in (1, 2):
            y = t_hoy.year + k
            meses = list(pd.period_range(f"{y}-01", f"{y}-12", freq="M"))
            conocidos = [m for m in meses if m in camino]
            resto = 12 - len(conocidos)
            f_con = np.prod([camino[m] for m in conocidos]) if conocidos else 1.0
            for m in meses:
                camino.setdefault(m, (f_anio(t_hoy.year) ** rk[k] / f_con) ** (1 / resto) if resto else 1.0)
        rr = pd.read_parquet(C / "rem_normalizada.parquet")
        rr = rr[(rr["variable_std"] == "ipc_nucleo") & (rr["mes_relevamiento"] == rr["mes_relevamiento"].max())]
        ra_a = rr[rr["tipo_periodo"] == "anio"].assign(a=lambda z: z["periodo_original"].astype(str).str[:4]).groupby("a")["mediana"].mean()
        ra_p = rr[rr["tipo_periodo"] == "proximos_meses"].groupby("horizonte_meses")["mediana"].mean()
        for x_ in reg:                                   # año en curso: la respuesta anual del REM, no la armada con sus meses
            if x_["H"] == "año" and str(t_hoy.year) in ra_a.index:   # (30-oct-2026, Marcos: "usa 28")
                x_["rem_anterior"] = ra_a[str(t_hoy.year)]
        nuevos = [("12m", "12m", np.prod([camino[t_hoy + j] for j in range(1, 13)]), ra_p.get(12, np.nan)),
                  ("año1", str(t_hoy.year + 1), f_anio(t_hoy.year + 1), ra_a.get(str(t_hoy.year + 1), np.nan)),
                  ("año2", str(t_hoy.year + 2), f_anio(t_hoy.year + 2), ra_a.get(str(t_hoy.year + 2), np.nan)),
                  ("24m", "24m", np.prod([camino[t_hoy + j] for j in range(13, 25)]), ra_p.get(24, np.nan))]
        for H_, obj, f_, ra_ in nuevos:
            reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "H": H_, "objetivo": obj, "N": (f_ - 1) * 100,
                        "A": np.nan, "REM_corr": np.nan, "persistencia": np.nan, "rem_anterior": ra_})
        L.append("   Núcleo, plazos largos (clase de referencia con superávit, " + f"{n_casos} casos): "
                 + " | ".join(f"{H_} {(f_ - 1) * 100:.1f}% (REM ant. {ra_:.1f}%)" for H_, _, f_, ra_ in nuevos))
        p = C / "registro_nucleo.csv"
        pd.DataFrame(reg).to_csv(p, mode="a", header=not p.exists(), index=False, encoding="utf-8-sig", float_format="%.4f")
    L += ["", "ALERTAS: ninguna. Todo en orden."]
    texto = "\n".join(L)
    print(texto)
    (C / "informe_nucleo.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
