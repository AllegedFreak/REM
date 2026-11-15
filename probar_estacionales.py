# probar_estacionales.py (14-nov-2026; análisis, fuera del circuito diario)
# ¿Hay una regla mejor que la actual para los ESTACIONALES de la general "desde abajo" (replicar_general.py, m0-m2)?
# Motivo: octubre 2026 estacionales real 5,52% vs nuestro 0,95%; explica ~0,43 de los 0,54 pp de error de la general.
# Tiempo real: en el relevamiento t se conoce el IPC por categorías hasta u = t-1; se pronostica el mes b = t+h (h 0..2).
# Reglas (e = estacionales, var. % mensual; e_y = e del mismo mes y años antes; cm_y = promedio centrado de 12 meses
# alrededor de ese mes; n = núcleo):
#   R0 actual        : promedio de los últimos 12 meses + PROMEDIO de (e_y - cm_y), y = 1..3
#   R1 mediana 3     : igual, con MEDIANA de los 3 desvíos
#   R2 todos prom.   : promedio de los desvíos de todos los años disponibles (desde 2017)
#   R3 todos med.    : mediana de los desvíos de todos los años disponibles
#   R4 sin estac.    : solo el promedio de 12 meses (referencia)
#   R5 año anterior  : promedio de 12 meses + desvío del mismo mes del año anterior (y = 1)
#   R6 núcleo+       : núcleo esperada + MEDIANA de (e_y - n_y), todos los años; núcleo esperada = promedio de los
#                      últimos 3 meses de la núcleo (persistencia; así no depende de que exista nuestro N)
#   R7 núcleo+ (3)   : igual que R6 con los últimos 3 años
# Medida: error absoluto medio de los estacionales y su efecto en la general (× peso efectivo de los estacionales);
# p de Diebold-Mariano (HLN) contra R0. Períodos: 2019-2024 y 2025-26 (régimen actual), como el resto de los informes.
# Informe: datos\informe_estacionales.txt
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import datos_macro as D
from backtest_rem import diebold_mariano

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
W0 = {"nucleo": 0.6776, "regulados": 0.2087, "estacional": 0.1133}
REGLAS = ["R0 actual", "R1 mediana 3", "R2 todos prom.", "R3 todos med.", "R4 sin estac.", "R5 año anterior",
          "R6 núcleo+", "R7 núcleo+ (3)"]


def main():
    c = D.ipc_categorias()[["general", "nucleo", "regulados", "estacional"]]
    c.index = pd.PeriodIndex(c.index, freq="M")
    m = (c / c.shift(1) - 1) * 100
    e, n = m["estacional"], m["nucleo"]

    def cm(p):          # promedio centrado de 12 meses alrededor de p (como en replicar_general.py)
        return np.nanmean([e.get(p - j, np.nan) for j in range(-5, 7)])

    filas = []
    for o in pd.period_range("2019-01", m.index.max() + 1, freq="M"):
        u = o - 1
        if u not in m.index:
            continue
        for h in range(3):
            b = o + h
            # años con el mismo mes ya conocidos, con su promedio centrado completo (hasta u)
            anios = [y for y in range(1, 12) if (b - 12 * y - 5) >= m.index.min() + 1 and (b - 12 * y + 5) <= u]
            if len(anios) < 3:
                continue
            tend = np.mean([e[u - i] for i in range(12)])
            dev = {y: e[b - 12 * y] - cm(b - 12 * y) for y in anios}
            rel = {y: e[b - 12 * y] - n[b - 12 * y] for y in anios}
            nuc = np.mean([n[u - i] for i in range(3)])
            s = {k: W0[k] * c[k][u] / c["general"][u] for k in W0}
            f = {"origen": o, "h": h, "obj": b, "real": e.get(b, np.nan), "peso": s["estacional"] / sum(s.values()),
                 "R0 actual": tend + np.mean([dev[y] for y in (1, 2, 3)]),
                 "R1 mediana 3": tend + np.median([dev[y] for y in (1, 2, 3)]),
                 "R2 todos prom.": tend + np.mean(list(dev.values())),
                 "R3 todos med.": tend + np.median(list(dev.values())),
                 "R4 sin estac.": tend,
                 "R5 año anterior": tend + dev[1],
                 "R6 núcleo+": nuc + np.median(list(rel.values())),
                 "R7 núcleo+ (3)": nuc + np.median([rel[y] for y in (1, 2, 3)])}
            filas.append(f)
    X = pd.DataFrame(filas)
    X.to_csv(C / "probar_estacionales.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    ev = X.dropna(subset=["real"])

    L = [f"=== ESTACIONALES DE LA GENERAL 'DESDE ABAJO': REGLAS EN TIEMPO REAL | {datetime.now():%Y-%m-%d %H:%M} ===",
         "Error absoluto medio de los estacionales (pp de su variación mensual) y, entre corchetes, efecto en la general (pp).",
         "p = Diebold-Mariano contra R0 (p < 0,05 = diferencia real)."]
    for nombre, q in (("2019-2024", ev[ev["obj"] <= pd.Period("2024-12", "M")]),
                      ("2025-26 (régimen actual)", ev[ev["obj"] >= pd.Period("2025-01", "M")])):
        L += ["", f"== {nombre}"]
        for h in range(3):
            qh = q[q["h"] == h]
            L += [f"   m{h} (n {len(qh)})"]
            e0 = (qh["R0 actual"] - qh["real"]).values
            for r in REGLAS:
                er = (qh[r] - qh["real"]).values
                p = diebold_mariano(er, e0, h + 1) if r != "R0 actual" else np.nan
                L += [f"      {r:<16} {np.mean(np.abs(er)):5.2f}  [{np.mean(np.abs(er) * qh['peso']):4.2f}]"
                      + ("" if np.isnan(p) else f"   p {p:4.2f}")]
    oct26 = ev[(ev["obj"] == pd.Period("2026-10", "M"))]
    if len(oct26):
        L += ["", "== Octubre 2026 (real 5,52%), por horizonte"]
        for _, x in oct26.iterrows():
            L += [f"   m{x['h']}: " + " | ".join(f"{r.split()[0]} {x[r]:.2f}" for r in REGLAS)]
    hoy = X[X["origen"] == X["origen"].max()]
    L += ["", f"== Pronóstico de hoy (relevamiento {X['origen'].max()})"]
    for _, x in hoy.iterrows():
        L += [f"   {x['obj']}: " + " | ".join(f"{r.split()[0]} {x[r]:.2f}" for r in REGLAS)]
    texto = "\n".join(L)
    (C / "informe_estacionales.txt").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
