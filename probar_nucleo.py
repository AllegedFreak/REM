# probar_nucleo.py (28-oct-2026; análisis, fuera del circuito diario)
# ¿Cómo pronosticar la INFLACIÓN NÚCLEO como la pide el REM (var. % mensual, meses t a t+6)?
# Usa las variables que ya arma replicar_rem.py por relevamiento y horizonte (datos\replica_rem_variables.csv):
#   nuc1, nuc_2, nuc_3 = núcleo de t-1, t-2, t-3 | p12n = núcleo del mismo mes un año antes (estacionalidad)
#   dev0, dev1 = devaluación del mes y del anterior | ei_med_m / ei_prom_m = expectativas de los hogares (UTDT), mensualizadas
# Modelos (en tiempo real: para cada relevamiento, solo relevamientos anteriores; ventana acumulada, mínimo 24):
#   se queda : promedio de la núcleo de los últimos 3 meses (sin estimar nada)
#   N-A      : regresión robusta (Huber) del REM núcleo sobre las variables (con la MEDIANA de los hogares): imita al REM
#   N-I      : igual con el PROMEDIO de los hogares (el mismo esquema que el I del IPC general)
#   N-A est  : N-A sin p12n (para ver si la estacionalidad aporta)
# Real = núcleo nacional del INDEC (versión actual). Errores en puntos porcentuales de inflación mensual.
# Informe: datos\informe_prueba_nucleo.txt
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
CORTE = pd.Period("2024-12", "M")
MODELOS = {"N-A": ["nuc1", "nuc_2", "nuc_3", "p12n", "dev0", "dev1", "ei_med_m"],
           "N-I": ["nuc1", "nuc_2", "nuc_3", "p12n", "dev0", "dev1", "ei_prom_m"],
           "N-A sin est.": ["nuc1", "nuc_2", "nuc_3", "dev0", "dev1", "ei_med_m"],
           # 28-oct-2026: la mediana de los hogares está casi fija en 2,21% mensual en 2025-26 y, aprendida con la inflación
           # alta de 2019-24, empujaba el pronóstico hacia arriba (m6 2,08% con REM 1,62%). Dos arreglos a comparar:
           "N sin hogares": ["nuc1", "nuc_2", "nuc_3", "dev0", "dev1"],
           "N ventana 36": (["nuc1", "nuc_2", "nuc_3", "dev0", "dev1", "ei_med_m"], 36)}


def huber(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(Xc, y, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def main():
    v = pd.read_csv(C / "replica_rem_variables.csv", encoding="utf-8-sig")
    v = v[v["H"].str.fullmatch(r"m\d")].drop(columns=[c for c in v.columns if c.startswith("percentil_")]).copy()   # son del REM general
    v["origen"] = pd.PeriodIndex(v["origen"], freq="M")
    v["obj"] = pd.PeriodIndex(v["objetivo"], freq="M")
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[(r["variable_std"] == "ipc_nucleo") & (r["tipo_periodo"] == "fecha")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["obj"] = pd.to_datetime(g["periodo_fin"]).dt.to_period("M")
    g = g.groupby(["origen", "obj"])[["mediana", "percentil_10", "percentil_90"]].mean().reset_index()
    i = pd.read_parquet(C / "ipc_indec.parquet")
    real = pd.Series(i["ipc_nucleo_indice_var_mensual"].values, index=pd.to_datetime(i["mes"]).dt.to_period("M"))
    X = v.merge(g, on=["origen", "obj"], how="left").rename(columns={"mediana": "rem_n", "percentil_10": "p10", "percentil_90": "p90"})
    X["real_n"] = X["obj"].map(real)
    X["se queda"] = X[["nuc1", "nuc_2", "nuc_3"]].mean(axis=1)
    for nom, cols in MODELOS.items():
        cols, ventana = cols if isinstance(cols, tuple) else (cols, None)
        X[nom] = np.nan
        for _, q in X.groupby("H"):
            for k, row in q.iterrows():
                tr = q[q["origen"] < row["origen"]].dropna(subset=cols + ["rem_n"])
                if ventana:
                    tr = tr[tr["origen"] >= row["origen"] - ventana]
                if len(tr) >= MIN and row[cols].notna().all():
                    X.loc[k, nom] = huber(tr[cols].values, tr["rem_n"].values, row[cols].values.astype(float))
    X.to_csv(C / "prueba_nucleo.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    mods = ["se queda"] + list(MODELOS)
    L = [f"=== PRONÓSTICO DE LA INFLACIÓN NÚCLEO (prueba) | {datetime.now():%Y-%m-%d %H:%M} ===",
         f"REM núcleo mensual: {g['origen'].min()} a {g['origen'].max()}. Real = núcleo INDEC nacional. Errores en pp de inflación mensual.",
         "Misma muestra para todos los modelos en cada fila (desde que los modelos estimados tienen 24 relevamientos).", ""]
    for per, m_ in (("2019-2024", X["origen"] <= CORTE), ("2025-26 (régimen actual)", X["origen"] > CORTE)):
        Z = X[m_].dropna(subset=["rem_n"] + mods)
        L += [f"== {per}", f"   {'':4}{'distancia al REM':>40} | {'error contra el real':>50} | {'P10-P90':>16}",
              f"   {'':4}" + "".join(f"{m[:9]:>10}" for m in mods) + " | " + "".join(f"{m[:9]:>10}" for m in mods) + f"{'REM':>8}{'n':>5}{'p I-REM':>8} | "
              + f"{'N-A':>7}{'N-I':>7}"]
        for H in [f"m{h}" for h in range(7)]:
            z = Z[Z["H"] == H]
            zr = z.dropna(subset=["real_n"])
            if len(z) < 5:
                continue
            d = lambda c, ref, zz: (zz[c] - zz[ref]).abs().mean()
            p = diebold_mariano((zr["N-I"] - zr["real_n"]).values, (zr["rem_n"] - zr["real_n"]).values, max(int(H[1]), 1)) if len(zr) > 10 else np.nan
            cob = lambda c: ((z[c] >= z["p10"]) & (z[c] <= z["p90"])).mean() * 100
            L.append(f"   {H:4}" + "".join(f"{d(m, 'rem_n', z):10.2f}" for m in mods) + " | "
                     + "".join(f"{d(m, 'real_n', zr):10.2f}" for m in mods) + f"{d('rem_n', 'real_n', zr):8.2f}{len(zr):5d}{p:8.2f} | "
                     + f"{cob('N-A'):6.0f}%{cob('N-I'):6.0f}%")
        L.append("")
    hoy = X[X["origen"] == X["origen"].max()]
    L.append(f"ÚLTIMO RELEVAMIENTO CON VARIABLES ({X['origen'].max()}): núcleo mensual pronosticada")
    L.append(f"   {'mes':9}{'REM':>7}" + "".join(f"{m[:12]:>13}" for m in mods))
    for _, x in hoy.iterrows():
        L.append(f"   {str(x['obj']):9}{x['rem_n']:7.2f}" + "".join(f"{x[m]:13.2f}" for m in mods))
    texto = "\n".join(L)
    print(texto)
    (C / "informe_prueba_nucleo.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
