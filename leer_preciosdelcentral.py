# leer_preciosdelcentral.py
# Extrae los precios diarios de las páginas "Últimos 365 días" de Precios del Central (preciosdelcentral.com.ar,
# sitio privado que publica los datos del Mercado Central de Buenos Aires; requiere usuario). Las páginas las guarda
# Marcos a mano desde su navegador (Ctrl+S, "Webpage, HTML only") en datos\copias\preciosdelcentral\<PRODUCTO>.html.
# El script solo LEE el texto del archivo (no ejecuta nada): busca el gráfico (Chart.js) con "labels" (fechas) y
# "datasets" (precio normal, mínimo y máximo promedio, $/kg).
# Salida: datos\preciosdelcentral.parquet (producto, fecha, serie, precio) y una comparación con las planillas
# oficiales del Mercado Central en los días que se superponen.
# Uso: py -3.13 -I leer_preciosdelcentral.py
import json
import re
import sys
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
ORIGEN = C / "copias" / "preciosdelcentral"
NOMBRES = {"Precio normal promedio": "normal", "Precio mínimo promedio": "minimo", "Precio máximo promedio": "maximo"}


def extraer(html):
    """Devuelve DataFrame fecha, serie, precio a partir del objeto del gráfico."""
    i = html.find('"datasets"')
    if i < 0:
        return None
    ini = html.rfind("{", 0, i)                  # objeto {"datasets": [...], "labels": [...]}
    prof, fin = 0, None
    for j in range(ini, len(html)):
        if html[j] == "{":
            prof += 1
        elif html[j] == "}":
            prof -= 1
            if prof == 0:
                fin = j + 1
                break
    obj = json.loads(html[ini:fin])
    filas = []
    for ds in obj["datasets"]:
        serie = NOMBRES.get(ds.get("label"), ds.get("label"))
        filas += [{"fecha": f, "serie": serie, "precio": v} for f, v in zip(obj["labels"], ds["data"])]
    return pd.DataFrame(filas)


def main():
    partes = []
    for p in sorted(ORIGEN.glob("*.htm*")):
        d = extraer(p.read_text(encoding="utf-8", errors="replace"))
        if d is None or d.empty:
            print(f"  {p.name}: sin datos del gráfico")
            continue
        d.insert(0, "producto", re.sub(r"[,.]HTML?$", "", p.stem.upper()).strip())
        partes.append(d)
        print(f"  {p.stem}: {d['fecha'].nunique()} días ({d['fecha'].min()} a {d['fecha'].max()})")
    if not partes:
        sys.exit("No hay páginas guardadas en " + str(ORIGEN))
    df = pd.concat(partes, ignore_index=True)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
    df["fuente"] = "preciosdelcentral.com.ar (sobre datos del MCBA)"
    df.to_parquet(C / "preciosdelcentral.parquet", index=False)
    # comparación con las planillas oficiales (precio modal por kg del promedio de la especie)
    mc_todo = pd.read_parquet(C / "mercado_central.parquet")
    mc_todo = mc_todo[mc_todo["modal_kg"] > 0]
    mc = mc_todo[mc_todo["promedio_especie"]][["fecha", "especie", "modal_kg", "max_kg", "min_kg"]]
    print("\nComparación con las planillas oficiales del Mercado Central (días comunes):")
    for prod, g in df.pivot_table(index=["producto", "fecha"], columns="serie", values="precio").reset_index().groupby("producto"):
        esp, _, var = prod.partition(" ")
        if var:   # página de una variedad: se compara con el promedio de las filas oficiales de esa variedad
            filas = mc_todo[(mc_todo["especie"] == esp) & ~mc_todo["promedio_especie"]].copy()
            norm = lambda s: re.sub(r"[^A-Z]", "", str(s).upper())
            vars_ = [v for v in filas["variedad"].dropna().unique()
                     if norm(v).startswith(norm(var)[:3]) or norm(var).startswith(norm(v)[:3])]
            filas = filas[filas["variedad"].isin(vars_)]
            ofi = filas.groupby("fecha").agg(modal_kg=("modal_kg", "mean"), max_kg=("max_kg", "mean"), min_kg=("min_kg", "mean")).reset_index()
            o = ofi.merge(g, on="fecha")
            prod = f"{prod} (oficial: {', '.join(vars_) or 'sin variedad igual'})"
        else:
            o = mc[mc["especie"] == prod].merge(g, on="fecha")
        if o.empty:
            print(f"  {prod}: sin días comunes")
            continue
        r = (o["normal"] / o["modal_kg"])
        print(f"  {prod}: {len(o)} días comunes | normal/modal oficial: mediana {r.median():.3f}, "
              f"iguales (±1%) {((r - 1).abs() < 0.01).mean():.0%} | correlación de variaciones diarias "
              f"{o['normal'].pct_change().corr(o['modal_kg'].pct_change()):.2f}")
        print(o[["fecha", "modal_kg", "normal", "min_kg", "minimo", "max_kg", "maximo"]].tail(5).round(1).to_string(index=False))


if __name__ == "__main__":
    main()
