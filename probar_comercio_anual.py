# probar_comercio_anual.py (30-oct-2026; análisis, fuera del circuito diario)
# AÑO SIGUIENTE de exportaciones e importaciones "como el REM". Hoy A arma el año siguiente encadenando: cada mes = el mismo
# mes del año anterior × la relación estimada para el horizonte de 6 meses (más allá de t+6 se repite la de h6). Para
# 2027 eso da exportaciones −1,6% contra 2026, mientras el REM espera +4,1% (y en 2024-26 el REM SUBESTIMÓ siempre).
# Prueba (definida antes de mirar resultados):
#   A_anual = nuestro año en curso (A) × exp(ĝ), con ĝ = Huber del crecimiento que espera el REM, log(REM año+1 / REM año en
#             curso), sobre g3, g12, último mes, mes del relevamiento y, en exportaciones, precios de granos y aceites
#             (variación interanual, Banco Mundial). Ventana acumulada, mínimo 24 informes; tiempo real.
#   Criterio: se adopta si no empeora contra el dato real y se acerca al REM.
# Errores en % (100·|log|). Informe: datos\informe_comercio_anual.txt
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_rem import diebold_mariano
from replicar_comercio import MIN, huber

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"


def main():
    X = pd.read_csv(C / "replica_comercio.csv", encoding="utf-8-sig")
    X["origen"] = pd.PeriodIndex(X["origen"], freq="M")
    L = [f"=== AÑO SIGUIENTE DE EXPORTACIONES E IMPORTACIONES, COMO EL REM | {datetime.now():%Y-%m-%d %H:%M} ===",
         "encadenado = A de hoy; A_anual = nuestro año en curso × crecimiento que esperaría el REM (regresión, sin usar el REM del mes).",
         "Errores en % (100·|log|), tiempo real. p DM < 0,10 = diferencia significativa.", ""]
    for var in ("exportaciones", "importaciones"):
        cols = ["g3", "g12", "ult_rel", "mes"] + (["px_om", "px_gr"] if var == "exportaciones" else [])
        m0 = X[(X["var"] == var) & (X["tipo"] == "mes") & (X["h"] == 0)].set_index("origen")
        a = X[(X["var"] == var) & (X["tipo"] == "año")]
        a0, a1 = a[a["h"] == 0].set_index("origen"), a[a["h"] == 1].set_index("origen")
        D = pd.DataFrame({"rem0": a0["rem"], "A0": a0["A"], "rem1": a1["rem"], "real1": a1["real"], "enc": a1["A"],
                          "p10": a1["p10"], "p90": a1["p90"], "anio1": a1["objetivo"]}).join(m0[[c for c in cols if c != "mes"]])
        D["mes"] = [o.month for o in D.index]
        D["y"] = np.log(D["rem1"] / D["rem0"])
        D = D.sort_index()
        D["A_anual"] = np.nan
        for o, row in D.iterrows():
            tr = D[D.index < o].dropna(subset=cols + ["y"])
            if len(tr) >= MIN and row[cols].notna().all() and not np.isnan(row["A0"]):
                D.loc[o, "A_anual"] = row["A0"] * np.exp(huber(tr[cols].values, tr["y"].values, row[cols].values.astype(float)))
        D.to_csv(C / f"probar_comercio_anual_{var}.csv", encoding="utf-8-sig", float_format="%.2f")
        e = lambda z, c, ref: (100 * np.log(z[c] / z[ref])).abs().mean()
        L.append(f"##### {var.upper()} (año siguiente)")
        L.append(f"   {'informes':16}{'distancia al REM':>20} | {'error contra el real':>30} | {'sesgo':>14} | {'P10-P90':>13} | {'p DM A_anual vs':>16}")
        L.append(f"   {'':16}{'encad.':>10}{'A_anual':>10} | {'encad.':>8}{'A_anual':>8}{'REM':>8}{'n':>6} | {'A_anual':>7}{'REM':>7} | {'encad.':>6}{'A_an.':>7} | {'REM':>8}{'encad.':>8}")
        for per, mk in (("2022-2024", (D.index >= pd.Period("2022-01", "M")) & (D.index <= pd.Period("2024-12", "M"))),
                        ("2025-26", D.index > pd.Period("2024-12", "M")), ("todos", D.index >= pd.Period("2020-01", "M"))):
            z = D[mk].dropna(subset=["enc", "A_anual", "rem1"])
            zr = z.dropna(subset=["real1"])
            if len(z) < 3:
                continue
            cob = lambda c: ((z[c] >= z["p10"]) & (z[c] <= z["p90"])).mean() * 100
            sg = lambda c: (100 * np.log(zr[c] / zr["real1"])).mean() if len(zr) else np.nan
            try:
                p1 = diebold_mariano(np.log(zr.A_anual / zr.real1).values, np.log(zr.rem1 / zr.real1).values, 12) if len(zr) > 6 else np.nan
                p2 = diebold_mariano(np.log(zr.A_anual / zr.real1).values, np.log(zr.enc / zr.real1).values, 12) if len(zr) > 6 else np.nan
            except Exception:
                p1 = p2 = np.nan
            L.append(f"   {per + f' ({len(z)})':16}{e(z, 'enc', 'rem1'):10.1f}{e(z, 'A_anual', 'rem1'):10.1f} | "
                     f"{e(zr, 'enc', 'real1') if len(zr) else np.nan:8.1f}{e(zr, 'A_anual', 'real1') if len(zr) else np.nan:8.1f}"
                     f"{e(zr, 'rem1', 'real1') if len(zr) else np.nan:8.1f}{len(zr):6d} | {sg('A_anual'):+6.1f}%{sg('rem1'):+6.1f}% | "
                     f"{cob('enc'):5.0f}%{cob('A_anual'):6.0f}% | {p1:8.2f}{p2:8.2f}")
        u = D.iloc[-1]
        L += ["", f"   HOY ({D.index[-1]}): año en curso A {u['A0']:,.0f} | año siguiente: encadenado {u['enc']:,.0f} "
                  f"({(u['enc'] / u['A0'] - 1) * 100:+.1f}%), A_anual {u['A_anual']:,.0f} ({(u['A_anual'] / u['A0'] - 1) * 100:+.1f}%); "
                  f"REM anterior {D['rem1'].dropna().iloc[-1]:,.0f} ({(D['rem1'].dropna().iloc[-1] / D['rem0'].dropna().iloc[-1] - 1) * 100:+.1f}% "
                  f"contra su año en curso)", ""]
    texto = "\n".join(L)
    print(texto)
    (C / "informe_comercio_anual.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
