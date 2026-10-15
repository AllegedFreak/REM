# backtest_12m.py
# Backtest en tiempo real contra el REM a 12 y 24 meses (inflación interanual, IPC INDEC nivel general).
#   REM "Próx. 12 meses" (relevamiento fin de t): variación del IPC entre t y t+12.
#   REM "Próx. 24 meses": variación interanual del mes t+24 (entre t+12 y t+24) -- lectura del campo, sin verificar.
# En cada relevamiento t el modelo usa solo lo publicado: IPC hasta t-1, TC de t hasta el día 25, dinero y emisión hasta t-1.
# Las filas de entrenamiento s se usan solo si su resultado ya estaba publicado en t (s+12 <= t-1, o s+24 <= t-1).
# Ventana acumulada (las ventanas cortas explotaron con el salto de dic-2023 en backtest_rem.py). Pocos parámetros.
# Modelos (pronóstico en log, se convierte a variación %):
#   ingenuo      : los próximos 12 meses repiten los últimos 12 (sin estimar)
#   inercia      : a + b·inflación 12 meses + c·inflación 3 meses anualizada
#   +TC          : inercia + devaluación de los últimos 3 meses
#   +base        : inercia + crecimiento interanual de la base
#   +emi neta    : inercia + emisión neta acumulada 12 meses (% de la base)
#   +emi Tesoro  : inercia + emisión al Tesoro bruta acumulada 12 meses
#   REM corregido: REM + corrección = error pasado del REM explicado por una variable
# Salida: datos/informe_backtest_12m.txt y datos/backtest_12m.csv
# Uso: py -3.13 backtest_12m.py
import sys
import warnings

import numpy as np
import pandas as pd

import datos_macro as D
from backtest_rem import diebold_mariano, ols_pred

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIA_CORTE_TC = 25
MIN_FILAS = 36
INFORME = D.CARPETA / "informe_backtest_12m.txt"
SALIDA = D.CARPETA / "backtest_12m.csv"
MODELOS = {"inercia": ["inf12", "inf3"], "+TC": ["inf12", "inf3", "dev3"], "+base": ["inf12", "inf3", "base12"],
           "+emi neta": ["inf12", "inf3", "emi12"], "+emi Tesoro": ["inf12", "inf3", "emiT12"]}
CORRECCIONES = {"sesgo": [], "inflación 3m": ["inf3"], "devaluación": ["dev3"], "base": ["base12"],
                "emi neta": ["emi12"], "emi Tesoro": ["emiT12"]}
PERIODOS = [("2016-2019", "2016-06", "2019-12"), ("2020-2023", "2020-01", "2023-12"), ("2024-2025", "2024-01", "2025-12"),
            ("todo", "2016-06", "2026-12")]


def main():
    pr = D.precios()
    ipc = pd.read_parquet(D.CARPETA / "ipc_indec.parquet")
    ipc.index = pd.to_datetime(ipc["mes"]).dt.to_period("M")
    lp_indec = np.log(ipc["ipc_general_indice"].dropna())
    versiones = {"con tramo": np.log(D.indice(pr["ipcba2012"])), "oficial": np.log(D.indice(pr["oficial"])),
                 "sin tramo": lp_indec}

    diaria = pd.read_parquet(D.CARPETA / "bcra_series.parquet")
    diaria["fecha"] = pd.to_datetime(diaria["fecha"])
    tc_d = diaria[diaria["serie"] == "tc_mayorista_a3500"].set_index("fecha")["valor"].sort_index()
    tc_m = tc_d.groupby(tc_d.index.to_period("M")).mean()
    tc_25 = tc_d[tc_d.index.day <= DIA_CORTE_TC]
    tc_25 = tc_25.groupby(tc_25.index.to_period("M")).mean()
    base_m = D.dinero()["base"]
    emi = pd.read_csv(D.CARPETA / "emision_mensual.csv", index_col=0)
    emi.index = pd.PeriodIndex(emi.index, freq="M")
    emi12 = emi["neta_pct"].rolling(12).sum()
    emiT12 = emi["tesoro_pct"].rolling(12).sum()

    rem = pd.read_parquet(D.CARPETA / "rem_normalizada.parquet")
    rem = rem[(rem["variable_std"] == "ipc_general") & (rem["tipo_periodo"] == "proximos_meses")]
    rem = rem.assign(origen=pd.to_datetime(rem["mes_relevamiento"]).dt.to_period("M"))
    rem = rem.groupby(["origen", "horizonte_meses"])["mediana"].mean()

    def rasgos(lp, s):
        g = lambda a, b: (lp.get(a, np.nan) - lp.get(b, np.nan)) * 100
        return {"inf12": g(s - 1, s - 13), "inf3": g(s - 1, s - 4) * 4,
                "dev3": (np.log(tc_25.get(s, np.nan)) - np.log(tc_m.get(s - 3, np.nan))) * 100,
                "base12": (np.log(base_m.get(s - 1, np.nan)) - np.log(base_m.get(s - 13, np.nan))) * 100,
                "emi12": emi12.get(s - 1, np.nan), "emiT12": emiT12.get(s - 1, np.nan)}

    def objetivo(lp, s, H):
        """100·log de la variación: H=12 entre s y s+12; H=24 entre s+12 y s+24."""
        a, b = (s + 12, s) if H == 12 else (s + 24, s + 12)
        return (lp.get(a, np.nan) - lp.get(b, np.nan)) * 100

    real = lambda s, H: np.exp(objetivo(lp_indec, s, H) / 100) * 100 - 100
    origenes = sorted(rem.index.get_level_values(0).unique())
    filas = []
    for H in (12, 24):
        for t in origenes:
            r0 = rem.get((t, H), np.nan)
            yr = real(t, H)
            if np.isnan(r0) or np.isnan(yr):
                continue
            for vers, lp in versiones.items():
                lp_pub = lp[lp.index <= t - 1]                       # solo IPC publicado
                x0 = pd.Series(rasgos(lp_pub, t), dtype=float)
                if H == 12 and not np.isnan(x0["inf12"]):
                    filas.append({"H": H, "origen": t, "version": vers, "modelo": "ingenuo", "pronostico": x0["inf12"],
                                  "rem": r0, "real": yr})
                cand = [s for s in lp_pub.index if s + H <= t - 1]
                if not cand:
                    continue
                tabla = pd.DataFrame({s: {**rasgos(lp_pub, s), "y": objetivo(lp_pub, s, H)} for s in cand}).T.astype(float)
                for mod, cols in MODELOS.items():
                    tb = tabla.dropna(subset=cols + ["y"])
                    if len(tb) < MIN_FILAS or x0[cols].isna().any():
                        continue
                    filas.append({"H": H, "origen": t, "version": vers, "modelo": mod, "rem": r0, "real": yr,
                                  "pronostico": ols_pred(tb[cols].values, tb["y"].values, x0[cols].values)})
            # REM corregido (no depende de la versión: el REM pronostica el INDEC)
            # rasgos de inflación con la serie con tramo (continua); el error del REM se mide contra el INDEC
            lp_ct = versiones["con tramo"][versiones["con tramo"].index <= t - 1]
            hist = [(s, rem.get((s, H), np.nan)) for s in origenes if s + H <= t - 1]
            tabla = pd.DataFrame([{**rasgos(lp_ct, s), "y": real(s, H) - r} for s, r in hist if not np.isnan(r)])
            x0 = pd.Series(rasgos(lp_ct, t), dtype=float)
            for nom, cols in CORRECCIONES.items():
                if tabla.empty:
                    continue
                tb = tabla.dropna(subset=cols + ["y"])
                if len(tb) < 24 or x0[cols].isna().any():
                    continue
                corr = ols_pred(tb[cols].values, tb["y"].values, x0[cols].values) if cols else float(tb["y"].mean())
                filas.append({"H": H, "origen": t, "version": "REM corregido", "modelo": nom, "rem": r0, "real": yr,
                              "pronostico_pct": r0 + corr})
    f = pd.DataFrame(filas)
    es_log = f["pronostico"].notna()
    f.loc[es_log, "pronostico_pct"] = np.exp(f.loc[es_log, "pronostico"] / 100) * 100 - 100
    f["err"], f["err_rem"] = f["real"] - f["pronostico_pct"], f["real"] - f["rem"]
    f.to_csv(SALIDA, index=False, encoding="utf-8-sig", float_format="%.4f")

    L = ["=== BACKTEST EN TIEMPO REAL CONTRA EL REM A 12 Y 24 MESES (IPC INDEC, variación %) ===",
         "Celdas: MAE en pp | cociente contra el REM (< 1 = le gana) | p Diebold-Mariano (HAC con H-1 rezagos).",
         "Ojo: los errores se superponen (un mismo shock afecta a 12-24 pronósticos seguidos): hay pocos años independientes",
         "y el p-valor es poco confiable. Mirar sobre todo si la mejora se repite en los distintos períodos.", ""]
    for H in (12, 24):
        fh = f[f["H"] == H]
        if fh.empty:
            continue
        L.append(f"##### Horizonte {H} meses: relevamientos {fh['origen'].min()} a {fh['origen'].max()}")
        grupos = [(v, m) for v in ("con tramo", "oficial", "sin tramo") for m in ["ingenuo"] + list(MODELOS)] + \
                 [("REM corregido", m) for m in CORRECCIONES]
        L.append(f"   {'datos':14} {'modelo':14} " + " ".join(f"{p[0]:>21}" for p in PERIODOS))
        q = fh.drop_duplicates("origen")
        fila_rem = []
        for nom, a, b in PERIODOS:
            qq = q[(q["origen"] >= pd.Period(a, "M")) & (q["origen"] <= pd.Period(b, "M"))]
            fila_rem.append(f"{qq['err_rem'].abs().mean():6.2f} (n={len(qq):3})".rjust(21))
        L.append(f"   {'REM':14} {'mediana':14} " + " ".join(fila_rem))
        for v, m in grupos:
            s = fh[(fh["version"] == v) & (fh["modelo"] == m)]
            if s.empty:
                continue
            cel = []
            for nom, a, b in PERIODOS:
                ss = s[(s["origen"] >= pd.Period(a, "M")) & (s["origen"] <= pd.Period(b, "M"))]
                if len(ss) < 6:
                    cel.append(f"{'-':>21}")
                    continue
                mae, mae_r = ss["err"].abs().mean(), ss["err_rem"].abs().mean()
                p = diebold_mariano(ss["err"].values, ss["err_rem"].values, H - 1)
                cel.append(f"{mae:6.2f} {mae / mae_r:5.2f} {p:5.2f}".rjust(21))
            L.append(f"   {v:14} {m:14} " + " ".join(cel))
        sesgo = q.groupby(q["origen"].dt.year)["err_rem"].mean()
        L.append("   Error medio del REM por año del relevamiento (real - REM, pp): " + " · ".join(f"{a} {v:+.1f}" for a, v in sesgo.items()))
        L.append("")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
