# probar_comercio_base.py (30-oct-2026; análisis, fuera del circuito diario)
# ¿Mejora el pronóstico de exportaciones e importaciones si el punto de partida deja de ser "el mismo mes del año anterior"
# tal cual y pasa a ser ese mes NORMALIZADO, como hacen las consultoras (nivel desestacionalizado × estacionalidad típica)?
# Caso que lo motiva: feb-2026 fue un mes anormalmente bajo (exportaciones 5.963) y A lo copiaba a feb-2027 (−19% contra
# el REM, que esperaba +23% interanual).
#   sf(mes)     = estacionalidad típica: promedio, en los últimos 5 años completos conocidos en t, de valor del mes / promedio
#                 de su año (normalizado a promedio 1)
#   nivel(b)    = promedio de valor/sf de los meses b-1, b y b+1 (solo meses conocidos en t)
#   base_norm(m)= nivel(m-12) × sf(mes de m)
#   A_norm      = base_norm × exp(Huber_h(g3, g12, último mes [+ precios de granos y aceites en exportaciones]))
#                 la misma regresión de A (replicar_comercio.py), estimada con log(REM / base_norm); ventana acumulada, mín. 24
#   A_atip      = como A, pero el mes base se reemplaza SOLO si es atípico: se aparta más de UMBRAL de su valor normal
#                 (mediana desestacionalizada de los 6 meses que lo rodean × su estacionalidad); los meses normales quedan como en A
#   años        = meses conocidos + A_norm; después de t+6, base normalizada del camino ya armado × la relación de h6
# Se compara contra A (lo de hoy) y contra el REM del mes: error contra el real (100·|log|), distancia al REM, % dentro de
# P10-P90 y Diebold-Mariano (HLN). Informe: datos\informe_comercio_base.txt
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_rem import diebold_mariano
from replicar_comercio import MIN, huber

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
UMBRAL = 0.12   # A_atip: el mes base se normaliza solo si se aparta más de 12% de su valor normal
IDS = {"exportaciones": "datosgob_74.3_IET_0_M_16", "importaciones": "datosgob_74.3_IIT_0_M_25"}


def estacionalidad(s, ult):
    """Factor de cada mes del año: promedio de valor/promedio del año en los últimos 5 años completos conocidos en ult."""
    anios = [a for a in range(ult.year - 6, ult.year + 1) if pd.Period(f"{a}-12", "M") <= ult][-5:]
    r = pd.concat([s[s.index.year == a] / s[s.index.year == a].mean() for a in anios])
    f = r.groupby(r.index.month).mean()
    return f / f.mean()


def base_norm(v, sf, m):
    """Mismo mes del año anterior normalizado: nivel desestacionalizado alrededor de m-12 × estacionalidad de m."""
    b = m - 12
    sa = [v[j] / sf[j.month] for j in (b - 1, b, b + 1) if j in v]
    return np.mean(sa) * sf[m.month] if sa else np.nan


def nivel_vecinos(v, sf, b):
    """Nivel desestacionalizado normal alrededor de b: MEDIANA de los 6 meses que lo rodean (b-3..b+3, sin b), así un solo
    mes raro no mueve la vara de sus vecinos."""
    sa = [v[j] / sf[j.month] for j in range(-3, 4) for j in [b + j] if j != b and j in v]
    return np.median(sa) if len(sa) >= 3 else np.nan


def base_atip(v, sf, m):
    """Mismo mes del año anterior tal cual, salvo que sea atípico (se aparta más de UMBRAL de su nivel normal × su
    estacionalidad): entonces, ese valor normal llevado al mes m."""
    b = m - 12
    if b not in v:
        return np.nan
    nv = nivel_vecinos(v, sf, b)
    if np.isnan(nv) or abs(np.log(v[b] / (nv * sf[b.month]))) <= UMBRAL:
        return v[b]
    return nv * sf[m.month]


def stl(s, ult):
    """STL robusto (período 12) sobre el log de los últimos 15 años conocidos en ult: tendencia, estacionalidad y resto."""
    from statsmodels.tsa.seasonal import STL
    x = np.log(s[(s.index <= ult) & (s.index > ult - 180)])
    r = STL(x.values, period=12, robust=True).fit()
    c = {k: pd.Series(getattr(r, k), index=x.index) for k in ("trend", "seasonal", "resid")}
    c["ult"] = ult
    c["seas_mes"] = c["seasonal"].groupby(c["seasonal"].index.month).last()   # estacionalidad más reciente de cada mes
    return c


def base_stl(v, c, m):
    """Mismo mes del año anterior según STL: tendencia de m-12 × estacionalidad más reciente del mes (sin el resto)."""
    b = m - 12
    if b <= c["ult"]:
        return float(np.exp(c["trend"][b] + c["seas_mes"][m.month]))
    return v.get(b, np.nan)


def base_stl_atip(v, c, m):
    """Mismo mes del año anterior tal cual, salvo que STL lo marque atípico (|resto| > UMBRAL): su valor normal."""
    b = m - 12
    if b <= c["ult"] and abs(c["resid"][b]) > UMBRAL:
        return float(np.exp(c["trend"][b] + c["seasonal"][b]))
    return v.get(b, np.nan)


def main():
    h = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    X = pd.read_csv(C / "replica_comercio.csv", encoding="utf-8-sig")
    X["origen"] = pd.PeriodIndex(X["origen"], freq="M")
    t_hoy = X["origen"].max()
    L = [f"=== EXPORTACIONES E IMPORTACIONES: BASE NORMALIZADA (como las consultoras) | {datetime.now():%Y-%m-%d %H:%M} ===",
         "A = lo de hoy (mismo mes del año anterior tal cual); A_stl = mismo mes según STL robusto (tendencia × estacionalidad);",
         "A_stl_atip = mismo mes tal cual salvo que STL lo marque atípico (resto > 12%). Prueba final, definida antes de mirar (30-oct-2026).",
         "Errores en % (100·|log|), tiempo real. p DM < 0,10 = diferencia significativa.", ""]
    hoy_txt = []
    for var, sid in IDS.items():
        q = h[h["id"] == sid]
        s = pd.Series(q["valor"].values, index=pd.to_datetime(q["fecha"]).dt.to_period("M")).groupby(level=0).last().sort_index()
        M = X[(X["var"] == var) & (X["tipo"] == "mes")].copy()
        M["obj"] = pd.PeriodIndex(M["objetivo"], freq="M")
        cols = ["g3", "g12", "ult_rel"] + (["px_om", "px_gr"] if var == "exportaciones" else [])
        sfs, bn, ba = {}, [], []
        for _, x in M.iterrows():
            t = x["origen"]
            ult = t - 1 if (t - 1) in s.index else s.index.max()
            if t not in sfs:
                sfs[t] = stl(s, ult)
            v = {m: s[m] for m in s.index if m <= ult}
            bn.append(base_stl(v, sfs[t], x["obj"]))
            ba.append(base_stl_atip(v, sfs[t], x["obj"]))
        M["base_norm"], M["base_atip"] = bn, ba
        VARIANTES = (("A_norm", "base_norm", base_stl), ("A_atip", "base_atip", base_stl_atip))   # columnas: A_norm = A_stl, A_atip = A_stl_atip
        for nom, bc, fb in VARIANTES:
            M["y_" + nom] = np.log(M["rem"] / M[bc])
            M[nom] = np.nan
            for hh, g in M.groupby("h"):
                g = g.sort_values("origen")
                for i, row in g.iterrows():
                    tr = g[g["origen"] < row["origen"]].dropna(subset=cols + ["y_" + nom])
                    if len(tr) >= MIN and row[cols].notna().all() and not np.isnan(row[bc]):
                        M.loc[i, nom] = row[bc] * np.exp(huber(tr[cols].values, tr["y_" + nom].values, row[cols].values.astype(float)))
        # años: meses conocidos + A_norm (h0..h6) + después, base normalizada del camino × relación de h6
        Y = X[(X["var"] == var) & (X["tipo"] == "año")].copy()
        for nom, bc, fb in VARIANTES:
            Y[nom] = np.nan
            for t, g in M.groupby("origen"):
                yh = (np.log(g.set_index("h")[nom] / g.set_index("h")[bc])).dropna()
                if len(yh) < 7:
                    continue
                ult = t - 1 if (t - 1) in s.index else s.index.max()
                v = {m: s[m] for m in s.index if m <= ult}
                for m in pd.period_range(ult + 1, pd.Period(f"{t.year + 1}-12", "M"), freq="M"):
                    v[m] = fb(v, sfs[t], m) * np.exp(yh[min(max((m - t).n, 0), 6)])
                for i in Y[Y["origen"] == t].index:
                    a = int(Y.loc[i, "objetivo"])
                    Y.loc[i, nom] = sum(v[m] for m in pd.period_range(f"{a}-01", f"{a}-12", freq="M"))
        L.append(f"##### {var.upper()}")
        e = lambda z, c, ref: (100 * np.log(z[c] / z[ref])).abs().mean()
        for per, mk in (("2023-2024 (incluye la sequía)", lambda o: (o >= pd.Period("2023-01", "M")) & (o <= pd.Period("2024-12", "M"))),
                        ("2025-26", lambda o: o > pd.Period("2024-12", "M"))):
            L.append(f"== {per}")
            L.append(f"   {'':8}{'distancia al REM':>27} | {'error contra el real':>38} | {'P10-P90':>20} | {'p DM contra REM / A':>24}")
            L.append(f"   {'':8}{'A':>9}{'A_stl':>9}{'stl_atip':>9} | {'A':>7}{'A_stl':>8}{'stl_at':>8}{'REM':>7}{'n':>5} | {'A':>6}{'stl':>7}{'s_at':>7} | {'stl':>11}{'stl_atip':>12}")
            for etq, Z, hz in [(f"m{k}", M[M["h"] == k], k) for k in range(7)] + [("año", Y[Y["h"] == 0], 12), ("año+1", Y[Y["h"] == 1], 12)]:
                z = Z[mk(Z["origen"])].dropna(subset=["A", "A_norm", "A_atip", "rem"])
                zr = z.dropna(subset=["real"])
                if len(z) < 4:
                    continue
                cob = lambda c: ((z[c] >= z["p10"]) & (z[c] <= z["p90"])).mean() * 100 if z["p10"].notna().any() else np.nan

                def dm(c, ref):
                    try:
                        return diebold_mariano(np.log(zr[c] / zr.real).values, np.log(zr[ref] / zr.real).values, max(hz, 1)) if len(zr) > 6 else np.nan
                    except Exception:
                        return np.nan
                L.append(f"   {etq:8}{e(z, 'A', 'rem'):9.1f}{e(z, 'A_norm', 'rem'):9.1f}{e(z, 'A_atip', 'rem'):9.1f} | {e(zr, 'A', 'real'):7.1f}"
                         f"{e(zr, 'A_norm', 'real'):8.1f}{e(zr, 'A_atip', 'real'):8.1f}{e(zr, 'rem', 'real'):7.1f}{len(zr):5d} | "
                         f"{cob('A'):5.0f}%{cob('A_norm'):6.0f}%{cob('A_atip'):6.0f}% | {dm('A_norm', 'rem'):5.2f}/{dm('A_norm', 'A'):.2f}"
                         f"{dm('A_atip', 'rem'):7.2f}/{dm('A_atip', 'A'):.2f}")
            L.append("")
        # hoy
        rem_ant = pd.read_parquet(C / "rem_normalizada.parquet")
        rem_ant = rem_ant[(rem_ant["variable_std"] == var)]
        rem_ant = rem_ant[pd.to_datetime(rem_ant["mes_relevamiento"]).dt.to_period("M") == pd.to_datetime(rem_ant["mes_relevamiento"]).dt.to_period("M").max()]
        ra = {(str(pd.to_datetime(x["periodo_fin"]).to_period("M")) if x["tipo_periodo"] == "fecha" else str(x["periodo_original"])): x["mediana"]
              for _, x in rem_ant.iterrows()}
        hoy_txt += [f"{var.upper()} (relevamiento {t_hoy}; REM anterior = mediana de {pd.to_datetime(rem_ant['mes_relevamiento']).max():%Y-%m})",
                    f"   {'período':9}{'año antes':>11}{'REM ant.':>10}{'A hoy':>9}{'A_stl':>9}{'stl_atip':>9}   {'ia REM':>7}{'ia A':>7}{'ia stl':>8}{'ia s_at':>8}"]
        for _, x in M[M["origen"] == t_hoy].sort_values("h").iterrows():
            b = s.get(x["obj"] - 12, np.nan)
            r_ = ra.get(str(x["obj"]), np.nan)
            hoy_txt.append(f"   {str(x['obj']):9}{b:11,.0f}{r_:10,.0f}{x['A']:9,.0f}{x['A_norm']:9,.0f}{x['A_atip']:9,.0f}   "
                           f"{(r_ / b - 1) * 100:+6.1f}%{(x['A'] / b - 1) * 100:+6.1f}%{(x['A_norm'] / b - 1) * 100:+7.1f}%{(x['A_atip'] / b - 1) * 100:+7.1f}%")
        for _, x in Y[Y["origen"] == t_hoy].iterrows():
            a = int(x["objetivo"])
            prev = s[s.index.year == a - 1].sum() if (s.index.year == a - 1).sum() == 12 else np.nan
            hoy_txt.append(f"   {a:<9}{prev:11,.0f}{ra.get(str(a), np.nan):10,.0f}{x['A']:9,.0f}{x['A_norm']:9,.0f}{x['A_atip']:9,.0f}")
        hoy_txt.append("")
    L += ["PRONÓSTICO DE HOY", ""] + hoy_txt
    texto = "\n".join(L)
    print(texto)
    (C / "informe_comercio_base.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
