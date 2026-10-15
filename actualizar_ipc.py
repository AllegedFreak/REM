# actualizar_ipc.py
# Actualiza el IPC del INDEC (nivel general y núcleo, base dic-2016) bajándolo DIRECTO del INDEC
# (serie_ipc_divisiones.csv, región Nacional). Hasta el 14-oct-2026 se usaba la API de datos.gob.ar, que publica
# el dato más tarde (ese día el INDEC ya tenía septiembre y datos.gob.ar no).
#   1) descarga y guarda una copia fechada en datos/copias
#   2) controla: estructura, meses faltantes, fechas duplicadas, variaciones atípicas
#      y que los datos históricos NO hayan cambiado respecto de la corrida anterior
#   3) calcula variación mensual e interanual y guarda datos/ipc_indec.parquet
#   4) informa el último mes cargado y las alertas
#   5) anota en datos/historial_ipc.csv la fecha y hora en que aparece por primera vez cada mes
#
# Uso:
#   py -3.13 actualizar_ipc.py                    -> descarga y actualiza
#   py -3.13 actualizar_ipc.py --archivo X.csv    -> usa un serie_ipc_divisiones.csv ya descargado
#   pythonw.exe actualizar_ipc.py --aviso         -> para el Programador de tareas (cartel solo si hay novedad)
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = error
import argparse
import hashlib
import io
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

SERIES = {  # código en el archivo del INDEC (columna Codigo) -> nombre de columna
    "0": "ipc_general_indice",
    "Núcleo": "ipc_nucleo_indice",
}
URL = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv"
TOLERANCIA_REVISION = 1e-5   # cambio relativo mínimo para considerar "modificado" un dato viejo (evita alertas por redondeo)

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
COPIAS = CARPETA / "copias"
SALIDA = CARPETA / "ipc_indec.parquet"
INFORME = CARPETA / "informe_ipc.txt"
ALERTAS_CSV = CARPETA / "alertas_ipc.csv"
HISTORIAL = CARPETA / "historial_ipc.csv"                    # primera vez que aparece cada mes
HISTORIAL_CORRIDAS = CARPETA / "historial_corridas_ipc.csv"  # una fila por corrida
HOY = datetime.now().strftime("%Y-%m-%d")

# Controles (cada uno es un tipo de error que el script sabe detectar)
CONTROLES = {
    "I01": ("historico_modificado", "Un dato de un mes ya publicado cambió respecto de la corrida anterior (revisión o cambio de base)"),
    "I02": ("mes_faltante", "Falta un mes en la serie"),
    "I03": ("variacion_atipica", "Variación mensual menor a -1% o mayor a 35%"),
    "I04": ("dato_vacio", "Mes sin dato en alguna de las dos series"),
    "I05": ("fecha_duplicada", "La misma fecha aparece más de una vez"),
}


class ErrorEstructura(Exception):
    pass


def obtener(archivo_local, informe):
    CARPETA.mkdir(exist_ok=True)
    COPIAS.mkdir(exist_ok=True)
    if archivo_local:
        contenido = Path(archivo_local).read_bytes()
        origen = f"archivo local ({archivo_local})"
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                contenido = r.read()
        except Exception as e:
            informe.append(f"No se pudo descargar el IPC del INDEC ({e}).")
            raise ErrorEstructura("descarga fallida")
        origen = "descargado del INDEC (serie_ipc_divisiones.csv)"
    huella = hashlib.sha256(contenido).hexdigest()
    ya = any(hashlib.sha256(p.read_bytes()).hexdigest() == huella for p in COPIAS.glob("ipc_indec_*.csv"))
    if ya:
        informe.append(f"IPC {origen}: idéntico a una copia anterior, el INDEC no publicó datos nuevos.")
    else:
        destino = COPIAS / f"ipc_indec_{HOY}.csv"
        n = 2
        while destino.exists():
            destino = COPIAS / f"ipc_indec_{HOY}_{n}.csv"
            n += 1
        destino.write_bytes(contenido)
        informe.append(f"IPC {origen}. Copia guardada: {destino.relative_to(BASE)}")
    return contenido


def armar(contenido):
    try:
        crudo = pd.read_csv(io.StringIO(contenido.decode("latin-1")), sep=";", decimal=",")
        faltan = {"Codigo", "Periodo", "Indice_IPC", "Region"} - set(crudo.columns)
        if faltan:
            raise ValueError(f"faltan las columnas {sorted(faltan)}")
        nac = crudo[(crudo["Region"] == "Nacional") & crudo["Codigo"].astype(str).isin(SERIES)]
        df = nac.pivot_table(index="Periodo", columns=nac["Codigo"].astype(str), values="Indice_IPC", aggfunc="first")
        df = df.rename(columns=SERIES).reset_index()
    except Exception as e:
        raise ErrorEstructura(f"El formato del archivo del INDEC cambió ({e}). Pegale este mensaje a Claude.")
    if df.empty or set(SERIES.values()) - set(df.columns):
        raise ErrorEstructura("El archivo del INDEC no trae el nivel general y la núcleo nacionales.")
    df["fecha_indice"] = pd.to_datetime(df["Periodo"].astype(str), format="%Y%m", errors="coerce")
    if df["fecha_indice"].isna().any():
        raise ErrorEstructura("Hay períodos ilegibles en el archivo del INDEC.")
    for c in SERIES.values():
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["fecha_indice"] + list(SERIES.values())]


def controlar(df, anterior):
    """Devuelve lista de alertas. Cada una: codigo, mes, detalle."""
    al = []

    def sumar(cod, mes, det):
        al.append({"codigo": cod, "mes": str(mes)[:7], "detalle": det})

    for f in df.loc[df["fecha_indice"].duplicated(keep=False), "fecha_indice"].drop_duplicates():
        sumar("I05", f, "fecha repetida")
    unica = df.drop_duplicates("fecha_indice").sort_values("fecha_indice")
    esperado = pd.date_range(unica["fecha_indice"].min(), unica["fecha_indice"].max(), freq="MS")
    for f in esperado.difference(pd.DatetimeIndex(unica["fecha_indice"])):
        sumar("I02", f, "mes ausente en la serie")
    for c in SERIES.values():
        for f in unica.loc[unica[c].isna(), "fecha_indice"]:
            sumar("I04", f, f"sin dato en {c}")
        v = unica[c + "_var_mensual"]
        for f, x in zip(unica.loc[(v < -1) | (v > 35), "fecha_indice"], v[(v < -1) | (v > 35)]):
            sumar("I03", f, f"{c}: variación mensual {x:.2f}%")
    if anterior is not None:
        m = anterior.merge(unica, on="fecha_indice", suffixes=("_ant", ""))
        for c in SERIES.values():
            dif = ((m[c] - m[c + "_ant"]).abs() > TOLERANCIA_REVISION * m[c + "_ant"].abs().clip(lower=1)) & m[c].notna() & m[c + "_ant"].notna()
            for _, r in m[dif].iterrows():
                sumar("I01", r["fecha_indice"], f"{c}: antes {r[c + '_ant']}, ahora {r[c]}")
    return al


def registrar_meses(meses, ruta):
    """Anota en el historial los meses que aparecen por primera vez. La primera corrida los marca como carga_inicial
    (no se sabe cuándo aparecieron de verdad); desde ahí, como observado. Devuelve la lista de meses agregados."""
    previo = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig") if ruta.exists() else None
    vistos = set(previo["mes"]) if previo is not None else set()
    nuevos = [m for m in sorted(meses) if m not in vistos]
    if nuevos:
        pd.DataFrame({"mes": nuevos, "primera_vez_visto": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "tipo": "observado" if previo is not None else "carga_inicial"}).to_csv(
            ruta, mode="a", header=previo is None, index=False, encoding="utf-8-sig")
    return nuevos


def avisar(codigo, ultimo, nuevos, alertas, informe):
    if codigo == 0 and not nuevos:
        return
    clave = f"{codigo}|{ultimo}|{nuevos}|" + ",".join(sorted(f"{a['codigo']}:{a['mes']}" for a in alertas))
    marca = CARPETA / "ultimo_aviso_ipc.txt"
    if marca.exists() and marca.read_text(encoding="utf-8") == clave:
        return
    marca.write_text(clave, encoding="utf-8")
    if codigo == 0:
        titulo, icono = "IPC INDEC: todo OK", 0x40
        msg = f"Se cargó el IPC de {ultimo}. Sin errores."
    elif codigo == 1:
        titulo, icono = "IPC INDEC: hay errores para revisar", 0x30
        msg = (f"Hay {len(alertas)} alertas (tipos: {', '.join(sorted({a['codigo'] for a in alertas}))}).\n\n"
               "Abrí datos\\informe_ipc.txt o pedile a Claude que lo revise.")
    else:
        titulo, icono = "IPC INDEC: no se pudo actualizar", 0x10
        msg = "\n".join(informe[-5:])[:600]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, icono | 0x1000)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Actualiza el IPC del INDEC")
    ap.add_argument("--archivo", help="usar este JSON en lugar de descargarlo")
    ap.add_argument("--aviso", action="store_true", help="cartel de Windows solo si hay novedades")
    args = ap.parse_args()

    informe = [f"=== ACTUALIZACIÓN IPC INDEC | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    codigo, alertas, ultimo, nuevos = 0, [], "-", False
    try:
        df = armar(obtener(args.archivo, informe))
        df = df.sort_values("fecha_indice").reset_index(drop=True)
        for c in SERIES.values():
            df[c + "_var_mensual"] = (df[c] / df[c].shift(1) - 1) * 100
            df[c + "_var_ia"] = (df[c] / df[c].shift(12) - 1) * 100
        df["mes"] = (df["fecha_indice"] + pd.offsets.MonthEnd(0)).dt.date  # fin de mes: igual que el REM
        anterior = None
        if SALIDA.exists():
            try:
                anterior = pd.read_parquet(SALIDA, columns=["fecha_indice"] + list(SERIES.values()))
            except Exception:
                anterior = None
        alertas = controlar(df, anterior)

        ult = df.dropna(subset=list(SERIES.values())).iloc[-1]
        ultimo = ult["fecha_indice"].strftime("%Y-%m")
        meses_prev = set(anterior["fecha_indice"]) if anterior is not None else set()
        agregados = [f.strftime("%Y-%m") for f in df["fecha_indice"] if f not in meses_prev] if anterior is not None else []
        nuevos = bool(agregados)

        out = df.copy()
        out["alerta_fuente"] = out["fecha_indice"].map(
            lambda f: ";".join(sorted({a["codigo"] for a in alertas if a["mes"] == str(f)[:7]})))
        out.to_parquet(SALIDA, index=False)
        anotados = registrar_meses([str(m) for m in df.dropna(subset=["ipc_general_indice"])["mes"]], HISTORIAL)

        informe.append(f"Meses cargados: {len(df)} ({df['fecha_indice'].min():%Y-%m} a {df['fecha_indice'].max():%Y-%m})")
        informe.append(f"Último mes con dato: {ultimo}  |  IPC general: var. mensual {ult['ipc_general_indice_var_mensual']:.2f}%, "
                       f"interanual {ult['ipc_general_indice_var_ia']:.1f}%  |  núcleo: mensual {ult['ipc_nucleo_indice_var_mensual']:.2f}%")
        if anterior is None:
            informe.append("Primera carga de esta serie.")
        elif agregados:
            informe.append(f"Meses nuevos desde la corrida anterior: {', '.join(agregados)}  <-- NUEVO")
        else:
            informe.append("Sin meses nuevos desde la corrida anterior.")
        if anotados:
            informe.append(f"Historial de primera aparición: se anotaron {len(anotados)} meses en datos\\historial_ipc.csv")
        informe.append(f"Archivo generado: datos\\ipc_indec.parquet")
        informe.append("")
        informe.append("Controles:")
        for cod, (nombre, desc) in CONTROLES.items():
            informe.append(f"  {cod} {nombre}: {sum(a['codigo'] == cod for a in alertas)} casos  ({desc})")
        informe.append("")
        informe.append("=" * 60)
        if alertas:
            codigo = 1
            pd.DataFrame(alertas).to_csv(ALERTAS_CSV, index=False, encoding="utf-8-sig")
            informe.append(f"ALERTAS: {len(alertas)} para revisar (detalle en datos\\alertas_ipc.csv)")
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
                              "meses_nuevos": len(agregados), "alertas": len(alertas)}])
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
        avisar(codigo, ultimo, nuevos, alertas, informe)
    sys.exit(codigo)


if __name__ == "__main__":
    main()