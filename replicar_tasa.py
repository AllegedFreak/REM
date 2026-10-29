# replicar_tasa.py
# Nuestro pronóstico de la TASA DE INTERÉS como la pide el REM: promedio mensual, TNA %, meses t+1 a t+6.
# El REM pidió la BADLAR de bancos privados hasta nov-2024 y la TAMAR de bancos privados desde dic-2024: se usan juntas
# (cada relevamiento contra su propia tasa) para tener más historia (2020-01 en adelante).
# Información en el relevamiento de fin del mes t:
#   r_now  = promedio de la tasa del mes t hasta el día 25 (mínimo 8 días con dato);
#   fwd_h  = tasa "forward" del mes t+h implícita en los futuros de dólar del CEM (cociente de contratos consecutivos,
#            anualizado), del último día con dato hasta el 25; si no hay contrato, se repite el último forward.
# Modelos (estimación robusta Huber, ventana acumulada, mínimo 24 relevamientos, uno por horizonte):
#   se queda : r_now.
#   A-tasa   : r_now + Huber(REM_h - r_now ~ fwd_h - r_now, r_now - r_mes_anterior, tasa real). Imita al REM; sin REM.
#   B-tasa   : REM anterior del mismo mes + Huber(cambio ~ sorpresa de la tasa del mes t, cambio del forward).
# Real = promedio mensual de la tasa (todos los días del mes, mínimo 10).
# Salidas: datos\replica_tasa.csv, datos\informe_tasa.txt, datos\registro_tasa.csv
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
MIN = 24
H = range(1, 7)
CAMBIO = pd.Period("2024-12", "M")          # desde este relevamiento el REM pide TAMAR (antes BADLAR)


CORRIDA = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")   # UNA sola hora por corrida (28-oct-2026: con la hora fila por fila,
# exportaciones e importaciones quedaron en 12:12 y 12:13 y el sello n.º 1 salió sin exportaciones)


def huber(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(Xc, y, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def cargar():
    b = pd.read_parquet(C / "bcra_series.parquet")
    b["fecha"] = pd.to_datetime(b["fecha"])
    tasas = {s: b[b["serie"] == s].set_index("fecha")["valor"].sort_index() for s in ("badlar_tna", "tamar_tna")}
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[r["variable_std"].isin(["tasa_badlar", "tasa_tamar"]) & (r["tipo_periodo"] == "fecha")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["mes"] = pd.to_datetime(g["periodo_fin"]).dt.to_period("M")
    g["h"] = [(m - o).n for m, o in zip(g["mes"], g["origen"])]
    g["serie"] = np.where(g["variable_std"] == "tasa_tamar", "tamar_tna", "badlar_tna")
    g = g[g["h"].between(0, 6)].groupby(["origen", "mes", "h", "serie"]).agg(
        rem=("mediana", "mean"), p10=("percentil_10", "mean"), p90=("percentil_90", "mean")).reset_index()
    f = pd.read_parquet(C / "dolar_futuro_cem.parquet")
    f = f[f["optionType"].isna() & f["symbol"].str.fullmatch(r"DLR\d{6}")].copy()
    f["fecha"] = pd.to_datetime(f["fecha"])
    f["venc"] = [pd.Period(f"{s[-4:]}-{s[3:5]}", "M") for s in f["symbol"]]
    ipc = pd.read_parquet(C / "ipc_indec.parquet")
    nuc = pd.Series(ipc["ipc_nucleo_indice_var_mensual"].values, index=pd.to_datetime(ipc["mes"]).dt.to_period("M"))
    lic = pd.read_parquet(C / "licitaciones_tesoro.parquet")
    lic = lic[lic["tna_corte"].notna()].copy()
    return tasas, g, f, nuc, lic


def prom_mes(s, m, hasta_dia=None, minimo=10):
    x = s[(s.index.to_period("M") == m)]
    if hasta_dia:
        x = x[x.index.day <= hasta_dia]
    return x.mean() if len(x) >= minimo else np.nan


def forwards(f, t):
    """Tasa forward (TNA %) de cada mes t+h implícita en la curva de futuros del último día hábil hasta el 25 de t."""
    d = f[(f["fecha"].dt.to_period("M") == t) & (f["fecha"].dt.day <= 25)]
    if d.empty:
        return {}
    d = d[d["fecha"] == d["fecha"].max()].set_index("venc")["settlement"].dropna()
    out, ult = {}, np.nan
    for h in range(1, 13):
        a, b = d.get(t + h - 1), d.get(t + h)
        if pd.notna(a) and pd.notna(b) and a > 0:
            ult = (b / a - 1) * 12 * 100           # devaluación mensual implícita, anualizada (TNA)
        out[h] = ult
    return out


def implicitas(f, t):
    """Tasa implícita (TNA %) informada por el CEM para el contrato que vence en t+h, último día hábil hasta el 25 de t."""
    d = f[(f["fecha"].dt.to_period("M") == t) & (f["fecha"].dt.day <= 25)]
    if d.empty:
        return {}
    d = d[d["fecha"] == d["fecha"].max()].set_index("venc")["impliedRate"].dropna()
    out, ult = {}, np.nan
    for h in range(0, 13):
        ult = d.get(t + h, ult)
        out[h] = ult
    return out


def lecap(lic, t):
    """Tasas de corte (TNA %) de la última licitación a tasa fija liquidada hasta el día 25 del mes t (y cuántos meses
    de antigüedad tiene): corta = mediana de las letras a <= 120 días; larga = mediana de las de más de 150 días."""
    hasta = pd.Timestamp(t.start_time) + pd.Timedelta(days=24)
    d = lic[lic["liquidacion"] <= hasta]
    if d.empty:
        return np.nan, np.nan, np.nan
    u = d[d["liquidacion"] == d["liquidacion"].max()]
    corta = u.loc[u["dias"] <= 120, "tna_corte"].median()
    if pd.isna(corta):                       # sin letra corta en la última: la más corta de esa licitación
        corta = u.sort_values("dias")["tna_corte"].iloc[0]
    larga = u.loc[u["dias"] > 150, "tna_corte"].median()
    edad = (t - u["liquidacion"].max().to_period("M")).n
    return corta, larga, edad


def main():
    tasas, g, f, nuc, lic = cargar()
    filas = []
    for (o, serie), q in g.groupby(["origen", "serie"]):
        s = tasas[serie]
        r_now = prom_mes(s, o, 25, 8)
        r_ant = prom_mes(s, o - 1)
        fw = forwards(f, o)
        fw_ant = forwards(f, o - 1)
        im = implicitas(f, o)
        lc, ll, edad = lecap(lic, o)
        nuc3 = nuc[(nuc.index <= o - 1) & (nuc.index >= o - 3)]
        real_rate = r_now - nuc3.mean() * 12 if len(nuc3) == 3 else np.nan
        prev = g[(g["origen"] == o - 1)].set_index("mes")
        for _, x in q.iterrows():
            m, h = x["mes"], x["h"]
            if h < 1:
                continue
            misma = len(prev) and prev["serie"].eq(serie).all()
            rem_ant = (prev["rem"].get(m, prev["rem"].iloc[-1] if len(prev) else np.nan)) if misma else np.nan
            rem_ant_t = prev["rem"].get(o, np.nan) if misma else np.nan
            filas.append({"origen": o, "mes": m, "h": h, "serie": serie, "rem": x["rem"], "p10": x["p10"], "p90": x["p90"],
                          "real": prom_mes(s, m), "r_now": r_now, "dr": r_now - r_ant, "real_rate": real_rate,
                          "fwd_gap": fw.get(h, np.nan) - r_now, "impl_gap": im.get(h, np.nan) - r_now,
                          "lecap_gap": lc - r_now if edad is not np.nan and edad <= 1 else np.nan, "lecap_pend": ll - lc, "rem_ant": rem_ant,
                          "sorpresa": r_now - rem_ant_t, "dfwd": fw.get(h, np.nan) - fw_ant.get(h + 1, np.nan)})
    X = pd.DataFrame(filas).sort_values(["h", "origen"]).reset_index(drop=True)
    COLS_A = ["fwd_gap", "dr", "real_rate"]
    COLS_B = ["sorpresa", "dfwd"]
    X["se_queda"] = X["r_now"]
    X["A"] = np.nan
    X["A_impl"] = np.nan
    X["A_lecap"] = np.nan
    COLS_L = COLS_A + ["lecap_gap"]
    COLS_I = ["impl_gap", "dr", "real_rate"]
    X["B"] = np.nan
    X["dif_rem"] = X["rem"] - X["r_now"]
    X["rev"] = X["rem"] - X["rem_ant"]
    for h, q in X.groupby("h"):
        for i, row in q.iterrows():
            tr = q[q["origen"] < row["origen"]]
            ta = tr.dropna(subset=COLS_A + ["dif_rem"])
            if len(ta) >= MIN and row[COLS_A].notna().all():
                X.loc[i, "A"] = row["r_now"] + huber(ta[COLS_A].values, ta["dif_rem"].values, row[COLS_A].values.astype(float))
            tl = tr.dropna(subset=COLS_L + ["dif_rem"])
            if len(tl) >= MIN and row[COLS_L].notna().all():
                X.loc[i, "A_lecap"] = row["r_now"] + huber(tl[COLS_L].values, tl["dif_rem"].values, row[COLS_L].values.astype(float))
            ti = tr.dropna(subset=COLS_I + ["dif_rem"])
            if len(ti) >= MIN and row[COLS_I].notna().all():
                X.loc[i, "A_impl"] = row["r_now"] + huber(ti[COLS_I].values, ti["dif_rem"].values, row[COLS_I].values.astype(float))
            tb = tr.dropna(subset=COLS_B + ["rev"])
            if pd.notna(row["rem_ant"]):
                if len(tb) >= MIN and row[COLS_B].notna().all():
                    X.loc[i, "B"] = row["rem_ant"] + huber(tb[COLS_B].values, tb["rev"].values, row[COLS_B].values.astype(float))
                else:
                    X.loc[i, "B"] = row["rem_ant"]
    X.to_csv(C / "replica_tasa.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    L = ["=== NUESTRO PRONÓSTICO DE LA TASA (BADLAR hasta nov-2024, TAMAR desde dic-2024; promedio mensual, TNA %) ===",
         "Distancia al REM y error contra la tasa real, en puntos porcentuales de TNA. Fuera de muestra, tiempo real.", ""]
    for per, m in (("2020-2024 (BADLAR)", X["origen"] < CAMBIO), ("2025-26 (TAMAR)", X["origen"] >= CAMBIO)):
        q = X[m].dropna(subset=["A"])
        L.append(f"== {per}")
        L.append(f"   {'':4}{'distancia al REM':>30} | {'error contra la tasa real':>40} | {'dentro P10-P90':>22}")
        L.append(f"   {'h':4}{'queda':>8}{'A':>8}{'B':>8}{'n':>6} | {'queda':>8}{'A':>8}{'B':>8}{'REM':>8}{'n':>5}{'p A-REM':>9} | "
                 f"{'queda':>7}{'A':>7}{'B':>7}")
        for h in H:
            z = q[q["h"] == h]
            zr = z.dropna(subset=["real"])
            if not len(z):
                continue
            d = lambda c, ref, zz: (zz[c] - zz[ref]).abs().mean()
            try:
                p = diebold_mariano((zr.A - zr.real).values, (zr.rem - zr.real).values, h) if len(zr) > 6 else np.nan
            except Exception:
                p = np.nan
            cob = lambda c: ((z[c] >= z.p10) & (z[c] <= z.p90)).mean() * 100
            L.append(f"   {h:<4}{d('se_queda', 'rem', z):8.2f}{d('A', 'rem', z):8.2f}{d('B', 'rem', z):8.2f}{len(z):6d} | "
                     f"{d('se_queda', 'real', zr):8.2f}{d('A', 'real', zr):8.2f}{d('B', 'real', zr):8.2f}{d('rem', 'real', zr):8.2f}"
                     f"{len(zr):5d}{p:9.2f} | {cob('se_queda'):6.0f}%{cob('A'):6.0f}%{cob('B'):6.0f}%")
        L.append("")
    # coeficientes de A con toda la muestra
    L.append("Qué mira el REM (A, toda la muestra, Huber): REM_h − tasa actual = a + b·(forward − tasa) + c·(cambio del mes) + d·(tasa real)")
    for h in (1, 3, 6):
        q = X[X["h"] == h].dropna(subset=COLS_A + ["dif_rem"])
        fit = sm.RLM(q["dif_rem"].values, sm.add_constant(q[COLS_A].values), M=sm.robust.norms.HuberT()).fit()
        L.append(f"   h{h}: " + "  ".join(f"{n} {b:+.3f} (t {t:.1f})" for n, b, t in
                                         zip(["const"] + COLS_A, fit.params, fit.tvalues)) + f"   n={len(q)}")
    L.append("")
    # pronóstico de hoy
    t = pd.Period(pd.Timestamp.today(), "M")
    s = tasas["tamar_tna"]
    r_now = prom_mes(s, t, 25, 8) if pd.Timestamp.today().day >= 25 else np.nan
    hasta = "hasta el 25"
    if pd.isna(r_now):
        r_now = s[s.index.to_period("M") == t].mean()
        hasta = f"PROVISORIO: {int((s.index.to_period('M') == t).sum())} días"
    fw = forwards(f, t)
    nuc3 = nuc[(nuc.index <= t - 1) & (nuc.index >= t - 3)]
    row = {"r_now": r_now, "dr": r_now - prom_mes(s, t - 1), "real_rate": r_now - nuc3.mean() * 12}
    prev = g[(g["origen"] == g["origen"].max())].set_index("mes")
    L.append(f"PRONÓSTICO DE HOY (TAMAR del mes {t}: {r_now:.2f}% [{hasta}]; REM de referencia: {g['origen'].max()}):")
    L.append(f"   {'mes':9}{'REM ant.':>9}{'queda':>8}{'A':>8}{'forward':>9}")
    reg = []
    for h in H:
        q = X[X["h"] == h].dropna(subset=COLS_A + ["dif_rem"])
        x0 = np.array([fw.get(h, np.nan) - r_now, row["dr"], row["real_rate"]], float)
        a = r_now + huber(q[COLS_A].values, q["dif_rem"].values, x0) if not np.isnan(x0).any() and len(q) >= MIN else np.nan
        m = t + h
        ra = prev["rem"].get(m, np.nan)
        L.append(f"   {str(m):9}{ra:9.2f}{r_now:8.2f}{a:8.2f}{fw.get(h, np.nan):9.2f}")
        reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t), "mes": str(m), "h": h,
                    "se_queda": r_now, "A": a, "forward": fw.get(h, np.nan), "rem_anterior": ra, "provisorio": hasta != "hasta el 25"})
    p = C / "registro_tasa.csv"
    pd.DataFrame(reg).to_csv(p, mode="a", header=not p.exists(), index=False, encoding="utf-8-sig", float_format="%.3f")
    L.append("ALERTAS: ninguna. Todo en orden.")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_tasa.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
