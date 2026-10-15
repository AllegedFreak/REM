# explorar_bcra.py - Explora la API de Estadísticas del BCRA: lista las variables (tipo de cambio, tasas)
# con su código (idVariable) y prueba bajar unos días de una de ellas. Solo mira, no guarda series.
# Uso: py -3.13 explorar_bcra.py
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
CARPETA = Path(__file__).resolve().parent / "datos"
CARPETA.mkdir(exist_ok=True)
CLAVES = ["tipo de cambio", "tamar", "badlar", "política monetaria", "politica monetaria", "leliq",
          "pases", "plazo fijo", "tm20", "tasa de interés", "tasa de interes"]


def pedir(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except ssl.SSLError as e:
        print(f"ERROR de certificado SSL: {e}")
        print("No desactivo la verificación por mi cuenta. Pegale este mensaje a Claude.")
        sys.exit(2)
    except Exception as e:
        print(f"ERROR de conexión: {type(e).__name__}: {e}")
        sys.exit(2)


print("1) Lista de variables (API v4.0)")
filas, offset, total = [], 0, None
while True:
    estado, cuerpo = pedir(f"{BASE_URL}?limit=1000&offset={offset}")
    print(f"   HTTP {estado}, {len(cuerpo)} bytes (offset {offset})")
    if estado != 200:
        print(cuerpo[:500].decode("utf-8", "replace"))
        sys.exit(2)
    datos = json.loads(cuerpo)
    if offset == 0:
        (CARPETA / "bcra_variables.json").write_bytes(cuerpo)
        print(f"   Claves principales: {list(datos.keys())}")
        total = datos.get("metadata", {}).get("resultset", {}).get("count")
    pagina = datos.get("results", [])
    filas += pagina
    offset += 1000
    if not pagina or (total is not None and len(filas) >= total) or offset > 20000:
        break
print(f"   Variables totales: {len(filas)} (informadas por la API: {total})")
if filas:
    print(f"   Campos de cada variable: {list(filas[0].keys())}")

print("\n2) Variables que nos interesan")
elegidas = []
for f in filas:
    texto = " ".join(str(v) for v in f.values()).lower()
    if any(k in texto for k in CLAVES):
        elegidas.append(f)
for f in elegidas:
    print(f"   id={f.get('idVariable')} | {f.get('descripcion')} | {f.get('categoria', '')} | "
          f"último: {f.get('ultFechaInformada')} = {f.get('ultValorInformado')}")
print(f"   ({len(elegidas)} variables)")

print("\n3) Prueba de descarga (últimos días) de la primera variable de tipo de cambio")
tc = [f for f in elegidas if "tipo de cambio" in str(f.get("descripcion", "")).lower()]
if tc:
    vid = tc[0].get("idVariable")
    estado, cuerpo = pedir(f"{BASE_URL}/{vid}?desde=2026-09-25&hasta=2026-10-13&limit=10")
    print(f"   id={vid} HTTP {estado}")
    print("   " + cuerpo[:900].decode("utf-8", "replace"))
else:
    print("   No encontré una variable de tipo de cambio en la lista.")
print("\nListo. Se guardó datos\\bcra_variables.json. Pegale a Claude toda esta salida.")