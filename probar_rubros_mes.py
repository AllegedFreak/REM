# probar_rubros_mes.py - (26-nov-2026, pedido de Marcos) Inflación del mes en curso (G m0): ¿mejora si se suman, de a uno,
# el termómetro del súper (SEPA), frutas y verduras del Mercado Central y la hacienda de Cañuelas (MAG)?
# Solo análisis: no cambia el modelo ni el registro.
# Todo con lo que se conoce al sellar (días 1 a 25 del mes) y en tiempo real (cada mes se estima con los meses anteriores
# cuyo IPC ya estaba publicado: objetivo <= t-1).
#   Súper  : C2 = G + w·(súper − G), w entre 0 y 1 estimado en tiempo real (como replicar_general.py, en observación).
#   Mercado Central: IPC GBA verduras (01.1.7) y frutas (01.1.6) = a + b·mayorista hasta el 25 (MCO, 2017-21 + 2026 ya publicado);
#                    corrección = peso INDEC de la clase × (pronóstico del rubro − pronóstico general).
#   Cañuelas: IPC GBA carnes (01.1.2) = a + b·novillito+vaquillona hasta el 25 + c·hacienda del mes anterior (MCO desde 2017);
#             corrección = peso INDEC de carnes × (pronóstico del rubro − pronóstico general).
# Pesos: ponderaciones oficiales del IPC (ENGHo 2004/05), nacionales. El súper ya incluye carnes y frescos: al sumarlos
# encima hay algo de doble conteo (se informa igual, es lo que pidió Marcos).
# Uso: py -3.13 probar_rubros_mes.py [ruta mag_precios.parquet alternativa]
# Salida: datos\informe_rubros_mes.txt y datos\probar_rubros_mes.csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_rem import diebold_mariano
from validar_mercado_central import CANASTAS

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
MIN_SUPER = 8


def lv(s):
    return np.log(s).diff() * 100


def ipc_clase(hs, clase):
    s = hs[hs["id"] == f"indec_ipcap_gba_{clase}"]
    return lv(pd.Series(s["valor"].values, index=pd.to_datetime(s["fecha"]).dt.to_period("M")).groupby(level=0).last().sort_index())


def mco_tiempo_real(y, X, t, desde=None):
    """Pronóstico de y en t con MCO estimado con los meses <= t-2 (IPC de t-1 publicado ~día 12 de t... se usa <= t-1)."""
    d = pd.concat([y.rename("y"), X], axis=1).dropna()
    tr = d[d.index <= t - 1]
    if desde is not None:
        tr = tr[tr.index >= desde]
    if len(tr) < 12 or t not in X.index or X.loc[t].isna().any():
        return np.nan
    A = np.column_stack([np.ones(len(tr)), tr[X.columns].values])
    b = np.linalg.lstsq(A, tr["y"].values, rcond=None)[0]
    return float(np.r_[1, X.loc[t].values] @ b)


def peso_nacional(clase):
    pw = pd.read_csv(C / "ponderaciones_ipc.csv", encoding="utf-8-sig", dtype={"codigo": str}).set_index("codigo")
    return float(pw.loc[clase, "Nacional"])


def prono_carnes(hasta, ruta_mag=None, hs=None):
    """Pronóstico en tiempo real de la carne del IPC GBA (01.1.2) para cada mes hasta `hasta` (Period), con la hacienda de
    Cañuelas (novillito + vaquillona hasta el día 25 y el mes anterior completo). Devuelve (pronóstico, IPC carnes, hacienda 25)."""
    ruta_mag = ruta_mag or C / "mag_precios.parquet"
    if hs is None:
        hs = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    a = pd.read_parquet(ruta_mag)
    a["mes"] = pd.PeriodIndex(a["mes"], freq="M")
    a = a[a["categoria"].isin(["NOVILLITOS", "VAQUILLONAS"])]
    pond = lambda q: (q["promedio"] * q["kgs"]).sum() / q["kgs"].sum()
    P = {tr: a[a["tramo"] == tr].groupby("mes").apply(pond) for tr in ("mes", "hasta25")}
    idx = pd.period_range(P["mes"].index.min(), max(P["hasta25"].index.max(), hasta), freq="M")
    pm, p25 = P["mes"].reindex(idx), P["hasta25"].reindex(idx)
    # h1 de t = mes t-1 completo (ya cerrado al sellar); el "mes completo" de t no se usa
    Xc = pd.DataFrame({"h25": (np.log(p25) - np.log(pm.shift(1))) * 100, "h1": lv(pm).shift(1)})
    yc = ipc_clase(hs, "01.1.2")
    meses = idx[idx >= pd.Period("2018-01", "M")]
    return pd.Series({t: mco_tiempo_real(yc, Xc, t) for t in meses}), yc, Xc["h25"]


def main():
    ruta_mag = Path(sys.argv[1]) if len(sys.argv) > 1 else C / "mag_precios.parquet"
    pw = pd.read_csv(C / "ponderaciones_ipc.csv", encoding="utf-8-sig", dtype={"codigo": str}).set_index("codigo")
    hs = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])

    # ---- pesos
    cl = pd.read_parquet(C / "sepa_indice_mensual_clases.parquet")
    clases_super = sorted(cl["clase"].unique())
    peso = {k: float(pw.loc[k, "Nacional"]) for k in ("01.1.2", "01.1.6", "01.1.7")}
    peso_gba = {k: float(pw.loc[k, "GBA"]) for k in ("01.1.2", "01.1.6", "01.1.7")}
    peso_super = float(pw.loc[[c for c in clases_super if c in pw.index], "Nacional"].sum())
    peso_super_gba = float(pw.loc[[c for c in clases_super if c in pw.index], "GBA"].sum())

    # ---- G del mes en curso
    g = pd.read_csv(C / "replica_general.csv", encoding="utf-8-sig")
    g = g[g["h"] == 0].copy()
    g["t"] = pd.PeriodIndex(g["obj"], freq="M")
    g = g.set_index("t").sort_index()[["G", "rem", "real"]]

    # ---- súper
    s = pd.read_parquet(C / "sepa_indice_mensual.parquet")
    s = s[s["region"] == "Nacional"]
    g["super"] = pd.Series(s["variacion_25_pct"].values, index=pd.PeriodIndex(s["mes"], freq="M")).reindex(g.index)

    # ---- Mercado Central: canasta hasta el 25 contra el mes anterior completo
    m = pd.read_parquet(C / "mercado_central.parquet")
    m = m[m["promedio_especie"] & (m["modal_kg"] > 0)].copy()
    m["fecha"] = pd.to_datetime(m["fecha"])
    m["mes"] = m["fecha"].dt.to_period("M")
    nowfv = {}
    for nom, cfg in CANASTAS.items():
        x = m[(m["tipo"] == cfg["tipo"]) & m["especie"].isin(cfg["productos"])]
        mens = x.groupby(["especie", "mes"])["modal_kg"].mean().unstack(0)
        mens25 = x[x["fecha"].dt.day <= 25].groupby(["especie", "mes"])["modal_kg"].mean().unstack(0)
        idx = pd.period_range(mens.index.min(), max(mens.index.max(), g.index.max()), freq="M")
        mens, mens25 = mens.reindex(idx), mens25.reindex(idx)
        x25 = ((np.log(mens25) - np.log(mens.shift(1))).mean(axis=1, skipna=False) * 100).rename("x25")
        y = ipc_clase(hs, cfg["clase"])
        g[f"mc_{nom}"] = x25.reindex(g.index)
        nowfv[nom] = pd.Series({t: mco_tiempo_real(y, x25.to_frame(), t) for t in g.index})
        g[f"ipc_{nom}"] = y.reindex(g.index)
    g["prono_verduras"], g["prono_frutas"] = nowfv["verduras"], nowfv["frutas"]

    # ---- Cañuelas: novillitos + vaquillonas
    pc, yc, h25 = prono_carnes(g.index.max(), ruta_mag, hs)
    g["mag_25"] = h25.reindex(g.index)
    g["prono_carnes"] = pc.reindex(g.index)
    g["ipc_carnes"] = yc.reindex(g.index)

    # ---- combinaciones
    g["w_super"] = np.nan
    g["G+súper"] = np.nan
    for t in g.index:
        tr = g[(g.index <= t - 1)].dropna(subset=["real", "super", "G"])
        if len(tr) >= MIN_SUPER and not np.isnan(g.loc[t, "super"]):
            y = (tr["real"] - tr["G"]).values
            d = (tr["super"] - tr["G"]).values
            w = float(np.clip(d @ y / (d @ d), 0, 1)) if d @ d > 0 else 0.0
            g.loc[t, "w_super"] = w
            g.loc[t, "G+súper"] = g.loc[t, "G"] + w * (g.loc[t, "super"] - g.loc[t, "G"])

    def suma_mc(base):
        return (base + peso["01.1.7"] * (g["prono_verduras"] - base) + peso["01.1.6"] * (g["prono_frutas"] - base))

    def suma_carne(base):
        return base + peso["01.1.2"] * (g["prono_carnes"] - base)

    g["G+MC"] = suma_mc(g["G"])
    g["G+carne"] = suma_carne(g["G"])
    g["G+súper+MC"] = suma_mc(g["G+súper"])
    g["G+súper+MC+carne"] = suma_carne(g["G+súper+MC"])
    g["G+súper+carne"] = suma_carne(g["G+súper"])
    out = g.copy()
    out.index = out.index.astype(str)
    out.to_csv(C / "probar_rubros_mes.csv", encoding="utf-8-sig", float_format="%.4f", index_label="mes")

    # ---- informe
    L = [f"=== MES EN CURSO: G + SÚPER + MERCADO CENTRAL + CAÑUELAS | {pd.Timestamp.now():%Y-%m-%d %H:%M} ===",
         f"Datos de Cañuelas: {ruta_mag}", "",
         "PESOS EN EL IPC (ponderaciones oficiales INDEC, ENGHo 2004/05)        Nacional     GBA",
         f"   Clases que cubre la canasta del súper ({len(clases_super)} clases)        {peso_super*100:6.1f}%  {peso_super_gba*100:6.1f}%",
         f"   Carnes y derivados (01.1.2)                             {peso['01.1.2']*100:6.1f}%  {peso_gba['01.1.2']*100:6.1f}%",
         f"   Verduras, tubérculos y legumbres (01.1.7)               {peso['01.1.7']*100:6.1f}%  {peso_gba['01.1.7']*100:6.1f}%",
         f"   Frutas (01.1.6)                                         {peso['01.1.6']*100:6.1f}%  {peso_gba['01.1.6']*100:6.1f}%",
         "   Clases del súper: " + ", ".join(clases_super), ""]

    def tabla(nombre, q, cols):
        L.append(f"{nombre}: {len(q)} meses ({q.index.min()} a {q.index.max()})")
        L.append(f"   {'':22}{'error':>7}{'sesgo':>7}{'gana a G':>9}{'p vs G':>8}")
        e0 = (q["G"] - q["real"]).values
        for c in cols:
            e = (q[c] - q["real"]).values
            p = diebold_mariano(e, e0, 1) if c != "G" and len(q) >= 5 else np.nan
            gana = (np.abs(e) < np.abs(e0)).mean() * 100
            L.append(f"   {c:22}{np.abs(e).mean():7.2f}{e.mean():7.2f}" + (f"{gana:8.0f}%" if c != "G" else " " * 9)
                     + (f"{p:8.2f}" if not np.isnan(p) else ""))
        L.append("")

    ev = g[g.index >= pd.Period("2024-01", "M")].dropna(subset=["real"])
    tabla("CADA FUENTE SOLA · súper", ev.dropna(subset=["G+súper"]), ["G", "G+súper", "rem"])
    tabla("CADA FUENTE SOLA · Mercado Central", ev.dropna(subset=["G+MC"]), ["G", "G+MC", "rem"])
    tabla("CADA FUENTE SOLA · Cañuelas (2025-26)", ev[ev.index >= pd.Period("2025-01", "M")].dropna(subset=["G+carne"]), ["G", "G+carne", "rem"])
    q = ev.dropna(subset=["G+súper+MC+carne"])
    tabla("SUMANDO DE A UNA (meses con las tres fuentes)", q, ["G", "G+súper", "G+súper+MC", "G+súper+MC+carne", "rem"])

    L.append("Rubros: pronóstico vs IPC GBA (error absoluto medio, pp; mismos meses que cada fuente sola)")
    for nom, col, real in (("verduras", "prono_verduras", "ipc_verduras"), ("frutas", "prono_frutas", "ipc_frutas"),
                           ("carnes", "prono_carnes", "ipc_carnes")):
        r = g[g.index >= pd.Period("2025-01", "M")].dropna(subset=[col, real])
        L.append(f"   {nom:9} n {len(r):2}  error del rubro {np.abs(r[col] - r[real]).mean():5.2f}  (IPC del rubro promedio {r[real].abs().mean():5.2f})")
    L.append("")
    L.append("Mes por mes (2025-26):")
    L.append(f"   {'mes':8}{'real':>6}{'G':>6}{'súper':>7}{'w':>5}{'G+S':>6}{'+MC':>6}{'+carne':>7}{'G+carne':>8}{'REM':>6}"
             f"{'carnes IPC/prono':>18}{'verd IPC/prono':>16}")
    for t, x in g[g.index >= pd.Period("2025-01", "M")].iterrows():
        f = lambda v, n=6: f"{v:{n}.2f}" if pd.notna(v) else " " * (n - 1) + "—"
        L.append(f"   {str(t):8}{f(x['real'])}{f(x['G'])}{f(x['super'], 7)}{f(x['w_super'], 5)}{f(x['G+súper'])}{f(x['G+súper+MC'])}"
                 f"{f(x['G+súper+MC+carne'], 7)}{f(x['G+carne'], 8)}{f(x['rem'])}   {f(x['ipc_carnes'])} / {f(x['prono_carnes'])}"
                 f"   {f(x['ipc_verduras'])} / {f(x['prono_verduras'])}")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_rubros_mes.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
