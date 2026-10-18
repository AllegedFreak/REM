# nowcast_emae.py
# Estima el EMAE del mes que todavía no publicó el INDEC (para el relevamiento del REM de fin del mes t, el EMAE llega a t-2;
# acá se estima t-1) con indicadores que a fin del mes t ya salieron para t-1:
#   - importaciones totales en USD (INDEC, ~día 20 del mes siguiente)
#   - producción de acero crudo (Cámara Argentina del Acero, a principio del mes siguiente)
#   - recaudación de IVA DGI deflactada por el IPC (ARCA, día 1 del mes siguiente; refleja sobre todo la actividad del mes
#     anterior, porque se paga la declaración del mes previo)
# Método: regresión robusta (Huber) de la variación interanual del EMAE original del mes m contra las interanuales de esos
# indicadores en m y la del EMAE en m-1; ventana acumulada, estimada solo con meses anteriores a m (tiempo real).
# El interanual estimado se pasa a desestacionalizado con el factor estacional del mismo mes del año anterior
# (original/desest de m-12).
# Salida: datos\emae_nowcast.parquet (mes, emae_desest_nowcast, yoy_nowcast, yoy_real) e informe en pantalla.
# ADVERTENCIA: el EMAE del pasado es la versión actual (pudo revisarse); los indicadores casi no se revisan.
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
IDS = {"impo": "datosgob_74.3_IIT_0_M_25", "acero": "datosgob_359.3_ACERO_CRUDUDO__11", "iva": "datosgob_142.3_IVA_D_2001_M_7"}
MIN = 36
DESDE = pd.Period("2014-01", "M")


def cargar():
    h = pd.read_parquet(C / "series_historicas.parquet", filters=[("id", "in", list(IDS.values()))])
    h["mes"] = pd.PeriodIndex(pd.to_datetime(h["fecha"]), freq="M")
    S = h.pivot_table(index="mes", columns="id", values="valor").rename(columns={v: k for k, v in IDS.items()})
    p = pd.read_parquet(C / "precios_encadenada.parquet")
    S["ipc"] = pd.Series(p["indice"].values, index=pd.PeriodIndex(pd.to_datetime(p["mes"]), freq="M"))
    E = pd.read_parquet(C / "emae.parquet")
    E.index = pd.PeriodIndex(E["mes"], freq="M")
    S = S.join(E[["original", "desest"]], how="outer").sort_index()
    S["iva_real"] = S["iva"] / S["ipc"]
    yoy = lambda c: (np.log(S[c]) - np.log(S[c].shift(12))) * 100
    D = pd.DataFrame({"y": yoy("original"), "impo": yoy("impo"), "acero": yoy("acero"), "iva": yoy("iva_real")})
    D["y1"] = D["y"].shift(1)
    D["sf12"] = (S["original"] / S["desest"]).shift(12)
    D["orig12"] = S["original"].shift(12)
    return D, S


COLS = ["y1", "impo", "acero", "iva"]


def ajustar(tr, x0, cols):
    X = np.column_stack([np.ones(len(tr)), tr[cols].values])
    try:
        b = sm.RLM(tr["y"].values, X, M=sm.robust.norms.HuberT()).fit().params
    except Exception:
        b = np.linalg.lstsq(X, tr["y"].values, rcond=None)[0]
    return float(np.r_[1, x0] @ b)


def nowcast(D, m, cols=COLS):
    """Interanual estimado del EMAE del mes m usando solo meses < m para estimar."""
    tr = D[(D.index < m) & (D.index >= DESDE)].dropna(subset=["y"] + cols)
    x = D.loc[m, cols] if m in D.index else None
    if x is None or x.isna().any() or len(tr) < MIN:
        return np.nan
    return ajustar(tr, x.values.astype(float), cols)


def a_desest(D, m, yoy):
    return D.loc[m, "orig12"] * np.exp(yoy / 100) / D.loc[m, "sf12"]


def main():
    D, S = cargar()
    filas = []
    for m in D.index[D.index >= DESDE + MIN]:
        f = {"mes": m, "yoy_real": D.loc[m, "y"]}
        for nombre, cols in (("todo", COLS), ("solo_y1", ["y1"]), ("sin_iva", ["y1", "impo", "acero"])):
            f[f"yoy_{nombre}"] = nowcast(D, m, cols)
        f["emae_desest_nowcast"] = a_desest(D, m, f["yoy_todo"]) if pd.notna(f["yoy_todo"]) else np.nan
        f["desest_real"] = S["desest"].get(m, np.nan)
        filas.append(f)
    R = pd.DataFrame(filas)
    out = R[["mes", "emae_desest_nowcast", "yoy_todo", "yoy_real"]].rename(columns={"yoy_todo": "yoy_nowcast"}).copy()
    out["mes"] = out["mes"].astype(str)
    out.to_parquet(C / "emae_nowcast.parquet", index=False)
    # evaluación: error del interanual y de la variación mensual desestacionalizada (contra "se queda" = repetir t-2)
    R["prev"] = [S["desest"].get(m - 1, np.nan) for m in R["mes"]]
    R["mm_real"] = (R["desest_real"] / R["prev"] - 1) * 100
    R["mm_nc"] = (R["emae_desest_nowcast"] / R["prev"] - 1) * 100
    z = R.dropna(subset=["yoy_real", "yoy_todo", "yoy_solo_y1", "yoy_sin_iva", "mm_real", "mm_nc"])
    print("=== NOWCAST DEL EMAE DEL MES QUE FALTA (fuera de muestra, tiempo real) ===")
    for per, q in (("2017-2024", z[z.mes <= pd.Period("2024-12", "M")]), ("2025-26", z[z.mes > pd.Period("2024-12", "M")])):
        e = lambda c: (q[c] - q["yoy_real"]).abs().mean()
        print(f"{per} (n={len(q)}): error interanual  solo inercia {e('yoy_solo_y1'):.2f} | +impo+acero {e('yoy_sin_iva'):.2f} | "
              f"+IVA {e('yoy_todo'):.2f} pp")
        print(f"          variación mensual s.e.: error nowcast {(q.mm_nc - q.mm_real).abs().mean():.2f} | "
              f"'se queda' (0%) {q.mm_real.abs().mean():.2f} pp")
    ult = R.dropna(subset=["yoy_todo"]).iloc[-3:]
    print("Últimos nowcasts:")
    for _, x in ult.iterrows():
        print(f"   {x.mes}: interanual {x.yoy_todo:+.2f}% (real {x.yoy_real:+.2f})  desest {x.emae_desest_nowcast:.1f} (real {x.desest_real:.1f})")


if __name__ == "__main__":
    main()
