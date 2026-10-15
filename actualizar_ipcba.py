# actualizar_ipcba.py
# Actualiza el IPCBA (IPC de la Ciudad de Buenos Aires, nivel general, base 2021=100, empalmado desde jul-2012)
# del Instituto de Estadística y Censos de la Ciudad.
#   1) intenta bajar el Excel desde la página de series empalmadas; si no puede, avisa y usa el archivo local
#   2) guarda una copia fechada en datos/copias si el archivo cambió, y reemplaza el Excel de datos/
#   3) controla: estructura, meses faltantes, fechas duplicadas, variaciones atípicas
#      y que los datos históricos NO hayan cambiado respecto de la corrida anterior
#   4) calcula variación mensual e interanual y guarda datos/ipcba.parquet
#   5) anota en datos/historial_ipcba.csv la fecha y hora en que aparece por primera vez cada mes
#
# Uso:
#   py -3.13 actualizar_ipcba.py                    -> descarga y actualiza
#   py -3.13 actualizar_ipcba.py --archivo X.xlsx   -> usa un Excel ya descargado (no intenta bajarlo)
#   pythonw.exe actualizar_ipcba.py --aviso         -> para el Programador de tareas (cartel solo si hay novedad)
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = error
import argparse
import hashlib
import io
import re
import sys
import urllib.request
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PAGINA = "https://www.estadisticaciudad.gob.ar/eyc/categoria-banco-datos/series-empalmadas/"
NOMBRE_EXCEL = "IPCBA_base_2021100-Nivel_general_empalme.xlsx"
# link directo conocido (oct-2026), se usa solo si no se encuentra el link en la página
URL_CONOCIDA = "https://www.estadisticaciudad.gob.ar/eyc/wp-content/uploads/2026/08/" + NOMBRE_EXCEL

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
COPIAS = CARPETA / "copias"
EXCEL = CARPETA / NOMBRE_EXCEL
SALIDA = CARPETA / "ipcba.parquet"
INFORME = CARPETA / "informe_ipcba.txt"
ALERTAS_CSV = CARPETA / "alertas_ipcba.csv"
HISTORIAL = CARPETA / "historial_ipcba.csv"                    # primera vez que aparece cada mes
HISTORIAL_CORRIDAS = CARPETA / "historial_corridas_ipcba.csv"  # una fila por corrida
HOY = datetime.now().strftime("%Y-%m-%d")

CONTROLES = {
    "P01": ("historico_modificado", "Un dato de un mes ya publicado cambió respecto de la corrida anterior (revisión o cambio de base)"),
    "P02": ("mes_faltante", "Falta un mes en la serie"),
    "P03": ("variacion_atipica", "Variación mensual menor a -1% o mayor a 35%"),
    "P04": ("dato_vacio", "Mes sin dato"),
    "P05": ("fecha_duplicada", "La misma fecha aparece más de una vez"),
    "P06": ("descarga_fallida", "No se pudo bajar el Excel de la página de la Ciudad: se usó el archivo local"),
}


class ErrorEstructura(Exception):
    pass


def pedir(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read(), r.headers.get("Last-Modified")


def descargar():
    """Busca el link al Excel en la página de series empalmadas y lo baja. Devuelve (bytes, url, Last-Modified)."""
    url = None
    try:
        html = pedir(PAGINA)[0].decode("utf-8", "replace")
        ficha = re.search(r'href="([^"]*/banco-datos/ipcba-base-2021-100-nivel-general-indice-mensual-empalmado[^"]*)"', html)
        if ficha:
            html = pedir(ficha.group(1))[0].decode("utf-8", "replace")
            link = re.search(r'href="([^"]+/' + re.escape(NOMBRE_EXCEL) + ')"', html)
            url = link.group(1) if link else None
    except Exception:
        url = None
    contenido, modificado = pedir(url or URL_CONOCIDA)
    if not contenido.startswith(b"PK"):
        raise ValueError("lo que se bajó no es un archivo Excel")
    return contenido, url or URL_CONOCIDA, modificado


def armar(contenido):
    """Lee el Excel: título en A1, encabezado 'Mes' en la columna A, luego mes (fecha) e índice (columna B)."""
    try:
        crudo = pd.read_excel(io.BytesIO(contenido), sheet_name=0, header=None)
    except Exception as e:
        raise ErrorEstructura(f"No se pudo leer el Excel del IPCBA ({e}).")
    titulo = str(crudo.iat[0, 0])
    if "IPCBA" not in titulo or "Nivel General" not in titulo:
        raise ErrorEstructura(f"El Excel no parece ser el IPCBA nivel general (título: '{titulo[:120]}').")
    filas_mes = crudo.index[crudo[0].astype(str).str.strip() == "Mes"]
    if len(filas_mes) == 0:
        raise ErrorEstructura("No se encontró la fila de encabezado 'Mes' en la columna A.")
    datos = crudo.loc[filas_mes[0] + 1:, [0, 1]]
    datos.columns = ["fecha_indice", "ipcba_indice"]
    datos["fecha_indice"] = pd.to_datetime(datos["fecha_indice"], errors="coerce")
    datos = datos.dropna(subset=["fecha_indice"])   # descarta notas al pie y filas vacías
    if datos.empty:
        raise ErrorEstructura("El Excel no tiene filas con fecha debajo del encabezado.")
    datos["ipcba_indice"] = pd.to_numeric(datos["ipcba_indice"], errors="coerce")
    return datos.reset_index(drop=True), titulo


def controlar(df, anterior):
    """Devuelve lista de alertas. Cada una: codigo, mes, detalle."""
    al = []

    def sumar(cod, mes, det):
        al.append({"codigo": cod, "mes": str(mes)[:7], "detalle": det})

    for f in df.loc[df["fecha_indice"].duplicated(keep=False), "fecha_indice"].drop_duplicates():
        sumar("P05", f, "fecha repetida")
    unica = df.drop_duplicates("fecha_indice").sort_values("fecha_indice")
    esperado = pd.date_range(unica["fecha_indice"].min(), unica["fecha_indice"].max(), freq="MS")
    for f in esperado.difference(pd.DatetimeIndex(unica["fecha_indice"])):
        sumar("P02", f, "mes ausente en la serie")
    for f in unica.loc[unica["ipcba_indice"].isna(), "fecha_indice"]:
        sumar("P04", f, "sin dato del índice")
    v = unica["ipcba_var_mensual"]
    for f, x in zip(unica.loc[(v < -1) | (v > 35), "fecha_indice"], v[(v < -1) | (v > 35)]):
        sumar("P03", f, f"variación mensual {x:.2f}%")
    if anterior is not None:
        m = anterior.merge(unica, on="fecha_indice", suffixes=("_ant", ""))
        c = "ipcba_indice"
        dif = ((m[c] - m[c + "_ant"]).abs() > 1e-6 * m[c + "_ant"].abs().clip(lower=1)) & m[c].notna() & m[c + "_ant"].notna()
        for _, r in m[dif].iterrows():
            sumar("P01", r["fecha_indice"], f"índice: antes {r[c + '_ant']}, ahora {r[c]}")
    return al


def registrar_meses(meses, ruta, servidor=None):
    """Anota en el historial los meses que aparecen por primera vez. La primera corrida los marca como carga_inicial
    (no se sabe cuándo aparecieron de verdad); desde ahí, como observado. Devuelve la lista de meses agregados."""
    previo = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig") if ruta.exists() else None
    vistos = set(previo["mes"]) if previo is not None else set()
    nuevos = [m for m in sorted(meses) if m not in vistos]
    if nuevos:
        pd.DataFrame({"mes": nuevos, "primera_vez_visto": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "tipo": "observado" if previo is not None else "carga_inicial",
                      "archivo_servidor_modificado": servidor or ""}).to_csv(
            ruta, mode="a", header=previo is None, index=False, encoding="utf-8-sig")
    return nuevos


def avisar(codigo, ultimo, nuevos, alertas, informe):
    if codigo == 0 and not nuevos:
        return
    clave = f"{codigo}|{ultimo}|{nuevos}|" + ",".join(sorted(f"{a['codigo']}:{a['mes']}" for a in alertas))
    marca = CARPETA / "ultimo_aviso_ipcba.txt"
    if marca.exists() and marca.read_text(encoding="utf-8") == clave:
        return
    marca.write_text(clave, encoding="utf-8")
    if codigo == 0:
        titulo, icono = "IPCBA: todo OK", 0x40
        msg = f"Se cargó el IPCBA de {ultimo}. Sin errores."
    elif codigo == 1:
        titulo, icono = "IPCBA: hay errores para revisar", 0x30
        msg = (f"Hay {len(alertas)} alertas (tipos: {', '.join(sorted({a['codigo'] for a in alertas}))}).\n\n"
               "Abrí datos\\informe_ipcba.txt o pedile a Claude que lo revise.")
    else:
        titulo, icono = "IPCBA: no se pudo actualizar", 0x10
        msg = "\n".join(informe[-5:])[:600]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, icono | 0x1000)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Actualiza el IPCBA (Ciudad de Buenos Aires)")
    ap.add_argument("--archivo", help="usar este Excel en lugar de bajarlo")
    ap.add_argument("--aviso", action="store_true", help="cartel de Windows solo si hay novedades")
    args = ap.parse_args()

    informe = [f"=== ACTUALIZACIÓN IPCBA (CABA) | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    codigo, alertas, ultimo, agregados = 0, [], "-", []
    try:
        CARPETA.mkdir(exist_ok=True)
        COPIAS.mkdir(exist_ok=True)
        servidor, descarga_fallida = None, None
        if args.archivo:
            contenido = Path(args.archivo).read_bytes()
            origen = f"archivo indicado ({args.archivo})"
        else:
            try:
                contenido, url, servidor = descargar()
                origen = f"Excel descargado de la página de la Ciudad ({url})"
            except Exception as e:
                descarga_fallida = f"{type(e).__name__}: {e}"
                if not EXCEL.exists():
                    informe.append(f"No se pudo bajar el Excel ({descarga_fallida}) y no hay copia local en datos\\{NOMBRE_EXCEL}.")
                    raise ErrorEstructura("descarga fallida y sin archivo local")
                contenido = EXCEL.read_bytes()
                origen = f"archivo local datos\\{NOMBRE_EXCEL}"
                informe.append(f"No se pudo bajar el Excel de la página de la Ciudad ({descarga_fallida}). Se usa el archivo local.")
        df, titulo = armar(contenido)   # si la estructura no es la esperada, se corta acá sin tocar nada

        huella = hashlib.sha256(contenido).hexdigest()
        if any(hashlib.sha256(p.read_bytes()).hexdigest() == huella for p in COPIAS.glob("ipcba_*.xlsx")):
            informe.append(f"IPCBA {origen}: idéntico a una copia anterior.")
        else:
            destino = COPIAS / f"ipcba_{HOY}.xlsx"
            n = 2
            while destino.exists():
                destino = COPIAS / f"ipcba_{HOY}_{n}.xlsx"
                n += 1
            destino.write_bytes(contenido)
            informe.append(f"IPCBA {origen}. Copia guardada: {destino.relative_to(BASE)}")
        if servidor:
            informe.append(f"Archivo modificado en el servidor: {servidor}")
        if not args.archivo and descarga_fallida is None and (not EXCEL.exists() or EXCEL.read_bytes() != contenido):
            EXCEL.write_bytes(contenido)   # la versión anterior queda en datos/copias
            informe.append(f"Excel actualizado: datos\\{NOMBRE_EXCEL}")

        df = df.sort_values("fecha_indice").reset_index(drop=True)
        df["ipcba_var_mensual"] = (df["ipcba_indice"] / df["ipcba_indice"].shift(1) - 1) * 100
        df["ipcba_var_ia"] = (df["ipcba_indice"] / df["ipcba_indice"].shift(12) - 1) * 100
        df["mes"] = (df["fecha_indice"] + pd.offsets.MonthEnd(0)).dt.date  # fin de mes: igual que el REM y el INDEC
        anterior = None
        if SALIDA.exists():
            try:
                anterior = pd.read_parquet(SALIDA, columns=["fecha_indice", "ipcba_indice"])
            except Exception:
                anterior = None
        alertas = controlar(df, anterior)
        if descarga_fallida:
            alertas.append({"codigo": "P06", "mes": "", "detalle": descarga_fallida})

        con_dato = df.dropna(subset=["ipcba_indice"])
        ult = con_dato.iloc[-1]
        ultimo = ult["fecha_indice"].strftime("%Y-%m")
        meses_prev = set(anterior["fecha_indice"]) if anterior is not None else set()
        nuevos_parquet = [f.strftime("%Y-%m") for f in df["fecha_indice"] if f not in meses_prev] if anterior is not None else []

        out = df[["mes", "fecha_indice", "ipcba_indice", "ipcba_var_mensual", "ipcba_var_ia"]].copy()
        out["alerta_fuente"] = out["fecha_indice"].map(
            lambda f: ";".join(sorted({a["codigo"] for a in alertas if a["mes"] == str(f)[:7]})))
        out.to_parquet(SALIDA, index=False)
        agregados = registrar_meses([str(m) for m in con_dato["mes"]], HISTORIAL, servidor)

        informe.append(f"Meses cargados: {len(df)} ({df['fecha_indice'].min():%Y-%m} a {df['fecha_indice'].max():%Y-%m})")
        informe.append(f"Último mes con dato: {ultimo}  |  IPCBA: var. mensual {ult['ipcba_var_mensual']:.2f}%, "
                       f"interanual {ult['ipcba_var_ia']:.1f}%")
        if anterior is None:
            informe.append("Primera carga de esta serie.")
        elif nuevos_parquet:
            informe.append(f"Meses nuevos desde la corrida anterior: {', '.join(nuevos_parquet)}  <-- NUEVO")
        else:
            informe.append("Sin meses nuevos desde la corrida anterior.")
        if agregados:
            informe.append(f"Historial de primera aparición: se anotaron {len(agregados)} meses en datos\\historial_ipcba.csv")
        informe.append("Archivo generado: datos\\ipcba.parquet")
        informe.append("")
        informe.append("Controles:")
        for cod, (nombre, desc) in CONTROLES.items():
            informe.append(f"  {cod} {nombre}: {sum(a['codigo'] == cod for a in alertas)} casos  ({desc})")
        informe.append("")
        informe.append("=" * 60)
        if alertas:
            codigo = 1
            pd.DataFrame(alertas).to_csv(ALERTAS_CSV, index=False, encoding="utf-8-sig")
            informe.append(f"ALERTAS: {len(alertas)} para revisar (detalle en datos\\alertas_ipcba.csv)")
            for a in alertas[:15]:
                informe.append(f"  - {a['codigo']} {CONTROLES[a['codigo']][0]} | {a['mes']} | {a['detalle']}")
            if len(alertas) > 15:
                informe.append(f"  ... y {len(alertas) - 15} más")
            informe.append("  Las filas afectadas quedan marcadas en la columna alerta_fuente. No se corrigió ni se borró nada.")
        else:
            informe.append("ALERTAS: ninguna. Todo en orden.")
            if ALERTAS_CSV.exists():
                ALERTAS_CSV.unlink()
        fila = pd.DataFrame([{"fecha_corrida": datetime.now().strftime("%Y-%m-%d %H:%M"), "ultimo_mes": ultimo,
                              "meses_nuevos": len(nuevos_parquet), "alertas": len(alertas)}])
        fila.to_csv(HISTORIAL_CORRIDAS, mode="a", header=not HISTORIAL_CORRIDAS.exists(), index=False, encoding="utf-8-sig")
    except ErrorEstructura as e:
        informe += ["", "=" * 60, f"ERROR: {e}", "No se modificó ningún archivo de salida."]
        codigo = 2
    except Exception as e:
        informe += ["", "=" * 60, f"FALLA INESPERADA: {type(e).__name__}: {e}"]
        codigo = 2

    texto = "\n".join(informe)
    print(texto)
    CARPETA.mkdir(exist_ok=True)
    INFORME.write_text(texto, encoding="utf-8")
    if args.aviso:
        avisar(codigo, ultimo, bool(agregados), alertas, informe)
    sys.exit(codigo)


if __name__ == "__main__":
    main()
