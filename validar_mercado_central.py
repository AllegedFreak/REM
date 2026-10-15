# validar_mercado_central.py
# ¿Los precios mayoristas del Mercado Central (MCBA) siguen o anticipan a verduras y frutas del IPC (GBA)?
# Canastas = productos que releva el INDEC en GBA. Verduras: papa, tomate redondo, lechuga, cebolla, zapallo anco,
# batata. Frutas: banana, manzana deliciosa, naranja, limón. Precio mayorista = precio modal por kg del promedio de la
# especie ("Prom.Esp."), promedio de los días del mes (también hasta el día 25: lo que se conoce al cerrar el REM).
# Variación de la canasta = promedio de las variaciones logarítmicas de sus productos (igual peso, como Jevons).
# Comparaciones (variación mensual, %): contra la clase del IPC GBA (01.1.7 verduras, 01.1.6 frutas) y contra el precio
# promedio de cada producto del INDEC. En crudo y DESESTACIONALIZADO (se resta a cada serie el promedio de su mes del año
# estimado en 2017-2021). Mismo mes y un mes de rezago. Prueba fuera de muestra: se estima en 2017-2021, se prueba en 2026.
# Salidas: datos\informe_validacion_mercado_central.txt, datos\mercado_central_mensual.csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
CANASTAS = {
    "verduras": {"clase": "01.1.7", "productos": {"PAPA": "Papa", "TOMATE": "Tomate redondo", "LECHUGA": "Lechuga",
                                                  "CEBOLLA": "Cebolla", "ZAPALLO": "Zapallo anco", "BATATA": "Batata"}, "tipo": "H"},
    "frutas": {"clase": "01.1.6", "productos": {"BANANA": "Banana", "MANZANA": "Manzana deliciosa", "NARANJA": "Naranja",
                                                "LIMON": "Limón"}, "tipo": "F"},
}
MUESTRA = ("2017-01", "2021-12")


def var_log(s):
    return np.log(s).diff() * 100


def desest(s, ref):
    """Resta a cada mes el promedio de su mes del año en el período de referencia (2017-2021)."""
    meses = s.index.month
    medias = s[(s.index >= pd.Period(ref[0], "M")) & (s.index <= pd.Period(ref[1], "M"))].groupby(
        s[(s.index >= pd.Period(ref[0], "M")) & (s.index <= pd.Period(ref[1], "M"))].index.month).mean()
    return s - pd.Series(meses, index=s.index).map(medias).values


def main():
    m = pd.read_parquet(C / "mercado_central.parquet")
    m = m[m["promedio_especie"] & (m["modal_kg"] > 0)].copy()
    m["fecha"] = pd.to_datetime(m["fecha"])
    m["mes"] = m["fecha"].dt.to_period("M")
    pp = pd.read_parquet(C / "precios_promedio_indec.parquet")
    pp = pp[(pp["hoja"] == "GBA") & (pp["region"] == "GBA")].copy()
    pp["mes"] = pd.to_datetime(pp["mes"]).dt.to_period("M")
    hs = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    L = ["=== MERCADO CENTRAL (mayorista) vs IPC GBA: verduras y frutas ===",
         "Variación mensual en %. 'desest.' = sin el promedio de cada mes del año (estimado en 2017-2021).",
         "Correlaciones con el mismo mes (t) y con el mayorista del mes anterior (t-1). Datos MCBA: 2017-2021 y 2026.", ""]
    tabla = []
    for nom, cfg in CANASTAS.items():
        x = m[(m["tipo"] == cfg["tipo"]) & m["especie"].isin(cfg["productos"])]
        mens = x.groupby(["especie", "mes"])["modal_kg"].mean().unstack(0)
        mens25 = x[x["fecha"].dt.day <= 25].groupby(["especie", "mes"])["modal_kg"].mean().unstack(0)
        idx = pd.period_range(mens.index.min(), mens.index.max(), freq="M")
        mens, mens25 = mens.reindex(idx), mens25.reindex(idx)
        canasta = var_log(mens).mean(axis=1, skipna=False)                 # mes completo contra mes completo
        canasta25 = (np.log(mens25) - np.log(mens.shift(1))).mean(axis=1, skipna=False) * 100   # hasta el 25 vs mes anterior
        s = hs[hs["id"] == f"indec_ipcap_gba_{cfg['clase']}"]
        ipc = var_log(pd.Series(s["valor"].values, index=pd.to_datetime(s["fecha"]).dt.to_period("M")).sort_index()).reindex(idx)
        d = pd.DataFrame({"mayorista": canasta, "mayorista_25": canasta25, "ipc": ipc})
        d["mayorista_ant"] = d["mayorista"].shift(1)
        for c in ["mayorista", "mayorista_25", "ipc", "mayorista_ant"]:
            d[c + "_desest"] = desest(d[c], MUESTRA)
        d["canasta"] = nom
        tabla.append(d)
        L.append(f"{nom.upper()} (IPC GBA clase {cfg['clase']}): {d['mayorista'].notna().sum()} meses con dato mayorista")
        for periodo, mask in (("2017-2021", (d.index <= pd.Period(MUESTRA[1], "M"))), ("2026", d.index.year == 2026)):
            q = d[mask].dropna(subset=["ipc", "mayorista"])
            if len(q) < 4:
                continue
            L.append(f"   {periodo} (n={len(q)}): correlación IPC con mayorista del mismo mes {q['ipc'].corr(q['mayorista']):+.2f} "
                     f"(hasta el 25: {q['ipc'].corr(q['mayorista_25']):+.2f}) | con el del mes anterior {q['ipc'].corr(q['mayorista_ant']):+.2f}")
            L.append(f"      desestacionalizado: mismo mes {q['ipc_desest'].corr(q['mayorista_desest']):+.2f} "
                     f"(hasta el 25: {q['ipc_desest'].corr(q['mayorista_25_desest']):+.2f}) | mes anterior "
                     f"{q['ipc_desest'].corr(q['mayorista_ant_desest']):+.2f}")
        # regresión de nowcast: IPC_t = a + b * mayorista_hasta_25_t + c * mayorista_{t-1} (desestacionalizados)
        e = d[d.index <= pd.Period(MUESTRA[1], "M")].dropna(subset=["ipc_desest", "mayorista_25_desest", "mayorista_ant_desest"])
        X = sm.add_constant(e[["mayorista_25_desest", "mayorista_ant_desest"]])
        r = sm.OLS(e["ipc_desest"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
        L.append(f"   Nowcast (2017-2021, desest.): IPC = {r.params['const']:+.2f} + {r.params['mayorista_25_desest']:.2f} x mayorista hasta el 25 "
                 f"(t {r.tvalues['mayorista_25_desest']:.1f}) + {r.params['mayorista_ant_desest']:.2f} x mayorista mes anterior "
                 f"(t {r.tvalues['mayorista_ant_desest']:.1f}) | R² {r.rsquared:.2f}")
        prueba = d[d.index.year == 2026].dropna(subset=["ipc", "mayorista_25_desest", "mayorista_ant_desest"])
        if len(prueba):
            est = r.predict(sm.add_constant(prueba[["mayorista_25_desest", "mayorista_ant_desest"]], has_constant="add"))
            # volver a sumar la estacionalidad del IPC para comparar en crudo
            estac = prueba["ipc"] - prueba["ipc_desest"]
            pred = est + estac
            ingenuo = estac                                               # solo la estacionalidad (sin mayorista)
            L.append(f"   Prueba 2026 (fuera de muestra, n={len(prueba)}): error medio del nowcast {np.abs(pred - prueba['ipc']).mean():.2f} pp "
                     f"vs solo estacionalidad {np.abs(ingenuo - prueba['ipc']).mean():.2f} pp")
            for mes, a, b_, c in zip(prueba.index, prueba["ipc"], pred, ingenuo):
                L.append(f"      {mes}: IPC {a:+6.1f} | nowcast {b_:+6.1f} | solo estacionalidad {c:+6.1f}")
        # producto por producto (crudo y desest., mismo mes), 2017-2021
        L.append("   Por producto (2017-2021, mismo mes; crudo / desest.):")
        for esp, prod in cfg["productos"].items():
            a = var_log(pp[pp["producto"] == prod].set_index("mes")["precio"].sort_index()).reindex(idx)
            b = var_log(mens[esp])
            q = pd.DataFrame({"a": a, "b": b}).loc[: pd.Period(MUESTRA[1], "M")].dropna()
            L.append(f"      {prod:20} {q['a'].corr(q['b']):+.2f} / {desest(q['a'], MUESTRA).corr(desest(q['b'], MUESTRA)):+.2f}  (n={len(q)})")
        L.append("")
    pd.concat(tabla).to_csv(C / "mercado_central_mensual.csv", encoding="utf-8-sig", float_format="%.3f")
    texto = "\n".join(L)
    print(texto)
    (C / "informe_validacion_mercado_central.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
