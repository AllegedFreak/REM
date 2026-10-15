# verificar_todo.py
# Verificación general del proyecto: no modifica datos, solo lee y controla.
#   1) que todos los scripts .py compilen
#   2) que cada archivo de datos exista, se lea, tenga las columnas esperadas, sin huecos ni duplicados y esté al día
#   3) consistencia entre fuentes (IPC guardado vs INDEC, versiones de precios, ponderaciones, base DuckDB, dólares)
# Salida: tabla OK / FALLA / AVISO en pantalla y en datos/informe_verificacion.txt
# Uso: py -3.13 verificar_todo.py
# Código de salida: 0 = todo OK (puede haber avisos) | 1 = alguna FALLA
import py_compile
import sys
import traceback
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
INFORME = CARPETA / "informe_verificacion.txt"
HOY = pd.Timestamp(date.today())
resultados = []


def chequeo(nombre):
    def deco(f):
        try:
            r = f()
            estado, detalle = r if isinstance(r, tuple) else ("OK", r or "")
        except AssertionError as e:
            estado, detalle = "FALLA", str(e)
        except Exception as e:
            estado, detalle = "FALLA", f"{type(e).__name__}: {e} | {traceback.format_exc().splitlines()[-3].strip()}"
        resultados.append((estado, nombre, detalle))
        return f
    return deco


def meses_continuos(periodos):
    p = pd.PeriodIndex(sorted(set(periodos)), freq="M")
    return len(pd.period_range(p.min(), p.max(), freq="M").difference(p))


# ---------------------------------------------------------------- 1) código
@chequeo("Scripts: todos compilan")
def _():
    malos = []
    archivos = sorted(BASE.glob("*.py"))
    for f in archivos:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            malos.append(f"{f.name}: {e.msg[:80]}")
    assert not malos, "; ".join(malos)
    return f"{len(archivos)} scripts"


# ---------------------------------------------------------------- 2) datos
@chequeo("REM normalizado")
def _():
    r = pd.read_parquet(CARPETA / "rem_normalizada.parquet")
    for c in ("fecha_relevamiento", "variable_std", "mediana", "horizonte_meses"):
        assert c in r.columns, f"falta la columna {c}"
    ult = pd.Timestamp(r["fecha_relevamiento"].max())
    assert (HOY - ult).days < 75, f"último relevamiento {ult.date()}: viejo"
    rel = r["fecha_relevamiento"].dt.to_period("M").unique()
    assert meses_continuos(rel) == 0, "faltan meses de relevamiento"
    return f"{r['fecha_relevamiento'].nunique()} relevamientos, último {ult.date()}"


@chequeo("IPC INDEC (ipc_indec.parquet)")
def _():
    i = pd.read_parquet(CARPETA / "ipc_indec.parquet")
    p = pd.to_datetime(i["mes"]).dt.to_period("M")
    assert p.duplicated().sum() == 0, "meses duplicados"
    assert meses_continuos(p) == 0, "faltan meses"
    assert i[["ipc_general_indice", "ipc_nucleo_indice"]].notna().all().all(), "hay índices vacíos"
    v = i["ipc_general_indice_var_mensual"].dropna()
    assert v.between(-1, 35).all(), "variación mensual fuera de rango"
    ult = p.max()
    atraso = (pd.Period(HOY, "M") - ult).n
    assert atraso <= 2, f"último mes {ult}: atrasado"
    return f"{p.min()} a {ult}, {len(i)} meses; último: {i.iloc[-1]['ipc_general_indice_var_mensual']:.2f}%"


@chequeo("IPC guardado = archivo del INDEC")
def _():
    sys.path.insert(0, str(BASE))
    import datos_macro as D
    cat = D.ipc_categorias()
    i = pd.read_parquet(CARPETA / "ipc_indec.parquet")
    i.index = pd.to_datetime(i["mes"]).dt.to_period("M")
    j = pd.concat([cat["general"], i["ipc_general_indice"]], axis=1).dropna()
    dif = (j.iloc[:, 0] / j.iloc[:, 1] - 1).abs().max()
    assert dif < 1e-6, f"difieren hasta {dif:.2e}"
    for c in ("nucleo", "regulados", "estacional"):
        assert cat[c].notna().all(), f"{c} con huecos"
    return f"{len(j)} meses idénticos; categorías completas hasta {cat.index.max()}"


@chequeo("IPCBA")
def _():
    i = pd.read_parquet(CARPETA / "ipcba.parquet")
    p = pd.to_datetime(i["mes"]).dt.to_period("M")
    assert meses_continuos(p) == 0 and not p.duplicated().any(), "huecos o duplicados"
    assert (pd.Period(HOY, "M") - p.max()).n <= 2, f"último mes {p.max()}: atrasado"
    return f"{p.min()} a {p.max()}"


@chequeo("Serie de precios encadenada")
def _():
    e = pd.read_parquet(CARPETA / "precios_encadenada.parquet")
    p = pd.to_datetime(e["mes"]).dt.to_period("M")
    assert meses_continuos(p) == 0, "faltan meses"
    assert e["var_mensual"].iloc[1:].notna().all(), "variaciones vacías"
    assert set(e["fuente"].unique()) == {"IPCBA", "INDEC"}, "fuentes inesperadas"
    corte = e[e["fuente"] == "INDEC"]["mes"].min()
    detalle = f"{p.min()} a {p.max()}; INDEC desde {corte}"
    if (pd.Period(HOY, "M") - p.max()).n > 1:
        return "AVISO", detalle + " (no incluye el último IPC: correr empalme_precios.py)"
    return "OK", detalle


@chequeo("4 versiones del IPC (datos_macro.precios)")
def _():
    import datos_macro as D
    pr = D.precios()
    assert list(pr.columns) == ["oficial", "neuquen", "ipcba2012", "sanluis"], f"columnas {list(pr.columns)}"
    assert pr.notna().all().all(), "hay meses vacíos"
    d = pr.loc[pr.index >= pd.Period("2019-01", "M")]
    assert (d.max(axis=1) - d.min(axis=1)).abs().max() < 0.05, "desde 2019 las versiones deberían coincidir"
    return f"{pr.index.min()} a {pr.index.max()}, sin huecos; coinciden desde 2019"


@chequeo("Series BCRA diarias y mensuales")
def _():
    b = pd.read_parquet(CARPETA / "bcra_series.parquet")
    m = pd.read_parquet(CARPETA / "bcra_series_mensual.parquet")
    esperadas = {"tc_mayorista_a3500", "base_monetaria", "billetes_publico", "m2_pesos", "badlar_tna", "tamar_tna"}
    faltan = esperadas - set(b["serie"])
    assert not faltan, f"faltan series {faltan}"
    assert not b.duplicated(["serie", "fecha"]).any(), "fechas duplicadas"
    viejas = []
    for s in esperadas:
        ult = pd.Timestamp(b[b["serie"] == s]["fecha"].max())
        if (HOY - ult).days > 10:
            viejas.append(f"{s} {ult.date()}")
    assert not viejas, f"desactualizadas: {viejas}"
    assert {"promedio", "fin_de_mes", "dias"} <= set(m.columns), "faltan columnas en la mensual"
    return f"{b['serie'].nunique()} series, al día"


@chequeo("Dólares y brecha")
def _():
    d = pd.read_parquet(CARPETA / "dolares.parquet")
    m = pd.read_parquet(CARPETA / "dolares_mensual.parquet")
    assert set(d["serie"]) == {"blue", "ccl", "mep"}, f"series {set(d['serie'])}: solo deben venir de terceros CCL/MEP/blue"
    d["fecha"] = pd.to_datetime(d["fecha"])
    assert (d["fecha"].dt.weekday < 5).all(), "hay fines de semana"
    a = pd.read_parquet(CARPETA / "bcra_series.parquet")
    habiles = set(pd.to_datetime(a[a["serie"] == "tc_mayorista_a3500"]["fecha"]))
    fuera = d[(~d["fecha"].isin(habiles)) & (d["fecha"] <= max(habiles))]
    assert fuera.empty, f"{len(fuera)} días que no son hábiles de mercado"
    br = m["brecha_ccl"].dropna()
    assert br.between(-20, 300).all(), "brecha fuera de rango"
    pocos = m[(m["dias_brecha"] < 10) & m["brecha_ccl"].notna() & (pd.to_datetime(m["mes"]).dt.to_period("M") != pd.Period(HOY, "M"))]
    assert pocos.empty, "meses con menos de 10 días usados en la brecha"
    return f"CCL hasta {d[d.serie == 'ccl']['fecha'].max().date()}; brecha último mes {br.iloc[-1]:.1f}%"


@chequeo("Series históricas y catálogo")
def _():
    h = pd.read_parquet(CARPETA / "series_historicas.parquet")
    c = pd.read_csv(CARPETA / "series_historicas_catalogo.csv", encoding="utf-8-sig")
    assert set(h["id"]) == set(c["id"]), "el catálogo no coincide con los datos"
    assert not h.duplicated(["id", "fecha"]).any(), "fechas duplicadas"
    assert not h["id"].str.startswith("terceros").any(), "quedó una serie de terceros que se decidió borrar"
    assert not h["id"].str.contains("173.1_RLADOSDOS").any(), "quedó la serie rota de regulados"
    return f"{h['id'].nunique()} series, {len(h):,} datos, desde {pd.to_datetime(h['fecha']).min().date()}"


@chequeo("Base DuckDB")
def _():
    import duckdb
    con = duckdb.connect(str(CARPETA / "base_rem.duckdb"), read_only=True)
    vistas = {r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()}
    parquets = {p.stem for p in CARPETA.glob("*.parquet")}
    falta = parquets - vistas
    rotas = []
    for v in vistas:
        try:
            con.execute(f"select count(*) from {v}").fetchone()
        except Exception:
            rotas.append(v)
    con.close()
    assert not rotas, f"vistas que no se pueden leer: {rotas}"
    if falta:
        return "AVISO", f"{len(vistas)} vistas; parquets sin vista (se agregan en la próxima corrida semanal): {sorted(falta)}"
    return f"{len(vistas)} vistas, todas legibles"


@chequeo("Ponderaciones oficiales del IPC")
def _():
    p = pd.read_csv(CARPETA / "ponderaciones_ipc.csv", encoding="utf-8-sig", dtype={"codigo": str})
    cols = ["Nacional", "GBA", "Pampeana", "Noreste", "Noroeste", "Cuyo", "Patagonia"]
    div = p[p["nivel"] == "división"]
    s = div[cols].sum()
    assert (s - 1).abs().max() < 0.002, f"las divisiones no suman 100%: {s.round(4).to_dict()}"
    # cada renglón = suma de sus hijos directos (división = grupos; grupo = clases, si las tiene)
    for _, d in p[p["nivel"].isin(["división", "grupo"])].iterrows():
        prof = d["codigo"].count(".") + 1
        hijos = p[p["codigo"].str.startswith(d["codigo"] + ".") & (p["codigo"].str.count(r"\.") == prof)]
        if len(hijos):
            for c in cols:
                dif = abs(hijos[c].sum() - d[c])
                assert dif < 0.002, f"{d['codigo']} {c}: hijos suman {hijos[c].sum():.4f} vs {d[c]:.4f}"
    hojas = p[p["hoja"]]
    assert (hojas[cols].sum() - 1).abs().max() < 0.003, f"las hojas no suman 100%: {hojas[cols].sum().round(4).to_dict()}"
    return (f"{len(div)} divisiones y {len(hojas)} rubros finales suman 100% en todas las regiones; "
            f"alimentos nacional {div.iloc[0]['Nacional'] * 100:.1f}%")


@chequeo("Historiales de primera aparición")
def _():
    out = []
    for f in ("historial_ipc.csv", "historial_ipcba.csv"):
        h = pd.read_csv(CARPETA / f, encoding="utf-8-sig")
        assert not h["mes"].duplicated().any(), f"{f}: meses repetidos"
        assert set(h["tipo"]) <= {"carga_inicial", "observado"}, f"{f}: tipos raros"
        out.append(f"{f}: {len(h)} meses, {int((h['tipo'] == 'observado').sum())} observados")
    return "; ".join(out)


@chequeo("Emisión mensual (sin meses vacíos)")
def _():
    e = pd.read_csv(CARPETA / "emision_mensual.csv", index_col=0)
    e.index = pd.PeriodIndex(e.index, freq="M")
    completos = e[e.index < pd.Period(HOY, "M")]
    assert completos[["tesoro", "esterilizacion", "neta"]].notna().all().all(), "meses con emisión vacía"
    assert meses_continuos(e.index) == 0, "faltan meses"
    return f"{e.index.min()} a {e.index.max()}"


@chequeo("Réplica del REM (archivo de resultados)")
def _():
    r = pd.read_csv(CARPETA / "replica_rem.csv")
    for c in ("B", "A", "A+regulados", "rem", "rem_ant"):
        assert c in r.columns, f"falta {c}"
    m1 = r[r["H"] == "m1"].dropna(subset=["B", "rem"])
    dist = (m1["B"] - m1["rem"]).abs().mean()
    assert dist < 1.0, f"distancia de B al REM demasiado grande ({dist:.2f})"
    return f"B a 1 mes: distancia media {dist:.2f} pp ({len(m1)} relevamientos)"


@chequeo("CLAUDE.md menciona los scripts")
def _():
    texto = (BASE / "CLAUDE.md").read_text(encoding="utf-8")
    principales = ["actualizar_todo.py", "actualizar_rem.py", "actualizar_ipc.py", "actualizar_ipcba.py", "actualizar_bcra.py",
                   "actualizar_dolares.py", "guardar_historicas.py", "replicar_rem.py", "ponderaciones_ipc.py"]
    faltan = [s for s in principales if s not in texto]
    if faltan:
        return "AVISO", f"no se mencionan: {faltan}"
    return "todos mencionados"


def main():
    print(f"=== VERIFICACIÓN GENERAL | {HOY.date()} ===\n")
    lineas = []
    for estado, nombre, detalle in resultados:
        marca = {"OK": "✔ OK   ", "AVISO": "! AVISO", "FALLA": "✘ FALLA"}[estado]
        lineas.append(f"{marca}  {nombre:45} {detalle}")
    n = {k: sum(r[0] == k for r in resultados) for k in ("OK", "AVISO", "FALLA")}
    lineas += ["", f"Resultado: {n['OK']} OK, {n['AVISO']} avisos, {n['FALLA']} fallas"]
    texto = "\n".join(lineas)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")
    sys.exit(1 if n["FALLA"] else 0)


if __name__ == "__main__":
    main()
