# emision_tesoro.py
# ¿Cuánto y cuándo suben los precios después de que el BCRA le da pesos al Tesoro?
#   Emisión al Tesoro = efecto monetario de adelantos transitorios (id 49), transferencia de utilidades (50) y resto de
#   operaciones con el Tesoro (51). Esterilización = pases (52), LELIQ/NOTALIQ (53), redescuentos y adelantos (54),
#   intereses de esos instrumentos (55), LEBAC/NOBAC (56) y letras fiscales de liquidez (58).
#   Emisión neta = Tesoro + esterilización (los efectos ya vienen con signo), en % de la base promedio del mes anterior.
# Proyecciones locales (Jordà): para h = 0..24,
#   100·[log P(t+h) - log P(t-1)] = a + b_h·emisión(t) + rezagos 1-6 de inflación, devaluación y emisión
#   con las 3 versiones del IPC. b_h = % acumulado que suben los precios por cada 1% de la base emitido en el mes t.
# Salida: datos/informe_emision.txt, datos/emision_mensual.csv, datos/emision_lp.csv
# Uso: py -3.13 emision_tesoro.py
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm

import datos_macro as D

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TESORO = {49: "adelantos transitorios", 50: "utilidades", 51: "resto operaciones Tesoro"}
ESTERILIZACION = {52: "pases", 53: "LELIQ/NOTALIQ", 54: "redescuentos", 55: "intereses de pases/LELIQ/etc.",
                  56: "LEBAC/NOBAC", 58: "letras fiscales de liquidez"}
REZAGOS = 6
HORIZONTES = range(0, 25)
MOSTRAR = (1, 3, 6, 9, 12, 18, 24)
INFORME = D.CARPETA / "informe_emision.txt"


def flujos_mensuales():
    cols = {}
    for vid in list(TESORO) + list(ESTERILIZACION):
        s = D.bcra(vid, "2003-01-01")
        cols[vid] = s.groupby(s.index.to_period("M")).sum()
    # regla de huecos: un mes sin dato en algún componente queda vacío (no se completa con cero). El BCRA informa
    # los días sin movimiento con cero explícito, así que esto solo salta ante un hueco real (verificado: ninguno hasta oct-2026)
    f = pd.DataFrame(cols)
    f["tesoro"] = f[list(TESORO)].sum(axis=1, min_count=len(TESORO))
    f["esterilizacion"] = f[list(ESTERILIZACION)].sum(axis=1, min_count=len(ESTERILIZACION))
    f["neta"] = f["tesoro"] + f["esterilizacion"]
    return f


def proyecciones_locales(lp, dev, x):
    """lp: log precios; dev: devaluación mensual (%); x: shock (% de la base). Devuelve DataFrame por horizonte."""
    pi = lp.diff() * 100
    filas = []
    for h in HORIZONTES:
        y = (lp.shift(-h) - lp.shift(1)) * 100
        X = pd.DataFrame({"shock": x})
        for l in range(1, REZAGOS + 1):
            X[f"pi_{l}"] = pi.shift(l)
            X[f"dev_{l}"] = dev.shift(l)
            X[f"x_{l}"] = x.shift(l)
        d = pd.concat([y.rename("y"), X], axis=1).dropna()
        m = sm.OLS(d["y"], sm.add_constant(d.drop(columns="y"))).fit(cov_type="HAC", cov_kwds={"maxlags": h + 1})
        b, se = m.params["shock"], m.bse["shock"]
        filas.append({"h": h, "b": b, "se": se, "inf90": b - 1.645 * se, "sup90": b + 1.645 * se, "n": len(d),
                      "desde": str(d.index.min()), "hasta": str(d.index.max())})
    return pd.DataFrame(filas)


def main():
    f = flujos_mensuales()
    din = D.dinero()
    base_ant = din["base"].shift(1)
    pct = f[["tesoro", "esterilizacion", "neta"]].div(base_ant.reindex(f.index), axis=0) * 100
    pct.columns = ["tesoro_pct", "esterilizacion_pct", "neta_pct"]
    tabla = pd.concat([f, pct], axis=1)
    tabla.to_csv(D.CARPETA / "emision_mensual.csv", encoding="utf-8-sig", float_format="%.4f")

    L = ["=== EMISIÓN AL TESORO Y PRECIOS ===",
         "Tesoro = " + ", ".join(f"{k} {v}" for k, v in TESORO.items()) + ".",
         "Esterilización = " + ", ".join(f"{k} {v}" for k, v in ESTERILIZACION.items()) + ".",
         "Neta = Tesoro + esterilización. En % de la base promedio del mes anterior.", ""]

    # ---- régimen actual ----
    L.append("1) Meses de 2024-2026 (millones de $ y % de la base del mes anterior)")
    L.append(f"   {'mes':8} {'adelantos':>11} {'utilidades':>11} {'resto Tes.':>11} {'Tesoro %':>9} {'esteril. %':>10} {'neta %':>8}")
    rec = tabla[tabla.index >= pd.Period("2024-01", "M")]
    for t, r in rec.iterrows():
        L.append(f"   {str(t):8} {r[49]:11.0f} {r[50]:11.0f} {r[51]:11.0f} {r['tesoro_pct']:9.2f} {r['esterilizacion_pct']:10.2f} {r['neta_pct']:8.2f}")
    umbral = 0.1
    for col, nom in (("tesoro_pct", "Tesoro"), ("neta_pct", "neta")):
        n0 = int((rec[col].abs() > umbral).sum())
        L.append(f"   Meses con emisión {nom} distinta de cero (|x| > {umbral}% de la base): {n0} de {len(rec)}")
    n_ad = int((rec[49].abs() > 0).sum())
    L.append(f"   Meses con adelantos transitorios distintos de cero: {n_ad} de {len(rec)}")
    L.append("")
    L.append("   Resumen por etapa (promedio mensual, % de la base):")
    for a, b in (("2003-01", "2007-12"), ("2008-01", "2015-12"), ("2016-01", "2019-12"), ("2020-01", "2023-12"), ("2024-01", "2026-12")):
        q = tabla[(tabla.index >= pd.Period(a, "M")) & (tabla.index <= pd.Period(b, "M"))]
        L.append(f"     {a} a {b[:4]}: Tesoro {q['tesoro_pct'].mean():6.2f}  esterilización {q['esterilizacion_pct'].mean():6.2f}  "
                 f"neta {q['neta_pct'].mean():6.2f}  ({len(q)} meses)")
    L.append("")

    # ---- proyecciones locales ----
    pr = D.precios()
    tc = D.tipo_cambio()
    dev = np.log(tc).diff() * 100
    L.append("2) Proyecciones locales: % acumulado que suben los precios por cada 1% de la base emitido (intervalo 90%)")
    L.append(f"   Controles: {REZAGOS} rezagos de inflación, devaluación (A3500) y de la propia emisión. Errores HAC.")
    res_todos = []
    for shock, nom_s in (("neta_pct", "emisión neta"), ("tesoro_pct", "emisión al Tesoro bruta")):
        for v in pr.columns:
            lp = np.log(D.indice(pr[v]))
            r = proyecciones_locales(lp, dev, pct[shock])
            r["version"], r["shock"] = v, nom_s
            res_todos.append(r)
            L.append("")
            L.append(f"   {nom_s} | IPC {v} ({r['desde'].iloc[0]} a {r['hasta'].iloc[0]}, n={r['n'].iloc[0]} en h=0)")
            L.append("   " + " ".join(f"{'h=' + str(h):>18}" for h in MOSTRAR))
            L.append("   " + " ".join(f"{r.loc[h, 'b']:6.2f} [{r.loc[h, 'inf90']:5.2f};{r.loc[h, 'sup90']:5.2f}]"[:18].rjust(18) for h in MOSTRAR))
            sig = [h for h in HORIZONTES if r.loc[h, "inf90"] > 0]
            L.append(f"   horizontes con efecto positivo significativo (90%): {sig if sig else 'ninguno'}")
    # robustez: hasta 2023-12 (desde 2024 los flujos con el Tesoro son sobre todo reacomodamiento de deuda, no financiamiento)
    L.append("")
    L.append("   Robustez: muestra hasta 2023-12 (sin el régimen actual). % acumulado por cada 1% de la base:")
    L.append(f"   {'shock':26} {'IPC':10} " + " ".join(f"{'h=' + str(h):>18}" for h in (6, 12, 18, 24)) + "  signif. desde")
    fin = pd.Period("2023-12", "M")
    for shock, nom_s in (("neta_pct", "emisión neta"), ("tesoro_pct", "emisión al Tesoro bruta")):
        for v in pr.columns:
            lp = np.log(D.indice(pr[v]))
            x = pct[shock].copy()
            x[x.index > fin] = np.nan          # el shock se corta en 2023-12; los precios posteriores sí se usan como resultado
            r = proyecciones_locales(lp, dev, x)
            r["version"], r["shock"] = v, nom_s + " hasta 2023"
            res_todos.append(r)
            sig = [h for h in HORIZONTES if r.loc[h, "inf90"] > 0]
            L.append(f"   {nom_s:26} {v:10} " + " ".join(
                f"{r.loc[h, 'b']:6.2f} [{r.loc[h, 'inf90']:5.2f};{r.loc[h, 'sup90']:5.2f}]"[:18].rjust(18) for h in (6, 12, 18, 24))
                + f"  {sig[0] if sig else '-'}")
    pd.concat(res_todos).to_csv(D.CARPETA / "emision_lp.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    # tamaño típico del shock
    q = pct["neta_pct"].dropna()
    L.append("")
    L.append(f"   Tamaño típico de la emisión neta: desvío estándar {q.std():.2f}% de la base; percentil 90: {q.quantile(0.9):.2f}%")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
