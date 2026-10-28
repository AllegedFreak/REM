# probar_sipa.py (28-oct-2026; fuera del circuito diario, como nowcast_emae.py)
# ¿Qué anticipa mejor la DESOCUPACIÓN de la EPH: la propia serie de la EPH o el empleo registrado del SIPA?
# Variable a explicar: cuánto se aparta la tasa del "se queda" desestacionalizado (I est de replicar_desempleo.py),
#   y = u(objetivo) − I est, a 1, 2 y 3 trimestres del último publicado (h1, h2, h3).
# Señales (todas en tiempo real: en el mes t solo lo publicado hasta t):
#   EPH    : cambio interanual de la tasa en el último trimestre publicado (du_ult) y el desvío del último trimestre
#            respecto de su estacionalidad (dev_ult).
#   SIPA   : crecimiento interanual (%) de los últimos 3 meses conocidos del total de trabajadores registrados
#            (151.1_TL_SIN_TAC_2012_M_15) y de los asalariados privados (151.1_AARIADOTAC_2012_M_26), ambos sin
#            estacionalidad; también la aceleración (crecimiento de 3 meses anualizado − interanual).
#            Conocido hasta t−4 (hoy, oct-2026, datos.gob.ar llega a jun-2026); se prueba también t−3.
#            Se usa la versión actual: el SIPA se corrige hacia arriba con declaraciones tardías (sesgo a favor del SIPA).
#   EMAE   : crecimiento interanual de los últimos 3 meses conocidos (t−2), como referencia.
# Orígenes: todos los meses desde mar-2018 (antes no hay un año completo para la estacionalidad) (no solo los del REM), sin los que tocan la pandemia (mar-2020 a sep-2021
# en el origen o en el trimestre pronosticado).
# Pruebas: 1) MCO con errores HAC (Newey-West), cada señal sola y juntas; 2) fuera de muestra (ventana acumulada desde
# 24 orígenes) con Diebold-Mariano (HLN) y Clark-West (modelos anidados); 3) encompassing; 4) placebo: la señal
# corrida al azar en el tiempo (conserva su autocorrelación); 5) estabilidad 2018-2019 vs 2021-2026;
# 6) ¿el REM ya usa el SIPA? (error del REM contra la señal).
# Informe: datos\informe_sipa.txt
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from backtest_rem import diebold_mariano
from replicar_desempleo import estacionalidad, objetivo

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
SIPA = {"sipa_total": "151.1_TL_SIN_TAC_2012_M_15", "sipa_priv": "151.1_AARIADOTAC_2012_M_26"}
PAND_M = pd.period_range("2020-03", "2021-09", freq="M")
PAND_Q = pd.period_range("2020Q1", "2021Q3", freq="Q")
RNG = np.random.default_rng(20261028)


def bajar_sipa():
    cache = C / "sipa_prueba.csv"
    try:
        ids = ",".join(SIPA.values())
        req = urllib.request.Request(f"https://apis.datos.gob.ar/series/api/series/?ids={ids}&format=json&limit=1000",
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=90).read())["data"]
        df = pd.DataFrame(d, columns=["fecha"] + list(SIPA))
        df.to_csv(cache, index=False)
    except Exception as e:
        print(f"(datos.gob.ar no respondió: {e}; uso la copia {cache.name})")
        df = pd.read_csv(cache)
    df["mes"] = pd.PeriodIndex(df["fecha"], freq="M")
    return df.set_index("mes")[list(SIPA)].astype(float)


def crec3(s, hasta):
    """Crecimiento interanual (%) de los 3 meses que terminan en 'hasta', y aceleración (3m anualizado − interanual)."""
    a = s.reindex(pd.period_range(hasta - 2, hasta, freq="M"))
    b = s.reindex(pd.period_range(hasta - 14, hasta - 12, freq="M"))
    c = s.reindex(pd.period_range(hasta - 5, hasta - 3, freq="M"))
    if a.isna().any() or b.isna().any():
        return np.nan, np.nan
    ia = 100 * np.log(a.sum() / b.sum())
    ac = 400 * np.log(a.sum() / c.sum()) - ia if not c.isna().any() else np.nan
    return ia, ac


def armar(lag_sipa):
    U = pd.read_parquet(C / "desempleo.parquet")
    U["q"] = pd.PeriodIndex(U["trimestre"], freq="Q")
    U["pub"] = pd.PeriodIndex(U["publicado"], freq="M")
    u_all = U.set_index("q")["desocupacion"]
    e = pd.read_parquet(C / "emae.parquet")
    e["mes"] = pd.PeriodIndex(e["mes"].astype(str), freq="M")
    emae = e.set_index("mes")["original"]
    S = bajar_sipa()
    filas = []
    for t in pd.period_range("2018-03", pd.Period(pd.Timestamp.today(), "M"), freq="M"):
        u = U[U["pub"] <= t].set_index("q")["desocupacion"].sort_index()
        ult = u.index.max()
        s = estacionalidad(u)
        base = {"origen": t, "ult": ult, "du_ult": u[ult] - u.get(ult - 4, np.nan),
                "dev_ult": u[ult] - u[u.index.year == ult.year].mean() - s[ult.quarter] if (u.index.year == ult.year).sum() == 4
                else u[ult] - u.iloc[-4:].mean() - s[ult.quarter],
                "emae_ia": crec3(emae, t - 2)[0]}
        for k in SIPA:
            base[k + "_ia"], base[k + "_ac"] = crec3(S[k], t - lag_sipa)
        for h in (1, 2, 3):
            q = ult + h
            if q not in u_all.index:
                continue
            f = dict(base, h=h, objetivo=q, real=u_all[q], I_est=u[ult] - s[ult.quarter] + s[q.quarter])
            f["y"] = f["real"] - f["I_est"]
            filas.append(f)
    X = pd.DataFrame(filas)
    malo = X["origen"].isin(PAND_M) | X["objetivo"].isin(PAND_Q) | X["ult"].isin(PAND_Q) | (X["ult"] - 4).isin(PAND_Q)
    return X[~malo].reset_index(drop=True), S


def hac(y, Xm, lags):
    return sm.OLS(y, sm.add_constant(Xm)).fit(cov_type="HAC", cov_kwds={"maxlags": lags})


def clark_west(e0, e1, f0, f1, lags):
    """Clark-West (2007): ¿el modelo grande (1) mejora al anidado (0)? p de una cola."""
    adj = e0 ** 2 - (e1 ** 2 - (f0 - f1) ** 2)
    m = sm.OLS(adj, np.ones(len(adj))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(m.params[0] / m.bse[0]), float(1 - stats.norm.cdf(m.params[0] / m.bse[0]))


def recursivo(z, cols, min_=24):
    """Pronóstico fuera de muestra de y con MCO de ventana acumulada (solo orígenes cuyo objetivo ya se conocía)."""
    pr = pd.Series(np.nan, index=z.index)
    for i, r in z.iterrows():
        tr = z[(z["objetivo"] <= r["ult"])].dropna(subset=cols + ["y"])   # objetivo publicado antes del origen
        tr = tr[tr["origen"] < r["origen"]]
        if len(tr) < min_ or r[cols].isna().any():
            continue
        if not cols:
            pr[i] = tr["y"].mean()
            continue
        b = np.linalg.lstsq(np.column_stack([np.ones(len(tr)), tr[cols].values]), tr["y"].values, rcond=None)[0]
        pr[i] = float(np.r_[1, r[cols].values.astype(float)] @ b)
    return pr


def main():
    L = [f"=== ¿QUÉ ANTICIPA MEJOR LA DESOCUPACIÓN: LA EPH O EL SIPA? | {datetime.now():%Y-%m-%d %H:%M} ===",
         "y = desocupación real − 'se queda' desestacionalizado (I est), en pp. Orígenes mensuales desde mar-2018, sin pandemia.",
         "Tiempo real: EPH publicada hasta t, EMAE hasta t−2, SIPA hasta t−4 (versión actual).", ""]
    X, S = armar(4)
    L.append(f"SIPA en datos.gob.ar: {S.dropna().index.min()} a {S.dropna().index.max()}. Filas: {len(X)} "
             f"(orígenes {X['origen'].min()} a {X['origen'].max()}).")
    SEN = {"EPH: cambio interanual (du_ult)": ["du_ult"], "EPH: desvío del último trim. (dev_ult)": ["dev_ult"],
           "EPH: las dos": ["du_ult", "dev_ult"],
           "SIPA total: interanual": ["sipa_total_ia"], "SIPA total: interanual + aceleración": ["sipa_total_ia", "sipa_total_ac"],
           "SIPA privados: interanual": ["sipa_priv_ia"], "SIPA privados: interanual + aceleración": ["sipa_priv_ia", "sipa_priv_ac"],
           "EMAE: interanual": ["emae_ia"]}
    # ---- 1) regresiones HAC
    L += ["", "1) REGRESIÓN DE y SOBRE CADA SEÑAL (MCO, errores Newey-West; muestra completa sin pandemia)",
          "   Cada fila: coeficiente (t robusto) de cada variable, R² y p conjunto. n = orígenes mensuales (muy superpuestos)."]
    for h in (1, 2, 3):
        z = X[X["h"] == h].dropna(subset=["y", "du_ult", "dev_ult", "sipa_total_ia", "sipa_total_ac", "sipa_priv_ia", "sipa_priv_ac", "emae_ia"])
        L.append(f"   -- h{h} (n {len(z)}, desvío de y {z['y'].std():.2f} pp)")
        for nm, cols in list(SEN.items()) + [("JUNTAS: EPH (las dos) + SIPA total + EMAE", ["du_ult", "dev_ult", "sipa_total_ia", "emae_ia"]),
                                             ("JUNTAS: EPH (las dos) + SIPA privados", ["du_ult", "dev_ult", "sipa_priv_ia"])]:
            m = hac(z["y"], z[cols], 3 * h + 2)
            coefs = "  ".join(f"{c} {m.params[c]:+.3f} ({m.tvalues[c]:+.1f})" for c in cols)
            pj = float(m.f_pvalue) if len(cols) > 1 else float(m.pvalues[cols[0]])
            L.append(f"   {nm:42} R² {m.rsquared:5.2f}  p {pj:5.3f} | {coefs}")
    # ---- 2) fuera de muestra
    L += ["", "2) FUERA DE MUESTRA (MCO de ventana acumulada, mínimo 24 orígenes; misma muestra para todos)",
          "   MAE y RMSE en pp. 'Se queda' = I est solo. DM: p de igual error que 'se queda' (dos colas, HLN).",
          "   CW: Clark-West, p de una cola de que la señal mejora a 'se queda' (corrige el ruido de estimar más parámetros)."]
    MOD = {"Se queda (I est)": [], "EPH (du_ult + dev_ult)": ["du_ult", "dev_ult"], "SIPA total": ["sipa_total_ia"],
           "SIPA privados": ["sipa_priv_ia"], "EMAE": ["emae_ia"], "EPH + SIPA total": ["du_ult", "dev_ult", "sipa_total_ia"],
           "EPH + SIPA privados": ["du_ult", "dev_ult", "sipa_priv_ia"]}
    OOS = {}
    for h in (1, 2, 3):
        z = X[X["h"] == h].dropna(subset=["y", "du_ult", "dev_ult", "sipa_total_ia", "sipa_priv_ia", "emae_ia"]).copy()
        for nm, cols in MOD.items():
            z["p_" + nm] = recursivo(z, cols)
        z = z.dropna(subset=[c for c in z.columns if c.startswith("p_")])
        OOS[h] = z
        L.append(f"   -- h{h} (n {len(z)}, orígenes {z['origen'].min()} a {z['origen'].max()})")
        e0 = (z["y"] - z["p_Se queda (I est)"]).values
        f0 = z["p_Se queda (I est)"].values
        for nm in MOD:
            e1 = (z["y"] - z["p_" + nm]).values
            dm = diebold_mariano(e1, e0, 3 * h + 2) if nm != "Se queda (I est)" else np.nan
            cw = clark_west(e0, e1, f0, z["p_" + nm].values, 3 * h + 2) if nm != "Se queda (I est)" else (np.nan, np.nan)
            L.append(f"   {nm:24} MAE {np.abs(e1).mean():5.2f}  RMSE {np.sqrt((e1 ** 2).mean()):5.2f}  DM p {dm:5.2f}  CW t {cw[0]:+5.2f} p {cw[1]:5.3f}")
        # 3) encompassing: y − f_eph = λ (f_sipa − f_eph)
        for a, b in (("EPH (du_ult + dev_ult)", "SIPA total"), ("SIPA total", "EPH (du_ult + dev_ult)"),
                     ("EPH (du_ult + dev_ult)", "SIPA privados"), ("SIPA privados", "EPH (du_ult + dev_ult)")):
            ea = z["y"] - z["p_" + a]
            m = sm.OLS(ea.values, (z["p_" + b] - z["p_" + a]).values).fit(cov_type="HAC", cov_kwds={"maxlags": 3 * h + 2})
            L.append(f"   encompassing: ¿{b} agrega a {a}? λ {m.params[0]:+.2f} (t {m.tvalues[0]:+.1f}, p {m.pvalues[0]:.3f})")
    # ---- 4) placebo: señal corrida al azar (bloques de años enteros, conserva la autocorrelación)
    L += ["", "4) PLACEBO: la misma regresión con la señal corrida en el tiempo al azar (500 veces, corrimientos de 12 a 60",
          "   meses). p empírico = parte de los placebos con |t| mayor o igual al real. Si la señal anticipa de verdad, p chico."]
    for h in (1, 2, 3):
        z = X[X["h"] == h].dropna(subset=["y", "du_ult", "dev_ult", "sipa_total_ia", "sipa_priv_ia", "emae_ia"]).reset_index(drop=True)
        partes = []
        for col in ("du_ult", "dev_ult", "sipa_total_ia", "sipa_priv_ia", "emae_ia"):
            t_real = abs(hac(z["y"], z[[col]], 3 * h + 2).tvalues[col])
            serie = z.groupby("origen")[col].first()
            ts = []
            for _ in range(500):
                k = int(RNG.integers(12, 61)) * (1 if RNG.random() < 0.5 else -1)
                corr = pd.Series(np.roll(serie.values, k), index=serie.index)
                ts.append(abs(hac(z["y"], z["origen"].map(corr).to_frame(col), 3 * h + 2).tvalues[col]))
            partes.append(f"{col} |t| {t_real:.1f} p {np.mean(np.array(ts) >= t_real):.2f}")
        L.append(f"   h{h}: " + " | ".join(partes))
    # ---- 5) estabilidad
    L += ["", "5) ESTABILIDAD: coeficiente (t robusto) de cada señal sola, por etapa"]
    for h in (1, 2, 3):
        partes = []
        for et, m_ in (("2018-19", lambda o: o <= pd.Period("2020-02", "M")), ("2021-26", lambda o: o >= pd.Period("2021-10", "M"))):
            z = X[(X["h"] == h) & X["origen"].map(m_)].dropna(subset=["y", "du_ult", "sipa_total_ia", "sipa_priv_ia", "emae_ia"])
            r = []
            for col in ("du_ult", "dev_ult", "sipa_total_ia", "sipa_priv_ia", "emae_ia"):
                m = hac(z["y"], z[[col]], 3 * h + 2)
                r.append(f"{col} {m.params[col]:+.2f} ({m.tvalues[col]:+.1f})")
            partes.append(f"{et} n {len(z)}: " + "  ".join(r))
        L.append(f"   h{h}: " + "\n        ".join(partes))
    # ---- sensibilidad: SIPA hasta t−3
    X3, _ = armar(3)
    L += ["", "SENSIBILIDAD: SIPA conocido hasta t−3 (si la Secretaría de Trabajo publica antes que datos.gob.ar)"]
    for h in (1, 2, 3):
        z = X3[X3["h"] == h].dropna(subset=["y", "du_ult", "dev_ult", "sipa_total_ia", "sipa_priv_ia"])
        r = []
        for nm, cols in (("SIPA total", ["sipa_total_ia"]), ("SIPA privados", ["sipa_priv_ia"]),
                         ("EPH + SIPA total", ["du_ult", "dev_ult", "sipa_total_ia"])):
            m = hac(z["y"], z[cols], 3 * h + 2)
            r.append(f"{nm}: R² {m.rsquared:.2f}, t SIPA {m.tvalues[cols[-1]]:+.1f}")
        L.append(f"   h{h}: " + " | ".join(r))
    # ---- 6) ¿el REM ya usa el SIPA?
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[(r["variable_std"] == "desocupacion") & (r["tipo_periodo"] == "trimestre")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["objetivo"] = [objetivo(x) for _, x in g.iterrows()]
    M = X.merge(g[["origen", "objetivo", "mediana"]], on=["origen", "objetivo"])
    M["err_rem"] = M["real"] - M["mediana"]
    L += ["", "6) ¿EL REM YA USA ESTA INFORMACIÓN? Error del REM (real − mediana) sobre cada señal. Si el coeficiente no",
          "   es significativo, el REM ya la incorporó (o no sirve); si lo es, el REM la está dejando pasar."]
    for h in (1, 2, 3):
        z = M[M["h"] == h].dropna(subset=["err_rem", "du_ult", "dev_ult", "sipa_total_ia", "sipa_priv_ia", "emae_ia"])
        if len(z) < 15:
            continue
        r_ = []
        for col in ("du_ult", "dev_ult", "sipa_total_ia", "sipa_priv_ia", "emae_ia"):
            m = hac(z["err_rem"], z[[col]], 3 * h + 2)
            r_.append(f"{col} {m.params[col]:+.2f} (t {m.tvalues[col]:+.1f})")
        L.append(f"   h{h} (n {len(z)}, error medio del REM {z['err_rem'].mean():+.2f}): " + "  ".join(r_))
    # control: el REM pasó de sobreestimar (2023-24) a subestimar (2025-26) justo cuando el SIPA dejó de crecer;
    # si la relación es solo ese cambio de sesgo, dentro de cada etapa desaparece
    L.append("   Control dentro de cada etapa (orígenes hasta 2024 / 2025-26):")
    for h in (1, 2, 3):
        for et, m_ in (("hasta 2024", M["origen"] < pd.Period("2025-01", "M")), ("2025-26", M["origen"] >= pd.Period("2025-01", "M"))):
            z = M[(M["h"] == h) & m_].dropna(subset=["err_rem", "sipa_total_ia", "emae_ia"])
            if len(z) < 8:
                continue
            r_ = [f"{c} {(m := hac(z['err_rem'], z[[c]], 3 * h + 2)).params[c]:+.2f} (t {m.tvalues[c]:+.1f})" for c in ("sipa_total_ia", "emae_ia")]
            L.append(f"   h{h} {et:10} n {len(z):2d}, error medio {z['err_rem'].mean():+.2f}: " + "  ".join(r_))
    texto = "\n".join(L)
    print(texto)
    (C / "informe_sipa.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
