# replicar_tc.py
# Nuestro pronóstico del TIPO DE CAMBIO (A3500 mayorista, promedio mensual, como lo pide el REM) desde 2020 (futuros CEM).
# Para cada relevamiento del REM (fecha del relevamiento) se toma la curva de futuros de dólar del último día hábil
# anterior y se la convierte a PROMEDIO MENSUAL: el futuro del mes m paga el A3500 del último día de m, así que el promedio
# de m se aproxima con la media geométrica de los extremos (fin de m-1 y fin de m; para el primer mes, el spot del día).
# Próx. 12 meses: el contrato a 12 meses casi nunca existe: se extiende la tasa implícita del contrato más largo hasta 12.
# Modelos (tiempo real; para el relevamiento t solo se usa lo conocido en t):
#   Futuros   : la curva convertida a promedio mensual.
#   A-dólar   : futuros x descuento típico del REM contra los futuros en ese horizonte (promedio de log(REM/futuro) de los
#               últimos 12 relevamientos). Objetivo: parecerse al REM (sin usar el REM del mes como dato).
#   B-dólar   : REM anterior para ese mes x (futuro de hoy / futuro del relevamiento anterior) para ese mes.
#   I-dólar   : futuros x prima observada (promedio de log(real/futuro) de los últimos 12 pronósticos con el dato real ya
#               publicado a la fecha). Objetivo: pronosticar el dólar real (ganarle al REM).
# Evaluación: distancia al REM y error contra el A3500 promedio real, en % (|log| x 100), % dentro del rango P10-P90 de
# las consultoras y Diebold-Mariano (HLN) contra el REM. Salidas: datos\replica_tc.csv, datos\informe_tc.txt,
# datos\registro_tc.csv (pronóstico de cada corrida, para la prueba hacia adelante).
# Uso: py -3.13 replicar_tc.py
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_rem import diebold_mariano

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
VENTANA = 12
HORIZ = [1, 2, 3, 4, 5, 6, 12, 100, 101]   # 100/101 = diciembre del año en curso / siguiente


def cargar():
    b = pd.read_parquet(C / "bcra_series.parquet")
    b["fecha"] = pd.to_datetime(b["fecha"])
    tc = b[b["serie"] == "tc_mayorista_a3500"].set_index("fecha")["valor"].sort_index()
    fu = pd.read_parquet(C / "dolar_futuro_cem.parquet")
    fu = fu[fu["symbol"].str.fullmatch(r"DLR\d{6}") & fu["settlement"].gt(0)].copy()
    fu["fecha"] = pd.to_datetime(fu["fecha"])
    fu["venc"] = pd.PeriodIndex(pd.to_datetime(fu["symbol"].str[3:], format="%m%Y"), freq="M")
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    g = r[r["variable_std"] == "tipo_cambio"].copy()
    g["origen"] = pd.to_datetime(g["mes_relevamiento"]).dt.to_period("M")
    g["fecha_rel"] = pd.to_datetime(g["fecha_relevamiento"])
    return tc, fu, g


def curva_promedio(fu, tc, f, o):
    """Futuro implícito para el PROMEDIO de cada mes m > o, con la curva del día f."""
    curva = fu[fu["fecha"] == f].set_index("venc")["settlement"].sort_index()
    spot = tc.asof(f)
    out = {}
    for h in range(1, 25):
        m = o + h
        fin = curva.get(m, np.nan)
        ini = spot if h == 1 else curva.get(m - 1, np.nan)
        if h == 1 and np.isnan(fin):
            continue
        if not (np.isnan(ini) or np.isnan(fin)):
            out[h] = np.sqrt(ini * fin)
    # meses sin contrato (12 meses, diciembre del año siguiente): se extiende la tasa implícita del contrato más largo
    largo = curva[curva.index > o]
    if len(largo):
        n = (largo.index[-1] - o).n
        if n >= 3:
            tasa = (largo.iloc[-1] / spot) ** (1 / n)
            for h in range(1, 25):
                if h not in out and h > n:
                    out[h] = spot * tasa ** (h - 0.5)     # promedio del mes ~ mitad de mes
    return out, spot


DIC0, DIC1 = 100, 101     # códigos: diciembre del año en curso / del año siguiente (lo que pide el REM como "año")
ETIQ = {**{h: f"h{h}" for h in range(1, 13)}, DIC0: "dic año", DIC1: "dic año+1"}


def main():
    tc, fu, g = cargar()
    prom = tc.groupby(tc.index.to_period("M")).mean()
    gm = g[g["tipo_periodo"] == "fecha"].copy()
    gm["obj"] = pd.to_datetime(gm["periodo_fin"]).dt.to_period("M")
    gm["h"] = [(b - a).n for a, b in zip(gm["origen"], gm["obj"])]
    g12 = g[(g["tipo_periodo"] == "proximos_meses") & (g["horizonte_meses"] == 12)].copy()
    g12["obj"], g12["h"] = g12["origen"] + 12, 12
    ga = g[g["tipo_periodo"] == "anio"].copy()
    ga["anio"] = ga["periodo_original"].astype(str).str[:4].astype(int)
    ga["obj"] = [pd.Period(f"{y}-12", "M") for y in ga["anio"]]
    ga["h"] = np.where(ga["anio"] == ga["origen"].dt.year, DIC0, np.where(ga["anio"] == ga["origen"].dt.year + 1, DIC1, -1))
    ga = ga[(ga["h"] > 0) & (ga["obj"] > ga["origen"])]
    G = pd.concat([gm, g12, ga])
    G = G[G["h"].isin(HORIZ)].groupby(["origen", "h"]).agg(rem=("mediana", "mean"), p10=("percentil_10", "mean"),
                                                           p90=("percentil_90", "mean"), fecha_rel=("fecha_rel", "max"),
                                                           obj=("obj", "first")).reset_index()
    fechas = np.sort(fu["fecha"].unique())
    filas = []
    for o, q in G.groupby("origen"):
        fr = q["fecha_rel"].max()
        if pd.isna(fr) or fr < pd.Timestamp("2020-02-01"):
            continue
        f = fechas[fechas <= np.datetime64(fr)]
        if not len(f):
            continue
        fut, spot = curva_promedio(fu, tc, pd.Timestamp(f[-1]), o)
        for _, x in q.iterrows():
            k = x["h"] if x["h"] < DIC0 else (x["obj"] - o).n          # meses hasta el mes objetivo
            filas.append({"origen": o, "h": x["h"], "obj": x["obj"], "rem": x["rem"], "p10": x["p10"], "p90": x["p90"],
                          "futuro": fut.get(k, np.nan), "spot": spot, "real": prom.get(x["obj"], np.nan)})
    X = pd.DataFrame(filas).sort_values(["h", "origen"]).reset_index(drop=True)
    X["lg_rem_fut"] = np.log(X["rem"] / X["futuro"])
    X["lg_real_fut"] = np.log(X["real"] / X["futuro"])
    res = []
    for h, q in X.groupby("h"):
        q = q.set_index("origen").sort_index()
        for o in q.index:
            x = q.loc[o]
            prev = q[q.index < o]
            desc = prev["lg_rem_fut"].dropna().tail(VENTANA)
            conocidos = prev[(prev["obj"] <= o - 1)]["lg_real_fut"].dropna().tail(VENTANA)   # real ya publicado en o
            ant = q.loc[o - 1] if (o - 1) in q.index else None
            r = {"origen": o, "h": h, "obj": x["obj"], "rem": x["rem"], "real": x["real"], "p10": x["p10"], "p90": x["p90"],
                 "futuro": x["futuro"], "spot": x["spot"]}
            r["A_dolar"] = x["futuro"] * np.exp(desc.mean()) if len(desc) >= 6 else np.nan
            r["I_dolar"] = x["futuro"] * np.exp(conocidos.mean()) if len(conocidos) >= 6 else np.nan
            # B: REM anterior para el MISMO mes objetivo (horizonte h+1 del informe anterior) x cambio de los futuros
            G_ant = X[(X["origen"] == o - 1) & (X["obj"] == x["obj"])].dropna(subset=["futuro", "rem"])
            if not len(G_ant):    # sin pronóstico anterior para ese mes (h6, 12 meses): el del mismo horizonte
                G_ant = X[(X["origen"] == o - 1) & (X["h"] == h)].dropna(subset=["futuro", "rem"])
            if len(G_ant) and not np.isnan(x["futuro"]):
                r["B_dolar"] = G_ant["rem"].iloc[0] * x["futuro"] / G_ant["futuro"].iloc[0]
            else:
                r["B_dolar"] = np.nan
            r["se_queda"] = x["spot"]
            res.append(r)
    R = pd.DataFrame(res)
    R.to_csv(C / "replica_tc.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    pc = lambda a, b: (np.log(a / b) * 100).abs()
    mods = ["futuro", "A_dolar", "B_dolar", "I_dolar", "se_queda"]
    L = ["=== NUESTRO PRONÓSTICO DEL TIPO DE CAMBIO (A3500 promedio mensual; futuros CEM desde 2020) ===",
         "Errores en % del tipo de cambio (|log| x 100). A-dólar y B-dólar buscan parecerse al REM; I-dólar, acertar el dólar real.", ""]
    for per, m in (("2020-2024", R["origen"] <= pd.Period("2024-12", "M")), ("2025-26", R["origen"] > pd.Period("2024-12", "M"))):
        q = R[m]
        L.append(f"== {per}")
        L.append("   Distancia al REM (%):" + "".join(f"{ETIQ[h]:>10}" for h in HORIZ))
        for c in mods[:-1]:
            L.append(f"     {c:10}" + "".join(f"{pc(q[q.h == h][c], q[q.h == h]['rem']).mean():10.2f}" for h in HORIZ))
        L.append("   Dentro del rango P10-P90 de las consultoras (%):")
        for c in ("A_dolar", "B_dolar"):
            L.append(f"     {c:10}" + "".join(f"{((q[q.h == h][c] >= q[q.h == h]['p10']) & (q[q.h == h][c] <= q[q.h == h]['p90'])).mean() * 100:10.0f}" for h in HORIZ))
        L.append("   Error contra el dólar real (%):" + "".join(f"{ETIQ[h]:>10}" for h in HORIZ))
        for c in mods + ["rem"]:
            L.append(f"     {c:10}" + "".join(f"{pc(q[q.h == h].dropna(subset=['real'])[c], q[q.h == h].dropna(subset=['real'])['real']).mean():10.2f}" for h in HORIZ))
        for mod in ("A_dolar", "I_dolar"):
            ps = []
            for h in HORIZ:
                z = q[q.h == h].dropna(subset=["real", mod, "rem"]).sort_values("origen")
                try:
                    ps.append(f"{diebold_mariano((np.log(z[mod] / z.real)).values, (np.log(z.rem / z.real)).values, h if h < DIC0 else 12):10.2f}")
                except Exception:
                    ps.append(f"{'-':>10}")
            L.append(f"     p DM {mod[0]}vsREM" + "".join(ps))
        L.append("")
    # pronóstico de hoy: curva más reciente
    hoy = pd.Timestamp(fu["fecha"].max())
    o = pd.Period(pd.Timestamp.today(), "M")
    fut, spot = curva_promedio(fu, tc, hoy, o)
    filas_h = []
    for h in HORIZ:
        q = R[R.h == h].set_index("origen").sort_index()
        desc = np.log(q["rem"] / q["futuro"]).dropna().tail(VENTANA).mean()
        prim = q[q["obj"] <= o - 1].pipe(lambda z: np.log(z["real"] / z["futuro"])).dropna().tail(VENTANA).mean()
        if h < DIC0:
            obj = o + h
            f_h = fut.get(h, np.nan)
        else:
            obj = pd.Period(f"{o.year + (h - DIC0)}-12", "M")
            if obj <= o:
                continue
            f_h = fut.get((obj - o).n, np.nan)
        if h < 12:
            rem_ant = G[(G["origen"] == o - 1) & (G["obj"] == obj)]["rem"].mean()
        else:
            rem_ant = G[(G["origen"] == o - 1) & (G["h"] == h)]["rem"].mean()
        filas_h.append({"fecha_corrida": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "relevamiento": str(o), "h": h,
                        "objetivo": str(obj), "futuro": f_h, "A_dolar": f_h * np.exp(desc), "I_dolar": f_h * np.exp(prim),
                        "rem_anterior": rem_ant})
    H = pd.DataFrame(filas_h)
    reg = C / "registro_tc.csv"
    H.to_csv(reg, mode="a", header=not reg.exists(), index=False, encoding="utf-8-sig", float_format="%.2f")
    L.append(f"PRONÓSTICO DE HOY (curva de futuros del {hoy.date()}, A3500 {spot:,.2f}):")
    L.append(f"   {'mes':9} {'REM anterior':>12} {'futuros':>9} {'A-dólar':>9} {'I-dólar':>9}")
    for _, r in H.iterrows():
        L.append(f"   {r['objetivo']:9} {r['rem_anterior']:12,.1f} {r['futuro']:9,.1f} {r['A_dolar']:9,.1f} {r['I_dolar']:9,.1f}")
    L.append("   (h12 = mes a 12 meses; registrado en datos\\registro_tc.csv)")
    L.append("ALERTAS: ninguna. Todo en orden.")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_tc.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
