# indice_sepa.py
# Índice de precios de alta frecuencia con SEPA (supermercados), para la canasta de 59 variedades del INDEC.
# Incremental: cada día procesa solo los días nuevos de E:\SEPA\resumen.
#   1) asignación producto SEPA -> variedad INDEC: se guarda en datos/sepa_canasta_mapeo.csv; solo se clasifican productos
#      nuevos (reglas de canasta_sepa.py)
#   2) precio efectivo de cada producto x cadena x bandera x región x día, con la regla del INDEC para promociones:
#      se usa el precio de promoción solo si la promoción es general (cualquier medio de pago, sin cantidad mínima,
#      sin banco/tarjeta/socios/jubilados) y está en la mayoría de las sucursales; si no, el precio de lista.
#      -> E:\SEPA\canasta\items_<día>.parquet
#   3) índice: cada producto se compara consigo mismo (misma cadena, bandera y región); promedio geométrico (Jevons) por
#      variedad; variedades -> rubro del IPC con igual peso dentro del rubro; rubros -> total con los pesos oficiales del
#      IPC de la región; nacional = regiones con su peso oficial.
#      Diario: base = primer día con datos (hasta tener dos meses completos). Mensual: promedio del mes contra el anterior.
# Salidas: datos/sepa_indice_diario.parquet, datos/sepa_indice_mensual.parquet, datos/informe_indice_sepa.txt
# Uso: py -3.13 indice_sepa.py
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import canasta_sepa as C

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
RESUMEN = Path("E:/SEPA/resumen")
ITEMS = Path("E:/SEPA/canasta")
MAPEO = CARPETA / "sepa_canasta_mapeo.csv"
DIARIO = CARPETA / "sepa_indice_diario.parquet"
MENSUAL = CARPETA / "sepa_indice_mensual.parquet"
INFORME = CARPETA / "informe_indice_sepa.txt"
REGIONES = ["GBA", "Pampeana", "Noreste", "Noroeste", "Cuyo", "Patagonia"]
MIN_DIAS_MES = 15
MIN_DIAS_25 = 12       # días con datos del 1 al 25 para calcular la variación "hasta el 25"
DIAS_DIARIO = 90      # el índice diario se arma con los últimos 90 días (base = primer día de la ventana)

# promociones que NO cuentan para el IPC (descuentos para grupos o con cantidad mínima)
PROMO_EXCLUIDA = (r"tarjeta|banco|bancari|visa|master|amex|american|cabal|mercado ?pago|\bmodo\b|cuenta dni|\bbna\b|galicia|"
                  r"santander|bbva|frances|macro|hsbc|icbc|supervielle|credicoop|comafi|naranja x|club|socio|jubilad|pensionad|"
                  r"\bapp\b|online|cuotas|reintegro|\d\s*x\s*\d|2da|segunda|llevando|lleva\b|\bx ?[2-9]\b|unidades|combo|"
                  r"beneficio|cupon|clientes|membres|cencopay|mi carrefour|comunidad")

# variedad INDEC -> clase/grupo del IPC (código de ponderadores_ipc.xls)
CLASE = {
    "Pan francés tipo flauta": "01.1.1", "Pan de mesa": "01.1.1", "Galletitas de agua envasadas": "01.1.1",
    "Galletitas dulces envasadas sin relleno": "01.1.1", "Harina de trigo común 000": "01.1.1", "Arroz blanco simple": "01.1.1",
    "Fideos secos tipo guisero": "01.1.1",
    "Asado": "01.1.2", "Carne picada común": "01.1.2", "Cuadril": "01.1.2", "Nalga": "01.1.2", "Paleta": "01.1.2",
    "Pollo entero": "01.1.2", "Hamburguesas congeladas": "01.1.2", "Jamón cocido": "01.1.2", "Salame": "01.1.2",
    "Salchichón": "01.1.2", "Salchicha tipo viena": "01.1.2",
    "Filet de merluza fresco": "01.1.3",
    "Leche fresca entera en sachet": "01.1.4", "Leche en polvo entera": "01.1.4", "Yogur firme": "01.1.4",
    "Queso cremoso": "01.1.4", "Queso pategrás": "01.1.4", "Queso sardo": "01.1.4", "Dulce de leche": "01.1.4",
    "Huevos de gallina": "01.1.4",
    "Aceite de girasol": "01.1.5", "Manteca": "01.1.5",
    "Banana": "01.1.6", "Limón": "01.1.6", "Manzana deliciosa": "01.1.6", "Naranja": "01.1.6",
    "Batata": "01.1.7", "Cebolla": "01.1.7", "Lechuga": "01.1.7", "Papa": "01.1.7", "Tomate redondo": "01.1.7",
    "Zapallo anco": "01.1.7", "Arvejas secas remojadas": "01.1.7", "Tomate entero en conserva": "01.1.7",
    "Azúcar": "01.1.8", "Polvo para flan": "01.1.8", "Sal fina": "01.1.9",
    "Café molido": "01.2.1", "Yerba mate": "01.2.1", "Agua sin gas": "01.2.2", "Gaseosa base cola": "01.2.2",
    "Vino común": "02.1.2", "Cerveza en botella": "02.1.3",
    "Detergente líquido": "05.6.1", "Lavandina": "05.6.1", "Jabón en polvo para ropa": "05.6.1", "Jabón en pan": "05.6.1",
    "Champú": "12.1.3", "Desodorante": "12.1.3", "Jabón de tocador": "12.1.3", "Algodón": "12.1.3", "Pañales descartables": "12.1.3",
}


def promo_general(leyenda):
    if leyenda is None or (isinstance(leyenda, float) and np.isnan(leyenda)):
        return False
    return not re.search(PROMO_EXCLUIDA, C.normal(leyenda))


def actualizar_mapeo(dias):
    """Agrega al mapeo los productos nuevos de estos días (no reclasifica los ya vistos)."""
    mapa = pd.read_csv(MAPEO, encoding="utf-8-sig", dtype={"clave": str}) if MAPEO.exists() else pd.DataFrame(columns=["clave"])
    vistos_archivo = CARPETA / "sepa_canasta_vistos.txt"     # claves ya revisadas (asignadas o no)
    vistos = set(vistos_archivo.read_text(encoding="utf-8").split("\n")) if vistos_archivo.exists() else set(mapa["clave"])
    nuevos = []
    for d in dias:
        x = pd.read_parquet(RESUMEN / f"sepa_{d}.parquet", columns=["id_comercio", "producto", "descripcion", "cantidad", "unidad"])
        x["clave"] = x["id_comercio"].astype(str) + "|" + x["producto"].astype(str)
        x = x[x["clave"].map(lambda c: isinstance(c, str))].drop_duplicates("clave")   # productos sin código: fuera
        nuevos.append(x[~x["clave"].isin(vistos)])
        vistos |= set(x["clave"])
    nuevos = pd.concat(nuevos).drop_duplicates("clave") if nuevos else pd.DataFrame()
    agregados = 0
    if len(nuevos):
        asignados = C.asignar(nuevos)
        if len(asignados):
            asignados = asignados.merge(nuevos[["clave", "descripcion", "cantidad", "unidad"]], on="clave")
            mapa = pd.concat([mapa, asignados], ignore_index=True).drop_duplicates("clave")
            agregados = len(asignados)
        mapa.to_csv(MAPEO, index=False, encoding="utf-8-sig")
    vistos_archivo.write_text("\n".join(sorted(v for v in vistos if isinstance(v, str))), encoding="utf-8")
    return mapa, len(nuevos), agregados


def procesar_dia(d, mapa):
    cols = ["fecha", "id_comercio", "id_bandera", "region", "producto", "n_sucursales", "precio_lista", "precio_promo1",
            "pct_promo1", "leyenda_promo1"]
    x = pd.read_parquet(RESUMEN / f"sepa_{d}.parquet", columns=cols)
    x["clave"] = x["id_comercio"].astype(str) + "|" + x["producto"].astype(str)
    x = x.merge(mapa[["clave", "producto_indec", "factor"]], on="clave")
    usa_promo = (x["pct_promo1"] >= 0.5) & (x["precio_promo1"] > 0) & (x["precio_promo1"] < x["precio_lista"]) \
        & x["leyenda_promo1"].map(promo_general)
    x["precio_efectivo"] = np.where(usa_promo, x["precio_promo1"], x["precio_lista"]) * x["factor"]
    x["con_promo"] = usa_promo
    x = x[x["region"].isin(REGIONES)]
    x["item"] = x["clave"] + "|" + x["id_bandera"].astype(str) + "|" + x["region"]
    return x[["fecha", "region", "producto_indec", "id_comercio", "item", "n_sucursales", "precio_efectivo", "con_promo"]]


def jevons(rel):
    """Promedio geométrico de relativos, descartando relativos absurdos (fuera de 1/3 a 3)."""
    rel = rel[(rel > 1 / 3) & (rel < 3)]
    return float(np.exp(np.log(rel).mean())) if len(rel) else np.nan


def agregar(var_variedad, region, pond):
    """var_variedad: Series variedad -> índice (base 100). Devuelve dict con índice por clase y total de la región."""
    df = pd.DataFrame({"indice": var_variedad})
    df["clase"] = df.index.map(CLASE)
    clases = df.groupby("clase")["indice"].apply(lambda s: float(np.exp(np.log(s).mean())))   # igual peso dentro de la clase
    w = pond.set_index("codigo")[region].reindex(clases.index)
    total = float((clases * w).sum() / w.sum())
    return clases, total, float(w.sum())


def main():
    if not RESUMEN.exists():
        sys.exit("No está E:\\SEPA\\resumen (¿disco conectado?)")
    ITEMS.mkdir(parents=True, exist_ok=True)
    dias = sorted(p.stem.replace("sepa_", "") for p in RESUMEN.glob("sepa_*.parquet"))
    hechos = {p.stem.replace("items_", "") for p in ITEMS.glob("items_*.parquet")}
    pendientes = [d for d in dias if d not in hechos]
    mapa, n_nuevos, n_asignados = actualizar_mapeo(pendientes)
    for d in pendientes:
        procesar_dia(d, mapa).to_parquet(ITEMS / f"items_{d}.parquet", index=False)
    import duckdb
    con = duckdb.connect()
    con.execute("set memory_limit='4GB'")
    glob = (ITEMS / "items_*.parquet").as_posix()
    # para el índice diario alcanza con los últimos DIAS_DIARIO días (con la historia completa serían decenas de millones de filas)
    ult_dia = con.execute(f"select max(fecha) from read_parquet('{glob}')").fetchone()[0]
    it = con.execute(f"select * from read_parquet('{glob}') where fecha > ?::date - {DIAS_DIARIO}", [ult_dia]).df()
    it["fecha"] = pd.to_datetime(it["fecha"])
    pond = pd.read_csv(CARPETA / "ponderaciones_ipc.csv", encoding="utf-8-sig", dtype={"codigo": str})
    peso_region = {"GBA": 0.4467, "Pampeana": 0.3419, "Noreste": 0.0451, "Noroeste": 0.0688, "Cuyo": 0.0518, "Patagonia": 0.0457}

    # ---- diario (últimos DIAS_DIARIO días): base = primer día de la ventana ----
    base_dia = it["fecha"].min()
    base = it[it["fecha"] == base_dia].set_index("item")["precio_efectivo"]
    filas, cobertura = [], {}
    for f, g in it.groupby("fecha"):
        g = g.set_index("item")
        totales = {}
        for reg in REGIONES:
            gr = g[g["region"] == reg]
            comunes = gr.index.intersection(base.index)
            if len(comunes) == 0:
                continue
            rel = gr.loc[comunes, "precio_efectivo"] / base.loc[comunes]
            por_var = rel.groupby(gr.loc[comunes, "producto_indec"]).apply(jevons).dropna() * 100
            if por_var.empty:
                continue
            clases, total, cob = agregar(por_var, reg, pond)
            cobertura[reg] = cob
            totales[reg] = total
            filas += [{"fecha": f.date(), "region": reg, "nivel": "variedad", "nombre": v, "indice": i} for v, i in por_var.items()]
            filas += [{"fecha": f.date(), "region": reg, "nivel": "clase", "nombre": c, "indice": i} for c, i in clases.items()]
            filas.append({"fecha": f.date(), "region": reg, "nivel": "total", "nombre": "total", "indice": total})
        if totales:
            w = np.array([peso_region[r] for r in totales])
            filas.append({"fecha": f.date(), "region": "Nacional", "nivel": "total", "nombre": "total",
                          "indice": float(np.dot(w, list(totales.values())) / w.sum())})
    diario = pd.DataFrame(filas)
    diario.to_parquet(DIARIO, index=False)

    # ---- mensual: promedio geométrico del mes (por ítem) contra el MES ANTERIOR (consecutivo), solo meses con al menos
    #      MIN_DIAS_MES días. Un mes sin el anterior completo queda sin variación (no se compara contra meses más viejos).
    dias_mes = con.execute(f"select strftime(fecha, '%Y-%m') as mes, count(distinct fecha) as n from read_parquet('{glob}') "
                           "group by 1 order by 1").df().set_index("mes")["n"]
    dias_mes.index = pd.PeriodIndex(dias_mes.index, freq="M")
    completos = [m for m, n in dias_mes.items() if n >= MIN_DIAS_MES]
    # precio promedio del mes completo y del 1 al 25 (lo que se conoce al cerrar el REM, que releva los últimos días hábiles)
    prom = con.execute(f"""select strftime(fecha, '%Y-%m') as mes, item, any_value(region) as region,
                                  any_value(producto_indec) as producto_indec, exp(avg(ln(precio_efectivo))) as precio,
                                  exp(avg(case when day(fecha) <= 25 then ln(precio_efectivo) end)) as precio25
                           from read_parquet('{glob}') where precio_efectivo > 0 group by 1, 2""").df()
    dias25 = con.execute(f"select strftime(fecha, '%Y-%m') as mes, count(distinct fecha) as n from read_parquet('{glob}') "
                         "where day(fecha) <= 25 group by 1").df().set_index("mes")["n"]
    dias25.index = pd.PeriodIndex(dias25.index, freq="M")
    # MES ANTERIOR INCOMPLETO (30-oct-2026, Marcos, opción A): si al mes anterior le faltan más de 3 días, los dos meses se
    # comparan en los MISMOS días del mes. Caso: sep-2026 solo tiene del 1 al 22 (el portal guarda 7 días y nadie archivó
    # del 23 al 30); sin esto, octubre se comparaba contra un septiembre sin su última semana (más barato): termómetro
    # inflado ~0,15-0,2 pp. Los meses a los que les faltan 1 a 3 días sueltos no cambian.
    dias_de = con.execute(f"select distinct strftime(fecha, '%Y-%m') as mes, day(fecha) as d from read_parquet('{glob}')").df()
    dias_de["mes"] = pd.PeriodIndex(dias_de["mes"], freq="M")
    mismos = {}
    for ma, n in dias_mes.items():
        if n >= ma.days_in_month - 3 or (ma + 1) not in dias_mes.index:
            continue
        comun = sorted(set(dias_de.loc[dias_de["mes"] == ma, "d"]) & set(dias_de.loc[dias_de["mes"] == ma + 1, "d"]))
        if len(comun) < MIN_DIAS_25:
            continue
        q = con.execute(f"""select strftime(fecha, '%Y-%m') as mes, item, exp(avg(ln(precio_efectivo))) as precio,
                                   exp(avg(case when day(fecha) <= 25 then ln(precio_efectivo) end)) as precio25
                            from read_parquet('{glob}') where precio_efectivo > 0
                              and strftime(fecha, '%Y-%m') in ('{ma}', '{ma + 1}') and day(fecha) in ({','.join(map(str, comun))})
                            group by 1, 2""").df()
        q["mes"] = pd.PeriodIndex(q["mes"], freq="M")
        mismos[ma + 1] = (q, comun)
    con.close()
    prom["mes"] = pd.PeriodIndex(prom["mes"], freq="M")
    mensual, clases_mes = [], []
    # meses para la variación hasta el 25: el mes anterior completo y al menos MIN_DIAS_25 días del 1 al 25 en el mes
    meses_25 = [m for m in dias25.index if dias25[m] >= MIN_DIAS_25 and (m - 1) in completos]
    for m1 in sorted(set(completos) | set(meses_25)):
        m0 = m1 - 1
        if m0 not in completos:
            continue
        a = prom[prom["mes"] == m0].set_index("item")["precio"]
        b = prom[prom["mes"] == m1].set_index("item")
        if m1 in mismos:                                   # mes anterior incompleto: mismos días en los dos meses
            q, comun = mismos[m1]
            a = q[q["mes"] == m0].set_index("item")["precio"]
            b = b[["region", "producto_indec"]].join(q[q["mes"] == m1].set_index("item")[["precio", "precio25"]], how="inner")
        b = b[b.index.isin(a.index)]
        b["rel"] = b["precio"] / a.reindex(b.index)
        b["rel25"] = b["precio25"] / a.reindex(b.index)
        for reg in REGIONES:
            br = b[b["region"] == reg]
            if br.empty:
                continue
            fila = {"mes": str(m1), "region": reg, "variacion_pct": np.nan, "variacion_25_pct": np.nan, "n_items": len(br),
                    "mismos_dias": (f"{mismos[m1][1][0]}-{mismos[m1][1][-1]}" if m1 in mismos else "")}
            if m1 in completos:
                por_var = br.groupby("producto_indec")["rel"].apply(jevons).dropna() * 100
                clases, total, _ = agregar(por_var, reg, pond)
                fila["variacion_pct"] = total - 100
                clases_mes += [{"mes": str(m1), "region": reg, "clase": c, "variacion_pct": v - 100} for c, v in clases.items()]
            if m1 in meses_25:
                pv25 = br.dropna(subset=["rel25"]).groupby("producto_indec")["rel25"].apply(jevons).dropna() * 100
                if not pv25.empty:
                    fila["variacion_25_pct"] = agregar(pv25, reg, pond)[1] - 100
            mensual.append(fila)
    mensual_df = pd.DataFrame(mensual)
    if len(mensual_df):   # nacional = regiones con su peso oficial
        prom_w = lambda q, c: float(np.dot(q["w"], q[c]) / q["w"].sum()) if q[c].notna().all() else np.nan
        nac = mensual_df.assign(w=mensual_df["region"].map(peso_region)).groupby("mes").apply(
            lambda q: pd.Series({"variacion_pct": prom_w(q, "variacion_pct"), "variacion_25_pct": prom_w(q, "variacion_25_pct"),
                                 "n_items": int(q["n_items"].sum())}))
        mensual_df = pd.concat([mensual_df, nac.reset_index().assign(region="Nacional")], ignore_index=True)
    mensual_df.to_parquet(MENSUAL, index=False)
    pd.DataFrame(clases_mes).to_parquet(CARPETA / "sepa_indice_mensual_clases.parquet", index=False)
    mensual = mensual_df.to_dict("records")

    # ---- informe ----
    ult = diario["fecha"].max()
    L = [f"=== ÍNDICE SEPA (canasta INDEC, alta frecuencia) | días {base_dia.date()} a {ult} ===",
         f"Productos nuevos clasificados en esta corrida: {n_nuevos:,} ({n_asignados:,} asignados a la canasta). "
         f"Días procesados ahora: {len(pendientes)}.",
         f"Ítems (producto x cadena x bandera x región) en el último día: {it[it['fecha'] == it['fecha'].max()]['item'].nunique():,}; "
         f"con promoción general aplicada: {it[it['fecha'] == it['fecha'].max()]['con_promo'].mean():.1%}",
         f"Cobertura de la canasta dentro del IPC de GBA: {cobertura.get('GBA', np.nan) * 100:.1f}% del gasto", ""]
    tot = diario[diario["nivel"] == "total"].pivot_table(index="fecha", columns="region", values="indice")
    L.append(f"Índice diario (base {base_dia.date()} = 100), total de la canasta:")
    L.append(tot[["Nacional"] + [r for r in REGIONES if r in tot.columns]].round(2).to_string())
    L.append("")
    gba = diario[(diario["region"] == "GBA") & (diario["nivel"] == "variedad") & (diario["fecha"] == ult)].sort_values("indice")
    L.append(f"GBA, variedades al {ult} (base {base_dia.date()} = 100): las que más bajaron y más subieron")
    for _, r in pd.concat([gba.head(5), gba.tail(5)]).iterrows():
        L.append(f"   {r['nombre']:42} {r['indice']:7.2f}")
    if mensual:
        L += ["", "Variación mensual (promedio del mes contra el anterior):"]
        L.append(pd.DataFrame(mensual).pivot_table(index="mes", columns="region", values="variacion_pct").round(2).to_string())
    else:
        L += ["", f"Variación mensual: todavía no hay dos meses con al menos {MIN_DIAS_MES} días (hay {dict(dias_mes.astype(int))})."]
    texto = "\n".join(L)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
