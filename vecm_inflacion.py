# vecm_inflacion.py
# VECM con dinero, precios, actividad y tipo de cambio (mensual, logaritmos, 2004 en adelante).
#   - 3 versiones del IPC después de 2006 (datos_macro.VERSIONES_PRECIOS)
#   - 3 versiones de actividad: EMAE oficial, EMAE con 2008-2015 reemplazado por el indicador alternativo, alternativo solo
#   - robustez: M2 en lugar de base; muestra 2016 en adelante
# Para cada especificación: rezagos (BIC), rango de cointegración (Johansen, traza 5%), relación de largo plazo
# normalizada en precios, velocidad de ajuste de los precios y respuesta de los precios a un shock de dinero.
# Antes: orden de integración de cada serie (ADF en niveles, primeras y segundas diferencias).
# Salida: datos/informe_vecm.txt y datos/vecm_resultados.csv
# Uso: py -3.13 vecm_inflacion.py
import sys
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import VECM, select_coint_rank, select_order

import datos_macro as D

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

INFORME = D.CARPETA / "informe_vecm.txt"
TABLA = D.CARPETA / "vecm_resultados.csv"
INICIO = pd.Period("2004-01", "M")
MAX_REZAGOS = 12
HORIZONTES = (3, 6, 12, 24, 36)
NOMBRES_ACT = {"emae": "EMAE oficial", "emae_sin_0815": "EMAE con 2008-15 alternativo", "alternativo": "alternativo solo"}


def ajustar(datos, nombre):
    """datos: DataFrame con columnas p, m, y, e (logs). Devuelve dict de resultados."""
    datos = datos.dropna()
    orden = select_order(datos, maxlags=MAX_REZAGOS, deterministic="ci")
    k = max(int(orden.bic), 1)
    rango = select_coint_rank(datos, det_order=0, k_ar_diff=k, method="trace", signif=0.05).rank
    res = VECM(datos, k_ar_diff=k, coint_rank=1, deterministic="ci").fit()   # 1 relación: la de largo plazo de precios
    beta = res.beta[:, 0] / res.beta[0, 0]
    se = res.stderr_beta[:, 0] / abs(res.beta[0, 0]) if hasattr(res, "stderr_beta") else np.full(len(beta), np.nan)
    alfa_p, t_alfa_p = res.alpha[0, 0] * res.beta[0, 0], res.tvalues_alpha[0, 0]
    vida_media = np.log(0.5) / np.log(1 + alfa_p) if -1 < alfa_p < 0 else np.nan
    # respuesta de precios a un shock de dinero: orden de Cholesky m, y, e, p (el dinero no reacciona en el mes a los demás)
    res2 = VECM(datos[["m", "y", "e", "p"]], k_ar_diff=k, coint_rank=1, deterministic="ci").fit()
    irf = res2.irf(max(HORIZONTES)).orth_irfs     # [horizonte, variable que responde, shock]
    impacto_m = irf[0, 0, 0]
    resp_p = irf[:, 3, 0] / impacto_m             # % en precios por cada 1% de shock inicial en el dinero
    resp_m = irf[:, 0, 0] / impacto_m
    final = resp_p[max(HORIZONTES)]
    mitad = next((h for h in range(len(resp_p)) if final > 0 and resp_p[h] >= final / 2), np.nan)
    r = {"especificacion": nombre, "meses": len(datos), "desde": str(datos.index.min()), "hasta": str(datos.index.max()),
         "rezagos": k, "rango_traza": rango,
         "elast_p_m": -beta[1], "se_m": se[1], "elast_p_y": -beta[2], "se_y": se[2], "elast_p_e": -beta[3], "se_e": se[3],
         "alfa_p": alfa_p, "t_alfa_p": t_alfa_p, "vida_media": vida_media,
         "dinero_a_36m": resp_m[max(HORIZONTES)], "mes_mitad_efecto": mitad}
    for h in HORIZONTES:
        r[f"p_{h}m"] = resp_p[h]
    return r


def ajustar_real(datos, nombre):
    """Versión en términos reales (para series casi I(2)): mr = m - p, y, q = e - p, pi = inflación mensual (100·Δp).
    Largo plazo = demanda de dinero: mr = b_y·y + b_pi·pi + b_q·q. Desvío positivo = exceso de dinero."""
    d = pd.DataFrame({"mr": datos["m"] - datos["p"], "y": datos["y"], "q": datos["e"] - datos["p"],
                      "pi": datos["p"].diff() * 100}).dropna()
    orden = select_order(d, maxlags=MAX_REZAGOS, deterministic="ci")
    k = max(int(orden.bic), 1)
    rango = select_coint_rank(d, det_order=0, k_ar_diff=k, method="trace", signif=0.05).rank
    res = VECM(d, k_ar_diff=k, coint_rank=1, deterministic="ci").fit()
    beta = res.beta[:, 0] / res.beta[0, 0]
    se = res.stderr_beta[:, 0] / abs(res.beta[0, 0])
    s = res.beta[0, 0]
    # shock de dinero real; orden de Cholesky mr, y, q, pi. Precios acumulados = suma de la inflación de cada mes
    irf = res.irf(max(HORIZONTES)).orth_irfs
    impacto = irf[0, 0, 0]
    # pi está en % mensual y el impacto en log: pi / impacto = pp por cada 100% de shock -> /100 = pp por cada 1%
    acum = np.cumsum(irf[:, 3, 0] / impacto / 100)   # % acumulado de suba de precios por cada 1% de shock
    final = acum[max(HORIZONTES)]
    mitad = next((h for h in range(len(acum)) if final > 0 and acum[h] >= final / 2), np.nan)
    r = {"especificacion": nombre, "meses": len(d), "rezagos": k, "rango_traza": rango,
         "elast_y": -beta[1], "se_y": se[1], "semi_pi": -beta[3], "se_pi": se[3], "elast_q": -beta[2], "se_q": se[2],
         "alfa_mr": res.alpha[0, 0] * s, "t_alfa_mr": res.tvalues_alpha[0, 0],
         "alfa_pi": res.alpha[3, 0] * s, "t_alfa_pi": res.tvalues_alpha[3, 0], "mes_mitad_efecto": mitad}
    for h in HORIZONTES:
        r[f"precios_{h}m"] = acum[h]
    return r


def main():
    pr = D.precios()
    din = D.dinero()
    tc = D.tipo_cambio()
    act, info = D.actividad()

    logp = {v: D.desestacionalizar_log(D.indice(pr[v])) for v in pr.columns}
    logm = {"base": D.desestacionalizar_log(din["base"].dropna()), "m2": D.desestacionalizar_log(din["m2"].dropna())}
    logy = {a: D.desestacionalizar_log(act[a].dropna()) for a in NOMBRES_ACT}
    loge = np.log(tc)

    L = ["=== VECM: DINERO, PRECIOS, ACTIVIDAD Y TIPO DE CAMBIO ===",
         "Variables en logaritmos, mensuales, desestacionalizadas (salvo el tipo de cambio A3500 promedio mensual).",
         "Precios: " + " | ".join(f"{k} = {v}" for k, v in D.VERSIONES_PRECIOS.items()),
         f"Actividad alternativa: indicadores físicos, ajustada en tramos confiables (R² {info['modelo'].rsquared:.2f}; validación "
         f"2005-2007 error {info['validacion_mae']:.1f} pp vs {info['validacion_ingenuo']:.1f} pp del promedio).", ""]

    # ---- orden de integración ----
    L.append("1) Orden de integración (p-valor ADF con constante; < 0,05 = estacionaria). 2004 en adelante.")
    L.append(f"   {'serie':32} {'nivel':>7} {'1ª dif.':>8} {'2ª dif.':>8}")
    def adf(s):
        s = s[s.index >= INICIO].dropna()
        return [adfuller(x.dropna(), autolag="AIC")[1] for x in (s, s.diff(), s.diff().diff())]
    series_ur = {**{f"precios {k}": v for k, v in logp.items()}, **{f"dinero {k}": v for k, v in logm.items()},
                 **{f"actividad {k}": v for k, v in logy.items()}, "tipo de cambio": loge}
    for n, s in series_ur.items():
        a = adf(s)
        L.append(f"   {n:32} {a[0]:7.3f} {a[1]:8.3f} {a[2]:8.3f}")
    L.append("   Si una serie necesita 2 diferencias (es I(2)), el VECM estándar no es del todo válido: leer con cuidado.")
    L.append("")

    # ---- especificaciones ----
    def armar(pv, mv, av, desde=INICIO):
        d = pd.DataFrame({"p": logp[pv], "m": logm[mv], "y": logy[av], "e": loge})
        return d[d.index >= desde]
    specs = []
    for pv in pr.columns:
        for av in NOMBRES_ACT:
            specs.append((f"{pv} | base | {NOMBRES_ACT[av]}", armar(pv, "base", av)))
    for pv in pr.columns:
        specs.append((f"{pv} | M2 | EMAE oficial", armar(pv, "m2", "emae")))
    for pv in ("oficial", "ipcba2012"):
        specs.append((f"{pv} | base | EMAE oficial | desde 2016", armar(pv, "base", "emae", pd.Period("2016-01", "M"))))

    filas = [ajustar(d, n) for n, d in specs]
    t = pd.DataFrame(filas)
    t.to_csv(TABLA, index=False, encoding="utf-8-sig", float_format="%.4f")

    L.append("2) Resultados por especificación (precios | dinero | actividad)")
    L.append("   Largo plazo: log P = a·log M + b·log Y + c·log E (+ constante). Teoría cuantitativa: a ≈ 1, b ≈ -1.")
    L.append("   α precios: cuánto del desvío de largo plazo corrigen los precios por mes (negativo = corrigen). Vida media en meses.")
    L.append("   Respuesta: % que suben los precios por cada 1% de shock inicial en el dinero, a 6/12/24/36 meses.")
    L.append("")
    L.append(f"   {'especificación':52} {'n':>4} {'rez':>3} {'rango':>5} | {'a (dinero)':>13} {'b (activ.)':>13} {'c (TC)':>13} | "
             f"{'α precios':>11} {'vida':>5} | {'6m':>5} {'12m':>5} {'24m':>5} {'36m':>5} {'mitad':>5}")
    for _, r in t.iterrows():
        L.append(f"   {r['especificacion']:52} {r['meses']:4} {r['rezagos']:3} {r['rango_traza']:5} | "
                 f"{r['elast_p_m']:6.2f} ({r['se_m']:4.2f}) {r['elast_p_y']:6.2f} ({r['se_y']:4.2f}) {r['elast_p_e']:6.2f} ({r['se_e']:4.2f}) | "
                 f"{r['alfa_p']:6.3f} ({r['t_alfa_p']:4.1f}) {r['vida_media']:5.0f} | "
                 f"{r['p_6m']:5.2f} {r['p_12m']:5.2f} {r['p_24m']:5.2f} {r['p_36m']:5.2f} {r['mes_mitad_efecto']:5.0f}")
    L.append("")
    L.append("   rango = cantidad de relaciones de cointegración según la traza al 5% (0 = no cointegran).")
    L.append("   Entre paréntesis: errores estándar (a, b, c) y t (α). 'mitad' = mes en que la suba de precios llega a la mitad de la de 36 meses.")
    L.append("")

    # ---- versión en términos reales ----
    L.append("3) VECM en términos reales (precios y dinero casi I(2)): dinero real (M/P), actividad, tipo de cambio real (E/P), inflación")
    L.append("   Orden de integración de las variables transformadas (p-valor ADF):")
    for pv in pr.columns:
        for n, s in (("dinero real (base/P)", logm["base"] - logp[pv]), ("tipo de cambio real", loge - logp[pv]),
                     ("inflación mensual", logp[pv].diff() * 100)):
            a = adf(s)
            L.append(f"     {pv:10} {n:22} nivel {a[0]:.3f} | 1ª dif. {a[1]:.3f}")
    filas_r = [ajustar_real(d, n) for n, d in specs]
    tr = pd.DataFrame(filas_r)
    tr.to_csv(D.CARPETA / "vecm_real_resultados.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    L.append("")
    L.append("   Largo plazo (demanda de dinero): log(M/P) = b·log Y + s·inflación + q·log(E/P). Teoría: b > 0 (≈ 1), s < 0 (con más")
    L.append("   inflación la gente quiere menos pesos: sube la velocidad). Desvío positivo = sobra dinero.")
    L.append("   α inflación > 0: cuando sobra dinero, la inflación sube en los meses siguientes (el mecanismo de Marcos).")
    L.append("   Precios: % acumulado que suben los precios por cada 1% de shock inicial en el dinero real.")
    L.append("")
    L.append(f"   {'especificación':52} {'n':>4} {'rez':>3} {'rango':>5} | {'b (activ.)':>13} {'s (inflación)':>14} {'q (TCR)':>13} | "
             f"{'α dinero':>12} {'α inflación':>13} | {'6m':>5} {'12m':>5} {'24m':>5} {'36m':>5} {'mitad':>5}")
    for _, r in tr.iterrows():
        L.append(f"   {r['especificacion']:52} {r['meses']:4} {r['rezagos']:3} {r['rango_traza']:5} | "
                 f"{r['elast_y']:6.2f} ({r['se_y']:4.2f}) {r['semi_pi']:7.3f} ({r['se_pi']:4.3f}) {r['elast_q']:6.2f} ({r['se_q']:4.2f}) | "
                 f"{r['alfa_mr']:6.3f} ({r['t_alfa_mr']:4.1f}) {r['alfa_pi']:7.3f} ({r['t_alfa_pi']:4.1f}) | "
                 f"{r['precios_6m']:5.2f} {r['precios_12m']:5.2f} {r['precios_24m']:5.2f} {r['precios_36m']:5.2f} {r['mes_mitad_efecto']:5.0f}")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
