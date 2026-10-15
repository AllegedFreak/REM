# ponderaciones_ipc.py
# Ponderaciones OFICIALES del IPC del INDEC (base dic-2016, canasta de la ENGHo 2004/05):
#   - ponderadores_ipc.xls: estructura completa (división, grupo, clase/subclase) por región
#   - sh_ipc_divisiones.xls, hoja "Ponderaciones": peso de cada región en el IPC nacional
# El INDEC no publica pesos nacionales por rubro: se calculan como suma de (peso de la región x peso del rubro en la región).
# Guarda las planillas originales en datos/copias y datos/ponderaciones_ipc.csv (formato largo + columna nacional).
# Uso: py -3.13 ponderaciones_ipc.py
import sys
import urllib.request
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
COPIAS = CARPETA / "copias"
URL = "https://www.indec.gob.ar/ftp/cuadros/economia/"
REGIONES = ["GBA", "Pampeana", "Noreste", "Noroeste", "Cuyo", "Patagonia"]
SALIDA = CARPETA / "ponderaciones_ipc.csv"


def bajar(nombre):
    destino = COPIAS / nombre
    COPIAS.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(URL + nombre, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            destino.write_bytes(r.read())
    except Exception:
        if not destino.exists():
            raise
    return destino


def main():
    # peso de cada región en el nacional (hoja Ponderaciones, fila "Importancia relativa de la región")
    h = pd.read_excel(bajar("sh_ipc_divisiones.xls"), sheet_name="Ponderaciones", header=None)
    fila = h[h[0].astype(str).str.contains("Importancia relativa", na=False)].iloc[0]
    peso_region = pd.Series(fila[1:7].astype(float).values, index=REGIONES)
    # estructura completa por región
    p = pd.read_excel(bajar("ponderadores_ipc.xls"), header=None)
    p = p.iloc[3:, :8]
    p.columns = ["codigo", "descripcion"] + REGIONES
    p = p.dropna(subset=["codigo"])
    p["codigo"] = p["codigo"].astype(str).str.strip()
    p = p[p["codigo"].str.match(r"^\d")]
    p[REGIONES] = p[REGIONES].astype(float)
    p["nivel"] = p["codigo"].str.count(r"\.").map({0: "división", 1: "grupo", 2: "clase"})
    p.loc[p["codigo"] == "0", "nivel"] = "nivel general"
    p["Nacional"] = (p[REGIONES] * peso_region / peso_region.sum()).sum(axis=1)
    # hoja = último nivel de apertura (clase, o grupo/división sin clases, ej. 02.2 Tabaco): las hojas suman 100%
    codigos = list(p["codigo"])
    p["hoja"] = [c != "0" and not any(o.startswith(c + ".") for o in codigos) for c in codigos]
    p[["codigo", "descripcion", "nivel", "hoja", "Nacional"] + REGIONES].to_csv(SALIDA, index=False, encoding="utf-8-sig", float_format="%.5f")

    print("Peso de cada región en el IPC nacional (%):", {r: round(v * 100, 2) for r, v in peso_region.items()},
          f"(suma {peso_region.sum() * 100:.2f})")
    d = p[p["nivel"] == "división"]
    print(f"\nDivisiones (%): suma nacional {d['Nacional'].sum() * 100:.2f}")
    print((d.set_index("descripcion")[["Nacional"] + REGIONES] * 100).round(1).to_string())
    c = p[p["hoja"]].sort_values("Nacional", ascending=False)
    print(f"\n{len(c)} rubros finales (hojas; suman {c['Nacional'].sum() * 100:.2f}%); los 25 de mayor peso nacional (%):")
    print((c.set_index(["codigo", "descripcion"])[["Nacional", "GBA"]] * 100).round(2).head(25).to_string())
    print(f"\nGuardado: {SALIDA}")


if __name__ == "__main__":
    main()
