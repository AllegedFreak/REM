# probar_inflacion_largo.py (30-oct-2026; análisis, fuera del circuito diario)
# Inflación más allá de 12 meses "como el REM". Hoy (grilla_anual en replicar_rem.py) el año siguiente, el año +2 y los
# "próx. 24 meses" suponen que la inflación se queda en la tasa del pronóstico de 12 meses: hoy 26% / 25% / 25% contra el REM
# 21% / 15% / 17%. El REM espera desinflación y nosotros no.
# Definiciones (BCRA, "Consideraciones del REM"): año = dic/dic; próx. 12 meses = inflación de t+1 a t+12; próx. 24 meses =
# inflación del SEGUNDO año (interanual del índice de t+24).
# Prueba (definida antes de mirar resultados): para cada pregunta k y cada índice (general, núcleo),
#   log(1 + REM_k) = Huber( log(1 + I 12m), inflación de los últimos 12 meses publicada (log), mes del relevamiento )
#   I 12m = nuestro pronóstico de la general a 12 meses (tiempo real, replica_rem.csv); ventana acumulada, mínimo 24.
#   Para la núcleo, la inflación de los últimos 12 meses es la núcleo. Se compara con "se mantiene" (lo de hoy, solo general)
#   y con el REM. Criterio: se adopta si se acerca al REM y no empeora contra el dato real donde exista.
# Informe: datos\informe_inflacion_largo.txt
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from replicar_comercio import huber

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
MIN = 24
CORTE = pd.Period("2024-12", "M")


def main():
    O = pd.read_csv(C / "replica_rem.csv", encoding="utf-8-sig")
    O["origen"] = pd.PeriodIndex(O["origen"], freq="M")
    I12 = O[O["H"] == "12m"].set_index("origen")["I"]
    ri = pd.read_csv(C / "registro_inflacion.csv", encoding="utf-8-sig")
    t_hoy = pd.Period(pd.Timestamp.today(), "M")
    hoy12 = ri[(ri["relevamiento"] == str(t_hoy)) & (ri["H"] == "12m")].sort_values("fecha_corrida")["pronostico_inflacion"]
    if len(hoy12):
        I12.loc[t_hoy] = hoy12.iloc[-1]
    ipc = pd.read_parquet(C / "ipc_indec.parquet")
    ipc.index = pd.to_datetime(ipc["mes"]).dt.to_period("M")
    niv = {"general": ipc["ipc_general_indice"], "nucleo": ipc["ipc_nucleo_indice"]}
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    r["origen"] = pd.to_datetime(r["mes_relevamiento"]).dt.to_period("M")
    L = [f"=== INFLACIÓN MÁS ALLÁ DE 12 MESES, COMO EL REM | {datetime.now():%Y-%m-%d %H:%M} ===",
         "nuevo = Huber del REM sobre nuestro 12m, inflación de 12 meses publicada y mes; se_mantiene = lo de hoy (tasa del 12m).",
         "Distancia al REM y error contra el real en puntos porcentuales; tiempo real.", ""]
    for ind, std in (("general", "ipc_general"), ("nucleo", "ipc_nucleo")):
        n = niv[ind].dropna()
        g = r[r["variable_std"] == std]
        filas = []
        for o in sorted(set(g["origen"]) | {t_hoy}):
            if o not in I12.index or (o - 13) not in n.index or (o - 1) not in n.index:
                continue
            q = g[g["origen"] == o]
            base = {"origen": o, "x12": np.log1p(I12[o] / 100) * 100, "inf12": np.log(n[o - 1] / n[o - 13]) * 100, "mes": o.month,
                    "i12": I12[o]}
            preg = {"año+1": (o.year + 1, None), "año+2": (o.year + 2, None), "24m": (None, 24), "12m": (None, 12)}
            for k, (anio, hz) in preg.items():
                if ind == "general" and k == "12m":
                    continue                                     # la general a 12 meses ya la pronostica I
                if anio:
                    rem = q[(q["tipo_periodo"] == "anio") & (q["periodo_original"].astype(str).str[:4] == str(anio))]["mediana"].mean()
                    a, b = pd.Period(f"{anio}-12", "M"), pd.Period(f"{anio - 1}-12", "M")
                else:
                    rem = q[(q["tipo_periodo"] == "proximos_meses") & (q["horizonte_meses"] == hz)]["mediana"].mean()
                    a, b = o + hz, o + hz - 12
                real = (n[a] / n[b] - 1) * 100 if a in n.index and b in n.index else np.nan
                tasa = (1 + I12[o] / 100) ** (1 / 12)
                filas.append({**base, "preg": k, "rem": rem, "real": real, "se_mantiene": (tasa ** 12 - 1) * 100})
        Z = pd.DataFrame(filas)
        Z["y"] = np.log1p(Z["rem"] / 100) * 100
        cols = ["x12", "inf12", "mes"]
        Z["nuevo"] = np.nan
        for k, q in Z.groupby("preg"):
            q = q.sort_values("origen")
            for i, row in q.iterrows():
                tr = q[q["origen"] < row["origen"]].dropna(subset=cols + ["y"])
                if len(tr) >= MIN and row[cols].notna().all():
                    Z.loc[i, "nuevo"] = np.expm1(huber(tr[cols].values, tr["y"].values, row[cols].values.astype(float)) / 100) * 100
        Z.to_csv(C / f"probar_inflacion_largo_{ind}.csv", encoding="utf-8-sig", index=False, float_format="%.3f")
        L.append(f"##### {ind.upper()}")
        for per, mk in (("2019-2024", Z["origen"] <= CORTE), ("2025-26", Z["origen"] > CORTE)):
            L.append(f"== {per}")
            L.append(f"   {'pregunta':10}{'dist. REM: mantiene':>21}{'nuevo':>8}{'n':>5} | {'error real: mantiene':>21}{'nuevo':>8}{'REM':>7}{'n':>5}")
            for k in ("12m", "año+1", "año+2", "24m"):
                z = Z[mk & (Z["preg"] == k)].dropna(subset=["nuevo", "rem"])
                if not len(z):
                    continue
                zr = z.dropna(subset=["real"])
                d = lambda c, ref, zz: (zz[c] - zz[ref]).abs().mean() if len(zz) else np.nan
                L.append(f"   {k:10}{d('se_mantiene', 'rem', z):21.2f}{d('nuevo', 'rem', z):8.2f}{len(z):5d} | "
                         f"{d('se_mantiene', 'real', zr):21.2f}{d('nuevo', 'real', zr):8.2f}{d('rem', 'real', zr):7.2f}{len(zr):5d}")
            L.append("")
        u = Z[Z["origen"] == t_hoy]
        ra = Z[Z["origen"] == t_hoy - 1].set_index("preg")["rem"]
        L.append(f"   HOY ({t_hoy}; nuestro 12m general {u['i12'].iloc[0] if len(u) else np.nan:.1f}%):")
        for _, x in u.iterrows():
            L.append(f"      {x['preg']:7} REM anterior {ra.get(x['preg'], np.nan):6.1f}% | se mantiene {x['se_mantiene']:6.1f}% | nuevo {x['nuevo']:6.1f}%")
        L.append("")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_inflacion_largo.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
