# diagnostico_rem.py (28-oct-2026; análisis, fuera del circuito diario)
# ¿Dónde se equivoca el REM? Inflación GENERAL y NÚCLEO mensual, horizontes m0 a m6.
# Error = real − mediana del REM (positivo = el REM se quedó corto).
#   1) Sesgo: error medio por horizonte y etapa (2017-19, 2020-22, 2023-24, 2025-26).
#   2) Reacción a medias (Coibion y Gorodnichenko 2015): error = a + b × revisión, con revisión = REM(t, mes) − REM(t−1, mes).
#      b > 0: cuando el REM corrige, la inflación termina yendo más lejos en la misma dirección (ajusta de menos).
#   3) Errores predecibles con lo que se sabía al responder: dólar del mes (dev0), último dato publicado menos el REM
#      (sorpresa de inercia), expectativas de los hogares menos el REM, regulados del último mes publicado.
#      Si alguna explica el error, el REM no está usando bien esa información.
# MCO con errores robustos (Newey-West, rezagos = horizonte + 2). Sin 2023-24 en las regresiones de "toda la muestra"
# (devaluación de dic-2023 y salto de régimen: domina cualquier estimación).
# Informe: datos\informe_diagnostico_rem.txt
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
ETAPAS = {"2017-19": (2017, 2019), "2020-22": (2020, 2022), "2023-24": (2023, 2024), "2025-26": (2025, 2026)}


def rem_mensual(var):
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[(r["variable_std"] == var) & (r["tipo_periodo"] == "fecha") & (r["unidad"] == "var. % mensual")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["obj"] = pd.to_datetime(g["periodo_fin"]).dt.to_period("M")
    return g.groupby(["origen", "obj"])["mediana"].mean()


def armar(var, col_real):
    rem = rem_mensual(var)
    i = pd.read_parquet(C / "ipc_indec.parquet")
    real = pd.Series(i[col_real].values, index=pd.to_datetime(i["mes"]).dt.to_period("M"))
    v = pd.read_csv(C / "replica_rem_variables.csv", encoding="utf-8-sig")
    v = v[v["H"].str.fullmatch(r"m\d")].copy()
    v["origen"] = pd.PeriodIndex(v["origen"], freq="M")
    v["obj"] = pd.PeriodIndex(v["objetivo"], freq="M")
    v["h"] = v["H"].str[1].astype(int)
    X = v[["origen", "obj", "h", "dev0", "nuc1", "p1", "ei_med_m", "reg1"]].copy()
    X["rem"] = [rem.get((o, b), np.nan) for o, b in zip(X["origen"], X["obj"])]
    X["rem_ant"] = [rem.get((o - 1, b), np.nan) for o, b in zip(X["origen"], X["obj"])]
    X["real"] = X["obj"].map(real)
    X["error"] = X["real"] - X["rem"]
    X["revision"] = X["rem"] - X["rem_ant"]
    ult = X["nuc1"] if var == "ipc_nucleo" else X["p1"]
    X["sorpresa_inercia"] = ult - X["rem"]          # último dato publicado contra lo que dice el REM
    X["hogares_gap"] = X["ei_med_m"] - X["rem"]     # expectativa de los hogares (mensualizada) contra el REM
    X["anio"] = X["obj"].dt.year
    return X.dropna(subset=["rem", "real"])


def hac(z, y, cols, lags):
    z = z.dropna(subset=[y] + cols)
    if len(z) < 12:
        return None, len(z)
    return sm.OLS(z[y], sm.add_constant(z[cols])).fit(cov_type="HAC", cov_kwds={"maxlags": lags}), len(z)


def main():
    L = [f"=== DIAGNÓSTICO DE LOS ERRORES DEL REM | {datetime.now():%Y-%m-%d %H:%M} ===",
         "Error = real − mediana del REM (positivo: el REM se quedó corto). Inflación mensual, pp.", ""]
    for var, col, nom in (("ipc_general", "ipc_general_indice_var_mensual", "GENERAL"),
                          ("ipc_nucleo", "ipc_nucleo_indice_var_mensual", "NÚCLEO")):
        X = armar(var, col)
        L.append(f"################ INFLACIÓN {nom} (relevamientos {X['origen'].min()} a {X['origen'].max()})")
        L.append("1) SESGO: error medio (y % de veces que el REM se quedó corto), por horizonte y etapa")
        L.append(f"   {'':5}" + "".join(f"{e:>16}" for e in ETAPAS))
        for h in range(7):
            z = X[X["h"] == h]
            celdas = []
            for e, (a, b) in ETAPAS.items():
                q = z[z["anio"].between(a, b)]
                celdas.append(f"{q['error'].mean():+6.2f} ({(q['error'] > 0).mean():3.0%})" if len(q) else f"{'-':>14}")
            L.append(f"   m{h:<4}" + "".join(f"{c:>16}" for c in celdas))
        for et, mask in (("toda la muestra sin 2023-24", ~X["anio"].between(2023, 2024)), ("2025-26", X["anio"] >= 2025)):
            L += ["", f"2) REACCIÓN A MEDIAS ({et}): error = a + b × revisión del REM",
                  "   b > 0 y significativo: el REM corrige de menos (si subió su pronóstico, la inflación subió todavía más)"]
            for h in range(0, 6):
                m, n = hac(X[mask & (X["h"] == h)], "error", ["revision"], h + 2)
                if m is not None:
                    L.append(f"   m{h}: b {m.params['revision']:+.2f} (t {m.tvalues['revision']:+.1f}) | a {m.params['const']:+.2f} | R² {m.rsquared:.2f} | n {n}")
            L += ["", f"3) ¿ERRORES PREDECIBLES? ({et}) error sobre cada dato conocido al responder (t robusto)"]
            for h in range(0, 7):
                z = X[mask & (X["h"] == h)]
                partes = []
                for c in ("dev0", "sorpresa_inercia", "hogares_gap", "reg1"):
                    m, n = hac(z, "error", [c], h + 2)
                    if m is not None:
                        partes.append(f"{c} {m.params[c]:+.2f} (t {m.tvalues[c]:+.1f})")
                m, n = hac(z, "error", ["dev0", "sorpresa_inercia", "hogares_gap", "reg1"], h + 2)
                junto = f" | juntas R² {m.rsquared:.2f}, p {m.f_pvalue:.3f}, n {n}" if m is not None else ""
                L.append(f"   m{h}: " + "  ".join(partes) + junto)
        L.append("")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_diagnostico_rem.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
