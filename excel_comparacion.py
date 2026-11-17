# excel_comparacion.py - (17-nov-2026, pedido de Marcos) Excel con lo que estimamos nosotros, lo que dijo el REM y el dato
# real (si ya existe), para todas las variables y todos los períodos que pide el REM, y si las diferencias son
# estadísticamente significativas.
#   Nuestro dato: oct-2026 = Pronóstico sellado n.º 1B (CSV del sello); nov-2026 en adelante = el último registrado
#                 (publicar_web.pronosticos(); si ya está sellado, el CSV del sello).
#   REM: mediana, desvío y P10-P90 del REM del MISMO relevamiento (vacío hasta que el BCRA lo publique).
#   Real: IPC (general y núcleo), A3500 y TAMAR (promedio de meses ya terminados), PIB s.e. (última versión),
#         desocupación EPH, exportaciones e importaciones (INDEC). Años, 12 y 24 meses: cuando el período terminó.
#   Nosotros vs REM: z = (nuestro − mediana) / desvío de las consultoras; |z| < 1,96 -> no significativa (95%).
#   Nosotros vs real: z = (nuestro − real) / error típico del REM (raíz del error cuadrático medio de la mediana contra el
#         real, misma variable y mismo plazo, relevamientos desde dic-2024); |z| < 1,96 -> no significativa.
# Uso: py -3.13 excel_comparacion.py   -> datos\comparacion_pronosticos.xlsx (se rehace entero cada vez)
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
C = BASE / "datos"
SALIDA = C / "comparacion_pronosticos.xlsx"
DESDE_REL = pd.Period("2026-10", "M")         # primer relevamiento con pronóstico registrado y sellado
DESDE_HIST = pd.Period("2024-12", "M")        # régimen actual, para el error típico del REM
Z95 = 1.96
MIN_HIST = 5
HOY = pd.Period(pd.Timestamp.today(), "M")
CLAVE_REM = {"ipc_general": "inflacion", "ipc_nucleo": "nucleo", "tipo_cambio": "dolar", "desocupacion": "desempleo",
             "tasa_tamar": "tasa", "importaciones": "importaciones", "exportaciones": "exportaciones",
             "resultado_primario": "fiscal"}
TITULO_A_CLAVE = {"Inflación": "inflacion", "Inflación núcleo": "nucleo", "Dólar mayorista (A3500)": "dolar",
                  "PIB trimestral": "pib", "PIB anual (pregunta del REM)": "pib_anual", "Desocupación (EPH)": "desempleo",
                  "Tasa TAMAR": "tasa", "Importaciones": "importaciones", "Exportaciones": "exportaciones",
                  "Resultado primario (SPN)": "fiscal"}
NOMBRE = {v: k for k, v in TITULO_A_CLAVE.items()}
NOMBRE["pib_anual"] = "PIB anual"
UNIDAD = {"inflacion": "% mensual (12 y 24 meses y años: % acumulado)", "nucleo": "% mensual (12 y 24 meses y años: % acumulado)",
          "dolar": "$ por USD, promedio del mes", "pib": "% trimestral s.e.", "pib_anual": "% anual (promedio)",
          "desempleo": "% de la población activa", "tasa": "TNA %, promedio del mes", "importaciones": "millones de USD",
          "exportaciones": "millones de USD", "fiscal": "miles de millones de $"}
ORDEN = ["inflacion", "nucleo", "dolar", "pib", "pib_anual", "desempleo", "tasa", "importaciones", "exportaciones", "fiscal"]
ROMANO = {"I": 1, "II": 2, "III": 3, "IV": 4}


# ---------------------------------------------------------------- REM con las mismas etiquetas que nuestras tarjetas
def rem_etiquetado():
    r = pd.read_parquet(C / "rem_normalizada.parquet")
    r["rel"] = pd.to_datetime(r["mes_relevamiento"]).dt.to_period("M")
    filas = []
    for x in r[r["rel"] >= DESDE_HIST - 2].itertuples():
        v, tp, po, rel = x.variable_std, x.tipo_periodo, str(x.periodo_original), x.rel
        clave = ("pib" if tp == "trimestre" else "pib_anual") if v == "pib" else CLAVE_REM.get(v)
        if clave is None:
            continue
        if tp == "fecha":
            obj = pd.Period(po[:7], "M")
            et, h = str(obj), (obj - rel).n
        elif tp == "anio":
            y = int(po[:4])
            et = (f"Año {y} (dic/dic)" if clave in ("inflacion", "nucleo") else f"Fin de {y} (dic)" if clave in ("dolar", "tasa")
                  else f"{y} T4" if clave == "desempleo" else str(y))
            h = 100 + (y - rel.year)                          # 100 = año en curso, 101 = siguiente...
        elif tp == "proximos_meses":
            hm = int(x.horizonte_meses)
            et, h = ("Próx. 12 meses", 12) if hm == 12 else ("Próx. 24 meses (2.º año)", 24)
        elif tp == "trimestre":
            rom, yy = po.split()[-1].split("-")
            q = pd.Period(f"20{yy}Q{ROMANO[rom]}", "Q")
            et, h = f"20{yy} T{ROMANO[rom]}", (q.asfreq("M", "end") - rel).n
        else:
            continue
        if clave == "desempleo" and tp == "anio":
            h = (pd.Period(f"{y}-12", "M") - rel).n           # el "año" de la EPH es su IV trimestre
        filas.append({"rel": rel, "clave": clave, "periodo": et, "h": h, "mediana": x.mediana, "desvio": x.desvio,
                      "p10": x.percentil_10, "p90": x.percentil_90, "n_part": x.cantidad_de_participantes})
    R = pd.DataFrame(filas)
    return R.groupby(["rel", "clave", "periodo"], as_index=False).first()


# ---------------------------------------------------------------- datos reales
def cargar_reales():
    i = pd.read_parquet(C / "ipc_indec.parquet")
    i["m"] = pd.to_datetime(i["mes"]).dt.to_period("M")
    i = i.set_index("m")
    P = {"inflacion": i["ipc_general_indice"], "nucleo": i["ipc_nucleo_indice"]}
    b = pd.read_parquet(C / "bcra_series_mensual.parquet")
    b["m"] = pd.to_datetime(b["mes"]).dt.to_period("M")
    M = {k: b[b["serie"] == s].set_index("m")["promedio"] for k, s in (("dolar", "tc_mayorista_a3500"), ("tasa", "tamar_tna"))}
    M = {k: v[v.index < HOY] for k, v in M.items()}       # solo meses terminados
    h = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
    for k, sid in (("exportaciones", "datosgob_74.3_IET_0_M_16"), ("importaciones", "datosgob_74.3_IIT_0_M_25")):
        s = h[h["id"] == sid]
        M[k] = pd.Series(s["valor"].values, index=pd.to_datetime(s["fecha"]).dt.to_period("M")).sort_index()
    pv = pd.read_parquet(C / "pib_desest_versiones.parquet")
    pv = pv[pv["version"].astype(str) == pv["version"].astype(str).max()].dropna(subset=["pib"])
    pib = pd.Series(pv["pib"].values, index=[pd.Period(f"{int(a)}Q{int(t)}", "Q") for a, t in zip(pv["anio"], pv["trim"])]).sort_index()
    U = pd.read_parquet(C / "desempleo.parquet")
    U = pd.Series(U["desocupacion"].values, index=pd.PeriodIndex(U["trimestre"], freq="Q"))
    return P, M, pib, U


def real_de(clave, et, rel, P, M, pib, U):
    """Dato real para una fila (NaN si todavía no existe)."""
    try:
        if clave in ("inflacion", "nucleo"):
            p = P[clave]
            if et == "Próx. 12 meses":
                return (p[rel + 12] / p[rel] - 1) * 100
            if et.startswith("Próx. 24"):
                return (p[rel + 24] / p[rel + 12] - 1) * 100
            if et.startswith("Año"):
                y = int(et[4:8])
                return (p[pd.Period(f"{y}-12", "M")] / p[pd.Period(f"{y - 1}-12", "M")] - 1) * 100
            m = pd.Period(et, "M")
            return (p[m] / p[m - 1] - 1) * 100
        if clave in ("dolar", "tasa"):
            s = M[clave]
            m = rel + 12 if et == "Próx. 12 meses" else pd.Period(f"{et[7:11]}-12", "M") if et.startswith("Fin de") else pd.Period(et, "M")
            return s[m]
        if clave in ("exportaciones", "importaciones"):
            s = M[clave]
            if len(et) == 4:
                ms = pd.period_range(f"{et}-01", f"{et}-12", freq="M")
                return s[ms].sum() if all(mm in s.index for mm in ms) else np.nan
            return s[pd.Period(et, "M")]
        if clave == "pib":
            q = pd.Period(f"{et[:4]}Q{et[-1]}", "Q")
            return (pib[q] / pib[q - 1] - 1) * 100
        if clave == "pib_anual":
            y = int(et)
            a = [pib.get(pd.Period(f"{y}Q{k}", "Q"), np.nan) for k in range(1, 5)]
            b = [pib.get(pd.Period(f"{y - 1}Q{k}", "Q"), np.nan) for k in range(1, 5)]
            return (np.sum(a) / np.sum(b) - 1) * 100 if not np.isnan(a + b).any() else np.nan
        if clave == "desempleo":
            return U[pd.Period(f"{et[:4]}Q{et[-1]}", "Q")]
    except (KeyError, ValueError, IndexError):
        return np.nan
    return np.nan                                          # resultado primario: se agrega cuando cierre el año


# ---------------------------------------------------------------- nuestros pronósticos
def nuestros():
    reg = pd.read_csv(C / "sellos" / "registro_sellos.csv", encoding="utf-8-sig", dtype=str).fillna("")
    filas = []
    sellados = set()
    for rel, g in reg.groupby("relevamiento"):
        x = g.iloc[-1]                                     # el último sello del mes (1B reemplaza al 1)
        if pd.Period(rel, "M") < DESDE_REL:
            continue
        d = pd.read_csv(C / "sellos" / rel / x["csv"], encoding="utf-8-sig")
        for f in d.itertuples():
            filas.append({"rel": pd.Period(rel, "M"), "estado": f"Sellado n.º {x['n']}", "clave": TITULO_A_CLAVE.get(f.variable, f.variable),
                          "periodo": str(f.periodo), "nuestro": f.datos_con_metodo})
        sellados.add(rel)
    import publicar_web
    for t in publicar_web.pronosticos():
        rel = str(t.get("relevamiento"))
        if rel in sellados or pd.Period(rel, "M") < DESDE_REL:
            continue
        k = 1000 if t["clave"] == "fiscal" else 1                     # el REM pide el resultado primario en miles de millones
        for f in t["filas"]:
            filas.append({"rel": pd.Period(rel, "M"), "estado": "Registrado, sin sellar", "clave": t["clave"],
                          "periodo": str(f["periodo"]), "nuestro": f["nuestro"] * k if f["nuestro"] is not None else np.nan})
    return pd.DataFrame(filas)


def main():
    R = rem_etiquetado()
    P, M, pib, U = cargar_reales()
    # error típico del REM (mediana contra el real), por variable y plazo, relevamientos desde dic-2024
    hist = R[R["rel"] >= DESDE_HIST].copy()
    hist["real"] = [real_de(c, e, r, P, M, pib, U) for c, e, r in zip(hist["clave"], hist["periodo"], hist["rel"])]
    hist["err"] = hist["mediana"] - hist["real"]
    sig = hist.dropna(subset=["err"]).groupby(["clave", "h"])["err"].agg(
        rmse=lambda e: float(np.sqrt(np.mean(e ** 2))), n="count").reset_index()

    N = nuestros()
    rem_rel = R[["rel", "clave", "periodo", "h", "mediana", "desvio", "p10", "p90", "n_part"]]
    D = N.merge(rem_rel, on=["rel", "clave", "periodo"], how="left")
    # h para las filas sin REM todavía (relevamiento en curso): el mismo que pidió el REM anterior, corrido
    D["h"] = D["h"].fillna(pd.Series([hh for hh in D.apply(lambda x: _h_sin_rem(x), axis=1)], index=D.index))
    D = D.merge(sig, on=["clave", "h"], how="left")
    D["real"] = [real_de(c, e, r, P, M, pib, U) for c, e, r in zip(D["clave"], D["periodo"], D["rel"])]
    D["rem_real"] = D["mediana"] - D["real"]
    D["dif_rem"] = D["nuestro"] - D["mediana"]
    D["z_rem"] = D["dif_rem"] / D["desvio"]
    D["dif_real"] = D["nuestro"] - D["real"]
    D["z_real"] = np.where(D["n"] >= MIN_HIST, D["dif_real"] / D["rmse"], np.nan)

    def veredicto(z, falta, nohist=False):
        if falta:
            return falta
        if nohist:
            return "Sin historia para medir el error"
        if pd.isna(z):
            return "—"
        return "No significativa (≈ iguales)" if abs(z) < Z95 else ("Significativa: nosotros más alto" if z > 0 else "Significativa: nosotros más bajo")
    D["sig_rem"] = [veredicto(z, "Sin REM todavía" if pd.isna(m) else ("Sin estimación nuestra" if pd.isna(n) else None))
                    for z, m, n in zip(D["z_rem"], D["mediana"], D["nuestro"])]
    D["sig_real"] = [veredicto(z, "Sin dato real todavía" if pd.isna(r) else None, nohist=(not pd.isna(r) and not (k >= MIN_HIST)))
                     for z, r, k in zip(D["z_real"], D["real"], D["n"])]
    D["orden"] = D["clave"].map({c: i for i, c in enumerate(ORDEN)})
    D = D.sort_values(["rel", "orden"], kind="stable")

    salida = pd.DataFrame({
        "Relevamiento": D["rel"].astype(str), "Estado": D["estado"], "Variable": D["clave"].map(NOMBRE), "Unidad": D["clave"].map(UNIDAD),
        "Período": D["periodo"], "Datos Con Método": D["nuestro"], "REM (mediana)": D["mediana"],
        "REM desvío consultoras": D["desvio"], "REM P10": D["p10"], "REM P90": D["p90"], "Consultoras": D["n_part"],
        "Dif. nosotros − REM": D["dif_rem"], "z vs REM": D["z_rem"], "¿Diferencia con el REM?": D["sig_rem"],
        "Dato real": D["real"], "Dif. nosotros − real": D["dif_real"], "Dif. REM − real": D["rem_real"],
        "Error típico del REM (mismo plazo)": D["rmse"], "Casos para el error típico": D["n"], "z vs real": D["z_real"],
        "¿Diferencia con el real?": D["sig_real"]})

    metodo = pd.DataFrame({"Cómo leer esta planilla": [
        "Una fila por variable y período que pide el REM, para cada relevamiento desde oct-2026.",
        "Datos Con Método: oct-2026 = Pronóstico sellado n.º 1B (archivo sellado en Bitcoin); relevamientos sin sellar = último pronóstico registrado.",
        "REM: mediana del REM del MISMO relevamiento (lo que contestaron las consultoras ese mes). Vacío hasta que el BCRA lo publique.",
        "Dato real: INDEC (IPC, PIB desestacionalizado, EPH, exportaciones e importaciones) y BCRA (A3500 y TAMAR, promedio de meses terminados). "
        "Años, 12 y 24 meses: cuando el período terminó. Resultado primario: se agrega cuando cierre el año.",
        "",
        "¿Diferencia con el REM?  z = (nuestro − mediana del REM) / desvío entre consultoras. Si |z| < 1,96, la diferencia no es "
        "estadísticamente significativa al 95%: nuestro número es uno más dentro del conjunto de consultoras.",
        "¿Diferencia con el real?  z = (nuestro − real) / error típico del REM para esa variable y ese plazo (raíz del error cuadrático "
        "medio de la mediana del REM contra el real, relevamientos desde dic-2024). Si |z| < 1,96, la diferencia con lo que pasó no es "
        "mayor que el error normal de un pronóstico a ese plazo: no es significativa.",
        f"Se necesita un mínimo de {MIN_HIST} casos para medir el error típico; si no, dice 'Sin historia para medir el error'.",
        "",
        f"Generado: {pd.Timestamp.now():%Y-%m-%d %H:%M}. Se rehace con: py -3.13 excel_comparacion.py"]})

    with pd.ExcelWriter(SALIDA, engine="openpyxl") as w:
        salida.to_excel(w, sheet_name="Comparación", index=False)
        metodo.to_excel(w, sheet_name="Método", index=False)
        formato(w, salida)
    print(f"Excel guardado en {SALIDA} ({len(salida)} filas)")
    for rel, g in salida.groupby("Relevamiento"):
        print(f"  {rel}: {len(g)} filas | con REM {g['REM (mediana)'].notna().sum()} | con dato real {g['Dato real'].notna().sum()}")
        print("     vs REM:  " + ", ".join(f"{k} {v}" for k, v in g["¿Diferencia con el REM?"].value_counts().items()))
        print("     vs real: " + ", ".join(f"{k} {v}" for k, v in g["¿Diferencia con el real?"].value_counts().items()))


def _h_sin_rem(x):
    """Plazo de una fila del relevamiento en curso (sin REM publicado): mismo cálculo que en rem_etiquetado()."""
    et, rel, clave = x["periodo"], x["rel"], x["clave"]
    if not pd.isna(x.get("h", np.nan)):
        return x["h"]
    try:
        if et == "Próx. 12 meses":
            return 12
        if et.startswith("Próx. 24"):
            return 24
        if et.startswith("Año") or et.startswith("Fin de"):
            return 100 + int(et[4:8] if et.startswith("Año") else et[7:11]) - rel.year
        if clave in ("pib", "desempleo"):
            q = pd.Period(f"{et[:4]}Q{et[-1]}", "Q")
            return (q.asfreq("M", "end") - rel).n
        if len(et) == 4:
            return 100 + int(et) - rel.year
        return (pd.Period(et, "M") - rel).n
    except Exception:
        return np.nan


def formato(w, df):
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    ws = w.sheets["Comparación"]
    ws.freeze_panes = "F2"
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2A78D6")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 45
    anchos = {"Relevamiento": 12, "Estado": 20, "Variable": 24, "Unidad": 22, "Período": 22, "¿Diferencia con el REM?": 30,
              "¿Diferencia con el real?": 30}
    for j, col in enumerate(df.columns, 1):
        ws.column_dimensions[get_column_letter(j)].width = anchos.get(col, 13)
        if df[col].dtype.kind == "f":
            for i in range(2, len(df) + 2):
                ws.cell(i, j).number_format = "#,##0.00"
    verde, rojo, gris = (PatternFill("solid", fgColor=c) for c in ("D9F2D9", "F8D7CF", "EEEEEE"))
    n = len(df) + 1
    for col in ("¿Diferencia con el REM?", "¿Diferencia con el real?"):
        L = get_column_letter(list(df.columns).index(col) + 1)
        rng = f"{L}2:{L}{n}"
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'LEFT({L}2,2)="No"'], fill=verde))
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'LEFT({L}2,5)="Signi"'], fill=rojo))
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'LEFT({L}2,3)="Sin"'], fill=gris))
    ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{n}"
    wm = w.sheets["Método"]
    wm.column_dimensions["A"].width = 140
    for r in wm.iter_rows(min_row=2):
        r[0].alignment = Alignment(wrap_text=True, vertical="top")
    wm["A1"].font = Font(bold=True)


if __name__ == "__main__":
    main()
