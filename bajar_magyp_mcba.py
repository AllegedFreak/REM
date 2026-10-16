# bajar_magyp_mcba.py
# Intento de descarga de los precios del Mercado Central de Buenos Aires publicados por el Ministerio de Agricultura
# (Subsecretaría de Mercados Agropecuarios, SSMA), para completar jul-2022 a oct-2025:
#   https://ssma.magyp.gob.ar/frutas.preciospromedioh.aspx  (precio promedio de hortalizas en $/kg en el MCBA)
#   https://ssma.magyp.gob.ar/frutas.preciospromediof.aspx  (ídem frutas)
#   https://ssma.magyp.gob.ar/frutas.precios.aspx           (precios diarios con filtro de fechas y botón Exportar)
# El sitio es lento e inestable (pensado para correrlo de madrugada). Guarda cada página como vino y baja los
# archivos .xls/.xlsx/.csv/.zip que estén enlazados de forma directa. No usa formularios ni automatiza el navegador.
# Salida: datos\copias\magyp_mcba\ (páginas y archivos) e informe datos\informe_magyp_mcba.txt
# Uso: py -3.13 bajar_magyp_mcba.py
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
DESTINO = BASE / "datos" / "copias" / "magyp_mcba"
INFORME = BASE / "datos" / "informe_magyp_mcba.txt"
PAGINAS = ["https://ssma.magyp.gob.ar/frutas.preciospromedioh.aspx", "https://ssma.magyp.gob.ar/frutas.preciospromediof.aspx",
           "https://ssma.magyp.gob.ar/frutas.precios.aspx", "https://ssma.magyp.gob.ar/frutas.msdmensual.aspx"]
UA = {"User-Agent": "Mozilla/5.0"}


def bajar(url, intentos=4, timeout=240):
    for i in range(intentos):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read(), None
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(30 * (i + 1))
    return None, err


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y-%m-%d_%H%M")
    L = [f"=== MAGyP / Mercado Central: intento de descarga | {datetime.now():%Y-%m-%d %H:%M} ==="]
    for u in PAGINAS:
        html, err = bajar(u)
        nombre = u.rsplit("/", 1)[-1].replace(".aspx", "")
        if html is None:
            L.append(f"{nombre}: NO RESPONDE ({err})")
            continue
        (DESTINO / f"{nombre}_{sello}.html").write_bytes(html)
        texto = html.decode("utf-8", "replace")
        links = sorted(set(re.findall(r"(?:href|src)=[\"']([^\"']+\.(?:xlsx?|csv|zip))", texto, re.I)))
        L.append(f"{nombre}: página guardada ({len(html):,} bytes); archivos enlazados: {len(links)}")
        for l in links:
            full = urllib.parse.urljoin(u, l)
            dest = DESTINO / urllib.parse.unquote(full.rsplit("/", 1)[-1])
            if dest.exists():
                L.append(f"   ya estaba: {dest.name}")
                continue
            b, e2 = bajar(full, intentos=3)
            if b:
                dest.write_bytes(b)
                L.append(f"   bajado: {dest.name} ({len(b):,} bytes)")
            else:
                L.append(f"   falló: {full} ({e2})")
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
