# probar_fiscal_meta.py
# PRUEBA (no entra en la corrida diaria): cuánto creerle a la meta fiscal. Lee datos\replica_fiscal.csv (backtest en tiempo
# real que arma replicar_fiscal.py) y compara contra el resultado real (IMIG, % del PIB):
#   actual  : I = w·sin_meta + (1 − w)·meta, w = k/12 (cumplimiento 100%)
#   1 (λ)   : I = w·sin_meta + (1 − w)·[λ·meta + (1 − λ)·sin_meta]; λ en 0..1 elegido con los años ya cerrados
#   2 (d)   : I = w·sin_meta + (1 − w)·(meta − d); d = desvío promedio (meta − real) de los años ya cerrados
#   3 (REM) : como 1, pero λ = cuánto le cree el REM a la meta (MCO sin constante de REM − sin_meta sobre (1 − w)·(meta − sin_meta))
# Todo en tiempo real: para el relevamiento t solo se usan años con el real publicado (el año a se conoce en marzo de a+1)
# y relevamientos anteriores a t. Regla declarada antes de mirar: gana el menor error absoluto medio contra el real.
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
X = pd.read_csv("datos/replica_fiscal.csv", encoding="utf-8-sig")
X["origen"] = pd.PeriodIndex(X["origen"], freq="M")
X = X.dropna(subset=["meta_pib", "I_sin_meta_pib"]).copy()
X["w"] = X["k"] / 12
GRID = np.linspace(0, 1, 101)


def real_conocido(a, t):
    return a <= t.year - 2 or (a == t.year - 1 and t.month >= 3)


def con_lambda(q, lam):
    return q.w * q.I_sin_meta_pib + (1 - q.w) * (lam * q.meta_pib + (1 - lam) * q.I_sin_meta_pib)


def fila(q, lam=None, d=None):
    if lam is not None:
        return con_lambda(q, lam)
    return q.w * q.I_sin_meta_pib + (1 - q.w) * (q.meta_pib - d)


out = []
for i, r in X.iterrows():
    t = r["origen"]
    tr = X[np.array([real_conocido(a, t) for a in X["anio"]]) & X["real_pib"].notna()]
    o = {"i": i, "lam1": np.nan, "d2": np.nan, "lam3": np.nan}
    if tr["anio"].nunique() >= 2:
        o["lam1"] = GRID[np.argmin([(con_lambda(tr, l) - tr.real_pib).abs().mean() for l in GRID])]
        anual = tr.groupby("anio").apply(lambda q: (q.meta_pib - q.real_pib).mean())
        o["d2"] = anual.mean()
    tr3 = X[(X["origen"] < t) & X["rem_pib"].notna()]
    x3 = (1 - tr3.w) * (tr3.meta_pib - tr3.I_sin_meta_pib)
    if len(tr3) >= 12 and (x3 ** 2).sum() > 0:
        o["lam3"] = float(np.clip((x3 * (tr3.rem_pib - tr3.I_sin_meta_pib)).sum() / (x3 ** 2).sum(), 0, 1))
    out.append(o)
P = pd.DataFrame(out).set_index("i")
X = X.join(P)
X["actual"] = con_lambda(X, 1.0)
X["sin_meta"] = X["I_sin_meta_pib"]
X["op1"] = X.w * X.I_sin_meta_pib + (1 - X.w) * (X.lam1 * X.meta_pib + (1 - X.lam1) * X.I_sin_meta_pib)
X["op2"] = X.w * X.I_sin_meta_pib + (1 - X.w) * (X.meta_pib - X.d2)
X["op3"] = X.w * X.I_sin_meta_pib + (1 - X.w) * (X.lam3 * X.meta_pib + (1 - X.lam3) * X.I_sin_meta_pib)
X["REM"] = X["rem_pib"]

print("Meta vs real por año (% PIB; + = no se cumplió):")
for a, q in X.dropna(subset=["real_pib"]).groupby("anio"):
    print(f"   {a}: meta (últ. conocida) {q.sort_values('origen').meta_pib.iloc[-1]:+.2f}  real {q.real_pib.iloc[0]:+.2f}  "
          f"meta − real {q.sort_values('origen').meta_pib.iloc[-1] - q.real_pib.iloc[0]:+.2f}")

M = ["actual", "sin_meta", "op1", "op2", "op3", "REM"]
E = X.dropna(subset=["real_pib", "op1", "op2", "op3"])
for nom, q in (("todos", E), ("sin 2020", E[E.anio != 2020])):
    print(f"\nError absoluto medio contra el real (% PIB), {nom} — {q.anio.min()}-{q.anio.max()}, n={len(q)}")
    print(f"   {'':14}" + "".join(f"{m:>9}" for m in M))
    for pos, z in q.groupby("pos"):
        print(f"   pos {pos} (n={len(z):3d})  " + "".join(f"{(z[m] - z.real_pib).abs().mean():9.2f}" for m in M))
    print(f"   total         " + "".join(f"{(q[m] - q.real_pib).abs().mean():9.2f}" for m in M))
    print("   por año (pronóstico − real, promedio):")
    for a, z in q.groupby("anio"):
        print(f"     {a}  " + "".join(f"{(z[m] - z.real_pib).mean():+9.2f}" for m in M))

hoy = X[X["origen"] == X["origen"].max()]
print(f"\nParámetros de hoy (relevamiento {X['origen'].max()}): λ1 = {hoy.lam1.iloc[0]:.2f}, d2 = {hoy.d2.iloc[0]:+.2f}, λ3 (REM) = {hoy.lam3.iloc[0]:.2f}")
print("Pronóstico de hoy con cada opción (% PIB):")
for _, r in hoy.iterrows():
    print(f"   {int(r.anio)} (k={int(r.k)}): meta {r.meta_pib:.2f} sin meta {r.I_sin_meta_pib:.2f} | actual {r.actual:.2f}  "
          f"1: {r.op1:.2f}  2: {r.op2:.2f}  3: {r.op3:.2f}  REM {r.REM:.2f}")
