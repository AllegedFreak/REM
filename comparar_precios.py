# comparar_precios.py
# Consulta personal: compara precios de 3 sucursales (SEPA, últimos 7 días en E:\SEPA\crudos) y arma un Excel.
#   - mismo producto = mismo código de barras (EAN); los códigos internos de las cadenas (carne, verdura, fiambre
#     al corte) se agrupan por nombre con un diccionario
#   - qué es / marca / variante / tamaño se deducen de la descripción; marca y descripción se completan con
#     el mismo EAN en las demás cadenas (E:\SEPA\resumen)
#   - precio de lista y mejor promoción publicada (SEPA no trae descuentos bancarios ni de programas de fidelidad)
# Uso: py -3.13 comparar_precios.py      Salida: comparacion_precios_<último día>.xlsx en esta carpeta
import io
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding="utf-8")
CARPETA = Path(__file__).parent
CRUDOS = Path("E:/SEPA/crudos")
RESUMEN = Path("E:/SEPA/resumen")
LOCALES = {("10", "2", "116"): "Carrefour", ("12", "1", "163"): "Coto", ("15", "1", "268"): "Día"}
NOMBRES = ["Carrefour", "Coto", "Día"]
DIRECCION = {"Carrefour": "Carrefour Market, Av. Santa Fe 1954, CABA (sucursal 10-2-116)",
             "Coto": "Coto, Junín 1073, CABA (sucursal 12-1-163)",
             "Día": "Supermercados Día, Riobamba 1014, CABA (sucursal 15-1-268)"}
OPC = "delim='|', header=true, all_varchar=true, ignore_errors=true, null_padding=true"

# ---------------------------------------------------------------- texto
ABREV = {"YOG": "YOGUR", "SAB": "SABOR", "DESC": "DESCREMADO", "ENT": "ENTERO", "LIQ": "LIQUIDO", "CONC": "CONCENTRADO",
         "ACOND": "ACONDICIONADOR", "DESOD": "DESODORANTE", "SH": "SHAMPOO", "SHAMP": "SHAMPOO", "GALL": "GALLETITAS",
         "GALLET": "GALLETITAS", "BEB": "BEBIDA", "ALIM": "ALIMENTO", "JAB": "JABON", "DET": "DETERGENTE",
         "LAVAV": "LAVAVAJILLAS", "MERM": "MERMELADA", "SUAV": "SUAVIZANTE", "QSO": "QUESO", "LECH": "LECHE",
         "CHOC": "CHOCOLATE", "TINT": "TINTURA", "LIMP": "LIMPIADOR", "MAYON": "MAYONESA", "GASEOS": "GASEOSA",
         "HIG": "HIGIENICO", "PAP": "PAPEL", "ARR": "ARROZ", "FID": "FIDEOS", "CERV": "CERVEZA",
         "D": "DE", "C": "CON", "S": "SIN", "P": "PARA", "DL": "DE LA"}


def sin_acentos(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()


def norm(s):
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    s = re.sub(r"[^A-Z0-9%]+", " ", sin_acentos(s))
    return " ".join(ABREV.get(w, w) for w in s.split())


# ---------------------------------------------------------------- tamaño
UNI = {"KG": ("g", 1000), "KGS": ("g", 1000), "KGM": ("g", 1000), "KILO": ("g", 1000), "KILOS": ("g", 1000),
       "G": ("g", 1), "GR": ("g", 1), "GRS": ("g", 1), "GRM": ("g", 1), "GRAMOS": ("g", 1),
       "ML": ("ml", 1), "MLS": ("ml", 1), "CC": ("ml", 1), "CM3": ("ml", 1), "CMQ": ("ml", 1), "CL": ("ml", 10),
       "L": ("ml", 1000), "LT": ("ml", 1000), "LTS": ("ml", 1000), "LTR": ("ml", 1000), "LITRO": ("ml", 1000),
       "U": ("u", 1), "UN": ("u", 1), "UD": ("u", 1), "UNI": ("u", 1), "UNID": ("u", 1), "UNIDADES": ("u", 1),
       "M": ("m", 1), "MT": ("m", 1), "MTS": ("m", 1), "MTR": ("m", 1), "M2": ("m2", 1), "MT2": ("m2", 1)}
RX_TAM = re.compile(r"(\d+(?:[.,]\d+)?)\s*(KGS?|KGM|KILOS?|GRAMOS|GRS?|GRM|G|MLS?|CC|CM3|CMQ|CL|LTS?|LTR|LITRO|L|"
                    r"UNIDADES|UNID|UNI|UN|UD|U|MTS?|MTR|MT2|M2|M)\b")


def a_base(q, u):
    u = sin_acentos(u).strip().replace(".", "") if isinstance(u, str) else ""
    if u not in UNI or q is None or not np.isfinite(q) or q <= 0:
        return None
    b, f = UNI[u]
    return round(q * f, 3), b


def tam_desc(desc):
    t = sin_acentos(desc)
    m = list(RX_TAM.finditer(t))
    if m:
        return a_base(float(m[-1].group(1).replace(",", ".")), m[-1].group(2))
    m = re.search(r"\bX\s*(\d+)\s*$", t)
    if m:
        return float(m.group(1)), "u"
    return None


def tamano_fila(q, u, desc):
    """Columnas de presentación; si dicen '1 unidad' (Carrefour) se busca la medida en la descripción."""
    t = a_base(q, u)
    if t is None or (t[1] == "u" and t[0] == 1):
        d = tam_desc(desc)
        return d if d is not None else t
    return t


def tam_texto(q, u):
    if q is None or u is None or (isinstance(q, float) and np.isnan(q)):
        return ""
    fmt = lambda x: f"{x:,.2f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".")
    if u == "g":
        return f"{fmt(q / 1000)} kg" if q >= 1000 else f"{fmt(q)} g"
    if u == "ml":
        return f"{fmt(q / 1000)} L" if q >= 1000 else f"{fmt(q)} ml"
    return f"{fmt(q)} {u}"


REF = {"g": ("kg", 1000), "ml": ("L", 1000), "u": ("u", 1), "m": ("m", 1), "m2": ("m2", 1)}

# ---------------------------------------------------------------- qué es (productos envasados)
REGLAS = [
    ("Agua saborizada", r"AGUA\b.*SABORIZADA|AGUA SABOR"),
    ("Agua", r"AGUA\b(?!.*(OXIGENADA|COLONIA|MICELAR|ROSAS|TERMAL|PERFUM))"),
    ("Gaseosa", r"GASEOSA|COCA ?COLA|PEPSI\b|SPRITE\b|7 ?UP\b|FANTA\b|SCHWEPPES|MANAOS|PASO DE LOS TOROS|CUNNINGTON|SECCO\b"),
    ("Cerveza", r"CERVEZA"),
    ("Vino", r"VINO\b|MALBEC|CABERNET|TORRONTES|CHARDONNAY|MERLOT|SYRAH|BONARDA"),
    ("Fernet", r"FERNET"), ("Sidra", r"SIDRA"), ("Espumante", r"ESPUMANTE|CHAMPAGNE|CHAMPANA"),
    ("Bebida blanca / aperitivo", r"WHISKY|VODKA|\bGIN\b|\bRON\b|APERITIVO|LICOR|VERMOUTH|VERMUT|GANCIA|CAMPARI|APEROL|CYNAR"),
    ("Jugo en polvo", r"JUGO (EN )?POLVO|\bTANG\b|\bCLIGHT\b|\bBC\b JUGO|JUGO .*SOBRE"),
    ("Jugo", r"JUGO|NECTAR\b"),
    ("Bebida isotónica / energizante", r"ISOTONIC|GATORADE|POWERADE|ENERGIZANTE|RED BULL|MONSTER|SPEED\b"),
    ("Yerba", r"YERBA"), ("Mate cocido", r"MATE COCIDO"),
    ("Té", r"\bTE\b"), ("Café", r"\bCAFE\b"),
    ("Leche chocolatada / cacao", r"CHOCOLATADA|CACAO EN POLVO|NESQUIK|TODDY|CHOCOLINO"),
    ("Dulce de leche", r"DULCE DE LECHE"), ("Leche condensada", r"LECHE CONDENSADA"),
    ("Leche en polvo", r"LECHE (ENTERA |DESCREMADO |DESCREMADA )?EN POLVO"),
    ("Crema de leche", r"CREMA (DE LECHE|PARA BATIR|DOBLE|CHANTILLY)"),
    ("Bebida vegetal", r"BEBIDA (DE |A BASE DE )?(ALMENDRA|AVENA|SOJA|COCO|ARROZ)|LECHE DE (ALMENDRA|COCO|SOJA|AVENA)"),
    ("Leche descremada", r"LECHE\b.*(DESCREMAD|PARCIAL)"),
    ("Leche entera", r"LECHE\b.*ENTER"),
    ("Leche", r"LECHE\b(?!.*(CORPORAL|DESMAQUILL|LIMPIEZA|PERRO|GATO|BEBE|INFANTIL))"),
    ("Leche infantil", r"LECHE\b.*(BEBE|INFANTIL)|NUTRILON|NUTRIBABY|SANCOR BEBE|VITAL 3"),
    ("Yogur", r"YOGUR|YOGHURT"),
    ("Queso rallado", r"QUESO\b.*RALLADO|RALLADO\b.*QUESO"),
    ("Queso crema / untable", r"QUESO (CREMA|UNTABLE)|CASANCREM|MENDICRIM|FINLANDIA"),
    ("Queso", r"QUESO|MOZZARELLA|MUZZARELLA|REGGIANITO|CREMOSO\b|PATEGRAS|PORT SALUT|TYBO|DAMBO|FONTINA|GOUDA"),
    ("Manteca", r"MANTECA\b(?!.*(MANI|CACAO|KARITE))"), ("Margarina", r"MARGARINA"), ("Ricota", r"RICOTA"),
    ("Postre / flan", r"POSTRE\b(?!.*POLVO)|\bFLAN\b(?!.*POLVO)|SERENITO|MOUSSE|ARROZ CON LECHE"),
    ("Huevos", r"HUEVO"),
    ("Quitaesmalte", r"QUITAESMALTE"),
    ("Higiene del bebé", r"(ACEITE|SHAMPOO|CREMA|COLONIA|TALCO|JABON|OLEO)\b.*\b(BEBE|BABY)\b|JOHNSON"),
    ("Galletas / snacks de arroz", r"ARROZ\b.*(SNACK|BIZCOCH|GALLET|TOSTADA|CROCANTE|INFLADO)|(GALLETAS?|TOSTADAS?|SNACKS?) DE ARROZ"),
    ("Aceite en aerosol", r"ROCIO VEGETAL|FRITOLIM|ACEITE\b.*AEROSOL|ANTIADHERENTE"),
    ("Aceite de oliva", r"ACEITE\b.*OLIVA"),
    ("Aceite", r"ACEITE\b(?!.*(BEBE|BABY|JOHNSON|CORPORAL|CAPILAR|MOTOR|ESENCIAL|MASAJE|ROSA MOSQUETA|ARGAN|PELO|CABELLO))"),
    ("Vinagre", r"VINAGRE|ACETO"), ("Azúcar", r"AZUCAR\b(?!.*(SIN|0 ))"), ("Edulcorante", r"EDULCORANTE|STEVIA|SUCRALOSA"),
    ("Harina", r"HARINA"),
    ("Tapas de empanada / tarta", r"TAPAS? (DE |PARA )?(PASCUALINA|EMPANADA|TARTA|COPETIN)|MASA (DE |PARA )?(EMPANADA|TARTA|HOJALDRE|PIZZA)"),
    ("Pastas frescas", r"RAVIOL|SORRENTINO|NOQUI|CAPELETTI|CANELON|LASAGN|AGNOLOTI|PANZOTTI"),
    ("Fideos", r"FIDEO|SPAGHETTI|TALLARIN|MOSTACHOL|TIRABUZON|MONITO|CODITO|PENNE|RIGATI|FETUCC|DEDALITO|"
               r"MUNICION|ENTREFINO|VERMICELLI|CAPELLINI|FUSILLI|TALLARINES|PASTA SECA|MOSTACCIOLI|FARFALLE|ROTINI|CINTAS? (AL HUEVO|DE SEMOLA)"),
    ("Cinta adhesiva", r"CINTA|ADHESIV"),
    ("Arroz", r"ARROZ\b(?!.*(BEBIDA|GALLET))"), ("Polenta", r"POLENTA"),
    ("Legumbres", r"LENTEJA|POROTO|GARBANZO|ARVEJA"),
    ("Tomate envasado", r"PURE DE TOMATE|TOMATE TRITURADO|PULPA DE TOMATE|PASSATA|TOMATES? (ENTERO|PELADO|PERITA EN)|"
                        r"TRITURADO DE TOMATE|SALSA DE TOMATE"),
    ("Salsa", r"SALSA"), ("Mayonesa", r"MAYONESA"), ("Ketchup", r"KETCHUP"), ("Mostaza", r"MOSTAZA"),
    ("Aderezo", r"ADEREZO|SAVORA|GOLF\b"),
    ("Sal", r"\bSAL\b(?! DE (BANO|FRUTA))"), ("Caldo", r"CALDO"), ("Sopa", r"SOPA"), ("Puré", r"\bPURE\b"),
    ("Condimento / especia", r"CONDIMENTO|PIMIENTA|OREGANO|PIMENTON|COMINO|NUEZ MOSCADA|ADOBO|PROVENZAL|AJI MOLIDO|CANELA|ESPECIA"),
    ("Atún", r"ATUN"), ("Conserva de pescado", r"CABALLA|SARDINA|JUREL|SALMON EN|MEJILLON|CALAMAR EN"),
    ("Aceitunas", r"ACEITUNA"), ("Conserva vegetal / fruta", r"CHOCLO (EN|CREMOSO|AMARILLO)|ALMIBAR|EN CONSERVA|PALMITO|CHAMPIGNON"),
    ("Dulce de batata / membrillo", r"DULCE DE (BATATA|MEMBRILLO)"), ("Mermelada", r"MERMELADA|CONFITURA"), ("Miel", r"\bMIEL\b"),
    ("Cereales / avena", r"CEREAL|COPOS|GRANOLA|AVENA|MUESLI|CORN FLAKES|ZUCARITAS|CHOCAPIC|FROOT LOOPS"),
    ("Barra de cereal", r"BARRA (DE )?CEREAL|BARRITA|\bBARRAS?\b(?!.*(QUESO|CHOCOLATE))"),
    ("Alfajor", r"ALFAJOR"),
    ("Galletitas", r"GALLETITA|GALLETA|CRACKER|OBLEA|VAINILLAS?\b|BIZCOCHITO DULCE|COOKIE|MINI ?TORTITA"),
    ("Budín / bizcochuelo", r"BUDIN|BIZCOCHUELO(?!.*POLVO)|MAGDALENA|MUFFIN|PANETON|PAN DULCE"),
    ("Chocolate", r"CHOCOLATE|BOMBON|TABLETA"),
    ("Golosinas", r"CARAMELO|CHICLE|PASTILLA|GOMITA|CHUPETIN|TURRON|CONFITE|MASTICABLE|GOMAS?\b"),
    ("Snacks", r"PAPAS FRITAS|SNACK|PALITOS|CHIZITO|CHEETOS|CHIPS|NACHOS|\bMANI\b|PALOMITA|POCHOCLO|TUTUCA|DORITOS|LAYS"),
    ("Frutos secos", r"NUEZ|NUECES|ALMENDRA|CASTANA|PASAS? DE UVA|MIX (DE )?FRUTOS|FRUTOS SECOS"),
    ("Pan rallado / rebozador", r"PAN RALLADO|REBOZADOR"),
    ("Pizza / prepizza", r"PREPIZZA|PIZZA"),
    ("Tostadas / grisines", r"TOSTADA|GRISIN|BIZCOCHITO|CRIOLLITA|TOSTADITA"),
    ("Pan", r"\bPAN\b|BAGUETTE|MIGNON|FACTURA|MEDIALUNA|PAN DE MOLDE|LACTAL|HOGAZA"),
    ("Repostería", r"PREMEZCLA|POLVO PARA|GELATINA|LEVADURA|POLVO DE HORNEAR|ESENCIA|BIZCOCHUELO EN POLVO|"
                   r"FLAN EN POLVO|POSTRE EN POLVO|CHIPA|COBERTURA|GRANA"),
    ("Hamburguesas / medallones", r"HAMBURGUESA|MEDALLON"), ("Salchichas", r"SALCHICHA|PANCHO"),
    ("Jamón", r"JAMON"), ("Fiambre", r"SALAME|MORTADELA|FIAMBRE|PALETA (COCIDA|SANDWICH)|LOMITO|SALCHICHON|PANCETA|BONDIOLA"),
    ("Rebozados congelados", r"MILANESA|NUGGET|PATITAS|BOCADITO|FORMITAS|REBOZAD"),
    ("Helado", r"HELADO"), ("Congelados", r"CONGELAD|FROZEN|PAPAS (PRE)?FRITAS CONGELADAS|BASTONES"),
    ("Desodorante de ambiente", r"DESODORANTE (DE )?AMBIENTE|AROMATIZANTE|AEROSOL AMBIENTE|POETT|GLADE|SAPHIRUS|DIFUSOR"),
    ("Desodorante", r"DESODORANTE|ANTITRANSPIRANTE"),
    ("Shampoo", r"SHAMPOO|SHAMPU"),
    ("Acondicionador", r"ACONDICIONADOR|ENJUAGUE CREMA|BALSAMO CAPILAR"),
    ("Tratamiento capilar", r"CREMA (PARA |DE )?PEINAR|TRATAMIENTO|MASCARA CAPILAR|ACEITE CAPILAR|SERUM|AMPOLLA|GEL PARA PELO|GEL CAPILAR|FIJADOR"),
    ("Tintura", r"TINTURA|COLORACION|TINTE|DECOLORANTE"),
    ("Jabón para ropa", r"JABON\b.*(POLVO|ROPA|LAVARROPA|MATIC)|DETERGENTE.*ROPA|LAVARROPA|QUITAMANCHA"),
    ("Suavizante", r"SUAVIZANTE"),
    ("Jabón de tocador", r"JABON"),
    ("Detergente", r"DETERGENTE|LAVAVAJILLA|LAVAPLATO"),
    ("Lavandina", r"LAVANDINA|\bCLORO\b"),
    ("Limpiadores", r"LIMPIADOR|LIMPIA\b|DESENGRASANTE|ANTIGRASA|MULTIUSO|\bCIF\b|DESINFECTANTE|AUTOBRILLO|\bCERA\b|"
                    r"LIMPIAVIDRIO|ANTISARRO|DESTAPA|LUSTRA|QUITASARRO|MR MUSCULO|PROCENEX|ODEX"),
    ("Papel higiénico", r"PAPEL HIGIENICO|HIGIENICO"),
    ("Rollo de cocina", r"ROLLO (DE )?COCINA|PAPEL (DE )?COCINA|TOALLA DE PAPEL"),
    ("Servilletas / pañuelos", r"SERVILLETA|PANUELO"),
    ("Pañales", r"PANAL|PANALES"),
    ("Higiene femenina", r"TOALLAS? (FEMENINA|HIGIENICA)|PROTECTOR(ES)? DIARIO|TAMPON|TOALLITAS FEMENINAS"),
    ("Toallitas húmedas", r"TOALLITAS? HUMEDA|TOALLITAS? (PARA )?BEBE"),
    ("Pasta dental", r"CREMA DENTAL|PASTA DENTAL|DENTIFRICO|GEL DENTAL"),
    ("Cepillo dental", r"CEPILLO (DENTAL|DE DIENTES)"), ("Enjuague bucal", r"ENJUAGUE BUCAL|COLUTORIO"),
    ("Afeitado", r"AFEITAR|AFEITADORA|MAQUINITA|PRESTOBARBA|HOJAS GILLETTE"),
    ("Protector solar", r"PROTECTOR SOLAR|BRONCEADOR|PANTALLA SOLAR"),
    ("Crema / cuidado de piel", r"CREMA|LOCION|EMULSION|GEL (DE )?LIMPIEZA|MICELAR|DESMAQUILL|ROSA MOSQUETA"),
    ("Algodón / hisopos", r"ALGODON|HISOPO"),
    ("Bolsas de residuos", r"BOLSAS? (DE )?(RESIDUO|CONSORCIO)|RESIDUOS"),
    ("Artículos de limpieza", r"ESPONJA|REJILLA|TRAPO|\bPANO\b|GUANTE|ESCOBA|SECADOR|BALDE|LAMPAZO|SOPAPA"),
    ("Insecticida / repelente", r"INSECTICIDA|REPELENTE|\bRAID\b|FUYI|\bOFF\b|ESPIRAL|MOSQUITO|CUCARACH|HORMIG"),
    ("Pilas", r"\bPILAS?\b|BATERIA"), ("Fósforos / encendedor", r"FOSFORO|ENCENDEDOR"),
    ("Alimento para mascotas", r"(ALIMENTO|COMIDA).*(PERRO|GATO|MASCOTA|CACHORRO|FELINO|CANINO)|DOG CHOW|CAT CHOW|PEDIGREE|WHISKAS|EUKANUBA|"
                               r"PRO PLAN|EXCELLENT|SIEGER|ROYAL CANIN|PIEDRAS? SANITARIA"),
    ("Papel aluminio / film", r"ALUMINIO|PAPEL FILM|FILM\b|PAPEL MANTECA"),
]
REGLAS = [(n, re.compile(p)) for n, p in REGLAS]


def que_es(textos):
    """Primero reglas que calzan con el comienzo de la descripción, después en cualquier parte; si nada, primera palabra."""
    for t in textos:
        for n, rx in REGLAS:
            if rx.match(t):
                return n, "regla", rx
    for t in textos:  # en cualquier parte: gana la palabra que aparece primero ("GALLO SNACKS ... ARROZ" = snack)
        hallados = [(m.start(), k) for k, (n, rx) in enumerate(REGLAS) for m in [rx.search(t)] if m]
        if hallados:
            n, rx = REGLAS[min(hallados)[1]]
            return n, "regla", rx
    t = textos[0] if textos else ""
    return (t.split()[0].capitalize() if t else ""), "primera palabra (revisar)", None


# ---------------------------------------------------------------- frescos (sin código de barras)
FRESCOS = [
    ("Fruta / verdura deshidratada", r"DESHIDRAT|DISECAD|\bSECOS?\b|OREJONES"),
    ("Frutos secos (por peso)", r"NUEZ|NUECES|ALMENDRA|CASTANA|PASAS?\b|\bMANI\b|PISTACH|AVELLANA|SEMILLAS?"),
    ("Pollo: milanesa", r"MILANESA.*(POLLO|SUPREMA)"), ("Pollo: pata muslo", r"PATA\b.*MUSLO|MUSLO"),
    ("Pollo: pechuga", r"PECHUGA"), ("Pollo: suprema", r"SUPREMA"), ("Pollo: alitas", r"ALITA|ALAS? DE POLLO"),
    ("Pollo: menudos", r"MENUDO|HIGADO DE POLLO|CORAZON DE POLLO"),
    ("Pollo entero", r"\bPOLLO\b"),
    ("Cerdo: bondiola", r"BONDIOLA(?!.*(COCID|FETEAD|CURAD))"), ("Cerdo: carré", r"CARRE"), ("Cerdo: pechito", r"PECHITO"),
    ("Cerdo: solomillo", r"SOLOMILLO"), ("Cerdo: matambre", r"MATAMBRE.*CERDO|MATAMBRITO"),
    ("Cerdo: costilla", r"COSTILL.*CERDO|CERDO.*COSTILL"), ("Cerdo: paleta", r"PALETA.*CERDO|CERDO.*PALETA"),
    ("Cerdo: nalga", r"NALGA.*CERDO|CERDO.*NALGA"), ("Cerdo: otro corte", r"CERDO|PORCINO"),
    ("Jamón cocido", r"JAMON COCIDO|JAMON (NATURAL|SANDWICH)"), ("Jamón crudo", r"JAMON CRUDO|SERRANO"),
    ("Salame / salamín", r"SALAME|SALAMIN"), ("Mortadela", r"MORTADELA"),
    ("Fiambre (otro)", r"PALETA (COCIDA|SANDWICH)|LOMITO|PANCETA|BONDIOLA|SALCHICHON|LEBERWURST|PASTRON|PASTRAMI"),
    ("Queso", r"QUESO|PATEGRAS|TYBO|MOZZARELLA|MUZZARELLA|REGGIANITO|SARDO|FONTINA|GOUDA|DAMBO|PORT SALUT|PROVOLONE"),
    ("Milanesa de carne", r"MILANESA"),
    ("Carne picada especial", r"PICAD.*ESPECIAL|PICAD.*MAGRA"), ("Carne picada común", r"PICAD"),
    ("Tapa de asado", r"TAPA DE ASADO"), ("Asado", r"ASADO|COSTILLA|BANDERITA"), ("Vacío", r"VACIO"),
    ("Matambre", r"MATAMBRE"), ("Tapa de nalga", r"TAPA DE NALGA"), ("Nalga", r"NALGA"), ("Cuadrada", r"CUADRADA"),
    ("Peceto", r"PECETO"), ("Bola de lomo", r"BOLA DE LOMO"), ("Colita de cuadril", r"COLITA"), ("Cuadril", r"CUADRIL"),
    ("Lomo", r"\bLOMO\b"), ("Bife de chorizo", r"BIFE.*CHORIZO"), ("Bife ancho / ojo de bife", r"BIFE ANCHO|OJO DE BIFE|ENTRECOT"),
    ("Bife angosto", r"BIFE ANGOSTO"), ("Bife (otro)", r"\bBIFE"), ("Entraña", r"ENTRANA"), ("Osobuco", r"OSO ?BUCO|OSSOBUCO"),
    ("Paleta", r"PALETA"), ("Roast beef", r"ROAST|ROSBIF"), ("Carnaza", r"CARNAZA"), ("Falda", r"FALDA"),
    ("Aguja", r"AGUJA"), ("Tortuguita", r"TORTUGUITA"), ("Chorizo", r"\bCHORIZO"), ("Morcilla", r"MORCILLA"),
    ("Salchicha parrillera", r"SALCHICHA"), ("Molleja", r"MOLLEJA"), ("Hígado", r"HIGADO"), ("Riñón", r"RINON"),
    ("Chinchulín", r"CHINCHULIN"), ("Carne (otro corte)", r"CARNE|VACUNO|NOVILLO|TERNERA"),
    ("Pescado", r"MERLUZA|SALMON|ABADEJO|PESCADO|LANGOSTINO|CORNALITO|CALAMAR|TRUCHA|PEJERREY|BROTOLA"),
    ("Tomate perita", r"TOMATE.*PERITA"), ("Tomate cherry", r"TOMATE.*CHERRY"), ("Tomate", r"TOMATE"),
    ("Papa negra", r"PAPA.*NEGRA"), ("Papa blanca", r"PAPA.*BLANCA"), ("Papa", r"\bPAPAS?\b"), ("Batata", r"BATATA"),
    ("Cebolla morada", r"CEBOLLA.*(MORADA|COLORADA)"), ("Cebolla de verdeo", r"VERDEO"), ("Cebolla", r"CEBOLLA"),
    ("Ajo", r"\bAJOS?\b"), ("Zanahoria", r"ZANAHORIA"), ("Zapallo anco", r"\bANCO\b|ANQUITO"),
    ("Zapallo cabutia", r"[CK]ABUTIA"), ("Zapallito", r"ZAPALLITO"), ("Zucchini", r"ZUCC?HINI"), ("Zapallo", r"ZAPALLO"),
    ("Lechuga", r"LECHUGA"), ("Acelga", r"ACELGA"), ("Espinaca", r"ESPINACA"), ("Rúcula", r"RUCULA"),
    ("Morrón rojo", r"(MORRON|PIMIENTO).*ROJO"), ("Morrón verde", r"(MORRON|PIMIENTO).*VERDE"),
    ("Morrón amarillo", r"(MORRON|PIMIENTO).*AMARILL"), ("Morrón", r"MORRON|PIMIENTO"),
    ("Pepino", r"PEPINO"), ("Berenjena", r"BERENJENA"), ("Brócoli", r"BROC+OLI"), ("Coliflor", r"COLIFLOR"),
    ("Repollo", r"REPOLLO"), ("Choclo", r"CHOCLO"), ("Puerro", r"PUERRO"), ("Apio", r"\bAPIO"), ("Remolacha", r"REMOLACHA"),
    ("Champiñón / hongos", r"CHAMPI|HONGO|GIRGOLA|PORTOBELLO"), ("Jengibre", r"JENGIBRE"), ("Perejil", r"PEREJIL"),
    ("Albahaca", r"ALBAHACA"), ("Rabanito", r"RABAN"), ("Chaucha", r"CHAUCHA"), ("Hinojo", r"HINOJO"),
    ("Banana", r"BANANA"), ("Manzana roja", r"MANZANA.*(ROJA|RED|DELICIOUS)"), ("Manzana verde", r"MANZANA.*(VERDE|GRANNY)"),
    ("Manzana", r"MANZANA"), ("Pera", r"\bPERAS?\b"), ("Naranja", r"NARANJA"), ("Mandarina", r"MANDARINA"),
    ("Limón", r"LIMON"), ("Pomelo", r"POMELO"), ("Frutilla", r"FRUTILLA"), ("Kiwi", r"KIWI"), ("Durazno", r"DURAZNO"),
    ("Ciruela", r"CIRUELA"), ("Uva", r"\bUVAS?\b"), ("Palta", r"PALTA"), ("Ananá", r"ANANA"), ("Melón", r"MELON"),
    ("Sandía", r"SANDIA"), ("Mango", r"\bMANGO"), ("Arándanos", r"ARANDANO"), ("Cereza", r"CEREZA"),
    ("Huevos", r"HUEVO"), ("Pan (por peso)", r"\bPAN\b|FELIPE|FRANCES|MINON|FACTURA|MEDIALUNA"),
]
FRESCOS = [(n, re.compile(p)) for n, p in FRESCOS]
RELLENO = {"X", "KG", "KGS", "KILO", "POR", "DE", "LA", "EL", "EN", "X1", "1", "KGM", "GRANEL", "SC", "FRESCO", "FRESCA",
           "UNIDAD", "UN", "UNI", "XKG", "Y", "CON", "SIN", "AL", "VACIO"}


def fresco(t):
    for n, rx in FRESCOS:
        if rx.search(t):
            return n
    return "(sin clasificar)"


# ---------------------------------------------------------------- promociones
def tipo_promo(ley):
    t = norm(ley)
    if not t:
        return ""
    if re.search(r"BANCO|\bBCO\b|TARJ|VISA|MASTER|CABAL|AMEX|NARANJA X|MERCADO ?PAGO|\bMODO\b|CUENTA DNI|BILLETERA|"
                 r"CREDITO|DEBITO|\bCRED\b|COMUNIDAD|MI CARREFOUR|\bCLUB\b|SOCIO|JUBILAD|REINTEGRO|\bREINT\b", t):
        return "medio de pago / fidelidad"
    if re.search(r"\b\d+ ?X ?\d+\b|\b2DO\b|\b2DA\b|SEGUND|LLEVANDO|LLEVA \d|COMPRANDO \d|COMBO|\b\d+ UNIDADES\b", t):
        return "por cantidad"
    if re.match(r"PROMO [A-Z] VALIDA", t):
        return "sin detalle"
    return "general"


# ---------------------------------------------------------------- carga
def num(s):
    return pd.to_numeric(s.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")


def cargar():
    con = duckdb.connect()
    tmp = Path(tempfile.mkdtemp())
    zips = sorted(CRUDOS.glob("*/sepa_*.zip"))[-7:]
    partes = []
    for zp in zips:
        fecha = zp.stem.replace("sepa_", "")
        z = zipfile.ZipFile(zp)
        for i in z.infolist():
            if not i.filename.endswith(".zip"):
                continue
            try:
                zi = zipfile.ZipFile(io.BytesIO(z.read(i)))
                with zi.open("comercio.csv") as f:
                    ids = set(pd.read_csv(f, sep="|", dtype=str, on_bad_lines="skip")["id_comercio"].dropna().str.strip())
            except Exception:
                continue
            mios = [k for k in LOCALES if k[0] in ids]
            if not mios:
                continue
            zi.extract("productos.csv", tmp)
            ruta = (tmp / "productos.csv").as_posix()
            for c, b, s in mios:
                df = con.execute(f"select * from read_csv('{ruta}', {OPC}) where trim(id_comercio)=? and trim(id_bandera)=? "
                                 f"and trim(id_sucursal)=?", [c, b, s]).df()
                df["local"] = LOCALES[(c, b, s)]
                df["fecha"] = fecha
                partes.append(df)
            (tmp / "productos.csv").unlink()
        print(f"  {fecha} leído", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    p = pd.concat(partes, ignore_index=True)
    p["id"] = p["id_producto"].astype(str).str.strip()
    flag = p["productos_ean"].astype(str).str.strip() == "1"
    interno = p["id"].str.zfill(13).str.len().eq(13) & p["id"].str.zfill(13).str.startswith("2")
    p["ean"] = np.where(flag & p["id"].str.fullmatch(r"\d{8,14}") & ~interno, p["id"].str.lstrip("0"), None)
    p["lista"] = num(p["productos_precio_lista"])
    p["promo1"] = num(p["productos_precio_unitario_promo1"])
    p["promo2"] = num(p["productos_precio_unitario_promo2"])
    p["cant"] = num(p["productos_cantidad_presentacion"])
    p["desc"] = p["productos_descripcion"].fillna("").astype(str).str.strip()
    p["marca_local"] = p["productos_marca"].fillna("").astype(str).str.strip()
    return p[p["lista"] > 0].copy(), zips


def otras_cadenas(eans, zips):
    archivos = [(RESUMEN / f"{z.stem}.parquet").as_posix() for z in zips if (RESUMEN / f"{z.stem}.parquet").exists()]
    con = duckdb.connect()
    con.register("mis", pd.DataFrame({"ean": sorted(eans)}))
    return con.execute(f"""select producto as ean, descripcion, marca, cantidad, unidad, count(*) as n
                           from read_parquet({archivos}) where producto in (select ean from mis) group by all""").df()


# ---------------------------------------------------------------- atributos por EAN
MARCA_MALA = {"", "SIN MARCA", "GENERICO", "GENERICA", "VARIOS", "S M", "NAN", "NONE", "SM", "OTROS", "OTRAS MARCAS",
              "SIN DE", "S D", "SIN", "SIN MARCA DE", "SD", "SIN NOMBRE"}
RX_ENVASADO = re.compile(r"\b(LAT|LATA|POT|BOT|BOTELLA|PAQ|BSA|DOY|FRASCO|TETRA|SACHET|CJA|CAJA|POU|TTB|SOB|ENVASE|BLISTER|BLI|BAN|BANDEJA)\b")
EMPAQUE = {"X", "BOT", "POT", "PAQ", "SOB", "DOY", "POU", "BSA", "LAT", "CJA", "BLISTER", "TET", "BOTELLA", "PET", "FCO", "SACHET",
           "DOYPACK", "LATA", "ESTUCHE", "TETRA", "BRICK", "FRASCO", "BANDEJA", "VASO", "UNI", "UN", "U"}
RX_TAM_NORM = re.compile(r"\b\d+( \d+)?\s*(KGS?|KGM|KILOS?|GRS?|GRM|G|MLS?|CC|CM3|CMQ|CL|LTS?|LTR|L|UNIDADES|UNID|UNI|UN|UD|U|"
                         r"MTS?|MTR|M2|M)\b")


def atributos(p, otros):
    filas = []
    cand_otros = {k: g for k, g in otros.groupby("ean")}
    for ean, g in p[p["ean"].notna()].groupby("ean"):
        o = cand_otros.get(ean)
        # descripciones candidatas (las de las 3 sucursales y las de otras cadenas)
        descs = Counter(g["desc"].tolist())
        marcas = Counter(norm(m) for m in g["marca_local"])
        tams = Counter(t for t in (tamano_fila(q, u, d) for q, u, d in
                                   zip(g["cant"], g["productos_unidad_medida_presentacion"], g["desc"])) if t)
        if o is not None:
            for d, m, q, u, n in o[["descripcion", "marca", "cantidad", "unidad", "n"]].itertuples(index=False):
                descs[str(d)] += int(n)
                marcas[norm(m)] += int(n)
                if not tams:
                    t = tamano_fila(q, u, d)
                    if t:
                        tams[t] += int(n)
        puntaje = lambda d: (len([w for w in norm(d).split() if len(w) >= 3 and not w.isdigit()]), descs[d])
        orden = sorted(descs, key=puntaje, reverse=True)
        mejor = orden[0]
        # marca: la más frecuente; si quedó cortada (Día), la versión larga que empieza igual
        ms = [(m, n) for m, n in marcas.most_common() if m not in MARCA_MALA]
        marca = ms[0][0] if ms else ""
        if marca and len(marca) <= 6:
            largas = [m for m, _ in ms if m.startswith(marca) and len(m) > len(marca)]
            if largas:
                marca = largas[0]
        textos = [norm(d) for d in orden]
        tipo, fuente, rx = que_es(textos)
        # variante = descripción sin el tipo, la marca, el tamaño y el envase
        t = textos[0]
        if rx is not None:
            m = rx.search(t)
            if m:
                t = (t[:m.start()] + " " + t[m.end():]) if m.end() - m.start() < 40 else t
        t = RX_TAM_NORM.sub(" ", t)
        quitar = set(marca.split()) | EMPAQUE
        var = " ".join(w for w in t.split() if w not in quitar and not w.isdigit())
        q, u = tams.most_common(1)[0][0] if tams else (None, None)
        filas.append({"ean": ean, "Qué es": tipo, "Qué es (cómo)": fuente, "Marca": marca.title(),
                      "Variante": var.title()[:70], "q": q, "u": u, "Descripción": mejor})
    return pd.DataFrame(filas)


def por_ref(lista, q, u):
    if q is None or u is None or (isinstance(q, float) and np.isnan(q)) or u not in REF:
        return np.nan, ""
    nombre, f = REF[u]
    return lista / q * f, nombre


# ---------------------------------------------------------------- Excel
VERDE = PatternFill("solid", fgColor="C6EFCE")
GRIS = PatternFill("solid", fgColor="DDEBF7")


def hoja(writer, df, nombre, precios=(), pcts=(), congelar="B2", anchos=None):
    codigos = [c for c in df.columns if str(c).startswith("Código")]
    if codigos:  # códigos solo con dígitos → número (hasta 14 dígitos: Excel guarda 15 sin perder precisión)
        df = df.copy()
        for c in codigos:
            df[c] = [int(v) if isinstance(v, str) and v.isdigit() and len(v) <= 15 else v for v in df[c]]
    df.to_excel(writer, sheet_name=nombre, index=False)
    ws = writer.sheets[nombre]
    ws.freeze_panes = congelar
    if len(df):
        ws.auto_filter.ref = ws.dimensions
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(1, j)
        c.font = Font(bold=True)
        c.fill = GRIS
        c.alignment = Alignment(wrap_text=True, vertical="top")
        largo = df[col].astype(str).str.len().quantile(0.9) if len(df) else 10
        ancho = (anchos or {}).get(col, min(max(10, largo + 2, min(len(str(col)), 18)), 55))
        ws.column_dimensions[get_column_letter(j)].width = ancho
        if col in precios or col in pcts:
            f = "#,##0.00" if col in precios else "0.0%"
            for fila in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                fila[0].number_format = f
        if col in codigos:
            for fila in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                fila[0].number_format = "0"
    ws.row_dimensions[1].height = 45
    return ws


def main():
    print("Leyendo los crudos de SEPA...")
    p, zips = cargar()
    dia = p["fecha"].max()
    print(f"  {len(p):,} precios; último día {dia}")
    otros = otras_cadenas(set(p["ean"].dropna()), zips)
    print(f"  {len(otros):,} descripciones del mismo EAN en otras cadenas")
    atr = atributos(p, otros)

    # promociones y precio de referencia (último día de cada local)
    ult = p[p["fecha"] == p.groupby("local")["fecha"].transform("max")].copy()
    for k in (1, 2):
        ok = ult[f"promo{k}"].gt(0) & ult[f"promo{k}"].lt(ult["lista"])
        ult[f"p{k}"] = ult[f"promo{k}"].where(ok)
    usa2 = ult["p2"].notna() & (ult["p1"].isna() | (ult["p2"] < ult["p1"]))
    ult["promo"] = np.where(usa2, ult["p2"], ult["p1"])
    ult["leyenda"] = np.where(usa2, ult["productos_leyenda_promo2"], np.where(ult["p1"].notna(), ult["productos_leyenda_promo1"], ""))
    ult["leyenda"] = pd.Series(ult["leyenda"]).fillna("").astype(str).str.strip().values
    ult["tipo promo"] = [tipo_promo(l) if pd.notna(pr) else "" for l, pr in zip(ult["leyenda"], ult["promo"])]
    ult["efectivo"] = ult[["lista", "promo"]].min(axis=1)
    p["clave"] = p["ean"].fillna("id:" + p["id"])
    ult["clave"] = ult["ean"].fillna("id:" + ult["id"])
    sem = p.groupby(["local", "clave"])["lista"].agg(["min", "max"]).reset_index()
    ult = ult.merge(sem, on=["local", "clave"], how="left")

    # ---------- 1) mismo código de barras
    e = ult[ult["ean"].notna()].sort_values("lista").drop_duplicates(["local", "ean"]).merge(atr, on="ean")
    ref = [por_ref(l, q, u) for l, q, u in zip(e["lista"], e["q"], e["u"])]
    e["por_ref"] = [r[0] for r in ref]
    e["Precio por"] = [r[1] for r in ref]
    base = atr.copy()
    base["Tamaño"] = [tam_texto(q, u) for q, u in zip(base["q"], base["u"])]
    base["Precio por"] = [REF[u][0] if u in REF else "" for u in base["u"]]
    comp = base.rename(columns={"ean": "Código de barras"})[["Qué es", "Marca", "Variante", "Tamaño", "Código de barras",
                                                              "Descripción", "Qué es (cómo)", "Precio por"]]
    for L in NOMBRES:
        x = e[e["local"] == L].set_index("ean")
        comp[f"{L} lista"] = comp["Código de barras"].map(x["lista"])
        comp[f"{L} promo"] = comp["Código de barras"].map(x["promo"])
        comp[f"{L} tipo promo"] = comp["Código de barras"].map(x["tipo promo"]).fillna("")
        comp[f"{L} por kg/L/u"] = comp["Código de barras"].map(x["por_ref"])
        comp[f"{L} mín 7 días"] = comp["Código de barras"].map(x["min"])
        comp[f"{L} máx 7 días"] = comp["Código de barras"].map(x["max"])
    listas = comp[[f"{L} lista" for L in NOMBRES]]
    efect = pd.concat([comp[[f"{L} lista", f"{L} promo"]].min(axis=1).rename(L) for L in NOMBRES], axis=1)
    efect = efect.where(listas.notna().values)
    comp.insert(8, "Locales", listas.notna().sum(axis=1))
    comp.insert(9, "Más barato (lista)", np.where(listas.notna().sum(axis=1) >= 2,
                                                   listas.fillna(np.inf).idxmin(axis=1).str.replace(" lista", ""), ""))
    comp.insert(10, "Dif. más caro vs más barato (lista)", np.where(listas.notna().sum(axis=1) >= 2,
                                                                    listas.max(axis=1) / listas.min(axis=1) - 1, np.nan))
    comp.insert(11, "Más barato (con promo publicada)", np.where(efect.notna().sum(axis=1) >= 2, efect.fillna(np.inf).idxmin(axis=1), ""))
    comp.insert(12, "Precio más bajo (con promo publicada)", efect.min(axis=1))
    comp.insert(13, "Revisar", np.where(comp["Dif. más caro vs más barato (lista)"] > 0.6,
                                        "diferencia muy grande: ¿un local informa el pack y otro la unidad?", ""))
    comp = comp[comp["Locales"] > 0].sort_values(["Locales", "Qué es", "Marca", "Tamaño"], ascending=[False, True, True, True])

    # ---------- 2) sin código de barras (frescos y códigos internos)
    f = ult[ult["ean"].isna()].copy()
    f["n"] = f["desc"].map(norm)
    # latas, frascos, paquetes con código interno (marcas propias de Coto, etc.) no son frescos
    f["Producto"] = [("(envasado sin código) " + que_es([t])[0]) if RX_ENVASADO.search(t) else fresco(t) for t in f["n"]]
    tt = [tamano_fila(q, u, d) for q, u, d in zip(f["cant"], f["productos_unidad_medida_presentacion"], f["desc"])]
    f["q"] = [t[0] if t else np.nan for t in tt]
    f["u"] = [t[1] if t else None for t in tt]
    ref = [por_ref(l, q, u) for l, q, u in zip(f["lista"], f["q"], f["u"])]
    f["por_ref"] = [r[0] for r in ref]
    f["Precio por"] = [r[1] for r in ref]
    f["palabras"] = f["n"].map(lambda t: " ".join(sorted(set(w for w in t.split() if w not in RELLENO and not w.isdigit()))))
    det = f[["local", "Producto", "desc", "marca_local", "q", "u", "lista", "por_ref", "Precio por", "promo", "tipo promo",
             "min", "max", "id"]].copy()
    det["Tamaño"] = [tam_texto(q, u) for q, u in zip(det["q"], det["u"])]
    det = det.rename(columns={"local": "Local", "desc": "Descripción del local", "marca_local": "Marca", "lista": "Lista",
                              "por_ref": "Lista por kg/L/u", "promo": "Promo", "tipo promo": "Tipo promo",
                              "min": "Mín 7 días", "max": "Máx 7 días", "id": "Código interno"})
    det = det[["Producto", "Local", "Descripción del local", "Marca", "Tamaño", "Lista", "Promo", "Tipo promo", "Lista por kg/L/u",
               "Precio por", "Mín 7 días", "Máx 7 días", "Código interno"]].sort_values(["Producto", "Local", "Lista por kg/L/u"])
    fr = f[(f["Producto"] != "(sin clasificar)") & ~f["Producto"].str.startswith("(envasado") & f["por_ref"].notna()]
    filas = []
    for (prod, unid), g in fr.groupby(["Producto", "Precio por"]):
        r = {"Producto": prod, "Precio por": unid}
        elegidos = []
        for L in NOMBRES:
            h = g[g["local"] == L].sort_values("por_ref")
            if len(h):
                b = h.iloc[0]
                r[f"{L} más barato (lista por {unid})"] = b["por_ref"]
                r[f"{L} con promo"] = min(b["por_ref"], b["promo"] / b["q"] * REF[b["u"]][1]) if pd.notna(b["promo"]) else np.nan
                r[f"{L} descripción"] = b["desc"]
                r[f"{L} opciones"] = len(h)
                elegidos.append(b["palabras"])
        vals = {L: r.get(f"{L} más barato (lista por {unid})") for L in NOMBRES if f"{L} más barato (lista por {unid})" in r}
        r["Locales"] = len(vals)
        r["Más barato"] = min(vals, key=vals.get) if len(vals) >= 2 else ""
        r["Dif. más caro vs más barato"] = max(vals.values()) / min(vals.values()) - 1 if len(vals) >= 2 else np.nan
        r["Coincidencia"] = ("exacta (misma descripción)" if len(set(elegidos)) == 1 else "aproximada (ver descripciones)") \
            if len(vals) >= 2 else ""
        filas.append(r)
    frescos = pd.DataFrame(filas)
    cols = ["Producto", "Precio por", "Locales", "Más barato", "Dif. más caro vs más barato", "Coincidencia"]
    for L in NOMBRES:
        cols += [f"{L} más barato (lista por kg)", f"{L} con promo", f"{L} descripción", f"{L} opciones"]
    for L in NOMBRES:  # el precio puede ser por kg o por unidad: una columna por local
        frescos[f"{L} más barato (lista por kg)"] = frescos.filter(like=f"{L} más barato (lista por").bfill(axis=1).iloc[:, 0] \
            if len(frescos.filter(like=f"{L} más barato (lista por").columns) else np.nan
    frescos = frescos.reindex(columns=cols).rename(columns={f"{L} más barato (lista por kg)": f"{L} más barato (lista)" for L in NOMBRES})
    frescos = frescos.sort_values(["Locales", "Producto"], ascending=[False, True])

    # ---------- 3) equivalentes (mismo tipo y tamaño, cualquier marca)
    eq = e[(e["Qué es (cómo)"] == "regla") & e["q"].notna()].copy()
    eq["Tamaño"] = [tam_texto(q, u) for q, u in zip(eq["q"], eq["u"])]
    eq["efectivo"] = eq[["lista", "promo"]].min(axis=1)
    filas = []
    for (tipo, tam), g in eq.groupby(["Qué es", "Tamaño"]):
        if g["local"].nunique() < 2:
            continue
        r = {"Qué es": tipo, "Tamaño": tam, "Productos en el grupo": g["ean"].nunique(), "Locales": g["local"].nunique()}
        vals = {}
        for L in NOMBRES:
            h = g[g["local"] == L]
            if len(h):
                b = h.sort_values("lista").iloc[0]
                c = h.sort_values("efectivo").iloc[0]
                r[f"{L} más barato (lista)"] = b["lista"]
                r[f"{L} producto"] = f"{b['Marca']} {b['Variante']}".strip()
                r[f"{L} más barato con promo"] = c["efectivo"]
                r[f"{L} producto (con promo)"] = f"{c['Marca']} {c['Variante']}".strip()
                vals[L] = b["lista"]
        r["Más barato (lista)"] = min(vals, key=vals.get)
        r["Dif. más caro vs más barato"] = max(vals.values()) / min(vals.values()) - 1
        filas.append(r)
    equiv = pd.DataFrame(filas)
    cols = ["Qué es", "Tamaño", "Productos en el grupo", "Locales", "Más barato (lista)", "Dif. más caro vs más barato"]
    for L in NOMBRES:
        cols += [f"{L} más barato (lista)", f"{L} producto", f"{L} más barato con promo", f"{L} producto (con promo)"]
    equiv = equiv.reindex(columns=cols).sort_values(["Qué es", "Tamaño"])

    # ---------- 4) promociones publicadas
    pr = ult[ult["promo"].notna()].copy()
    pr = pr.merge(atr[["ean", "Qué es", "Marca"]], on="ean", how="left")
    pr["Descuento"] = 1 - pr["promo"] / pr["lista"]
    promos = pr.rename(columns={"local": "Local", "desc": "Descripción del local", "lista": "Lista", "promo": "Precio promo",
                                "tipo promo": "Tipo", "leyenda": "Leyenda (tal cual la publica la cadena)", "clave": "Código"})
    promos = promos[["Local", "Qué es", "Marca", "Descripción del local", "Lista", "Precio promo", "Descuento", "Tipo",
                     "Leyenda (tal cual la publica la cadena)", "Código"]].sort_values(["Local", "Descuento"], ascending=[True, False])

    # ---------- Léeme
    n_ok = (comp["Qué es (cómo)"] == "regla").mean()
    leeme = pd.DataFrame({"Comparación de precios": [
        f"Precios del {dia} (último día publicado); mínimo y máximo de los 7 días del {zips[0].stem[5:]} al {zips[-1].stem[5:]}.",
        "Fuente: SEPA / Precios Claros (Secretaría de Comercio). Precio informado por cada sucursal; la ley obliga a que sea el de góndola.",
        "", "Locales:"] + [f"  - {DIRECCION[L]}" for L in NOMBRES] + [
        "", "Hojas:",
        "  - Mismo producto: un renglón por código de barras. 'Locales' = en cuántos de los 3 hay precio (filtrá 2 o 3 para comparar).",
        "    Verde = el más barato (precio de lista). 'por kg/L/u' = precio de lista llevado a 1 kg, 1 litro o 1 unidad.",
        "  - Frescos: carnicería, verdulería, fiambrería y otros productos con código interno de la cadena, agrupados por nombre.",
        "    En cada local se muestra la opción más barata por kg (o por unidad). 'Coincidencia aproximada' = las descripciones",
        "    no son iguales (por ejemplo, banana Cavendish vs banana NOA): mirá las descripciones antes de concluir.",
        "  - Equivalentes: mismo tipo de producto y mismo tamaño, cualquier marca (incluye marcas propias). Orientativo: el tipo",
        "    se deduce de la descripción y puede juntar cosas distintas (por ejemplo, yogur bebible y firme).",
        "  - Promos publicadas: todas las promociones que informó cada local, con la leyenda original.",
        "  - Sin código (detalle): todos los productos sin código de barras, uno por renglón.",
        "", "Cómo se normalizó:",
        "  - Mismo producto = mismo código de barras. Marca y descripción se completan con el mismo código en las otras cadenas de SEPA",
        "    (Día corta los nombres a 20 letras).",
        f"  - 'Qué es' sale de reglas sobre la descripción ({n_ok:.0%} de los productos). El resto dice 'primera palabra (revisar)'.",
        "  - Tamaño: de las columnas de presentación (Coto, Día) o de la descripción (Carrefour, 'X 500 GRS').",
        "", "Promociones:",
        "  - 'promo' = mejor precio de promoción publicado ese día. Tipo: general (cualquiera, 1 unidad) · por cantidad (2do al 50%,",
        "    3x2: el precio es por unidad llevando la cantidad) · sin detalle (Carrefour solo informa 'Promo A válida desde...').",
        "  - SEPA NO informa descuentos con banco, tarjeta, Mercado Pago ni programas de fidelidad (Comunidad Coto, Mi Carrefour,",
        "    Club Día). Coto no publica ninguna promoción en SEPA.",
        "", "Ojo con las carnes de Día: las informa 'por kg' a casi la mitad del precio de Carrefour y Coto (por ejemplo, cuadril",
        "  $8.390 vs $18.499-21.700). El dato publicado es ese, pero conviene verificar en góndola si es por kg o por paquete.",
        "", "Cuidado: es una foto de un día; los supermercados cambian precios en ciclos semanales (ver mín/máx 7 días)."]})

    salida = CARPETA / f"comparacion_precios_{dia}.xlsx"
    with pd.ExcelWriter(salida, engine="openpyxl") as w:
        hoja(w, leeme, "Léeme", congelar=None, anchos={"Comparación de precios": 130})
        precios_c = [c for c in comp.columns if any(c.endswith(s) for s in (" lista", " promo", " por kg/L/u", " mín 7 días", " máx 7 días"))]
        ws = hoja(w, comp, "Mismo producto", precios=precios_c + ["Precio más bajo (con promo publicada)"],
                  pcts=["Dif. más caro vs más barato (lista)"], congelar="E2",
                  anchos={"Descripción": 45, "Variante": 30, "Código de barras": 15})
        idx = {c: j + 1 for j, c in enumerate(comp.columns)}
        for i, (_, r) in enumerate(comp.iterrows(), start=2):
            if r["Más barato (lista)"]:
                ws.cell(i, idx[f"{r['Más barato (lista)']} lista"]).fill = VERDE
        precios_f = [c for c in frescos.columns if "más barato (lista)" in c or c.endswith("con promo")]
        ws = hoja(w, frescos, "Frescos", precios=precios_f, pcts=["Dif. más caro vs más barato"], congelar="C2")
        idx = {c: j + 1 for j, c in enumerate(frescos.columns)}
        for i, (_, r) in enumerate(frescos.iterrows(), start=2):
            if r["Más barato"]:
                ws.cell(i, idx[f"{r['Más barato']} más barato (lista)"]).fill = VERDE
        precios_e = [c for c in equiv.columns if "más barato" in c and "Dif" not in c and c != "Más barato (lista)"]
        ws = hoja(w, equiv, "Equivalentes", precios=precios_e, pcts=["Dif. más caro vs más barato"], congelar="C2")
        idx = {c: j + 1 for j, c in enumerate(equiv.columns)}
        for i, (_, r) in enumerate(equiv.iterrows(), start=2):
            ws.cell(i, idx[f"{r['Más barato (lista)']} más barato (lista)"]).fill = VERDE
        hoja(w, promos, "Promos publicadas", precios=["Lista", "Precio promo"], pcts=["Descuento"], congelar="D2",
             anchos={"Leyenda (tal cual la publica la cadena)": 80})
        hoja(w, det, "Sin código (detalle)", precios=["Lista", "Promo", "Lista por kg/L/u", "Mín 7 días", "Máx 7 días"], congelar="D2")

    print(f"\nExcel: {salida}")
    print(f"Mismo producto: {len(comp):,} códigos (en 3 locales {int((comp['Locales'] == 3).sum()):,}, en 2 {int((comp['Locales'] == 2).sum()):,})")
    print(f"  'Qué es' por regla: {n_ok:.1%}; primeras palabras más comunes sin regla:")
    print(comp[comp["Qué es (cómo)"] != "regla"]["Qué es"].value_counts().head(25).to_string())
    print(f"Frescos: {len(frescos)} productos ({int((frescos['Locales'] >= 2).sum())} en 2+ locales); "
          f"sin clasificar: {int((f['Producto'] == '(sin clasificar)').sum())} de {len(f)}")
    print(f"Equivalentes: {len(equiv)} grupos | Promos: {len(promos)}")
    print("Tipos de promo:", promos.groupby(["Local", "Tipo"]).size().to_dict())


if __name__ == "__main__":
    main()
