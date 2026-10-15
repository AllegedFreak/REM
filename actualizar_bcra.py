# actualizar_bcra.py
# Actualiza series del BCRA (tipo de cambio, tasas y oferta monetaria) desde la API oficial de Estadísticas Monetarias v4.0.
#   1) verifica que cada código (idVariable) siga siendo la serie que esperamos
#   2) baja solo lo nuevo (primera vez: toda la historia) y guarda una copia fechada de lo descargado
#   3) controla: datos históricos modificados, valores atípicos nuevos, series que dejaron de actualizarse,
#      fechas repetidas y valores vacíos
#   4) guarda datos/bcra_series.parquet (diario, formato largo) y datos/bcra_series_mensual.parquet
#
# Uso:
#   py -3.13 actualizar_bcra.py              -> actualiza
#   pythonw.exe actualizar_bcra.py --aviso   -> para el Programador de tareas (cartel solo si hay novedad)
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = error
import argparse
import json
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"

# nombre propio -> id de la API, texto que debe contener su descripción, unidad y si sigue publicándose
SERIES = {
    "tc_mayorista_a3500":     dict(id=5,   clave="mayorista",         unidad="$/USD", activa=True),
    "tc_minorista_bna":       dict(id=4,   clave="minorista",         unidad="$/USD", activa=True),
    "badlar_tna":             dict(id=7,   clave="badlar",            unidad="TNA %", activa=True),
    "tamar_tna":              dict(id=44,  clave="tamar",             unidad="TNA %", activa=True),
    "plazo_fijo_30d_tna":     dict(id=12,  clave="30 dias",           unidad="TNA %", activa=True),
    "politica_monetaria_tna": dict(id=160, clave="politica monetaria", unidad="TNA %", activa=False),
    "pase_pasivo_1d_tna":     dict(id=162, clave="pases pasivos",     unidad="TNA %", activa=False),
    "pase_pasivo_7d_tna":     dict(id=163, clave="7 dias",            unidad="TNA %", activa=False),
    "lebac_leliq_1m_tna":     dict(id=166, clave="lebac",             unidad="TNA %", activa=False),
    # oferta monetaria: saldos diarios en millones de $; para el análisis usar el promedio mensual
    "base_monetaria":         dict(id=15,  clave="base monetaria",    unidad="mill $", activa=True),
    "billetes_publico":       dict(id=17,  clave="billetes y monedas", unidad="mill $", activa=True),
    "m2_pesos":               dict(id=109, clave="m2",                unidad="mill $", activa=True),
}
SUPERPOSICION_DIAS = 45     # en cada corrida se vuelven a bajar los últimos 45 días para detectar revisiones
DIAS_SIN_ACTUALIZAR = 10    # alerta si una serie activa no tiene datos de los últimos 10 días
SALTO_MAXIMO = 0.25         # alerta si un dato NUEVO cambia más de 25% respecto del anterior

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
COPIAS = CARPETA / "copias"
SALIDA = CARPETA / "bcra_series.parquet"
MENSUAL = CARPETA / "bcra_series_mensual.parquet"
CATALOGO = CARPETA / "bcra_series_catalogo.csv"
INFORME = CARPETA / "informe_bcra.txt"
ALERTAS_CSV = CARPETA / "alertas_bcra.csv"
HISTORIAL = CARPETA / "historial_bcra.csv"
HOY = datetime.now().strftime("%Y-%m-%d")

CONTROLES = {
    "B01": ("historico_modificado", "Un dato ya publicado cambió respecto de la corrida anterior (revisión)"),
    "B02": ("serie_cambio_de_significado", "El código ya no corresponde a la descripción esperada: NO se cargó esa serie"),
    "B03": ("serie_desactualizada", "Una serie activa no tiene datos recientes"),
    "B04": ("valor_atipico", "Un dato nuevo cambia más de 25% respecto del anterior"),
    "B05": ("fecha_repetida_o_vacio", "Fecha repetida o valor vacío"),
}


class ErrorAPI(Exception):
    pass


def sin_acentos(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def pedir(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    for intento in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            cuerpo = e.read()[:300].decode("utf-8", "replace")
            if e.code == 429 and intento < 2:
                time.sleep(5 * (intento + 1))
                continue
            if e.code == 410:
                raise ErrorAPI(f"El BCRA dio de baja esta versión de la API (HTTP 410): {cuerpo}")
            raise ErrorAPI(f"La API del BCRA respondió HTTP {e.code}: {cuerpo}")
        except Exception as e:
            if intento < 2:
                time.sleep(3)
                continue
            raise ErrorAPI(f"No se pudo conectar con la API del BCRA ({type(e).__name__}: {e})")


def bajar_serie(vid, desde, copia):
    """Devuelve DataFrame fecha, valor. Pagina de a 3000 datos."""
    filas, offset = [], 0
    while True:
        url = f"{API}/{vid}?limit=3000&offset={offset}" + (f"&desde={desde}" if desde else "")
        datos = pedir(url)
        copia.append({"url": url, "respuesta": datos})
        try:
            total = datos["metadata"]["resultset"]["count"]
            detalle = datos["results"][0]["detalle"] if datos["results"] else []
        except Exception:
            raise ErrorAPI(f"El formato de la respuesta de la API cambió (id {vid}). Pegale este mensaje a Claude.")
        filas += [(d["fecha"], d["valor"]) for d in detalle]
        offset += 3000
        if offset >= total or not detalle:
            break
        time.sleep(0.4)
    df = pd.DataFrame(filas, columns=["fecha", "valor"])
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    return df


def avisar(codigo, nuevos, alertas, informe):
    if codigo == 0:   # las series diarias traen datos nuevos todos los días: solo se avisa si hay alertas o error
        return
    clave = f"{codigo}|" + ",".join(sorted(f"{a['codigo']}:{a['serie']}:{a['fecha']}" for a in alertas))
    marca = CARPETA / "ultimo_aviso_bcra.txt"
    if marca.exists() and marca.read_text(encoding="utf-8") == clave:
        return
    marca.write_text(clave, encoding="utf-8")
    if codigo == 0:
        titulo, icono, msg = "BCRA series: todo OK", 0x40, "Se cargaron datos nuevos del BCRA. Sin errores."
    elif codigo == 1:
        titulo, icono = "BCRA series: hay errores para revisar", 0x30
        msg = (f"Hay {len(alertas)} alertas (tipos: {', '.join(sorted({a['codigo'] for a in alertas}))}).\n\n"
               "Abrí datos\\informe_bcra.txt o pedile a Claude que lo revise.")
    else:
        titulo, icono, msg = "BCRA series: no se pudo actualizar", 0x10, "\n".join(informe[-5:])[:600]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, icono | 0x1000)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Actualiza series del BCRA")
    ap.add_argument("--aviso", action="store_true", help="cartel de Windows solo si hay novedades")
    args = ap.parse_args()

    informe = [f"=== ACTUALIZACIÓN SERIES BCRA | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    codigo, alertas, nuevos_fechas = 0, [], []
    try:
        CARPETA.mkdir(exist_ok=True)
        COPIAS.mkdir(exist_ok=True)
        almacenado = pd.read_parquet(SALIDA) if SALIDA.exists() else pd.DataFrame(columns=["fecha", "serie", "id_variable", "valor"])
        almacenado["fecha"] = pd.to_datetime(almacenado["fecha"]).dt.date
        copia, catalogo, partes, resumen = [], [], [], []

        def alerta(cod, serie, fecha, detalle):
            alertas.append({"codigo": cod, "serie": serie, "fecha": str(fecha), "detalle": detalle})

        for nombre, cfg in SERIES.items():
            vid = cfg["id"]
            meta = pedir(f"{API}?idVariable={vid}")
            copia.append({"url": f"{API}?idVariable={vid}", "respuesta": meta})
            filas_meta = meta.get("results", [])
            if not filas_meta:
                alerta("B02", nombre, "", f"el código {vid} ya no existe en la API")
                partes.append(almacenado[almacenado["serie"] == nombre])
                continue
            m = filas_meta[0]
            if cfg["clave"] not in sin_acentos(m.get("descripcion", "")):
                alerta("B02", nombre, "", f"el código {vid} ahora dice '{m.get('descripcion')}' y esperábamos algo con '{cfg['clave']}'. No se actualizó.")
                partes.append(almacenado[almacenado["serie"] == nombre])
                continue
            catalogo.append({"serie": nombre, "id_variable": vid, "descripcion": str(m.get("descripcion")).strip(),
                             "unidad_api": m.get("unidadExpresion"), "unidad": cfg["unidad"], "activa": cfg["activa"],
                             "primera_fecha": m.get("primerFechaInformada"), "ultima_fecha_api": m.get("ultFechaInformada")})

            previo = almacenado[almacenado["serie"] == nombre].sort_values("fecha")
            desde = None
            if len(previo):
                desde = (max(previo["fecha"]) - timedelta(days=SUPERPOSICION_DIAS)).isoformat()
            nuevo = bajar_serie(vid, desde, copia)
            nuevo = nuevo.sort_values("fecha")

            for f in nuevo.loc[nuevo["fecha"].duplicated(keep=False), "fecha"].drop_duplicates():
                alerta("B05", nombre, f, "fecha repetida")
            for f in nuevo.loc[nuevo["valor"].isna(), "fecha"]:
                alerta("B05", nombre, f, "valor vacío")
            nuevo = nuevo.drop_duplicates("fecha", keep="first").dropna(subset=["valor"])

            if len(previo):
                limite = date.fromisoformat(desde)
                solape = previo[previo["fecha"] >= limite].merge(nuevo, on="fecha", suffixes=("_ant", ""))
                dif = (solape["valor"] - solape["valor_ant"]).abs() > 1e-6 * solape["valor_ant"].abs().clip(lower=1)
                for _, r in solape[dif].iterrows():
                    alerta("B01", nombre, r["fecha"], f"antes {r['valor_ant']}, ahora {r['valor']}")
                ultimo_prev = max(previo["fecha"])
                agregados = nuevo[nuevo["fecha"] > ultimo_prev].copy()
                # valores atípicos solo en los datos nuevos
                ref = pd.concat([previo[["fecha", "valor"]], agregados]).sort_values("fecha").reset_index(drop=True)
                ref["cambio"] = ref["valor"].pct_change().abs()
                for _, r in ref[ref["fecha"] > ultimo_prev].iterrows():
                    if pd.notna(r["cambio"]) and r["cambio"] > SALTO_MAXIMO:
                        alerta("B04", nombre, r["fecha"], f"cambio de {r['cambio'] * 100:.0f}% respecto del dato anterior")
                conservar = previo[previo["fecha"] < limite][["fecha", "valor"]]
                total = pd.concat([conservar, nuevo[["fecha", "valor"]]])
                nuevos_fechas += list(agregados["fecha"])
                n_nuevos = len(agregados)
            else:
                total = nuevo[["fecha", "valor"]]
                n_nuevos = len(total)
                nuevos_fechas += list(total["fecha"])
            total = total.sort_values("fecha").drop_duplicates("fecha", keep="last")
            total["serie"], total["id_variable"] = nombre, vid
            partes.append(total[["fecha", "serie", "id_variable", "valor"]])

            ult = total.iloc[-1]
            if cfg["activa"] and (date.today() - ult["fecha"]).days > DIAS_SIN_ACTUALIZAR:
                alerta("B03", nombre, ult["fecha"], f"último dato del {ult['fecha']}: hace más de {DIAS_SIN_ACTUALIZAR} días")
            resumen.append((nombre, cfg["unidad"], len(total), total["fecha"].min(), ult["fecha"], ult["valor"], n_nuevos, cfg["activa"]))

        largo = pd.concat(partes, ignore_index=True).sort_values(["serie", "fecha"]).reset_index(drop=True)
        marcas = {}
        for a in alertas:
            if a["fecha"]:
                marcas.setdefault((a["serie"], a["fecha"]), set()).add(a["codigo"])
        largo["alerta_fuente"] = [";".join(sorted(marcas.get((s, str(f)), []))) for s, f in zip(largo["serie"], largo["fecha"])]
        largo.to_parquet(SALIDA, index=False)

        d = largo.copy()
        d["fecha"] = pd.to_datetime(d["fecha"])
        d["mes"] = (d["fecha"] + pd.offsets.MonthEnd(0)).dt.date
        mensual = d.groupby(["serie", "mes"]).agg(promedio=("valor", "mean"), fin_de_mes=("valor", "last"),
                                                 dias=("valor", "size"), ultimo_dia=("fecha", "max")).reset_index()
        mensual["ultimo_dia"] = mensual["ultimo_dia"].dt.date
        mensual.to_parquet(MENSUAL, index=False)
        pd.DataFrame(catalogo).to_csv(CATALOGO, index=False, encoding="utf-8-sig")
        if copia and (nuevos_fechas or not any(COPIAS.glob("bcra_*.json"))):
            (COPIAS / f"bcra_{HOY}.json").write_text(json.dumps(copia, ensure_ascii=False), encoding="utf-8")

        informe.append("")
        informe.append(f"{'serie':26} {'unidad':7} {'datos':>6}  {'desde':10}  {'último':10}  {'valor':>10}  nuevos")
        for n, u, c, d0, d1, v, nn, act in resumen:
            informe.append(f"{n:26} {u:7} {c:6}  {d0}  {d1}  {v:10.2f}  {nn:>4}{'' if act else '   (serie discontinuada)'}")
        informe.append("")
        informe.append("Controles:")
        for cod, (nom, desc) in CONTROLES.items():
            informe.append(f"  {cod} {nom}: {sum(a['codigo'] == cod for a in alertas)} casos  ({desc})")
        informe.append("")
        informe.append("Archivos: datos\\bcra_series.parquet (diario) y datos\\bcra_series_mensual.parquet (promedio y fin de mes)")
        informe.append("")
        informe.append("=" * 60)
        if alertas:
            codigo = 1
            pd.DataFrame(alertas).to_csv(ALERTAS_CSV, index=False, encoding="utf-8-sig")
            informe.append(f"ALERTAS: {len(alertas)} para revisar (detalle en datos\\alertas_bcra.csv)")
            for a in alertas[:15]:
                informe.append(f"  - {a['codigo']} {CONTROLES[a['codigo']][0]} | {a['serie']} | {a['fecha']} | {a['detalle']}")
            if len(alertas) > 15:
                informe.append(f"  ... y {len(alertas) - 15} más")
            informe.append("  Las filas afectadas quedan marcadas en alerta_fuente. No se corrigió ni se borró nada.")
        else:
            informe.append("ALERTAS: ninguna. Todo en orden.")
            if ALERTAS_CSV.exists():
                ALERTAS_CSV.unlink()
        pd.DataFrame([{"fecha_corrida": datetime.now().strftime("%Y-%m-%d %H:%M"), "datos_nuevos": len(nuevos_fechas),
                       "alertas": len(alertas)}]).to_csv(HISTORIAL, mode="a", header=not HISTORIAL.exists(),
                                                         index=False, encoding="utf-8-sig")
    except ErrorAPI as e:
        informe += ["", "=" * 60, f"ERROR: {e}", "No se modificó ningún archivo de salida."]
        codigo = 2
    except Exception as e:
        informe += ["", "=" * 60, f"FALLA INESPERADA: {type(e).__name__}: {e}"]
        codigo = 2

    texto = "\n".join(informe)
    print(texto)
    CARPETA.mkdir(exist_ok=True)
    INFORME.write_text(texto, encoding="utf-8")
    if args.aviso:
        avisar(codigo, nuevos_fechas, alertas, informe)
    sys.exit(codigo)


if __name__ == "__main__":
    main()