# inventario_variables.py
# Listado de TODAS las variables guardadas: fuente, nombre, frecuencia, desde, hasta y cantidad de registros.
# Junta el catálogo de series_historicas con los archivos que tienen actualizador propio (REM, IPC, IPCBA, BCRA diario,
# dólares, futuros, SEPA, combustibles). Salida: datos\inventario_variables.xlsx (hojas "Resumen" y "Variables").
# Uso: py -3.13 inventario_variables.py
import sys
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"


def fila(conjunto, fuente, id_, nombre, frecuencia, fechas, archivo, unidad=""):
    f = pd.to_datetime(pd.Series(fechas)).dropna()
    return {"conjunto": conjunto, "fuente": fuente, "id": id_, "nombre": nombre, "frecuencia": frecuencia, "unidad": unidad,
            "desde": f.min().date() if len(f) else None, "hasta": f.max().date() if len(f) else None,
            "registros": int(len(f)), "archivo": archivo}


def main():
    filas = []
    # 1) series históricas (catálogo)
    cat = pd.read_csv(C / "series_historicas_catalogo.csv", encoding="utf-8-sig")
    for _, r in cat.iterrows():
        filas.append({"conjunto": "Series históricas", "fuente": r["fuente"], "id": r["id"], "nombre": r["nombre"],
                      "frecuencia": r["frecuencia"], "unidad": r["unidad"], "desde": r["desde"], "hasta": r["hasta"],
                      "registros": r["datos"], "archivo": "series_historicas.parquet"})
    # 2) BCRA diario (actualizar_bcra.py)
    b = pd.read_parquet(C / "bcra_series.parquet")
    for s, g in b.groupby("serie"):
        filas.append(fila("BCRA (actualizador diario)", "BCRA API v4.0", f"id {g['id_variable'].iloc[0]}", s, "D", g["fecha"], "bcra_series.parquet"))
    # 3) IPC INDEC e IPCBA
    ipc = pd.read_parquet(C / "ipc_indec.parquet")
    for c in [c for c in ipc.columns if c != "mes"]:
        filas.append(fila("IPC INDEC", "INDEC", c, c, "M", ipc.loc[ipc[c].notna(), "mes"], "ipc_indec.parquet"))
    ib = pd.read_parquet(C / "ipcba.parquet")
    for c in [c for c in ib.columns if c != "mes"]:
        filas.append(fila("IPCBA", "IDECBA (Ciudad de Buenos Aires)", c, c, "M", ib.loc[ib[c].notna(), "mes"], "ipcba.parquet"))
    # 4) dólares paralelos
    d = pd.read_parquet(C / "dolares.parquet")
    for s, g in d.groupby("serie"):
        filas.append(fila("Dólares paralelos", "argentinadatos.com", s, f"Dólar {s} (venta)", "D", g["fecha"], "dolares.parquet"))
    # 5) futuros de dólar (CEM): por tipo
    fu = pd.read_parquet(C / "dolar_futuro_cem.parquet")
    fut = fu[fu["symbol"].str.fullmatch(r"DLR\d{6}")]
    filas.append(fila("Dólar futuro", "A3 Mercados (CEM)", "DLR futuros", f"Futuros de dólar ({fut['symbol'].nunique()} contratos)",
                      "D", fut["fecha"], "dolar_futuro_cem.parquet"))
    filas[-1]["registros"] = len(fut)
    opc = fu[~fu["symbol"].str.fullmatch(r"DLR\d{6}")]
    filas.append(fila("Dólar futuro", "A3 Mercados (CEM)", "DLR opciones", f"Opciones sobre dólar ({opc['symbol'].nunique()} símbolos)",
                      "D", opc["fecha"], "dolar_futuro_cem.parquet"))
    filas[-1]["registros"] = len(opc)
    # 6) REM: por variable
    rem = pd.read_parquet(C / "rem_normalizada.parquet")
    for v, g in rem.groupby("variable_std"):
        filas.append(fila("REM del BCRA", "BCRA (REM)", v, f"REM: {v} ({g['periodo_original'].nunique()} períodos pronosticados)",
                          "M (relevamiento)", g["mes_relevamiento"], "rem_normalizada.parquet"))
        filas[-1]["registros"] = len(g)
    # 7) SEPA (precios de supermercados): días con resumen; registros = filas (producto x cadena x bandera x región x día)
    res = sorted(Path("E:/SEPA/resumen").glob("sepa_*.parquet"))
    if res:
        import pyarrow.parquet as pq
        dias = [p.stem.replace("sepa_", "") for p in res]
        filas.append(fila("Precios de supermercados (SEPA)", "Secretaría de Comercio (SEPA); historia 2024-26 vía preciazo",
                          "SEPA resumen", f"Precio mediano por producto x cadena x región ({len(dias)} días)", "D", dias,
                          "E:\\SEPA\\resumen"))
        filas[-1]["registros"] = sum(pq.ParquetFile(p).metadata.num_rows for p in res)
        filas[-1]["unidad"] = "filas de resumen (cada día ~14 millones de precios crudos)"
    # 8) frescos: Mercado Central, Precios del Central, hacienda MAG
    for arch, conj, fuente, nom in [
            ("mercado_central.parquet", "Mercado Central (frutas y hortalizas)", "CMCBA (2017-22 vía web.archive.org)",
             "Precios mayoristas diarios por especie y variedad"),
            ("preciosdelcentral.parquet", "Mercado Central (frutas y hortalizas)", "CMCBA vía preciosdelcentral.com.ar",
             "Precios diarios por variedad, últimos 365 días"),
            ("mag_precios.parquet", "Hacienda (MAG Cañuelas)", "Mercado Agroganadero S.A.",
             "Precios de hacienda por categoría, mensual y hasta el día 25")]:
        if (C / arch).exists():
            x = pd.read_parquet(C / arch)
            fechas = x["fecha"] if "fecha" in x.columns else pd.PeriodIndex(x["mes"], freq="M").to_timestamp()
            filas.append(fila(conj, fuente, arch.replace(".parquet", ""), nom, "D" if "fecha" in x.columns else "M", fechas, arch))
            filas[-1]["registros"] = len(x)
    z = sorted(Path("E:/SEPA/historico_zenodo").glob("precios_*.csv.gz"))
    if z:
        filas.append(fila("Precios de supermercados (SEPA)", "Zenodo (Daruich, Kozlowski, Vilhuber)", "SEPA 2016-2018",
                          "Precios SEPA históricos (códigos y precios, sin descripción)", "D",
                          [p.name[8:18] for p in z], "E:\\SEPA\\historico_zenodo"))
        filas[-1]["unidad"] = "días con datos"
    for nom, arch in [("Índice SEPA mensual por región", "sepa_indice_mensual.parquet")]:
        if (C / arch).exists():
            m = pd.read_parquet(C / arch)
            filas.append(fila("Precios de supermercados (SEPA)", "Elaboración propia sobre SEPA", "sepa_indice", nom, "M",
                              pd.PeriodIndex(m["mes"], freq="M").to_timestamp(), arch))
            filas[-1]["registros"] = len(m)

    V = pd.DataFrame(filas)
    V["desde"] = pd.to_datetime(V["desde"]).dt.date
    V["hasta"] = pd.to_datetime(V["hasta"]).dt.date
    R = V.groupby(["conjunto", "fuente"]).agg(variables=("id", "size"), desde=("desde", "min"), hasta=("hasta", "max"),
                                              registros=("registros", "sum")).reset_index().sort_values("registros", ascending=False)
    R = pd.concat([R, pd.DataFrame([{"conjunto": "TOTAL", "fuente": "", "variables": int(R["variables"].sum()),
                                     "desde": R["desde"].min(), "hasta": R["hasta"].max(), "registros": int(R["registros"].sum())}])],
                  ignore_index=True)
    with pd.ExcelWriter(C / "inventario_variables.xlsx", engine="openpyxl") as x:
        R.to_excel(x, sheet_name="Resumen", index=False)
        V.to_excel(x, sheet_name="Variables", index=False)
        for hoja, df in (("Resumen", R), ("Variables", V)):
            ws = x.sheets[hoja]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for i, col in enumerate(df.columns, 1):
                ancho = min(70, max(10, int(df[col].astype(str).str.len().quantile(0.95)) + 2, len(col) + 2))
                ws.column_dimensions[ws.cell(1, i).column_letter].width = ancho
            for fila_ in ws.iter_rows(min_row=2):
                for c in fila_:
                    if isinstance(c.value, int) and c.column_letter in ("C", "F", "I") or df.columns[c.column - 1] == "registros":
                        c.number_format = "#,##0"
    print(f"{len(V):,} variables, {V['registros'].sum():,} registros -> datos\\inventario_variables.xlsx")
    print(R.to_string(index=False))


if __name__ == "__main__":
    main()
