# replicar_rem.py
# Meta: aproximarse a la mediana del REM (no ganarle). El objetivo a pronosticar es el REM mismo.
#   A. Réplica independiente: REM(t, mes t+h) explicado solo con datos públicos conocidos en t
#      (IPC general y núcleo hasta t-1, devaluación de t hasta el día 25, emisión al Tesoro y base hasta t-1).
#   B. Réplica de la revisión: REM(t, t+h) = REM(t-1, t+h) + revisión; la revisión se explica con la sorpresa del
#      último IPC (dato de t-1 contra lo que esperaba el REM a fin de t-1) y la devaluación.
#   Referencia: "REM anterior" = el REM repite lo que dijo el mes pasado para ese mismo mes.
# También a 12 meses (REM "Próx. 12 meses").
# Estimación en tiempo real: para cada relevamiento t, solo relevamientos anteriores (ventana acumulada, mínimo 24).
# Las variables de inflación son lo que veían los analistas: INDEC desde 2017 (antes, IPC oficial del BCRA).
# Salida: datos/informe_replica_rem.txt y datos/replica_rem.csv
# Uso: py -3.13 replicar_rem.py
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm

import datos_macro as D
from backtest_rem import diebold_mariano, ols_pred

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIA_CORTE_TC = 25
MIN_FILAS = 24
MIN_DIAS_25 = 8     # días hábiles válidos mínimos (hasta el día 25) para usar la brecha de un mes
MIN_DIAS_MES = 10   # ídem para el mes completo
HORIZONTES = range(0, 7)
INFORME = D.CARPETA / "informe_replica_rem.txt"
SALIDA = D.CARPETA / "replica_rem.csv"
REGISTRO = D.CARPETA / "registro_pronosticos.csv"   # un renglón por corrida x horizonte; nunca se borra (prueba hacia adelante)
# Desde el 15-oct-2026 A incluye la mediana de las expectativas de inflación de los hogares (UTDT): ei_med_m (equivalente
# mensual) en m0-m6 y ei_med (% anual) a 12 meses. "A sin EI" es el A anterior, para comparar.
A_SIN_EI_MENSUAL = ["p1", "p2", "p3", "p12", "nuc1", "dev0", "dev1", "emiT12"]
A_SIN_EI_12 = ["inf12", "inf3", "nuc3", "dev3", "emiT12", "base12"]
A_GENERAL_MENSUAL = A_SIN_EI_MENSUAL + ["ei_med_m"]   # A hasta el 16-oct-2026 ("A general"): inflación general de t-1..t-3
# Desde el 16-oct-2026 (decisión de Marcos) A usa la NÚCLEO de los últimos 3 meses en lugar de la inflación general:
# así un salto de tarifas (regulados) no se arrastra como si fuera a continuar (caso jun-2026). La estacionalidad (p12)
# sigue con el general. En 2025-26: m1 0,41 -> 0,32 pp de distancia al REM; dentro de P10-P90 m1 38% -> 52%, m2 57% -> 81%
A_MENSUAL = ["nuc1", "nuc_2", "nuc_3", "p12", "dev0", "dev1", "emiT12", "ei_med_m"]
# desde el 15-oct-2026 el 12m de A incluye además la devaluación esperada a un año del dólar futuro (CEM, contrato a 9 meses
# llevado a 12; datos desde 2020: el 12m de A se estima recién desde ~2022). "A 12m sin futuros" queda para comparar
A_12_NIVELES = A_SIN_EI_12 + ["ei_med", "fut12"]      # versión lineal en niveles (hasta el 15-oct-2026)
A_12_SIN_FUT = A_SIN_EI_12 + ["ei_med"]
# 12m de A EN LOGARITMOS (decisión de Marcos, 15-oct-2026): REM12, expectativa de hogares, devaluación esperada (futuros) e
# inflación de los últimos 12 meses, todos como log(1+x) x 100. En niveles, una recta estimada con inflaciones de 50-160%
# subestimaba ~5-7 pp con inflaciones de 20% (2025-26); en logaritmos: distancia al REM 8,8 pp y 80% dentro de P10-P90
A_12 = ["inf12", "ei_med_l", "fut12"]
EN_LOG = lambda tipo, H, cols: tipo == "nivel" and H == "12m" and ("ei_med_l" in cols or "ei_prom_l" in cols)   # cuándo se estima en logaritmos
B_MENSUAL = ["sorpresa", "dev0", "dev1"]
B_12 = ["sorpresa", "dev0", "dev1", "dbrecha"]   # 16-oct-2026: + variación de la brecha; revisión en logaritmos (ver REV_LOG)
B_12_PUNTOS = ["sorpresa", "dev0", "dev1"]          # B a 12 meses hasta el 15-oct-2026 (revisión en puntos), para comparar
# B a 12 meses revisa el REM anterior EN LOGARITMOS (16-oct-2026): rev_l = log(1+REM12) - log(1+REM12 anterior), x100.
# En 2025-26 la distancia al REM baja de 1,62 a 1,03 pp (REM anterior sin cambios: 1,18); en toda la muestra ~igual.
REV_LOG = lambda tipo, H, cols: tipo == "revision" and H == "12m" and "dbrecha" in cols
def y_de(tipo, H, cols):
    if tipo == "nivel":
        return "rem_l" if EN_LOG(tipo, H, cols) else "rem"
    return "rev_l" if REV_LOG(tipo, H, cols) else "rev"
VARIANTES_MCO = {"A MCO"}   # variantes de A que se siguen estimando por MCO (para comparar)


def estimar(nom, tipo, H, X, y, x0):
    """A mensual (nivel, m0-m6) se estima con regresión ROBUSTA de Huber desde el 16-oct-2026: le resta peso a los meses
    extremos (2023-24) que deformaban los coeficientes. Prueba en tiempo real: 2019-24 distancia al REM m1 1,01 -> 0,93,
    m2 1,11 -> 0,95; 2025-26 error contra el IPC real m2 0,66 -> 0,58, m4 0,49 -> 0,40. El 12m (en logaritmos) y B
    (revisión) siguen con MCO."""
    if tipo == "nivel" and H != "12m" and nom not in VARIANTES_MCO:
        try:
            Xc = np.column_stack([np.ones(len(X)), X])
            b = sm.RLM(y, Xc, M=sm.robust.norms.HuberT()).fit().params
            return float(np.r_[1, x0] @ b)
        except Exception:
            pass
    return ols_pred(X, y, x0)


def elegir_I(preds, fila):
    """Nuestro pronóstico de la inflación: 'I+dólar' si la devaluación esperada del mes supera UMBRAL_DOLAR; si no,
    'I sin dólar'."""
    dm = fila.get("dolar_m", np.nan) if hasattr(fila, "get") else np.nan
    con = preds.get("I+dólar", np.nan)
    if dm is not None and not pd.isna(dm) and dm > UMBRAL_DOLAR and not pd.isna(con):
        return con
    return preds.get("I sin dólar", np.nan)


def elegir_A(preds, fila):
    """Nuestro pronóstico del REM: 'A+dólar' si la devaluación esperada del mes supera UMBRAL_DOLAR_A; si no, A."""
    dm = fila.get("dolar_m", np.nan) if hasattr(fila, "get") else np.nan
    con = preds.get("A+dólar", np.nan)
    if dm is not None and not pd.isna(dm) and dm > UMBRAL_DOLAR_A and not pd.isna(con):
        return con
    return preds.get("A", np.nan)


def deshacer(tipo, H, cols, p, rem_ant):
    """Pasa la predicción de la regresión a % (nivel o revisión, en puntos o en logaritmos)."""
    if tipo == "nivel":
        return np.expm1(p / 100) * 100 if EN_LOG(tipo, H, cols) else p
    if REV_LOG(tipo, H, cols):
        return np.expm1((np.log1p(rem_ant / 100) * 100 + p) / 100) * 100
    return rem_ant + p
# salarios (índice de salarios INDEC, total registrado): en el relevamiento de t el último publicado es el de t-2
#   sal1 = variación del dato recién publicado (t-2); sal3 = variación mensual promedio t-4..t-2; sal12 = interanual a t-2
VARIANTES = {   # nombre: (tipo, columnas mensuales, columnas 12 meses)
    "A sin EI": ("nivel", A_SIN_EI_MENSUAL, A_SIN_EI_12),
    "A": ("nivel", A_MENSUAL, A_12),
    "A 12m sin futuros": ("nivel", A_MENSUAL, A_12_SIN_FUT),
    "A 12m niveles": ("nivel", A_MENSUAL, A_12_NIVELES),  # versión anterior (lineal en niveles, con futuros)
    "A con futuros 12m": ("nivel", A_MENSUAL, A_12),      # igual a "A": nombre para la prueba de la sección 4b
    "A+salarios": ("nivel", A_MENSUAL + ["sal3"], A_12 + ["sal12"]),
    "B": ("revision", B_MENSUAL, B_12),
    "B 12m puntos": ("revision", B_MENSUAL, B_12_PUNTOS),
    "B+salarios": ("revision", B_MENSUAL + ["sal1"], B_12 + ["sal1"]),
    # brecha cambiaria: CCL (argentinadatos, sin días congelados) / A3500 - 1, en %; brecha0 = mes t hasta el día 25,
    # dbrecha = brecha0 - brecha del mes t-1 completo
    "A+brecha": ("nivel", A_MENSUAL + ["brecha0"], A_12 + ["brecha0"]),
    "A+sal+brecha": ("nivel", A_MENSUAL + ["sal3", "brecha0"], A_12 + ["sal12", "brecha0"]),
    "B+brecha": ("revision", B_MENSUAL + ["dbrecha"], B_12 + ["dbrecha"]),
    # categorías del IPC INDEC (desde ene-2017): reg1/reg3 = regulados t-1 y promedio t-3..t-1; est1 = estacionales t-1;
    # reg12 = regulados interanual a t-1; sorpresa_nuc = núcleo de t-1 menos el nowcast de núcleo del REM
    # con categorías se saca p1 (el general de t-1 ya es núcleo + regulados + estacionales: estaría repetido)
    "A+regulados": ("nivel", [c for c in A_MENSUAL if c != "p1"] + ["reg1", "reg3", "est1"], A_12 + ["reg12"]),
    "A+sal+brecha+reg": ("nivel", [c for c in A_MENSUAL if c != "p1"] + ["sal3", "brecha0", "reg1", "reg3", "est1"],
                         A_12 + ["sal12", "brecha0", "reg12"]),
    "B+núcleo": ("revision", B_MENSUAL + ["sorpresa_nuc"], B_12 + ["sorpresa_nuc"]),
    "B+regulados": ("revision", B_MENSUAL + ["reg1"], B_12 + ["reg1"]),
    # expectativas de inflación de los hogares (UTDT, próximos 12 meses; desde ago-2006). Se publican a mitad del mes t,
    # antes del relevamiento del REM de fin de t: se usa el dato de t. En B: cambio contra t-1. (El promedio en vez de la
    # mediana se probó el 15-oct-2026 y anduvo peor: ver CLAUDE.md)
    "B+EI mediana": ("revision", B_MENSUAL + ["dei_med_m"], B_12 + ["dei_med"]),
    # confianza del consumidor (UTDT, misma encuesta; dato de t). Probada el 15-oct-2026: sola no sirve; con EI ayuda poco y
    # sin significancia. Se deja para seguirla
    "A+ICC": ("nivel", A_MENSUAL + ["icc"], A_12 + ["icc"]),
}
# pruebas de variables: variante -> (variante base, columnas agregadas en m0-m6, columnas agregadas en 12m).
# Todas las A de abajo ya incluyen la expectativa: miden qué agrega cada variable ENCIMA de A con EI
PRUEBAS = {"A": ("A sin EI", ["ei_med_m"], ["ei_med_l"]),   # 12m: A en logaritmos (cambian EI y escala a la vez)
           "A+salarios": ("A", ["sal3"], ["sal12"]), "A+brecha": ("A", ["brecha0"], ["brecha0"]),
           "A+sal+brecha": ("A", ["sal3", "brecha0"], ["sal12", "brecha0"]),
           "A+regulados": ("A", ["reg1", "reg3", "est1"], ["reg12"]),
           "A+sal+brecha+reg": ("A", ["sal3", "brecha0", "reg1", "reg3", "est1"], ["sal12", "brecha0", "reg12"]),
           "A+ICC": ("A", ["icc"], ["icc"]), "B+EI mediana": ("B", ["dei_med_m"], ["dei_med"]),
           "A con futuros 12m": ("A 12m niveles", [], ["ei_med_l", "fut12"])}   # 12m: logaritmos contra niveles
ICC_SUB = ["icc_pers", "icc_macro", "icc_dur"]   # subíndices de confianza (se calculan; probados el 15-oct-2026, empeoraban)
# datos nuevos (bajados el 14/15-oct-2026), probados encima de A con EI. Disponibilidad al cierre del REM de fin de t:
#   IPIM (mayoristas INDEC) t-1 | combustibles: nafta súper, mediana país, hasta el día 25 de t (E:\combustibles) |
#   fiscal (IMIG resultado primario) t-2 | RIPTE t-2 | ITCRM diario hasta el día 25 de t | commodities Banco Mundial t-1
NUEVAS = {"A+IPIM": (["ipim1", "ipim3"], ["ipim12"]), "A+combustibles": (["dcomb0", "dcomb1"], ["dcomb12"]),
          "A+fiscal": (["fiscal12"], ["fiscal12"]), "A+RIPTE": (["ripte3"], ["ripte12"]), "A+ITCRM": (["itcrm0"], ["itcrm0"]),
          "A+commodities": (["alim12", "energ12"], ["alim12", "energ12"])}
NUEVAS_B = {"B+IPIM": ["ipim1"], "B+combustibles": ["dcomb0"], "B+RIPTE": ["ripte1"]}
# A con NÚCLEO en lugar de la inflación general de los últimos 3 meses (16-oct-2026): para que un salto de tarifas
# (regulados) no se arrastre como si fuera a continuar. La estacionalidad (p12) sigue con el general.
# Desde el 16-oct-2026 ese es A (A_MENSUAL). Quedan para comparar: "A general" (el A anterior) y "A núcleo+reg".
VARIANTES["A general"] = ("nivel", A_GENERAL_MENSUAL, A_12)
VARIANTES["A MCO"] = ("nivel", A_MENSUAL, A_12)        # mismo A estimado por MCO (hasta el 16-oct-2026)
# "I" = NUESTRO PRONÓSTICO DE LA INFLACIÓN (16-oct-2026; objetivo de Marcos: ganarle al REM pronosticando la inflación).
# Igual que A (robusta, núcleo) pero con el PROMEDIO de las expectativas de los hogares (se mueve de forma continua; la
# mediana queda clavada de a 5 puntos). Se entrena igual que A (con el REM como "maestro", no como dato de entrada) y se
# evalúa contra el IPC real (sección 10). En 2025-26: error contra el IPC real m3 0,36 · m4 0,26 · m6 0,33 (REM 0,53 · 0,54
# · 0,73); 12m 3,4 pp (REM 10,8). No usa el REM como variable.
I_MENSUAL = [c if c != "ei_med_m" else "ei_prom_m" for c in A_MENSUAL]
I_12 = ["inf12", "ei_prom_l", "fut12"]
# I con DÓLAR ESPERADO (17-oct-2026): devaluación que espera A-dólar (replicar_tc.py) para el mes pronosticado y el
# anterior. Solo se usa si la devaluación esperada del mes supera UMBRAL_DOLAR (épocas agitadas); si no, I sin dólar.
# Prueba en tiempo real: 2022-24 error contra el IPC real m3 3,87 -> 2,44, m4 4,38 -> 3,40, m5 4,73 -> 3,85; en 2025-26
# casi no se activa (5% de los meses) y queda igual.
UMBRAL_DOLAR = 3.0
# Lo mismo para A (nuestro pronóstico del REM), con umbral de 4%: 2022-24 distancia al REM m3 1,56 -> 1,33, m5 1,58 ->
# 1,04 (dentro de P10-P90 m4 62% -> 86%); 2025-26 igual (no se activa). "A sin dólar" queda para comparar.
UMBRAL_DOLAR_A = 4.0
VARIANTES["A+dólar"] = ("nivel", A_MENSUAL + ["dolar_m", "dolar_m1"], A_12)
VARIANTES["I sin dólar"] = ("nivel", I_MENSUAL, I_12)
VARIANTES["I+dólar"] = ("nivel", I_MENSUAL + ["dolar_m", "dolar_m1"], I_12)
PRUEBAS["A MCO"] = ("A", [], [])
VARIANTES["A núcleo+reg"] = ("nivel", A_MENSUAL + ["reg1", "est1"], A_12)
PRUEBAS["A general"] = ("A", ["p1", "p2", "p3"], [])
PRUEBAS["A núcleo+reg"] = ("A", ["reg1", "est1"], [])
# estacionalidad sin arrastrar tarifazos (16-oct-2026): p12 (general, mismo mes del año anterior) reemplazado por
# p12n (núcleo) o por estac3 (desvío típico del mes, promedio de 3 años)
VARIANTES["A est.núcleo"] = ("nivel", [c if c != "p12" else "p12n" for c in A_MENSUAL], A_12)
VARIANTES["A est.3años"] = ("nivel", [c if c != "p12" else "estac3" for c in A_MENSUAL], A_12)
PRUEBAS["A est.núcleo"] = ("A", ["p12n"], [])
PRUEBAS["A est.3años"] = ("A", ["estac3"], [])
# expectativa de los hogares relativa (16-oct-2026): en lugar del nivel de la expectativa, la inflación de 12 meses
# (mensualizada) más la diferencia entre la expectativa y esa inflación (así el modelo puede pesar distinto cada parte)
VARIANTES["A EI relativa"] = ("nivel", [c for c in A_MENSUAL if c != "ei_med_m"] + ["inf12m", "ei_gap"], A_12)
PRUEBAS["A EI relativa"] = ("A", ["inf12m", "ei_gap"], [])
# hacienda MAG Cañuelas (15-oct-2026): variación de novillitos+vaquillonas del mes hasta el día 25 y del mes anterior
NUEVAS["A+carne"] = (["carne25", "carne1"], [])
NUEVAS_B["B+carne"] = ["carne25"]
# futuros de dólar (CEM, desde ene-2020): A = devaluación esperada para el mes pronosticado (12m: a un año);
# B = cambio de esa expectativa contra la curva del mes anterior. Muestra corta: no entran en la muestra común de la sección 1
NUEVAS["A+futuros"] = (["fut_m"], [])                             # (el 12m de A ya incluye fut12)
NUEVAS["A+futuros tope"] = (["fut_m_tope"], [])                    # tope 15% mensual
NUEVAS["A+futuros prom"] = (["fut_prom"], [])                      # promedio desde hoy hasta el mes pronosticado, con tope
NUEVAS_B["B+futuros"] = ["dfut_m"]
SOLO_DESDE_2020 = {"A+futuros", "A+futuros tope", "A+futuros prom", "B+futuros", "A+dólar", "I+dólar"}
for _n, (_cm, _c12) in NUEVAS.items():
    VARIANTES[_n] = ("nivel", A_MENSUAL + _cm, A_12 + _c12)
    PRUEBAS[_n] = ("A", _cm, _c12)
for _n, _c in NUEVAS_B.items():
    VARIANTES[_n] = ("revision", B_MENSUAL + _c, B_12 + _c)
    PRUEBAS[_n] = ("B", _c, _c)
VARIANTES["B+futuros"] = ("revision", B_MENSUAL + ["dfut_m"], B_12 + ["dfut12"])   # a 12m: cambio de la devaluación esperada a un año
PRUEBAS["B+futuros"] = ("B", ["dfut_m"], ["dfut12"])
# sin columnas repetidas (p. ej. "B+brecha": desde el 16-oct-2026 B a 12m ya incluye dbrecha)
VARIANTES = {k: (tp, list(dict.fromkeys(cm)), list(dict.fromkeys(c12))) for k, (tp, cm, c12) in VARIANTES.items()}


# ---------- métodos de estimación para A (15-oct-2026) ----------
# Regla: los métodos se eligen mirando SOLO los relevamientos hasta CORTE_SELECCION. 2025-2026 queda reservado como prueba
# final: el informe lo muestra solo con --ver-prueba (correrlo una vez, al final, con el método ya elegido).
CORTE_SELECCION = pd.Period("2024-12", "M")
METODOS = {   # nombre: (variante de columnas, semivida en meses de los pesos (None = todos iguales), penalización ridge)
    "A (MCO)": ("A", None, 0.0),
    "A pond24": ("A", 24, 0.0), "A pond36": ("A", 36, 0.0),
    "A ridge1": ("A", None, 1.0), "A ridge10": ("A", None, 10.0),
    "A pond36+ridge1": ("A", 36, 1.0), "A pond24+ridge10": ("A", 24, 10.0),
}


def pred_metodo(X, y, x0, edades, semivida=None, lam=0.0):
    """Regresión ponderada (pesos 0,5^(edad/semivida): una fila de hace `semivida` meses pesa la mitad) con penalización
    ridge sobre los coeficientes de las variables estandarizadas (la constante no se penaliza)."""
    w = np.ones(len(y)) if semivida is None else 0.5 ** (np.asarray(edades, float) / semivida)
    w = w / w.sum()
    mx = w @ X
    sx = np.sqrt(w @ (X - mx) ** 2)
    sx[sx == 0] = 1.0
    Z = (X - mx) / sx
    my = w @ y
    A = Z.T @ (Z * w[:, None]) + lam / len(y) * np.eye(Z.shape[1])
    b = np.linalg.solve(A, Z.T @ (w * (y - my)))
    return float(my + ((x0 - mx) / sx) @ b)


def combustible_cortes(cortes, producto="Nafta (súper)%"):
    """Precio mediano en surtidor (país, horario diurno) vigente en cada momento de `cortes`, desde la base de
    actualizar_combustibles.py. Mismo criterio que guardar_historicas: cada precio rige hasta el próximo cambio de la
    estación, con tope de 6 meses. Si no está el disco E:, devuelve vacío (las variantes con combustibles quedan afuera)."""
    import duckdb
    base = "E:/combustibles/precios.parquet"
    try:
        con = duckdb.connect()
        con.register("cortes", pd.DataFrame({"ts": pd.to_datetime(cortes)}))
        r = con.execute(f"""
            with p as (select * from read_parquet('{base}') where producto like '{producto}'),
            iv as (select f, precio, least(coalesce(lead(f) over (partition by est, producto order by f), current_timestamp),
                                          f + interval 6 month) as hasta from p)
            select c.ts, median(iv.precio) as precio from cortes c join iv on iv.f <= c.ts and iv.hasta > c.ts group by c.ts""").df()
        return r.set_index("ts")["precio"].sort_index()
    except Exception:
        return pd.Series(dtype=float)
# réplica A que se usa en el pronóstico de la sección 5 (se elige mirando 1 y 1b). Desde el 15-oct-2026, "A" (con EI): con la
# expectativa adentro, salarios, brecha y regulados ya no agregan (ver 4b) y A es la más simple
A_PRONOSTICO = "A"


SENALES = [   # (columna, descripción, unidad): señales de que el mes puede ser agitado (sección 8)
    ("dfut12", "Devaluación esperada a 12 meses (futuros): cambio contra el mes anterior", "pp"),
    ("dfut_m", "Devaluación esperada para el mes que viene (futuros): cambio contra el mes anterior", "pp"),
    ("fut_m", "Devaluación esperada para el mes que viene (futuros): nivel", "%"),
    ("dbrecha", "Brecha CCL/A3500: cambio contra el mes anterior", "pp"),
    ("dei", "Expectativa de inflación de los hogares (UTDT): cambio", "pp"),
    ("dicc", "Confianza del consumidor (UTDT): cambio", "puntos"),
    ("dicg", "Confianza en el gobierno (UTDT): cambio", "puntos"),
    ("sorpresa", "Sorpresa del último IPC contra lo que esperaba el REM", "pp"),
    ("dev0", "Devaluación del mes (A3500 hasta el día 25)", "%"),
    ("disp_rem", "Desacuerdo entre consultoras en el REM anterior ((P90-P10)/mediana, m3)", "cociente"),
    ("vol_rel", "Volumen de futuros de dólar contra los 12 meses previos", "log"),
    ("oi_rel", "Interés abierto de futuros de dólar contra los 12 meses previos", "log")]
UMBRAL_ALERTA = 2.5    # desvíos estándar contra la mediana de la historia previa (mínimo 24 meses)
REGISTRO_ALERTAS = D.CARPETA / "registro_alertas.csv"


def alertas(T, O, t, rasgos, hist_s, fu_todo):
    """Sección 8. Cada señal se compara con su propia historia previa (z = (valor - mediana) / desvío). Alerta si
    |z| > UMBRAL_ALERTA. Semáforo: 0 señales = verde, 1 = amarillo, 2 o más = rojo. Prueba histórica (15-oct-2026): con
    2 o más señales, el error de A contra el REM fue ~4 veces el de los meses sin alertas (m1 y 12m)."""
    por_h = lambda H, c: T[T.H == H].set_index("origen")[c]
    idx = pd.period_range(T["origen"].min(), t, freq="M")
    S = pd.DataFrame(index=idx)
    for c, H, col in [("dfut12", "12m", "dfut12"), ("dfut_m", "m1", "dfut_m"), ("fut_m", "m1", "fut_m"), ("dbrecha", "m1", "dbrecha"),
                      ("dei", "12m", "dei_med"), ("dicc", "12m", "dicc"), ("sorpresa", "m1", "sorpresa"), ("dev0", "m1", "dev0")]:
        S[c] = por_h(H, col)
    r3 = T[T.H == "m3"].set_index("origen")
    S["disp_rem"] = ((r3["percentil_90"] - r3["percentil_10"]) / r3["rem"]).reindex(idx).shift(1)
    fu = fu_todo[fu_todo["symbol"].str.fullmatch(r"DLR\d{6}")].copy()
    fu["fecha"] = pd.to_datetime(fu["fecha"])
    fu = fu[fu["fecha"].dt.day <= DIA_CORTE_TC]
    fu["mes"] = fu["fecha"].dt.to_period("M")
    vol = fu.groupby("mes")["volume"].sum() / fu.groupby("mes")["fecha"].nunique()          # volumen por día hábil
    oi = fu.groupby(["mes", "fecha"])["openInterest"].sum().groupby("mes").last()
    S["vol_rel"] = np.log(vol / vol.shift(1).rolling(12, min_periods=6).mean())
    S["oi_rel"] = np.log(oi / oi.shift(1).rolling(12, min_periods=6).mean())
    icg = hist_s[hist_s["id"] == "utdt_icg"]
    icg = pd.Series(icg["valor"].values, index=pd.to_datetime(icg["fecha"]).dt.to_period("M")).groupby(level=0).last().sort_index()
    S["dicg"] = icg.diff()
    # valores del mes en curso (todavía sin REM publicado)
    if t not in set(T["origen"]):
        r1, r0 = rasgos(t, 1), rasgos(t, 0)
        for c, v in [("dfut12", r0.get("dfut12")), ("dfut_m", r1.get("dfut_m")), ("fut_m", r1.get("fut_m")), ("dbrecha", r1.get("dbrecha")),
                     ("dei", r0.get("dei_med")), ("dicc", r0.get("dicc")), ("sorpresa", r1.get("sorpresa")), ("dev0", r1.get("dev0"))]:
            S.loc[t, c] = v
    S = S.astype(float)

    def z_de(x, i):
        prev = x.iloc[:i].dropna()
        if len(prev) < 24 or np.isnan(x.iloc[i]) or prev.std() == 0:
            return np.nan
        return (x.iloc[i] - prev.median()) / prev.std()

    Z = pd.DataFrame({c: [z_de(S[c], i) for i in range(len(S))] for c, _, _ in SENALES}, index=S.index)
    L = ["", f"8) ALERTAS: ¿puede fallar este mes nuestro pronóstico del REM? (relevamiento {t})",
         f"   Cada señal se compara con su historia: alerta si se aparta más de {UMBRAL_ALERTA} desvíos de lo normal.",
         "   Semáforo: VERDE = ninguna señal en alerta; AMARILLO = una; ROJO = dos o más (mes agitado: desconfiar de A y B).",
         f"   {'señal':80} {'hoy':>8} {'normal':>8} {'desvíos':>8}"]
    n_al, activas = 0, []
    for c, desc, uni in SENALES:
        x, z = S[c].get(t, np.nan), Z[c].get(t, np.nan)
        normal = S[c][S.index < t].median()
        if np.isnan(x):
            L.append(f"   {desc + ' [' + uni + ']':80} {'sin dato todavía':>26}")
            continue
        marca = "  <-- ALERTA" if abs(z) > UMBRAL_ALERTA else ""
        n_al += bool(marca)
        if marca:
            activas.append(c)
        L.append(f"   {desc + ' [' + uni + ']':80} {x:8.2f} {normal:8.2f} {z:+8.1f}{marca}")
    nivel = "VERDE" if n_al == 0 else ("AMARILLO" if n_al == 1 else "ROJO")
    L.append(f"   SEMÁFORO: {nivel} ({n_al} señal{'es' if n_al != 1 else ''} en alerta{': ' + ', '.join(activas) if activas else ''}).")
    # prueba histórica: error de A contra el REM según cuántas señales había en alerta ese mes
    cuenta = (Z.abs() > UMBRAL_ALERTA).sum(axis=1)
    L.append("   Prueba histórica (mismas reglas, en tiempo real): distancia media de A al REM según el semáforo")
    for H in ("m1", "12m"):
        o = O[O.H == H].set_index("origen")
        e = (o["A"] - o["rem"]).abs().dropna()
        k = cuenta.reindex(e.index)
        partes = [f"{nom} {e[m].mean():.2f} (n={m.sum()})" for nom, m in
                  (("verde", k == 0), ("amarillo", k == 1), ("rojo", k >= 2)) if m.sum()]
        L.append(f"     {H:4} " + " · ".join(partes) + "  " + ("pp" if H == "m1" else "pp de inflación a 12 meses"))
    L.append(f"     Meses en rojo: {', '.join(str(p) for p in cuenta[(cuenta >= 2) & (cuenta.index < t)].index)}")
    fila = pd.DataFrame([{"fecha_corrida": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "relevamiento": str(t),
                          "semaforo": nivel, "senales_en_alerta": n_al, "cuales": " ".join(activas),
                          **{f"z_{c}": Z[c].get(t, np.nan) for c, _, _ in SENALES}}])
    fila.to_csv(REGISTRO_ALERTAS, mode="a", header=not REGISTRO_ALERTAS.exists(), index=False, encoding="utf-8-sig", float_format="%.3f")
    return L


REGISTRO_TERMOMETRO = D.CARPETA / "registro_termometro.csv"
REGISTRO_INFLACION = D.CARPETA / "registro_inflacion.csv"
I_HOY = {}   # pronóstico de la inflación ("I") del relevamiento en curso, por horizonte (lo llena la sección 5)


def pronostico_inflacion(O, t, lp, rem12):
    """Sección 10 (desde el 16-oct-2026): NUESTRO PRONÓSTICO DE LA INFLACIÓN ("I"). Objetivo de Marcos: ganarle al REM
    pronosticando la inflación (el IPC que efectivamente se publica). I = A con el PROMEDIO de las expectativas de los
    hogares (VARIANTES["I"]); en el mes en curso, corregido con el termómetro del súper. No usa el REM como variable.
    Probado y descartado (16-oct-2026): entrenar contra el IPC real (aprende tarde), corregir por sesgo reciente, combinar
    con inercia pura, reglas de semáforo o de sesgo del REM. Evaluación: error contra el IPC real, % de meses en que gana
    y Diebold-Mariano (HLN) contra el REM. El pronóstico de cada corrida va a datos\\registro_inflacion.csv."""
    L = ["", "10) NUESTRO PRONÓSTICO DE LA INFLACIÓN (objetivo: ganarle al REM pronosticando el IPC real)",
         "    I = A con el promedio de las expectativas de los hogares (mes en curso: + termómetro del súper). Sin usar el REM.",
         "    Error medio contra el IPC real (pp), % de meses en que I gana y p de Diebold-Mariano contra el REM (p<0,05 = ventaja real):"]
    P = np.exp(lp)
    real12 = pd.Series({o: (P.get(o + 12) / P.get(o) - 1) * 100 for o in P.index if (o + 12) in P.index})
    E = O[["origen", "H", "I", "rem", "real"]].copy()
    m12 = E["H"] == "12m"
    E.loc[m12, "real"] = E.loc[m12, "origen"].map(real12)
    orden = [f"m{h}" for h in HORIZONTES] + ["12m"]
    for nom, mask in (("2019-2024", E["origen"] <= CORTE_SELECCION), ("2025-26 (régimen actual)", E["origen"] > CORTE_SELECCION),
                      ("Todo", E["origen"] > pd.Period("2000-01", "M"))):
        q = E[mask].dropna(subset=["real", "rem", "I"])
        hs = [H for H in orden if (q.H == H).sum() >= 3]
        if not hs:
            continue
        L += [f"    {nom}:", f"      {'':12}" + "".join(f"{H:>7}" for H in hs)]
        for etq, c in (("I", "I"), ("REM", "rem")):
            L.append(f"      {etq:12}" + "".join(f"{(q[q.H == H][c] - q[q.H == H]['real']).abs().mean():7.2f}" for H in hs))
        L.append(f"      {'I gana en':12}" + "".join(
            f"{((q[q.H == H]['I'] - q[q.H == H]['real']).abs() < (q[q.H == H]['rem'] - q[q.H == H]['real']).abs()).mean():7.0%}" for H in hs))
        ps = []
        for H in hs:
            g = q[q.H == H].sort_values("origen")
            h = 12 if H == "12m" else max(int(H[1:]), 1)
            try:
                ps.append(f"{diebold_mariano((g['I'] - g['real']).values, (g['rem'] - g['real']).values, h):7.2f}")
            except Exception:
                ps.append(f"{'-':>7}")
        L.append(f"      {'p (DM)':12}" + "".join(ps))
    # pronóstico de hoy: I (sección 5); en m0, corregido hacia el INDEC con el termómetro del súper
    corr = ""
    p_hoy = dict(I_HOY)
    try:
        M = pd.read_parquet(D.CARPETA / "sepa_indice_mensual.parquet")
        M = M[M["region"] == "Nacional"].copy()
        M.index = pd.PeriodIndex(M["mes"], freq="M")
        o = O[O.H == "m0"].copy()
        o.index = pd.PeriodIndex(o["origen"], freq="M")
        X = o[["I", "real"]].join(M["variacion_25_pct"]).dropna()
        s_t = M["variacion_25_pct"].get(t, np.nan)
        if len(X) >= 10 and not np.isnan(s_t) and not np.isnan(p_hoy.get("m0", np.nan)):
            b = sm.OLS(X["real"] - X["I"], sm.add_constant(X[["variacion_25_pct"]])).fit().params
            p_hoy["m0"] = p_hoy["m0"] + b["const"] + b["variacion_25_pct"] * s_t
            corr = f" (mes en curso con termómetro del súper {s_t:+.2f}%)"
    except Exception:
        pass
    reg, linea = [], []
    for H in orden:
        p = p_hoy.get(H, np.nan)
        if np.isnan(p):
            continue
        mes = "próx. 12m" if H == "12m" else str(t + int(H[1:]))
        linea.append(f"{mes}: {p:.2f}")
        reg.append({"fecha_corrida": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "relevamiento": str(t), "H": H,
                    "objetivo": "12m" if H == "12m" else mes, "pronostico_inflacion": p,
                    "fuente": "I + termómetro" if (H == "m0" and corr) else "I"})
    if reg:
        pd.DataFrame(reg).to_csv(REGISTRO_INFLACION, mode="a", header=not REGISTRO_INFLACION.exists(), index=False,
                                 encoding="utf-8-sig", float_format="%.4f")
        L.append(f"    Pronóstico de la inflación hoy{corr} (% mensual; 12m en %): " + " | ".join(linea))
    # prueba hacia adelante: último pronóstico registrado dentro de cada mes, contra el IPC publicado y el REM
    if REGISTRO_INFLACION.exists():
        ri = pd.read_csv(REGISTRO_INFLACION, encoding="utf-8-sig")
        ri = ri[pd.to_datetime(ri["fecha_corrida"]).dt.to_period("M").astype(str) == ri["relevamiento"]]
        ri = ri.sort_values("fecha_corrida").groupby(["relevamiento", "H"]).last().reset_index()
        ri["origen"] = pd.PeriodIndex(ri["relevamiento"], freq="M")
        ev = ri.merge(E[["origen", "H", "rem", "real"]], on=["origen", "H"]).dropna(subset=["real", "rem"])
        if len(ev):
            L.append(f"    Prueba hacia adelante ({len(ev)} pronósticos con IPC publicado): error I "
                     f"{(ev.pronostico_inflacion - ev.real).abs().mean():.2f} | REM {(ev.rem - ev.real).abs().mean():.2f}")
        else:
            L.append("    Prueba hacia adelante: todavía no hay meses con IPC publicado desde que se registra.")
    return L


def grilla_anual(O, t, pi, rem_all):
    """Sección 11 (17-oct-2026): GRILLA COMPLETA como la del REM. Además de los 7 meses y los próximos 12 meses, el REM
    pide la inflación del año calendario (dic/dic) del año en curso y los 2 siguientes, y los próximos 24 meses.
    Regla (para A e I, en tiempo real): año en curso = meses ya publicados x pronósticos m0..m6 que caen en el año x, para
    los meses que faltan después del m6, la tasa mensual implícita en el pronóstico de 12 meses. Años siguientes y 24
    meses: se extiende esa tasa mensual (SUPUESTO: la inflación se mantiene; los modelos no tienen información propia
    más allá de 12 meses). Evaluación: distancia al REM y, para años cerrados, error contra la inflación real dic/dic."""
    L = ["", "11) GRILLA COMPLETA (años calendario y próximos 24 meses, como el REM)",
         "    Año en curso: meses publicados + pronósticos mensuales + tasa del 12m para los meses que faltan. Años siguientes y",
         "    24 meses: se extiende la tasa mensual del pronóstico de 12 meses (supuesto: la inflación se mantiene)."]
    g = rem_all[rem_all["variable_std"] == "ipc_general"]
    rem_anio = g[g["tipo_periodo"] == "anio"].assign(anio=lambda x: x["periodo_original"].astype(str).str[:4].astype(int))
    rem_anio = rem_anio.groupby(["origen", "anio"])["mediana"].mean()
    rem24 = g[(g["tipo_periodo"] == "proximos_meses") & (g["horizonte_meses"] == 24)].groupby("origen")["mediana"].mean()
    real_anio = {}
    for y in range(2017, t.year + 1):
        ms = [pd.Period(f"{y}-{m:02d}", "M") for m in range(1, 13)]
        if all(m in pi.index for m in ms):
            real_anio[y] = (np.prod([1 + pi[m] / 100 for m in ms]) - 1) * 100

    def armar(o, pred):
        """pred: dict H -> pronóstico (m0..m6 en % mensual, 12m en %). Devuelve {año: %, '24m': %}."""
        if any(np.isnan(pred.get(H, np.nan)) for H in ["12m"] + [f"m{h}" for h in range(7)]):
            return None
        tasa = (1 + pred["12m"] / 100) ** (1 / 12)
        out = {}
        for k, y in enumerate((o.year, o.year + 1, o.year + 2)):
            f = 1.0
            for mm in range(1, 13):
                m = pd.Period(f"{y}-{mm:02d}", "M")
                if m <= o - 1:
                    if m not in pi.index:
                        return None
                    f *= 1 + pi[m] / 100
                elif (m - o).n <= 6:
                    f *= 1 + pred[f"m{(m - o).n}"] / 100
                else:
                    f *= tasa
            out[y] = (f - 1) * 100
        out["24m"] = (tasa ** 12 - 1) * 100   # "Próx. 24 meses" del REM = interanual en t+24 (con el supuesto: igual al 12m)
        return out

    filas = []
    for o, q in O.groupby("origen"):
        for mod in ("A", "I"):
            pred = dict(zip(q["H"], q[mod])) if mod in q else {}
            r = armar(o, pred)
            if not r:
                continue
            for k, (y_or, v) in enumerate(r.items()):
                etiqueta = "24m" if y_or == "24m" else ("año en curso", "año siguiente", "año +2")[k]
                rem_v = rem24.get(o, np.nan) if y_or == "24m" else rem_anio.get((o, y_or), np.nan)
                filas.append({"origen": o, "modelo": mod, "periodo": etiqueta, "anio": y_or, "nuestro": v, "rem": rem_v,
                              "real": real_anio.get(y_or, np.nan) if y_or != "24m" else np.nan})
    G = pd.DataFrame(filas)
    if G.empty:
        return L + ["    Sin datos suficientes."]
    for per, mask in (("2019-2024", G["origen"] <= CORTE_SELECCION), ("2025-26", G["origen"] > CORTE_SELECCION)):
        q = G[mask]
        L.append(f"    {per}: distancia al REM (pp) | error contra la inflación real del año (pp; REM entre paréntesis)")
        for etq in ("año en curso", "año siguiente", "año +2", "24m"):
            partes = []
            for mod in ("A", "I"):
                x = q[(q["periodo"] == etq) & (q["modelo"] == mod)].dropna(subset=["rem"])
                if not len(x):
                    continue
                xr = x.dropna(subset=["real"])
                txt = f"{mod}: {(x.nuestro - x.rem).abs().mean():6.2f} (n={len(x)})"
                if len(xr):
                    txt += f" | {(xr.nuestro - xr.real).abs().mean():6.2f} ({(xr.rem - xr.real).abs().mean():.2f}; n={len(xr)})"
                partes.append(txt)
            if partes:
                L.append(f"      {etq:14} " + "   ".join(partes))
    hoy = G[G["origen"] == G["origen"].max()]
    if len(hoy):
        o = hoy["origen"].iloc[0]
        L.append(f"    Hoy (relevamiento {o}):")
        for _, r in hoy[hoy["modelo"] == "I"].iterrows():
            a = hoy[(hoy["modelo"] == "A") & (hoy["periodo"] == r["periodo"])]["nuestro"]
            L.append(f"      {str(r['anio']):5} ({r['periodo']}): I {r['nuestro']:6.1f}% | A {a.iloc[0] if len(a) else np.nan:6.1f}% | "
                     f"REM anterior {G[(G['origen'] == o - 1) & (G['modelo'] == 'I') & (G['anio'] == r['anio'])]['rem'].mean():6.1f}%")
    return L


def termometro(O, t):
    """Sección 9 (desde el 15-oct-2026, en observación): termómetro del súper. Variación de la canasta SEPA del mes en
    curso (días 1 a 25, o hasta hoy) contra el mes anterior completo, y A del mes en curso (m0) corregida con esa señal.
    La corrección (A + a + b x termómetro) se estima con todos los meses en que hay termómetro y REM publicado. Hacia
    el REM y hacia la inflación del INDEC por separado. NO cambia el pronóstico oficial de A: solo se registra para
    evaluarlo hacia adelante (datos\\registro_termometro.csv)."""
    L = ["", f"9) TERMÓMETRO DEL SÚPER (en observación): precios de supermercados SEPA del mes en curso ({t})"]
    try:
        M = pd.read_parquet(D.CARPETA / "sepa_indice_mensual.parquet")
    except Exception as e:
        return L + [f"   Sin índice SEPA ({type(e).__name__})."]
    M = M[M["region"] == "Nacional"].copy()
    M.index = pd.PeriodIndex(M["mes"], freq="M")
    s_t = M["variacion_25_pct"].get(t, np.nan)
    o = O[O.H == "m0"].copy()
    o.index = pd.PeriodIndex(o["origen"], freq="M")
    X = o[["A", "rem", "real"]].join(M["variacion_25_pct"]).dropna(subset=["variacion_25_pct", "rem", "A"])
    if np.isnan(s_t) or len(X) < 10:
        return L + [f"   Todavía no hay dato del mes en curso o hay pocos meses para estimar ({len(X)})."]
    a_t = np.nan
    if REGISTRO.exists():
        rg = pd.read_csv(REGISTRO, encoding="utf-8-sig")
        rg = rg[(rg["relevamiento"] == str(t)) & (rg["H"] == "m0")]
        a_t = rg["A"].iloc[-1] if len(rg) else np.nan
    res = {}
    for obj, col in (("REM", "rem"), ("INDEC", "real")):
        q = X.dropna(subset=[col])
        b = sm.OLS(q[col] - q["A"], sm.add_constant(q[["variacion_25_pct"]])).fit()
        res[obj] = (a_t + b.params["const"] + b.params["variacion_25_pct"] * s_t, b.params["variacion_25_pct"],
                    b.tvalues["variacion_25_pct"], len(q))
    L += [f"   La canasta del súper viene subiendo {s_t:+.2f}% este mes (promedio del 1 al 25, o hasta hoy, contra el mes anterior).",
          f"   A del mes en curso, sin corregir: {a_t:.2f}%",
          f"   A corregida hacia el REM:   {res['REM'][0]:.2f}%   (b {res['REM'][1]:.2f}, t {res['REM'][2]:.1f}, {res['REM'][3]} meses)",
          f"   A corregida hacia el INDEC: {res['INDEC'][0]:.2f}%   (b {res['INDEC'][1]:.2f}, t {res['INDEC'][2]:.1f}, {res['INDEC'][3]} meses)",
          "   (No reemplaza a A: se registra para ver, mes a mes, si la corrección acierta.)"]
    fila = pd.DataFrame([{"fecha_corrida": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "relevamiento": str(t),
                          "termometro_pct": s_t, "A_m0": a_t, "A_corr_rem": res["REM"][0], "A_corr_indec": res["INDEC"][0],
                          "meses_estimacion": res["REM"][3]}])
    fila.to_csv(REGISTRO_TERMOMETRO, mode="a", header=not REGISTRO_TERMOMETRO.exists(), index=False,
                encoding="utf-8-sig", float_format="%.4f")
    # evaluación hacia adelante: último registro de cada mes ya cerrado contra el REM y el INDEC publicados
    rt = pd.read_csv(REGISTRO_TERMOMETRO, encoding="utf-8-sig")
    rt = rt[pd.to_datetime(rt["fecha_corrida"]).dt.to_period("M").astype(str) == rt["relevamiento"]]
    rt = rt.sort_values("fecha_corrida").groupby("relevamiento").last()
    rt.index = pd.PeriodIndex(rt.index, freq="M")
    ev = rt.join(o[["rem", "real"]]).dropna(subset=["rem"])
    if len(ev):
        L.append(f"   Prueba hacia adelante ({len(ev)} meses): |A-REM| {(ev.A_m0 - ev.rem).abs().mean():.2f} -> corregida "
                 f"{(ev.A_corr_rem - ev.rem).abs().mean():.2f}")
    else:
        L.append("   Prueba hacia adelante: todavía no hay meses cerrados registrados.")
    return L


def main():
    # ---------- datos ----------
    pr = D.precios()
    ipc = pd.read_parquet(D.CARPETA / "ipc_indec.parquet")
    ipc.index = pd.to_datetime(ipc["mes"]).dt.to_period("M")
    pi = ipc["ipc_general_indice_var_mensual"].dropna().combine_first(pr["oficial"])     # lo que veían los analistas
    nuc = ipc["ipc_nucleo_indice_var_mensual"].dropna()
    real = ipc["ipc_general_indice_var_mensual"].dropna()
    # desvío de cada mes respecto del promedio de los 12 meses que terminan en él (estacionalidad sin la tendencia del año;
    # solo usa meses ya publicados, sin mirar hacia adelante)
    desvio12 = pi - pi.rolling(12, min_periods=12).mean()
    lp = np.log(D.indice(pi))
    lnuc = np.log(D.indice(nuc))
    diaria = pd.read_parquet(D.CARPETA / "bcra_series.parquet")
    diaria["fecha"] = pd.to_datetime(diaria["fecha"])
    tc_d = diaria[diaria["serie"] == "tc_mayorista_a3500"].set_index("fecha")["valor"].sort_index()
    tc_m = tc_d.groupby(tc_d.index.to_period("M")).mean()
    tc_25 = tc_d[tc_d.index.day <= DIA_CORTE_TC]
    tc_25 = tc_25.groupby(tc_25.index.to_period("M")).mean()
    dev_m = np.log(tc_m).diff() * 100
    dev_par = (np.log(tc_25) - np.log(tc_m.shift(1))) * 100
    base_m = D.dinero()["base"]
    emi = pd.read_csv(D.CARPETA / "emision_mensual.csv", index_col=0)
    emi.index = pd.PeriodIndex(emi.index, freq="M")
    emiT12 = emi["tesoro_pct"].rolling(12).sum()
    lsal = np.log(D.salarios()["total_registrado"].dropna())
    dol = pd.read_parquet(D.CARPETA / "dolares.parquet")
    ccl = dol[(dol["serie"] == "ccl") & ~dol["congelado"]].assign(fecha=lambda x: pd.to_datetime(x["fecha"])).set_index("fecha")["venta"]
    br = pd.concat([ccl.rename("ccl"), tc_d.rename("a3500")], axis=1, join="inner")
    br = (br["ccl"] / br["a3500"] - 1) * 100
    brecha_m = br.groupby(br.index.to_period("M")).mean()
    br25 = br[br.index.day <= DIA_CORTE_TC]
    brecha25 = br25.groupby(br25.index.to_period("M")).mean()
    # regla de huecos: promedio con pocos días válidos -> vacío (la fila sale de la estimación; no se rellena)
    brecha25[br25.groupby(br25.index.to_period("M")).size() < MIN_DIAS_25] = np.nan
    brecha_m[br.groupby(br.index.to_period("M")).size() < MIN_DIAS_MES] = np.nan
    meses_sin_brecha = sorted(str(m) for m in brecha25[brecha25.isna()].index)
    cat = D.ipc_categorias()                      # INDEC directo (el CSV de datos.gob.ar está cortado en 2020)
    reg = (cat["regulados"] / cat["regulados"].shift(1) - 1) * 100
    est = (cat["estacional"] / cat["estacional"].shift(1) - 1) * 100
    lreg = np.log(cat["regulados"])
    hist_s = pd.read_parquet(D.CARPETA / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    ei = {}
    for k in ("mediana", "promedio"):
        s = hist_s[hist_s["id"] == f"utdt_ei_{k}"]
        ei[k] = pd.Series(s["valor"].values, index=pd.to_datetime(s["fecha"]).dt.to_period("M")).sort_index()
    ei_m = {k: ((1 + v / 100) ** (1 / 12) - 1) * 100 for k, v in ei.items()}   # equivalente mensual
    for k, sid in [("icc", "nacional"), ("icc_pers", "situacion_personal"), ("icc_macro", "situacion_macro"), ("icc_dur", "bienes_durables")]:
        s = hist_s[hist_s["id"] == f"utdt_icc_{sid}"]
        ei[k] = pd.Series(s["valor"].values, index=pd.to_datetime(s["fecha"]).dt.to_period("M")).sort_index()

    # futuros de dólar (CEM Matba-Rofex/A3, desde ene-2020): curva del último día hábil hasta el 25 de cada mes
    fu = pd.read_parquet(D.CARPETA / "dolar_futuro_cem.parquet")
    fu = fu[fu["symbol"].str.fullmatch(r"DLR\d{6}") & fu["settlement"].gt(0)].copy()
    fu["fecha"] = pd.to_datetime(fu["fecha"])
    fu = fu[fu["fecha"].dt.day <= DIA_CORTE_TC]
    fu["mes"] = fu["fecha"].dt.to_period("M")
    fu = fu[fu["fecha"] == fu.groupby("mes")["fecha"].transform("max")]
    fu["venc"] = pd.PeriodIndex(pd.to_datetime(fu["symbol"].str[3:], format="%m%Y"), freq="M")
    F = {(m, v): s for m, v, s in zip(fu["mes"], fu["venc"], fu["settlement"])}
    foto = fu.groupby("mes")["fecha"].max()
    spot_foto = {m: tc_d.asof(f) for m, f in foto.items()}   # A3500 del día de la foto (o el último anterior publicado)

    def fut_mes(t, j):
        """Devaluación esperada para el mes j según la curva de t (en %, log): futuro de j contra futuro de j-1
        (o contra el mayorista del día de la foto, si j = t)."""
        a = F.get((t, j), np.nan)
        b = spot_foto.get(t, np.nan) if j == t else F.get((t, j - 1), np.nan)
        return np.log(a / b) * 100 if a and b and a > 0 and b > 0 else np.nan

    def fut12(t):
        """Devaluación esperada a un año: el contrato a 12 meses casi nunca existe (2% de los meses); se usa el de 9
        meses (99%) llevado a 12."""
        a, b = F.get((t, t + 9), np.nan), spot_foto.get(t, np.nan)
        return np.log(a / b) * 100 * 12 / 9 if a and b and a > 0 and b > 0 else np.nan

    def fut_prom(t, j):
        """Devaluación esperada mensual promedio desde la foto de t hasta fin del mes j (menos ruidosa que la de un mes)."""
        a, b = F.get((t, j), np.nan), spot_foto.get(t, np.nan)
        return np.log(a / b) * 100 / ((j - t).n + 1) if a and b and a > 0 and b > 0 else np.nan

    TOPE_FUT = 15.0   # % mensual: nov-2023 esperaba 80% para diciembre; sin tope, ese mes domina la estimación

    def serie_m(sid):
        s = hist_s[hist_s["id"] == sid]
        return pd.Series(s["valor"].values, index=pd.to_datetime(s["fecha"]).dt.to_period("M")).groupby(level=0).last().sort_index()
    # hacienda MAG Cañuelas (novillitos + vaquillonas): variación del mes hasta el día 25 contra el mes anterior completo
    try:
        mg = pd.read_parquet(D.CARPETA / "mag_precios.parquet")
        mg = mg[mg["categoria"].isin(["NOVILLITOS", "VAQUILLONAS"])]
        mg["pk"] = mg["promedio"] * mg["kgs"]
        p_mag = mg.groupby(["tramo", "mes"])[["pk", "kgs"]].sum()
        p_mag = (p_mag["pk"] / p_mag["kgs"]).unstack(0)
        p_mag.index = pd.PeriodIndex(p_mag.index, freq="M")
        mag = pd.DataFrame({"consumo": np.log(p_mag["mes"]).diff() * 100,
                            "consumo_25": (np.log(p_mag["hasta25"]) - np.log(p_mag["mes"].shift(1))) * 100})
    except FileNotFoundError:
        mag = pd.DataFrame(columns=["consumo", "consumo_25"])
    # dólar esperado por A-dólar (replicar_tc.py corre antes): por relevamiento y horizonte; el relevamiento en curso
    # sale del registro de hoy (datos\registro_tc.csv)
    tc_a, tc_sp = pd.DataFrame(), pd.Series(dtype=float)
    try:
        TCr = pd.read_csv(D.CARPETA / "replica_tc.csv")
        TCr["origen"] = pd.PeriodIndex(TCr["origen"], freq="M")
        tc_a = TCr.pivot_table(index="origen", columns="h", values="A_dolar")
        tc_sp = TCr.groupby("origen")["spot"].first()
        rtc = pd.read_csv(D.CARPETA / "registro_tc.csv", encoding="utf-8-sig")
        t_hoy = pd.Period(pd.Timestamp.today(), "M")
        rtc = rtc[rtc["relevamiento"] == str(t_hoy)].groupby("h")["A_dolar"].last()
        if len(rtc) and t_hoy not in tc_a.index:
            tc_a.loc[t_hoy] = rtc.reindex(tc_a.columns)
            tc_sp.loc[t_hoy] = tc_d.iloc[-1]
    except (FileNotFoundError, KeyError):
        pass

    def dev_esp(o, h):
        """Devaluación esperada (log x 100) del mes o+h contra el mes anterior según A-dólar (mes o: spot del relevamiento)."""
        if o not in tc_a.index or h < 1:
            return np.nan
        a = tc_a.loc[o].get(h, np.nan)
        b = tc_sp.get(o, np.nan) if h == 1 else tc_a.loc[o].get(h - 1, np.nan)
        return float(np.log(a / b) * 100) if (a and b and a > 0 and b > 0) else np.nan
    lipim = np.log(serie_m("indec_ipim_ng_nivel_general"))
    lripte = np.log(serie_m("datosgob_158.1_REPTE_0_0_5"))
    primario12 = serie_m("datosgob_452.3_RESULTADO_RIO_0_M_18_54").rolling(12).sum()   # 12 meses completos o vacío
    fiscal12 = primario12 / base_m * 100           # superávit primario 12 meses en % de la base monetaria (proxy: no hay PIB mensual)
    lalim = (np.log(serie_m("bm_indice_grains")) + np.log(serie_m("bm_indice_oils_meals"))) / 2
    lenerg = np.log(serie_m("bm_indice_energy"))
    itcrm_d = hist_s[hist_s["id"] == "bcra_xls_itcrm_diario"].assign(fecha=lambda x: pd.to_datetime(x["fecha"]))
    itcrm_d = itcrm_d[itcrm_d["fecha"].dt.day <= DIA_CORTE_TC]
    itcrm25 = itcrm_d.groupby(itcrm_d["fecha"].dt.to_period("M"))["valor"].mean()
    meses_c = pd.period_range("2016-01", pd.Period(pd.Timestamp.today(), "M"), freq="M")
    c25 = combustible_cortes([pd.Timestamp(m.year, m.month, DIA_CORTE_TC, 23, 59) for m in meses_c])
    cfin = combustible_cortes([m.to_timestamp(how="end").floor("s") for m in meses_c])
    lc25 = pd.Series(np.log(c25.values), index=pd.DatetimeIndex(c25.index).to_period("M")) if len(c25) else pd.Series(dtype=float)
    lcfin = pd.Series(np.log(cfin.values), index=pd.DatetimeIndex(cfin.index).to_period("M")) if len(cfin) else pd.Series(dtype=float)
    lcfin = lcfin[lcfin.index < pd.Period(pd.Timestamp.today(), "M")]          # el mes en curso no está cerrado

    rem_all = pd.read_parquet(D.CARPETA / "rem_normalizada.parquet")
    rem_all = rem_all.assign(origen=pd.to_datetime(rem_all["mes_relevamiento"]).dt.to_period("M"))
    g = rem_all[(rem_all["variable_std"] == "ipc_general")]
    rem_m = g[(g["tipo_periodo"] == "fecha") & (g["unidad"] == "var. % mensual")].groupby(["origen", "periodo_fin"])["mediana"].mean()
    rem_m.index = pd.MultiIndex.from_arrays([rem_m.index.get_level_values(0), pd.to_datetime(rem_m.index.get_level_values(1)).to_period("M")])
    rem12 = g[(g["tipo_periodo"] == "proximos_meses") & (g["horizonte_meses"] == 12)].groupby("origen")["mediana"].mean()
    origenes = sorted(set(rem_m.index.get_level_values(0)))
    gn = rem_all[(rem_all["variable_std"] == "ipc_nucleo") & (rem_all["tipo_periodo"] == "fecha") & (rem_all["unidad"] == "var. % mensual")]
    rem_nuc = gn.groupby(["origen", "periodo_fin"])["mediana"].mean()
    rem_nuc.index = pd.MultiIndex.from_arrays([rem_nuc.index.get_level_values(0), pd.to_datetime(rem_nuc.index.get_level_values(1)).to_period("M")])
    # rango de las consultoras (percentiles) para evaluar si la réplica "es una consultora más"
    pct = g[(g["tipo_periodo"] == "fecha") & (g["unidad"] == "var. % mensual")].groupby(["origen", "periodo_fin"])[
        ["percentil_10", "percentil_25", "percentil_75", "percentil_90"]].mean()
    pct.index = pd.MultiIndex.from_arrays([pct.index.get_level_values(0), pd.to_datetime(pct.index.get_level_values(1)).to_period("M")])

    R = lambda t, m: rem_m.get((t, m), np.nan)

    def sorpresa_nuc(t):
        esperado = rem_nuc.get((t - 1, t - 1), np.nan)
        if np.isnan(esperado):
            esperado = rem_nuc.get((t - 2, t - 1), np.nan)
        return nuc.get(t - 1, np.nan) - esperado

    def sorpresa(t):
        """IPC de t-1 (publicado a mitad de t) menos lo que esperaba el REM: su nowcast a fin de t-1, o si no hay, su h1 de t-2."""
        esperado = R(t - 1, t - 1)
        if np.isnan(esperado):
            esperado = R(t - 2, t - 1)
        return pi.get(t - 1, np.nan) - esperado

    def rasgos(t, h):
        d = lambda a, b, s=lp: (s.get(a, np.nan) - s.get(b, np.nan)) * 100
        return {"p1": pi.get(t - 1), "p2": pi.get(t - 2), "p3": pi.get(t - 3), "p12": pi.get(t + h - 12),
                "nuc1": nuc.get(t - 1), "dev0": dev_par.get(t), "dev1": dev_m.get(t - 1), "emiT12": emiT12.get(t - 1),
                "sorpresa": sorpresa(t), "inf12": d(t - 1, t - 13), "inf3": d(t - 1, t - 4) * 4,
                "nuc3": d(t - 1, t - 4, lnuc) * 4, "dev3": (np.log(tc_25.get(t, np.nan)) - np.log(tc_m.get(t - 3, np.nan))) * 100,
                "base12": (np.log(base_m.get(t - 1, np.nan)) - np.log(base_m.get(t - 13, np.nan))) * 100,
                "sal1": d(t - 2, t - 3, lsal), "sal3": d(t - 2, t - 5, lsal) / 3, "sal12": d(t - 2, t - 14, lsal),
                "brecha0": brecha25.get(t, np.nan), "dbrecha": brecha25.get(t, np.nan) - brecha_m.get(t - 1, np.nan),
                "reg1": reg.get(t - 1, np.nan), "reg3": np.mean([reg.get(t - k, np.nan) for k in (1, 2, 3)]),
                "est1": est.get(t - 1, np.nan), "reg12": d(t - 1, t - 13, lreg), "sorpresa_nuc": sorpresa_nuc(t),
                "ei_med": ei["mediana"].get(t, np.nan), "ei_prom": ei["promedio"].get(t, np.nan),
                "ei_med_m": ei_m["mediana"].get(t, np.nan), "ei_prom_m": ei_m["promedio"].get(t, np.nan),
                "dei_med": ei["mediana"].get(t, np.nan) - ei["mediana"].get(t - 1, np.nan),
                "dei_prom": ei["promedio"].get(t, np.nan) - ei["promedio"].get(t - 1, np.nan),
                "dei_med_m": ei_m["mediana"].get(t, np.nan) - ei_m["mediana"].get(t - 1, np.nan),
                "dei_prom_m": ei_m["promedio"].get(t, np.nan) - ei_m["promedio"].get(t - 1, np.nan),
                **{k: ei[k].get(t, np.nan) for k in ["icc"] + ICC_SUB},
                **{"d" + k: ei[k].get(t, np.nan) - ei[k].get(t - 1, np.nan) for k in ["icc"] + ICC_SUB},
                "ipim1": d(t - 1, t - 2, lipim), "ipim3": d(t - 1, t - 4, lipim) / 3, "ipim12": d(t - 1, t - 13, lipim),
                "dcomb0": (lc25.get(t, np.nan) - lcfin.get(t - 1, np.nan)) * 100,
                "dcomb1": d(t - 1, t - 2, lcfin), "dcomb12": d(t - 1, t - 13, lcfin),
                "fiscal12": fiscal12.get(t - 2, np.nan), "ripte1": d(t - 2, t - 3, lripte), "ripte3": d(t - 2, t - 5, lripte) / 3,
                "ripte12": d(t - 2, t - 14, lripte), "itcrm0": itcrm25.get(t, np.nan),
                "alim12": d(t - 1, t - 13, lalim), "energ12": d(t - 1, t - 13, lenerg),
                "fut_m": fut_mes(t, t + h), "dfut_m": fut_mes(t, t + h) - fut_mes(t - 1, t + h),
                "fut12": fut12(t), "dfut12": fut12(t) - fut12(t - 1),
                "ei_med_l": np.log1p(ei["mediana"].get(t, np.nan) / 100) * 100,
                "ei_prom_l": np.log1p(ei["promedio"].get(t, np.nan) / 100) * 100,
                "carne25": mag["consumo_25"].get(t, np.nan), "carne1": mag["consumo"].get(t - 1, np.nan),
                "nuc_2": nuc.get(t - 2, np.nan), "nuc_3": nuc.get(t - 3, np.nan),
                "dolar_m": dev_esp(t, h), "dolar_m1": dev_par.get(t, np.nan) if h == 1 else dev_esp(t, h - 1),
                # estacionalidad del mes pronosticado (t+h): núcleo del mismo mes del año anterior, y desvío típico de ese
                # mes respecto del promedio de su año, promediado en los últimos 3 años (diluye un tarifazo de un solo año)
                "p12n": nuc.get(t + h - 12, np.nan),
                # expectativa de los hogares RELATIVA a la inflación de los últimos 12 meses (en log, mensualizada): cuánto
                # más pesimistas que la inflación actual están los hogares
                "inf12m": d(t - 1, t - 13) / 12,
                "ei_gap": np.log1p(ei["mediana"].get(t, np.nan) / 100) * 100 / 12 - d(t - 1, t - 13) / 12,
                "estac3": np.nanmean([desvio12.get(t + h - 12 * y, np.nan) for y in (1, 2, 3)])
                if any(not np.isnan(desvio12.get(t + h - 12 * y, np.nan)) for y in (1, 2, 3)) else np.nan,
                "fut_m_tope": min(fut_mes(t, t + h), TOPE_FUT), "fut_prom": min(fut_prom(t, t + h), TOPE_FUT),
                "fut12_tope": min(fut12(t), TOPE_FUT * 12)}

    # ---------- tabla de relevamientos ----------
    filas = []
    for t in origenes:
        for h in HORIZONTES:
            p = pct.loc[(t, t + h)] if (t, t + h) in pct.index else pd.Series(dtype=float)
            filas.append({"origen": t, "H": f"m{h}", "objetivo": t + h, "rem": R(t, t + h), "rem_ant": R(t - 1, t + h),
                          "real": real.get(t + h, np.nan), **rasgos(t, h),
                          **{k: p.get(k, np.nan) for k in ("percentil_10", "percentil_25", "percentil_75", "percentil_90")}})
        filas.append({"origen": t, "H": "12m", "rem": rem12.get(t, np.nan), "rem_ant": rem12.get(t - 1, np.nan),
                      "real": np.nan, **rasgos(t, 0)})
    T = pd.DataFrame(filas).dropna(subset=["rem"])
    T["rev"] = T["rem"] - T["rem_ant"]
    T["rem_l"] = np.log1p(T["rem"] / 100) * 100          # objetivo en logaritmos (12m de A)
    T["rev_l"] = T["rem_l"] - np.log1p(T["rem_ant"] / 100) * 100   # revisión en logaritmos (12m de B)
    T.to_csv(D.CARPETA / "replica_rem_variables.csv", index=False, encoding="utf-8-sig", float_format="%.5f")   # para diagnósticos

    # ---------- réplica en tiempo real ----------
    out = []
    for H, th in T.groupby("H"):
        th = th.sort_values("origen")
        for _, fila in th.iterrows():
            t = fila["origen"]
            hist = th[th["origen"] < t]
            res = {"origen": t, "H": H, "rem": fila["rem"], "rem_ant": fila["rem_ant"], "real": fila["real"],
                   **{k: fila.get(k, np.nan) for k in ("percentil_10", "percentil_25", "percentil_75", "percentil_90")}}
            for nom, (tipo, cm, c12) in VARIANTES.items():
                cols = c12 if H == "12m" else cm
                y = y_de(tipo, H, cols)
                tb = hist.dropna(subset=cols + [y])
                necesarios = cols + ([] if tipo == "nivel" else ["rem_ant"])
                if len(tb) < MIN_FILAS or fila[necesarios].isna().any():
                    continue
                pred = estimar(nom, tipo, H, tb[cols].values.astype(float), tb[y].values.astype(float), fila[cols].values.astype(float))
                res[nom] = deshacer(tipo, H, cols, pred, fila["rem_ant"])
            res["I"] = elegir_I(res, fila)
            res["A sin dólar"] = res.get("A", np.nan)
            res["A"] = elegir_A(res, fila)
            for nom, (base_nom, semivida, lam) in METODOS.items():
                tipo, cm, c12 = VARIANTES[base_nom]
                cols = c12 if H == "12m" else cm
                log = EN_LOG(tipo, H, cols)
                y = "rem_l" if log else "rem"
                tb = hist.dropna(subset=cols + [y])
                if len(tb) < MIN_FILAS or fila[cols].isna().any():
                    continue
                edades = [(t - o).n for o in tb["origen"]]
                pred = pred_metodo(tb[cols].values.astype(float), tb[y].values.astype(float),
                                   fila[cols].values.astype(float), edades, semivida, lam)
                res[nom] = np.expm1(pred / 100) * 100 if log else pred
            out.append(res)
    O = pd.DataFrame(out)
    O.to_csv(SALIDA, index=False, encoding="utf-8-sig", float_format="%.4f")

    # ---------- informe ----------
    L = ["=== RÉPLICA DEL REM (objetivo: la mediana del REM; meta: aproximarse, no ganarle) ===",
         "Distancia = |réplica - REM| en pp. 'REM anterior' = el REM repite lo que dijo el mes pasado para ese mes.",
         "A = solo datos públicos. B = REM anterior + revisión explicada por la sorpresa del último IPC y la devaluación.",
         "+salarios: índice de salarios INDEC (total registrado), último dato publicado = t-2.",
         f"Huecos: meses sin brecha por tener menos de {MIN_DIAS_25} días válidos hasta el 25 (salen de la estimación): "
         f"{', '.join(meses_sin_brecha) if meses_sin_brecha else 'ninguno'}.", ""]
    orden_H = [f"m{h}" for h in HORIZONTES] + ["12m"]
    columnas = [("REM anterior", "rem_ant")] + [(v, v) for v in VARIANTES]
    comun = O.dropna(subset=[v for v in VARIANTES if v not in SOLO_DESDE_2020] + ["rem_ant"])
    dist = lambda q, col: (q[col] - q["rem"]).abs().mean()
    L.append(f"1) Distancia media al REM, muestra común (relevamientos {comun['origen'].min()} a {comun['origen'].max()})")
    L.append(f"   {'':14} " + " ".join(f"{h:>7}" for h in orden_H))
    for nom, col in columnas:
        L.append(f"   {nom:14} " + " ".join(f"{dist(comun[comun.H == h], col):7.2f}" if (comun.H == h).any() else f"{'-':>7}" for h in orden_H))
    L.append("   (m0 = mes del relevamiento, m1 = el siguiente, ...; 12m = próximos 12 meses en %)")
    L.append("")
    L.append("1b) ¿Es 'una consultora más'? % de meses dentro del 80% central de las consultoras (P10-P90) | dentro del 50% central (P25-P75)")
    L.append("    Una consultora típica: ~80% | ~50%.")
    L.append(f"   {'':16} " + " ".join(f"{h:>13}" for h in orden_H[:-1]))
    for nom, col in columnas:
        celdas = []
        for h in orden_H[:-1]:
            q = comun[(comun.H == h)].dropna(subset=["percentil_10", "percentil_90"])
            if q.empty:
                celdas.append(f"{'-':>13}")
                continue
            en80 = ((q[col] >= q["percentil_10"]) & (q[col] <= q["percentil_90"])).mean() * 100
            en50 = ((q[col] >= q["percentil_25"]) & (q[col] <= q["percentil_75"])).mean() * 100
            celdas.append(f"{en80:5.0f}% |{en50:4.0f}%".rjust(13))
        L.append(f"   {nom:16} " + " ".join(celdas))
    L.append("")
    L.append("2) Distancia por año del relevamiento")
    comun = comun.assign(anio=comun["origen"].dt.year)
    for H in ("m1", "m3", "12m"):
        q = comun[comun.H == H]
        L.append(f"   {H}:")
        for nom, col in columnas:
            L.append(f"     {nom:14} " + " ".join(f"{a}: {dist(g, col):5.2f}" for a, g in q.groupby("anio")))
    L.append("")
    L.append("3) Error contra lo que pasó (IPC INDEC), mensual: ¿la réplica se equivoca parecido al REM?")
    L.append(f"   {'':14} " + " ".join(f"{h:>7}" for h in orden_H[:-1]))
    cr = comun.dropna(subset=["real"])
    for nom, col in [("REM", "rem")] + columnas:
        L.append(f"   {nom:14} " + " ".join(f"{(cr[cr.H == h][col] - cr[cr.H == h]['real']).abs().mean():7.2f}" if (cr.H == h).any() else f"{'-':>7}" for h in orden_H[:-1]))
    L.append("")

    # ---------- qué mira el REM (muestra completa, errores HAC) ----------
    L.append("4) ¿A qué reacciona el REM? Regresiones con toda la muestra (coeficiente y t HAC)")
    for H in ("m0", "m1", "m3", "m6", "12m"):
        q = T[T.H == H]
        for nom, (tipo, cm, c12) in VARIANTES.items():
            cols = c12 if H == "12m" else cm
            y = y_de(tipo, H, cols)
            d = q.dropna(subset=cols + [y])
            if len(d) < 30:
                continue
            m = sm.OLS(d[y].astype(float), sm.add_constant(d[cols].astype(float))).fit(cov_type="HAC", cov_kwds={"maxlags": 6})
            L.append(f"   {H:4} {nom:11} n={len(d):3} R²={m.rsquared:4.2f} | " +
                     "  ".join(f"{c} {m.params[c]:+.3f} ({m.tvalues[c]:+.1f})" for c in cols))

    # ---------- 4b) prueba de las expectativas de inflación de los hogares (UTDT) ----------
    L += ["", "4b) ENCUESTAS DE LA UTDT (expectativas de inflación EI y confianza del consumidor ICC): ¿agregan algo a A y a B?",
          "    Dentro de muestra: coeficiente de cada variable agregada y su t HAC (|t| > 2 ≈ significativo al 5%).",
          "    Fuera de muestra (tiempo real): distancia al REM sin y con la variable; p = Diebold-Mariano con corrección HLN",
          f"    (igual distancia). Son {len(PRUEBAS)} variantes x 8 horizontes: con tantas pruebas, alguna da p < 0,05 por azar (exigir p < 0,01).",
          "    Unidades: EI en A = nivel (equivalente mensual en m0-m6; % anual en 12m); ICC = índice 0-100; en B, cambio contra el mes anterior."]
    for nom, (base_nom, cols_m, cols_12) in PRUEBAS.items():
        L.append(f"   {nom} (contra {base_nom}):")
        for H in orden_H:
            h = 12 if H == "12m" else int(H[1:])
            tipo, cm, c12 = VARIANTES[nom]
            cols, nuevas = (c12 if H == "12m" else cm), (cols_12 if H == "12m" else cols_m)
            y = y_de(tipo, H, cols)
            q = T[T.H == H].dropna(subset=cols + [y])
            if len(q) < 30:
                continue
            m = sm.OLS(q[y].astype(float), sm.add_constant(q[cols].astype(float))).fit(cov_type="HAC", cov_kwds={"maxlags": 6})
            o = O[O.H == H].dropna(subset=[nom, base_nom, "rem"]).sort_values("origen")
            e_base, e_new = (o[base_nom] - o["rem"]).values, (o[nom] - o["rem"]).values
            p = diebold_mariano(e_new, e_base, max(h, 1))
            d0, d1 = np.abs(e_base).mean(), np.abs(e_new).mean()
            coefs = "  ".join(f"{c} {m.params[c]:+.3f} (t {m.tvalues[c]:+.1f})" for c in nuevas)
            L.append(f"     {H:4} n={len(q):3} {coefs} | R² {m.rsquared:.2f} | "
                     f"distancia al REM {d0:.3f} -> {d1:.3f} ({(d1 / d0 - 1) * 100:+.1f}%), p={p:.3f} (n={len(o)})")

    # ---------- 6) método de estimación de A (selección hasta CORTE_SELECCION; prueba 2025-26 reservada) ----------
    ver_prueba = True   # desde el 16-oct-2026 (Marcos): 2025-26 se usa para modelar; la prueba limpia es hacia adelante (sección 7)
    met = list(METODOS)
    ei_o = T[T.H == "m1"].set_index("origen")["ei_med_m"]
    piv = O[O.H.isin([f"m{h}" for h in range(1, 7)])].pivot_table(index="origen", columns="H", values=met)
    r12 = O[O.H == "12m"].set_index("origen")
    cad = pd.DataFrame(index=r12.index)
    cad["rem"] = r12["rem"]
    for m_ in met:   # "próx. 12 meses" = meses t+1..t+12: m1..m6 pronosticados; 7-12 = m6 repetido o la expectativa de hogares
        if m_ not in piv.columns.get_level_values(0):
            continue
        sub = piv[m_].reindex(columns=[f"m{h}" for h in range(1, 7)])
        prod6 = (1 + sub / 100).prod(axis=1, min_count=6)
        cad[m_ + " | cadena m6"] = (prod6 * (1 + sub["m6"] / 100) ** 6 - 1) * 100
        cad[m_ + " | cadena EI"] = (prod6 * (1 + ei_o.reindex(sub.index) / 100) ** 6 - 1) * 100
        cad[m_ + " | directo"] = r12.get(m_)

    # glide path (Faust y Wright 2013): meses 1-6 = pronóstico mensual del modelo A (MCO); meses 7-12 planean desde el m6
    # hacia un punto de llegada (ancla), en línea recta hasta el mes 12 o acercándose a la mitad cada `semivida` meses
    inf12_o = T[T.H == "m1"].set_index("origen")["inf12"]                  # log-dif x100 de t-13 a t-1
    anclas = {"EI": ei_o, "infl. 12m": ((np.exp(inf12_o / 100)) ** (1 / 12) - 1) * 100}
    anclas["promedio"] = (anclas["EI"] + anclas["infl. 12m"]) / 2
    glide = pd.DataFrame(index=cad.index)
    glide["rem"] = cad["rem"]
    sub_a = piv["A (MCO)"].reindex(columns=[f"m{h}" for h in range(1, 7)]).reindex(cad.index)
    for an, serie in anclas.items():
        a = serie.reindex(cad.index)
        for vel, nombre in [(None, "recta al mes 12"), (3, "mitad cada 3 meses"), (6, "mitad cada 6 meses")]:
            meses = [sub_a[f"m{h}"] for h in range(1, 7)]
            for j in range(7, 13):
                k = j - 6
                peso = (k / 6) if vel is None else (1 - 0.5 ** (k / vel))      # cuánto ya se acercó al ancla
                meses.append(sub_a["m6"] + (a - sub_a["m6"]) * peso)
            prod = pd.concat([(1 + m / 100) for m in meses], axis=1).prod(axis=1, min_count=12)
            glide[f"ancla {an}, {nombre}"] = (prod - 1) * 100
    glide["12m directo (A MCO)"] = cad.get("A (MCO) | directo")

    def tabla_glide(titulo, mascara):
        g_ = glide[mascara].dropna()
        L.append(f"   {titulo} (n={len(g_)}): distancia media al REM a 12 meses, pp")
        for c in [c for c in glide.columns if c != "rem"]:
            L.append(f"     {c:42} {(g_[c] - g_['rem']).abs().mean():7.2f}")

    def bloque(titulo, mascara_o, mascara_c):
        q = O[mascara_o]
        L.append(f"   {titulo}")
        L.append(f"   {'':18} " + " ".join(f"{h:>6}" for h in orden_H[:-1]) + f" | {'12m dir':>8} {'12m cad6':>8} {'12m cadEI':>9} |"
                 f" {'en P10-P90: m1':>15} {'m3':>5} {'m5':>5}")
        filas_l = []
        for m_ in met + ["B"]:
            celdas = []
            for h in orden_H[:-1]:
                qq = q[q.H == h].dropna(subset=[c for c in met if c in q.columns] + ["rem"])
                celdas.append(f"{(qq[m_] - qq['rem']).abs().mean():6.2f}" if len(qq) and m_ in qq else f"{'-':>6}")
            c = cad[mascara_c].dropna()
            if m_ in METODOS:
                d12 = [f"{(c[m_ + s] - c['rem']).abs().mean():8.2f}" if m_ + s in c else f"{'-':>8}" for s in (" | directo", " | cadena m6")]
                d12.append(f"{(c[m_ + ' | cadena EI'] - c['rem']).abs().mean():9.2f}" if m_ + " | cadena EI" in c else f"{'-':>9}")
            else:
                cb = O[mascara_o & (O.H == "12m")].dropna(subset=["B"])
                d12 = [f"{(cb['B'] - cb['rem']).abs().mean():8.2f}", f"{'':>8}", f"{'':>9}"]
            en = []
            for h in ("m1", "m3", "m5"):
                qq = q[q.H == h].dropna(subset=[c_ for c_ in met if c_ in q.columns] + ["percentil_10", "percentil_90"])
                en.append(f"{((qq[m_] >= qq['percentil_10']) & (qq[m_] <= qq['percentil_90'])).mean() * 100:4.0f}%" if len(qq) else "  - ")
            filas_l.append(f"   {m_:18} " + " ".join(celdas) + " | " + " ".join(d12) + f" | {en[0]:>15} {en[1]:>5} {en[2]:>5}")
        L.extend(filas_l)
        L.append(f"   (relevamientos: {q['origen'].min()} a {q['origen'].max()}; B como referencia)")

    L += ["", f"6) MÉTODO DE ESTIMACIÓN DE A (mismas variables: A con expectativa de hogares)",
          "   MCO = regresión común (igual que 'A'). pondN = pesos que se reducen a la mitad cada N meses (los meses recientes pesan más).",
          "   ridgeL = penalización que achica coeficientes inestables (variables estandarizadas). Distancia al REM en pp.",
          "   12m dir = regresión directa del REM a 12 meses; cad6 = encadenando m1..m6 y repitiendo m6 para los meses 7-12;",
          "   cadEI = encadenando m1..m6 y usando la expectativa de los hogares para los meses 7-12.",
          f"   Se muestran los dos períodos: hasta {CORTE_SELECCION} y desde {CORTE_SELECCION + 1} (régimen actual). La prueba limpia es hacia adelante (sección 7)."]
    bloque(f"Período de SELECCIÓN (hasta {CORTE_SELECCION}):", O["origen"] <= CORTE_SELECCION, cad.index <= CORTE_SELECCION)
    if ver_prueba:
        bloque(f"Régimen actual (desde {CORTE_SELECCION + 1}):", O["origen"] > CORTE_SELECCION, cad.index > CORTE_SELECCION)
    else:
        L.append(f"   Período de PRUEBA (desde {CORTE_SELECCION + 1}): reservado (correr con --ver-prueba al final).")
    L += ["", "6b) GLIDE PATH para los próximos 12 meses (modelo A): meses 1-6 = pronóstico mensual; meses 7-12 planean hacia un ancla."]
    tabla_glide(f"Período de SELECCIÓN (hasta {CORTE_SELECCION})", glide.index <= CORTE_SELECCION)
    if ver_prueba:
        tabla_glide(f"2025-2026 (régimen actual)", glide.index > CORTE_SELECCION)

    # ---------- 5) pronóstico con los datos de hoy (próximo relevamiento del REM, todavía no publicado) ----------
    t = pd.Period(pd.Timestamp.today(), "M")
    if t not in origenes:
        dias_tc = int(((tc_d.index.to_period("M") == t) & (tc_d.index.day <= DIA_CORTE_TC)).sum())
        hoy = pd.Timestamp.today().normalize()
        habiles_restantes = len(pd.bdate_range(hoy + pd.Timedelta(days=1), pd.Timestamp(t.year, t.month, DIA_CORTE_TC)))
        L += ["", f"5) PRONÓSTICO CON LOS DATOS DE HOY ({hoy.date()}): qué va a decir el REM de fin de {t} y qué inflación implica",
              f"   IPC de {t - 1} publicado: {'sí' if not np.isnan(pi.get(t - 1, np.nan)) else 'NO'} ({pi.get(t - 1, np.nan):.2f}%). "
              f"Sorpresa contra el REM: {sorpresa(t):+.2f} pp.",
              f"   Dólar de {t}: {dias_tc} días hábiles con dato hasta hoy (el modelo usa hasta el día {DIA_CORTE_TC}; faltan ~{habiles_restantes}). "
              + ("PROVISORIO: recalcular después del 25." if habiles_restantes > 0 else "Completo."),
              "   Rango del próximo REM: 8 de cada 10 veces, según los errores pasados de cada réplica contra el REM.",
              "   Rango de la inflación: 8 de cada 10 veces, según los errores pasados del REM contra lo que pasó."]
        # la expectativa de inflación de t (UTDT) sale hacia el día 20: hasta entonces se usa la de t-1 (provisorio)
        ei_t = ei["mediana"].get(t, np.nan)
        ei_prov = np.isnan(ei_t)
        L.append(f"   Expectativa de inflación de los hogares (UTDT) de {t}: " +
                 (f"{ei_t:.1f}%." if not ei_prov else
                  f"todavía NO publicada: se usa la de {t - 1} ({ei['mediana'].get(t - 1, np.nan):.1f}%). PROVISORIO para {A_PRONOSTICO}."))
        L.append("")
        registro, insumos = [], []
        L.append(f"   {'mes':9} {'REM anterior':>12} | {'B':>6} {'rango del REM (B)':>18} | {A_PRONOSTICO:>16} | {'rango de la inflación':>22}")
        for H in [f"m{h}" for h in HORIZONTES] + ["12m"]:
            h = 0 if H == "12m" else int(H[1:])
            fila = pd.Series({**rasgos(t, h), "rem_ant": (rem12.get(t - 1, np.nan) if H == "12m" else R(t - 1, t + h))})
            if ei_prov:
                fila["ei_med"] = ei["mediana"].get(t - 1, np.nan)
                fila["ei_med_l"] = np.log1p(fila["ei_med"] / 100) * 100
                fila["ei_med_m"] = ei_m["mediana"].get(t - 1, np.nan)
                fila["ei_gap"] = fila["ei_med_l"] / 12 - fila["inf12m"]
                fila["ei_prom_m"] = ei_m["promedio"].get(t - 1, np.nan)
                fila["ei_prom_l"] = np.log1p(ei["promedio"].get(t - 1, np.nan) / 100) * 100
            th = T[T.H == H]
            insumos.append({"relevamiento": str(t), "H": H, **fila.to_dict()})
            preds = {}
            for nom in ("B", A_PRONOSTICO, "A+dólar", "I sin dólar", "I+dólar"):   # A sin salarios: el dato de t-2 todavía puede no estar publicado
                tipo, cm, c12 = VARIANTES[nom]
                cols = c12 if H == "12m" else cm
                y = y_de(tipo, H, cols)
                tb = th.dropna(subset=cols + [y])
                if len(tb) < MIN_FILAS or fila[cols + ([] if tipo == "nivel" else ["rem_ant"])].isna().any():
                    preds[nom] = np.nan
                    continue
                p = estimar(nom, tipo, H, tb[cols].values.astype(float), tb[y].values.astype(float), fila[cols].values.astype(float))
                preds[nom] = deshacer(tipo, H, cols, p, fila["rem_ant"])
            # rangos con errores RELATIVOS (cociente), para que no los inflen los años de inflación alta
            hist = O[(O.H == H)].dropna(subset=["B", "rem"])
            ratio_b = hist["B"] / hist["rem"]                          # réplica / REM
            lo_rem, hi_rem = preds["B"] / ratio_b.quantile(0.9), preds["B"] / ratio_b.quantile(0.1)
            ratio_r = (th["real"] / th["rem"]).dropna()                # lo que pasó / REM
            lo_inf, hi_inf = (preds["B"] * ratio_r.quantile(0.1), preds["B"] * ratio_r.quantile(0.9)) if len(ratio_r) > 20 else (np.nan, np.nan)
            mes = "próx. 12m" if H == "12m" else str(t + h)
            rango_inf = f"{lo_inf:5.2f} a {hi_inf:5.2f}" if not np.isnan(lo_inf) else "-"
            L.append(f"   {mes:9} {fila['rem_ant']:12.2f} | {preds['B']:6.2f} {lo_rem:7.2f} a {hi_rem:6.2f} | "
                     f"{preds[A_PRONOSTICO]:16.2f} | {rango_inf:>22}")
            I_HOY[H] = elegir_I(preds, fila)
            preds[A_PRONOSTICO] = elegir_A(preds, fila)
            registro.append({"fecha_corrida": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "relevamiento": str(t), "H": H,
                             "objetivo": "12m" if H == "12m" else str(t + h), "rem_anterior": fila["rem_ant"],
                             "B": preds["B"], "B_rango_bajo": lo_rem, "B_rango_alto": hi_rem,
                             "A": preds[A_PRONOSTICO], "A_variante": A_PRONOSTICO,
                             "provisorio_dolar": habiles_restantes > 0, "provisorio_ei": bool(ei_prov),
                             "ipc_t1_publicado": not np.isnan(pi.get(t - 1, np.nan))})
        L.append("   (m0 = mes en curso; '12m' = inflación de los próximos 12 meses en %)")
        # registro del pronóstico ANTES de que salga el REM de t: es la prueba limpia hacia adelante (sección 7)
        # datos con que se armó el pronóstico de hoy (se pisa en cada corrida; el pronóstico queda en el registro)
        pd.DataFrame(insumos).to_csv(D.CARPETA / "pronostico_insumos.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
        reg = pd.DataFrame(registro)
        reg.to_csv(REGISTRO, mode="a", header=not REGISTRO.exists(), index=False, encoding="utf-8-sig", float_format="%.4f")
        L.append(f"   Pronóstico registrado en datos\\{REGISTRO.name} ({len(reg)} filas).")

    # ---------- 7) prueba hacia adelante: pronósticos registrados antes de cada REM, contra el REM publicado ----------
    L += ["", "7) PRUEBA HACIA ADELANTE (la única prueba limpia desde el 15-oct-2026): último pronóstico registrado dentro del",
          "   mes del relevamiento (antes de que se publique ese REM) contra la mediana del REM publicada."]
    if REGISTRO.exists():
        rg = pd.read_csv(REGISTRO, encoding="utf-8-sig")
        rg = rg[pd.to_datetime(rg["fecha_corrida"]).dt.to_period("M").astype(str) == rg["relevamiento"]]
        rg = rg.sort_values("fecha_corrida").groupby(["relevamiento", "H"]).last().reset_index()
        rg["rem"] = [rem12.get(pd.Period(r, "M"), np.nan) if H == "12m" else R(pd.Period(r, "M"), pd.Period(o, "M"))
                     for r, H, o in zip(rg["relevamiento"], rg["H"], rg["objetivo"])]
        ev = rg.dropna(subset=["rem"])
        if ev.empty:
            L.append(f"   Todavía no hay REM publicado para los relevamientos registrados ({', '.join(sorted(rg['relevamiento'].unique()))}).")
        else:
            L.append(f"   Relevamientos evaluados: {ev['relevamiento'].nunique()} ({ev['relevamiento'].min()} a {ev['relevamiento'].max()})")
            L.append(f"   {'':6} {'n':>3} {'|A-REM|':>8} {'|B-REM|':>8} {'|REM ant-REM|':>14}")
            for H in orden_H:
                q = ev[ev.H == H]
                if len(q):
                    L.append(f"   {H:6} {len(q):3} {(q['A'] - q['rem']).abs().mean():8.2f} {(q['B'] - q['rem']).abs().mean():8.2f} "
                             f"{(q['rem_anterior'] - q['rem']).abs().mean():14.2f}")
    else:
        L.append("   Todavía no hay pronósticos registrados.")

    # ---------- 8) alertas: ¿este mes puede fallar nuestro pronóstico del REM? ----------
    L += alertas(T, O, t, rasgos, hist_s, fu_todo=pd.read_parquet(D.CARPETA / "dolar_futuro_cem.parquet"))
    L += termometro(O, t)
    L += pronostico_inflacion(O, t, lp, rem12)
    # grilla: para el relevamiento en curso (sin REM todavía) se suman los pronósticos de hoy
    O_hoy = O
    if t not in set(O["origen"]):
        extra = [{"origen": t, "H": H, "I": I_HOY.get(H, np.nan)} for H in I_HOY]
        if REGISTRO.exists():
            rg = pd.read_csv(REGISTRO, encoding="utf-8-sig")
            rg = rg[rg["relevamiento"] == str(t)].groupby("H")["A"].last()
            for e in extra:
                e["A"] = rg.get(e["H"], np.nan)
        O_hoy = pd.concat([O, pd.DataFrame(extra)], ignore_index=True)
    L += grilla_anual(O_hoy, t, pi, rem_all)
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
