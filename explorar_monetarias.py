# explorar_monetarias.py - Busca en la API del BCRA v4.0 las series de oferta monetaria (base monetaria,
# circulación, billetes y monedas, M1/M2/M3, depósitos, factores de explicación) y muestra
# código, periodicidad, unidad y desde cuándo hay datos. Solo mira: no modifica las series guardadas.
# Guarda el listado en datos/bcra_monetarias_candidatas.csv
# Uso: py -3.13 explorar_monetarias.py
import csv
import json
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
CARPETA = Path(__file__).resolve().parent / "datos"
SALIDA = CARPETA / "bcra_monetarias_candidatas.csv"
CLAVES = ["base monetaria", "circulacion", "billetes", "m1", "m2", "m3", "medios de pago", "agregado",
          "depositos", "efecto monetario", "emision", "factores de explicacion", "cuenta corriente en el bcra",
          "cuentas corrientes en el bcra", "compra de divisas", "tesoro", "pasivos remunerados"]


def sin_acentos(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def pedir(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"ERROR: la API respondió HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")
        sys.exit(2)
    except ssl.SSLError as e:
        print(f"ERROR de certificado SSL: {e}")
        print("No desactivo la verificación por mi cuenta. Pegale este mensaje a Claude.")
        sys.exit(2)
    except Exception as e:
        print(f"ERROR de conexión: {type(e).__name__}: {e}")
        sys.exit(2)


filas, offset = [], 0
while True:
    datos = pedir(f"{BASE_URL}?limit=1000&offset={offset}")
    pagina = datos.get("results", [])
    filas += pagina
    total = datos.get("metadata", {}).get("resultset", {}).get("count", 0)
    offset += 1000
    if not pagina or len(filas) >= total:
        break
print(f"Variables en la API: {len(filas)}")

elegidas = []
for f in filas:
    texto = " " + sin_acentos(f"{f.get('descripcion')} {f.get('categoria')}") + " "
    if any((" " + k + " " in texto) if len(k) <= 2 else (k in texto) for k in CLAVES):
        elegidas.append(f)
elegidas.sort(key=lambda f: (str(f.get("categoria")), f.get("idVariable")))

CAMPOS = ["idVariable", "descripcion", "categoria", "tipoSerie", "periodicidad", "unidadExpresion", "moneda",
          "primerFechaInformada", "ultFechaInformada", "ultValorInformado"]
CARPETA.mkdir(exist_ok=True)
with open(SALIDA, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=CAMPOS, extrasaction="ignore")
    w.writeheader()
    w.writerows(elegidas)

cat_actual = None
for f in elegidas:
    if f.get("categoria") != cat_actual:
        cat_actual = f.get("categoria")
        print(f"\n== {cat_actual}")
    print(f"  id={f.get('idVariable'):<5} {f.get('periodicidad')} | {f.get('tipoSerie')} | {f.get('moneda')} | "
          f"{f.get('primerFechaInformada')} a {f.get('ultFechaInformada')} | {f.get('unidadExpresion')} | "
          f"{str(f.get('descripcion')).strip()}")
print(f"\n{len(elegidas)} candidatas. Listado en {SALIDA}")
