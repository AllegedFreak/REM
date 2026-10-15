# validar_indice_sepa.py
# ¿El índice SEPA (supermercados) sigue al IPC del INDEC mes a mes?
# Compara la variación mensual del índice SEPA de cada región con un "INDEC equivalente": las mismas clases del IPC que
# cubre la canasta, con los mismos pesos. Si el INDEC no publica la clase, se usa el grupo que la contiene
# (01.1.3 y 01.1.9 -> 01.1; 02.1.2 y 02.1.3 -> 02.1; 05.6.1 -> 05.6; 12.1.3 -> 12.1).
# También contra Alimentos y bebidas (división 01) y el nivel general.
# Salidas: datos\informe_validacion_sepa.txt, datos\validacion_sepa.csv
# Uso: py -3.13 validar_indice_sepa.py
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from indice_sepa import CLASE

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CARPETA = Path(__file__).resolve().parent / "datos"
REG_ID = {"GBA": "gba", "Pampeana": "pampeana", "Noreste": "noreste", "Noroeste": "noroeste", "Cuyo": "cuyo", "Patagonia": "patagonia"}
PESO_REGION = {"GBA": 0.4467, "Pampeana": 0.3419, "Noreste": 0.0451, "Noroeste": 0.0688, "Cuyo": 0.0518, "Patagonia": 0.0457}
SUSTITUTO = {"01.1.3": "01.1", "01.1.9": "01.1", "02.1.2": "02.1", "02.1.3": "02.1", "05.6.1": "05.6", "12.1.3": "12.1"}


def main():
    hs = pd.read_parquet(CARPETA / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    hs = hs[hs["id"].str.startswith(("indec_ipcap_", "indec_ipc_"))]
    hs["mes"] = pd.to_datetime(hs["fecha"]).dt.to_period("M")
    var = lambda sid: (lambda s: (s / s.shift(1) - 1) * 100)(hs[hs["id"] == sid].set_index("mes")["valor"].sort_index())
    pond = pd.read_csv(CARPETA / "ponderaciones_ipc.csv", encoding="utf-8-sig", dtype={"codigo": str}).set_index("codigo")
    sepa = pd.read_parquet(CARPETA / "sepa_indice_mensual.parquet")
    sepa["mes"] = pd.PeriodIndex(sepa["mes"], freq="M")
    clases = sorted(set(CLASE.values()))
    filas = []
    for reg, rid in REG_ID.items():
        s = sepa[sepa["region"] == reg].set_index("mes")["variacion_pct"]
        if s.empty:
            continue
        # INDEC equivalente: promedio ponderado de las variaciones de las clases (o su grupo), pesos de la canasta
        partes, pesos = [], []
        for c in clases:
            cod = c
            v = var(f"indec_ipcap_{rid}_{cod}")
            if v.empty:
                cod = SUSTITUTO.get(c, c[:4])
                v = var(f"indec_ipcap_{rid}_{cod}")
            if v.empty or c not in pond.index:
                continue
            partes.append(v.rename(c))
            pesos.append(float(pond.loc[c, reg]))
        P = pd.concat(partes, axis=1)
        w = pd.Series(pesos, index=P.columns)
        equiv = (P * w).sum(axis=1, min_count=1) / P.notna().mul(w).sum(axis=1)
        for m, v in s.items():
            filas.append({"mes": m, "region": reg, "sepa": v, "indec_equivalente": equiv.get(m, np.nan),
                          "indec_alimentos": var(f"indec_ipc_{rid}_division_01").get(m, np.nan),
                          "indec_general": var(f"indec_ipc_{rid}_nivel_general").get(m, np.nan)})
    V = pd.DataFrame(filas).dropna(subset=["indec_equivalente"])
    # nacional: regiones con su peso oficial
    nac = V.assign(w=V["region"].map(PESO_REGION)).groupby("mes").apply(
        lambda q: pd.Series({c: np.average(q[c], weights=q["w"]) for c in ["sepa", "indec_equivalente", "indec_alimentos"]}
                            | {"n_regiones": len(q)}))
    g = hs[hs["id"] == "indec_ipc_nivel_general"].set_index("mes")["valor"].sort_index()
    nac["indec_general"] = ((g / g.shift(1) - 1) * 100).reindex(nac.index)
    nac = nac.reset_index().assign(region="Nacional")
    V = pd.concat([V, nac.drop(columns="n_regiones")], ignore_index=True)
    V.to_csv(CARPETA / "validacion_sepa.csv", index=False, encoding="utf-8-sig", float_format="%.3f")

    L = ["=== VALIDACIÓN DEL ÍNDICE SEPA CONTRA EL IPC DEL INDEC (variación mensual, %) ===",
         "INDEC equivalente = mismas clases que la canasta SEPA, mismos pesos. Diferencia = SEPA - INDEC equivalente.", ""]
    for reg in ["Nacional"] + list(REG_ID):
        q = V[V["region"] == reg].sort_values("mes")
        if len(q) < 3:
            continue
        d = q["sepa"] - q["indec_equivalente"]
        L.append(f"{reg}: {len(q)} meses ({q['mes'].min()} a {q['mes'].max()})")
        L.append(f"   correlación con INDEC equivalente {q['sepa'].corr(q['indec_equivalente']):.2f} | con alimentos "
                 f"{q['sepa'].corr(q['indec_alimentos']):.2f} | con nivel general {q['sepa'].corr(q['indec_general']):.2f}")
        L.append(f"   diferencia media {d.mean():+.2f} pp | diferencia absoluta media {d.abs().mean():.2f} pp | "
                 f"misma dirección del cambio mes a mes: {(np.sign(q['sepa'].diff()) == np.sign(q['indec_equivalente'].diff())).iloc[1:].mean():.0%}")
    L += ["", "Nacional, mes por mes:"]
    q = V[V["region"] == "Nacional"].sort_values("mes")[["mes", "sepa", "indec_equivalente", "indec_alimentos", "indec_general"]]
    L.append(q.round(2).to_string(index=False))
    texto = "\n".join(L)
    print(texto)
    (CARPETA / "informe_validacion_sepa.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
