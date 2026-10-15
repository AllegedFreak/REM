# bajar_preciazo.py
# Baja la historia de SEPA (Precios Claros) archivada por el proyecto preciazo (github.com/catdevnull/preciazo), que guarda
# los datasets oficiales de datos.produccion.gob.ar recomprimidos (tar.zst, ~8 veces más chicos) en Backblaze B2.
# Índice: github.com/catdevnull/sepa-precios-metadata (index.md). Período: desde el 19-ago-2024 (con huecos: mar-jun 2026).
# Son datos oficiales copiados por un tercero (como Zenodo): sirven de historia y validación; el contenido es el mismo formato
# que los ZIP oficiales (una carpeta por comercio con comercio.csv, sucursales.csv y productos.csv).
#   - un archivo por día: E:\SEPA\historico_preciazo\sepa_AAAA-MM-DD.tar.zst
#   - se puede retomar: saltea los días ya bajados y sanos; verifica que cada archivo se descomprima completo
#   - no baja los días que ya están en E:\SEPA\crudos (bajados del portal oficial)
# Registro: E:\SEPA\historico_preciazo\registro_descarga.csv; avance en avance.txt
# Uso: py -3.13 bajar_preciazo.py [hilos]   (para que no dependa de Claude Code: Start-Process py -ArgumentList ... -WindowStyle Hidden)
import csv
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import zstandard

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DESTINO = Path("E:/SEPA/historico_preciazo")
CRUDOS = Path("E:/SEPA/crudos")
REGISTRO = DESTINO / "registro_descarga.csv"
INDICE = "https://raw.githubusercontent.com/catdevnull/sepa-precios-metadata/main/index.md"
HILOS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def indice():
    """Fecha -> URL del primer archivo descargable (✅) de ese día en el índice de preciazo."""
    txt = urllib.request.urlopen(urllib.request.Request(INDICE, headers=UA), timeout=120).read().decode("utf-8")
    urls, actual = {}, None
    for linea in txt.splitlines():
        m = re.match(r"\* \w+, (\d{2})/(\d{2})/(\d{4}):", linea)
        if m:
            actual = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            continue
        u = re.search(r"\((https://f004\.backblazeb2\.com/[^)]+\.tar\.zst)\)", linea)
        if actual and u and actual not in urls:
            urls[actual] = u.group(1)
    return urls


def sano(ruta):
    """El archivo se descomprime completo y el tar se recorre sin errores (detecta descargas cortadas)."""
    try:
        n = 0
        with open(ruta, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as r:
            with tarfile.open(fileobj=r, mode="r|") as tar:
                for m in tar:
                    if m.isfile():
                        f = tar.extractfile(m)
                        while f.read(16 * 1024 * 1024):
                            pass
                        n += 1
        return n > 0
    except Exception:
        return False


def bajar(fecha, url):
    ruta = DESTINO / f"sepa_{fecha}.tar.zst"
    if ruta.exists() and ruta.stat().st_size > 0 and sano(ruta):
        return fecha, "ya estaba", ruta.stat().st_size, 0.0
    t0 = time.time()
    for intento in range(6):
        try:
            tmp = ruta.with_suffix(".parcial")
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=900) as r, open(tmp, "wb") as f:
                while True:
                    b = r.read(8 * 1024 * 1024)
                    if not b:
                        break
                    f.write(b)
            if not sano(tmp):
                tmp.unlink(missing_ok=True)
                raise IOError("archivo cortado o roto")
            tmp.replace(ruta)
            return fecha, "bajado", ruta.stat().st_size, time.time() - t0
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return fecha, "no existe", 0, time.time() - t0
            time.sleep(30 * (intento + 1) if e.code in (429, 503) else 10)
        except Exception:
            time.sleep(10 * (intento + 1))
    return fecha, "falló (reintentar después)", 0, time.time() - t0


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)
    urls = indice()
    oficiales = {p.stem.replace("sepa_", "") for p in CRUDOS.glob("*/sepa_*.zip")}
    pendientes = {f: u for f, u in sorted(urls.items()) if f not in oficiales}
    print(f"{datetime.now():%H:%M} Índice: {len(urls)} días con descarga; {len(urls) - len(pendientes)} ya están en crudos oficiales; "
          f"bajando {len(pendientes)} a {DESTINO} ({HILOS} a la vez)...", flush=True)
    nuevo = not REGISTRO.exists()
    resumen, total = {}, 0
    with open(REGISTRO, "a", newline="", encoding="utf-8") as fh, ThreadPoolExecutor(HILOS) as ex:
        w = csv.writer(fh)
        if nuevo:
            w.writerow(["fecha", "estado", "bytes", "segundos", "url", "anotado"])
        futuros = {ex.submit(bajar, f, u): u for f, u in pendientes.items()}
        for i, fu in enumerate(as_completed(futuros), 1):
            fecha, estado, b, s = fu.result()
            w.writerow([fecha, estado, b, round(s, 1), futuros[fu], f"{datetime.now():%Y-%m-%d %H:%M}"])
            fh.flush()
            resumen[estado] = resumen.get(estado, 0) + 1
            total += b
            linea = f"{datetime.now():%Y-%m-%d %H:%M} {i}/{len(pendientes)} días | {total / 1e9:.1f} GB | {resumen}"
            (DESTINO / "avance.txt").write_text(linea, encoding="utf-8")
            if i % 25 == 0 or i == len(pendientes):
                print(linea, flush=True)
    print(f"Listo. {resumen}. Total: {total / 1e9:.1f} GB. Registro: {REGISTRO}")
    sys.exit(0 if not resumen.get("falló (reintentar después)") else 1)


if __name__ == "__main__":
    main()
