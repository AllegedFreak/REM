# replicar_pib.py
# Nuestro pronóstico del PIB trimestral desestacionalizado (var. % contra el trimestre anterior), como lo pide el REM.
# El REM pregunta 3 trimestres: en mar/jun/sep/dic (el INDEC ya publicó el trimestre anterior) el trimestre en curso y
# los 2 siguientes; en los demás meses, el trimestre anterior (aún no publicado), el en curso y el siguiente.
# "Real" = PRIMER dato publicado de cada trimestre (versión del INDEC en que aparece por primera vez; datos\pib_desest_versiones).
# Información en tiempo real para el relevamiento de fin del mes t:
#   - último PIB publicado (versión con mes <= t) y su variación del último trimestre;
#   - EMAE desestacionalizado hasta t-2 (sale ~día 20 del mes t-?; ADVERTENCIA: se usa la versión actual del EMAE, que pudo
#     revisarse; las versiones propias se guardan desde el 17-oct-2026);
#   - emae_nc(Q) = promedio del EMAE de los meses conocidos del trimestre Q (los que faltan, con el último dato) contra el
#     promedio del trimestre anterior (también completado así) = "lo que daría el trimestre si la actividad se queda".
# Modelos (estimación robusta Huber, ventana acumulada, mínimo 20 relevamientos, por posición del trimestre k=-1..2):
#   EMAE solo : emae_nc del trimestre.
#   A-PIB     : REM ~ emae_nc + meses conocidos + PIB último + EMAE último mes. Objetivo: parecerse al REM.
#   I-PIB     : primer dato ~ mismas variables (solo trimestres ya publicados a la fecha). Objetivo: ganarle al REM.
# Salidas: datos\replica_pib.csv, datos\informe_pib.txt, datos\registro_pib.csv
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from backtest_rem import diebold_mariano

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
MIN = 20
COLS = ["nc_ult", "nc_p3", "nc_tc", "meses_conocidos", "pib_ult", "emae_mm"]   # para A-PIB (imitar al REM)
# I-PIB (nuestro pronóstico del PIB; 30-oct-2026, "camino coherente", decisión de Marcos) = EMAE con UN SOLO camino para
# los meses que faltan, igual en todos los trimestres: arranca en el PROMEDIO DE LOS ÚLTIMOS 3 MESES y crece al ritmo de
# los últimos 5 años de EMAE; en los trimestres sin ningún mes de EMAE, promedio con el REM anterior. Antes (17-oct)
# completaba con p3 el trimestre anterior y con la tendencia-ciclo los demás: septiembre-2026 tenía dos valores y el
# IV-2026 rebotaba de forma mecánica; I-2027 daba 0. Prueba en tiempo real (probar_pib_horizonte.py), error contra el
# primer dato, 2025-26: anterior 0,26 / en curso 0,39 / siguiente 0,47 pp (REM del mes 0,42 / 0,56 / 0,61; p DM 0,04 /
# 0,03 / 0,03). En 2016-24 el REM sigue siendo mejor (1,01 / 1,91 / 2,30 contra 0,56 / 1,47 / 1,95).


CORRIDA = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")   # UNA sola hora por corrida (28-oct-2026: con la hora fila por fila,
# exportaciones e importaciones quedaron en 12:12 y 12:13 y el sello n.º 1 salió sin exportaciones)


def huber(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(Xc, y, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def ritmo_mensual(desest, t):
    """Crecimiento mensual de tendencia con el EMAE publicado al fin de t: promedio de 12 meses contra el de 5 años antes."""
    dd = desest[desest.index <= t - 2].dropna()
    return (dd.iloc[-12:].mean() / dd.iloc[-72:-60].mean()) ** (1 / 60) if len(dd) >= 72 else 1.0


def camino_anual(V, R, t, g_lp):
    """Niveles trimestrales en el relevamiento t: publicados (última versión <= t), después I-PIB de ese relevamiento y,
    más allá, crecimiento g_lp por trimestre. Devuelve {año: var. % prom. anual} para t.year .. t.year+2."""
    vers = V[V["version"] <= t]
    if vers.empty:
        return {}
    uv = vers[vers["version"] == vers["version"].max()].set_index("Q")["pib"].dropna()
    nivel = uv.to_dict()
    q, ult = uv.index.max(), uv.iloc[-1]
    prox = R[R["origen"] == t].set_index("Q")["I_pib"].dropna()
    while q < pd.Period(f"{t.year + 2}Q4", "Q"):
        q += 1
        ult *= 1 + (prox.get(q, g_lp)) / 100
        nivel[q] = ult
    out = {}
    for y in (t.year, t.year + 1, t.year + 2):
        a = [nivel.get(pd.Period(f"{y}Q{i}", "Q")) for i in range(1, 5)]
        b = [nivel.get(pd.Period(f"{y - 1}Q{i}", "Q")) for i in range(1, 5)]
        if None not in a and None not in b:
            out[y] = (np.mean(a) / np.mean(b) - 1) * 100
    return out


def pib_anual(V, R, r, real_q, t_hoy, desest):
    """PIB anual (var. % del promedio anual, desestacionalizado) armado desde los trimestres; contra el REM y el primer dato."""
    an = r[(r["variable_std"] == "pib") & (r["tipo_periodo"] == "anio")].copy()
    an["origen"] = pd.to_datetime(an["mes_relevamiento"]).dt.to_period("M")
    an["anio"] = pd.to_numeric(an["periodo_original"], errors="coerce")
    an = an.dropna(subset=["anio"]).groupby(["origen", "anio"]).agg(rem=("mediana", "mean"), p10=("percentil_10", "mean"),
                                                                    p90=("percentil_90", "mean"), p25=("percentil_25", "mean"), pmax=("maximo", "mean")).reset_index()
    # primer dato anual: la primera versión que trae el 4° trimestre del año
    real = {}
    for y in range(2015, t_hoy.year + 1):
        vs = V[V["Q"] == pd.Period(f"{y}Q4", "Q")]["version"]
        if len(vs):
            w = V[V["version"] == vs.min()].set_index("Q")["pib"]
            a = [w.get(pd.Period(f"{y}Q{i}", "Q")) for i in range(1, 5)]
            b = [w.get(pd.Period(f"{y - 1}Q{i}", "Q")) for i in range(1, 5)]
            if None not in a and None not in b and not np.isnan(a + b).any():
                real[y] = (np.mean(a) / np.mean(b) - 1) * 100
    filas = []
    for t in sorted(an["origen"].unique()):
        hist = real_q[real_q.index <= V[V["version"] <= t]["Q"].max()] if (V["version"] <= t).any() else real_q.iloc[:0]
        variantes = {"cero": 0.0, "prom20": hist.iloc[-20:].mean() if len(hist) >= 8 else 0.0,
                     "tend": (ritmo_mensual(desest, t) ** 3 - 1) * 100}   # el mismo ritmo que el camino de I-PIB
        caminos = {k: camino_anual(V, R, t, g) for k, g in variantes.items()}
        for _, x in an[an["origen"] == t].iterrows():
            y = int(x["anio"])
            f = {"origen": t, "anio": y, "pos": y - t.year, "rem": x["rem"], "p10": x["p10"], "p90": x["p90"], "p25": x["p25"], "pmax": x["pmax"],
                 "real": real.get(y, np.nan) if y < t_hoy.year or (V["Q"] == pd.Period(f"{y}Q4", "Q")).any() else np.nan}
            for k, c in caminos.items():
                f[f"I_{k}"] = c.get(y, np.nan)
            filas.append(f)
    A = pd.DataFrame(filas).dropna(subset=["I_cero", "I_prom20"])
    # A-PIB anual: REM ~ camino con cero (arrastre + trimestres I) y camino con prom20; Huber, ventana acumulada por posición
    COLS_A = ["I_cero", "I_prom20"]
    A["A_anual"] = np.nan
    for pos, q in A.groupby("pos"):
        for i, row in q.iterrows():
            tr = q[q["origen"] < row["origen"]].dropna(subset=COLS_A + ["rem"])
            if len(tr) >= MIN:
                A.loc[i, "A_anual"] = huber(tr[COLS_A].values.astype(float), tr["rem"].values, row[COLS_A].values.astype(float))
    A.to_csv(C / "replica_pib_anual.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    etq = {0: "año en curso", 1: "año siguiente", 2: "año +2"}
    L = ["=== PIB ANUAL (var. % del promedio anual) ===",
         "I cero = trimestres publicados + I-PIB de los próximos trimestres + 0% después; I prom20 = después, el promedio de",
         "los últimos 20 trimestres publicados; I tend = después, el ritmo de 5 años del EMAE (el del camino de I-PIB);",
         "A = regresión sobre I cero e I p20 que imita al REM. Real = primer dato del año. p DM = I cero / I tend contra el REM.", ""]
    for per, m in (("2016-2024", A["origen"] <= pd.Period("2024-12", "M")), ("2025-26", A["origen"] > pd.Period("2024-12", "M"))):
        q = A[m]
        L.append(f"== {per}")
        L.append(f"   {'':14}{'distancia al REM':>40} | {'error contra el primer dato':>48}")
        L.append(f"   {'':14}{'I cero':>8}{'I p20':>8}{'I tend':>8}{'A':>8}{'n':>6} | {'I cero':>8}{'I p20':>8}{'I tend':>8}{'A':>8}"
                 f"{'REM':>8}{'n':>6}{'p DM':>12}")
        for pos in sorted(q["pos"].unique()):
            z = q[q["pos"] == pos].dropna(subset=["A_anual"])
            zr = z.dropna(subset=["real"])
            if not len(z):
                continue
            d = lambda c, ref, zz: (zz[c] - zz[ref]).abs().mean()
            ps = []
            for c in ("I_cero", "I_tend"):
                try:
                    ps.append(diebold_mariano((zr[c] - zr.real).values, (zr.rem - zr.real).values, 12) if len(zr) > 6 else np.nan)
                except Exception:
                    ps.append(np.nan)
            L.append(f"   {etq.get(pos, pos):14}{d('I_cero', 'rem', z):8.2f}{d('I_prom20', 'rem', z):8.2f}{d('I_tend', 'rem', z):8.2f}"
                     f"{d('A_anual', 'rem', z):8.2f}{len(z):6d} | "
                     f"{d('I_cero', 'real', zr):8.2f}{d('I_prom20', 'real', zr):8.2f}{d('I_tend', 'real', zr):8.2f}"
                     f"{d('A_anual', 'real', zr):8.2f}{d('rem', 'real', zr):8.2f}{len(zr):6d}{ps[0]:6.2f}/{ps[1]:.2f}")
        L.append("")
    # hoy
    hist = real_q[real_q.index <= V[V["version"] <= t_hoy]["Q"].max()]
    gt = (ritmo_mensual(desest, t_hoy) ** 3 - 1) * 100
    c0, c20, ct = (camino_anual(V, R, t_hoy, g) for g in (0.0, hist.iloc[-20:].mean(), gt))
    L.append(f"PRONÓSTICO DE HOY (después de los trimestres de I-PIB: 0%, {hist.iloc[-20:].mean():+.2f}% o {gt:+.2f}% por trimestre):")
    L.append(f"   {'año':6}{'REM anterior':>13}{'I cero':>8}{'I p20':>8}{'I tend':>8}")
    for y in sorted(c0):
        ra = an[(an["origen"] == an["origen"].max()) & (an["anio"] == y)]["rem"].mean()
        L.append(f"   {y:<6}{ra:13.2f}{c0[y]:8.2f}{c20.get(y, np.nan):8.2f}{ct.get(y, np.nan):8.2f}")
    # nuestro PIB anual = I tend (decisión de Marcos, 30-oct-2026: coherente con el camino trimestral; la prueba no distingue
    # entre las tres variantes). Va al sitio solo como respuesta al REM; todavía NO se sella.
    reg = [{"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "anio": y, "I": ct[y],
            "rem_anterior": an[(an["origen"] == an["origen"].max()) & (an["anio"] == y)]["rem"].mean()} for y in sorted(ct)]
    if reg:
        p = C / "registro_pib_anual.csv"
        pd.DataFrame(reg).to_csv(p, mode="a", header=not p.exists(), index=False, encoding="utf-8-sig", float_format="%.3f")
    return L


def main():
    V = pd.read_parquet(C / "pib_desest_versiones.parquet")
    V["Q"] = [pd.Period(f"{int(a)}Q{int(t)}", "Q") for a, t in zip(V["anio"], V["trim"])]
    V["version"] = pd.PeriodIndex(V["version"], freq="M")
    V = V.sort_values(["version", "Q"])
    V["g"] = V.groupby("version")["pib"].pct_change() * 100
    primero = V.dropna(subset=["g"]).sort_values("version").groupby("Q").first()          # primer dato de cada trimestre
    real = primero["g"]
    E = pd.read_parquet(C / "emae.parquet")
    E.index = pd.PeriodIndex(E["mes"], freq="M")
    desest = E["desest"]
    tendencia = E["tendencia"]
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[(r["variable_std"] == "pib") & (r["tipo_periodo"] == "trimestre") & (r["unidad"] == "var. % trim. s.e.")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")

    def qq(s):
        m = re.search(r"([IV]+)-(\d\d)", str(s))
        return pd.Period(f"20{m.group(2)}Q{ {'I': 1, 'II': 2, 'III': 3, 'IV': 4}[m.group(1)] }", "Q") if m else None
    g["Q"] = g["periodo_original"].map(qq)
    g = g.dropna(subset=["Q"])
    g["k"] = [(b - a.asfreq("Q")).n for a, b in zip(g["origen"], g["Q"])]
    g = g[g["k"].between(-1, 2)].groupby(["origen", "Q", "k"]).agg(rem=("mediana", "mean"), p10=("percentil_10", "mean"),
                                                                    p90=("percentil_90", "mean"), p25=("percentil_25", "mean"), pmax=("maximo", "mean")).reset_index()

    def rasgos(t, Q):
        # último EMAE publicado al fin del mes t; si todavía no salió (17-nov-2026: a mitad de noviembre el de septiembre no
        # estaba y el III y IV trimestre quedaban vacíos), el último publicado. En la prueba hacia atrás siempre es t-2
        ult_m = min(t - 2, desest.dropna().index.max())
        d = desest[desest.index <= ult_m]
        if len(d) < 6:
            return None
        tc_ = tendencia[tendencia.index <= ult_m]
        rellenos = {"ult": d.iloc[-1], "p3": d.iloc[-3:].mean(),
                    "tc": tc_.iloc[-1] * (d.iloc[-12:].mean() / tc_.iloc[-12:].mean())}   # tendencia-ciclo escalada
        def prom_q(q, nivel):
            ms = [q.asfreq("M", "s") + i for i in range(3)]
            return np.mean([d.get(m, nivel) if m <= ult_m else nivel for m in ms])
        nc = {f"nc_{k}": (prom_q(Q, v) / prom_q(Q - 1, v) - 1) * 100 for k, v in rellenos.items()}
        gm = ritmo_mensual(desest, t)
        def prom_coh(q):   # camino coherente: promedio de 3 meses y, desde ahí, crecimiento de tendencia
            ms = [q.asfreq("M", "s") + i for i in range(3)]
            return np.mean([d.get(m, np.nan) if m <= ult_m else rellenos["p3"] * gm ** (m - ult_m).n for m in ms])
        nc["nc_coh"] = (prom_coh(Q) / prom_coh(Q - 1) - 1) * 100
        vers = V[V["version"] <= t]
        if vers.empty:
            return None
        uv = vers[vers["version"] == vers["version"].max()].dropna(subset=["g"])
        ms_q = [Q.asfreq("M", "s") + i for i in range(3)]
        k_ = (Q - t.asfreq("Q")).n
        return {**nc, "emae_nc": nc["nc_coh"], "meses_conocidos": sum(m <= ult_m for m in ms_q),
                "pib_ult": uv["g"].iloc[-1], "pib_ult_Q": uv["Q"].iloc[-1], "emae_mm": (d.iloc[-1] / d.iloc[-2] - 1) * 100}

    def mezcla(v, meses, t, Q):
        """Trimestre sin ningún mes de EMAE: promedio con el REM anterior (mediana del relevamiento t-1 para Q)."""
        ra = g[(g["origen"] == t - 1) & (g["Q"] == Q)]["rem"].mean()
        return (v + ra) / 2 if meses == 0 and not np.isnan(ra) else v

    filas = []
    for _, x in g.iterrows():
        f = rasgos(x["origen"], x["Q"])
        if f is None:
            continue
        filas.append({**x.to_dict(), **f, "real": real.get(x["Q"], np.nan)})
    X = pd.DataFrame(filas).sort_values(["k", "origen"]).reset_index(drop=True)
    res = []
    for k, q in X.groupby("k"):
        for i, row in q.iterrows():
            o = row["origen"]
            tr = q[q["origen"] < o].dropna(subset=COLS + ["rem"])
            out = {"origen": o, "Q": row["Q"], "k": k, "rem": row["rem"], "real": row["real"], "p10": row["p10"], "p90": row["p90"], "p25": row["p25"], "pmax": row["pmax"],
                   "emae_solo": row["emae_nc"]}
            if len(tr) >= MIN:
                out["A_pib"] = huber(tr[COLS].values.astype(float), tr["rem"].values.astype(float), row[COLS].values.astype(float))
            out["I_pib"] = mezcla(row["emae_nc"], row["meses_conocidos"], o, row["Q"])
            res.append(out)
    R = pd.DataFrame(res)
    R.to_csv(C / "replica_pib.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    etq = {-1: "trim. anterior", 0: "trim. en curso", 1: "trim. siguiente", 2: "trim. +2"}
    L = ["=== NUESTRO PRONÓSTICO DEL PIB TRIMESTRAL (desestacionalizado, var. % contra el trimestre anterior) ===",
         "Real = primer dato publicado por el INDEC. Errores en puntos porcentuales.",
         "ADVERTENCIA: el EMAE del pasado es la versión actual (pudo revisarse); el PIB sí está en tiempo real (42 versiones).", ""]
    for per, m in (("2016-2024", R["origen"] <= pd.Period("2024-12", "M")), ("2025-26", R["origen"] > pd.Period("2024-12", "M"))):
        q = R[m]
        L.append(f"== {per}")
        L.append(f"   {'':16}{'distancia al REM':>34} | {'error contra el primer dato':>42} | {'dentro P25-máx':>16}")
        L.append(f"   {'':16}{'EMAE':>8}{'A':>8}{'I':>8}{'n':>6} | {'EMAE':>8}{'A':>8}{'I':>8}{'REM':>8}{'n':>6}{'p DM I-REM':>12} | {'A':>8}{'I':>8}")
        for k in sorted(q["k"].unique()):
            z = q[q["k"] == k].dropna(subset=["A_pib", "I_pib"])
            zr = z.dropna(subset=["real"])
            if not len(z):
                continue
            d = lambda c, ref, zz: (zz[c] - zz[ref]).abs().mean()
            try:
                p = diebold_mariano((zr.I_pib - zr.real).values, (zr.rem - zr.real).values, max(k + 2, 1)) if len(zr) > 6 else np.nan
            except Exception:
                p = np.nan
            cob = lambda c: ((z[c] >= z.p25) & (z[c] <= z.pmax)).mean() * 100
            L.append(f"   {etq[k]:16}{d('emae_solo', 'rem', z):8.2f}{d('A_pib', 'rem', z):8.2f}{d('I_pib', 'rem', z):8.2f}{len(z):6d} | "
                     f"{d('emae_solo', 'real', zr):8.2f}{d('A_pib', 'real', zr):8.2f}{d('I_pib', 'real', zr):8.2f}{d('rem', 'real', zr):8.2f}"
                     f"{len(zr):6d}{p:12.2f} | {cob('A_pib'):7.0f}%{cob('I_pib'):7.0f}%")
        L.append("")
    # pronóstico de hoy
    t = pd.Period(pd.Timestamp.today(), "M")
    pub = V[V["version"] <= t]["Q"].max()                 # último trimestre publicado
    Qs = [pub + 1, pub + 2, pub + 3]
    hoy, reg = [], []
    for Q in Qs:
        f = rasgos(t, Q)
        if f is None:
            continue
        k = (Q - t.asfreq("Q")).n
        q = X[(X["k"] == k)].dropna(subset=COLS + ["rem"])
        a = huber(q[COLS].values.astype(float), q["rem"].values.astype(float), np.array([f[c] for c in COLS], float)) if len(q) >= MIN else np.nan
        i_ = mezcla(f["emae_nc"], f["meses_conocidos"], t, Q)
        rem_ant = g[(g["origen"] == t - 1) & (g["Q"] == Q)]["rem"].mean()
        hoy.append(f"   {str(Q):8} {rem_ant:12.2f} {f['nc_ult']:9.2f} {a:8.2f} {i_:8.2f}   (meses de EMAE conocidos: {f['meses_conocidos']})")
        reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t), "trimestre": str(Q),
                    "emae_solo": f["emae_nc"], "A_pib": a, "I_pib": i_, "rem_anterior": rem_ant})
    if reg:
        p = C / "registro_pib.csv"
        pd.DataFrame(reg).to_csv(p, mode="a", header=not p.exists(), index=False, encoding="utf-8-sig", float_format="%.3f")
    L += [f"PRONÓSTICO DE HOY (último PIB publicado: {pub}, {V[V['version'] <= t].dropna(subset=['g']).sort_values(['version', 'Q']).iloc[-1]['g']:+.2f}%; "
          f"EMAE hasta {desest.index.max()}):", f"   {'trimestre':8} {'REM anterior':>12} {'EMAE últ.':>9} {'A-PIB':>8} {'I-PIB':>8}"] + hoy
    if not (R["origen"] == t).any():   # el relevamiento en curso todavía no está en R (30-oct-2026: el anual de hoy no los usaba)
        R = pd.concat([R, pd.DataFrame([{"origen": t, "Q": pd.Period(x["trimestre"], "Q"), "I_pib": x["I_pib"]} for x in reg])],
                      ignore_index=True)
    L += [""] + pib_anual(V, R, r, real, t, desest)
    # ALERTA R02 (17-nov-2026): trimestre de hoy sin I-PIB (no se rellena, pero se avisa; ver ALERTA R01 en replicar_rem.py)
    vacios = [x["trimestre"] for x in reg if pd.isna(x["I_pib"])]
    if vacios:
        L += [f"ALERTA R02: I-PIB vacío en {', '.join(vacios)} (relevamiento {t}; EMAE hasta {desest.dropna().index.max()}).",
              "   Efecto: la tarjeta del PIB trimestral y el PIB anual quedan incompletos o mal calculados.",
              "   Qué hacer: revisar datos\\emae.parquet (actualizar_actividad.py) o pedirle a Claude que lo revise.",
              f"ALERTAS: 1 (PIB de hoy incompleto)"]
    else:
        L.append("ALERTAS: ninguna. Todo en orden.")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_pib.txt").write_text(texto, encoding="utf-8")
    if vacios:
        sys.exit(1)


if __name__ == "__main__":
    main()
