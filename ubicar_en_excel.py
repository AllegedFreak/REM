# ubicar_en_excel.py - muestra en qué fila/celda del Excel está un dato.
# Uso:  py -3.13 ubicar_en_excel.py --fecha 2024-12-31 --variable TAMAR
#       (--variable es opcional y busca por parte del nombre; --hoja es opcional)
import argparse
import warnings
import pandas as pd
warnings.filterwarnings("ignore")
ap = argparse.ArgumentParser()
ap.add_argument("--fecha", required=True, help="AAAA-MM-DD del relevamiento")
ap.add_argument("--variable", default="")
ap.add_argument("--referencia", default="", help="parte del texto de la columna C")
ap.add_argument("--hoja", default="Base de Datos Completa")
a = ap.parse_args()
if a.hoja.lower().startswith("top"):
    a.hoja = "Base Completa TOP-10"
df = pd.read_excel("datos/rem_historico.xlsx", sheet_name=a.hoja, header=None)
f = pd.to_datetime(df[0], errors="coerce")
m = f.dt.strftime("%Y-%m-%d") == a.fecha
if a.variable:
    m &= df[1].astype(str).str.contains(a.variable, case=False, regex=False)
if a.referencia:
    m &= df[2].astype(str).str.contains(a.referencia, case=False, regex=False)
print(f"Hoja '{a.hoja}': {int(m.sum())} filas.  Columnas: A fecha, B variable, C referencia, D período, E mediana")
for i in df.index[m][:60]:
    r = df.loc[i]
    print(f"  Fila {i + 1} | D{i + 1} = {r[3]!r} | E{i + 1} mediana = {r[4]} | {r[1]} | {r[2]}")
if m.sum() > 60:
    print("  ... (se muestran las primeras 60)")