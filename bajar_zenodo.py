# bajar_zenodo.py
# Baja la historia de SEPA 2016-2018 publicada en Zenodo (DOI 10.5281/zenodo.6568295, "Precios Claros (2016-05 - 2018-03)")
# a E:\SEPA\historico_zenodo\precios_AAAA-MM-DD.csv.gz. Uso: validar el método del índice (no es fuente de producción).
#   - prueba todos los días del 2016-05-09 al 2018-03-11 (la lista completa de archivos no se puede leer: la API da 403);
#     los que no existen (404) quedan anotados como faltantes
#   - se puede retomar: saltea los días ya bajados y sanos; verifica que cada .gz descomprima completo
#   - 6 descargas en paralelo (Zenodo da ~160 KB/s por conexión); si pide frenar (429) espera y reintenta
# Registro: E:\SEPA\historico_zenodo\registro_descarga.csv; avance en E:\SEPA\historico_zenodo\avance.txt
# Uso: py -3.13 bajar_zenodo.py [hilos]
#      Para que no dependa de Claude Code: Start-Process py -ArgumentList '-3.13','bajar_zenodo.py' -WindowStyle Hidden
#      (retoma solo si se corta: volver a correrlo)
import csv
import gzip
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DESTINO = Path("E:/SEPA/historico_zenodo")
REGISTRO = DESTINO / "registro_descarga.csv"
URL = "https://zenodo.org/records/6568295/files/precios_{}.csv.gz?download=1"
DESDE, HASTA = date(2016, 5, 9), date(2018, 3, 11)
HILOS = int(sys.argv[1]) if len(sys.argv) > 1 else 6   # Zenodo limita cada conexión a ~160 KB/s: más hilos = más rápido


def sano(ruta):
    """El .gz descomprime completo (detecta descargas cortadas)."""
    try:
        with gzip.open(ruta, "rb") as f:
            while f.read(16 * 1024 * 1024):
                pass
        return True
    except Exception:
        return False


def bajar(dia):
    nombre = dia.isoformat()
    ruta = DESTINO / f"precios_{nombre}.csv.gz"
    if ruta.exists() and ruta.stat().st_size > 0 and sano(ruta):
        return nombre, "ya estaba", ruta.stat().st_size, 0.0
    t0 = time.time()
    for intento in range(6):
        try:
            req = urllib.request.Request(URL.format(nombre), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=900) as r:
                tmp = ruta.with_suffix(".parcial")
                with open(tmp, "wb") as f:
                    while True:
                        bloque = r.read(8 * 1024 * 1024)
                        if not bloque:
                            break
                        f.write(bloque)
            if not sano(tmp):
                tmp.unlink(missing_ok=True)
                raise IOError("archivo cortado")
            tmp.replace(ruta)
            return nombre, "bajado", ruta.stat().st_size, time.time() - t0
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return nombre, "no existe en Zenodo", 0, time.time() - t0
            time.sleep(30 * (intento + 1) if e.code == 429 else 10)
        except Exception:
            time.sleep(10 * (intento + 1))
    return nombre, "falló (reintentar después)", 0, time.time() - t0


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)
    dias = [DESDE + timedelta(d) for d in range((HASTA - DESDE).days + 1)]
    nuevo = not REGISTRO.exists()
    print(f"{datetime.now():%H:%M} Bajando {len(dias)} días a {DESTINO} ({HILOS} a la vez)...", flush=True)
    resumen, total = {}, 0
    with open(REGISTRO, "a", newline="", encoding="utf-8") as fh, ThreadPoolExecutor(HILOS) as ex:
        w = csv.writer(fh)
        if nuevo:
            w.writerow(["fecha", "estado", "bytes", "segundos", "anotado"])
        futuros = [ex.submit(bajar, d) for d in dias]
        for i, fu in enumerate(as_completed(futuros), 1):
            nombre, estado, b, s = fu.result()
            w.writerow([nombre, estado, b, round(s, 1), f"{datetime.now():%Y-%m-%d %H:%M}"])
            fh.flush()
            resumen[estado] = resumen.get(estado, 0) + 1
            total += b
            linea = f"{datetime.now():%Y-%m-%d %H:%M} {i}/{len(dias)} días | {total / 1e9:.1f} GB | {resumen}"
            (DESTINO / "avance.txt").write_text(linea, encoding="utf-8")
            if i % 25 == 0 or i == len(dias):
                print(linea, flush=True)
    print(f"Listo. {resumen}. Total en disco: {total / 1e9:.1f} GB. Registro: {REGISTRO}")
    sys.exit(0 if not resumen.get("falló (reintentar después)") else 1)


if __name__ == "__main__":
    main()
