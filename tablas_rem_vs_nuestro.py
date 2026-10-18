# tablas_rem_vs_nuestro.py
# Una tabla por horizonte (mes en curso, 1 a 6 meses, próximos 12 meses): para cada informe del REM, lo que pronosticó
# el REM, lo que hubiéramos pronosticado nosotros ese mismo mes con la información disponible (A = nuestro pronóstico del
# REM; I = nuestro pronóstico de la inflación; ambos en tiempo real, de datos\replica_rem.csv), la inflación real y las
# diferencias en puntos porcentuales.
# Significancia de la suma (o promedio) de las diferencias: prueba t con errores de Newey-West (los pronósticos a
# varios meses se superponen), rezagos = horizonte. También: ¿quién estuvo más cerca de la inflación real?
# (Diebold-Mariano con corrección HLN).
# Salida: datos\comparacion_rem_vs_nuestro.xlsx (una hoja por horizonte + "Resumen").
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

import datos_macro as D
from backtest_rem import diebold_mariano

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"


def t_nw(x, lag):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return np.nan, np.nan
    r = sm.OLS(x, np.ones(len(x))).fit(cov_type="HAC", cov_kwds={"maxlags": max(lag, 1)})
    return float(r.tvalues[0]), float(r.pvalues[0])


def main():
    O = pd.read_csv(C / "replica_rem.csv")
    O["origen"] = pd.PeriodIndex(O["origen"], freq="M")
    ipc = pd.read_parquet(C / "ipc_indec.parquet")
    ipc.index = pd.to_datetime(ipc["mes"]).dt.to_period("M")
    pi = ipc["ipc_general_indice_var_mensual"].dropna().combine_first(D.precios()["oficial"])
    P = D.indice(pi)
    real12 = pd.Series({o: (P.get(o + 12) / P.get(o) - 1) * 100 for o in P.index if (o + 12) in P.index})
    orden = [f"m{h}" for h in range(7)] + ["12m"]
    nombres = {"m0": "Mes en curso", "m1": "1 mes", "m2": "2 meses", "m3": "3 meses", "m4": "4 meses", "m5": "5 meses",
               "m6": "6 meses", "12m": "Próx. 12 meses"}
    hojas, resumen = {}, []
    for H in orden:
        q = O[O["H"] == H].dropna(subset=["rem", "A"]).sort_values("origen").copy()
        h = 12 if H == "12m" else int(H[1:])
        if H == "12m":
            q["real"] = q["origen"].map(real12)
            q["mes pronosticado"] = "12 meses desde " + (q["origen"] + 1).astype(str)
        else:
            q["mes pronosticado"] = (q["origen"] + h).astype(str)
        t = pd.DataFrame({"informe REM": q["origen"].astype(str).values, "mes pronosticado": q["mes pronosticado"].values,
                          "REM": q["rem"].values, "A (nuestro pron. del REM)": q["A"].values,
                          "I (nuestro pron. de la inflación)": q.get("I", pd.Series(np.nan, index=q.index)).values,
                          "inflación real": q["real"].values})
        t["A − REM (pp)"] = t["A (nuestro pron. del REM)"] - t["REM"]
        t["I − REM (pp)"] = t["I (nuestro pron. de la inflación)"] - t["REM"]
        t["error REM (pp)"] = t["REM"] - t["inflación real"]
        t["error I (pp)"] = t["I (nuestro pron. de la inflación)"] - t["inflación real"]
        t["más cerca"] = np.where(t["inflación real"].isna(), "", np.where(t["error I (pp)"].abs() < t["error REM (pp)"].abs(), "I", "REM"))
        hojas[nombres[H]] = t
        for per, m in (("Todo", np.ones(len(t), bool)), ("2025-26", (q["origen"] > pd.Period("2024-12", "M")).values)):
            x = t[m]
            fila = {"horizonte": nombres[H], "período": per, "informes": len(x)}
            for c, et in (("A − REM (pp)", "A"), ("I − REM (pp)", "I")):
                d = x[c].dropna()
                tt, pp = t_nw(d, h)
                fila.update({f"suma {et}−REM": d.sum(), f"promedio {et}−REM": d.mean(), f"p {et}−REM": pp})
            v = x.dropna(subset=["error I (pp)", "error REM (pp)"])
            fila["con IPC publicado"] = len(v)
            fila["error medio REM"] = v["error REM (pp)"].abs().mean()
            fila["error medio I"] = v["error I (pp)"].abs().mean()
            fila["I más cerca (%)"] = (v["más cerca"] == "I").mean() * 100 if len(v) else np.nan
            fila["p DM (I vs REM)"] = diebold_mariano(v["error I (pp)"].values, v["error REM (pp)"].values, max(h, 1)) if len(v) > 5 else np.nan
            resumen.append(fila)
    R = pd.DataFrame(resumen)
    with pd.ExcelWriter(C / "comparacion_rem_vs_nuestro.xlsx", engine="openpyxl") as x:
        R.round(3).to_excel(x, sheet_name="Resumen", index=False)
        for nom, t in hojas.items():
            t.round(2).to_excel(x, sheet_name=nom, index=False)
        for ws in x.sheets.values():
            ws.freeze_panes = "A2"
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(12, min(34, len(str(col[0].value)) + 2))
    pd.set_option("display.width", 250)
    print(R.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
