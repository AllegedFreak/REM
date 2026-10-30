# probar_pib_horizonte.py (29-oct-2026; análisis, fuera del circuito diario)
# ¿Cómo pronosticar el PIB en los trimestres SIN ningún mes de EMAE publicado? Hoy (replicar_pib.py) el EMAE de los meses
# que faltan se completa con el último nivel de la tendencia-ciclo y queda QUIETO: I-2027 da 0,0 siempre (no es un
# pronóstico, es la falta de uno) y el PIB anual de 2027-28 da ~0%.
# Alternativas (en tiempo real: para el relevamiento de fin del mes t, EMAE hasta t-2 y PIB publicado hasta la versión t):
#   plano     : lo de hoy (nivel quieto desde el último dato de la tendencia-ciclo)
#   tendencia : el nivel crece al ritmo de los últimos 5 años de EMAE (promedio de 12 meses contra el de 5 años antes)
#   vuelta    : tendencia lineal (en log) de los últimos 60 meses + la brecha de hoy, que se cierra al ritmo AR(1)
#               estimado sobre la brecha de esos 60 meses
#   REM corr  : REM del mes anterior para ese trimestre + error medio del REM anterior en ese horizonte (solo errores ya
#               conocidos en t, mínimo 8)
#   combinado : promedio de vuelta y REM corr
# Solo se cambian los trimestres sin meses de EMAE; los otros siguen con I-PIB (que ya le gana al REM).
# Real = primer dato publicado por el INDEC. Diebold-Mariano (HLN) contra el REM del mes.
# ADVERTENCIA: el EMAE del pasado es la versión actual (pudo revisarse).
# Informe: datos\informe_pib_horizonte.txt
import re
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
CORTE = pd.Period("2024-12", "M")
METODOS = ["plano", "tendencia", "vuelta", "REM corr", "combinado"]


def cargar():
    V = pd.read_parquet(C / "pib_desest_versiones.parquet")
    V["Q"] = [pd.Period(f"{int(a)}Q{int(t)}", "Q") for a, t in zip(V["anio"], V["trim"])]
    V["version"] = pd.PeriodIndex(V["version"], freq="M")
    V = V.sort_values(["version", "Q"])
    V["g"] = V.groupby("version")["pib"].pct_change() * 100
    real = V.dropna(subset=["g"]).sort_values("version").groupby("Q").first()["g"]
    pub_en = V.dropna(subset=["g"]).sort_values("version").groupby("Q")["version"].first()   # mes en que se publicó
    E = pd.read_parquet(C / "emae.parquet")
    E.index = pd.PeriodIndex(E["mes"], freq="M")
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[(r["variable_std"] == "pib") & (r["tipo_periodo"] == "trimestre") & (r["unidad"] == "var. % trim. s.e.")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")

    def qq(s):
        m = re.search(r"([IV]+)-(\d\d)", str(s))
        return pd.Period(f"20{m.group(2)}Q{ {'I': 1, 'II': 2, 'III': 3, 'IV': 4}[m.group(1)] }", "Q") if m else None
    g["Q"] = g["periodo_original"].map(qq)
    g = g.dropna(subset=["Q"]).groupby(["origen", "Q"])["mediana"].mean().rename("rem").reset_index()
    return V, real, pub_en, E["desest"], E["tendencia"], g


def camino(desest, tendencia, t, metodo):
    """EMAE mensual: datos hasta t-2 y, después, el camino del método (36 meses)."""
    u = t - 2
    d = desest[desest.index <= u].dropna()
    tc = tendencia[tendencia.index <= u].dropna()
    base = tc.iloc[-1] * (d.iloc[-12:].mean() / tc.iloc[-12:].mean())
    futuros = [u + j for j in range(1, 37)]
    if metodo == "plano":
        fut = [base] * 36
    elif metodo == "tendencia":
        gm = (d.iloc[-12:].mean() / d.iloc[-72:-60].mean()) ** (1 / 60)
        fut = [base * gm ** j for j in range(1, 37)]
    else:   # vuelta
        y = np.log(d.iloc[-60:].values)
        x = np.arange(60)
        b, a = np.polyfit(x, y, 1)
        brecha = y - (a + b * x)
        rho = float(np.clip(np.polyfit(brecha[:-1], brecha[1:], 1)[0], 0, 0.99))
        b0 = np.log(base) - (a + b * 59)
        fut = [np.exp(a + b * (59 + j) + b0 * rho ** j) for j in range(1, 37)]
    return pd.concat([d, pd.Series(fut, index=pd.PeriodIndex(futuros, freq="M"))])


def camino_coherente(desest, tendencia, t, inicio, crece):
    """Un SOLO camino mensual para todos los trimestres (29-oct-2026: replicar_pib.py completa el trimestre anterior con el
    promedio de 3 meses y el en curso con la tendencia-ciclo, y así septiembre tiene dos valores distintos).
    inicio: 'ult' (último dato), 'p3' (promedio de los últimos 3 meses) o 'tc' (tendencia-ciclo escalada);
    crece: 'cero' o 'tend' (ritmo de los últimos 5 años)."""
    u = t - 2
    d = desest[desest.index <= u].dropna()
    tc = tendencia[tendencia.index <= u].dropna()
    base = {"ult": d.iloc[-1], "p3": d.iloc[-3:].mean(), "tc": tc.iloc[-1] * (d.iloc[-12:].mean() / tc.iloc[-12:].mean())}[inicio]
    gm = (d.iloc[-12:].mean() / d.iloc[-72:-60].mean()) ** (1 / 60) if crece == "tend" else 1.0
    fut = pd.Series([base * gm ** j for j in range(1, 37)], index=pd.PeriodIndex([u + j for j in range(1, 37)], freq="M"))
    return pd.concat([d, fut])


def var_q(serie, Q):
    prom = lambda q: np.mean([serie.get(q.asfreq("M", "s") + i, np.nan) for i in range(3)])
    return (prom(Q) / prom(Q - 1) - 1) * 100


def main():
    V, real, pub_en, desest, tendencia, g = cargar()
    R = pd.read_csv(C / "replica_pib.csv", encoding="utf-8-sig")
    R["origen"] = pd.PeriodIndex(R["origen"], freq="M")
    R["Q"] = pd.PeriodIndex(R["Q"], freq="Q")
    R["meses"] = [sum((Q.asfreq("M", "s") + i) <= t - 2 for i in range(3)) for t, Q in zip(R["origen"], R["Q"])]
    R["rem_ant"] = [g[(g["origen"] == t - 1) & (g["Q"] == Q)]["rem"].mean() for t, Q in zip(R["origen"], R["Q"])]
    R = R.sort_values("origen").reset_index(drop=True)
    cam = {}
    for t in R["origen"].unique():
        for m in ("plano", "tendencia", "vuelta"):
            cam[(t, m)] = camino(desest, tendencia, t, m)
    for m in ("plano", "tendencia", "vuelta"):
        R[m] = [var_q(cam[(t, m)], Q) if n == 0 else i for t, Q, n, i in zip(R["origen"], R["Q"], R["meses"], R["I_pib"])]
    # REM anterior corregido: error medio (real - REM anterior) por horizonte k, solo trimestres ya publicados en t
    R["err_ant"] = R["real"] - R["rem_ant"]
    corr = []
    for _, x in R.iterrows():
        pasado = R[(R["k"] == x["k"]) & (R["origen"] < x["origen"])]
        ya = np.array([pub_en.get(q, pd.Period("2100-01", "M")) <= x["origen"] for q in pasado["Q"]], dtype=bool)
        pasado = pasado.loc[ya, "err_ant"].dropna()
        corr.append(x["rem_ant"] + pasado.mean() if len(pasado) >= 8 else np.nan)
    R["REM corr"] = np.where(R["meses"] == 0, corr, R["I_pib"])
    R["combinado"] = np.where(R["meses"] == 0, (R["vuelta"] + R["REM corr"]) / 2, R["I_pib"])

    L = [f"=== PIB: TRIMESTRES SIN EMAE | {datetime.now():%Y-%m-%d %H:%M} ===",
         "Error absoluto medio contra el primer dato (pp de var. trimestral desestacionalizada). Solo trimestres con 0 meses de EMAE.",
         "p = Diebold-Mariano (HLN) del método contra el REM del mes (p < 0,10: diferencia significativa).", ""]
    Z = R[(R["meses"] == 0)].dropna(subset=["real", "rem", "REM corr"])
    for per, mk in (("2016-2024", Z["origen"] <= CORTE), ("2025-26", Z["origen"] > CORTE), ("todo", Z["origen"] > CORTE - 1000)):
        L += [f"== {per}", f"   {'':16}{'n':>4}{'REM mes':>9}{'REM ant':>9}" + "".join(f"{m:>11}" for m in METODOS)
              + "   p:" + "".join(f"{m[:6]:>8}" for m in METODOS)]
        for k, z in Z[mk].groupby("k"):
            e = lambda c: (z[c] - z["real"]).abs().mean()
            ps = [diebold_mariano((z[m] - z["real"]).values, (z["rem"] - z["real"]).values, k + 2) for m in METODOS]
            L.append(f"   {'trim. k=' + str(k):16}{len(z):4d}{e('rem'):9.2f}{e('rem_ant'):9.2f}" + "".join(f"{e(m):11.2f}" for m in METODOS)
                     + "     " + "".join(f"{p:8.2f}" for p in ps))
        L.append("")
    # sesgo: ¿los métodos dan crecimiento razonable en promedio?
    L.append("Promedio del pronóstico vs real (todo, trimestres sin EMAE): real "
             f"{Z['real'].mean():+.2f} | REM {Z['rem'].mean():+.2f} | " + " | ".join(f"{m} {Z[m].mean():+.2f}" for m in METODOS))
    # ---- camino coherente: un solo relleno para todos los trimestres (todos los k, con y sin meses de EMAE) ----
    VAR = [(i, c) for i in ("ult", "p3", "tc") for c in ("cero", "tend")]
    cc = {(t, i, c): camino_coherente(desest, tendencia, t, i, c) for t in R["origen"].unique() for i, c in VAR}
    for i, c in VAR:
        R[f"{i}-{c}"] = [var_q(cc[(t, i, c)], Q) for t, Q in zip(R["origen"], R["Q"])]
        R[f"{i}-{c}+mez"] = np.where((R["meses"] == 0) & R["rem_ant"].notna(), (R[f"{i}-{c}"] + R["rem_ant"]) / 2, R[f"{i}-{c}"])
    cols = ["I_pib"] + [f"{i}-{c}" for i, c in VAR] + [f"{i}-{c}+mez" for i, c in VAR]
    L += ["", "=== CAMINO COHERENTE (un solo relleno para septiembre en todos los trimestres) ===",
          "I_pib = lo de hoy. inicio: ult / p3 / tc; crecimiento: cero / tend; +mez = trimestres sin EMAE promediados con el REM anterior."]
    Z2 = R.dropna(subset=["real", "rem"] + cols)
    for per, mk in (("2016-2024", Z2["origen"] <= CORTE), ("2025-26", Z2["origen"] > CORTE)):
        L += [f"== {per}", f"   {'modelo':16}" + "".join(f"{'k=' + str(k):>8}" for k in sorted(Z2["k"].unique())) + "   p DM vs REM por k"]
        z0 = Z2[mk]
        L.append(f"   {'REM del mes':16}" + "".join(f"{(z['rem'] - z['real']).abs().mean():8.2f}" for _, z in z0.groupby("k")))
        for c in cols:
            L.append(f"   {c:16}" + "".join(f"{(z[c] - z['real']).abs().mean():8.2f}" for _, z in z0.groupby("k")) + "   "
                     + "".join(f"{diebold_mariano((z[c] - z['real']).values, (z['rem'] - z['real']).values, max(k + 2, 1)):6.2f}"
                               for k, z in z0.groupby("k")))
        L.append("   n: " + " ".join(f"k={k}:{len(z)}" for k, z in z0.groupby("k")))
        L.append("")
    # hoy
    t = pd.Period(pd.Timestamp.today(), "M")
    L.append(f"HOY con camino coherente (relevamiento {t}):")
    pub = V[V["version"] <= t]["Q"].max()
    for i, c in VAR:
        s = camino_coherente(desest, tendencia, t, i, c)
        vals = []
        for Q in (pub + 1, pub + 2, pub + 3):
            v = var_q(s, Q)
            ra = g[(g["origen"] == t - 1) & (g["Q"] == Q)]["rem"].mean()
            n = sum((Q.asfreq("M", "s") + j) <= t - 2 for j in range(3))
            vals.append(f"{str(Q)} {v:6.2f}" + (f" (mez {(v + ra) / 2:5.2f})" if n == 0 else " " * 12))
        L.append(f"   {i + '-' + c:10}" + "   ".join(vals))
    L.append("")
    # hoy
    t = pd.Period(pd.Timestamp.today(), "M")
    pub = V[V["version"] <= t]["Q"].max()
    L += ["", f"HOY (relevamiento {t}; último PIB publicado {pub}; EMAE hasta {t - 2}):",
          f"   {'trimestre':10}{'meses EMAE':>11}{'REM ant':>9}" + "".join(f"{m:>11}" for m in ["plano", "tendencia", "vuelta"])]
    for Q in (pub + 1, pub + 2, pub + 3):
        n = sum((Q.asfreq("M", "s") + i) <= t - 2 for i in range(3))
        ra = g[(g["origen"] == t - 1) & (g["Q"] == Q)]["rem"].mean()
        vals = [var_q(camino(desest, tendencia, t, m), Q) for m in ("plano", "tendencia", "vuelta")]
        L.append(f"   {str(Q):10}{n:11d}{ra:9.2f}" + "".join(f"{v:11.2f}" for v in vals))
    texto = "\n".join(L)
    print(texto)
    (C / "informe_pib_horizonte.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
