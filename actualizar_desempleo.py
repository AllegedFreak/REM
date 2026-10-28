# actualizar_desempleo.py
# Tasa de DESOCUPACIÓN de la EPH (INDEC, total 31 aglomerados urbanos, % de la PEA), trimestral, bajada DIRECTO del INDEC.
#   1) busca en la página de la EPH (Nivel4/Tema/4/31/58) la planilla trimestral más nueva
#      "cuadros_tasas_indicadores_eph_MM_AA.xls" (MM_AA = mes y año de publicación) y la guarda en datos\eph\publicaciones
#      (ahí están todas desde jun-2018, bajadas el 28-oct-2026: el servidor del INDEC las conserva)
#   2) lee la tasa de cada planilla (cuadro 1.1) → datos\eph\vintages_desocupacion.csv (una fila por publicación y trimestre)
#   3) arma la serie: el valor de la publicación más nueva; II a IV-2016 (las planillas arrancan en 2017) salen de
#      datos.gob.ar (45.2_ECTDT_0_T_33), y si la API falla, de la copia datos\eph\continua.csv
#   4) "publicado" = mes de la primera planilla que trae el trimestre. Regla del INDEC: el trimestre sale a mediados
#      del 3er mes siguiente (I → jun, II → sep, III → dic, IV → mar), antes del relevamiento del REM de fin de mes.
#      Para 2016-2017 (sin planillas de esa época) se usa la regla.
#   5) controla: revisiones (el INDEC no corrige la tasa: hasta hoy solo hubo redondeos), trimestres faltantes y
#      valores fuera de rango → datos\desempleo.parquet, datos\informe_eph.txt, datos\historial_desempleo.csv
#   6) empleo registrado SIPA (total y asalariados privados, sin estacionalidad; datos.gob.ar) → datos\sipa.parquet,
#      solo para mostrarlo en el sitio (no anticipa la desocupación: probar_sipa.py). Si falla, avisa y sigue.
# La serie arranca en II-2016 a propósito: 2007-2015 el INDEC la desestima ("con reservas") y antes de 2003 es otra
# encuesta (EPH puntual). Ver la sección "Desocupación" de CLAUDE.md.
# Uso: py -3.13 actualizar_desempleo.py [--aviso]
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = error
import argparse
import json
import re
import sys
import urllib.request
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
EPH = CARPETA / "eph"
PUBS = EPH / "publicaciones"
PAGINA = "https://www.indec.gob.ar/Nivel4/Tema/4/31/58"
FTP = "https://www.indec.gob.ar/ftp/cuadros/sociedad/"
API = "https://apis.datos.gob.ar/series/api/series/?ids=45.2_ECTDT_0_T_33&format=json&limit=1000"
API_SIPA = ("https://apis.datos.gob.ar/series/api/series/?ids=151.1_TL_SIN_TAC_2012_M_15,151.1_AARIADOTAC_2012_M_26"
            "&format=json&limit=1000")
DESDE = pd.Period("2016Q2", "Q")
SALIDA = CARPETA / "desempleo.parquet"
INFORME = CARPETA / "informe_eph.txt"
HISTORIAL = CARPETA / "historial_desempleo.csv"
TOL = 0.05   # pp: diferencias menores son redondeo (algunas planillas traen la tasa con todos los decimales)

CONTROLES = {
    "D01": ("tasa_revisada", "La tasa de un trimestre ya publicado cambió más de 0,05 pp entre planillas"),
    "D02": ("trimestre_faltante", "Falta un trimestre en la serie desde II-2016"),
    "D03": ("fuera_de_rango", "Tasa menor a 2% o mayor a 30%"),
    "D04": ("planilla_ilegible", "No se pudo leer la tasa en una planilla del INDEC"),
}


def bajar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def buscar_nueva(informe):
    """Baja la planilla más nueva que lista la página de la EPH, si no estaba. Devuelve su nombre o None."""
    try:
        html = bajar(PAGINA).decode("utf-8", "ignore")
    except Exception as e:
        informe.append(f"No se pudo leer la página de la EPH del INDEC ({e}). Se usan las planillas guardadas.")
        return None
    nombres = sorted(set(re.findall(r"cuadros_tasas_indicadores_eph_\d\d_\d\d\.xls", html)),
                     key=lambda n: re.search(r"_(\d\d)_(\d\d)\.", n).group(2, 1))
    if not nombres:
        informe.append("La página de la EPH no lista ninguna planilla 'cuadros_tasas_indicadores_eph' (¿cambió el nombre?).")
        return None
    n = nombres[-1]
    if not (PUBS / n).exists():
        contenido = bajar(FTP + n)
        if contenido[:4] != b"\xd0\xcf\x11\xe0":   # un .xls de verdad; el INDEC devuelve una página HTML si no existe
            informe.append(f"{n}: el INDEC no devolvió una planilla.")
            return None
        (PUBS / n).write_bytes(contenido)
        informe.append(f"Planilla nueva bajada: {n}  <-- NUEVO")
    return n


def leer(f):
    """Tasa de desocupación por trimestre en el cuadro 1.1 de una planilla ({'2026Q2': 7.9, ...})."""
    x = pd.read_excel(f, sheet_name=None, header=None)
    d = x[next(k for k in x if k.strip().lower().startswith("cuadro 1.1"))]
    fy = next(i for i, r in d.iterrows() if any(re.search(r"A.o \d{4}", str(v)) for v in r))
    anio, cols = None, {}
    for j in range(d.shape[1]):
        a = re.search(r"A.o (\d{4})", str(d.iat[fy, j]))
        if a:
            anio = int(a.group(1))
        q = re.match(r"\s*(\d)", str(d.iat[fy + 1, j]))
        if anio and q and "trim" in str(d.iat[fy + 1, j]).lower():
            cols[j] = f"{anio}Q{q.group(1)}"
    for _, r in d.iterrows():
        lab = str(r[0]).lower()
        if "desocupaci" in lab and "sub" not in lab:
            return {p: float(v) for j, p in cols.items() if pd.notna(v := pd.to_numeric(r[j], errors="coerce"))}
    raise ValueError("no está la fila de desocupación")


def tramo_2016(informe):
    try:
        d = json.loads(bajar(API))["data"]
        s = {str(pd.Period(f, "Q")): 100 * v for f, v in d if v is not None}
    except Exception as e:
        informe.append(f"datos.gob.ar no respondió ({e}): II a IV-2016 salen de la copia datos\\eph\\continua.csv")
        c = pd.read_csv(EPH / "continua.csv")
        s = {str(pd.Period(f, "Q")): 100 * v for f, v in zip(c["indice_tiempo"], c.iloc[:, 1]) if pd.notna(v)}
    return {q: round(v, 1) for q, v in s.items() if DESDE <= pd.Period(q, "Q") <= pd.Period("2016Q4", "Q")}


def sipa(informe):
    """Empleo registrado (SIPA, Secretaría de Trabajo vía datos.gob.ar), miles de trabajadores, sin estacionalidad.
    Solo para mostrar en el sitio (no entra al pronóstico: ver probar_sipa.py). Si falla, se avisa y se sigue."""
    try:
        d = json.loads(bajar(API_SIPA))["data"]
        df = pd.DataFrame(d, columns=["fecha", "total", "privados"])
        df["mes"] = pd.PeriodIndex(df["fecha"], freq="M").astype(str)
        df = df.dropna(subset=["total"])[["mes", "total", "privados"]]
        df.to_parquet(CARPETA / "sipa.parquet", index=False)
        u = df.iloc[-1]
        a = df[df["mes"] == str(pd.Period(u["mes"], "M") - 12)]
        ia = f" ({(u['total'] / a['total'].iloc[0] - 1) * 100:+.1f}% interanual)" if len(a) else ""
        informe.append(f"Empleo registrado SIPA hasta {u['mes']}: {u['total'] / 1000:.2f} millones de trabajadores{ia}")
    except Exception as e:
        informe.append(f"No se pudo actualizar el SIPA ({type(e).__name__}: {e}); queda el datos\\sipa.parquet anterior.")


def avisar(codigo, ultimo, nuevo, alertas, informe):
    if codigo == 0 and not nuevo:
        return
    clave = f"{codigo}|{ultimo}|" + ",".join(sorted(f"{a['codigo']}:{a['trimestre']}" for a in alertas))
    marca = CARPETA / "ultimo_aviso_eph.txt"
    if marca.exists() and marca.read_text(encoding="utf-8") == clave:
        return
    marca.write_text(clave, encoding="utf-8")
    titulo, icono, msg = {0: ("EPH: todo OK", 0x40, f"Se cargó la desocupación de {ultimo}. Sin errores."),
                          1: ("EPH: hay alertas para revisar", 0x30, "Abrí datos\\informe_eph.txt o pedile a Claude que lo revise."),
                          2: ("EPH: no se pudo actualizar", 0x10, "\n".join(informe[-5:])[:600])}[codigo]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, icono | 0x1000)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Actualiza la tasa de desocupación de la EPH (INDEC)")
    ap.add_argument("--aviso", action="store_true", help="cartel de Windows solo si hay novedades o errores")
    args = ap.parse_args()
    informe = [f"=== ACTUALIZACIÓN EPH: TASA DE DESOCUPACIÓN | {datetime.now():%Y-%m-%d %H:%M} ==="]
    codigo, alertas, ultimo, nuevo = 0, [], "-", False
    try:
        PUBS.mkdir(parents=True, exist_ok=True)
        buscar_nueva(informe)
        filas = []
        for f in sorted(PUBS.glob("cuadros_tasas_indicadores_eph_*.xls")):
            m, a = re.search(r"_(\d\d)_(\d\d)\.xls", f.name).groups()
            try:
                filas += [{"publicacion": f"20{a}-{m}", "trimestre": q, "desocupacion": v} for q, v in leer(f).items()]
            except Exception as e:
                alertas.append({"codigo": "D04", "trimestre": "-", "detalle": f"{f.name}: {e}"})
        if not filas:
            raise RuntimeError("no se pudo leer ninguna planilla")
        V = pd.DataFrame(filas).sort_values(["trimestre", "publicacion"])
        V.to_csv(EPH / "vintages_desocupacion.csv", index=False, encoding="utf-8-sig")
        for q, z in V.groupby("trimestre"):
            if z["desocupacion"].max() - z["desocupacion"].min() > TOL:
                alertas.append({"codigo": "D01", "trimestre": q, "detalle": " → ".join(f"{p}: {v:.2f}" for p, v in
                                                                                         zip(z["publicacion"], z["desocupacion"]))})
        ult_pub = V.groupby("trimestre").last()
        prim_pub = V.groupby("trimestre")["publicacion"].first()
        serie = {q: round(v, 1) for q, v in ult_pub["desocupacion"].items()}
        for q, v in tramo_2016(informe).items():
            serie.setdefault(q, v)
        S = pd.DataFrame({"trimestre": sorted(serie)})
        S["periodo"] = pd.PeriodIndex(S["trimestre"], freq="Q")
        S = S[S["periodo"] >= DESDE]
        S["desocupacion"] = S["trimestre"].map(serie)
        S["regla"] = [str((p.asfreq("M", "e") + 3)) for p in S["periodo"]]
        S["publicado"] = [prim_pub[q] if q in prim_pub and prim_pub[q] <= r else r for q, r in zip(S["trimestre"], S["regla"])]
        S["publicado_fuente"] = ["planilla" if q in prim_pub and prim_pub[q] <= r else "regla" for q, r in zip(S["trimestre"], S["regla"])]
        S["fuente"] = ["INDEC planilla " + str(ult_pub["publicacion"].get(q)) if q in ult_pub.index else "datos.gob.ar 45.2_ECTDT_0_T_33"
                       for q in S["trimestre"]]
        for p in pd.period_range(S["periodo"].min(), S["periodo"].max(), freq="Q").difference(pd.PeriodIndex(S["periodo"])):
            alertas.append({"codigo": "D02", "trimestre": str(p), "detalle": "sin dato"})
        for _, r in S[(S["desocupacion"] < 2) | (S["desocupacion"] > 30)].iterrows():
            alertas.append({"codigo": "D03", "trimestre": r["trimestre"], "detalle": f"{r['desocupacion']}"})
        previo = pd.read_parquet(SALIDA) if SALIDA.exists() else None
        nuevos = sorted(set(S["trimestre"]) - set(previo["trimestre"])) if previo is not None else []
        nuevo = bool(nuevos)
        S.drop(columns="periodo").to_parquet(SALIDA, index=False)
        vistos = set(pd.read_csv(HISTORIAL, dtype=str)["trimestre"]) if HISTORIAL.exists() else set()
        an = [q for q in S["trimestre"] if q not in vistos]
        if an:
            pd.DataFrame({"trimestre": an, "primera_vez_visto": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          "tipo": "observado" if vistos else "carga_inicial"}).to_csv(
                HISTORIAL, mode="a", header=not HISTORIAL.exists(), index=False, encoding="utf-8-sig")
        u = S.iloc[-1]
        ultimo = u["trimestre"]
        informe.append(f"Planillas leídas: {V['publicacion'].nunique()} ({V['publicacion'].min()} a {V['publicacion'].max()})")
        informe.append(f"Trimestres cargados: {len(S)} ({S['trimestre'].iloc[0]} a {ultimo})")
        informe.append(f"Último trimestre con dato: {ultimo}  |  desocupación {u['desocupacion']:.1f}%  (publicado {u['publicado']})")
        informe.append(f"Trimestres nuevos desde la corrida anterior: {', '.join(nuevos)}  <-- NUEVO" if nuevos else
                       "Sin trimestres nuevos desde la corrida anterior." if previo is not None else "Primera carga de esta serie.")
        informe.append("Archivo generado: datos\\desempleo.parquet")
        sipa(informe)
        informe += ["", "Controles:"] + [f"  {c} {n}: {sum(a['codigo'] == c for a in alertas)} casos  ({d})" for c, (n, d) in CONTROLES.items()]
        informe += ["", "=" * 60]
        if alertas:
            codigo = 1
            informe.append(f"ALERTAS: {len(alertas)} para revisar")
            informe += [f"  - {a['codigo']} {CONTROLES[a['codigo']][0]} | {a['trimestre']} | {a['detalle']}" for a in alertas[:15]]
        else:
            informe.append("ALERTAS: ninguna. Todo en orden.")
    except Exception as e:
        informe += ["", "=" * 60, f"FALLA INESPERADA: {type(e).__name__}: {e}", "No se modificó datos\\desempleo.parquet."]
        codigo = 2
    texto = "\n".join(informe)
    print(texto)
    INFORME.write_text(texto, encoding="utf-8")
    if args.aviso:
        avisar(codigo, ultimo, nuevo, alertas, informe)
    sys.exit(codigo)


if __name__ == "__main__":
    main()
