# replicar_desempleo.py
# Nuestro pronóstico de la TASA DE DESOCUPACIÓN (EPH, 31 aglomerados, % de la PEA) como la pide el REM (desde dic-2020):
# tres trimestres (el del relevamiento y los dos siguientes) y el IV trimestre del año en curso y del siguiente
# (lo que el Excel del REM llama "año" es el IV trimestre).
# Dato real: datos\desempleo.parquet (actualizar_desempleo.py). El INDEC no revisa la tasa: primer dato = dato de hoy.
# En el relevamiento de fin del mes t se conocen los trimestres con "publicado" <= t (el INDEC publica a mediados del
# 3er mes después del trimestre). EMAE conocido hasta t-2 (versión actual).
# Serie desde II-2016 (2007-2015 desestimada por el INDEC; ver CLAUDE.md). Para estimar se sacan II-2020 a I-2021
# (pandemia: encuesta telefónica y caída del empleo fuera de toda relación normal).
# Modelos:
#   I est  : "se queda" desestacionalizado: último dato − su estacionalidad + la del trimestre pronosticado.
#            Estacionalidad = desvío promedio de cada trimestre respecto del promedio de su año (años completos, sin 2020).
#   I okun : mismo trimestre del año anterior + c + b × crecimiento interanual del EMAE (últimos 3 meses conocidos,
#            se supone que sigue igual); c y b estimados con lo conocido en cada fecha (cambio interanual de la tasa
#            contra crecimiento interanual del EMAE). Los trimestres sin dato se encadenan.
#   I      : = I est (decisión de Marcos, 28-oct-2026: es lo más simple y ni Okun, ni la tendencia de la EPH, ni el SIPA
#            le ganaron fuera de muestra; ver probar_sipa.py). Para comparar queda "I prom" = promedio de I est e I okun.
#   A      : regresión robusta (Huber) de (REM − I est) sobre (I okun − I est) y el último cambio interanual de la tasa;
#            una por horizonte, ventana acumulada, mínimo 24 relevamientos. No usa el REM del mismo relevamiento.
# Errores en puntos porcentuales (pp). El error de muestreo de la EPH es ~±0,5 pp (intervalo al 90%).
# Salidas: datos\replica_desempleo.csv, datos\informe_desempleo.txt, datos\registro_desempleo.csv
import sys
from datetime import datetime
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
MIN = 24
PANDEMIA = pd.period_range("2020Q2", "2021Q1", freq="Q")
ROMANO = {"I": 1, "II": 2, "III": 3, "IV": 4}


CORRIDA = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")   # UNA sola hora por corrida (28-oct-2026: con la hora fila por fila,
# exportaciones e importaciones quedaron en 12:12 y 12:13 y el sello n.º 1 salió sin exportaciones)


def huber(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(Xc, y, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def objetivo(x):
    """Trimestre que pide una fila del REM: 'Trim. III-26' → 2026Q3; año '2026' → 2026Q4."""
    p = str(x["periodo_original"]).strip()
    if x["tipo_periodo"] == "anio":
        return pd.Period(f"{int(float(p))}Q4", "Q")
    r, a = p.replace("Trim.", "").strip().split("-")
    return pd.Period(f"{2000 + int(a)}Q{ROMANO[r]}", "Q")


def estacionalidad(u):
    """Desvío promedio de cada trimestre respecto del promedio de su año (años completos, sin 2020)."""
    z = u[~u.index.isin(PANDEMIA)]
    z = z[z.index.year != 2020]
    anios = [a for a, g in z.groupby(z.index.year) if len(g) == 4]
    dev = pd.concat([z[z.index.year == a] - z[z.index.year == a].mean() for a in anios]) if anios else pd.Series(dtype=float)
    s = dev.groupby(dev.index.quarter).mean().reindex([1, 2, 3, 4]).fillna(0)
    return s - s.mean()


def okun(u, yq):
    """c y b de: cambio interanual de la tasa = c + b × crecimiento interanual del EMAE (sin pandemia ni sus rebotes)."""
    d = pd.DataFrame({"du": u - u.shift(4, freq="Q"), "gy": 100 * np.log(yq / yq.shift(4, freq="Q"))}).dropna()
    d = d[~d.index.isin(PANDEMIA) & ~d.index.isin(PANDEMIA + 4)]
    if len(d) < 8:
        return np.nan, np.nan
    b, c = np.polyfit(d["gy"], d["du"], 1)
    return c, b


def main():
    U = pd.read_parquet(C / "desempleo.parquet")
    U["q"] = pd.PeriodIndex(U["trimestre"], freq="Q")
    U["pub"] = pd.PeriodIndex(U["publicado"], freq="M")
    u_all = U.set_index("q")["desocupacion"].sort_index()
    e = pd.read_parquet(C / "emae.parquet")
    e["mes"] = pd.PeriodIndex(e["mes"].astype(str), freq="M")
    emae = e.set_index("mes")["original"].sort_index()
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[r["variable_std"] == "desocupacion"].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["obj"] = [objetivo(x) for _, x in g.iterrows()]
    g["tipo"] = np.where(g["tipo_periodo"] == "anio", "año", "trim")
    t_hoy = pd.Period(pd.Timestamp.today(), "M")
    filas = []
    for t in sorted(set(g["origen"]) | {t_hoy}):
        u = U[U["pub"] <= t].set_index("q")["desocupacion"].sort_index()
        em = emae[emae.index <= t - 2]
        if len(u) < 12 or len(em) < 15:
            continue
        ult = u.index.max()
        s = estacionalidad(u)
        mq = em.groupby(em.index.asfreq("Q")).agg(["mean", "size"])
        yq = mq.loc[mq["size"] == 3, "mean"]
        c, b = okun(u, yq)
        g_now = 100 * np.log(em.iloc[-3:].sum() / em.reindex(em.index[-3:] - 12).sum())
        du_ult = u[ult] - u.get(ult - 4, np.nan)
        if t == t_hoy:   # lo que va a pedir el próximo REM: el trimestre anterior al del mes siguiente, los dos que siguen
            q1 = (t + 1).asfreq("Q") - 1   # y el IV trimestre del año del mes siguiente y del otro (patrón del REM 2025-26)
            pedidos = [(q, "trim") for q in pd.period_range(q1, q1 + 2, freq="Q")] + [(pd.Period(f"{a}Q4", "Q"), "año") for a in ((t + 1).year, (t + 1).year + 1)]
        else:            # en diciembre el REM ya pide el año siguiente y el otro
            pedidos = list(g.loc[g["origen"] == t, ["obj", "tipo"]].itertuples(index=False, name=None))
        # caminos hasta el último trimestre pedido
        fin = max(q for q, _ in pedidos)
        est = {q: u[ult] - s[ult.quarter] + s[q.quarter] for q in pd.period_range(ult + 1, fin, freq="Q")}
        ok = dict(u)
        for q in pd.period_range(ult + 1, fin, freq="Q"):
            ok[q] = ok[q - 4] + c + b * g_now
        q_rem = g[g["origen"] == t].set_index(["obj", "tipo"])
        for q, tipo in pedidos:
            if q <= ult:          # el REM a veces pide un trimestre ya publicado: no hay nada que pronosticar
                continue
            x = q_rem.loc[(q, tipo)] if (q, tipo) in q_rem.index else None
            filas.append({"origen": t, "tipo": tipo, "objetivo": str(q), "h": (q - ult).n,
                          "etiqueta": f"h{(q - ult).n}" if tipo == "trim" else "año" + (f"+{q.year - t.year}" if q.year > t.year else ""),
                          "ult_conocido": str(ult), "rem": np.nan if x is None else x["mediana"],
                          "p10": np.nan if x is None else x["percentil_10"], "p90": np.nan if x is None else x["percentil_90"],
                          "real": u_all.get(q, np.nan), "I_est": est[q], "I_okun": ok[q], "I": est[q], "I_prom": (est[q] + ok[q]) / 2,
                          "okun_c": c, "okun_b": b, "emae_ia": g_now, "du_ult": du_ult})
    X = pd.DataFrame(filas)
    # IV trimestre del año siguiente (30-oct-2026, Marcos): nuestro IV trimestre anterior + el cambio que espera el REM anterior
    # entre los dos IV trimestres (REM del último relevamiento antes de t; sin él, queda "se queda").
    ra = g[g["tipo"] == "año"].groupby(["origen", "obj"])["mediana"].mean()
    for i, x in X[X["etiqueta"].str.startswith("año+")].iterrows():
        q = pd.Period(x["objetivo"], "Q")
        prev = [o for o in ra.index.get_level_values(0).unique() if o < x["origen"]]
        if not prev:
            continue
        o = max(prev)
        base = X[(X["origen"] == x["origen"]) & (X["objetivo"] == str(q - 4))]["I"]
        base = base.iloc[0] if len(base) else u_all.get(q - 4, np.nan)
        cambio = ra.get((o, q), np.nan) - ra.get((o, q - 4), np.nan)
        if pd.notna(base) and pd.notna(cambio):
            X.loc[i, "I"] = base + cambio
    X["y"] = X["rem"] - X["I_est"]
    X["gap"] = X["I_okun"] - X["I_est"]
    COLS = ["gap", "du_ult"]
    X["A"] = np.nan
    for _, q in X.groupby("h"):   # el IV trimestre que el REM llama "año" es un trimestre más: se estima junto con los otros
        for i, row in q.iterrows():
            tr = q[q["origen"] < row["origen"]].dropna(subset=COLS + ["y"])
            if len(tr) >= MIN and row[COLS].notna().all():
                X.loc[i, "A"] = row["I_est"] + huber(tr[COLS].values, tr["y"].values, row[COLS].values.astype(float))
    X.to_csv(C / "replica_desempleo.csv", index=False, encoding="utf-8-sig", float_format="%.3f")

    L = [f"=== NUESTRO PRONÓSTICO DE LA DESOCUPACIÓN (EPH, % de la PEA) | {datetime.now():%Y-%m-%d %H:%M} ===",
         "I = 'se queda' desestacionalizado (I est). Errores en pp, fuera de muestra y en tiempo real. Real = EPH (el INDEC no la revisa). Ruido de muestreo ~±0,5 pp.",
         "hN = N trimestres después del último publicado (h1 = el primero sin dato); año = IV trim. del año en curso; año+1 = del siguiente.", ""]
    orden = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "año": 5, "año+1": 6, "año+2": 7}
    for per, m_ in (("2020-2024", X["origen"] <= pd.Period("2024-12", "M")), ("2025-26", X["origen"] > pd.Period("2024-12", "M"))):
        L.append(f"== {per}  (A se evalúa desde que tiene {MIN} relevamientos; cada fila usa la misma muestra para todos)")
        L.append(f"   {'':7}{'distancia al REM':>30} | {'error contra el real':>44} | {'P10-P90':>12}")
        L.append(f"   {'':7}{'I prom':>7}{'I okun':>8}{'I':>7}{'A':>7}{'n':>4} | {'I prom':>7}{'I okun':>8}{'I':>7}{'A':>7}{'REM':>7}{'n':>4}{'p I-REM':>8} | {'I':>5}{'A':>6}")
        Z = X[m_ & X["rem"].notna()]
        for et in sorted(Z["etiqueta"].unique(), key=lambda k: orden.get(k, 9)):
            z = Z[Z["etiqueta"] == et]
            za = z.dropna(subset=["A"])
            zr = z.dropna(subset=["real"])
            zra = za.dropna(subset=["real"])
            d = lambda c, ref, zz: (zz[c] - zz[ref]).abs().mean() if len(zz) else np.nan
            h = int(z["h"].max())
            p = diebold_mariano((zr["I"] - zr["real"]).values, (zr["rem"] - zr["real"]).values, 3 * (h + 1)) if len(zr) > 10 else np.nan
            cob = lambda c, zz: ((zz[c] >= zz["p10"]) & (zz[c] <= zz["p90"])).mean() * 100 if len(zz) else np.nan
            L.append(f"   {et:7}{d('I_prom', 'rem', z):7.2f}{d('I_okun', 'rem', z):8.2f}{d('I', 'rem', z):7.2f}{d('A', 'rem', za):7.2f}{len(z):4d} | "
                     f"{d('I_prom', 'real', zr):7.2f}{d('I_okun', 'real', zr):8.2f}{d('I', 'real', zr):7.2f}{d('A', 'real', zra):7.2f}"
                     f"{d('rem', 'real', zr):7.2f}{len(zr):4d}{p:8.2f} | {cob('I', z):4.0f}%{cob('A', za):5.0f}%")
            if len(zra):
                L.append(f"   {'':7}{'':37} | (muestra de A, n {len(zra)}: A {d('A', 'real', zra):.2f} vs REM {d('rem', 'real', zra):.2f})")
        L.append("")
    hoy = X[X["origen"] == t_hoy].sort_values("etiqueta", key=lambda s: s.map(lambda k: orden.get(k, 9)))
    ult_rem = g["origen"].max()
    ra = g[g["origen"] == ult_rem].set_index(["obj", "tipo"])["mediana"]
    if len(hoy):
        h0 = hoy.iloc[0]
        L.append(f"PRONÓSTICO DE HOY (desocupación; EPH hasta {h0['ult_conocido']}; EMAE interanual {h0['emae_ia']:.1f}%; "
                 f"Okun: {h0['okun_b']:.2f} pp por punto de PIB; REM de referencia {ult_rem}):")
        L.append(f"   {'período':10}{'REM ant.':>9}{'I prom':>8}{'I okun':>8}{'I':>8}{'A':>8}")
        reg = []
        for _, x in hoy.iterrows():
            q = pd.Period(x["objetivo"], "Q")
            rv = ra.get((q, x["tipo"]), np.nan)
            L.append(f"   {x['objetivo'] + ' ' + x['etiqueta']:16}{rv:5.1f}{x['I_prom']:8.1f}{x['I_okun']:8.1f}{x['I']:8.1f}{x['A']:8.1f}")
            reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "periodo": x["objetivo"],
                        "etiqueta": x["etiqueta"], "I": x["I"], "A": x["A"], "rem_anterior": rv})
        p = C / "registro_desempleo.csv"
        pd.DataFrame(reg).to_csv(p, mode="a", header=not p.exists(), index=False, encoding="utf-8-sig", float_format="%.2f")
    L += ["", "ALERTAS: ninguna. Todo en orden."]
    texto = "\n".join(L)
    print(texto)
    (C / "informe_desempleo.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
