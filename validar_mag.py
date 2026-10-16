# validar_mag.py
# ¿El precio de la hacienda en Cañuelas (MAG) sigue o anticipa a la carne del IPC (GBA)?
# Hacienda de consumo = novillitos + vaquillonas (precio promedio ponderado por kilos). También novillos y el total.
# Se compara la variación mensual (%) con: IPC GBA clase 01.1.2 (carnes y derivados) y el precio promedio INDEC (GBA)
# de los cortes vacunos (asado, nalga, carne picada, cuadril, paleta). Mismo mes (completo y hasta el día 25) y con
# 1 a 3 meses de rezago; crudo y desestacionalizado (se resta el promedio de cada mes del año, 2017-2024).
# Prueba fuera de muestra: se estima 2017-2023 y se prueba 2024-2026.
# Salidas: datos\informe_validacion_mag.txt, datos\mag_mensual.csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
CORTES = ["Asado", "Nalga", "Carne picada común", "Cuadril", "Paleta"]


def lv(s):
    return np.log(s).diff() * 100


def desest(s, hasta="2024-12"):
    ref = s[s.index <= pd.Period(hasta, "M")]
    return s - pd.Series(s.index.month, index=s.index).map(ref.groupby(ref.index.month).mean()).values


def main():
    m = pd.read_parquet(C / "mag_precios.parquet")
    m["mes"] = pd.PeriodIndex(m["mes"], freq="M")
    pond = lambda q: (q["promedio"] * q["kgs"]).sum() / q["kgs"].sum()
    series = {}
    for nom, cats in (("consumo", ["NOVILLITOS", "VAQUILLONAS"]), ("novillos", ["NOVILLOS"]),
                      ("total", ["NOVILLOS", "NOVILLITOS", "VAQUILLONAS", "VACAS", "TOROS", "MEJ"])):
        for tramo in ("mes", "hasta25"):
            series[f"{nom}_{tramo}"] = m[(m.tramo == tramo) & m.categoria.isin(cats)].groupby("mes").apply(pond)
    P = pd.DataFrame(series).sort_index()
    P = P.reindex(pd.period_range(P.index.min(), P.index.max(), freq="M"))
    hs = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    s = hs[hs.id == "indec_ipcap_gba_01.1.2"]
    ipc = pd.Series(s.valor.values, index=pd.to_datetime(s.fecha).dt.to_period("M")).groupby(level=0).last().sort_index()
    pp = pd.read_parquet(C / "precios_promedio_indec.parquet")
    pp = pp[(pp.hoja == "GBA") & (pp.region == "GBA") & pp.producto.isin(CORTES)].copy()
    pp["mes"] = pd.to_datetime(pp.mes).dt.to_period("M")
    cortes = pp.pivot_table(index="mes", columns="producto", values="precio")
    D = pd.DataFrame({"ipc_carnes": lv(ipc), "cortes": lv(cortes).mean(axis=1)})
    for c in ("consumo", "novillos", "total"):
        D[c] = lv(P[f"{c}_mes"])
        D[c + "_25"] = (np.log(P[f"{c}_hasta25"]) - np.log(P[f"{c}_mes"].shift(1))) * 100
    D = D.dropna(subset=["consumo"])
    D.to_csv(C / "mag_mensual.csv", encoding="utf-8-sig", float_format="%.3f")
    L = ["=== HACIENDA (MAG Cañuelas) vs CARNE EN EL IPC (GBA) — variación mensual % ===",
         f"Meses: {D.index.min()} a {D.index.max()} ({len(D)}). 'desest.' = sin el promedio de cada mes del año (2017-2024).", ""]
    for obj in ("ipc_carnes", "cortes"):
        L.append(f"Objetivo: {'IPC GBA carnes (01.1.2)' if obj == 'ipc_carnes' else 'precio promedio INDEC de 5 cortes vacunos (GBA)'}")
        for c in ("consumo", "novillos", "total"):
            partes = []
            for k in (0, 1, 2, 3):
                x = D[c].shift(k)
                q = pd.DataFrame({"y": D[obj], "x": x}).dropna()
                partes.append(f"rezago {k}: {q.y.corr(q.x):+.2f} / {desest(q.y).corr(desest(q.x)):+.2f}")
            q = pd.DataFrame({"y": D[obj], "x": D[c + "_25"]}).dropna()
            L.append(f"   {c:9} (crudo / desest.)  " + " | ".join(partes) + f" | hasta el 25: {q.y.corr(q.x):+.2f} / {desest(q.y).corr(desest(q.x)):+.2f}")
        L.append("")
    # nowcast: IPC carnes_t = a + b0 * hacienda hasta el 25 de t + b1 * hacienda t-1 + c * IPC carnes t-1 (desest.)
    E = pd.DataFrame({"y": desest(D.ipc_carnes), "h25": desest(D.consumo_25), "h1": desest(D.consumo.shift(1)),
                      "y1": desest(D.ipc_carnes.shift(1)), "estac": D.ipc_carnes - desest(D.ipc_carnes), "real": D.ipc_carnes}).dropna()
    tr, te = E[E.index <= pd.Period("2023-12", "M")], E[E.index >= pd.Period("2024-01", "M")]
    for xs in (["y1"], ["h25", "h1"], ["h25", "h1", "y1"]):
        r = sm.OLS(tr.y, sm.add_constant(tr[xs])).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
        pred = r.predict(sm.add_constant(te[xs], has_constant="add")) + te.estac
        L.append(f"Nowcast IPC carnes con {'+'.join(xs):10}: " + ", ".join(f"{x} {r.params[x]:.2f} (t {r.tvalues[x]:.1f})" for x in xs)
                 + f" | R² {r.rsquared:.2f} | prueba 2024-26 (n={len(te)}): error medio {np.abs(pred - te.real).mean():.2f} pp")
    L.append(f"Solo estacionalidad (sin datos): error medio 2024-26 {np.abs(te.estac - te.real).mean():.2f} pp")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_validacion_mag.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
