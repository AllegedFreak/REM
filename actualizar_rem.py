# actualizar_rem.py
# Actualiza el REM del BCRA de punta a punta:
#   1) descarga el Excel histórico y guarda una copia fechada (datos/copias)
#   2) verifica que el Excel tenga la estructura esperada
#   3) aplica las correcciones verificadas de datos/correcciones_rem.csv
#   4) normaliza (fechas desde/hasta, horizonte, nombres) y guarda Parquet
#   5) revisa errores, informa los tipos de error resueltos y avisa de los nuevos
#
# Uso:
#   py -3.13 actualizar_rem.py                      -> descarga y actualiza
#   py -3.13 actualizar_rem.py --archivo X.xlsx     -> usa un Excel ya descargado
#   py -3.13 actualizar_rem.py --aceptar-variables  -> da por conocidas las variables nuevas
#   pythonw.exe actualizar_rem.py --aviso           -> para el Programador de tareas de Windows: corre en silencio
#                                                      y muestra un cartel solo si hay relevamiento nuevo, alertas o error
#
# Código de salida: 0 = todo en orden | 1 = hay alertas para revisar | 2 = error de estructura
import argparse
import hashlib
import re
import shutil
import sys
import unicodedata
import urllib.request
import warnings
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

URL = ("https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/"
       "relevamiento-expectativas-mercado-historico.xlsx")

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
COPIAS = CARPETA / "copias"
EXCEL = CARPETA / "rem_historico.xlsx"
CORRECCIONES = CARPETA / "correcciones_rem.csv"
REGISTRO = CARPETA / "tipos_error_rem.csv"
VARIABLES_CONOCIDAS = CARPETA / "variables_conocidas.csv"
INFORME = CARPETA / "informe_actualizacion.txt"
HISTORIAL = CARPETA / "historial_actualizaciones.csv"
ALERTAS_CSV = CARPETA / "alertas_rem.csv"

HOJAS = {  # hoja del Excel -> nombre del archivo Parquet de salida
    "Base de Datos Completa": "rem_normalizada",
    "Base Completa TOP-10": "rem_top10_normalizada",
}

# ---------------------------------------------------------------------------
# Catálogo de tipos de error.
# E = tipos ya resueltos (cada caso se corrige solo o con una línea en correcciones_rem.csv)
# N = tipos que el script sabe detectar pero que todavía no se resolvieron: aparecen como alerta
# Los tipos nuevos se suman solos a datos/tipos_error_rem.csv la primera vez que se detectan.
# ---------------------------------------------------------------------------
TIPOS_ERROR = {
    "E01": ("horizonte_inconsistente",
            "'Próx. N meses' con un N que no coincide con el mes de la referencia (error de tipeo del BCRA)",
            "Corrección manual en correcciones_rem.csv, verificada con 3 fuentes", "resuelto"),
    "E02": ("periodo_duplicado",
            "Dos filas con la misma clave y distinto valor: una está mal rotulada",
            "Corrección manual en correcciones_rem.csv usando filtro_mediana", "resuelto"),
    "E03": ("anio_como_texto",
            "Años guardados como texto ('2025') en vez de número",
            "Automático: se interpreta como año", "resuelto"),
    "E04": ("anio_como_numero",
            "Período anual guardado como número (2026) y no como fecha",
            "Automático: desde 01/01 hasta 31/12", "resuelto"),
    "E05": ("variable_mayusculas_distintas",
            "Misma variable escrita con mayúsculas distintas ('Resultado Primario' / 'Resultado primario')",
            "Automático: se unifican en una sola variable_std", "resuelto"),
    "E06": ("ipc_gba_descartado",
            "IPC GBA: serie vieja que termina en 2017",
            "Automático: se descarta", "resuelto"),
    "E07": ("periodo_mensual_y_anual_coinciden",
            "Pronóstico mensual de diciembre y anual caen en la misma fecha y parecían duplicados",
            "Automático: la clave incluye tipo_periodo", "resuelto"),
    "E08": ("periodo_con_minusculas",
            "Período escrito con otra capitalización ('próx. 12 meses' en vez de 'Próx. 12 meses')",
            "Automático: se ignoran mayúsculas/minúsculas", "resuelto"),
    "E09": ("anio_como_fecha_serial",
            "Año (2023) guardado como número con formato de fecha: Excel lo lee como 15/07/1905",
            "Automático: se recupera el año", "resuelto"),
    "N06": ("anual_con_referencia_trimestral",
            "Período anual en una variable cuya referencia es trimestral (ej. PIB 'var. % trim. s.e.' con período 2023)",
            "Pendiente: ver la celda y decidir a qué trimestre corresponde", "pendiente"),
    "N07": ("horizonte_fuera_de_rango",
            "Horizonte menor a -12 o mayor a 48 meses (fecha mal leída)",
            "Pendiente: ver la celda", "pendiente"),
    "E10": ("fila_copia_exacta",
            "Fila idéntica a su gemela salvo la referencia (ej. PIB 'trim. s.e.' copia de 'prom. anual')",
            "Corrección en correcciones_rem.csv con campo excluir_si_copia: el script verifica que todos los valores coincidan; "
            "la fila se saca del Parquet y queda en filas_excluidas_rem.csv", "resuelto"),
    "N08": ("copia_no_identica",
            "Se pidió excluir una fila por ser copia de su gemela, pero los valores NO coinciden: puede ser un dato real",
            "Pendiente: comparar ambas filas en el Excel; la fila se conserva", "pendiente"),
    "N02": ("variable_nueva",
            "Aparece una variable que no estaba en variables_conocidas.csv",
            "Pendiente: revisar y, si corresponde, --aceptar-variables", "pendiente"),
    "N03": ("periodo_sin_fecha",
            "Período con un formato desconocido o del que no se pudo deducir la fecha",
            "Pendiente: ver la celda y decidir cómo interpretarla", "pendiente"),
    "N04": ("salto_orden_magnitud",
            "El valor cambia más de 10 veces respecto del relevamiento anterior (posible cambio de unidad)",
            "Pendiente: comparar con el informe PDF del BCRA", "pendiente"),
    "N05": ("fecha_relevamiento_invalida",
            "La fecha del relevamiento no se puede leer",
            "Pendiente: ver la celda", "pendiente"),
}

HOY = datetime.now().strftime("%Y-%m-%d")


class ErrorEstructura(Exception):
    pass


# ---------------------------------------------------------------- utilidades de texto
def nfc(s):
    return unicodedata.normalize("NFC", str(s)).strip()


def sin_acentos(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def canon_periodo(v):
    """Pasa el contenido de la celda Período a un texto canónico y dice qué tipo de celda era."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "", "vacio"
    if isinstance(v, (pd.Timestamp, datetime)):
        if v.year < 1950:  # un año (2023) guardado como número con formato fecha: Excel lo muestra como 1905
            serie = (pd.Timestamp(v) - pd.Timestamp("1899-12-30")).days
            if 1990 <= serie <= 2100:
                return str(serie), "fecha_serial"
        return v.strftime("%Y-%m-%d"), "fecha"
    if isinstance(v, bool):
        return str(v), "texto"
    if isinstance(v, (int, float)):
        return (str(int(v)) if float(v).is_integer() else str(v)), "numero"
    return re.sub(r"\s+", " ", nfc(v)), "texto"


# ---------------------------------------------------------------- descarga y copia fechada
def obtener_excel(archivo_local, informe):
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
            informe.append(f"No se pudo descargar el Excel del BCRA ({e}).")
            informe.append("Descargalo desde el navegador, guardalo como datos\\rem_historico.xlsx "
                           "y ejecutá de nuevo con:  py -3.13 actualizar_rem.py --archivo datos\\rem_historico.xlsx")
            raise ErrorEstructura("descarga fallida")
        origen = "descargado del BCRA"
    huella = hashlib.sha256(contenido).hexdigest()
    ya_guardada = any(hashlib.sha256(p.read_bytes()).hexdigest() == huella for p in COPIAS.glob("rem_historico_*.xlsx"))
    EXCEL.write_bytes(contenido)
    if ya_guardada:
        informe.append(f"Excel {origen} ({len(contenido) / 1024:.0f} KB): idéntico a una copia anterior, "
                       "el BCRA no publicó cambios.")
    else:
        destino = COPIAS / f"rem_historico_{HOY}.xlsx"
        n = 2
        while destino.exists():
            destino = COPIAS / f"rem_historico_{HOY}_{n}.xlsx"
            n += 1
        destino.write_bytes(contenido)
        informe.append(f"Excel {origen} ({len(contenido) / 1024:.0f} KB). Copia guardada: {destino.relative_to(BASE)}")


# ---------------------------------------------------------------- lectura y verificación de estructura
CANON = {
    "fecha de pronostico": "fecha_relevamiento", "variable": "variable_original",
    "referencia": "referencia", "periodo": "periodo_raw", "mediana": "mediana",
    "promedio": "promedio", "desvio": "desvio", "maximo": "maximo", "minimo": "minimo",
    "percentil 90": "percentil_90", "percentil 75": "percentil_75",
    "percentil 25": "percentil_25", "percentil 10": "percentil_10",
}
NUMERICAS = ["mediana", "promedio", "desvio", "maximo", "minimo", "percentil_90",
             "percentil_75", "percentil_25", "percentil_10", "cantidad_de_participantes"]


def leer_hoja(hoja):
    hojas = pd.ExcelFile(EXCEL).sheet_names
    if hoja not in hojas:
        raise ErrorEstructura(f"Falta la hoja '{hoja}'. Hojas que trae el Excel: {hojas}")
    bruto = pd.read_excel(EXCEL, sheet_name=hoja, header=None)
    if len(bruto) < 3:
        raise ErrorEstructura(f"La hoja '{hoja}' casi no tiene filas ({len(bruto)}).")
    posiciones = {}
    for pos, h in enumerate(bruto.iloc[1].tolist()):
        h = sin_acentos(h).lower().strip()
        if h in CANON:
            posiciones[CANON[h]] = pos
        elif h.startswith("cantidad de participa"):
            posiciones["cantidad_de_participantes"] = pos
    esperadas = list(CANON.values()) + ["cantidad_de_participantes"]
    faltan = [c for c in esperadas if c not in posiciones]
    if faltan:
        raise ErrorEstructura(f"En la hoja '{hoja}' (fila 2 de encabezados) faltan las columnas: {faltan}. "
                              f"Encabezados que trae: {bruto.iloc[1].tolist()}")
    datos = bruto.iloc[2:]
    datos = datos[datos[posiciones["variable_original"]].notna() | datos[posiciones["fecha_relevamiento"]].notna()]
    df = pd.DataFrame({"fila_excel": datos.index + 1})
    df["fecha_relevamiento"] = pd.to_datetime(datos[posiciones["fecha_relevamiento"]].values, errors="coerce")
    df["variable_original"] = [nfc(v) for v in datos[posiciones["variable_original"]]]
    df["referencia"] = [nfc(v) for v in datos[posiciones["referencia"]]]
    pares = [canon_periodo(v) for v in datos[posiciones["periodo_raw"]]]
    df["periodo_original"] = [p[0] for p in pares]
    df["tipo_celda"] = [p[1] for p in pares]
    for c in NUMERICAS:
        df[c] = pd.to_numeric(datos[posiciones[c]].values, errors="coerce")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- correcciones verificadas
def cargar_correcciones():
    if not CORRECCIONES.exists():
        return None
    c = pd.read_csv(CORRECCIONES, dtype=str, encoding="utf-8-sig").fillna("")
    req = ["fecha_relevamiento", "variable_original", "referencia", "filtro_mediana", "campo",
           "valor_erroneo", "valor_correcto"]
    faltan = [k for k in req if k not in c.columns]
    if faltan:
        raise ErrorEstructura(f"correcciones_rem.csv: faltan las columnas {faltan}")
    for col in c.columns:
        c[col] = c[col].map(nfc)
    if "tipo_error" not in c.columns:
        c["tipo_error"] = ""
    return c


EXCLUIDAS = []  # filas sacadas por excluir_si_copia (se guardan en filas_excluidas_rem.csv)


def aplicar_correcciones(df, correcciones, hoja):
    df["excluir"] = False
    df["periodo_fuente"] = df["periodo_original"]
    df["correccion_aplicada"] = ""
    resultados = []
    if correcciones is None:
        return resultados
    for i, r in correcciones.iterrows():
        cid = f"C{i + 1:02d}"
        info = {"id": cid, "tipo_error": r["tipo_error"] or "sin_tipo",
                "descripcion": f"{r['variable_original']} | {r['referencia']} | {r['fecha_relevamiento']}",
                "filas": 0, "estado": ""}
        if r.get("hoja", "") not in ("", hoja):
            info["estado"] = "no corresponde a esta hoja"
            resultados.append(info)
            continue
        if r["campo"] == "excluir_si_copia":
            base = ((df["fecha_relevamiento"] == pd.Timestamp(r["fecha_relevamiento"]))
                    & (df["variable_original"] == r["variable_original"])
                    & (df["periodo_original"] == r["valor_erroneo"]))
            cand = df[base & (df["referencia"] == r["referencia"])]
            gem = df[base & (df["referencia"] == r["valor_correcto"])]
            if len(cand) == 0:
                info["estado"] = "no aplicada: no hay filas con ese dato (¿el BCRA ya lo corrigió?)"
            elif len(cand) > 1 or len(gem) != 1:
                info["estado"] = (f"NO aplicada: {len(cand)} filas a excluir y {len(gem)} gemelas "
                                  f"(se esperaba 1 y 1)")
            else:
                a, b = cand.iloc[0], gem.iloc[0]
                difieren = [c for c in NUMERICAS
                            if not (pd.isna(a[c]) and pd.isna(b[c]))
                            and not (pd.notna(a[c]) and pd.notna(b[c]) and abs(a[c] - b[c]) <= 1e-9 * max(1, abs(a[c])))]
                if not difieren:
                    df.loc[cand.index, "excluir"] = True
                    df.loc[cand.index, "correccion_aplicada"] = cid
                    info["filas"] = 1
                    info["estado"] = f"aplicada (fila {int(a['fila_excel'])} idéntica a la fila {int(b['fila_excel'])}: excluida)"
                    EXCLUIDAS.append({"hoja": hoja, "fila_excel": int(a["fila_excel"]),
                                      "fila_gemela": int(b["fila_excel"]), "fecha_relevamiento": str(a["fecha_relevamiento"])[:10],
                                      "variable_original": a["variable_original"], "referencia": a["referencia"],
                                      "periodo_original": a["periodo_original"], "mediana": a["mediana"], "correccion": cid})
                else:
                    info["estado"] = f"NO aplicada: las filas {int(a['fila_excel'])} y {int(b['fila_excel'])} difieren en {difieren}"
                    info["alertas"] = [{"codigo": "N08", "fila_excel": int(a["fila_excel"]),
                                        "fecha_relevamiento": str(a["fecha_relevamiento"])[:10],
                                        "variable_original": a["variable_original"], "referencia": a["referencia"],
                                        "periodo_original": a["periodo_original"],
                                        "detalle": f"no es copia exacta de la fila {int(b['fila_excel'])}: difiere en {difieren}"}]
            resultados.append(info)
            continue
        if r["campo"] != "periodo":
            info["estado"] = f"campo '{r['campo']}' no soportado (solo 'periodo')"
            resultados.append(info)
            continue
        mask = ((df["fecha_relevamiento"] == pd.Timestamp(r["fecha_relevamiento"]))
                & (df["variable_original"] == r["variable_original"])
                & (df["referencia"] == r["referencia"])
                & (df["periodo_original"] == r["valor_erroneo"]))
        if r["filtro_mediana"] != "":
            mask &= (df["mediana"] - float(r["filtro_mediana"])).abs() < 1e-6
        n = int(mask.sum())
        info["filas"] = n
        if n == 1:
            df.loc[mask, "periodo_original"] = r["valor_correcto"]
            df.loc[mask, "correccion_aplicada"] = cid
            info["estado"] = "aplicada"
        elif n == 0:
            info["estado"] = "no aplicada: no hay filas con ese dato (¿el BCRA ya lo corrigió?)"
        else:
            info["estado"] = f"NO aplicada: coincide con {n} filas, agregá filtro_mediana"
        resultados.append(info)
    return resultados


# ---------------------------------------------------------------- normalización (SQL)
SQL_NORMALIZAR = r"""
create or replace table norm as
with base as (
    select
        fila_excel, fecha_relevamiento, variable_original,
        trim(split_part(referencia, ';', 1)) as unidad,
        referencia, periodo_original, periodo_fuente, correccion_aplicada,
        mediana, promedio, desvio, maximo, minimo,
        percentil_10, percentil_25, percentil_75, percentil_90, cantidad_de_participantes,
        case
            when variable_original like '%GBA%'                      then null
            when variable_original like '%IPC nivel general%'        then 'ipc_general'
            when variable_original like '%IPC n_cleo%'               then 'ipc_nucleo'
            when variable_original like 'Tipo de cambio%'            then 'tipo_cambio'
            when variable_original like 'PIB%'                       then 'pib'
            when variable_original like 'Desocupaci%'                then 'desocupacion'
            when variable_original = 'Exportaciones'                 then 'exportaciones'
            when variable_original = 'Importaciones'                 then 'importaciones'
            when lower(variable_original) like 'resultado primario%' then 'resultado_primario'
            when variable_original like 'Tasa de inter_s (%'
                then 'tasa_' || replace(strip_accents(lower(regexp_extract(variable_original, '\((.*)\)', 1))), ' ', '_')
            when variable_original like 'Tasa de pol_tica monetaria (%'
                then 'politica_' || replace(strip_accents(lower(regexp_extract(variable_original, '\((.*)\)', 1))), ' ', '_')
            else 'otra_' || replace(strip_accents(lower(variable_original)), ' ', '_')
        end as variable_std
    from raw
),
con_tipo as (
    select *,
        case
            when regexp_matches(periodo_original, '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') then 'fecha'
            when regexp_matches(periodo_original, '^[0-9]{4}$')                   then 'anio'
            when lower(periodo_original) like 'trim.%'                            then 'trimestre'
            when lower(periodo_original) like 'pr_x.%meses'                       then 'proximos_meses'
            else 'desconocido'
        end as tipo_periodo
    from base
    where variable_std is not null
),
piezas as (
    select *,
        case when tipo_periodo = 'anio' then cast(periodo_original as integer) end as anio_num,
        case when tipo_periodo = 'fecha' then try_cast(periodo_original as date) end as f_periodo,
        case when tipo_periodo = 'trimestre'
                  and regexp_matches(upper(periodo_original), '^TRIM[.] (I|II|III|IV)-[0-9]{2}$')
             then make_date(
                    2000 + cast(regexp_extract(periodo_original, '-([0-9]+)$', 1) as integer),
                    (case regexp_extract(upper(periodo_original), 'TRIM[.] ([IV]+)-', 1)
                        when 'I' then 1 when 'II' then 2 when 'III' then 3 when 'IV' then 4
                     end - 1) * 3 + 1, 1) end as q_inicio,
        case when tipo_periodo = 'proximos_meses'
                  and regexp_matches(lower(referencia), ';\s*[a-z]{3}-[0-9]{2}$')
                  and list_position(['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'],
                                    lower(regexp_extract(referencia, ';\s*([A-Za-z]{3})-', 1))) is not null
             then make_date(
                    2000 + cast(regexp_extract(referencia, '-([0-9]{2})$', 1) as integer),
                    list_position(['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'],
                                  lower(regexp_extract(referencia, ';\s*([A-Za-z]{3})-', 1))),
                    1) end as mes_objetivo
    from con_tipo
),
con_periodo as (
    select *,
        case tipo_periodo
            when 'anio'           then make_date(anio_num, 1, 1)
            when 'fecha'          then date_trunc('month', f_periodo)::date
            when 'trimestre'      then q_inicio
            when 'proximos_meses' then fecha_relevamiento::date
        end as periodo_inicio,
        case tipo_periodo
            when 'anio'           then make_date(anio_num, 12, 31)
            when 'fecha'          then last_day(f_periodo)
            when 'trimestre'      then last_day((q_inicio + interval 2 month)::date)
            when 'proximos_meses' then last_day(mes_objetivo)
        end as periodo_fin
    from piezas
)
select
    fila_excel,
    fecha_relevamiento,
    last_day(fecha_relevamiento::date) as mes_relevamiento,
    variable_std, variable_original, unidad, referencia,
    periodo_original, periodo_fuente, correccion_aplicada,
    tipo_periodo, periodo_inicio, periodo_fin,
    case
        when tipo_periodo = 'proximos_meses'
            then cast(regexp_extract(periodo_original, '([0-9]+)', 1) as integer)
        when periodo_fin is not null
            then (year(periodo_fin) - year(fecha_relevamiento)) * 12
                 + (month(periodo_fin) - month(fecha_relevamiento))
    end as horizonte_meses,
    case when periodo_fin is not null
         then (year(periodo_fin) - year(fecha_relevamiento)) * 12
              + (month(periodo_fin) - month(fecha_relevamiento))
    end as horizonte_calculado,
    mediana, promedio, desvio, maximo, minimo,
    percentil_10, percentil_25, percentil_75, percentil_90, cantidad_de_participantes
from con_periodo
"""

CLAVE = ["fecha_relevamiento", "variable_std", "unidad", "referencia", "tipo_periodo",
         "periodo_inicio", "periodo_fin", "horizonte_meses"]


# ---------------------------------------------------------------- detección de errores
def detectar(norm, bruto, ultimo_previo):
    """Devuelve (alertas, conteos). alertas = lista de dicts con la fila exacta del Excel."""
    alertas = []

    def sumar(codigo, filas, detalle):
        for _, f in filas.iterrows():
            alertas.append({
                "codigo": codigo, "fila_excel": int(f["fila_excel"]),
                "fecha_relevamiento": str(f["fecha_relevamiento"])[:10],
                "variable_original": f["variable_original"], "referencia": f["referencia"],
                "periodo_original": f["periodo_original"], "detalle": detalle(f),
            })

    # E01: horizonte declarado distinto del que sale de la referencia
    m = (norm["tipo_periodo"] == "proximos_meses") & norm["horizonte_calculado"].notna() \
        & (norm["horizonte_calculado"] != norm["horizonte_meses"])
    sumar("E01", norm[m], lambda f: f"dice '{f['periodo_original']}' pero la referencia está a "
                                    f"{int(f['horizonte_calculado'])} meses del relevamiento")
    # E02: claves duplicadas
    d = norm[norm.duplicated(subset=CLAVE, keep=False)]
    sumar("E02", d, lambda f: f"misma clave que otra fila; mediana={f['mediana']}")
    # N03: período sin formato conocido o sin fecha deducible
    m = (norm["tipo_periodo"] == "desconocido") | norm["periodo_fin"].isna()
    sumar("N03", norm[m], lambda f: f"período '{f['periodo_original']}' no interpretable")
    # N05: fecha de relevamiento ilegible (viene de la lectura cruda)
    sumar("N05", bruto[bruto["fecha_relevamiento"].isna()], lambda f: "fecha de relevamiento ilegible")
    # N06: período anual con referencia trimestral
    m = (norm["tipo_periodo"] == "anio") & norm["unidad"].str.contains("trim", case=False)
    sumar("N06", norm[m], lambda f: f"período anual '{f['periodo_original']}' con referencia '{f['unidad']}'")
    # N07: horizonte absurdo
    m = norm["horizonte_meses"].notna() & ((norm["horizonte_meses"] < -12) | (norm["horizonte_meses"] > 48))
    sumar("N07", norm[m], lambda f: f"horizonte de {int(f['horizonte_meses'])} meses")
    # N04: salto de orden de magnitud entre relevamientos consecutivos (solo los relevamientos nuevos)
    ordenado = norm.sort_values(["fecha_relevamiento", "fila_excel"]).copy()
    claves = ["variable_std", "unidad", "referencia", "tipo_periodo", "periodo_fin"]
    ordenado["valor_previo"] = ordenado.groupby(claves, dropna=False)["mediana"].shift(1)
    if ultimo_previo is not None:
        nuevos = ordenado["fecha_relevamiento"] > ultimo_previo
    else:
        nuevos = ordenado["fecha_relevamiento"] == ordenado["fecha_relevamiento"].max()
    a, b = ordenado["mediana"].abs(), ordenado["valor_previo"].abs()
    razon = a / b.where(b > 0)
    m = nuevos & (a >= 0.01) & (b >= 0.01) & ((razon > 10) | (razon < 0.1))
    sumar("N04", ordenado[m], lambda f: f"mediana {f['mediana']} vs {f['valor_previo']} del relevamiento anterior")
    return alertas


def variables_nuevas(norm_vars, aceptar):
    """Compara las variables del Excel contra variables_conocidas.csv (la primera vez la crea)."""
    actuales = sorted(set(norm_vars))
    if not VARIABLES_CONOCIDAS.exists():
        pd.DataFrame({"variable_original": actuales}).to_csv(VARIABLES_CONOCIDAS, index=False, encoding="utf-8-sig")
        return [], True
    conocidas = set(pd.read_csv(VARIABLES_CONOCIDAS, encoding="utf-8-sig")["variable_original"].map(nfc))
    nuevas = [v for v in actuales if v not in conocidas]
    if nuevas and aceptar:
        pd.DataFrame({"variable_original": sorted(conocidas | set(nuevas))}).to_csv(
            VARIABLES_CONOCIDAS, index=False, encoding="utf-8-sig")
        return nuevas, "aceptadas"
    return nuevas, False


# ---------------------------------------------------------------- registro de tipos de error
def cargar_registro():
    columnas = ["codigo", "nombre", "descripcion", "como_se_resuelve", "estado", "primera_vez"]
    if REGISTRO.exists():
        reg = pd.read_csv(REGISTRO, dtype=str, encoding="utf-8-sig").fillna("")
    else:
        reg = pd.DataFrame(columns=columnas)
    for cod, (nombre, desc, como, estado) in TIPOS_ERROR.items():
        if cod.startswith("E") and cod not in set(reg["codigo"]):
            reg.loc[len(reg)] = [cod, nombre, desc, como, estado, HOY]
    return reg


def registrar_nuevos(reg, codigos_detectados):
    """Suma al registro los tipos N detectados por primera vez. Devuelve la lista de recién agregados."""
    agregados = []
    for cod in sorted(codigos_detectados):
        if cod.startswith("N") and cod not in set(reg["codigo"]):
            nombre, desc, como, estado = TIPOS_ERROR[cod]
            reg.loc[len(reg)] = [cod, nombre, desc, como, estado, HOY]
            agregados.append(cod)
    reg.sort_values("codigo").to_csv(REGISTRO, index=False, encoding="utf-8-sig")
    return agregados


def comando_ubicar(a):
    var = a["variable_original"]
    m = re.search(r"\((.*?)\)", var)
    partes = ["py -3.13 ubicar_en_excel.py", f'--hoja "{a["hoja"]}"', f"--fecha {a['fecha_relevamiento']}",
              f'--variable "{m.group(1) if m else var[:25]}"', f'--referencia "{a["referencia"]}"']
    return " ".join(partes)


# ---------------------------------------------------------------- una hoja de punta a punta
def procesar_hoja(hoja, salida, correcciones, informe, aceptar_variables, registro):
    informe.append("")
    informe.append(f"--- Hoja: {hoja} ---")
    df = leer_hoja(hoja)
    leidas = len(df)
    resultados = aplicar_correcciones(df, correcciones, hoja)
    alertas_corr = []
    for r in resultados:
        alertas_corr += r.pop("alertas", [])
    n_excluidas = int(df["excluir"].sum())
    df = df[~df["excluir"]].reset_index(drop=True)

    # Conteos de tipos de error automáticos
    conteos = {c: 0 for c in TIPOS_ERROR}
    es_anio = df["periodo_original"].str.fullmatch(r"[0-9]{4}")
    conteos["E03"] = int((es_anio & (df["tipo_celda"] == "texto")).sum())
    conteos["E04"] = int((es_anio & (df["tipo_celda"] == "numero")).sum())
    claves_var = df["variable_original"].map(lambda s: sin_acentos(s).lower())
    grupos = df.groupby(claves_var)["variable_original"].nunique()
    conteos["E05"] = int(claves_var.isin(grupos[grupos > 1].index).sum())
    conteos["E08"] = int(df["periodo_original"].str.match(r"^(próx|trim)\.").sum())
    conteos["E09"] = int((df["tipo_celda"] == "fecha_serial").sum())
    conteos["E06"] = int(df["variable_original"].str.contains("GBA", regex=False).sum())

    # Previa: último relevamiento de la corrida anterior
    destino = CARPETA / f"{salida}.parquet"
    ultimo_previo = None
    if destino.exists():
        try:
            ultimo_previo = pd.read_parquet(destino, columns=["fecha_relevamiento"])["fecha_relevamiento"].max()
        except Exception:
            ultimo_previo = None

    validas = df[df["fecha_relevamiento"].notna()].copy()
    con = duckdb.connect()
    con.register("raw", validas)
    con.execute(SQL_NORMALIZAR)
    norm = con.sql("select * from norm").df()
    descartadas = len(validas) - len(norm)

    conteos["E10"] = n_excluidas
    conteos["E07"] = int(norm.groupby(["fecha_relevamiento", "variable_std", "unidad", "periodo_fin"])
                         ["tipo_periodo"].nunique().gt(1).sum())

    alertas = detectar(norm, df, ultimo_previo) + alertas_corr

    # Variables nuevas (N02)
    nuevas, estado_var = variables_nuevas(df["variable_original"].unique(), aceptar_variables)
    if estado_var is True:
        informe.append("Primera corrida: se creó variables_conocidas.csv con las variables actuales como base.")
    for v in nuevas:
        f = df[df["variable_original"] == v].iloc[0]
        if estado_var != "aceptadas":
            alertas.append({"codigo": "N02", "fila_excel": int(f["fila_excel"]),
                            "fecha_relevamiento": str(f["fecha_relevamiento"])[:10],
                            "variable_original": v, "referencia": f["referencia"],
                            "periodo_original": f["periodo_original"],
                            "detalle": f"variable nueva '{v}' (primera aparición)"})
    if nuevas and estado_var == "aceptadas":
        informe.append(f"Variables nuevas aceptadas como conocidas: {nuevas}")

    # Marcar las filas con alerta y guardar
    marcas = {}
    for a in alertas:
        marcas.setdefault(a["fila_excel"], set()).add(a["codigo"])
    norm["alerta_fuente"] = norm["fila_excel"].map(lambda r: ";".join(sorted(marcas.get(int(r), []))))
    con.register("norm_final", norm)
    con.execute(f"copy (select * from norm_final order by fecha_relevamiento, variable_std, referencia, "
                f"periodo_inicio, fila_excel) to '{destino.as_posix()}' (format parquet)")

    # ---- informe de la hoja
    ultimo = norm["fecha_relevamiento"].max()
    informe.append(f"Filas leídas: {leidas} | normalizadas: {len(norm)} | descartadas (IPC GBA): {descartadas}"
                   + (f" | excluidas por ser copia exacta: {n_excluidas}" if n_excluidas else ""))
    if ultimo_previo is None:
        informe.append(f"Último relevamiento cargado: {str(ultimo)[:10]} (primera carga en este archivo)")
    elif ultimo > ultimo_previo:
        nuevos = norm.loc[norm["fecha_relevamiento"] > ultimo_previo, "fecha_relevamiento"].nunique()
        informe.append(f"Último relevamiento cargado: {str(ultimo)[:10]}  <-- NUEVO ({nuevos} relevamiento/s "
                       f"desde la corrida anterior, que llegaba a {str(ultimo_previo)[:10]})")
    else:
        informe.append(f"Último relevamiento cargado: {str(ultimo)[:10]} (sin relevamientos nuevos desde la corrida anterior)")
    sin_mediana = int(norm["mediana"].isna().sum())
    if sin_mediana:
        informe.append(f"Dato: {sin_mediana} filas sin mediana (no se tocan).")
    informe.append(f"Archivo generado: datos\\{salida}.parquet")

    informe.append("")
    informe.append("Correcciones verificadas (datos\\correcciones_rem.csv):")
    if not resultados:
        informe.append("  (ninguna cargada)")
    for r in resultados:
        if r["estado"] != "no corresponde a esta hoja":
            informe.append(f"  {r['id']} [{r['tipo_error']}] {r['descripcion']} -> {r['estado']}")

    corregidos = {}
    for r in resultados:
        if r["estado"] == "aplicada":
            corregidos[r["tipo_error"]] = corregidos.get(r["tipo_error"], 0) + 1
    pendientes_tipo = {}
    for a in alertas:
        pendientes_tipo[a["codigo"]] = pendientes_tipo.get(a["codigo"], 0) + 1

    informe.append("")
    informe.append("Tipos de error resueltos (casos en esta corrida):")
    for _, r in registro[registro["estado"] == "resuelto"].sort_values("codigo").iterrows():
        cod = r["codigo"]
        if cod in ("E01", "E02", "E10"):
            resumen = (f"{corregidos.get(cod, 0)} corregidos con correcciones_rem.csv, "
                       f"{pendientes_tipo.get(cod, 0)} sin corregir")
        else:
            resumen = f"{conteos.get(cod, 0)} casos, resueltos automáticamente"
        informe.append(f"  {cod} {r['nombre']}: {resumen}")

    return alertas, {"hoja": hoja, "ultimo": ultimo, "filas": len(norm),
                     "nuevo": ultimo_previo is None or ultimo > ultimo_previo}


# ---------------------------------------------------------------- aviso en pantalla (Windows)
def avisar(codigo, resumenes, alertas, informe):
    """Cartel de Windows. Silencioso si no hay novedad; no repite el mismo aviso dos veces."""
    nuevo = bool(resumenes) and resumenes[0]["nuevo"]
    if codigo == 0 and not nuevo:
        return
    ultimo = str(resumenes[0]["ultimo"])[:7] if resumenes else "-"
    clave = f"{codigo}|{ultimo}|{nuevo}|" + ",".join(sorted(f"{a['hoja']}:{a['codigo']}:{a['fila_excel']}" for a in alertas))
    marca = CARPETA / "ultimo_aviso.txt"
    if marca.exists() and marca.read_text(encoding="utf-8") == clave:
        return
    marca.write_text(clave, encoding="utf-8")
    if codigo == 0:
        titulo, icono = "REM: todo OK", 0x40
        msg = f"Se cargó el relevamiento de {ultimo}. Sin errores.\n\nLos datos están actualizados en la carpeta datos."
    elif codigo == 1:
        tipos = sorted({a["codigo"] for a in alertas})
        titulo, icono = "REM: hay errores para revisar", 0x30
        msg = (f"Hay {len(alertas)} alertas (tipos: {', '.join(tipos)}).\n\n"
               "Abrí datos\\informe_actualizacion.txt o pedile a Claude que lo revise.")
    else:
        titulo, icono = "REM: no se pudo actualizar", 0x10
        msg = "\n".join(informe[-6:])[:600]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, icono | 0x1000)
    except Exception:
        pass


# ---------------------------------------------------------------- programa principal
def main():
    ap = argparse.ArgumentParser(description="Actualiza el REM del BCRA")
    ap.add_argument("--archivo", help="usar este Excel en lugar de descargarlo")
    ap.add_argument("--aceptar-variables", action="store_true",
                    help="dar por conocidas las variables nuevas detectadas")
    ap.add_argument("--aviso", action="store_true",
                    help="mostrar un cartel de Windows solo si hay novedades (para el Programador de tareas)")
    args = ap.parse_args()

    informe = [f"=== ACTUALIZACIÓN REM | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    codigo_salida = 0
    todas_alertas = []
    resumenes = []
    try:
        obtener_excel(args.archivo, informe)
        correcciones = cargar_correcciones()
        registro = cargar_registro()
        for hoja, salida in HOJAS.items():
            alertas, res = procesar_hoja(hoja, salida, correcciones, informe, args.aceptar_variables, registro)
            for a in alertas:
                a["hoja"] = hoja
            todas_alertas += alertas
            resumenes.append(res)

        # Registro de tipos nuevos
        codigos = {a["codigo"] for a in todas_alertas}
        agregados = registrar_nuevos(registro, codigos)

        informe.append("")
        informe.append("=" * 60)
        if not todas_alertas:
            informe.append("ALERTAS: ninguna. Todo en orden.")
            if ALERTAS_CSV.exists():
                ALERTAS_CSV.unlink()
        else:
            codigo_salida = 1
            informe.append(f"ALERTAS: {len(todas_alertas)} para revisar (detalle completo en datos\\alertas_rem.csv)")
            pd.DataFrame(todas_alertas).to_csv(ALERTAS_CSV, index=False, encoding="utf-8-sig")
            reg = registro.set_index("codigo")
            for cod in sorted(codigos):
                grupo = [a for a in todas_alertas if a["codigo"] == cod]
                nombre = TIPOS_ERROR[cod][0]
                if cod in agregados:
                    cabecera = (f"TIPO DE ERROR NUEVO {cod} ({nombre}): primera vez que aparece, "
                                f"se sumó a datos\\tipos_error_rem.csv como pendiente")
                elif cod.startswith("E"):
                    cabecera = (f"{cod} ({nombre}): tipo ya resuelto antes, pero estos casos no tienen corrección. "
                                f"Verificá el dato con 3 fuentes y agregá una línea a correcciones_rem.csv")
                else:
                    cabecera = f"{cod} ({nombre}): tipo ya registrado como '{reg.loc[cod, 'estado']}'"
                informe.append("")
                informe.append(f"* {cabecera}")
                informe.append(f"  {TIPOS_ERROR[cod][1]}")
                for a in grupo[:10]:
                    informe.append(f"  - [{a['hoja']}] fila {a['fila_excel']} | {a['fecha_relevamiento']} | "
                                   f"{a['variable_original']} | {a['referencia']} | {a['periodo_original']} | {a['detalle']}")
                if len(grupo) > 10:
                    informe.append(f"  ... y {len(grupo) - 10} más (ver alertas_rem.csv)")
                informe.append("  Para ver estas filas en el Excel:")
                vistos = []
                for a in grupo:
                    c = comando_ubicar(a)
                    if c not in vistos:
                        vistos.append(c)
                for c in vistos[:4]:
                    informe.append(f"    {c}")
                if len(vistos) > 4:
                    informe.append(f"    ... y {len(vistos) - 4} comandos más (ver alertas_rem.csv)")
                if cod in agregados:
                    informe.append("  Cuando lo hayamos resuelto, cambiá el estado a 'resuelto' en tipos_error_rem.csv "
                                   "(o pedile a Claude que lo haga).")

        if EXCLUIDAS:
            pd.DataFrame(EXCLUIDAS).to_csv(CARPETA / "filas_excluidas_rem.csv", index=False, encoding="utf-8-sig")
            informe.append("")
            informe.append(f"Filas excluidas por ser copia exacta de su gemela: {len(EXCLUIDAS)} (detalle en datos\\filas_excluidas_rem.csv)")

        # Historial
        completa = resumenes[0]
        fila = pd.DataFrame([{
            "fecha_corrida": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ultimo_relevamiento": str(completa["ultimo"])[:10],
            "filas_normalizadas": completa["filas"],
            "relevamiento_nuevo": "si" if completa["nuevo"] else "no",
            "alertas": len(todas_alertas),
        }])
        fila.to_csv(HISTORIAL, mode="a", header=not HISTORIAL.exists(), index=False, encoding="utf-8-sig")
    except ErrorEstructura as e:
        informe.append("")
        informe.append("=" * 60)
        informe.append(f"ERROR DE ESTRUCTURA: {e}")
        informe.append("No se modificó ningún archivo de salida. El BCRA pudo haber cambiado el formato del Excel.")
        informe.append("Para ver el Excel por dentro, ejecutá:  py -3.13 ubicar_en_excel.py --fecha 2026-01-31")
        codigo_salida = 2
    except Exception as e:  # cualquier falla inesperada también se informa
        informe.append("")
        informe.append("=" * 60)
        informe.append(f"FALLA INESPERADA: {type(e).__name__}: {e}")
        codigo_salida = 2

    texto = "\n".join(informe)
    print(texto)
    CARPETA.mkdir(exist_ok=True)
    INFORME.write_text(texto, encoding="utf-8")
    if args.aviso:
        avisar(codigo_salida, resumenes, todas_alertas, informe)
    sys.exit(codigo_salida)


if __name__ == "__main__":
    main()