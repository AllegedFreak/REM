# probar_ei_12m.py - (17-nov-2026, pedido de Marcos) ¿Mejora la inflación a 12 meses si se suaviza la expectativa de los
# hogares (UTDT) o si el modelo la "descuenta" menos? Solo análisis: no cambia el modelo ni el registro.
# Tiempo real: en cada origen t se estima solo con lo que se conocía en t. Real = inflación de t+1 a t+12 (IPC INDEC).
# Variantes:
#   I actual       : log(1+REM12) ~ inflación 12m + log(1+EI promedio) + futuros 12m (MCO; como I_12 de replicar_rem.py)
#   I suavizada    : igual, con la EI promedio de los últimos 3 meses
#   I contra real  : misma regresión pero aprende de la inflación real (solo orígenes con los 12 meses ya publicados en t-1)
#   hogares prom.  : la EI promedio tal cual, sin regresión
#   hogares med.   : la EI mediana tal cual
#   mitad I/med.   : mitad I actual, mitad EI mediana (en logaritmos)
# Salida: datos\informe_ei_12m.txt y datos\probar_ei_12m.csv
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = __import__("pathlib").Path(__file__).resolve().parent / "datos"
MIN_FILAS = 24
COLS = ["inf12", "ei_prom_l", "fut12"]


def ols(X, y, x0):
    m = sm.OLS(y, sm.add_constant(X, has_constant="add")).fit()
    return float(np.r_[1, x0] @ m.params)


def main():
    v = pd.read_csv(C / "replica_rem_variables.csv", encoding="utf-8-sig")
    v = v[v["H"] == "12m"].copy()
    v["o"] = pd.PeriodIndex(v["origen"], freq="M")
    hoy = pd.read_csv(C / "pronostico_insumos.csv", encoding="utf-8-sig")
    hoy = hoy[hoy["H"] == "12m"].drop_duplicates("relevamiento", keep="last")
    hoy = hoy[~hoy["relevamiento"].isin(v["origen"])].rename(columns={"relevamiento": "origen"})
    hoy["o"] = pd.PeriodIndex(hoy["origen"], freq="M")
    v = pd.concat([v, hoy[[c for c in hoy.columns if c in v.columns or c == "o"]]], ignore_index=True).sort_values("o")
    v = v.set_index("o")

    s = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    ei = {k: s[s["id"] == f"utdt_ei_{k}"].assign(m=lambda z: pd.to_datetime(z["fecha"]).dt.to_period("M")).set_index("m")["valor"].sort_index()
          for k in ("mediana", "promedio")}
    v["ei_med_x"] = ei["mediana"].reindex(v.index).values
    v["ei_prom_x"] = ei["promedio"].reindex(v.index).values
    v["ei_prom3_l"] = np.log1p(ei["promedio"].rolling(3).mean().reindex(v.index).values / 100) * 100
    # hoy (nov-2026) la UTDT de noviembre todavía no salió: el modelo usa la de octubre (t-1); igual acá
    for col, serie in (("ei_med_x", ei["mediana"]), ("ei_prom_x", ei["promedio"])):
        falta = v[col].isna()
        v.loc[falta, col] = [serie.get(o - 1, np.nan) for o in v.index[falta]]
    falta = v["ei_prom3_l"].isna()
    p3 = ei["promedio"].rolling(3).mean()
    v.loc[falta, "ei_prom3_l"] = [np.log1p(p3.get(o - 1, np.nan) / 100) * 100 for o in v.index[falta]]

    i = pd.read_parquet(C / "ipc_indec.parquet")
    P = i.assign(m=pd.to_datetime(i["mes"]).dt.to_period("M")).set_index("m")["ipc_general_indice"]
    v["real"] = [(P[o + 12] / P[o] - 1) * 100 if (o + 12) in P.index and o in P.index else np.nan for o in v.index]
    v["rem_l"] = np.log1p(v["rem"] / 100) * 100
    v["real_l"] = np.log1p(v["real"] / 100) * 100

    filas = []
    for t in v.index:
        x = v.loc[t]
        r = {"origen": str(t), "rem": x["rem"], "real": x["real"]}
        hist = v[v.index < t]
        for nom, cols, y, h in (("I actual", COLS, "rem_l", hist),
                                ("I suavizada", ["inf12", "ei_prom3_l", "fut12"], "rem_l", hist),
                                ("I contra real", COLS, "real_l", hist.loc[np.array([(o + 12) <= (t - 1) for o in hist.index], dtype=bool)])):
            tb = h.dropna(subset=cols + [y])
            if len(tb) >= MIN_FILAS and not x[cols].isna().any():
                r[nom] = np.expm1(ols(tb[cols].values.astype(float), tb[y].values.astype(float), x[cols].values.astype(float)) / 100) * 100
        r["hogares prom."] = x["ei_prom_x"]
        r["hogares med."] = x["ei_med_x"]
        if not pd.isna(r.get("I actual", np.nan)):
            r["mitad I/med."] = np.expm1((np.log1p(r["I actual"] / 100) + np.log1p(x["ei_med_x"] / 100)) / 2) * 100
        filas.append(r)
    R = pd.DataFrame(filas)
    R.to_csv(C / "probar_ei_12m.csv", index=False, encoding="utf-8-sig", float_format="%.3f")

    var = ["I actual", "I suavizada", "I contra real", "hogares prom.", "hogares med.", "mitad I/med.", "rem"]
    L = [f"=== INFLACIÓN A 12 MESES: ¿SUAVIZAR LA EXPECTATIVA O DESCONTARLA MENOS? | {pd.Timestamp.now():%Y-%m-%d %H:%M} ===",
         "Tiempo real. Error = pronóstico − inflación real de t+1 a t+12 (pp). Solo análisis: el modelo no cambia.", ""]
    for nom, a, b in (("2019-2024", "2019-01", "2024-12"), ("2025-26 (régimen actual)", "2025-01", "2026-12")):
        q = R[(R["origen"] >= a) & (R["origen"] <= b)].dropna(subset=["real"])
        q = q.dropna(subset=[c for c in var if c in q and c != "I contra real"])
        L.append(f"{nom}: {len(q)} orígenes con los 12 meses cerrados ({q['origen'].min()} a {q['origen'].max()})")
        L.append(f"   {'':16}{'error medio':>12}{'sesgo':>8}")
        for c in var:
            e = q[c] - q["real"]
            L.append(f"   {('REM' if c == 'rem' else c):16}{e.abs().mean():12.2f}{e.mean():8.2f}")
        L.append("")
    q = R[R["origen"] >= "2025-01"]
    L.append("Mes por mes desde 2025 (% a 12 meses):")
    L.append(f"   {'origen':8}" + "".join(f"{('REM' if c == 'rem' else c):>15}" for c in var) + f"{'real':>8}")
    for _, x in q.iterrows():
        L.append(f"   {x['origen']:8}" + "".join(f"{x.get(c, np.nan):15.1f}" for c in var) + f"{x['real']:8.1f}")
    L.append("")
    L.append("Hoy (último origen): " + " | ".join(f"{('REM ant.' if c == 'rem' else c)} {R.iloc[-1].get(c, np.nan):.1f}" for c in var if c != "rem"))
    texto = "\n".join(L)
    print(texto)
    (C / "informe_ei_12m.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
