# probar_fiscal_partidas.py
# PRUEBA (no entra en la corrida diaria): resultado primario del SPN "partida por partida", versión reducida.
# Cada mes que falta = mismo mes del año anterior × crecimiento de su base (en pesos, IMIG base caja):
#   IVA, Ganancias, Débitos y créditos y resto de ingresos : (1 + inflación interanual del mes) × (1 + crecimiento real del PIB del año)
#   Seguridad social                                        : (1 + inflación interanual del mes)          (salario real constante)
#   Derechos de exportación                                 : tipo de cambio del mes / el de un año antes (volumen y alícuota constantes)
#   Gasto indexado (jubilaciones, asignaciones, pensiones)  : IPC(m−2) / IPC(m−14)                         (movilidad por IPC con 2 meses de rezago)
#   Resto del gasto (salarios, energía, transporte, capital, provincias, etc.) : (1 + inflación interanual del mes)  (constante en términos reales)
# Insumos macro en tiempo real: en el relevamiento t, IPC y tipo de cambio reales hasta t−1 y, desde ahí, las medianas del REM
# de ese mes (meses que publica + el resto del año completado para que cierre con el anual dic/dic o el promedio de diciembre);
# crecimiento del PIB = mediana del REM para cada año.
# Candidato N (declarado antes de mirar): el modelo actual con "partidas" en lugar de "sin meta":
#   N = k/12·partidas + (1 − k/12)·meta.  Además se informa "partidas" solo (sin meta) y el ajuste que haría falta para la meta.
# Regla (Marcos, 30-oct-2026): tiempo real, relevamientos desde ene-2024, años cerrados (2024 y 2025); gana el menor error
# absoluto medio contra el real (% del PIB). Si N no le gana al actual, queda el actual.
import glob
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

f = max(glob.glob("datos/copias/imig/imig_*.csv"), key=os.path.getmtime)
d = pd.read_csv(f)
d.index = pd.PeriodIndex(pd.to_datetime(d["indice_tiempo"]), freq="M")
ci = list(d.columns[1:d.columns.get_loc("ingresos_capital") + 1])
ing = d[ci].sum(axis=1)
gas = ing - d["resultado_primario"]
IDX = ["jubilaciones_pensiones_contributivas", "asignacion_familiares_hijo", "pensiones_no_contributivas"]
P = pd.DataFrame({
    "iva": d["iva_neto_reintegros"], "gan": d["ganancias"], "dyc": d["debitos_creditos"],
    "ss": d["aportes_contribuciones_seguridad_social"], "dex": d["derechos_exportacion"],
})
P["resto_ing"] = ing - P.sum(axis=1)
P["idx"] = d[IDX].sum(axis=1)
P["resto_gas"] = gas - P["idx"]
ING = ["iva", "gan", "dyc", "ss", "dex", "resto_ing"]
GAS = ["idx", "resto_gas"]

pe = pd.read_parquet("datos/precios_encadenada.parquet")
IPC = pd.Series(pe["indice"].values, index=pd.to_datetime(pe["mes"]).dt.to_period("M"))
b = pd.read_parquet("datos/bcra_series_mensual.parquet")
b = b[(b["serie"] == "tc_mayorista_a3500") & (b["dias"] >= 10)]
TC = pd.Series(b["promedio"].values, index=pd.to_datetime(b["mes"]).dt.to_period("M"))

r = pd.read_parquet("datos/rem_normalizada.parquet")
r["origen"] = pd.to_datetime(r["mes_relevamiento"]).dt.to_period("M")
R = pd.read_csv("datos/replica_fiscal.csv", encoding="utf-8-sig")
R["origen"] = pd.PeriodIndex(R["origen"], freq="M")
ESC = R.groupby("anio")["esc"].last()


def camino(real, ult, rem_mens, anual, es_tasa):
    """Serie mensual: real hasta ult; REM mensual después; huecos completados en forma geométrica para cerrar con el anual.
    es_tasa: rem_mens y anual son variaciones (%) del IPC; si no, niveles (tipo de cambio)."""
    s = real[real.index <= ult].copy()
    for a in (ult.year, ult.year + 1, ult.year + 2):
        fin = pd.Period(f"{a}-12", "M")
        if fin <= ult:
            continue
        meses = pd.period_range(max(ult + 1, pd.Period(f"{a}-01", "M")), fin, freq="M")
        if es_tasa:
            dic0 = s.get(pd.Period(f"{a - 1}-12", "M"))
            conoc = [m for m in meses if m in rem_mens.index]
            fijo = np.prod([1 + rem_mens[m] / 100 for m in conoc])
            falta = [m for m in meses if m not in rem_mens.index]
            if a in anual.index and dic0 is not None and falta:
                ya = s[s.index.year == a]
                base = (ya.iloc[-1] / dic0) if len(ya) else 1.0
                rr = ((1 + anual[a] / 100) / (base * fijo)) ** (1 / len(falta)) - 1
            else:
                rr = rem_mens[conoc[-1]] / 100 if conoc else 0.02
            for m in meses:
                s[m] = s[m - 1] * (1 + (rem_mens[m] / 100 if m in rem_mens.index else rr))
        else:
            ancla = {m: v for m, v in rem_mens.items() if m in meses}
            if a in anual.index:
                ancla.setdefault(fin, anual[a])
            prev = s.index.max()
            for m in sorted(ancla):
                n = (m - prev).n
                g = (ancla[m] / s[prev]) ** (1 / n)
                for j in range(1, n + 1):
                    s[prev + j] = s[prev] * g ** j
                prev = m
            for m in meses:
                if m not in s.index:
                    s[m] = s[m - 1]
    return s.sort_index()


def partidas(t, q):
    """Resultado (millones de $) del año t.year y t.year+1 con info del relevamiento t (q = REM de ese mes)."""
    ult = t - 1
    def rem(var, tipo):
        z = q[(q["variable_std"] == var) & (q["tipo_periodo"] == tipo)]
        if tipo == "fecha":
            return pd.Series(z["mediana"].values, index=pd.to_datetime(z["periodo_original"]).dt.to_period("M")).groupby(level=0).mean()
        z = z.assign(a=pd.to_numeric(z["periodo_original"], errors="coerce")).dropna(subset=["a"])
        return pd.Series(z["mediana"].values, index=z["a"].astype(int)).groupby(level=0).mean()
    ipc = camino(IPC, ult, rem("ipc_general", "fecha"), rem("ipc_general", "anio"), True)
    tc = camino(TC, ult, rem("tipo_cambio", "fecha"), rem("tipo_cambio", "anio"), False)
    gpib = rem("pib", "anio")
    M = P[P.index <= ult].copy()
    for m in pd.period_range(ult + 1, f"{t.year + 1}-12", freq="M"):
        base = M.loc[m - 12]
        pi = ipc[m] / ipc[m - 12]
        g = 1 + gpib.get(m.year, 0) / 100
        M.loc[m] = {"iva": base.iva * pi * g, "gan": base.gan * pi * g, "dyc": base.dyc * pi * g, "resto_ing": base.resto_ing * pi * g,
                    "ss": base.ss * pi, "dex": base.dex * tc[m] / tc[m - 12],
                    "idx": base.idx * ipc[m - 2] / ipc[m - 14], "resto_gas": base.resto_gas * pi}
    res = M[ING].sum(axis=1) - M[GAS].sum(axis=1)
    return {a: res[res.index.year == a].sum() for a in (t.year, t.year + 1)}, M


filas = []
for t in sorted(R["origen"].unique()):
    if t < pd.Period("2024-01", "M"):
        continue
    q = r[r["origen"] == t]
    out, _ = partidas(t, q)
    for _, x in R[R["origen"] == t].iterrows():
        a = int(x["anio"])
        if a in out:
            filas.append({"origen": t, "anio": a, "pos": x["pos"], "k": x["k"], "meta": x["meta_pib"], "real": x["real_pib"],
                          "actual": x["I_pib"], "sin_meta": x["I_sin_meta_pib"], "REM": x["rem_pib"],
                          "partidas": out[a] / 1000 / ESC[a]})
X = pd.DataFrame(filas)
w = X["k"] / 12
X["N"] = np.where(X["meta"].notna(), w * X["partidas"] + (1 - w) * X["meta"], X["partidas"])

M_ = ["actual", "N", "partidas", "sin_meta", "REM"]
E = X.dropna(subset=["real"])
print(f"Error absoluto medio contra el real (% PIB) — relevamientos {E.origen.min()} a {E.origen.max()}, años {sorted(E.anio.unique())}")
print(f"   {'':16}" + "".join(f"{m:>10}" for m in M_))
for pos, z in E.groupby("pos"):
    print(f"   pos {pos} (n={len(z):2d})     " + "".join(f"{(z[m] - z.real).abs().mean():10.2f}" for m in M_))
print(f"   total (n={len(E):2d})     " + "".join(f"{(E[m] - E.real).abs().mean():10.2f}" for m in M_))
print("\nPor año y horizonte (pronóstico − real, promedio de los relevamientos):")
for (a, pos), z in E.groupby(["anio", "pos"]):
    print(f"   {a} pos {pos} real {z.real.iloc[0]:+.2f}: " + "  ".join(f"{m} {(z[m] - z.real).mean():+.2f}" for m in M_))
print("\nRelevamiento por relevamiento (% PIB):")
print(E[["origen", "anio", "k", "meta", "real", "actual", "N", "partidas", "REM"]].round(2).to_string(index=False))

# hoy: IMIG hasta el último mes publicado, REM más reciente como insumo macro
ult = P.index.max()
t = ult + 1
q = r[r["origen"] == r["origen"].max()]
out, Mh = partidas(t, q)
mt = pd.read_csv("datos/metas_fiscales.csv", parse_dates=["vigente_desde"])
print(f"\nHOY (IMIG hasta {ult}, REM de {r['origen'].max()} como insumo macro):")
for a, v in out.items():
    meta = mt[mt.anio == a].sort_values("vigente_desde").meta_pct_pib.iloc[-1]
    kk = ult.month if a == ult.year else 0
    p = v / 1000 / ESC[a]
    n_ = kk / 12 * p + (1 - kk / 12) * meta
    rg = Mh.loc[Mh.index.year == a, "resto_gas"].sum() / 1000 / ESC[a]
    print(f"   {a}: partidas {p:.2f}% ({v / 1e6:.1f} billones) | meta {meta:.2f} | N {n_:.2f}% ({n_ * ESC[a] / 1000:.1f} billones) | "
          f"ajuste para la meta {meta - p:+.2f} pp del PIB = {(meta - p) / rg * 100:.1f}% del gasto no indexado")
    tab = (Mh[Mh.index.year == a].sum() / 1000 / ESC[a]).round(2)
    print("      " + "  ".join(f"{c} {tab[c]:.2f}" for c in ING + GAS))

# estado de resultados: 2025 real, 2026 (ene-sep real + oct-dic proyectado), 2027 proyectado
NOM = {"iva": "IVA", "gan": "Ganancias", "dyc": "Débitos y créditos", "ss": "Seguridad social", "dex": "Derechos de exportación",
       "resto_ing": "Resto de ingresos", "idx": "Gasto indexado (jubil., AUH, pensiones)", "resto_gas": "Resto del gasto primario"}
EST = pd.DataFrame({a: Mh[Mh.index.year == a].sum() for a in (2025, 2026, 2027)}) / 1000
EST.loc["ING"] = EST.loc[ING].sum()
EST.loc["GAS"] = EST.loc[GAS].sum()
EST.loc["RES"] = EST.loc["ING"] - EST.loc["GAS"]
print("\nESTADO DE RESULTADOS (miles de millones de $ | % del PIB)")
for c in ING + ["ING"] + GAS + ["GAS", "RES"]:
    n = {"ING": "INGRESOS TOTALES", "GAS": "GASTO PRIMARIO", "RES": "RESULTADO PRIMARIO"}.get(c, NOM.get(c))
    print(f"   {n:42}" + "".join(f"{EST.loc[c, a]:12,.0f}{EST.loc[c, a] / ESC[a]:7.2f}%" for a in EST.columns))
