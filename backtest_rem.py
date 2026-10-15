# backtest_rem.py
# Backtest en tiempo real contra el REM: inflación mensual (IPC INDEC nivel general), horizontes 0 a 6 meses.
# En cada relevamiento del REM (fin del mes t) el modelo usa solo lo que se conocía ese día:
#   - IPC hasta t-1 (el de t se publica a mitad de t+1)
#   - tipo de cambio A3500 del mes t hasta el día 25 (el REM se releva en los últimos días hábiles)
#   - base monetaria y emisión neta hasta t-1; la estacionalidad de la base se estima solo con datos hasta t-1
# Pronóstico directo para cada horizonte h (mes objetivo t+h), con mínimos cuadrados en ventana móvil (36 meses)
# o acumulada. Modelos:
#   AR         : inflación t-1, t-2, t-3 y la del mismo mes un año antes (estacionalidad)
#   +TC        : AR + devaluación del mes t (hasta el 25), t-1 y t-2
#   +dinero    : +TC + crecimiento de la base desestacionalizada (t-6..t-1 y t-12..t-7, mensualizado)
#   +emisión   : +TC + emisión neta acumulada en 12 meses (% de la base, emision_tesoro.py)
#   +todo      : +TC + dinero + emisión
#   REM+TC     : promedio del REM y del modelo +TC
# Datos para estimar: "con tramo" (IPCBA 2012-08 a 2018-12), "oficial" (INDEC oficial) y "sin tramo" (solo INDEC desde 2017).
# El resultado a pronosticar es siempre el IPC del INDEC.
# Salida: datos/informe_backtest.txt y datos/backtest_pronosticos.csv
# Uso: py -3.13 backtest_rem.py
import sys
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import seasonal_decompose

import datos_macro as D

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HORIZONTES = range(0, 7)
VENTANA_MOVIL = 36
MIN_ACUMULADA = 36
DIA_CORTE_TC = 25
INFORME = D.CARPETA / "informe_backtest.txt"
SALIDA = D.CARPETA / "backtest_pronosticos.csv"
PERIODOS = [("2017-2019", "2016-06", "2019-12"), ("2020-2023", "2020-01", "2023-12"), ("2024-2026", "2024-01", "2026-12"),
            ("todo", "2016-06", "2026-12")]
MODELOS = {
    "AR": ["p1", "p2", "p3", "p12"],
    "+TC": ["p1", "p2", "p3", "p12", "dev0", "dev1", "dev2"],
    "+dinero": ["p1", "p2", "p3", "p12", "dev0", "dev1", "dev2", "g6", "g12"],
    "+emisión": ["p1", "p2", "p3", "p12", "dev0", "dev1", "dev2", "emi12"],
    "+todo": ["p1", "p2", "p3", "p12", "dev0", "dev1", "dev2", "g6", "g12", "emi12"],
}


def ols_pred(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    b, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    return float(np.r_[1.0, x0] @ b)


def diebold_mariano(e1, e2, h):
    """p-valor (dos colas) de igual error absoluto; varianza HAC con h rezagos.
    Con la corrección de Harvey, Leybourne y Newbold (1997) para muestras chicas y errores superpuestos
    (estadístico ajustado y distribución t con n-1 grados de libertad). Sin la corrección, verificar_analisis.py
    mostró que el test rechazaba 12-15% (en lugar de 5%) a horizontes de 6-12 meses."""
    from scipy import stats
    d = np.abs(e1) - np.abs(e2)
    d = d[~np.isnan(d)]
    n = len(d)
    if n < 10:
        return np.nan
    dc = d - d.mean()
    var = dc @ dc / n
    for k in range(1, h + 1):
        var += 2 * (1 - k / (h + 1)) * (dc[k:] @ dc[:-k]) / n
    if var <= 0:
        return np.nan
    dm = d.mean() / np.sqrt(var / n)
    horizonte = h + 1
    ajuste = np.sqrt(max(n + 1 - 2 * horizonte + horizonte * (horizonte - 1) / n, 1) / n)
    return float(2 * (1 - stats.t.cdf(abs(dm * ajuste), df=n - 1)))


def main():
    # ---------- datos ----------
    pr = D.precios()
    ipc = pd.read_parquet(D.CARPETA / "ipc_indec.parquet")
    ipc.index = pd.to_datetime(ipc["mes"]).dt.to_period("M")
    real = ipc["ipc_general_indice_var_mensual"].dropna()          # lo que pasó (INDEC)
    infl = {"con tramo": pr["ipcba2012"], "oficial": pr["oficial"],
            "sin tramo": real[real.index >= pd.Period("2017-01", "M")]}
    diaria = pd.read_parquet(D.CARPETA / "bcra_series.parquet")
    diaria["fecha"] = pd.to_datetime(diaria["fecha"])
    tc_d = diaria[diaria["serie"] == "tc_mayorista_a3500"].set_index("fecha")["valor"].sort_index()
    base_d = diaria[diaria["serie"] == "base_monetaria"].set_index("fecha")["valor"].sort_index()
    tc_m = tc_d.groupby(tc_d.index.to_period("M")).mean()
    tc_25 = tc_d[tc_d.index.day <= DIA_CORTE_TC]
    tc_25 = tc_25.groupby(tc_25.index.to_period("M")).mean()
    dev_m = np.log(tc_m).diff() * 100                                 # devaluación de meses completos
    dev_parcial = (np.log(tc_25) - np.log(tc_m.shift(1))) * 100       # mes t hasta el día 25 contra el mes t-1 completo
    base_m = base_d.groupby(base_d.index.to_period("M")).mean()
    emi = pd.read_csv(D.CARPETA / "emision_mensual.csv", index_col=0)
    emi.index = pd.PeriodIndex(emi.index, freq="M")
    emi12 = emi["neta_pct"].rolling(12).sum()

    rem = pd.read_parquet(D.CARPETA / "rem_normalizada.parquet")
    rem = rem[(rem["variable_std"] == "ipc_general") & (rem["tipo_periodo"] == "fecha") & (rem["unidad"] == "var. % mensual")
              & rem["horizonte_meses"].between(0, 6)]
    rem = rem.assign(origen=pd.to_datetime(rem["mes_relevamiento"]).dt.to_period("M"),
                     h=rem["horizonte_meses"].astype(int))
    rem = rem.groupby(["origen", "h"])["mediana"].mean()
    origenes = sorted(rem.index.get_level_values(0).unique())

    def crecimiento_base_tiempo_real(t):
        """crecimiento mensual promedio de la base desestacionalizada (hasta t-1), estacionalidad estimada hasta t-1."""
        b = base_m[(base_m.index <= t - 1) & (base_m.index >= pd.Period("2003-01", "M"))]
        lg = np.log(b)
        est = seasonal_decompose(pd.Series(lg.values, index=lg.index.to_timestamp()), period=12,
                                 extrapolate_trend="freq").seasonal
        sa = pd.Series(lg.values - est.values, index=lg.index)
        return sa

    filas = []
    for t in origenes:
        sa = crecimiento_base_tiempo_real(t)
        g6 = ((sa - sa.shift(6)) / 6 * 100)
        g12 = ((sa.shift(6) - sa.shift(12)) / 6 * 100)
        for vers, pi in infl.items():
            pi = pi[pi.index <= t - 1]            # solo IPC publicado
            if len(pi) < 40:
                continue
            for h in HORIZONTES:
                # filas de entrenamiento: orígenes s con objetivo s+h ya publicado (s+h <= t-1)
                def rasgos(s):
                    return {"p1": pi.get(s - 1), "p2": pi.get(s - 2), "p3": pi.get(s - 3), "p12": pi.get(s + h - 12),
                            "dev0": dev_parcial.get(s), "dev1": dev_m.get(s - 1), "dev2": dev_m.get(s - 2),
                            "g6": g6.get(s - 1), "g12": g12.get(s - 1), "emi12": emi12.get(s - 1)}
                ult = t - 1 - h
                cand = [s for s in pi.index if s <= ult]
                tabla = pd.DataFrame({s: {**rasgos(s), "y": pi.get(s + h)} for s in cand}).T.astype(float)
                x0 = pd.Series(rasgos(t), dtype=float)
                for ventana in ("móvil 36", "acumulada"):
                    tb = tabla.dropna()
                    tb = tb.iloc[-VENTANA_MOVIL:] if ventana == "móvil 36" else tb
                    if len(tb) < (VENTANA_MOVIL if ventana == "móvil 36" else MIN_ACUMULADA):
                        continue
                    for mod, cols in MODELOS.items():
                        if x0[cols].isna().any():
                            continue
                        filas.append({"origen": t, "h": h, "objetivo": t + h, "version": vers, "ventana": ventana,
                                      "modelo": mod, "pronostico": ols_pred(tb[cols].values, tb["y"].values, x0[cols].values)})
    f = pd.DataFrame(filas)
    f["real"] = f["objetivo"].map(real)
    f["rem"] = [rem.get((o, h), np.nan) for o, h in zip(f["origen"], f["h"])]
    comb = f[f["modelo"] == "+TC"].copy()
    comb["modelo"], comb["pronostico"] = "REM+TC", (comb["pronostico"] + comb["rem"]) / 2
    f = pd.concat([f, comb], ignore_index=True)
    f = f.dropna(subset=["real", "rem"])
    f["err"] = f["real"] - f["pronostico"]
    f["err_rem"] = f["real"] - f["rem"]
    f.to_csv(SALIDA, index=False, encoding="utf-8-sig", float_format="%.4f")

    # ---------- informe ----------
    L = ["=== BACKTEST EN TIEMPO REAL CONTRA EL REM (IPC INDEC, var. % mensual) ===",
         f"Relevamientos evaluados: {f['origen'].min()} a {f['origen'].max()}; objetivos hasta {f['objetivo'].max()}.",
         "MAE en puntos porcentuales. Cociente < 1 = el modelo le gana al REM. DM = p-valor de Diebold-Mariano (igual error).", ""]
    modelos = list(MODELOS) + ["REM+TC"]

    def tabla_mae(sub, titulo):
        L.append(titulo)
        L.append(f"   {'modelo':10} " + " ".join(f"{'h' + str(h):>13}" for h in HORIZONTES))
        q = sub.drop_duplicates(["origen", "h"])
        L.append(f"   {'REM':10} " + " ".join(f"{q[q.h == h]['err_rem'].abs().mean():13.2f}" for h in HORIZONTES))
        for m in modelos:
            s = sub[sub["modelo"] == m]
            cel = []
            for h in HORIZONTES:
                sh = s[s.h == h]
                if len(sh) < 8:
                    cel.append(f"{'-':>13}")
                    continue
                mae, mae_r = sh["err"].abs().mean(), sh["err_rem"].abs().mean()
                p = diebold_mariano(sh["err"].values, sh["err_rem"].values, max(h, 1))
                cel.append(f"{mae:5.2f} {mae / mae_r:4.2f} {p:4.2f}".rjust(13))
            L.append(f"   {m:10} " + " ".join(cel))
        L.append(f"   (cada celda: MAE, cociente contra REM, p DM; n por horizonte ≈ {len(q[q.h == 1])})")
        L.append("")

    for vers in ("con tramo", "oficial", "sin tramo"):
        for ventana in ("móvil 36", "acumulada"):
            sub = f[(f["version"] == vers) & (f["ventana"] == ventana)]
            if sub.empty:
                continue
            tabla_mae(sub, f"--- datos {vers} | ventana {ventana} | {sub['origen'].min()} a {sub['origen'].max()}")

    # muestra común (todas las versiones disponibles) y por período, para la mejor combinación
    L.append("=== Por período (datos con tramo, ventana móvil 36) ===")
    for nom, a, b in PERIODOS:
        sub = f[(f["version"] == "con tramo") & (f["ventana"] == "móvil 36") & (f["origen"] >= pd.Period(a, "M")) & (f["origen"] <= pd.Period(b, "M"))]
        if not sub.empty:
            tabla_mae(sub, f"--- {nom}")
    # ---------- ¿aportan información que el REM no usa? REM corregido ----------
    # error del REM (real - REM) explicado por variables conocidas en t; ventana acumulada; pronóstico = REM + corrección
    CORRECCIONES = {"sesgo": [], "devaluación": ["dev0", "dev1"], "dinero": ["g6", "g12"], "emisión": ["emi12"],
                    "dev+dinero+emisión": ["dev0", "dev1", "g6", "g12", "emi12"]}
    filas_c = []
    rem_df = rem.rename("rem").reset_index()
    base_sa_cache = {}
    for t in origenes:
        sa = crecimiento_base_tiempo_real(t)
        base_sa_cache[t] = sa
    for t in origenes:
        sa = base_sa_cache[t]
        g6 = (sa - sa.shift(6)) / 6 * 100
        g12 = (sa.shift(6) - sa.shift(12)) / 6 * 100
        def rasgos(s):
            return {"dev0": dev_parcial.get(s), "dev1": dev_m.get(s - 1), "g6": g6.get(s - 1), "g12": g12.get(s - 1),
                    "emi12": emi12.get(s - 1)}
        x0 = pd.Series(rasgos(t), dtype=float)
        for h in HORIZONTES:
            r0 = rem.get((t, h), np.nan)
            if np.isnan(r0):
                continue
            hist = rem_df[(rem_df["h"] == h) & (rem_df["origen"] <= t - 1 - h)]
            if len(hist) < 24:
                continue
            tabla = pd.DataFrame([{**rasgos(s), "y": real.get(s + h, np.nan) - r} for s, r in zip(hist["origen"], hist["rem"])]).astype(float)
            for nom, cols in CORRECCIONES.items():
                tb = tabla.dropna(subset=cols + ["y"])
                if len(tb) < 24 or x0[cols].isna().any():
                    continue
                corr = ols_pred(tb[cols].values, tb["y"].values, x0[cols].values) if cols else float(tb["y"].mean())
                filas_c.append({"origen": t, "h": h, "correccion": nom, "rem": r0, "pronostico": r0 + corr})
    c = pd.DataFrame(filas_c)
    c["real"] = (c["origen"] + c["h"]).map(real)
    c = c.dropna(subset=["real"])
    c["err"], c["err_rem"] = c["real"] - c["pronostico"], c["real"] - c["rem"]
    c.to_csv(D.CARPETA / "backtest_rem_corregido.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    L.append("=== ¿Tienen información que el REM no usa? Pronóstico = REM + corrección estimada en tiempo real ===")
    L.append("    (corrección = regresión del error pasado del REM sobre la variable; ventana acumulada, mínimo 24 relevamientos)")
    for nom_p, a, b in PERIODOS:
        sub = c[(c["origen"] >= pd.Period(a, "M")) & (c["origen"] <= pd.Period(b, "M"))]
        if sub.empty:
            continue
        L.append(f"--- {nom_p} ({sub['origen'].min()} a {sub['origen'].max()})")
        L.append(f"   {'corrección':20} " + " ".join(f"{'h' + str(h):>13}" for h in HORIZONTES))
        q = sub.drop_duplicates(["origen", "h"])
        L.append(f"   {'REM solo':20} " + " ".join(f"{q[q.h == h]['err_rem'].abs().mean():13.2f}" for h in HORIZONTES))
        for nom in CORRECCIONES:
            s = sub[sub["correccion"] == nom]
            cel = []
            for h in HORIZONTES:
                sh = s[s.h == h]
                if len(sh) < 8:
                    cel.append(f"{'-':>13}")
                    continue
                mae, mae_r = sh["err"].abs().mean(), sh["err_rem"].abs().mean()
                p = diebold_mariano(sh["err"].values, sh["err_rem"].values, max(h, 1))
                cel.append(f"{mae:5.2f} {mae / mae_r:4.2f} {p:4.2f}".rjust(13))
            L.append(f"   {nom:20} " + " ".join(cel))
        L.append("")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
