# subir_cambios.py
# Sube a GitHub (https://github.com/AllegedFreak/REM, privado, rama main) los cambios en los scripts y en CLAUDE.md:
#   git add -> git status -> git commit "Actualización automática AAAA-MM-DD HH:MM" -> git push
# - Si no hay cambios, no hace nada.
# - Nunca fuerza nada: sin --force, sin pull, sin rebase, sin reset. Si el push falla (por ejemplo, porque GitHub
#   tiene commits que esta PC no tiene), avisa y deja el commit local para resolverlo a mano.
# - NO sube datos ni claves. Antes de agregar, revisa la lista de archivos y frena todo (sin agregar nada) si aparece:
#   algo dentro de datos\, archivos de datos (.parquet, .xls, .xlsx, .csv, .json, .duckdb, .zip, .gz, .zst), archivos
#   de claves (.env, *.key, *.pem, credentials, token, secret) o un archivo de más de 5 MB. Después revisa el texto de
#   lo que se va a subir buscando claves (tokens de GitHub, claves privadas, contraseñas o api keys asignadas a una
#   variable) y, si encuentra algo, saca esos archivos del área de preparación (git restore --staged: no toca los
#   archivos) y frena.
# - Si git pide usuario o contraseña, falla en lugar de quedarse esperando (para el Programador de tareas).
# Informe: datos\informe_git.txt (datos\ no se sube). Código de salida: 0 = subido o sin cambios | 1 = frenado por
# seguridad o push rechazado | 2 = error.
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
INFORME = BASE / "datos" / "informe_git.txt"
GIT = next((g for g in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files\Git\bin\git.exe", "git") if g == "git" or Path(g).exists()))
REMOTO_ESPERADO = "github.com/AllegedFreak/REM"
RAMA = "main"
MAX_MB = 5
PROHIBIDO_RUTA = re.compile(r"(^|/)datos/|(^|/)\.env|\.(parquet|xls|xlsx|csv|json|duckdb|duckdb\.wal|zip|gz|zst|key|pem|p12|pfx)$|"
                            r"credential|secret|token", re.IGNORECASE)
PROHIBIDO_TEXTO = re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                             r"AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{32,}|(password|passwd|api_key|apikey|secret)\s*[=:]\s*['\"][^'\"]{6,}",
                             re.IGNORECASE)


def git(*args, ok=(0,)):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")      # nunca quedarse esperando usuario/contraseña
    r = subprocess.run([GIT, *args], cwd=BASE, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if r.returncode not in ok:
        raise RuntimeError(f"git {' '.join(args)} -> código {r.returncode}: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def main():
    L = [f"=== SUBIR CAMBIOS A GITHUB | {datetime.now():%Y-%m-%d %H:%M} ==="]
    codigo = 0
    try:
        # 1) controles del repositorio
        if git("rev-parse", "--is-inside-work-tree").strip() != "true":
            raise RuntimeError("la carpeta no es un repositorio git")
        rama = git("branch", "--show-current").strip()
        if rama != RAMA:
            raise RuntimeError(f"la rama actual es '{rama}', no '{RAMA}': no se sube nada")
        remoto = git("remote", "get-url", "origin").strip()
        if REMOTO_ESPERADO.lower() not in remoto.lower():
            raise RuntimeError(f"el remoto es '{remoto}', no {REMOTO_ESPERADO}: no se sube nada")

        # 2) qué se agregaría (simulación: no cambia nada)
        candidatos = [l.split("'")[1] if "'" in l else l[4:].strip() for l in git("add", "--all", "--dry-run").splitlines() if l.strip()]
        if not candidatos and not git("diff", "--cached", "--name-only").strip():
            L.append("Sin cambios: no se sube nada.")
            raise SystemExit
        prohibidos = [c for c in candidatos if PROHIBIDO_RUTA.search(c.replace("\\", "/"))]
        grandes = [c for c in candidatos if (BASE / c).is_file() and (BASE / c).stat().st_size > MAX_MB * 1e6]
        if prohibidos or grandes:
            codigo = 1
            L.append("FRENADO POR SEGURIDAD: no se agregó nada. Archivos que no deben subirse (revisar .gitignore):")
            L += [f"   {c}" for c in prohibidos] + [f"   {c} (más de {MAX_MB} MB)" for c in grandes]
            raise SystemExit

        # 3) agregar y revisar el texto buscando claves
        git("add", "--all")
        estado = git("status", "--short")
        L.append("Cambios a subir:")
        L += [f"   {l}" for l in estado.splitlines()]
        diff = git("diff", "--cached", "-U0")
        con_claves = sorted({m.group(0)[:12] + "..." for m in PROHIBIDO_TEXTO.finditer(diff)})
        if con_claves:
            archivos = git("diff", "--cached", "--name-only").split()
            sospechosos = [a for a in archivos if PROHIBIDO_TEXTO.search(git("diff", "--cached", "-U0", "--", a))]
            git("restore", "--staged", "--", *sospechosos)   # solo los saca de la preparación; no toca los archivos
            codigo = 1
            L.append(f"FRENADO POR SEGURIDAD: posible clave en {', '.join(sospechosos)} ({', '.join(con_claves)}). "
                     "Se sacaron de la preparación; no se hizo commit ni push.")
            raise SystemExit

        # 4) commit y push (sin forzar)
        mensaje = f"Actualización automática {datetime.now():%Y-%m-%d %H:%M}"
        git("commit", "-m", mensaje)
        L.append(f"Commit: {git('log', '-1', '--format=%h %s').strip()}")
        try:
            git("push", "origin", RAMA)
            L.append(f"Subido a {remoto} (rama {RAMA}).")
        except RuntimeError as e:
            codigo = 1
            L.append(f"ALERTAS: el push no se pudo hacer ({e}). El commit quedó en esta PC; no se forzó nada. "
                     "Pedile a Claude que lo revise.")
    except SystemExit:
        pass
    except Exception as e:
        codigo = 2
        L.append(f"ERROR: {e}")
    if codigo == 0 and not any(l.startswith("ALERTAS") for l in L):
        L.append("ALERTAS: ninguna. Todo en orden.")
    texto = "\n".join(L)
    print(texto)
    try:
        INFORME.write_text(texto, encoding="utf-8")
    except Exception:
        pass
    sys.exit(codigo)


if __name__ == "__main__":
    main()
