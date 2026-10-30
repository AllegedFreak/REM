# probar_reglas_regulados.py (29-oct-2026; análisis, fuera del circuito diario)
# ¿Las reglas de ajuste de los regulados (datos\reglas_regulados.csv) explican lo que mide el INDEC?
# Para cada rubro del IPC GBA: y = variación % mensual del rubro; regla = lo que diría la norma con datos ya publicados.
#   transporte público (07.3)       : IPC t-2 + 2 pp                                  (colectivos CABA y Provincia)
#   electricidad y gas (04.5)       : 0,58 IPIM t-2 + 0,42 IPC t-2  (promedio de luz 67/33 y gas 50/50; solo márgenes)
#   prepagas (06.4.1)               : IPC t-2                                         (copian el último IPC publicado)
#   educación (10)                  : índice de salarios t-2                          (topes según paritaria docente)
#   placebos (no tienen regla)      : telefonía (08.3), tabaco (02.2), combustibles (07.2.2) contra IPC t-2
# Pruebas (errores robustos Newey-West, 2 rezagos):
#   1) y = a + b·regla: b, t, R²
#   2) regla pura (a = 0, b = 1): p de Wald; p alto = no se rechaza que el rubro siga la regla
#   3) pronóstico en tiempo real contra el método actual (promedio de 3 meses del rubro):
#      "regla" = regla pura; "regla+sesgo" = regla + promedio de (y - regla) de los meses anteriores (mín. 6)
#      MAE y p de Diebold-Mariano (HLN, h=1) contra el promedio de 3 meses
# El agua no está en las aperturas del IPC: no se puede probar.
# Informe: datos\informe_reglas_regulados.txt
import sys
from datetime import datetime
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
MIN_SESGO = 6


def serie(h, id_):
    s = h[h["id"] == id_].set_index("fecha")["valor"]
    s.index = pd.PeriodIndex(pd.to_datetime(s.index), freq="M")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return (s / s.shift(1) - 1) * 100


def main():
    h = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    ipc = D.ipc_categorias()["general"]
    ipc = (ipc / ipc.shift(1) - 1) * 100
    ipim = serie(h, "indec_ipim_ng_nivel_general")
    sal = serie(h, "datosgob_149.1_TL_INDIIOS_OCTU_0_21")
    g = lambda c: serie(h, f"indec_ipcap_gba_{c}")
    PRUEBAS = [  # nombre, serie, regla, desde
        ("transporte público (07.3)", g("07.3"), ipc.shift(2) + 2, "2024-07"),
        ("electricidad y gas (04.5)", g("04.5"), 0.58 * ipim.shift(2) + 0.42 * ipc.shift(2), "2025-06"),
        ("prepagas (06.4.1)", g("06.4.1"), ipc.shift(2), "2024-07"),
        ("educación (10)", g("10"), sal.shift(2), "2024-07"),
        ("placebo: telefonía (08.3)", g("08.3"), ipc.shift(2), "2024-07"),
        ("placebo: tabaco (02.2)", g("02.2"), ipc.shift(2), "2024-07"),
        ("placebo: combustibles (07.2.2)", g("07.2.2"), ipc.shift(2), "2024-07"),
    ]
    L = [f"=== REGLAS DE LOS REGULADOS CONTRA EL IPC GBA | {datetime.now():%Y-%m-%d %H:%M} ===",
         "Errores en pp de variación mensual del rubro. HAC = Newey-West con 2 rezagos.", "",
         f"{'rubro':32}{'meses':>6}{'b':>7}{'t':>7}{'R²':>6}{'p pura':>8} | {'MAE 3m':>7}{'regla':>7}{'p DM':>6}{'r+sesgo':>8}{'p DM':>6}"]
    for nom, y, r, desde in PRUEBAS:
        z = pd.DataFrame({"y": y, "r": r})
        z["p3"] = (z["y"].shift(1) + z["y"].shift(2) + z["y"].shift(3)) / 3   # método actual (ya publicado al pronosticar t)
        z = z[z.index >= pd.Period(desde, "M")].dropna()
        X = sm.add_constant(z["r"])
        f = sm.OLS(z["y"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
        p_pura = float(f.wald_test("const = 0, r = 1", scalar=True).pvalue)
        sesgo = (z["y"] - z["r"]).expanding().mean().shift(1)
        sesgo[np.arange(len(z)) < MIN_SESGO] = np.nan
        z["rs"] = z["r"] + sesgo
        e3, er = (z["p3"] - z["y"]).values, (z["r"] - z["y"]).values
        w = z.dropna(subset=["rs"])
        ers, e3w = (w["rs"] - w["y"]).values, (w["p3"] - w["y"]).values
        L.append(f"{nom:32}{len(z):6d}{f.params['r']:7.2f}{f.tvalues['r']:7.1f}{f.rsquared:6.2f}{p_pura:8.2f} | "
                 f"{np.abs(e3).mean():7.2f}{np.abs(er).mean():7.2f}{diebold_mariano(er, e3, 1):6.2f}"
                 f"{np.abs(ers).mean():8.2f}{diebold_mariano(ers, e3w, 1):6.2f}")
        if not nom.startswith("placebo"):
            ult = z.tail(4)
            L.append("      últimos meses (real / regla / prom. 3m): "
                     + "  ".join(f"{p}: {a:.1f}/{b:.1f}/{c:.1f}" for p, a, b, c in zip(ult.index, ult["y"], ult["r"], ult["p3"])))
    L += ["", "Lectura: la regla sirve si b ≈ 1 con t alto, el MAE de la regla es menor que el del promedio de 3 meses",
          "y el p de Diebold-Mariano es < 0,10. Los placebos muestran cuánto 'explica' el IPC t-2 por casualidad."]
    texto = "\n".join(L)
    print(texto)
    (C / "informe_reglas_regulados.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
