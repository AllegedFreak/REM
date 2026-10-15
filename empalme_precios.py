# empalme_precios.py
# 1) Arma la serie de precios encadenada: variación mensual del IPCBA hasta CORTE-1 y del INDEC desde CORTE,
#    con columna fuente. El índice queda en la escala del INDEC (dic-2016 = 100).
#    Guarda datos/precios_encadenada.parquet
# 2) Tests sobre la relación INDEC = a + b x IPCBA (variaciones mensuales, meses con ambos datos):
#    Chow en CORTE, Andrews sup-F, CUSUM y Bai-Perron
# 3) Validación fuera de muestra: calibra en 2019-2022 y predice 2023 en adelante
#    Guarda el informe en datos/informe_empalme.txt
# Uso: py -3.13 empalme_precios.py               -> serie + tests (tarda unos minutos)
#      py -3.13 empalme_precios.py --solo-serie  -> solo rearma la serie (lo usa actualizar_todo.py)
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.diagnostic import breaks_cusumolsresid

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CORTE = pd.Period("2019-01", "M")
CALIBRA = (pd.Period("2019-01", "M"), pd.Period("2022-12", "M"))
PREDICE_DESDE = pd.Period("2023-01", "M")
RECORTE = 0.15          # sup-F y Bai-Perron: cada tramo con al menos 15% de los meses
MAX_QUIEBRES = 5
REPETICIONES = 1999     # bootstrap del sup-F
rng = np.random.default_rng(2026)

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
SALIDA = CARPETA / "precios_encadenada.parquet"
INFORME = CARPETA / "informe_empalme.txt"


def mco(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    return beta, e, float(e @ e)


def f_quiebre(y, X, t):
    """F de Chow con quiebre en la posición t (el tramo 2 empieza en t)."""
    k, n = X.shape[1], len(y)
    ssr_r = mco(y, X)[2]
    ssr_u = mco(y[:t], X[:t])[2] + mco(y[t:], X[t:])[2]
    return ((ssr_r - ssr_u) / k) / (ssr_u / (n - 2 * k))


def sup_f(y, X, h):
    fs = np.array([f_quiebre(y, X, t) for t in range(h, len(y) - h + 1)])
    return fs.max(), h + int(fs.argmax())


def cusum_recursivo(y, X):
    """CUSUM de Brown-Durbin-Evans: residuos recursivos acumulados y bandas al 5%."""
    n, k = X.shape
    w = []
    for t in range(k, n):
        Xt, yt = X[:t], y[:t]
        beta = np.linalg.lstsq(Xt, yt, rcond=None)[0]
        xtx_inv = np.linalg.inv(Xt.T @ Xt)
        w.append((y[t] - X[t] @ beta) / np.sqrt(1 + X[t] @ xtx_inv @ X[t]))
    w = np.array(w)
    W = np.cumsum(w) / w.std(ddof=1)
    r = np.arange(1, n - k + 1)
    banda = 0.948 * np.sqrt(n - k) + 2 * 0.948 * r / np.sqrt(n - k)
    return W, banda


def bai_perron(y, X, h, m_max):
    """Mínimo de la suma de residuos para 0..m_max quiebres (programación dinámica). Elige m por BIC."""
    n, k = X.shape
    ssr = np.full((n + 1, n + 1), np.inf)
    for i in range(n):
        for j in range(i + h, n + 1):
            ssr[i, j] = mco(y[i:j], X[i:j])[2]
    # mejor[m][j] = mínima SSR de y[0:j] con m quiebres; corte[m][j] = dónde empieza el último tramo
    mejor = [ssr[0].copy()]
    cortes = [np.zeros(n + 1, dtype=int)]
    for m in range(1, m_max + 1):
        b, c = np.full(n + 1, np.inf), np.zeros(n + 1, dtype=int)
        for j in range((m + 1) * h, n + 1):
            cand = [mejor[m - 1][i] + ssr[i, j] for i in range(m * h, j - h + 1)]
            if cand:
                i = int(np.argmin(cand))
                b[j], c[j] = cand[i], m * h + i
        mejor.append(b)
        cortes.append(c)
    res = []
    for m in range(m_max + 1):
        if not np.isfinite(mejor[m][n]):
            break
        fechas, j = [], n
        for mm in range(m, 0, -1):
            j = cortes[mm][j]
            fechas.insert(0, j)
        p = (m + 1) * k + m
        res.append({"m": m, "ssr": mejor[m][n], "bic": n * np.log(mejor[m][n] / n) + p * np.log(n), "quiebres": fechas})
    return res


def main():
    solo_serie = "--solo-serie" in sys.argv     # modo rápido para la actualización diaria: arma la serie y no corre los tests
    ipcba = pd.read_parquet(CARPETA / "ipcba.parquet")
    indec = pd.read_parquet(CARPETA / "ipc_indec.parquet")
    ipcba["periodo"] = pd.to_datetime(ipcba["mes"]).dt.to_period("M")
    indec["periodo"] = pd.to_datetime(indec["mes"]).dt.to_period("M")
    d = (ipcba[["periodo", "ipcba_var_mensual"]]
         .merge(indec[["periodo", "ipc_general_indice", "ipc_general_indice_var_mensual"]], on="periodo", how="outer")
         .rename(columns={"ipcba_var_mensual": "var_ipcba", "ipc_general_indice_var_mensual": "var_indec"})
         .sort_values("periodo").reset_index(drop=True))
    ultimo_indec = d.loc[d["var_indec"].notna(), "periodo"].max()
    d = d[d["periodo"] <= ultimo_indec].reset_index(drop=True)

    # ---------- 1) serie encadenada ----------
    antes = d["periodo"] < CORTE
    d["var_mensual"] = np.where(antes, d["var_ipcba"], d["var_indec"])
    d["fuente"] = np.where(antes, "IPCBA", "INDEC")
    vacios = d.index[d["var_mensual"].isna()][1:] if pd.isna(d.loc[0, "var_mensual"]) else d.index[d["var_mensual"].isna()]
    if len(vacios):   # regla de huecos: solo el primer mes puede no tener variación
        raise ValueError(f"faltan variaciones en {', '.join(str(d.loc[i, 'periodo']) for i in vacios)}")
    factor = (1 + d["var_mensual"].fillna(0) / 100).cumprod()   # fillna solo afecta al primer mes (base)
    ancla = d.index[d["periodo"] == CORTE - 1][0]
    d["indice"] = factor / factor[ancla] * d.loc[ancla, "ipc_general_indice"]
    d["mes"] = d["periodo"].dt.to_timestamp(how="end").dt.date
    out = d[["mes", "var_mensual", "indice", "fuente", "var_ipcba", "var_indec"]]
    out.to_parquet(SALIDA, index=False)
    desde_indec = d["periodo"] >= CORTE - 1
    dif_max = (d.loc[desde_indec, "indice"] / d.loc[desde_indec, "ipc_general_indice"] - 1).abs().max()
    if solo_serie:
        texto = (f"=== SERIE DE PRECIOS ENCADENADA ===\nSerie encadenada: datos\\precios_encadenada.parquet, "
                 f"{d['periodo'].min()} a {d['periodo'].max()} ({len(d)} meses). Coincide con el INDEC desde {CORTE - 1} "
                 f"(dif. máx. {dif_max:.1e}).\nALERTAS: ninguna. Todo en orden.")
        print(texto)
        (CARPETA / "informe_empalme_serie.txt").write_text(texto, encoding="utf-8")
        return

    # ---------- 2) tests sobre INDEC = a + b x IPCBA ----------
    r = d.dropna(subset=["var_ipcba", "var_indec"]).reset_index(drop=True)
    y = r["var_indec"].to_numpy()
    X = sm.add_constant(r["var_ipcba"].to_numpy())
    n, k = X.shape
    per = list(r["periodo"])
    h = int(np.ceil(RECORTE * n))
    beta_total, e_total, _ = mco(y, X)

    # Chow en CORTE (clásico y robusto a heterocedasticidad)
    t_corte = per.index(CORTE)
    f_chow = f_quiebre(y, X, t_corte)
    from scipy import stats
    p_chow = 1 - stats.f.cdf(f_chow, k, n - 2 * k)
    dummy = (r["periodo"] >= CORTE).astype(float).to_numpy()
    Xi = np.column_stack([X, dummy, dummy * X[:, 1]])
    rob = sm.OLS(y, Xi).fit(cov_type="HC3").wald_test(np.array([[0, 0, 1, 0], [0, 0, 0, 1]]), scalar=True)
    p_chow_rob = float(rob.pvalue)
    b_pre, _, _ = mco(y[:t_corte], X[:t_corte])
    b_post, _, _ = mco(y[t_corte:], X[t_corte:])

    # Andrews sup-F con p-valor por bootstrap (común y "wild", robusto a varianza cambiante)
    sf, t_sf = sup_f(y, X, h)
    ajuste = X @ beta_total
    boot_iid, boot_wild = [], []
    for _ in range(REPETICIONES):
        boot_iid.append(sup_f(ajuste + rng.choice(e_total, n, replace=True), X, h)[0])
        boot_wild.append(sup_f(ajuste + e_total * rng.choice([-1.0, 1.0], n), X, h)[0])
    p_sf_iid = (1 + sum(b >= sf for b in boot_iid)) / (REPETICIONES + 1)
    p_sf_wild = (1 + sum(b >= sf for b in boot_wild)) / (REPETICIONES + 1)

    # CUSUM (recursivo y de residuos MCO)
    W, banda = cusum_recursivo(y, X)
    fuera = np.where(np.abs(W) > banda)[0]
    cruce = per[k + fuera[0]] if len(fuera) else None
    t_max = int(np.argmax(np.abs(W) / banda))
    stat_ols, p_ols, _ = breaks_cusumolsresid(e_total, ddof=k)

    # Bai-Perron
    bp = bai_perron(y, X, h, MAX_QUIEBRES)
    elegido = min(bp, key=lambda z: z["bic"])
    limites = [0] + elegido["quiebres"] + [n]
    tramos = []
    for i0, i1 in zip(limites[:-1], limites[1:]):
        b, e, _ = mco(y[i0:i1], X[i0:i1])
        tramos.append((per[i0], per[i1 - 1], i1 - i0, b[0], b[1], np.mean(np.abs(y[i0:i1] - X[i0:i1, 1]))))

    # ---------- 3) validación fuera de muestra ----------
    cal = r[(r["periodo"] >= CALIBRA[0]) & (r["periodo"] <= CALIBRA[1])]
    pru = r[r["periodo"] >= PREDICE_DESDE].copy()
    b_cal, _, _ = mco(cal["var_indec"].to_numpy(), sm.add_constant(cal["var_ipcba"].to_numpy()))
    pru["pred"] = b_cal[0] + b_cal[1] * pru["var_ipcba"]
    pru["err_modelo"] = pru["var_indec"] - pru["pred"]
    pru["err_ingenuo"] = pru["var_indec"] - pru["var_ipcba"]   # suponer INDEC = IPCBA
    pru["anio"] = pru["periodo"].dt.year

    def metr(e):
        return np.mean(np.abs(e)), np.sqrt(np.mean(e ** 2)), np.mean(e)

    # ---------- informe ----------
    L = ["=== EMPALME DE PRECIOS IPCBA -> INDEC ===", ""]
    L.append(f"1) Serie encadenada: datos\\precios_encadenada.parquet")
    L.append(f"   {d['periodo'].min()} a {d['periodo'].max()} ({len(d)} meses). IPCBA hasta {CORTE - 1} "
             f"({antes.sum()} meses), INDEC desde {CORTE} ({(~antes).sum()} meses).")
    L.append(f"   Índice en escala INDEC (dic-2016 = 100). Desde {CORTE - 1} coincide con el INDEC (dif. máx. {dif_max:.1e}).")
    L.append("")
    L.append(f"2) Relación INDEC = a + b x IPCBA, variaciones mensuales, {per[0]} a {per[-1]} ({n} meses)")
    L.append(f"   Toda la muestra: a = {beta_total[0]:.3f}, b = {beta_total[1]:.3f}")
    L.append("")
    L.append(f"   {'Test':34} {'Resultado':52} {'p-valor':>9}")
    L.append(f"   {'Chow en ' + str(CORTE):34} {f'F = {f_chow:.2f}':52} {p_chow:9.3f}")
    L.append(f"   {'  (versión robusta, HC3)':34} {'':52} {p_chow_rob:9.3f}")
    L.append(f"   {'  antes de ' + str(CORTE):34} {f'a = {b_pre[0]:.3f}, b = {b_pre[1]:.3f} ({t_corte} meses)':52}")
    L.append(f"   {'  desde ' + str(CORTE):34} {f'a = {b_post[0]:.3f}, b = {b_post[1]:.3f} ({n - t_corte} meses)':52}")
    L.append(f"   {'Andrews sup-F (fecha libre)':34} {f'sup-F = {sf:.2f}, quiebre más probable: {per[t_sf]}':52} {p_sf_iid:9.3f}")
    L.append(f"   {'  (bootstrap wild, robusto)':34} {'':52} {p_sf_wild:9.3f}")
    txt_cusum = f"cruza la banda del 5% en {cruce}" if cruce else f"no cruza la banda del 5% (máx. {np.abs(W[t_max]) / banda[t_max]:.0%} de la banda, {per[k + t_max]})"
    L.append(f"   {'CUSUM recursivo':34} {txt_cusum:52} {'-':>9}")
    L.append(f"   {'CUSUM de residuos MCO':34} {f'estadístico = {stat_ols:.2f}':52} {p_ols:9.3f}")
    L.append(f"   {'Bai-Perron (elige por BIC)':34} {f'{elegido['m']} quiebre(s): ' + (', '.join(str(per[q]) for q in elegido['quiebres']) or 'ninguno'):52} {'-':>9}")
    L.append("")
    L.append("   Bai-Perron, BIC por cantidad de quiebres (menor = mejor):")
    for z in bp:
        L.append(f"     {z['m']} quiebres: BIC {z['bic']:8.2f}   {', '.join(str(per[q]) for q in z['quiebres']) or '-'}")
    L.append("   Tramos elegidos:")
    for p0, p1, nn, a, b, dm in tramos:
        L.append(f"     {p0} a {p1} ({nn:3} meses): a = {a:6.3f}, b = {b:5.3f}, diferencia media |INDEC - IPCBA| = {dm:.2f} pp")
    L.append("")
    L.append(f"3) Fuera de muestra: calibrado en {CALIBRA[0]} a {CALIBRA[1]} ({len(cal)} meses): a = {b_cal[0]:.3f}, b = {b_cal[1]:.3f}")
    L.append(f"   Predicción de {pru['periodo'].min()} a {pru['periodo'].max()} ({len(pru)} meses), errores en pp (INDEC - predicción)")
    L.append(f"   {'período':12} {'meses':>5}  {'modelo: MAE':>11} {'RMSE':>6} {'sesgo':>6}   {'INDEC=IPCBA: MAE':>16} {'RMSE':>6} {'sesgo':>6}")
    grupos = [(str(a), g) for a, g in pru.groupby("anio")] + [("total", pru)]
    for nombre, g in grupos:
        m1, m2 = metr(g["err_modelo"]), metr(g["err_ingenuo"])
        L.append(f"   {nombre:12} {len(g):5}  {m1[0]:11.2f} {m1[1]:6.2f} {m1[2]:6.2f}   {m2[0]:16.2f} {m2[1]:6.2f} {m2[2]:6.2f}")
    peores = pru.reindex(pru["err_modelo"].abs().sort_values(ascending=False).index).head(4)
    L.append("   Meses con mayor error del modelo: " + ", ".join(
        f"{p} ({e:+.2f} pp: INDEC {yv:.1f}%, IPCBA {xv:.1f}%)" for p, e, yv, xv in
        zip(peores["periodo"], peores["err_modelo"], peores["var_indec"], peores["var_ipcba"])))
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
