# canasta_sepa.py
# Ubica en SEPA los productos de la planilla de precios promedio del INDEC (GBA: 59 variedades) y calcula su precio diario
# por región, llevado a la misma unidad que publica el INDEC (por kg, por 500 g, por 1,5 L, por docena, etc.).
#   - reglas por producto: palabras que la descripción debe tener y palabras que la excluyen
#   - tamaño: se lee de la descripción o de los campos cantidad/unidad; el precio se lleva a la unidad del INDEC
#   - precio del producto-región-día: mediana de los productos SEPA que coinciden (cada producto x cadena x bandera
#     cuenta una vez), descartando valores fuera de [1/3, 3] veces la mediana
#   - precio usado: de LISTA (falta aplicar la regla de promociones del INDEC)
# Entrada: E:\SEPA\resumen\sepa_*.parquet. Salidas: datos/sepa_canasta_diaria.parquet (precios) y
# datos/sepa_canasta_mapeo.csv (qué productos SEPA se asignaron a cada producto INDEC, para auditar)
# Uso: py -3.13 canasta_sepa.py
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
RESUMEN = Path("E:/SEPA/resumen")
SALIDA = CARPETA / "sepa_canasta_diaria.parquet"
MAPEO = CARPETA / "sepa_canasta_mapeo.csv"

# producto INDEC: (unidad de referencia, cantidad de referencia, palabras obligatorias, palabras excluidas, rango de tamaño aceptado)
#   unidad: "g", "ml" o "u" (unidades). El precio se lleva a la cantidad de referencia.
#   rango: (mín, máx) de la presentación aceptada en la misma unidad; None = cualquiera (típico de los "por kg")
EXCL_PROCESADO = r"congel|rebozad|empanad|milanesa|hamburg|salsa|pure|deshidrat|en polvo|chips|snack|jugo|helado|galletit|yogur|alfajor|torta|budin|caramelo|mermelada"
REGLAS = {
    "Aceite de girasol": ("ml", 1500, [r"\baceite\b", r"girasol"], r"mezcla|oliva|maiz|spray|aerosol|canola", (1200, 1800)),
    "Agua sin gas": ("ml", 1500, [r"\bagua\b"], r"saboriz|con gas|gasif|tonica|soda|jabon|oxigen|destil|lavand|colonia|perfum|micelar|desmaq|termal|coco|rosas|limpi|plancha|bateria", (1200, 2000)),
    "Algodón": ("g", 100, [r"algodon"], r"hisopo|disco|pad|desmaq|redondel|cuadrad|bolitas|toallit", (50, 200)),
    "Arroz blanco simple": ("g", 1000, [r"\barroz\b"], r"integral|yamani|parboil|risott|arborio|carnaroli|prepar|galleta|harina|sushi|jazmin|basmati|con leche|mix|salvaje|negro|rojo|yerba|bebida|snack|crocante|para microondas|listo", (800, 1200)),
    "Arvejas secas remojadas": ("u", 1, [r"arveja"], r"congel|fresca|partida|harina|sopa|con jamon|mix|ensalada", None),
    "Asado": ("g", 1000, [r"\basado\b"], r"pollo|cerdo|condiment|salsa|carbon|parrilla elec|hamburg|vacio|achura|chorizo|morcilla|cordero|sal para", None),
    "Azúcar": ("g", 1000, [r"azucar"], r"impalpable|negra|mascabo|organic|light|edulcor|rubia|glas|cana|sin azucar|0%|stevia|vainill|con|azucarad|bombon|caramel", (800, 1200)),
    "Banana": ("g", 1000, [r"\bbanana"], r"dulce|licuad|chips|deshidrat|yogur|cereal|barra|batido|bebida|helado|galletit|snack|pure|torta", None),
    "Batata": ("g", 1000, [r"\bbatata"], r"dulce|chips|pure|congel|snack|bastones", None),
    "Café molido": ("g", 500, [r"\bcafe\b", r"molido"], r"instant|capsula|descafein|soluble|con leche|cappu|saboriz", (200, 1100)),
    "Carne picada común": ("g", 1000, [r"picad"], r"especial|premium|magra|cerdo|pollo|soja|vegetal|especial|nalga|cebolla|ajo|perejil|aceitun|mani|almendr|nuez|frutos|tomate|verdura|jamon|queso", None),
    "Cebolla": ("g", 1000, [r"\bcebolla"], r"deshidrat|polvo|salsa|escabech|frita|sopa|verdeo|caramel|crispy|aros|snack|pure|encurt|morada en", None),
    "Cerveza en botella": ("ml", 1000, [r"cerveza"], r"\blata\b|sin alcohol|0,0|0\.0|00%|sin tacc|barril|copa|vaso|jabon|shampoo", (900, 1100)),
    "Champú": ("ml", 400, [r"shampoo|champu"], r"acondicion|seco|perro|mascota|alfombra|auto|bebe", (300, 750)),
    "Cuadril": ("g", 1000, [r"cuadril"], r"colita|milanesa|hamburg|empanad", None),
    "Desodorante": ("ml", 150, [r"desodorante"], r"piso|ambient|inodoro|\bpie|calzado|auto|textil|ropa|mascota|bano|cocina", (90, 250)),
    "Detergente líquido": ("ml", 750, [r"detergente"], r"ropa|polvo|maquina|pastilla|lavarropa|capsula|repuesto|ecologico pastilla", (400, 1250)),
    "Dulce de leche": ("g", 400, [r"dulce de leche"], r"alfajor|helado|galletit|conito|tapa|bocadit|torta|budin|yogur|postre|reposter|bombon|oblea|vainill|cereal|barra|chocolate|caramel|cookie|vauquita|alfajorcito", (200, 1000)),
    "Fideos secos tipo guisero": ("g", 500, [r"fideo|tirabuzon|codito|mostachol|monito|rigatti|penne|fusil|guiser"], r"fresco|integral|de arroz|maiz|legumbre|lenteja|garbanzo|sopa|instant|spaghetti|tallarin|cabello|tallar|lasan|nido|vermicel|mono|soja", (400, 1100)),
    "Filet de merluza fresco": ("g", 1000, [r"merluza"], r"rebozad|empanad|bastones|hamburg|congel|medallon|patita|crocant|al vapor", None),
    "Galletitas de agua envasadas": ("g", 250, [r"galletit", r"\bagua\b"], r"salvado|integral|dulce|chocolat|rellen|semilla|queso|arroz", (150, 400)),
    "Galletitas dulces envasadas sin relleno": ("g", 150, [r"galletit"], r"rellen|banad|oblea|alfajor|\bagua\b|salvado|cracker|integral|sandwich|queso|crema|chip|semilla|arroz|chocolate y|bano|tapita", (100, 300)),
    "Gaseosa base cola": ("ml", 1500, [r"\bcola\b|coca|pepsi"], r"zero|light|sin azucar|lata|cero|diet|cereza|vainilla|cafe|lima|0%|max", (1200, 1800)),
    "Hamburguesas congeladas": ("u", 4, [r"hamburg"], r"pollo|vegetal|soja|pescado|\bpan\b|cerdo|lenteja|garbanzo|quinoa|calabaza|espinaca|arveja|condimento|salsa", None),
    "Harina de trigo común 000": ("g", 1000, [r"harina"], r"0000|leudante|integral|maiz|arroz|garbanzo|mandioca|prepar|pizza|bizcoch|almendra|coco|centeno|avena|soja|sin tacc|reposter|leud", (800, 1200)),
    "Huevos de gallina": ("u", 12, [r"huevo"], r"pascua|chocolat|codorniz|kinder|liquid|deshidrat|polvo|pasta|fideo|sorpresa|mayonesa|tallarin", None),
    "Jabón de tocador": ("g", 125, [r"jabon"], r"liquid|polvo|ropa|en pan|lavar|platos|intimo|lavavaj|matic|lavarrop|pan blanco|repuesto|perro|mascota|gel", (70, 200)),
    "Jabón en pan": ("g", 200, [r"jabon", r"\bpan\b"], r"tocador|liquid|polvo", (150, 300)),
    "Jabón en polvo para ropa": ("g", 800, [r"jabon|detergente", r"polvo"], r"liquid|tocador|lavavaj|platos|bebe", (400, 3000)),
    "Jamón cocido": ("g", 1000, [r"jamon cocido|jamon cocid"], r"crudo|sandwich|pizza|tarta|empanad|fosforito|salchich|medallon|pollo", None),
    "Lavandina": ("ml", 1000, [r"lavandina"], r"gel|pastilla|tabletas|aditiv|ropa color", (900, 2100)),
    "Leche en polvo entera": ("g", 800, [r"leche", r"polvo", r"entera"], r"descrem|deslact|infantil|matern|crecim|chocolat|cacao|fortific|reducid", (300, 1100)),
    "Leche fresca entera en sachet": ("ml", 1000, [r"\bleche\b", r"\bentera\b"], r"descrem|polvo|chocolat|larga vida|uat|caja|botella|deslact|condens|cultiv|cacao|vainill|frutill|vegetal|almendra|avena|coco|soja|infantil|crecim|sin lactosa|reducid|zero|0%|proteina|fermentada|dulce", (900, 1100)),
    "Lechuga": ("g", 1000, [r"lechuga"], r"mix|ensalada|hidropon|baby|bolsa|lista|lavada", None),
    "Limón": ("g", 1000, [r"\blimon\b"], r"jugo|galletit|\bte\b|helado|gaseosa|agua|caramelo|limpiad|lavav|jabon|desodor|torta|sabor|mermelada|pimienta|ralladura|esencia|aroma|detergente|lavandina|bebida", None),
    "Manteca": ("g", 200, [r"manteca"], r"cacao|mani|untable|galletit|alfajor|masa|tapa|hojaldre|caramel|dulce", (100, 500)),
    "Manzana deliciosa": ("g", 1000, [r"manzana"], r"jugo|pure|sidra|chips|deshidrat|\bte\b|galletit|vinagre|gaseosa|agua|mermelada|dulce|torta|compota|verde|granny|pink|gala|aroma|sabor|shampoo|jabon|desodor|caramel|helado|yogur|barra|cereal", None),
    "Nalga": ("g", 1000, [r"\bnalga\b"], r"milanesa|hamburg|picad|empanad", None),
    "Naranja": ("g", 1000, [r"\bnaranja"], r"jugo|gaseosa|agua|mermelada|galletit|helado|\bte\b|sabor|esencia|aroma|torta|caramel|bebida|shampoo|jabon|desodor|licor|cereal|yogur|dulce", None),
    "Paleta": ("g", 1000, [r"\bpaleta\b"], r"cerdo|cocida|sandwich|fiambre|jamon|helado|pintura|pintor|plastic|ping|tenis|padel|cocina", None),
    "Pan de mesa": ("g", 390, [r"\bpan\b", r"mesa|lactal|molde"], r"integral|salvado|hamburg|pancho|rallado|dulce|semilla|centeno|sin tacc|multicereal|light|queso|chip|keto|avena|hot dog", (250, 700)),
    "Pan francés tipo flauta": ("g", 1000, [r"pan frances|flauta|baguette|\bpan x kg"], r"congel|precocid|ajo|queso|semilla|integral|rallado", None),
    "Papa": ("g", 1000, [r"\bpapas?\b"], r"frita|pure|chips|congel|bastones|snack|noisette|crema|baston|carita|rejilla|smile|duquesa|rosti|horno|hash|tortilla|leche de|panchitos|condiment", None),
    "Pañales descartables": ("u", 10, [r"panal"], r"adulto|toallit|crema|incontinen|bolsa|apositos", None),
    "Pollo entero": ("g", 1000, [r"pollo", r"entero"], r"trozad|pata|muslo|pechuga|suprema|hamburg|nugget|caldo|sopa|rotis|asado|condiment|alita|milanesa|medallon|arroz|empanad|tarta|pizza|alimento|perro|gato", None),
    "Polvo para flan": ("u", 1, [r"\bflan\b"], r"listo|postre lacteo|refrigerad|vasito|casero|dulce de leche y", None),
    "Queso cremoso": ("g", 1000, [r"cremoso"], r"untable|light|rallado|en barra|saborizad|dulce|helado|sandwich|pizza|fundido", None),
    "Queso pategrás": ("g", 1000, [r"pategras|pategrass"], r"rallado|sandwich|feteado en", None),
    "Queso sardo": ("g", 1000, [r"\bsardo\b"], r"rallado|en hebras|sandwich", None),
    "Sal fina": ("g", 500, [r"\bsal\b", r"\bfina\b"], r"gruesa|parrill|marina|rosada|sin sodio|baja en sodio|condiment|hierbas|ajo|apio|light|liviana|reducid|entrefina", (250, 1100)),
    "Salame": ("g", 1000, [r"salam"], r"pizza|sandwich|picada mix|palitos|con queso|empanad|tarta", None),
    "Salchicha tipo viena": ("u", 6, [r"salchich"], r"parrill|alema|pollo|vegetal|soja|cocktail|copetin|bolsa x|frank|pan|congel|salchichon", None),
    "Salchichón": ("g", 1000, [r"salchichon"], r"sandwich|picada mix", None),
    "Tomate entero en conserva": ("u", 1, [r"tomate", r"entero|perita|pelado"], r"triturad|pure|salsa|extracto|deshidrat|cherry|seco|fresco|x kg|redondo|perita x|tomate perita$", None),
    "Tomate redondo": ("g", 1000, [r"tomate", r"redondo"], r"cherry|conserva|lata|salsa|pure|perita|triturad|extracto|seco", None),
    "Vino común": ("ml", 1000, [r"\bvino\b"], r"espumant|champa|vinagre|cocina|sangria|malbec|cabernet|reserva|varietal|roble|syrah|merlot|chardonnay|torrontes|bonarda|blend|rose|lata|dulce natural|fino|gran", (900, 1150)),
    "Yerba mate": ("g", 500, [r"yerba"], r"compuest|saboriz|hierbas|mate cocido|\bte\b|bebida|cocido|cascarilla|organica|barbacua|sin palo|suave|limon|naranja|menta|boldo|digestiv|manzanilla", (400, 1100)),
    "Yogur firme": ("u", 1, [r"yogur"], r"bebible|liquido|con cereal|griego|batido|con frutas|colchon|gelatina|probiotic|entero bebible|sachet|1 l|1lt|900|1000", None),
    "Zapallo anco": ("g", 1000, [r"zapallo|calabaza|anco"], r"pure|congel|dulce|sopa|zapallito|cabutia|kabutia|plomo|zuchini|zucchini|en almibar", None),
}
# Productos que el INDEC releva "por kg" sueltos (carnes, fiambres al corte, frutas y verduras): se aceptan SOLO los que
# SEPA vende por kilo. Frutas y verduras, además, solo si la descripción EMPIEZA con el nombre (evita "yerba sabor naranja").
SUELTOS = {"Asado", "Cuadril", "Nalga", "Paleta", "Carne picada común", "Pollo entero", "Filet de merluza fresco",
           "Jamón cocido", "Salame", "Salchichón", "Queso cremoso", "Queso pategrás", "Queso sardo", "Pan francés tipo flauta"}
VERDULERIA = {"Banana": r"^banana", "Batata": r"^batata", "Cebolla": r"^cebolla", "Lechuga": r"^lechuga", "Limón": r"^limon",
              "Manzana deliciosa": r"^manzana", "Naranja": r"^naranja", "Papa": r"^papas?\b", "Tomate redondo": r"^tomate",
              "Zapallo anco": r"^(zapallo|calabaza)"}
REGLAS["Zapallo anco"] = ("g", 1000, [r"\bzapallo\b|\bcalabaza\b|\banco\b"], REGLAS["Zapallo anco"][3], None)
REGLAS["Carne picada común"] = ("g", 1000, [r"carne picada|picada comun|picada vacuna|picada de carne"],
                                REGLAS["Carne picada común"][3] + r"|surtida|picadillo|salamin|salame", None)
UNIDADES = {"kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
            "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "grm": ("g", 1), "gramos": ("g", 1), "grs.": ("g", 1),
            "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000), "ltr": ("ml", 1000), "litro": ("ml", 1000), "litros": ("ml", 1000),
            "ml": ("ml", 1), "cc": ("ml", 1), "cm3": ("ml", 1), "mls": ("ml", 1),
            "u": ("u", 1), "un": ("u", 1), "unid": ("u", 1), "unidad": ("u", 1), "unidades": ("u", 1), "uni": ("u", 1)}


def normal(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def tamano(desc, cantidad, unidad):
    """(unidad base, cantidad) leyendo primero la descripción y después los campos. None si no se puede."""
    d = normal(desc)
    m = re.findall(r"(\d+(?:[.,]\d+)?)\s*(kgs?|kilos?|grs?\.?|grm|gramos|g|lts?|ltr|litros?|l|ml|cc|cm3)\b", d)
    if m:
        v, u = m[-1]
        u = u.rstrip(".")
        if u in UNIDADES:
            base, f = UNIDADES[u]
            return base, float(v.replace(",", ".")) * f
    m = re.search(r"\bx\s*(\d+)\s*(u|un|unid|unidades)?\b|\b(\d+)\s*(u|un|unid|unidades)\b", d)
    unidad = normal(unidad).rstrip(".") if pd.notna(unidad) else ""
    if unidad in UNIDADES and pd.notna(cantidad) and cantidad > 0:
        base, f = UNIDADES[unidad]
        if not (base == "ml" and cantidad * f < 50 and "1l" in d.replace(" ", "")):   # Cencosud: "1 ml." que es 1 L
            if base != "u" or not m:
                return base, float(cantidad) * f
    if m:
        n = m.group(1) or m.group(3)
        return "u", float(n)
    if "docena" in d:
        return "u", 12.0
    return None


def por_kilo(desc, cantidad, unidad):
    """¿Se vende suelto por kilo? (unidad kg con cantidad ~1, o 'x kg' / 'por kg' en la descripción)."""
    d = normal(desc)
    u = normal(unidad).rstrip(".") if pd.notna(unidad) else ""
    if u in ("kg", "kgm", "kgs", "kilo") and pd.notna(cantidad) and 0.9 <= float(cantidad) <= 1.1:
        return True
    return bool(re.search(r"\bx ?kg\b|por kg|\bkg\.?\s*$|x kilo|\bxkg\b|\(kg\)", d))


def asignar(productos):
    """productos: DataFrame único por producto SEPA (clave = cadena + producto). Devuelve filas asignadas a un producto INDEC."""
    filas = []
    for _, p in productos.iterrows():
        d = normal(p["descripcion"])
        for nombre, (ubase, cref, oblig, excl, rango) in REGLAS.items():
            if all(re.search(o, d) for o in oblig) and not re.search(excl, d):
                if nombre in VERDULERIA and not re.search(VERDULERIA[nombre], d):
                    continue
                if nombre in SUELTOS or nombre in VERDULERIA:
                    if not por_kilo(p["descripcion"], p["cantidad"], p["unidad"]):
                        continue
                    filas.append({"clave": p["clave"], "producto_indec": nombre, "factor": 1.0, "tamano": 1000.0, "unidad_base": "g"})
                    break
                t = tamano(p["descripcion"], p["cantidad"], p["unidad"])
                por_kg = rango is None and ubase == "g"
                if por_kg:
                    if t is None or t[0] != "g":
                        t = ("g", 1000.0) if re.search(r"\bx ?kg|por kg|\bkg\b|kilo", d) or (p["unidad"] and normal(p["unidad"]).startswith("kg")) else None
                    if t is None:
                        continue
                elif ubase == "u" and nombre in ("Arvejas secas remojadas", "Tomate entero en conserva", "Polvo para flan", "Yogur firme"):
                    t = ("u", 1.0)
                if t is None or t[0] != ubase or t[1] <= 0:
                    continue
                if rango and not (rango[0] <= t[1] <= rango[1]):
                    continue
                filas.append({"clave": p["clave"], "producto_indec": nombre, "factor": cref / t[1], "tamano": t[1], "unidad_base": ubase})
                break
    return pd.DataFrame(filas)


def main():
    archivos = sorted(RESUMEN.glob("sepa_*.parquet"))
    if not archivos:
        sys.exit("No hay resúmenes de SEPA en E:\\SEPA\\resumen (¿disco conectado?)")
    cols = ["fecha", "id_comercio", "id_bandera", "bandera", "region", "producto", "descripcion", "cantidad", "unidad",
            "n_sucursales", "precio_lista"]
    df = pd.concat([pd.read_parquet(a, columns=cols) for a in archivos], ignore_index=True)
    df["clave"] = df["id_comercio"].astype(str) + "|" + df["producto"].astype(str)
    unicos = df.drop_duplicates("clave")[["clave", "descripcion", "cantidad", "unidad"]]
    print(f"{len(archivos)} días, {len(df):,} filas, {len(unicos):,} productos distintos por cadena")
    mapa = asignar(unicos)
    mapa = mapa.merge(unicos, on="clave")
    mapa.to_csv(MAPEO, index=False, encoding="utf-8-sig")
    print(f"productos SEPA asignados: {len(mapa):,} a {mapa['producto_indec'].nunique()} de {len(REGLAS)} productos INDEC")

    x = df.merge(mapa[["clave", "producto_indec", "factor"]], on="clave")
    x["precio_ref"] = x["precio_lista"] * x["factor"]
    # descartar valores absurdos dentro de cada producto-región-día
    med = x.groupby(["fecha", "region", "producto_indec"])["precio_ref"].transform("median")
    x = x[(x["precio_ref"] > med / 3) & (x["precio_ref"] < med * 3)]
    g = x.groupby(["fecha", "region", "producto_indec"])
    diaria = g.agg(precio=("precio_ref", "median"), p25=("precio_ref", lambda s: s.quantile(0.25)),
                   p75=("precio_ref", lambda s: s.quantile(0.75)), n_productos=("clave", "nunique"),
                   n_cadenas=("id_comercio", "nunique"), n_sucursales=("n_sucursales", "sum")).reset_index()
    diaria.to_parquet(SALIDA, index=False)

    # control contra el INDEC (GBA, último mes publicado)
    ind = pd.read_parquet(CARPETA / "precios_promedio_indec.parquet")
    ind = ind[(ind["hoja"] == "GBA")]
    ult = ind["mes"].max()
    ind = ind[ind["mes"] == ult].set_index("producto")["precio"]
    gba = diaria[diaria["region"] == "GBA"].groupby("producto_indec").agg(precio=("precio", "median"), n=("n_productos", "max"),
                                                                           cadenas=("n_cadenas", "max"))
    c = gba.join(ind.rename("indec"), how="outer")
    c["cociente"] = c["precio"] / c["indec"]
    print(f"\nGBA: SEPA ({diaria['fecha'].min()} a {diaria['fecha'].max()}, precio de lista) vs INDEC ({ult})")
    print(f"{'producto':42} {'SEPA':>10} {'INDEC':>10} {'SEPA/INDEC':>10} {'productos':>9} {'cadenas':>7}")
    for p, r in c.sort_values("cociente").iterrows():
        cs = "-" if pd.isna(r["cociente"]) else f"{r['cociente']:.2f}"
        sp = "-" if pd.isna(r["precio"]) else f"{r['precio']:,.0f}"
        n = "-" if pd.isna(r["n"]) else f"{int(r['n'])}"
        cad = "-" if pd.isna(r["cadenas"]) else f"{int(r['cadenas'])}"
        print(f"{p[:42]:42} {sp:>10} {r['indec']:>10,.0f} {cs:>10} {n:>9} {cad:>7}")
    ok = c["cociente"].between(0.8, 1.25)
    print(f"\nCon cociente entre 0,80 y 1,25: {int(ok.sum())} de {int(c['cociente'].notna().sum())} con dato; "
          f"sin dato en SEPA: {int(c['precio'].isna().sum())}. Mediana del cociente: {c['cociente'].median():.2f}")


if __name__ == "__main__":
    main()
