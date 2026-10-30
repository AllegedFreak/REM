# probar_inflacion_referencia.py (30-oct-2026; análisis, fuera del circuito diario)
# Inflación de 2027 y 2028 por CLASE DE REFERENCIA (otros países), decidido con Marcos el 30-oct-2026: con datos solo de
# Argentina no se puede validar un pronóstico a 13-24 meses para el régimen actual (los casos con 20-30% de inflación
# todavía no tienen resultado), y la historia argentina reciente (50-200%) no sirve para entrenar.
# Datos: FMI, World Economic Outlook, inflación de fin de período (diciembre contra diciembre, PCPIEPCH), 227 países, desde
# 1980. Solo años ya ocurridos (hasta 2025); las proyecciones del FMI no se usan para estimar.
# Clases (definidas antes de mirar resultados):
#   A: inflación del año entre 20% y 40% y menor que la del año anterior.
#   B: A y, además, uno de los dos años anteriores al menos duplicaba la inflación del año (desinflación grande reciente).
#   C (agregada después de ver A y B, definida antes de correrla): A y resultado fiscal del gobierno general >= 0 % del PIB
#      ese año (FMI, GGXCNL_NGDP). Hipótesis monetaria: sin financiamiento del déficit la inflación baja más rápido.
# Medida: r_k = log(1 + π_{t+k}) / log(1 + π_t), k = 1, 2 (la inflación de 1 y 2 años después en proporción a la de partida).
# Pronóstico de un caso: π_t elevado a la mediana de r_k de los casos de OTROS países con t+k ya ocurrido antes del año t
# (sin mirar el futuro). Se compara con "se mantiene" (π_{t+k} = π_t). Errores en puntos porcentuales.
# Informe: datos\informe_inflacion_referencia.txt
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
URL = "https://www.imf.org/external/datamapper/api/v1/PCPIEPCH"
ULTIMO_REAL = 2025
MIN_CASOS = 15


def bajar():
    """Baja el WEO; si el FMI rechaza el pedido (a veces devuelve 403 a Python), usa la última copia guardada
    (datos/copias/fmi_pcpiepch_*.json, se puede bajar con curl)."""
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
        (C / "copias").mkdir(exist_ok=True)
        (C / "copias" / f"fmi_pcpiepch_{datetime.now():%Y%m%d}.json").write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        d = json.loads(sorted((C / "copias").glob("fmi_pcpiepch_*.json"))[-1].read_text(encoding="utf-8"))
    filas = [(p, int(a), float(v)) for p, serie in d["values"]["PCPIEPCH"].items() for a, v in serie.items() if v is not None]
    return pd.DataFrame(filas, columns=["pais", "anio", "pi"])


def main():
    D = bajar()
    proy = D[(D["pais"] == "ARG") & (D["anio"] > ULTIMO_REAL)].set_index("anio")["pi"]
    D = D[D["anio"] <= ULTIMO_REAL]
    P = D.pivot(index="anio", columns="pais", values="pi")
    casos = []
    for p in P.columns:
        s = P[p]
        for t in s.index:
            pt = s.get(t)
            if pd.isna(pt) or not (20 <= pt <= 40) or pd.isna(s.get(t - 1)) or s.get(t - 1) <= pt:
                continue
            prev2 = max(s.get(t - 1, np.nan), s.get(t - 2, np.nan) if not pd.isna(s.get(t - 2, np.nan)) else -np.inf)
            casos.append({"pais": p, "anio": t, "pi0": pt, "pi_1": s.get(t + 1, np.nan), "pi_2": s.get(t + 2, np.nan),
                          "clase_B": prev2 >= 2 * pt})
    K = pd.DataFrame(casos)
    g = json.loads(sorted((C / "copias").glob("fmi_ggxcnl_ngdp_*.json"))[-1].read_text(encoding="utf-8"))["values"]["GGXCNL_NGDP"]
    K["saldo"] = [g.get(p, {}).get(str(a), np.nan) for p, a in zip(K["pais"], K["anio"])]
    for k in (1, 2):
        K[f"r{k}"] = np.log1p(K[f"pi_{k}"] / 100) / np.log1p(K["pi0"] / 100)
    L = [f"=== INFLACIÓN 2027-2028 POR CLASE DE REFERENCIA (FMI, dic/dic, 227 países, 1980-{ULTIMO_REAL}) | {datetime.now():%Y-%m-%d %H:%M} ===",
         f"Clase A: inflación 20-40% y bajando ({len(K)} casos, {K['pais'].nunique()} países). "
         f"Clase B: A + desinflación grande reciente ({int(K['clase_B'].sum())} casos, {K[K['clase_B']]['pais'].nunique()} países).", ""]
    L.append(f"Clase C: A + superávit fiscal ({int((K['saldo'] >= 0).sum())} casos, {K[K['saldo'] >= 0]['pais'].nunique()} países; "
             f"con dato fiscal {int(K['saldo'].notna().sum())}). Control: A con déficit ({int((K['saldo'] < 0).sum())} casos).")
    L.append("")
    for nom, Z in (("A", K), ("B", K[K["clase_B"]]), ("C (superávit)", K[K["saldo"] >= 0]), ("A con déficit", K[K["saldo"] < 0])):
        L.append(f"## Clase {nom}")
        for k in (1, 2):
            z = Z.dropna(subset=[f"r{k}"])
            q = z[f"pi_{k}"]
            L.append(f"   {k} año(s) después: inflación mediana {q.median():.1f}% (P10 {q.quantile(.1):.1f}, P90 {q.quantile(.9):.1f}); "
                     f"en proporción a la de partida (log): mediana {z[f'r{k}'].median():.2f} (P10 {z[f'r{k}'].quantile(.1):.2f}, "
                     f"P90 {z[f'r{k}'].quantile(.9):.2f}); sube en el {(z[f'pi_{k}'] > z['pi0']).mean() * 100:.0f}% de los casos; n {len(z)}")
        # validación sin mirar el futuro, excluyendo el propio país
        for k in (1, 2):
            err_ref, err_per = [], []
            for _, c in Z.dropna(subset=[f"pi_{k}"]).iterrows():
                tr = Z[(Z["pais"] != c["pais"]) & (Z["anio"] + k < c["anio"])].dropna(subset=[f"r{k}"])
                if len(tr) < MIN_CASOS:
                    continue
                pred = np.expm1(np.log1p(c["pi0"] / 100) * tr[f"r{k}"].median()) * 100
                err_ref.append(pred - c[f"pi_{k}"])
                err_per.append(c["pi0"] - c[f"pi_{k}"])
            er, ep = np.array(err_ref), np.array(err_per)
            if len(er):
                L.append(f"   validación {k} año(s): error medio clase de referencia {np.abs(er).mean():.1f} pp (mediana {np.median(np.abs(er)):.1f}) "
                         f"| 'se mantiene' {np.abs(ep).mean():.1f} pp (mediana {np.median(np.abs(ep)):.1f}) | gana la clase en el "
                         f"{(np.abs(er) < np.abs(ep)).mean() * 100:.0f}% de los casos | n {len(er)}")
        L.append("")
    # Argentina hoy
    ri = pd.read_csv(C / "registro_inflacion.csv", encoding="utf-8-sig")
    L.append("ARGENTINA, punto de partida 2026 (dic/dic):")
    for etq, pi0 in (("nuestro (meses publicados + I)", 30.3), ("FMI (proyección)", proy.get(2026, np.nan))):
        partes = []
        for nom, Z in (("A", K), ("B", K[K["clase_B"]]), ("C", K[K["saldo"] >= 0])):
            for k, anio in ((1, 2027), (2, 2028)):
                z = Z.dropna(subset=[f"r{k}"])
                f = lambda qq: np.expm1(np.log1p(pi0 / 100) * z[f"r{k}"].quantile(qq)) * 100
                partes.append(f"{nom} {anio}: {f(.5):.1f}% [{f(.1):.1f}-{f(.9):.1f}]")
        L.append(f"   {etq} {pi0:.1f}% -> " + " | ".join(partes))
    L.append(f"   Referencias: REM anterior 2027 21,2% / 2028 15,0%; gobierno 18% / 10%; FMI {proy.get(2027, np.nan)}% / {proy.get(2028, np.nan)}%")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_inflacion_referencia.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
