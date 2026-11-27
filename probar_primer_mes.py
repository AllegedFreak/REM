# probar_primer_mes.py - (26-nov-2026, pedido de Marcos) ¿Mejora la inflación de los primeros meses si se combina nuestro
# pronóstico publicado (G) con el REM del mes anterior o con el modelo B? Solo análisis: no cambia el modelo ni el registro.
# Al sellar (fin del mes t) el REM del mes t todavía no salió: solo se puede usar el REM de t-1 ("REM anterior") o B
# (REM anterior corregido por la sorpresa del IPC y la devaluación). El REM de t se usa solo como vara de comparación.
# Tiempo real: el peso w de la combinación se elige en cada origen con los errores ya conocidos (meses con dato real
# publicado antes del sello: objetivo <= t-1). Mínimo 12 casos; antes, 50/50.
# h = 0: mes del relevamiento (oct-26 en el REM de oct-26); h = 1: mes siguiente.
# Salida: datos\informe_primer_mes.txt y datos\probar_primer_mes.csv
import sys

import numpy as np
import pandas as pd
from scipy import stats

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = __import__("pathlib").Path(__file__).resolve().parent / "datos"
PESOS = np.round(np.arange(0, 1.0001, 0.1), 1)
MIN_CASOS = 12


def dm(e1, e2):
    """Diebold-Mariano con error absoluto (h=1): p bilateral. Negativo = e1 mejor."""
    d = np.abs(e1) - np.abs(e2)
    d = d[~np.isnan(d)]
    if len(d) < 8 or d.std(ddof=1) == 0:
        return np.nan, np.nan
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
    return t, 2 * (1 - stats.t.cdf(abs(t), len(d) - 1))


def main():
    g = pd.read_csv(C / "replica_general.csv", encoding="utf-8-sig")
    r = pd.read_csv(C / "replica_rem.csv", encoding="utf-8-sig")
    filas = []
    for h in (0, 1):
        a = g[g["h"] == h][["origen", "obj", "G", "real"]]
        b = r[r["H"] == f"m{h}"][["origen", "rem", "rem_ant", "B"]]
        d = a.merge(b, on="origen", how="inner").sort_values("origen").reset_index(drop=True)
        d["h"] = h
        d["o"] = pd.PeriodIndex(d["origen"], freq="M")
        d["ob"] = pd.PeriodIndex(d["obj"], freq="M")
        for base in ("rem_ant", "B"):
            col_w, col = f"w_{base}", f"G+{base}"
            ws, ps = [], []
            for i, x in d.iterrows():
                hist = d[(d["ob"] <= x["o"] - 1) & d["real"].notna() & d["G"].notna() & d[base].notna()]
                if len(hist) >= MIN_CASOS:
                    err = [np.abs(w * hist["G"] + (1 - w) * hist[base] - hist["real"]).mean() for w in PESOS]
                    w = float(PESOS[int(np.argmin(err))])
                else:
                    w = 0.5
                ws.append(w)
                ps.append(w * x["G"] + (1 - w) * x[base])
            d[col_w], d[col] = ws, ps
        d["mitad G/B"] = (d["G"] + d["B"]) / 2
        filas.append(d)
    R = pd.concat(filas, ignore_index=True)
    R.drop(columns=["o", "ob"]).to_csv(C / "probar_primer_mes.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    cand = [("G", "Publicado (G)"), ("rem_ant", "REM anterior"), ("B", "Modelo B"), ("mitad G/B", "Mitad G / mitad B"),
            ("G+rem_ant", "G + REM ant. (peso óptimo)"), ("G+B", "G + B (peso óptimo)"), ("rem", "REM del mes (vara)")]
    L = [f"=== INFLACIÓN DE LOS PRIMEROS MESES: ¿COMBINAR CON EL REM ANTERIOR O CON B? | {pd.Timestamp.now():%Y-%m-%d %H:%M} ===",
         "Tiempo real. Error absoluto contra el IPC (pp). El REM del mes no se conoce al sellar: va solo como vara.",
         "p = Diebold-Mariano contra el publicado (G); < 0,05 = diferencia significativa.", ""]
    for h in (0, 1):
        q0 = R[(R["h"] == h)].dropna(subset=["real", "G", "rem_ant", "B", "rem"])
        L.append(f"##### h = {h} ({'mes del relevamiento' if h == 0 else 'mes siguiente'})")
        for nom, a, b in (("2019-2024", "2019-01", "2024-12"), ("2025-26 (régimen actual)", "2025-01", "2026-12")):
            q = q0[(q0["origen"] >= a) & (q0["origen"] <= b)]
            if not len(q):
                continue
            L.append(f"{nom}: {len(q)} meses ({q['origen'].min()} a {q['origen'].max()})")
            L.append(f"   {'':28}{'error':>7}{'sesgo':>7}{'gana a G':>9}{'p vs G':>8}")
            for c, n in cand:
                e = q[c] - q["real"]
                t, p = dm(e.values, (q["G"] - q["real"]).values) if c != "G" else (np.nan, np.nan)
                gana = (np.abs(e) < np.abs(q["G"] - q["real"])).mean() * 100 if c != "G" else np.nan
                L.append(f"   {n:28}{e.abs().mean():7.2f}{e.mean():7.2f}" + (f"{gana:8.0f}%" if c != "G" else f"{'':9}")
                         + (f"{p:8.2f}" if not np.isnan(p) else f"{'':8}"))
            ult = q.iloc[-1]
            L.append(f"   Peso de G hoy (último origen): con REM ant. {ult['w_rem_ant']:.1f} · con B {ult['w_B']:.1f}")
            L.append("")
        q = q0[q0["origen"] >= "2025-01"]
        L.append("   Mes por mes desde 2025:")
        L.append(f"   {'origen':8}{'real':>7}{'G':>7}{'REMant':>7}{'B':>7}{'G+B':>7}{'w':>5}{'REM':>7}")
        for _, x in q.iterrows():
            L.append(f"   {x['origen']:8}{x['real']:7.2f}{x['G']:7.2f}{x['rem_ant']:7.2f}{x['B']:7.2f}{x['G+B']:7.2f}{x['w_B']:5.1f}{x['rem']:7.2f}")
        L.append("")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_primer_mes.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
