# probar_termometro_general.py (17-nov-2026; análisis, fuera del circuito diario)
# ¿Mejora la general "desde abajo" del mes en curso (G m0, replicar_general.py) si se corrige con el termómetro del súper?
# Termómetro = variación de la canasta SEPA del mes, días 1 a 25, contra el mes anterior (sepa_indice_mensual.parquet,
# Nacional, variacion_25_pct; lo que se conoce al cerrar el REM). Hay datos oct-2024 a mar-2026 y desde ago-2026.
# Correcciones, estimadas en tiempo real con los meses anteriores ya publicados (mínimo MIN):
#   C1 a + b·termo   : G + a + b·termómetro           (como la corrección de I en replicar_rem.py, sección 9)
#   C2 mezcla        : G + w·(termómetro − G)          (w entre 0 y 1, sin constante: cuánto creerle al súper)
# Medida: error absoluto medio contra la general del INDEC y p de Diebold-Mariano contra G sin corregir.
# Ojo: muestra muy chica (~12 meses evaluados); sirve para orientar, no para decidir solo con esto.
# Informe: datos\informe_termometro_general.txt
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_rem import diebold_mariano

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
MIN = 8


def main():
    t = pd.read_parquet(C / "sepa_indice_mensual.parquet")
    t = t[t["region"] == "Nacional"].set_index("mes")["variacion_25_pct"].dropna()
    a = pd.read_csv(C / "replica_general.csv", encoding="utf-8-sig")
    a = a[a["h"] == 0].copy()
    a["termo"] = a["obj"].map(t)
    a = a.dropna(subset=["termo"]).sort_values("obj").reset_index(drop=True)
    for k in ("C1 a + b·termo", "C2 mezcla"):
        a[k] = np.nan
    a["w"], a["b"] = np.nan, np.nan
    for k, x in a.iterrows():
        tr = a[(a["obj"] < x["obj"])].dropna(subset=["real"])     # meses ya publicados al relevar
        tr = tr[tr["obj"] <= str(pd.Period(x["obj"], "M") - 1)]
        if len(tr) < MIN:
            continue
        y = (tr["real"] - tr["abajo"]).values
        X = np.column_stack([np.ones(len(tr)), tr["termo"].values])
        c0, c1 = np.linalg.lstsq(X, y, rcond=None)[0]
        a.loc[k, "C1 a + b·termo"] = x["abajo"] + c0 + c1 * x["termo"]
        d = (tr["termo"] - tr["abajo"]).values
        w = float(np.clip(d @ y / (d @ d), 0, 1)) if d @ d > 0 else 0.0
        a.loc[k, "C2 mezcla"] = x["abajo"] + w * (x["termo"] - x["abajo"])
        a.loc[k, "w"], a.loc[k, "b"] = w, c1
    a.to_csv(C / "probar_termometro_general.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    ev = a.dropna(subset=["real", "C1 a + b·termo"])
    e0 = (ev["abajo"] - ev["real"]).values
    L = [f"=== TERMÓMETRO DEL SÚPER EN LA GENERAL DEL MES EN CURSO (G m0) | {datetime.now():%Y-%m-%d %H:%M} ===",
         f"Meses evaluados: {len(ev)} ({ev['obj'].min()} a {ev['obj'].max()}); estimación con los meses anteriores (mín. {MIN}).",
         "Error absoluto medio contra la general del INDEC (pp); p = Diebold-Mariano contra G sin corregir.", ""]
    for nombre, col in (("G sin corregir", "abajo"), ("C1 a + b·termo", "C1 a + b·termo"), ("C2 mezcla", "C2 mezcla"),
                        ("Termómetro solo", "termo"), ("REM del mes", "rem")):
        er = (ev[col] - ev["real"]).values
        p = diebold_mariano(er, e0, 1) if col != "abajo" else np.nan
        L += [f"   {nombre:<18} {np.nanmean(np.abs(er)):5.2f}" + ("" if np.isnan(p) else f"   p {p:4.2f}")]
    L += ["", "Mes por mes (real, G, C1, C2, termómetro, REM; w = peso del súper en C2):"]
    for _, x in ev.iterrows():
        L += [f"   {x['obj']}  real {x['real']:5.2f} | G {x['abajo']:5.2f} | C1 {x['C1 a + b·termo']:5.2f} | "
              f"C2 {x['C2 mezcla']:5.2f} | súper {x['termo']:5.2f} | REM {x['rem']:5.2f} | w {x['w']:.2f}"]
    hoy = a[a["real"].isna()].tail(1)
    for _, x in hoy.iterrows():
        L += ["", f"Hoy ({x['obj']}): G {x['abajo']:.2f} | C1 {x['C1 a + b·termo']:.2f} | C2 {x['C2 mezcla']:.2f} | súper {x['termo']:.2f}"]
    texto = "\n".join(L)
    (C / "informe_termometro_general.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
