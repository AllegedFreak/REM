# velocidad_dinero.py
# Antes de usar MV = PY como evidencia: velocidad de la base y de M2 año por año (V = PIB nominal / dinero promedio del año)
# y comparación del crecimiento del PIB oficial con un indicador de actividad alternativo (2008-2015 cuestionado por el FMI).
# Salida: datos/informe_velocidad.txt y datos/velocidad_anual.csv
# Uso: py -3.13 velocidad_dinero.py
import sys
import warnings

import numpy as np
import pandas as pd

import datos_macro as D

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

INFORME = D.CARPETA / "informe_velocidad.txt"
TABLA = D.CARPETA / "velocidad_anual.csv"


def anual(mensual, minimo=12):
    g = mensual.groupby(mensual.index.year)
    return g.mean()[g.count() >= minimo]


def main():
    pib = D.pib_anual()
    din = D.dinero()
    act, info = D.actividad()
    pr = D.precios()
    niveles_p = pr.apply(D.indice)

    L = ["=== VELOCIDAD DEL DINERO Y ACTIVIDAD ===", ""]
    # ---- actividad alternativa ----
    mod = info["modelo"]
    L.append("1) Actividad alternativa (indicadores físicos: cemento, acero, autos, electricidad, gas industrial)")
    L.append(f"   Variación interanual del EMAE explicada por la de los indicadores, estimada SOLO en 2005-2007 y 2016 en adelante "
             f"({info['n_ajuste']} meses): R² = {mod.rsquared:.2f}")
    L.append("   Pesos: " + ", ".join(f"{k} {v:+.3f}" for k, v in mod.params.items()))
    L.append(f"   Validación: estimado solo con 2016+, predice 2005-2007 con error medio {info['validacion_mae']:.1f} pp "
             f"de crecimiento interanual (sesgo {info['validacion_sesgo']:+.1f} pp); suponer el crecimiento promedio: {info['validacion_ingenuo']:.1f} pp")
    a_emae = anual(act["emae"])
    a_mix = anual(act["emae_sin_0815"])
    L.append("")
    L.append(f"   {'año':5} {'EMAE oficial':>13} {'alternativo':>12} {'diferencia':>11}   (crecimiento del promedio anual, %)")
    crec = lambda s: (s / s.shift(1) - 1) * 100
    ce, cm = crec(a_emae), crec(a_mix)
    for a in range(2005, int(a_emae.index.max()) + 1):
        marca = "  <- tramo reemplazado" if 2008 <= a <= 2015 else ""
        L.append(f"   {a:5} {ce[a]:13.1f} {cm[a]:12.1f} {cm[a] - ce[a]:11.1f}{marca}")
    acum_of = a_emae[2015] / a_emae[2007]
    acum_alt = a_mix[2015] / a_mix[2007]
    L.append(f"   Acumulado 2007-2015: oficial {(acum_of - 1) * 100:+.1f}%, alternativo {(acum_alt - 1) * 100:+.1f}%")
    L.append("")

    # ---- velocidad ----
    t = pd.DataFrame({"pib_nominal": pib["pib_nominal"], "fuente_nominal": pib["fuente_nominal"], "pib_real": pib["pib_real"]})
    t["deflactor"] = t["pib_nominal"] / t["pib_real"] * 100   # implícito (la serie publicada llega solo hasta 2024)
    t["base"] = anual(din["base"])
    t["m2"] = anual(din["m2"])
    t["v_base"] = t["pib_nominal"] / t["base"]
    t["v_m2"] = t["pib_nominal"] / t["m2"]
    t["pib_real_alt"] = t["pib_real"] * (a_mix / a_emae).reindex(t.index)
    for col in pr.columns:
        t[f"ipc_{col}"] = anual(niveles_p[col])
    t = t.dropna(subset=["pib_nominal", "base", "m2"])
    t.to_csv(TABLA, encoding="utf-8-sig", float_format="%.6g")

    g = lambda c: (t[c] / t[c].shift(1) - 1) * 100
    L.append("2) Velocidad = PIB nominal / dinero promedio del año. Crecimientos en % anual (promedio del año contra el anterior).")
    L.append("   PIB nominal: INDEC (2004-2005: PIB real x deflactor). Los IPC son promedio anual de cada versión.")
    L.append("")
    L.append(f"   {'año':5} {'PIB nom.':>9} {'PIB real':>9} {'real alt':>9} {'deflact.':>9} {'IPC ofic':>9} {'IPC Neuq':>9} {'IPC IPCBA':>9} "
             f"{'base':>7} {'V base':>7} {'M2':>7} {'V M2':>6}")
    for a in t.index:
        f = lambda c: g(c)[a]
        fmt = lambda x: "      -" if pd.isna(x) else f"{x:7.1f}"
        L.append(f"   {a:5} {fmt(f('pib_nominal')):>9} {fmt(f('pib_real')):>9} {fmt(f('pib_real_alt')):>9} {fmt(f('deflactor')):>9} "
                 f"{fmt(f('ipc_oficial')):>9} {fmt(f('ipc_neuquen')):>9} {fmt(f('ipc_ipcba2012')):>9} "
                 f"{fmt(f('base')):>7} {t.loc[a, 'v_base']:7.1f} {fmt(f('m2')):>7} {t.loc[a, 'v_m2']:6.2f}")
    L.append("")
    for c, nom in (("v_base", "base"), ("v_m2", "M2")):
        v = t[c]
        tend = np.polyfit(v.index, np.log(v), 1)[0] * 100
        L.append(f"   Velocidad de la {nom}: promedio {v.mean():.2f}, mínimo {v.min():.2f} ({v.idxmin()}), máximo {v.max():.2f} ({v.idxmax()}), "
                 f"coef. de variación {v.std() / v.mean() * 100:.0f}%, tendencia {tend:+.1f}% por año")
    L.append("")
    L.append("3) Descomposición MV = PY en logaritmos (puntos de log x 100, acumulado en cada tramo): dinero + velocidad = PIB nominal")
    for a0, a1 in ((t.index.min(), 2015), (2015, t.index.max()), (t.index.min(), t.index.max())):
        dl = lambda c: (np.log(t.loc[a1, c]) - np.log(t.loc[a0, c])) * 100
        L.append(f"   {a0}-{a1}: base {dl('base'):6.0f} + V base {dl('v_base'):5.0f} | M2 {dl('m2'):6.0f} + V M2 {dl('v_m2'):5.0f} "
                 f"= PIB nominal {dl('pib_nominal'):6.0f} = deflactor {dl('deflactor'):6.0f} + PIB real {dl('pib_real'):5.0f}")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
