# comparar_top10.py
# ¿El Top 10 del REM (las consultoras con mejor desempeño reciente) pronostica mejor la inflación que la mediana de todas?
# Compara, para cada relevamiento y mes pronosticado, el error contra lo que pasó (IPC INDEC) de la mediana del Top 10 y de
# la mediana total; por horizonte, por período y a 12 meses. Test de Diebold-Mariano con corrección HLN.
# Salida: datos/informe_top10.txt
# Uso: py -3.13 comparar_top10.py
import sys

import numpy as np
import pandas as pd

import datos_macro as D
from backtest_rem import diebold_mariano

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

INFORME = D.CARPETA / "informe_top10.txt"


def mensual(df):
    g = df[(df["variable_std"] == "ipc_general") & (df["tipo_periodo"] == "fecha") & (df["unidad"] == "var. % mensual")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["objetivo"] = pd.to_datetime(g["periodo_fin"]).dt.to_period("M")
    return g.groupby(["origen", "objetivo"])["mediana"].mean()


def doce(df):
    g = df[(df["variable_std"] == "ipc_general") & (df["tipo_periodo"] == "proximos_meses") & (df["horizonte_meses"] == 12)].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    return g.groupby("origen")["mediana"].mean()


def main():
    total = pd.read_parquet(D.CARPETA / "rem_normalizada.parquet")
    top = pd.read_parquet(D.CARPETA / "rem_top10_normalizada.parquet")
    ipc = pd.read_parquet(D.CARPETA / "ipc_indec.parquet")
    ipc.index = pd.to_datetime(ipc["mes"]).dt.to_period("M")
    real = ipc["ipc_general_indice_var_mensual"].dropna()
    lp = np.log((1 + real / 100).cumprod())

    m = pd.concat([mensual(total).rename("total"), mensual(top).rename("top10")], axis=1, join="inner").reset_index()
    m["h"] = [(o - r).n for r, o in zip(m["origen"], m["objetivo"])]
    m["real"] = m["objetivo"].map(real)
    m = m.dropna(subset=["real"])
    m["e_total"], m["e_top"] = m["total"] - m["real"], m["top10"] - m["real"]

    L = ["=== TOP 10 DEL REM contra la MEDIANA de todas las consultoras (IPC general, error contra lo que pasó) ===",
         f"Relevamientos con Top 10: {m['origen'].min()} a {m['origen'].max()}. Error = pronóstico - IPC INDEC (pp).",
         "p = Diebold-Mariano con corrección HLN (igual error absoluto). 'Top mejor' = % de casos en que el Top 10 se equivocó menos.", ""]
    L.append(f"{'':5} {'n':>4} {'MAE total':>10} {'MAE top10':>10} {'dif %':>7} {'p':>6} | {'sesgo total':>11} {'sesgo top10':>11} | "
             f"{'Top mejor':>9} | {'|top-total|':>11}")
    for h in range(0, 7):
        q = m[m["h"] == h].sort_values("origen")
        if len(q) < 10:
            continue
        p = diebold_mariano(q["e_top"].values, q["e_total"].values, max(h, 1))
        a, b = q["e_total"].abs().mean(), q["e_top"].abs().mean()
        L.append(f"m{h:<4} {len(q):4} {a:10.2f} {b:10.2f} {(b / a - 1) * 100:+6.1f}% {p:6.3f} | {q['e_total'].mean():+11.2f} "
                 f"{q['e_top'].mean():+11.2f} | {(q['e_top'].abs() < q['e_total'].abs()).mean() * 100:8.0f}% | "
                 f"{(q['top10'] - q['total']).abs().mean():11.2f}")
    L += ["", "Por período (m1 y m3):"]
    for h in (1, 3):
        for nom, desde, hasta in [("2020-2023", "2020-01", "2023-12"), ("2024-2026", "2024-01", "2026-12")]:
            q = m[(m["h"] == h) & (m["origen"] >= pd.Period(desde, "M")) & (m["origen"] <= pd.Period(hasta, "M"))].sort_values("origen")
            if len(q) < 10:
                continue
            p = diebold_mariano(q["e_top"].values, q["e_total"].values, max(h, 1))
            a, b = q["e_total"].abs().mean(), q["e_top"].abs().mean()
            L.append(f"   m{h} {nom}: n={len(q):3} MAE total {a:.2f} | top10 {b:.2f} ({(b / a - 1) * 100:+.1f}%), p={p:.3f} | "
                     f"sesgo total {q['e_total'].mean():+.2f}, top10 {q['e_top'].mean():+.2f}")

    # 12 meses: variación de P entre t y t+12 (meses t+1..t+12)
    d = pd.concat([doce(total).rename("total"), doce(top).rename("top10")], axis=1, join="inner")
    d["real"] = [(np.exp(lp.get(o + 12, np.nan) - lp.get(o, np.nan)) - 1) * 100 for o in d.index]
    d = d.dropna().sort_index()
    if len(d) >= 10:
        e_t, e_p = d["total"] - d["real"], d["top10"] - d["real"]
        p = diebold_mariano(e_p.values, e_t.values, 12)
        L += ["", f"Próximos 12 meses: n={len(d)} | MAE total {e_t.abs().mean():.1f} pp | top10 {e_p.abs().mean():.1f} pp "
                  f"({(e_p.abs().mean() / e_t.abs().mean() - 1) * 100:+.1f}%), p={p:.3f} | sesgo total {e_t.mean():+.1f}, top10 {e_p.mean():+.1f} | "
                  f"Top mejor {(e_p.abs() < e_t.abs()).mean() * 100:.0f}%"]
    L += ["", "Cuidado: el Top 10 se elige cada mes por el desempeño PASADO de cada consultora (el BCRA lo publica así);",
          "no hay sesgo de mirar el futuro. Los horizontes se superponen: los p de m3-m6 son menos confiables (exigir p < 0,025)."]
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
