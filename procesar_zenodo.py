# procesar_zenodo.py
# Termómetro del súper 2016-2018 con la historia SEPA de Zenodo (E:\SEPA\historico_zenodo, DOI 10.5281/zenodo.6568295).
# Esos archivos solo traen códigos (comercio, bandera, sucursal, código de barras, precio de lista y de promociones), sin
# descripción, sin ubicación ni leyendas de promoción, y solo ~5.100 productos. Los productos se reconocen por código de
# barras (EAN) con el mapeo de la canasta actual (datos\sepa_canasta_mapeo.csv). Se usa el PRECIO DE LISTA (no se puede
# aplicar la regla de promociones sin leyendas). Índice NACIONAL (no hay regiones).
# Método igual a indice_sepa.py: precio mediano por producto x cadena x bandera y día; promedio geométrico del mes (y del
# 1 al 25) por ítem; relativo contra el mes anterior completo; Jevons por variedad (relativos entre 1/3 y 3); igual peso
# dentro de la clase del IPC; clases con los pesos nacionales oficiales.
# Salidas: E:\SEPA\canasta_zenodo\items_<día>.parquet (incremental), datos\sepa_zenodo_mensual.parquet,
#          datos\informe_zenodo.txt (validación contra el IPC del INDEC y contra lo que no anticipó el REM)
# Uso: py -3.13 procesar_zenodo.py
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from indice_sepa import CLASE, jevons

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
C = BASE / "datos"
ORIGEN = Path("E:/SEPA/historico_zenodo")
ITEMS = Path("E:/SEPA/canasta_zenodo")
MIN_DIAS_MES, MIN_DIAS_25 = 15, 12


def mapa_ean():
    m = pd.read_csv(C / "sepa_canasta_mapeo.csv", encoding="utf-8-sig", dtype={"clave": str})
    m["ean"] = m["clave"].str.split("|").str[1]
    m = m[m["ean"].str.fullmatch(r"\d{8,14}", na=False)]
    m["ean"] = m["ean"].str.lstrip("0")
    # un EAN puede haber quedado asignado en varias cadenas: se usa la variedad más frecuente
    return m.groupby("ean")["producto_indec"].agg(lambda s: s.mode().iloc[0])


def procesar_dias(eans):
    import duckdb
    ITEMS.mkdir(parents=True, exist_ok=True)
    hechos = {p.stem.replace("items_", "") for p in ITEMS.glob("items_*.parquet")}
    archivos = sorted(ORIGEN.glob("precios_*.csv.gz"))
    pend = [(p, re.search(r"(\d{4}-\d{2}-\d{2})", p.name).group(1)) for p in archivos]
    pend = [(p, d) for p, d in pend if d not in hechos]
    con = duckdb.connect()
    con.execute("set memory_limit='4GB'")
    con.register("eans", pd.DataFrame({"ean": eans.index, "producto_indec": eans.values}))
    for i, (p, d) in enumerate(pend, 1):
        f = p.as_posix()
        df = con.execute(f"""
            with x as (select comercio_id, bandera_id, ltrim(trim(producto_sepa_id), '0') as ean,
                              try_cast(replace(precio_lista, ',', '.') as double) as precio
                       from read_csv('{f}', header=true, all_varchar=true, ignore_errors=true))
            select '{d}'::date as fecha, x.comercio_id || '|' || x.bandera_id || '|' || x.ean as item, e.producto_indec,
                   median(precio) as precio, count(*) as n_sucursales
            from x join eans e using (ean)
            where precio > 0 and precio < 1e7
            group by 1, 2, 3""").df()
        df.to_parquet(ITEMS / f"items_{d}.parquet", index=False)
        if i % 50 == 0 or i == len(pend):
            print(f"  [{i}/{len(pend)}] {d}: {len(df):,} ítems", flush=True)


def indice():
    import duckdb
    glob = (ITEMS / "items_*.parquet").as_posix()
    con = duckdb.connect()
    prom = con.execute(f"""select strftime(fecha, '%Y-%m') as mes, item, any_value(producto_indec) as producto_indec,
                                  exp(avg(ln(precio))) as precio,
                                  exp(avg(case when day(fecha) <= 25 then ln(precio) end)) as precio25
                           from read_parquet('{glob}') where precio > 0 group by 1, 2""").df()
    dias = con.execute(f"select strftime(fecha, '%Y-%m') as mes, count(distinct fecha) as n, "
                       f"count(distinct case when day(fecha) <= 25 then fecha end) as n25 from read_parquet('{glob}') group by 1").df()
    dias.index = pd.PeriodIndex(dias["mes"], freq="M")
    prom["mes"] = pd.PeriodIndex(prom["mes"], freq="M")
    pond = pd.read_csv(C / "ponderaciones_ipc.csv", encoding="utf-8-sig", dtype={"codigo": str}).set_index("codigo")
    completos = set(dias.index[dias["n"] >= MIN_DIAS_MES])
    filas, clases_f = [], []
    for m1 in sorted(dias.index):
        m0 = m1 - 1
        if m0 not in completos:
            continue
        a = prom[prom["mes"] == m0].set_index("item")["precio"]
        b = prom[prom["mes"] == m1].set_index("item")
        b = b[b.index.isin(a.index)].copy()
        b["rel"], b["rel25"] = b["precio"] / a.reindex(b.index), b["precio25"] / a.reindex(b.index)
        fila = {"mes": str(m1), "n_items": len(b), "variacion_pct": np.nan, "variacion_25_pct": np.nan}
        for col, cond, nombre in (("rel", m1 in completos, "variacion_pct"), ("rel25", dias.loc[m1, "n25"] >= MIN_DIAS_25, "variacion_25_pct")):
            if not cond:
                continue
            pv = b.dropna(subset=[col]).groupby("producto_indec")[col].apply(jevons).dropna() * 100
            df = pd.DataFrame({"indice": pv})
            df["clase"] = df.index.map(CLASE)
            cl = df.groupby("clase")["indice"].apply(lambda s: float(np.exp(np.log(s).mean())))
            w = pond["Nacional"].reindex(cl.index)
            fila[nombre] = float((cl * w).sum() / w.sum()) - 100
            if nombre == "variacion_pct":
                clases_f += [{"mes": str(m1), "clase": c, "variacion_pct": v - 100, "peso": pond.loc[c, "Nacional"]} for c, v in cl.items()]
        filas.append(fila)
    M = pd.DataFrame(filas)
    M.to_parquet(C / "sepa_zenodo_mensual.parquet", index=False)
    pd.DataFrame(clases_f).to_parquet(C / "sepa_zenodo_mensual_clases.parquet", index=False)
    return M, pd.DataFrame(clases_f), dias


def validar(M, K):
    hs = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    def var(sid):
        s = hs[hs["id"] == sid]
        s = pd.Series(s["valor"].values, index=pd.to_datetime(s["fecha"]).dt.to_period("M")).groupby(level=0).last().sort_index()
        return (s / s.shift(1) - 1) * 100
    ipc = pd.read_parquet(C / "ipc_indec.parquet")
    ipc.index = pd.to_datetime(ipc["mes"]).dt.to_period("M")
    general = ipc["ipc_general_indice_var_mensual"]
    M = M.copy()
    M.index = pd.PeriodIndex(M["mes"], freq="M")
    # INDEC equivalente nacional: no hay aperturas nacionales por clase; se usan las de GBA (la mayor parte del peso)
    equiv = {}
    for m, q in K.groupby("mes"):
        vals, ws = [], []
        for _, r in q.iterrows():
            v = var(f"indec_ipcap_gba_{r['clase']}")
            if v.empty:
                v = var(f"indec_ipcap_gba_{r['clase'][:4]}")
            x = v.get(pd.Period(m, "M"), np.nan)
            if not np.isnan(x):
                vals.append(x); ws.append(r["peso"])
        equiv[pd.Period(m, "M")] = np.average(vals, weights=ws) if vals else np.nan
    M["indec_equiv_gba"] = pd.Series(equiv)
    M["ipc_general"] = general.reindex(M.index)
    M["alimentos"] = var("indec_ipc_division_01").reindex(M.index)
    rem = pd.read_parquet(C / "rem_normalizada.parquet")
    g = rem[(rem.variable_std == "ipc_general") & (rem.tipo_periodo == "fecha") & (rem.unidad == "var. % mensual")].copy()
    g["origen"] = pd.to_datetime(g.mes_relevamiento).dt.to_period("M")
    g["obj"] = pd.to_datetime(g.periodo_fin).dt.to_period("M")
    rem_m1 = g[g["obj"] == g["origen"] + 1].groupby("obj")["mediana"].mean()   # lo que esperaba el REM a fin del mes anterior
    M["rem_t_1"] = rem_m1.reindex(M.index)
    M["sorpresa_rem"] = M["ipc_general"] - M["rem_t_1"]
    q = M.dropna(subset=["variacion_25_pct", "ipc_general"])
    L = ["=== TERMÓMETRO DEL SÚPER 2016-2018 (Zenodo, precio de lista, nacional) ===",
         f"Meses con variación: {len(M)} ({M.index.min()} a {M.index.max()}); con IPC nacional INDEC: {len(q)}",
         f"Ítems por mes (mediana): {int(M['n_items'].median()):,}", "",
         "Correlación de la variación mensual del súper (1 al 25 contra el mes anterior) con:",
         f"   IPC general nacional {q['variacion_25_pct'].corr(q['ipc_general']):+.2f} | alimentos {q['variacion_25_pct'].corr(q['alimentos']):+.2f} | "
         f"mismas clases (GBA) {q['variacion_25_pct'].corr(q['indec_equiv_gba']):+.2f}",
         f"   lo que el REM NO anticipó (IPC real − REM de fin del mes anterior): {q.dropna(subset=['sorpresa_rem'])['variacion_25_pct'].corr(q.dropna(subset=['sorpresa_rem'])['sorpresa_rem']):+.2f} "
         f"(n={q['sorpresa_rem'].notna().sum()})", "",
         "Mes por mes:",
         M[["variacion_pct", "variacion_25_pct", "ipc_general", "alimentos", "indec_equiv_gba", "rem_t_1", "sorpresa_rem"]].round(2).to_string()]
    texto = "\n".join(L)
    print(texto)
    (C / "informe_zenodo.txt").write_text(texto, encoding="utf-8")


def main():
    eans = mapa_ean()
    print(f"Códigos de barras de la canasta: {len(eans):,} ({eans.nunique()} variedades)")
    procesar_dias(eans)
    M, K, _ = indice()
    validar(M, K)


if __name__ == "__main__":
    main()
