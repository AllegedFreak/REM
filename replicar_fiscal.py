# replicar_fiscal.py
# Nuestro pronóstico del RESULTADO PRIMARIO del Sector Público Nacional como lo pide el REM: año calendario, miles de
# millones de $ (año en curso y siguiente).
# Fuente del dato: IMIG (Informe Mensual de Ingresos y Gastos del SPN, base caja; Secretaría de Hacienda vía datos.gob.ar,
# dataset 452.3). Cada versión distinta del CSV se guarda en datos\copias\imig\.
# Información en el relevamiento de fin del mes t: meses hasta t-1 (el IMIG del mes m sale a mediados de m+1).
# Método (sin REM): resultado = conocido del año + Σ meses que faltan de (ingresos − gastos primarios), cada uno igual al
# mismo mes del año anterior × (1 + g), con g = crecimiento interanual de los últimos 6 meses conocidos (por separado
# para ingresos y gastos). Año siguiente: los 12 meses del año en curso (pronosticados) × (1 + g).
#   I-fiscal   : (desde 18-oct-2026) mezcla de la META oficial vigente (datos\metas_fiscales.csv: Presupuesto / FMI /
#                anuncios, con la fecha en que se conoció) y la proyección "I sin meta", pesando la meta por la parte
#                del año que falta: I = k/12·(I sin meta) + (1 − k/12)·meta, con k = meses ya publicados del año.
#                Sin meta conocida (p. ej. el año siguiente antes del Presupuesto de septiembre), I = I sin meta.
#   I sin meta : g = inflación interanual del último IPC para ingresos y gastos (crecen con los precios).
#   I g6       : g propio de ingresos y de gastos (últimos 6 meses). Inestable: el resultado es una diferencia chica entre
#                dos flujos grandes (en 2025 erraba 1,5% del PIB); queda para comparar.
#   A-fiscal   : regresión robusta del REM (en % del PIB) sobre I sin meta, I g6, lo conocido del año y la meta
#                (separada en meta·(1 − k/12) y meta·k/12).
# Errores en % del PIB del año (PIB nominal INDEC). Real = suma de los 12 meses del IMIG (versión actual).
# Salidas: datos\replica_fiscal.csv, datos\informe_fiscal.txt, datos\registro_fiscal.csv
import hashlib
import sys
import urllib.request
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
URL = "https://infra.datos.gob.ar/catalog/sspm/dataset/452/distribution/452.3/download/imig-mensual.csv"
COPIAS = C / "copias" / "imig"
MIN = 24
K = 6


CORRIDA = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")   # UNA sola hora por corrida (28-oct-2026: con la hora fila por fila,
# exportaciones e importaciones quedaron en 12:12 y 12:13 y el sello n.º 1 salió sin exportaciones)


def bajar_imig(L):
    COPIAS.mkdir(parents=True, exist_ok=True)
    try:
        datos = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read()
        h = hashlib.md5(datos).hexdigest()[:8]
        if not any(hashlib.md5(p.read_bytes()).hexdigest()[:8] == h for p in COPIAS.glob("imig_*.csv")):
            (COPIAS / f"imig_{datetime.now():%Y%m%d}_{h}.csv").write_bytes(datos)
            L.append(f"IMIG: versión nueva guardada (imig_{datetime.now():%Y%m%d}_{h}.csv)")
    except Exception as e:
        L.append(f"AVISO F01: no se pudo bajar el IMIG ({type(e).__name__}); se usa la última copia guardada")
    ult = max(COPIAS.glob("imig_*.csv"), key=lambda p: p.stat().st_mtime)
    d = pd.read_csv(ult)
    d.index = pd.PeriodIndex(pd.to_datetime(d["indice_tiempo"]), freq="M")
    rp = d["resultado_primario"]
    cols_ing = list(d.columns[1:d.columns.get_loc("ingresos_capital") + 1])
    ing = d[cols_ing].sum(axis=1, min_count=1)
    gas = ing - rp                                           # gasto primario = ingresos − resultado primario
    return pd.DataFrame({"rp": rp, "ing": ing, "gas": gas}).dropna(), ult.name


def g_ia(s, ult, k=K):
    """Crecimiento interanual de los últimos k meses hasta 'ult' (suma contra suma de un año antes)."""
    a = s[(s.index > ult - k) & (s.index <= ult)]
    b = s[(s.index > ult - k - 12) & (s.index <= ult - 12)]
    return a.sum() / b.sum() - 1 if len(a) == k and len(b) == k and b.sum() > 0 else np.nan


def proyectar(M, t, g_ing, g_gas):
    """Resultado del año t.year y t.year+1 con meses conocidos hasta t-1 y el resto proyectado (en millones de $)."""
    ult = t - 1
    y = t.year
    meses = {}
    for m in pd.period_range(f"{y}-01", f"{y + 1}-12", freq="M"):
        if m <= ult and m in M.index:
            meses[m] = (M.loc[m, "ing"], M.loc[m, "gas"])
        else:
            base = meses.get(m - 12) or ((M.loc[m - 12, "ing"], M.loc[m - 12, "gas"]) if (m - 12) in M.index else None)
            if base is None:
                return {}
            meses[m] = (base[0] * (1 + g_ing), base[1] * (1 + g_gas))
    return {a: sum(i - g for m, (i, g) in meses.items() if m.year == a) for a in (y, y + 1)}


def meta_vigente(mt, t, a):
    """Meta de resultado primario (% del PIB) del año a conocida hasta el día 25 del mes t (NaN si no hay)."""
    q = mt[(mt["anio"] == a) & (mt["vigente_desde"] <= t.start_time + pd.Timedelta(days=24))]
    return q.sort_values("vigente_desde")["meta_pct_pib"].iloc[-1] if len(q) else np.nan


def huber(X, y, x0):
    Xc = np.column_stack([np.ones(len(X)), X])
    try:
        b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(Xc, y, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def main():
    L = [f"=== NUESTRO PRONÓSTICO DEL RESULTADO PRIMARIO (SPN, año calendario) | {datetime.now():%Y-%m-%d %H:%M} ==="]
    M, archivo = bajar_imig(L)
    import datos_macro
    try:
        pib = datos_macro.pib_anual()["pib_nominal"]
        pib.index = pib.index.astype(int)
    except Exception as e:
        L.append(f"AVISO F02: no se pudo leer el PIB nominal ({type(e).__name__}); errores solo en miles de millones")
        pib = pd.Series(dtype=float)
    pe = pd.read_parquet(C / "precios_encadenada.parquet")      # IPCBA hasta 2018 + INDEC (para tener 2016-2017)
    ind = pd.Series(pe["indice"].values, index=pd.to_datetime(pe["mes"]).dt.to_period("M"))
    ipc_ia = (ind / ind.shift(12) - 1) * 100
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[(r["variable_std"] == "resultado_primario") & (r["tipo_periodo"] == "anio")].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["anio"] = pd.to_numeric(g["periodo_original"], errors="coerce")
    g = g.dropna(subset=["anio"]).groupby(["origen", "anio"]).agg(rem=("mediana", "mean"), p10=("percentil_10", "mean"),
                                                                  p90=("percentil_90", "mean")).reset_index()
    mt = pd.read_csv(C / "metas_fiscales.csv", parse_dates=["vigente_desde"])
    real = M["rp"].groupby(M.index.year).agg(["sum", "count"])
    real = real.loc[real["count"] == 12, "sum"]
    # escala: PIB del año (si todavía no está, el último × crecimiento nominal de los ingresos)
    def escala(a):
        if a in pib.index and pd.notna(pib.get(a)):
            return pib[a]
        u = pib.dropna()
        if u.empty:
            return np.nan
        g12 = g_ia(M["ing"], M.index.max(), 12)
        return u.iloc[-1] * (1 + g12) ** (a - u.index[-1])
    filas = []
    for t in sorted(g["origen"].unique()):
        ult = t - 1
        if ult not in M.index:
            continue
        gi, gg = g_ia(M["ing"], ult), g_ia(M["gas"], ult)
        p = ipc_ia.get(ult, np.nan) / 100
        cam = {"I_sin_meta": proyectar(M, t, p, p), "I_g6": proyectar(M, t, gi, gg)}
        conocido = M.loc[(M.index.year == t.year) & (M.index <= ult), "rp"].sum()
        for _, x in g[g["origen"] == t].iterrows():
            a = int(x["anio"])
            esc = escala(a) / 100                               # 1% del PIB, en millones
            f = {"origen": t, "anio": a, "pos": a - t.year, "rem": x["rem"], "p10": x["p10"], "p90": x["p90"],
                 "real": real.get(a, np.nan) / 1000, "esc": esc / 1000, "conocido": conocido / 1000 if a == t.year else 0.0,
                 "g_ing": gi * 100, "g_gas": gg * 100, "meta_pib": meta_vigente(mt, t, a),
                 "k": (t.month - 1) if a == t.year else 0}
            for k, c in cam.items():
                f[k] = c.get(a, np.nan) / 1000                  # miles de millones
            filas.append(f)
    X = pd.DataFrame(filas)
    for c in ("rem", "p10", "p90", "real", "I_sin_meta", "I_g6", "conocido"):
        X[c + "_pib"] = X[c] / X["esc"]
    w = X["k"] / 12
    X["I_pib"] = np.where(X["meta_pib"].notna(), w * X["I_sin_meta_pib"] + (1 - w) * X["meta_pib"], X["I_sin_meta_pib"])
    X["I"] = X["I_pib"] * X["esc"]
    X["mt_ini"] = (X["meta_pib"] * (1 - w)).fillna(0)
    X["mt_fin"] = (X["meta_pib"] * w).fillna(0)
    X["hay_meta"] = X["meta_pib"].notna().astype(float)
    COLS = ["I_sin_meta_pib", "I_g6_pib", "conocido_pib", "mt_ini", "mt_fin", "hay_meta"]
    X["A_pib"] = np.nan
    for pos, q in X.groupby("pos"):
        for i, row in q.iterrows():
            tr = q[q["origen"] < row["origen"]].dropna(subset=COLS + ["rem_pib"])
            if len(tr) >= MIN and row[COLS].notna().all():
                X.loc[i, "A_pib"] = huber(tr[COLS].values, tr["rem_pib"].values, row[COLS].values.astype(float))
    X["A"] = X["A_pib"] * X["esc"]
    X.to_csv(C / "replica_fiscal.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    L += [f"Dato: IMIG ({archivo}), último mes {M.index.max()}. Errores en puntos del PIB del año. Fuera de muestra, tiempo real.", ""]
    etq = {0: "año en curso", 1: "año siguiente"}
    for per, m in (("2016-2024", X["origen"] <= pd.Period("2024-12", "M")), ("2025-26", X["origen"] > pd.Period("2024-12", "M"))):
        q = X[m].dropna(subset=["A_pib"])
        L.append(f"== {per}")
        L.append(f"   {'':14}{'distancia al REM':>26} | {'error contra el dato real':>40} | {'dentro P10-P90':>16}")
        L.append(f"   {'':14}{'I':>8}{'sin meta':>9}{'A':>8}{'n':>5} | {'I':>8}{'sin meta':>9}{'A':>8}{'REM':>8}{'n':>5}{'p I-REM':>9} | {'I':>7}{'A':>7}")
        for pos in (0, 1):
            z = q[q["pos"] == pos]
            zr = z.dropna(subset=["real_pib"])
            if not len(z):
                continue
            d = lambda c, ref, zz: (zz[c + "_pib"] - zz[ref + "_pib"]).abs().mean() if c != "A" else (zz["A_pib"] - zz[ref + "_pib"]).abs().mean()
            try:
                p = diebold_mariano((zr.I_pib - zr.real_pib).values, (zr.rem_pib - zr.real_pib).values, 12) if len(zr) > 6 else np.nan
            except Exception:
                p = np.nan
            cob = lambda c: ((z[c] >= z.p10) & (z[c] <= z.p90)).mean() * 100
            L.append(f"   {etq[pos]:14}{d('I', 'rem', z):8.2f}{d('I_sin_meta', 'rem', z):9.2f}{d('A', 'rem', z):8.2f}{len(z):5d} | "
                     f"{d('I', 'real', zr):8.2f}{d('I_sin_meta', 'real', zr):9.2f}{d('A', 'real', zr):8.2f}{d('rem', 'real', zr):8.2f}"
                     f"{len(zr):5d}{p:9.2f} | {cob('I'):6.0f}%{cob('A'):6.0f}%")
        L.append("")
    # por año: error del primer y último relevamiento del año en curso
    L.append("Año en curso, error contra el real (% PIB) según el mes del relevamiento (promedio de los años con dato):")
    z = X[(X["pos"] == 0)].dropna(subset=["real_pib"])
    z["mes"] = [o.month for o in z["origen"]]
    t_ = z.groupby("mes").apply(lambda q: pd.Series({"I": (q.I_pib - q.real_pib).abs().mean(), "REM": (q.rem_pib - q.real_pib).abs().mean(),
                                                    "n": len(q)}))
    L.append("   mes " + " ".join(f"{m:>6}" for m in t_.index))
    L.append("   I   " + " ".join(f"{v:6.2f}" for v in t_["I"]))
    L.append("   REM " + " ".join(f"{v:6.2f}" for v in t_["REM"]))
    L.append("")
    L.append("Error por año (año en curso, promedio de los relevamientos del año, % PIB; + = pronóstico mayor al real):")
    for a, q in z.groupby("anio"):
        L.append(f"   {a}: real {q.real_pib.iloc[0]:+.2f}% | I {(q.I_pib - q.real_pib).mean():+.2f} | REM {(q.rem_pib - q.real_pib).mean():+.2f}")
    L.append("")
    # hoy
    t = pd.Period(pd.Timestamp.today(), "M")
    ult = M.index.max()
    gi, gg = g_ia(M["ing"], ult), g_ia(M["gas"], ult)
    tt = ult + 1                                              # proyecta con lo publicado hasta hoy
    p = ipc_ia.get(ult, np.nan) / 100
    c_i = proyectar(M, tt, p, p)
    c_p = proyectar(M, tt, gi, gg)
    ra = g[g["origen"] == g["origen"].max()].set_index("anio")["rem"]
    L.append(f"PRONÓSTICO DE HOY (IMIG hasta {ult}; ingresos {gi * 100:+.1f}% y gastos {gg * 100:+.1f}% interanual, últimos {K} meses; IPC {p * 100:.1f}% ia; "
             f"REM de referencia: {g['origen'].max()}):")
    L.append(f"   {'año':6}{'REM ant.':>10}{'I':>10}{'sin meta':>10}{'meta':>8}{'(% PIB) REM':>13}{'I':>8}   miles de millones de $")
    reg = []
    for a in sorted(c_i):
        esc = escala(a) / 100 / 1000
        mv = meta_vigente(mt, tt, a)                         # la meta conocida HOY (antes usaba la fecha del último REM; 30-oct-2026)
        kk = ult.month if a == ult.year else 0              # meses publicados del año
        sin_meta = c_i[a]
        if pd.notna(mv):
            c_i[a] = (kk / 12 * sin_meta / 1000 / esc + (1 - kk / 12) * mv) * esc * 1000
        L.append(f"   {a:<6}{ra.get(a, np.nan):10.0f}{c_i[a] / 1000:10.0f}{sin_meta / 1000:10.0f}{mv:8.2f}"
                 f"{ra.get(a, np.nan) / esc:13.2f}{c_i[a] / 1000 / esc:8.2f}")
        reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t), "imig_hasta": str(ult),
                    "anio": a, "I": c_i[a] / 1000, "I_sin_meta": sin_meta / 1000, "meta_pct_pib": mv, "rem_anterior": ra.get(a, np.nan)})
    pr = C / "registro_fiscal.csv"
    nuevo = pd.DataFrame(reg)
    if pr.exists():                                       # control: no mezclar columnas distintas bajo un mismo encabezado
        cab = pd.read_csv(pr, nrows=0, encoding="utf-8-sig").columns.tolist()
        if cab != nuevo.columns.tolist():
            pr = C / f"registro_fiscal_{datetime.now():%Y%m%d}.csv"
            L.append(f"AVISO F03: cambiaron las columnas del registro; esta corrida se guarda en {pr.name}")
    nuevo.to_csv(pr, mode="a", header=not pr.exists(), index=False, encoding="utf-8-sig", float_format="%.1f")
    avisos = [x for x in L if x.startswith("AVISO")]
    L.append("ALERTAS: " + ("; ".join(avisos) if avisos else "ninguna. Todo en orden."))
    texto = "\n".join(L)
    print(texto)
    (C / "informe_fiscal.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
