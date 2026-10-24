# actualizar_todo.py - Corre en orden las actualizaciones (REM, IPC del INDEC, IPCBA, series del BCRA, dólares y,
# una vez por semana, las series históricas completas).
# Cada una avisa por su cuenta con un cartel de Windows solo si hay novedades o errores (opción --aviso).
# Este script arma además un resumen en datos\informe_general.txt.
#
# Uso:  py -3.13 actualizar_todo.py                 -> corre y muestra el resumen
#       pythonw.exe actualizar_todo.py              -> para el Programador de tareas (silencioso)
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = algún error
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
PASOS = [
    ("REM del BCRA", "actualizar_rem.py", "informe_actualizacion.txt"),
    ("IPC del INDEC", "actualizar_ipc.py", "informe_ipc.txt"),
    ("IPCBA (Ciudad de Buenos Aires)", "actualizar_ipcba.py", "informe_ipcba.txt"),
    ("Serie de precios encadenada (IPCBA -> INDEC)", "empalme_precios.py --solo-serie", "informe_empalme_serie.txt"),   # usa IPC e IPCBA
    ("Series del BCRA (tipo de cambio, tasas y oferta monetaria)", "actualizar_bcra.py", "informe_bcra.txt"),
    ("Dólares paralelos y brecha (argentinadatos.com)", "actualizar_dolares.py", "informe_dolares.txt"),   # va después del BCRA (usa el A3500)
    ("Precios de supermercados (SEPA, disco E:)", "actualizar_sepa.py", "informe_sepa.txt"),   # ~2 min por día nuevo
    ("Índice SEPA (canasta INDEC, alta frecuencia)", "indice_sepa.py", "informe_indice_sepa.txt"),   # después de SEPA
    ("Combustibles en surtidor (disco E:)", "actualizar_combustibles.py", "informe_combustibles.txt"),   # 9 MB/día; 780 MB 1 vez/mes
    ("Futuros de dólar (CEM Matba-Rofex/A3)", "actualizar_futuros.py", "informe_futuros.txt"),   # solo días nuevos
    ("Series históricas completas (semanal)", "guardar_historicas.py", "informe_historicas.txt"),   # al final: arma la base DuckDB con todo
    ("Mercado Central: precios mayoristas de frutas y hortalizas", "actualizar_mercado_central.py", "informe_mercado_central.txt"),
    ("Hacienda (MAG Cañuelas)", "actualizar_mag.py", "informe_mag.txt"),
    ("Actividad (INDEC): PIB desestacionalizado y EMAE", "actualizar_actividad.py", "informe_actividad.txt"),
    ("Pronóstico del PIB", "replicar_pib.py", "informe_pib.txt"),
    ("Pronóstico del tipo de cambio", "replicar_tc.py", "informe_tc.txt"),   # antes de la inflación: I usa el dólar esperado
    ("Licitaciones del Tesoro (colocaciones de deuda)", "actualizar_licitaciones.py", "informe_licitaciones.txt"),
    ("Pronóstico de la tasa (TAMAR)", "replicar_tasa.py", "informe_tasa.txt"),   # usa las licitaciones
    ("Pronóstico del resultado primario (IMIG)", "replicar_fiscal.py", "informe_fiscal.txt"),
    ("Estimaciones agrícolas (Ministerio de Agricultura)", "actualizar_estimaciones_agricolas.py", "informe_estimaciones_agricolas.txt"),
    ("Pronóstico de exportaciones e importaciones", "replicar_comercio.py", "informe_comercio.txt"),
    ("Réplica del REM y registro del pronóstico", "replicar_rem.py", "informe_replica_rem.txt"),   # usa todo lo anterior (~1,5 min)
    ("Pronóstico sellado (desde el día 26) y sellos de Bitcoin", "emitir_pronostico.py", "informe_sellos.txt"),
    ("Sitio web Datos Con Método", "publicar_web.py", "informe_web.txt"),   # después de todos los pronósticos
    ("Respaldo (OneDrive y disco F:)", "respaldo.py", "informe_respaldo.txt"),   # al final: copia lo actualizado hoy
    ("Subir scripts a GitHub (sin datos)", "subir_cambios.py", "informe_git.txt"),   # último: solo código y CLAUDE.md
]
LINEAS_CLAVE = ("Último relevamiento", "Último mes con dato", "Meses nuevos", "ALERTAS", "ERROR", "FALLA", "No se pudo",
                "Excel", "IPC descargado", "Brecha", "Series guardadas", "Sin cambios", "Serie encadenada", "Días guardados",
                "Sin días nuevos", "=== ÍNDICE SEPA", "Variación mensual", "Cambios de precio", "Histórico completo", "Pronóstico registrado", "Relevamientos evaluados", "Futuros guardados", "Curva del", "SEMÁFORO", "La canasta del súper", "A del mes en curso, sin", "A corregida hacia", "Respaldo terminado", "2) Disco de respaldo", "Sin cambios: no se sube", "Commit:", "Subido a", "FRENADO", "PRONÓSTICO DE HOY (TAMAR", "Colocaciones guardadas", "IMIG: versión nueva", "AVISO F0", "Sitio publicado", "Fuentes atrasadas", "Pronóstico sellado n.º", "Huella SHA-256", "PUBLICAR EN LINKEDIN", "anclado en Bitcoin")


def main():
    resumen = [f"=== RESUMEN GENERAL | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    peor = 0
    for nombre, script, informe in PASOS:
        try:
            archivo, *extra = script.split()
            r = subprocess.run([sys.executable, str(BASE / archivo), *extra, "--aviso"], cwd=str(BASE), capture_output=True,
                               timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            codigo = r.returncode
        except Exception as e:
            codigo = 2
            resumen += ["", f"## {nombre}: NO SE PUDO EJECUTAR ({type(e).__name__}: {e})"]
            peor = max(peor, codigo)
            continue
        peor = max(peor, codigo)
        estado = {0: "OK", 1: "CON ALERTAS", 2: "ERROR"}.get(codigo, f"código {codigo}")
        resumen += ["", f"## {nombre}: {estado}"]
        ruta = CARPETA / informe
        if ruta.exists():
            lineas = ruta.read_text(encoding="utf-8").splitlines()
            for linea in lineas:
                if linea.strip().startswith(LINEAS_CLAVE) or linea.startswith(LINEAS_CLAVE):
                    resumen.append("   " + linea.strip())
            if codigo != 0:
                resumen.append(f"   (detalle completo en datos\\{informe})")
        else:
            resumen.append(f"   (no se generó datos\\{informe})")
    resumen += ["", "RESULTADO GENERAL: " + {0: "todo en orden", 1: "hay alertas para revisar", 2: "hubo errores"}[min(peor, 2)]]
    texto = "\n".join(resumen)
    print(texto)
    CARPETA.mkdir(exist_ok=True)
    (CARPETA / "informe_general.txt").write_text(texto, encoding="utf-8")
    sys.exit(min(peor, 2))


if __name__ == "__main__":
    main()