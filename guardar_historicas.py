# guardar_historicas.py
# Guarda con TODA su historia las series que se usaron o revisaron en el proyecto y que no tienen actualizador propio
# (BCRA API v4.0, datos.gob.ar y archivos del INDEC). Nunca borra: si una fuente falla o trae menos datos que antes,
# se conservan los guardados y se avisa.
#   Salida: datos/series_historicas.parquet (formato largo: fuente, id, nombre, frecuencia, fecha, valor)
#           datos/series_historicas_catalogo.csv (una fila por serie: desde, hasta, cantidad, unidad, url)
#           datos/base_rem.duckdb (una vista por cada .parquet de datos/, para consultar todo junto)
#   Controles: H01 descarga fallida (se conserva lo anterior) | H02 la fuente trae menos fechas que antes
#              (se conservan las faltantes) | H03 valores ya guardados que cambiaron
# Corre como mucho una vez por semana (salvo --forzar). Está en actualizar_todo.py.
# Uso:
#   py -3.13 guardar_historicas.py [--forzar]
#   pythonw.exe guardar_historicas.py --aviso
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = error
import argparse
import io
import json
import re
import sys
import tempfile
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
SALIDA = CARPETA / "series_historicas.parquet"
CATALOGO = CARPETA / "series_historicas_catalogo.csv"
DUCKDB = CARPETA / "base_rem.duckdb"
INFORME = CARPETA / "informe_historicas.txt"
ALERTAS_CSV = CARPETA / "alertas_historicas.csv"
HISTORIAL = CARPETA / "historial_historicas.csv"
DIAS_ENTRE_CORRIDAS = 7
API_BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"

# BCRA (las que no baja actualizar_bcra.py): reservas, circulación, inflación, CER, factores de explicación de la base,
# saldos de la base y cuasimonedas, M1/M2/M3 diarios discontinuados, depósitos, activos/pasivos y agregados mensuales desde 1940
BCRA_IDS = ([1, 16, 19, 25, 27, 28, 29, 30] + list(range(46, 62)) + [64, 66, 67, 68, 71, 72, 73, 1269]
            + [1229, 1230, 1231, 1232, 1233, 1234, 1239, 1241, 1242] + [1247, 1248, 1249, 1254, 1264]
            + list(range(1606, 1634))
            # agregadas el 14-oct-2026 (revisión del catálogo de la API; sin repetidas ni aperturas finas por titular/línea)
            + [197]                                                         # M2 transaccional del sector privado
            + [153, 157, 159, 196, 1223, 1258, 1259, 1260, 1261, 1262, 1263]  # stocks de pases, LEBAC/LELIQ/NOCOM/LeFi, LEGAR
            + [1224, 1265, 1266, 1267, 1268, 1250]                          # crédito al Tesoro y bancos, depósitos del gobierno, encajes
            + [18, 21, 22, 23, 24, 26] + list(range(85, 109)) + list(range(110, 128))  # depósitos y préstamos al sector privado
            + [8, 11, 13, 14, 138, 141, 150, 151, 164, 165]                 # tasas: TM20, BAIBAR, adelantos, personales, BADLAR total, pases
            + list(range(1189, 1197)) + [1203, 1204, 1205, 1207, 1208, 1209, 1210, 1211, 1213, 1214]  # tasas de plazo fijo
            + [1199, 1215, 1216, 1217, 1218, 1219, 1220, 1240, 1227, 1221]  # tasas activas, PF UVA, liquidez
            + [31, 32, 40, 1187, 1188]                                      # UVA, UVI, ICL, banda cambiaria
            + [1605]                                                        # reservas internacionales mensuales desde 1940
            + [161] + list(range(167, 196))                                 # tasas discontinuadas: política monetaria 2020-25, LEBAC/NOBAC/NOTALIQ
            + [43, 1197, 1198]                                              # tasas judiciales (Com. P 14.290, intereses moratorios, pasiva ley 27.802)
            + list(range(78, 83)) + [65, 74, 76, 84]                        # variación de reservas por origen (compra de divisas, etc.), cuasimonedas
            + [62, 63, 75, 83, 147, 149, 1200, 1201, 1202, 1222, 1226, 1228, 1243, 1246,  # moneda extranjera en bancos, líneas del
               1251, 1252, 1253, 1255, 1256, 1257, 1270, 1604]              # exterior, fondo de liquidez, cuentas en el BCRA, montos interbancarios
            + list(range(1235, 1239)))                                      # préstamos por tipo del Informe Monetario (discontinuados may-2026)
# datos.gob.ar (se baja el CSV original de cada distribución)
DATOS_GOB = [
    # IPC históricos, provinciales y regulados
    "178.1_NL_GENERAL_0_0_13", "177.1_NL_GENERAL_1974_0_13", "176.1_NL_GENERAL_1988_0_13", "96.3_ING_2008_M_19",
    "96.3_INGV_2008_M_20", "97.2_ING_2008_M_17", "99.3_IR_2008_0_9", "100.3_I2NG_2003_M_22",
    "195.1_NIVEL_GENERAL_0_0_13", "196.1_NIVEL_GENERAL_2014_0_13", "197.1_NIVEL_GENERAL_2014_0_13",
    "199.1_NIVEL_GENERAL_2014_0_13", "199.2_NIVEL_GENERAL_DICI_0_13_51",
    # (las categorías del IPC -173.1 y 145.9- NO van: su CSV está cortado en mar-2020; se bajan directo del INDEC)
    # actividad y cuentas nacionales
    "143.3_NO_PR_2004_A_21", "143.3_NO_PR_2004_A_28", "143.2_NO_PR_2004_A_31", "166.1_PPIB_0_0_3", "166.2_PPIB_0_0_3",
    "9.1_PP2_2004_A_16", "9.2_PP2_2004_T_16", "113.1_IOGP_2004_A_21", "113.2_IOGP_2004_T_21",
    # indicadores físicos de actividad
    "41.3_CP_0_A_16", "41.3_AA_0_A_23", "41.3_AC_0_A_11", "359.3_ACERO_CRUDUDO__11", "367.3_DEMANDA_TOTAL__13",
    "364.3_INDUSTRIARIA__9", "363.3_VENTAS_DE_OIL__16",
    # salarios
    "149.1_TL_INDIIOS_OCTU_0_21", "149.1_TL_REGIADO_OCTU_0_16", "149.1_SOR_PRIADO_OCTU_0_25", "149.1_SOR_PUBICO_OCTU_0_14",
    "18.2_NG_1997_T_13", "18.1_NG_1997_A_13",
    # monetarias y fiscales (Ministerio de Economía)
    "300.1_AP_PAS_BASRIA_0_M_21", "331.1_SALDO_BASERIA__15", "331.1_RESCATE_CUDAS__5", "172.3_TL_RECAION_M_0_0_17",
    # precios mayoristas y del productor: IPIB, IPP desde dic-2015 e IPIM base 1993 (1995-2015). El IPIM actual (448.1) NO va:
    # su CSV está cortado en oct-2019; se baja directo del INDEC con todas las aperturas (ver series_indec)
    "449.1_NIVEL_GENERAL_0_0_13_97", "450.1_NIVEL_GENERAL_0_0_13_92", "106.3_I1NG_1993_M_23",
    # resultado fiscal mensual. IMIG (Informe Mensual de Ingresos y Gastos, SPN, desde 2016): primario, financiero,
    # intereses netos, primario con PIP (meta FMI); esquema AIF del SPN base caja: 2015+, 2007-2014, 1993-2006
    "452.3_RESULTADO_RIO_0_M_18_54", "452.3_RESULTADO_ERO_0_M_20_25", "452.3_INTERESES_TOS_0_M_15_62", "452.3_RESULTADO_PIP_0_M_22_71",
    "379.9_SUPERAVIT_017__23_94", "379.9_RESULTADO_017__18_38", "379.8_SUPERAVIT_014__48_10", "379.8_RESULTADO_014__23_17",
    "379.7_SUPERAVIT_006__28_65", "379.7_RESULTADO_006__23_14",
    # RIPTE (Secretaría de Trabajo): sale antes que el índice de salarios del INDEC
    "158.1_REPTE_0_0_5",
    # indicadores adelantados de actividad para el PIB (17-oct-2026): IVA DGI y recaudación total (salen los primeros
    # días del mes siguiente), importaciones y exportaciones totales en USD (INDEC, ~día 20 del mes siguiente), IPI
    # manufacturero (INDEC), ventas de gasoil y cigarrillos
    "142.3_IVA_D_2001_M_7", "142.3_IVA_2001_M_3",
    "74.3_IIT_0_M_25", "74.3_IET_0_M_16", "74.3_IBCPP_0_M_32", "74.3_IICL_0_M_42",
    "453.1_SERIE_ORIGNAL_0_0_14_46", "453.1_SERIE_TENDCLO_0_0_21_61",
    "41.3_DC_0_A_21",
    # empleo registrado SIPA (Secretaría de Trabajo), sin estacionalidad: total y asalariados privados. Probado contra la
    # EPH para anticipar la desocupación el 28-oct-2026 (probar_sipa.py): no sirve, pero se guarda (regla "guardar todo lo revisado")
    "151.1_TL_SIN_TAC_2012_M_15", "151.1_AARIADOTAC_2012_M_26",
]   # (recaudación total 172.3 y gasoil 363.3 ya estaban más arriba: repetirlas duplicaba los datos; quitadas el 18-oct-2026)
# dataset completo "Indicadores de producción de la Industria manufacturera" (41.3, Subsecretaría de Programación
# Macroeconómica): las 95 series (petróleo, faena, lácteos, papel, química, combustibles, construcción, siderurgia,
# automotores, electrodomésticos). Agregado el 23-oct-2026 (regla "guardar todo lo revisado").
DATOS_GOB += [
    "41.3_PC_0_A_14", "41.3_PP_0_A_18", "41.3_GN_0_A_11", "41.3_H_0_A_10", "41.3_AV_0_A_17", "41.3_SO_0_A_24",
    "41.3_FCV_0_A_18", "41.3_FA_0_A_10", "41.3_FP_0_A_14", "41.3_FO_0_A_12", "41.3_LIP_0_A_27", "41.3_M_0_A_7",
    "41.3_Y_0_A_5", "41.3_Q_0_A_6", "41.3_C_0_A_5", "41.3_DL_0_A_11", "41.3_PLF_0_A_22", "41.3_BE_0_A_20",
    "41.3_DAVMI_0_A_42", "41.3_ME_0_A_15", "41.3_DG_0_A_18", "41.3_DC_0_A_21", "41.3_PPI_0_A_26", "41.3_PP_0_A_17",
    "41.3_PPSOU_0_A_33", "41.3_JTL_0_A_21", "41.3_DLIL_0_A_42", "41.3_OA_0_A_15", "41.3_U_0_A_4", "41.3_P_0_A_3",
    "41.3_E_0_A_7", "41.3_P_0_A_11", "41.3_SB_0_A_11", "41.3_I_0_A_11", "41.3_O_0_A_7", "41.3_N_0_A_9",
    "41.3_NT_0_A_12", "41.3_NC_0_A_11", "41.3_NS_0_A_11", "41.3_NU_0_A_11", "41.3_GO_0_A_7", "41.3_FO_0_A_8",
    "41.3_K_0_A_8", "41.3_A_0_A_12", "41.3_L_0_A_11", "41.3_P_0_A_7", "41.3_B_0_A_6", "41.3_GR_0_A_13",
    "41.3_COQ_0_A_5", "41.3_G_0_A_6", "41.3_A_0_A_7", "41.3_SA_0_A_20", "41.3_SOLA_0_A_20", "41.3_SH_0_A_16",
    "41.3_A_0_A_8", "41.3_OPL_0_A_24", "41.3_OPM_0_A_24", "41.3_OPP_0_A_23", "41.3_CS_0_A_16", "41.3_NH_0_A_10",
    "41.3_PP_0_A_24", "41.3_PPU_0_A_22", "41.3_CPR_0_A_30", "41.3_ASC_0_A_29", "41.3_CP_0_A_16", "41.3_LCH_0_A_26",
    "41.3_PY_0_A_11", "41.3_HP_0_A_15", "41.3_AC_0_A_11", "41.3_LEF_0_A_14", "41.3_LECNP_0_A_28", "41.3_P_0_A_6",
    "41.3_AP_0_A_17", "41.3_AA_0_A_23", "41.3_U_0_A_11", "41.3_CP_0_A_15", "41.3_T_0_A_9", "41.3_LS_0_A_21",
    "41.3_CT_0_A_22", "41.3_CE_0_A_20", "41.3_AA_0_A_21", "41.3_CGF_0_A_20", "41.3_HA_0_A_13", "41.3_HM_0_A_17",
    "41.3_HF_0_A_18", "41.3_T_0_A_11", "41.3_RV_0_A_19", "41.3_EA_0_A_13", "41.3_A_0_A_11", "41.3_CF_0_A_20",
    "41.3_AAP_0_A_29", "41.3_TC_0_A_19", "41.3_MLP_0_A_19", "41.3_NNTP_0_A_28", "41.3_DC_0_A_16",
]
DATOS_GOB = list(dict.fromkeys(DATOS_GOB))   # por las dudas: una serie repetida en la lista no se baja dos veces
# otras fuentes oficiales en archivo (agregadas el 14-oct-2026)
URL_ITCRM = "https://www.bcra.gob.ar/Pdfs/PublicacionesEstadisticas/ITCRMSerie.xlsx"
PAGINA_BM = "https://www.worldbank.org/en/research/commodity-markets"   # el enlace al Excel cambia con cada publicación
URL_COMBUSTIBLES = ("http://datos.energia.gob.ar/dataset/1c181390-5045-475e-94dc-410429be4b17/resource/"
                    "f8dda0d5-2a9f-4d34-b79b-4e63de3995df/download/precios-historicos.csv")
BASE_COMBUSTIBLES = Path("E:/combustibles/precios.parquet")   # la arma actualizar_combustibles.py (diario)
MESES_VIGENCIA_COMBUSTIBLE = 6   # una estación que no informa cambios en 6 meses sale del cálculo (no se arrastra su precio)
# archivos del INDEC
INDEC_XLS = {
    "cvs": ("https://www.indec.gob.ar/ftp/cuadros/sociedad/serie_cvs_2012.xls", "Serie histórica CVS",
            {1: "privado_registrado", 2: "privado_no_registrado", 3: "publico", 4: "nivel_general"}),
    "is_2012": ("https://www.indec.gob.ar/ftp/cuadros/sociedad/serie_is_2012.xls", "Serie histórica IS",
                {1: "privado_registrado", 2: "privado_no_registrado", 3: "publico", 4: "nivel_general"}),
}
CONTROLES = {"H01": ("descarga_fallida", "No se pudo bajar la serie: se conservan los datos guardados"),
             "H02": ("perdio_fechas", "La fuente trae menos fechas que antes: se conservan las que faltan"),
             "H03": ("historico_modificado", "Valores ya guardados que cambiaron en la fuente"),
             "H04": ("csv_desactualizado", "El CSV de datos.gob.ar llega a una fecha anterior que la API: se conservan los datos guardados"),
             "H05": ("serie_no_vino", "Serie guardada que no vino en esta corrida (fuente caída o sacada del catálogo): se conserva")}


def bajar(url, json_=True):
    headers = {"User-Agent": "Mozilla/5.0"}
    if json_:
        headers["Accept"] = "application/json"
    for intento in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
                return r.read()
        except Exception:
            if intento == 2:
                raise
            time.sleep(3)


def series_bcra():
    lista, off = [], 0
    while True:
        d = json.loads(bajar(f"{API_BCRA}?limit=1000&offset={off}"))
        lista += d["results"]
        off += 1000
        if off >= d["metadata"]["resultset"]["count"]:
            break
    meta = {v["idVariable"]: v for v in lista}
    for vid in BCRA_IDS:
        m = meta.get(vid)
        if m is None:
            yield f"bcra_{vid}", None, {"error": f"el id {vid} ya no existe en la API"}
            continue
        try:
            filas, off = [], 0
            while True:
                d = json.loads(bajar(f"{API_BCRA}/{vid}?desde={m['primerFechaInformada']}&limit=3000&offset={off}"))
                det = d["results"][0]["detalle"] if d["results"] else []
                filas += [(x["fecha"], x["valor"]) for x in det]
                off += 3000
                if off >= d["metadata"]["resultset"]["count"] or not det:
                    break
                time.sleep(0.3)
            df = pd.DataFrame(filas, columns=["fecha", "valor"])
            info = {"fuente": "BCRA API v4.0", "nombre": str(m["descripcion"]).strip(), "frecuencia": m["periodicidad"],
                    "unidad": m.get("unidadExpresion"), "url": f"{API_BCRA}/{vid}"}
            yield f"bcra_{vid}", df, info
        except Exception as e:
            yield f"bcra_{vid}", None, {"error": f"{type(e).__name__}: {e}"}


def series_datos_gob():
    cache = {}
    for sid in DATOS_GOB:
        try:
            meta = json.loads(bajar(f"https://apis.datos.gob.ar/series/api/series/?ids={sid}&metadata=full&limit=1&format=json"))["meta"]
            url = meta[1]["distribution"]["downloadURL"]
            if url not in cache:
                cache[url] = pd.read_csv(io.BytesIO(bajar(url, json_=False)))
            csv = cache[url]
            col = meta[1]["field"]["title"]
            df = pd.DataFrame({"fecha": csv["indice_tiempo"], "valor": pd.to_numeric(csv[col], errors="coerce")})
            fin_api = str(meta[1]["field"].get("time_index_end") or "")[:7]
            fin_csv = str(df.dropna()["fecha"].max())[:7]
            if fin_api and fin_api > fin_csv:   # el CSV de descarga se quedó atrás de la API (pasó con las categorías del IPC)
                yield f"datosgob_{sid}", None, {"error": f"CSV_DESACTUALIZADO: el CSV llega a {fin_csv} y la API a {fin_api}"}
                continue
            info = {"fuente": "datos.gob.ar (" + str(meta[1]["dataset"].get("source", "")).strip() + ")",   # nombre completo (antes se cortaba a 60)
                    "nombre": str(meta[1]["field"].get("description", col)), "frecuencia": meta[0]["frequency"],
                    "unidad": meta[1]["field"].get("units"), "url": url}
            yield f"datosgob_{sid}", df, info
        except Exception as e:
            yield f"datosgob_{sid}", None, {"error": f"{type(e).__name__}: {e}"}


def sin_acentos(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def series_itcrm():
    """Tipo de cambio real multilateral y bilaterales del BCRA (base 17-12-15=100): promedio mensual y diario."""
    try:
        x = pd.ExcelFile(io.BytesIO(bajar(URL_ITCRM, json_=False)))
        for hoja, frec, suf in [("ITCRM y bilaterales prom. mens.", "M", ""), ("ITCRM y bilaterales", "D", "_diario")]:
            h = x.parse(hoja, header=None)
            cab = h.iloc[1]
            fechas = pd.to_datetime(h[0], errors="coerce")
            datos = h[fechas.notna()]
            for j in range(1, h.shape[1]):
                nom = str(cab[j]).strip()
                if nom in ("", "nan"):
                    continue
                if frec == "D" and nom != "ITCRM":   # diario: solo el multilateral
                    continue
                clave = re.sub(r"[^a-z0-9]+", "_", sin_acentos(nom).lower()).strip("_")
                df = pd.DataFrame({"fecha": fechas[fechas.notna()], "valor": pd.to_numeric(datos[j], errors="coerce")})
                yield f"bcra_xls_{clave}{suf}", df, {"fuente": "BCRA (archivo ITCRM)", "nombre": f"{nom} ({'diario' if frec == 'D' else 'promedio mensual'})",
                                                     "frecuencia": frec, "unidad": "índice 17-12-15=100", "url": URL_ITCRM}
    except Exception as e:
        yield "bcra_xls_itcrm", None, {"error": f"{type(e).__name__}: {e}"}


MESES_ABREV = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10,
               "nov": 11, "dic": 12}


def mes_utdt(v):
    """Fecha de la columna A de los Excel de la UTDT: casi siempre una fecha, pero algunos meses vienen como texto
    ('Dic-07', 'abr.-22') y alguno con día distinto de 1 (2008-02-08). Devuelve el primer día del mes o None."""
    if hasattr(v, "year"):
        return pd.Timestamp(v.year, v.month, 1) if v.year >= 1990 else None
    m = re.fullmatch(r"([a-záé]{3})\.?-(\d{2})", str(v).strip().lower())
    if m and m.group(1) in MESES_ABREV:
        return pd.Timestamp(2000 + int(m.group(2)), MESES_ABREV[m.group(1)], 1)
    return None


def series_utdt():
    """Universidad Torcuato Di Tella, Centro de Investigación en Finanzas (no hay equivalente oficial):
    Expectativas de Inflación de los hogares (próximos 12 meses, mediana y promedio, desde ago-2006) e Índice de Confianza
    del Consumidor (por región desde jul-1998 y por subíndice desde mar-2001). El nombre del Excel cambia cada mes:
    se busca el enlace en la página de la serie histórica."""
    base = "https://www.utdt.edu/"
    paginas = {"ei": base + "ver_contenido.php?id_contenido=8512&id_item_menu=16455",
               "icc": base + "listado_contenidos.php?id_item_menu=16458"}
    for clave, pagina in paginas.items():
        try:
            html = bajar(pagina, json_=False).decode("utf-8", "replace")
            enlaces = re.findall(r'download\.php\?fname=[^"\'\s>]+\.xls', html)
            if not enlaces:
                raise RuntimeError(f"no se encontró el Excel en {pagina}")
            url = base + enlaces[0]
            x = pd.ExcelFile(io.BytesIO(bajar(url, json_=False)))
            info = {"fuente": "UTDT (Centro de Investigación en Finanzas)", "frecuencia": "M", "url": url}
            if clave == "ei":
                h = x.parse(x.sheet_names[0], header=None)
                fechas = pd.to_datetime(h[0].map(mes_utdt))
                d = h[fechas.notna()]
                for col, nom in [(1, "mediana"), (2, "promedio")]:
                    yield f"utdt_ei_{nom}", pd.DataFrame({"fecha": fechas[fechas.notna()], "valor": pd.to_numeric(d[col], errors="coerce")}), {
                        **info, "nombre": f"Expectativas de inflación de los hogares, próximos 12 meses ({nom})", "unidad": "% anual"}
            else:
                for hoja, cols in [("Desagregación por regiones", {1: "capital", 3: "interior", 5: "gba", 7: "nacional"}),
                                   ("Desagregación por subíndices", {1: "situacion_personal", 3: "situacion_macro", 5: "bienes_durables"})]:
                    h = x.parse(hoja, header=None)
                    fechas = pd.to_datetime(h[0].map(mes_utdt))
                    d = h[fechas.notna()]
                    for col, nom in cols.items():
                        yield f"utdt_icc_{nom}", pd.DataFrame({"fecha": fechas[fechas.notna()], "valor": pd.to_numeric(d[col], errors="coerce")}), {
                            **info, "nombre": f"Índice de Confianza del Consumidor, {nom.replace('_', ' ')}", "unidad": "índice 0-100"}
        except Exception as e:
            yield f"utdt_{clave}", None, {"error": f"{type(e).__name__}: {e}"}


def series_confianza_gobierno():
    """Índice de Confianza en el Gobierno (UTDT, Escuela de Gobierno; escala 0 a 5, mensual desde nov-2001). La UTDT solo lo
    publica en PDF: se toma de la API de argentinadatos.com (tercero; no hay fuente oficial en formato de datos). Validado el
    15-oct-2026 contra el informe UTDT de sep-2026 (1,94 y promedios por presidencia: coinciden con ±0,02)."""
    url = "https://api.argentinadatos.com/v1/politica/indices/confianza-gobierno"
    try:
        d = pd.DataFrame(json.loads(bajar(url)))
        yield "utdt_icg", pd.DataFrame({"fecha": pd.to_datetime(d["fecha"]), "valor": pd.to_numeric(d["valor"], errors="coerce")}), {
            "fuente": "UTDT Escuela de Gobierno (vía argentinadatos.com)", "nombre": "Índice de Confianza en el Gobierno (0 a 5)",
            "frecuencia": "M", "unidad": "índice 0-5", "url": url}
    except Exception as e:
        yield "utdt_icg", None, {"error": f"{type(e).__name__}: {e}"}


def series_precios_promedio():
    """Precios promedio oficiales del INDEC (sh_ipc_precios_promedio.xls): hoja GBA (~60 variedades) y hoja Nacional
    (14 productos x 6 regiones), mensual desde 2016-2017. Usa precios_promedio_indec.py, que además deja al día
    datos/precios_promedio_indec.parquet (lo usa canasta_sepa.py)."""
    import contextlib
    import precios_promedio_indec as ppi
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            ppi.main()
        df = pd.read_parquet(ppi.SALIDA)
        for (hoja, region, prod, unidad), g in df.groupby(["hoja", "region", "producto", "unidad"]):
            clave = re.sub(r"[^a-z0-9]+", "_", sin_acentos(f"{hoja}_{region}_{prod}").lower()).strip("_")
            yield f"indec_pp_{clave}", pd.DataFrame({"fecha": pd.to_datetime(g["mes"]), "valor": g["precio"].values}), {
                "fuente": "INDEC (archivo)", "nombre": f"Precio promedio {prod} ({region}, hoja {hoja})", "frecuencia": "M",
                "unidad": f"$ por {unidad}", "url": ppi.URL}
    except Exception as e:
        yield "indec_precios_promedio", None, {"error": f"{type(e).__name__}: {e}"}


def series_banco_mundial():
    """Precios internacionales de commodities (Banco Mundial, Pink Sheet), mensual en USD nominales, desde 1960."""
    try:
        html = bajar(PAGINA_BM, json_=False).decode("utf-8", "replace")
        enlaces = re.findall(r'https?://[^"\']+CMO-Historical-Data-Monthly\.xlsx', html)
        if not enlaces:
            raise RuntimeError("no se encontró el enlace al Excel en la página del Banco Mundial")
        url = enlaces[0]
        x = pd.ExcelFile(io.BytesIO(bajar(url, json_=False)))
        for hoja, pref in [("Monthly Prices", "bm"), ("Monthly Indices", "bm_indice")]:
            h = x.parse(hoja, header=None)
            filas = h[0].astype(str).str.fullmatch(r"\d{4}M\d{2}")
            fechas = pd.PeriodIndex(h.loc[filas, 0].str.replace("M", "-"), freq="M").to_timestamp()
            cab = h.loc[:filas.idxmax() - 1]
            for j in range(1, h.shape[1]):
                if hoja == "Monthly Prices":
                    nom, unidad = str(cab.iloc[-2, j]).strip(), str(cab.iloc[-1, j]).strip()
                else:   # índices: el nombre está en distintas filas según el nivel de la jerarquía
                    v = cab[j].dropna().astype(str)
                    v = v[~v.str.contains("monthly|Updated", case=False) & v.str.strip().ne("")]   # hay celdas con un espacio
                    nom, unidad = (v.iloc[-1].strip() if len(v) else ""), "2010=100"
                if nom in ("", "nan"):
                    continue
                nom = nom.replace("**", "").strip()
                valores = pd.to_numeric(h.loc[filas, j].replace({"…": None, "...": None}), errors="coerce")
                clave = re.sub(r"[^a-z0-9]+", "_", nom.lower()).strip("_")
                yield f"{pref}_{clave}", pd.DataFrame({"fecha": fechas, "valor": valores.values}), {
                    "fuente": "Banco Mundial (Pink Sheet)", "nombre": nom, "frecuencia": "M", "unidad": unidad, "url": url}
    except Exception as e:
        yield "bm_commodities", None, {"error": f"{type(e).__name__}: {e}"}


def series_combustibles():
    """Precio de combustibles en surtidor (Secretaría de Energía, Res. 314/2016): mediana mensual entre estaciones, por
    producto, total país y CABA. En cada fin de mes se toma el precio vigente de cada estación (horario diurno), siempre que
    lo haya informado en los últimos MESES_VIGENCIA_COMBUSTIBLE meses; la cantidad de estaciones queda como serie aparte.
    Lee la base local que arma actualizar_combustibles.py (E:\\combustibles\\precios.parquet): no descarga nada."""
    import duckdb
    tmp = Path(tempfile.mkdtemp())
    try:
        if not BASE_COMBUSTIBLES.exists():
            raise FileNotFoundError(f"no está {BASE_COMBUSTIBLES} (¿disco E: desconectado? correr actualizar_combustibles.py)")
        con = duckdb.connect()
        # límites: la primera versión (cruce estaciones x meses) llenó 78 GB de temporales
        con.execute(f"set memory_limit='4GB'; set max_temp_directory_size='10GiB'; set preserve_insertion_order=false; "
                    f"set temp_directory='{(tmp / 'duck').as_posix()}'")
        # cada precio rige desde su fecha hasta el próximo cambio de esa estación y producto, con tope de N meses;
        # en cada fin de mes se toman las estaciones con precio vigente (unión por rango, sin grilla)
        q = f"""
        with p2 as (select * from read_parquet('{BASE_COMBUSTIBLES.as_posix()}')),
        iv as (select *, least(coalesce(lead(f) over (partition by est, producto order by f), current_timestamp),
                               f + interval {MESES_VIGENCIA_COMBUSTIBLE} month) as hasta from p2),
        meses as (select unnest(generate_series(date '2016-01-01', date_trunc('month', current_date) - interval 1 month,
                                                interval 1 month)) as mes),   -- solo meses cerrados
        fines as (select mes, least(mes + interval 1 month - interval 1 second, current_timestamp - interval 1 second) as fin from meses)
        select m.mes, iv.producto,
               case when iv.provincia similar to '.*(CAPITAL FEDERAL|CIUDAD AUT|CABA).*' then 'caba' else 'otra' end as zona,
               iv.precio
        from fines m join iv on iv.f <= m.fin and iv.hasta > m.fin
        """
        v = con.execute(f"""select mes, producto, zona, median(precio) as mediana, count(*) as n from ({q}) group by all
                            union all
                            select mes, producto, 'pais', median(precio), count(*) from ({q}) group by all""").df()
        con.close()
        v["mes"] = pd.to_datetime(v["mes"])
        for ambito in ("pais", "caba"):
            for prod, m in v[v["zona"] == ambito].groupby("producto"):
                m = m.set_index("mes").sort_index().rename(columns={"mediana": "median", "n": "count"})
                clave = re.sub(r"[^a-z0-9]+", "_", sin_acentos(prod).lower()).strip("_")
                base = {"fuente": "Secretaría de Energía (precios en surtidor)", "frecuencia": "M", "url": URL_COMBUSTIBLES}
                yield f"comb_{ambito}_{clave}", pd.DataFrame({"fecha": m.index, "valor": m["median"].values}), {
                    **base, "nombre": f"{prod}, precio mediano en surtidor ({'total país' if ambito == 'pais' else 'CABA'})", "unidad": "$/litro"}
                yield f"comb_{ambito}_{clave}_estaciones", pd.DataFrame({"fecha": m.index, "valor": m["count"].values.astype(float)}), {
                    **base, "nombre": f"{prod}, estaciones con precio vigente ({'total país' if ambito == 'pais' else 'CABA'})", "unidad": "estaciones"}
    except Exception as e:
        yield "comb_surtidor", None, {"error": f"{type(e).__name__}: {e}"}
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def series_indec():
    for clave, (url, hoja, cols) in INDEC_XLS.items():
        try:
            h = pd.read_excel(io.BytesIO(bajar(url, json_=False)), sheet_name=hoja, header=None)
            fechas = pd.to_datetime(h[0], errors="coerce", format="mixed")
            h, fechas = h[fechas.notna()], fechas[fechas.notna()]
            for c, nom in cols.items():
                df = pd.DataFrame({"fecha": fechas, "valor": pd.to_numeric(h[c], errors="coerce")})
                yield f"indec_{clave}_{nom}", df, {"fuente": "INDEC (archivo)", "nombre": f"{clave.upper()} {nom.replace('_', ' ')}",
                                                   "frecuencia": "M", "unidad": "índice", "url": url}
        except Exception as e:
            yield f"indec_{clave}", None, {"error": f"{type(e).__name__}: {e}"}
    try:   # IPC por categorías, bienes/servicios y divisiones COICOP, nacional y 6 regiones, directo del INDEC
        url = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv"
        csv = pd.read_csv(io.StringIO(bajar(url, json_=False).decode("latin-1")), sep=";", decimal=",")
        nombres = {"0": "nivel_general", "Núcleo": "nucleo", "Regulados": "regulados", "Estacional": "estacional",
                   "B": "bienes", "S": "servicios"}
        for (reg, cod), g in csv.groupby([csv["Region"], csv["Codigo"].astype(str)]):
            nom = nombres.get(cod, f"division_{cod}")
            pref = "indec_ipc" if reg == "Nacional" else f"indec_ipc_{sin_acentos(reg).lower()}"   # ids nacionales sin cambios
            df = pd.DataFrame({"fecha": pd.PeriodIndex(g["Periodo"].astype(str), freq="M").to_timestamp(),
                               "valor": pd.to_numeric(g["Indice_IPC"], errors="coerce").values})
            desc = g["Descripcion"].dropna().iloc[0] if g["Descripcion"].notna().any() else nom
            yield f"{pref}_{nom}", df, {"fuente": "INDEC (archivo)", "nombre": f"IPC {reg.lower() if reg == 'Nacional' else reg} {desc}".strip(),
                                        "frecuencia": "M", "unidad": "índice dic-2016=100", "url": url}
    except Exception as e:
        yield "indec_ipc_divisiones", None, {"error": f"{type(e).__name__}: {e}"}
    try:   # IPC por grupos y clases (aperturas), por región (el INDEC no publica aperturas nacionales)
        url = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_aperturas.csv"
        csv = pd.read_csv(io.StringIO(bajar(url, json_=False).decode("latin-1")), sep=";", decimal=",")
        for (reg, cod), g in csv.groupby([csv["Region"], csv["Codigo"].astype(str)]):
            df = pd.DataFrame({"fecha": pd.PeriodIndex(g["Periodo"].astype(str), freq="M").to_timestamp(),
                               "valor": pd.to_numeric(g["Indice_IPC"], errors="coerce").values})
            desc = g["Descripcion_aperturas"].dropna().iloc[0]
            yield f"indec_ipcap_{sin_acentos(reg).lower()}_{cod}", df, {
                "fuente": "INDEC (archivo)", "nombre": f"IPC {reg} {cod} {desc} ({g['Clasificador'].iloc[0]})",
                "frecuencia": "M", "unidad": "índice dic-2016=100", "url": url}
    except Exception as e:
        yield "indec_ipc_aperturas", None, {"error": f"{type(e).__name__}: {e}"}
    for sistema in ("ipim", "ipib", "ipp"):   # precios mayoristas/productor (base dic-2015=100), todas las aperturas
        try:
            url = f"https://www.indec.gob.ar/ftp/cuadros/economia/indice_{sistema}.csv"
            csv = pd.read_csv(io.StringIO(bajar(url, json_=False).decode("latin-1")), sep=";", decimal=",")
            col_ap, col_val = csv.columns[1], csv.columns[2]
            for ap, g in csv.groupby(col_ap):
                clave = re.sub(r"[^a-z0-9]+", "_", sin_acentos(ap).lower()).strip("_")
                df = pd.DataFrame({"fecha": pd.to_datetime(g["periodo"]), "valor": pd.to_numeric(g[col_val], errors="coerce").values})
                yield f"indec_{sistema}_{clave}", df, {"fuente": "INDEC (archivo)", "nombre": f"{sistema.upper()} {ap.replace('_', ' ')}",
                                                       "frecuencia": "M", "unidad": "índice dic-2015=100", "url": url}
        except Exception as e:
            yield f"indec_{sistema}", None, {"error": f"{type(e).__name__}: {e}"}
    try:
        url = "https://www.indec.gob.ar/ftp/cuadros/sociedad/indice_salarios.csv"
        csv = pd.read_csv(io.BytesIO(bajar(url, json_=False)), sep=";", decimal=",", na_values=["NA"])
        for col in [c for c in csv.columns if c != "periodo"]:
            df = pd.DataFrame({"fecha": pd.to_datetime(csv["periodo"], dayfirst=True), "valor": pd.to_numeric(csv[col], errors="coerce")})
            yield f"indec_{col.lower()}", df, {"fuente": "INDEC (archivo)", "nombre": f"Índice de salarios {col}",
                                               "frecuencia": "M", "unidad": "índice oct-2016=100", "url": url}
    except Exception as e:
        yield "indec_indice_salarios", None, {"error": f"{type(e).__name__}: {e}"}


def armar_duckdb():
    global duckdb
    import duckdb
    con = duckdb.connect(str(DUCKDB))
    vistas, salteadas = [], []
    for p in sorted(CARPETA.glob("*.parquet")):
        nombre = p.stem.replace("-", "_")
        try:   # un parquet vacío (p. ej. sepa_indice_mensual antes de tener dos meses) no debe frenar todo
            con.execute(f"create or replace view {nombre} as select * from read_parquet('{p.as_posix()}')")
            vistas.append(nombre)
        except duckdb.Error as e:
            salteadas.append(f"{p.name} ({str(e).splitlines()[0][:80]})")
    con.close()
    return vistas, salteadas


def main():
    ap = argparse.ArgumentParser(description="Guarda series históricas completas")
    ap.add_argument("--forzar", action="store_true", help="correr aunque no haya pasado una semana")
    ap.add_argument("--aviso", action="store_true", help="cartel de Windows solo si hay alertas")
    args = ap.parse_args()
    informe = [f"=== SERIES HISTÓRICAS | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    if HISTORIAL.exists() and not args.forzar:
        h = pd.read_csv(HISTORIAL, encoding="utf-8-sig")
        ok = h[h["codigo"] < 2]
        if len(ok) and datetime.now() - pd.to_datetime(ok["fecha_corrida"].iloc[-1]) < timedelta(days=DIAS_ENTRE_CORRIDAS):
            informe.append(f"Sin cambios: la última corrida fue el {ok['fecha_corrida'].iloc[-1]} (corre una vez por semana; "
                           "usar --forzar para correr igual).")
            print("\n".join(informe))
            INFORME.write_text("\n".join(informe), encoding="utf-8")
            sys.exit(0)
    codigo, alertas = 0, []
    try:
        anterior = pd.read_parquet(SALIDA) if SALIDA.exists() else pd.DataFrame(columns=["id", "fecha", "valor"])
        cat_ant = pd.read_csv(CATALOGO, encoding="utf-8-sig").set_index("id") if CATALOGO.exists() else pd.DataFrame()
        partes, catalogo = [], []
        for gen in (series_bcra(), series_datos_gob(), series_indec(), series_itcrm(), series_banco_mundial(), series_combustibles(),
                    series_utdt(), series_precios_promedio(), series_confianza_gobierno()):
            for sid, df, info in gen:
                prev = anterior[anterior["id"] == sid]
                if df is None:
                    cod = "H04" if info["error"].startswith("CSV_DESACTUALIZADO") else "H01"
                    alertas.append({"codigo": cod, "id": sid, "detalle": info["error"]})
                    if len(prev):
                        partes.append(prev)
                        if sid in cat_ant.index:
                            catalogo.append({"id": sid, **cat_ant.loc[sid].to_dict(), "estado": "no actualizada"})
                    continue
                df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
                df = df.dropna(subset=["fecha", "valor"]).drop_duplicates("fecha", keep="last").sort_values("fecha")
                if len(prev):
                    p = prev.set_index("fecha")["valor"]
                    p.index = pd.to_datetime(p.index)
                    n = df.set_index("fecha")["valor"]
                    faltan = p.index.difference(n.index)
                    if len(faltan):
                        alertas.append({"codigo": "H02", "id": sid, "detalle": f"{len(faltan)} fechas que estaban ya no vienen (se conservan)"})
                        df = pd.concat([df, p[faltan].rename("valor").reset_index()]).sort_values("fecha")
                    comun = p.index.intersection(n.index)
                    cambios = int(((p[comun] - n[comun]).abs() > 1e-9 * p[comun].abs().clip(lower=1)).sum())
                    if cambios:
                        alertas.append({"codigo": "H03", "id": sid, "detalle": f"{cambios} valores ya guardados cambiaron"})
                df["id"] = sid
                for k in ("fuente", "nombre", "frecuencia"):
                    df[k] = info[k]
                partes.append(df[["fuente", "id", "nombre", "frecuencia", "fecha", "valor"]])
                catalogo.append({"id": sid, "fuente": info["fuente"], "nombre": info["nombre"], "frecuencia": info["frecuencia"],
                                 "unidad": info.get("unidad"), "desde": df["fecha"].min().date(), "hasta": df["fecha"].max().date(),
                                 "datos": len(df), "url": info["url"], "estado": "actualizada"})
        # series guardadas que no vinieron en esta corrida (fuente entera caída o serie sacada del catálogo): se conservan
        ya = set().union(*(set(p["id"].unique()) for p in partes)) if partes else set()
        for sid, prev in anterior[~anterior["id"].isin(ya)].groupby("id"):
            partes.append(prev)
            alertas.append({"codigo": "H05", "id": sid, "detalle": "no vino en esta corrida: se conservan los datos guardados"})
            if sid in cat_ant.index:
                catalogo.append({"id": sid, **cat_ant.loc[sid].to_dict(), "estado": "no actualizada"})
        if not partes:
            raise RuntimeError("no se pudo bajar ninguna serie")
        total = pd.concat(partes, ignore_index=True)
        total["fecha"] = pd.to_datetime(total["fecha"]).dt.date
        total.to_parquet(SALIDA, index=False)
        cat = pd.DataFrame(catalogo)
        for k in ("desde", "hasta"):   # las del catálogo anterior vienen como texto: todo a texto AAAA-MM-DD
            cat[k] = cat[k].astype(str).str[:10]
        cat.to_csv(CATALOGO, index=False, encoding="utf-8-sig")
        vistas, salteadas = armar_duckdb()

        informe.append(f"Series guardadas: {cat['id'].nunique()} ({int((cat['estado'] == 'actualizada').sum())} actualizadas) | "
                       f"{len(total):,} datos | la más antigua desde {cat['desde'].min()}")
        for f, g in cat.groupby("fuente"):
            informe.append(f"  {f[:70]:70} {len(g):3} series, desde {g['desde'].min()}")
        informe.append(f"Archivos: datos\\series_historicas.parquet y datos\\series_historicas_catalogo.csv")
        informe.append(f"Base DuckDB: datos\\base_rem.duckdb con {len(vistas)} vistas ({', '.join(vistas)})")
        if salteadas:
            informe.append(f"  Sin vista (archivo vacío o ilegible): {'; '.join(salteadas)}")
        informe.append("")
        informe.append("Controles:")
        for cod, (nom, desc) in CONTROLES.items():
            informe.append(f"  {cod} {nom}: {sum(a['codigo'] == cod for a in alertas)} casos  ({desc})")
        informe.append("")
        informe.append("=" * 60)
        if alertas:
            codigo = 1
            pd.DataFrame(alertas).to_csv(ALERTAS_CSV, index=False, encoding="utf-8-sig")
            informe.append(f"ALERTAS: {len(alertas)} para revisar (detalle en datos\\alertas_historicas.csv)")
            for a in alertas[:15]:
                informe.append(f"  - {a['codigo']} {CONTROLES[a['codigo']][0]} | {a['id']} | {a['detalle'][:120]}")
            informe.append("  No se borró ningún dato guardado.")
        else:
            informe.append("ALERTAS: ninguna. Todo en orden.")
            if ALERTAS_CSV.exists():
                ALERTAS_CSV.unlink()
    except Exception as e:
        import traceback
        informe += ["", "=" * 60, f"FALLA INESPERADA: {type(e).__name__}: {e}", "No se modificó el archivo de series.",
                    "Detalle: " + " | ".join(l.strip() for l in traceback.format_exc().splitlines()[-16:-6])]
        codigo = 2
    pd.DataFrame([{"fecha_corrida": datetime.now().strftime("%Y-%m-%d %H:%M"), "codigo": codigo, "alertas": len(alertas)}]).to_csv(
        HISTORIAL, mode="a", header=not HISTORIAL.exists(), index=False, encoding="utf-8-sig")
    texto = "\n".join(informe)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")
    if args.aviso and codigo:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"Series históricas: {len(alertas)} alertas. Abrí datos\\informe_historicas.txt.",
                                             "Series históricas", (0x30 if codigo == 1 else 0x10) | 0x1000)
        except Exception:
            pass
    sys.exit(codigo)


if __name__ == "__main__":
    main()

