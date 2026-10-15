# respaldo.py
# Copia de respaldo del proyecto con robocopy (de Windows). Las copias SOLO agregan y actualizan: nunca borran nada en
# el destino (si algo se borra por error en el original, el respaldo lo conserva).
#   1) OneDrive (C:\Users\marco\OneDrive\Respaldo_REM): lo irremplazable y liviano (~8 GB): carpeta del proyecto,
#      crudos y resúmenes propios de SEPA, canasta y combustibles. NO las historias de terceros (Zenodo, preciazo:
#      ~76 GB, se pueden volver a bajar), para no llenar el disco C:.
#   2) Disco de respaldo (DISCO_RESPALDO, si está conectado): todo lo anterior + las historias de terceros.
# Si el disco de respaldo no está conectado, avisa y sigue.
# Informe: datos\informe_respaldo.txt. Uso: py -3.13 respaldo.py
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
ONEDRIVE = Path(os.environ.get("OneDrive", r"C:\Users\marco\OneDrive")) / "Respaldo_REM"
DISCO_RESPALDO = Path("F:/Respaldo_REM")   # disco de 1 TB (etiqueta "1TB")
EXCLUIR_DIR = ["tmp", "tmp_preciazo", "tmp_debug", "__pycache__", ".git"]
EXCLUIR_ARCH = ["*.duckdb", "*.duckdb.wal", "*.tmp"]   # la base DuckDB se rearma sola desde los parquet

LIVIANO = [(BASE, "rem"), (Path("E:/SEPA/crudos"), "SEPA/crudos"), (Path("E:/SEPA/resumen"), "SEPA/resumen"),
           (Path("E:/SEPA/canasta"), "SEPA/canasta"), (Path("E:/combustibles"), "combustibles")]
PESADO = [(Path("E:/SEPA/historico_zenodo"), "SEPA/historico_zenodo"), (Path("E:/SEPA/historico_preciazo"), "SEPA/historico_preciazo")]


def copiar(origen, destino, L):
    if not origen.exists():
        L.append(f"   (no existe {origen}: se saltea)")
        return 0
    cmd = ["robocopy", str(origen), str(destino), "/E", "/XO", "/R:2", "/W:5", "/NP", "/NFL", "/NDL", "/NJH",
           "/XD", *EXCLUIR_DIR, "/XF", *EXCLUIR_ARCH]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="cp850", errors="replace")
    # robocopy: 0 = nada que copiar, 1 = copió, 2-7 = extras/diferencias sin error, >= 8 = error
    resumen = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith(("Dirs", "Files", "Bytes", "Dir", "Arch"))]
    estado = "OK" if r.returncode < 8 else f"ERROR (código {r.returncode})"
    L.append(f"   {origen} -> {destino}: {estado}")
    L += [f"      {x}" for x in resumen[:3]]
    return r.returncode


def main():
    L = [f"=== RESPALDO | {datetime.now():%Y-%m-%d %H:%M} ==="]
    peor = 0
    L.append(f"1) OneDrive ({ONEDRIVE}): proyecto, crudos y resúmenes propios de SEPA, combustibles")
    for o, d in LIVIANO:
        peor = max(peor, copiar(o, ONEDRIVE / d, L))
    for f in (Path("E:/SEPA/registro.csv"),):
        if f.exists():
            (ONEDRIVE / "SEPA").mkdir(parents=True, exist_ok=True)
            subprocess.run(["robocopy", str(f.parent), str(ONEDRIVE / "SEPA"), f.name, "/XO", "/NJH", "/NJS"], capture_output=True)
    if DISCO_RESPALDO is None:
        L.append("2) Disco de respaldo: todavía no configurado (falta la letra del disco de 1 TB).")
    elif not Path(str(DISCO_RESPALDO)[:3]).exists():
        L.append(f"2) Disco de respaldo {DISCO_RESPALDO}: NO CONECTADO. ALERTAS: respaldo local sin hacer hoy.")
    else:
        L.append(f"2) Disco de respaldo ({DISCO_RESPALDO}): todo")
        for o, d in LIVIANO + PESADO:
            peor = max(peor, copiar(o, DISCO_RESPALDO / d, L))
    L.append("Respaldo terminado sin errores." if peor < 8 else "ALERTAS: hubo errores en el respaldo (ver arriba).")
    texto = "\n".join(L)
    print(texto)
    (BASE / "datos" / "informe_respaldo.txt").write_text(texto, encoding="utf-8")
    sys.exit(0 if peor < 8 else 1)


if __name__ == "__main__":
    main()
