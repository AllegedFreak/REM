# verificar_analisis.py
# Verifica que los MÉTODOS de los análisis funcionen bien (no modifica datos).
#   A) Simulación con respuesta conocida: datos fabricados donde se sabe el resultado; ¿las funciones lo encuentran?
#   B) Cruce con otras implementaciones: statsmodels o fuerza bruta.
#   C) Placebo: se desordena la emisión al azar; el efecto sobre los precios debería desaparecer.
#   D) Diagnóstico: residuos y estabilidad del VECM, sensibilidad a rezagos, corrección por comparaciones múltiples.
# Salida: tabla OK / AVISO / FALLA en pantalla y en datos/informe_verificacion_analisis.txt
# Uso: py -3.13 verificar_analisis.py          (tarda unos minutos)
# Código de salida: 0 = sin fallas | 1 = alguna FALLA
import itertools
import sys
import traceback
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import backtest_rem as BR          # noqa: E402
import datos_macro as D            # noqa: E402
import emision_tesoro as ET        # noqa: E402
import empalme_precios as EP       # noqa: E402
import vecm_inflacion as VI        # noqa: E402

INFORME = D.CARPETA / "informe_verificacion_analisis.txt"
rng = np.random.default_rng(20261014)
resultados = []


def chequeo(grupo, nombre):
    def deco(f):
        try:
            r = f()
            estado, detalle = r if isinstance(r, tuple) else ("OK", r or "")
        except AssertionError as e:
            estado, detalle = "FALLA", str(e)
        except Exception as e:
            estado, detalle = "FALLA", f"{type(e).__name__}: {e} | {traceback.format_exc().splitlines()[-3].strip()}"
        resultados.append((grupo, estado, nombre, detalle))
        print(f"  [{estado}] {nombre}")
        return f
    return deco


def periodos(n, desde="1990-01"):
    return pd.period_range(desde, periods=n, freq="M")


# ======================================================================== A) simulación con respuesta conocida
@chequeo("A", "Proyecciones locales recuperan un efecto conocido (emisión → precios desde el mes 8)")
def _():
    n, efecto, rho = 600, 0.05, 0.5
    idx = periodos(n)
    x = pd.Series(rng.normal(0, 5, n), index=idx)          # emisión (% de la base)
    dev = pd.Series(rng.normal(1, 3, n), index=idx)        # devaluación
    pi = np.zeros(n)
    for t in range(1, n):
        pi[t] = 1 + rho * pi[t - 1] + 0.1 * dev.iloc[t - 1] + (efecto * x.iloc[t - 8] if t >= 8 else 0) + rng.normal(0, 0.5)
    lp = np.log(D.indice(pd.Series(pi, index=idx)))
    r = ET.proyecciones_locales(lp, dev, x).set_index("h")
    verdad = lambda h: sum(efecto * rho ** (k - 8) for k in range(8, h + 1))   # suma de la respuesta de la inflación
    errores = []
    for h in (4, 12, 24):
        if not (r.loc[h, "inf90"] - 0.02 <= verdad(h) <= r.loc[h, "sup90"] + 0.02):
            errores.append(f"h={h}: estimado {r.loc[h, 'b']:.3f} [{r.loc[h, 'inf90']:.3f};{r.loc[h, 'sup90']:.3f}], verdadero {verdad(h):.3f}")
    assert not errores, "; ".join(errores)
    return (f"verdadero h4 0, h12 {verdad(12):.3f}, h24 {verdad(24):.3f} | estimado h4 {r.loc[4, 'b']:.3f}, "
            f"h12 {r.loc[12, 'b']:.3f}, h24 {r.loc[24, 'b']:.3f}")


@chequeo("A", "Diebold-Mariano: rechaza ~5% cuando los dos pronósticos son igual de buenos (tamaño)")
def _():
    out, peor = [], 0
    for h in (1, 6, 12):
        rech = 0
        for _ in range(400):
            z = rng.normal(0, 1, (120 + h, 2))
            e = np.array([np.convolve(z[:, j], np.ones(h), "valid")[:120] for j in range(2)])   # errores superpuestos MA(h-1)
            rech += BR.diebold_mariano(e[0], e[1], h) < 0.05
        tasa = rech / 400 * 100
        peor = max(peor, tasa)
        out.append(f"h={h}: {tasa:.0f}%")
    # con la corrección de Harvey-Leybourne-Newbold (14-oct-2026) queda ~8-9% a 6-12 meses: aceptable, exigir p < 0,025 ahí
    assert peor <= 15, "rechaza de más: " + ", ".join(out)
    if peor > 10:
        return "AVISO", f"rechaza de más con errores superpuestos ({', '.join(out)}): p-valores del DM a 6-24 meses son optimistas"
    return "OK", ", ".join(out) + " (ideal 5%; a 6-12 meses exigir p < 0,025)"


@chequeo("A", "Diebold-Mariano: detecta cuando un pronóstico es claramente peor (potencia)")
def _():
    rech = sum(BR.diebold_mariano(rng.normal(0, 1, 100), rng.normal(0, 1.6, 100), 1) < 0.05 for _ in range(300))
    assert rech / 300 > 0.7, f"detecta solo {rech / 3:.0f}%"
    return f"detecta la diferencia en {rech / 3:.0f}% de las simulaciones"


@chequeo("A", "sup-F encuentra la fecha de un quiebre conocido")
def _():
    n, tq = 116, 70
    x = rng.normal(2, 1, n)
    y = 0.1 + np.where(np.arange(n) < tq, 1.0, 1.6) * x + rng.normal(0, 0.3, n)
    X = sm.add_constant(x)
    sf, t = EP.sup_f(y, X, int(np.ceil(0.15 * n)))
    assert abs(t - tq) <= 3, f"encontró el quiebre en {t}, verdadero {tq}"
    return f"quiebre verdadero en {tq}, encontrado en {t} (sup-F {sf:.1f})"


@chequeo("A", "CUSUM: sin quiebre, cruza la banda del 5% ~5% de las veces")
def _():
    cruces = 0
    for _ in range(300):
        x = rng.normal(2, 1, 116)
        y = 0.1 + x + rng.normal(0, 0.3, 116)
        W, banda = EP.cusum_recursivo(y, sm.add_constant(x))
        cruces += (np.abs(W) > banda).any()
    tasa = cruces / 3
    assert tasa <= 10, f"cruza en {tasa:.0f}% (debería ~5%)"
    return f"cruza en {tasa:.1f}% de 300 simulaciones (ideal ≤ 5%)"


@chequeo("A", "VECM en términos reales recupera una demanda de dinero conocida")
def _():
    estim = []
    for _ in range(8):
        n = 270
        idx = periodos(n, "2004-01")
        y = np.cumsum(rng.normal(0.002, 0.01, n))                # actividad (log)
        q = np.cumsum(rng.normal(0, 0.02, n))                    # tipo de cambio real (log)
        pi = np.clip(2 + np.cumsum(rng.normal(0, 0.3, n)), 0.2, None)   # inflación mensual (%), I(1)
        u = np.zeros(n)
        for t in range(1, n):
            u[t] = 0.7 * u[t - 1] + rng.normal(0, 0.03)
        mr = 1.0 * y - 0.25 * pi + 0.3 * q + u                   # demanda de dinero verdadera: b=1, s=-0,25, c=0,3
        p = np.cumsum(pi / 100)
        datos = pd.DataFrame({"p": p, "m": mr + p, "y": y, "e": q + p}, index=idx)
        r = VI.ajustar_real(datos, "sim")
        estim.append((r["elast_y"], r["semi_pi"], r["elast_q"]))
    e = np.median(np.array(estim), axis=0)
    assert abs(e[1] + 0.25) < 0.08, f"semielasticidad estimada {e[1]:.3f}, verdadera −0,25"
    assert abs(e[0] - 1.0) < 0.4, f"elasticidad a la actividad {e[0]:.2f}, verdadera 1"
    return f"verdadero b=1, s=−0,25, c=0,3 | estimado (mediana de 8) b={e[0]:.2f}, s={e[1]:.3f}, c={e[2]:.2f}"


# ======================================================================== B) cruce con otras implementaciones
@chequeo("B", "Chow propio = statsmodels")
def _():
    n, t = 100, 50
    x = rng.normal(0, 1, n)
    y = 1 + 2 * x + np.where(np.arange(n) >= t, 0.5 + 0.5 * x, 0) + rng.normal(0, 1, n)
    X = sm.add_constant(x)
    propio = EP.f_quiebre(y, X, t)
    d = (np.arange(n) >= t).astype(float)
    m = sm.OLS(y, np.column_stack([X, d, d * x])).fit()
    ref = m.f_test(np.array([[0, 0, 1, 0], [0, 0, 0, 1]])).fvalue
    ref = float(np.squeeze(ref))
    assert abs(propio / ref - 1) < 1e-8, f"propio {propio:.6f} vs statsmodels {ref:.6f}"
    return f"F = {propio:.4f} en los dos"


@chequeo("B", "Bai-Perron propio = búsqueda por fuerza bruta")
def _():
    n, h = 60, 9
    x = rng.normal(0, 1, n)
    y = np.where(np.arange(n) < 20, 0, np.where(np.arange(n) < 40, 2, -1)) + 0.5 * x + rng.normal(0, 0.4, n)
    X = sm.add_constant(x)
    bp = {z["m"]: z for z in EP.bai_perron(y, X, h, 2)}
    ssr = lambda a, b: EP.mco(y[a:b], X[a:b])[2]
    mejor1 = min((ssr(0, i) + ssr(i, n), i) for i in range(h, n - h + 1))
    mejor2 = min((ssr(0, i) + ssr(i, j) + ssr(j, n), (i, j)) for i in range(h, n - 2 * h + 1) for j in range(i + h, n - h + 1))
    assert abs(bp[1]["ssr"] - mejor1[0]) < 1e-8 and bp[1]["quiebres"] == [mejor1[1]], f"1 quiebre: {bp[1]['quiebres']} vs {mejor1[1]}"
    assert abs(bp[2]["ssr"] - mejor2[0]) < 1e-8 and tuple(bp[2]["quiebres"]) == mejor2[1], f"2 quiebres: {bp[2]['quiebres']} vs {mejor2[1]}"
    return f"quiebres verdaderos 20 y 40; los dos métodos dan {list(mejor2[1])}"


@chequeo("B", "Pronóstico por MCO propio = statsmodels")
def _():
    X = rng.normal(0, 1, (80, 4))
    y = X @ [1, -2, 0.5, 3] + rng.normal(0, 1, 80)
    x0 = rng.normal(0, 1, 4)
    propio = BR.ols_pred(X, y, x0)
    ref = sm.OLS(y, sm.add_constant(X)).fit().predict(np.r_[1, x0].reshape(1, -1))[0]
    assert abs(propio - ref) < 1e-9, f"{propio} vs {ref}"
    return f"iguales ({propio:.4f})"


# ======================================================================== C) placebo con datos reales
def datos_lp(version):
    pr = D.precios()
    lp = np.log(D.indice(pr[version]))
    dev = np.log(D.tipo_cambio()).diff() * 100
    emi = pd.read_csv(D.CARPETA / "emision_mensual.csv", index_col=0)
    emi.index = pd.PeriodIndex(emi.index, freq="M")
    return lp, dev, emi


for _version in ("oficial", "sanluis"):
    @chequeo("C", f"Placebo: emisión al Tesoro desordenada al azar (IPC {_version})")
    def _(version=_version):
        lp, dev, emi = datos_lp(version)
        pct = emi["tesoro_pct"] / 1.0
        real = ET.proyecciones_locales(lp, dev, pct).set_index("h")
        validos = pct.dropna()
        b12, sig = [], 0
        for _ in range(200):
            falso = pd.Series(rng.permutation(validos.values), index=validos.index).reindex(pct.index)
            r = ET.proyecciones_locales(lp, dev, falso).set_index("h")
            b12.append(r.loc[12, "b"])
            sig += r.loc[12, "inf90"] > 0
        b12 = np.array(b12)
        p_emp = (1 + (b12 >= real.loc[12, "b"]).sum()) / 201
        falsos = sig / 2
        detalle = (f"efecto real a 12 meses {real.loc[12, 'b']:.2f}; placebo: mediana {np.median(b12):.2f}, percentil 95 "
                   f"{np.percentile(b12, 95):.2f}; p empírico {p_emp:.3f}; placebos 'significativos' {falsos:.0f}% (ideal ~5%)")
        assert falsos <= 12, "el método encuentra efectos donde no hay: " + detalle
        if p_emp > 0.05:
            return "AVISO", "el efecto real no se distingue del azar: " + detalle
        return "OK", detalle


# ======================================================================== D) diagnóstico
def datos_vecm_real(version="sanluis"):
    pr = D.precios()
    din = D.dinero()
    act, _ = D.actividad()
    lp = D.desestacionalizar_log(D.indice(pr[version]))
    lm = D.desestacionalizar_log(din["base"].dropna())
    ly = D.desestacionalizar_log(act["emae"].dropna())
    le = np.log(D.tipo_cambio())
    d = pd.DataFrame({"p": lp, "m": lm, "y": ly, "e": le})
    d = d[d.index >= pd.Period("2004-01", "M")].dropna()
    return pd.DataFrame({"mr": d["m"] - d["p"], "y": d["y"], "q": d["e"] - d["p"], "pi": d["p"].diff() * 100}).dropna()


@chequeo("D", "VECM real: semielasticidad estable al cambiar los rezagos (1 a 4)")
def _():
    from statsmodels.tsa.vector_ar.vecm import VECM
    d = datos_vecm_real()
    s = []
    for k in (1, 2, 3, 4):
        res = VECM(d, k_ar_diff=k, coint_rank=1, deterministic="ci").fit()
        b = res.beta[:, 0] / res.beta[0, 0]
        s.append(-b[3])
    assert all(v < 0 for v in s), f"cambia de signo: {np.round(s, 3)}"
    rango = max(s) - min(s)
    detalle = "s con 1-4 rezagos: " + ", ".join(f"{v:.3f}" for v in s)
    return ("AVISO", detalle + " (varía bastante)") if rango > 0.15 else ("OK", detalle)


@chequeo("D", "VECM real: estabilidad (raíces dentro del círculo unitario) y residuos")
def _():
    from statsmodels.tsa.vector_ar.vecm import VECM
    d = datos_vecm_real()
    res = VECM(d, k_ar_diff=1, coint_rank=1, deterministic="ci").fit()
    A = res.var_rep
    K, p = A.shape[1], A.shape[0]
    comp = np.zeros((K * p, K * p))
    comp[:K, :] = np.hstack(list(A))
    if p > 1:
        comp[K:, :-K] = np.eye(K * (p - 1))
    mod = np.sort(np.abs(np.linalg.eigvals(comp)))[::-1]
    resto = mod[K - 1:]                 # con rango 1 hay K-1 raíces unitarias
    assert resto.max() < 1.0 + 1e-6, f"raíces fuera del círculo: {np.round(mod, 3)}"
    blanco = res.test_whiteness(nlags=12).pvalue
    normal = res.test_normality().pvalue
    detalle = (f"mayor raíz no unitaria {resto.max():.3f}; residuos: autocorrelación p={blanco:.3f}, normalidad p={normal:.3f}")
    if blanco < 0.05 or normal < 0.05:
        return "AVISO", detalle + " (residuos con autocorrelación y/o no normales: usar errores robustos y leer con cuidado)"
    return "OK", detalle


@chequeo("D", "Comparaciones múltiples: rezagos de la oferta monetaria (Holm y Benjamini-Hochberg)")
def _():
    t = pd.read_csv(D.CARPETA / "oferta_inflacion_rezagos.csv")
    t["p"] = 2 * (1 - stats.norm.cdf(t["t_con_inercia"].abs()))
    antes = int(((t["p"] < 0.05) & (t["t_con_inercia"] > 0)).sum())
    holm = 0
    for _, g in t.groupby(["inflacion", "agregado", "crecimiento", "etapa"]):
        p = np.sort(g["p"].values)
        m = len(p)
        rechazos = 0
        for i, pv in enumerate(p):
            if pv <= 0.05 / (m - i):
                rechazos += 1
            else:
                break
        idx = g["p"].sort_values().index[:rechazos]
        holm += int((t.loc[idx, "t_con_inercia"] > 0).sum())
    p = t["p"].sort_values()
    m = len(p)
    umbral = [pv for i, pv in enumerate(p.values, 1) if pv <= 0.05 * i / m]
    bh_idx = p.index[:len(umbral)] if umbral else []
    bh = int((t.loc[bh_idx, "t_con_inercia"] > 0).sum()) if len(bh_idx) else 0
    detalle = (f"{len(t)} pruebas; positivas significativas sin corregir {antes}; con Holm (por familia de 12 rezagos) {holm}; "
               f"con Benjamini-Hochberg (todas) {bh}")
    if holm == 0 and bh == 0:
        return "AVISO", detalle + ": ningún rezago sobrevive a la corrección"
    return "OK", detalle


def main():
    print(f"\n=== VERIFICACIÓN DE MÉTODOS DE ANÁLISIS ===\n")
    nombres = {"A": "A) Simulación con respuesta conocida", "B": "B) Cruce con otras implementaciones",
               "C": "C) Placebo con datos reales", "D": "D) Diagnóstico"}
    lineas = []
    for g, titulo in nombres.items():
        lineas.append(titulo)
        for grupo, estado, nombre, detalle in resultados:
            if grupo == g:
                marca = {"OK": "✔ OK   ", "AVISO": "! AVISO", "FALLA": "✘ FALLA"}[estado]
                lineas.append(f"  {marca}  {nombre}\n             {detalle}")
        lineas.append("")
    n = {k: sum(r[1] == k for r in resultados) for k in ("OK", "AVISO", "FALLA")}
    lineas.append(f"Resultado: {n['OK']} OK, {n['AVISO']} avisos, {n['FALLA']} fallas")
    texto = "\n".join(lineas)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")
    sys.exit(1 if n["FALLA"] else 0)


if __name__ == "__main__":
    main()
