# actualizar_dolares.py
# Cotizaciones históricas de dólares paralelos/financieros desde argentinadatos.com (historia completa en un pedido):
#   blue, contadoconliqui (CCL), bolsa (MEP). La brecha se calcula con el CCL contra el A3500 del BCRA.
# La fuente REPITE EL ÚLTIMO VALOR cuando le falta un dato (ej.: mayorista congelado del 13-dic-2023 al 9-ene-2024),
# por eso se controla:
#   D01 tramo congelado: mismo valor 8+ días hábiles seguidos (CCL y MEP: esos días se marcan y no se usan en la brecha)
#   D02 CCL y MEP difieren más de 10% el mismo día
#   D03 dato nuevo que cambia más de 25% contra el anterior
#   D04 dato histórico modificado respecto de la corrida anterior
#   D05 serie desactualizada (último dato hace más de 5 días)
#   D07 fecha corrida: desde ~1-sep-2026 la fuente guarda cada cierre con la FECHA DEL DÍA SIGUIENTE (30-oct-2026: el
#       sábado ya no repite al viernes, trae el cierre del viernes, y el lunes repite al domingo; lo mismo en mayorista,
#       que contra el A3500 del BCRA confirma un día de corrimiento). Se detecta solo (corrimiento()) y se corrige:
#       desde el primer sábado corrido cada dato pasa al día anterior; la semana del cambio queda marcada (no se usa)
# Guarda datos/dolares.parquet (diario, días hábiles de mercado = días con A3500) y datos/dolares_mensual.parquet
# (promedio mensual y brecha CCL; un mes con menos de 10 días válidos queda vacío y se guarda cuántos días tuvo).
# Uso:
#   py -3.13 actualizar_dolares.py              -> actualiza
#   pythonw.exe actualizar_dolares.py --aviso   -> para el Programador de tareas (cartel solo si hay novedad)
# Código de salida: 0 = todo en orden | 1 = hay alertas | 2 = error
import argparse
import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.argentinadatos.com/v1/cotizaciones/dolares/{}"
CASAS = {"blue": "blue", "contadoconliqui": "ccl", "bolsa": "mep"}
RACHA_CONGELADA = 8
SALTO_MAXIMO = 0.25
DIF_CCL_MEP = 0.10
DIAS_SIN_ACTUALIZAR = 5
MIN_DIAS_MES = 10   # mínimo de días hábiles válidos para calcular un promedio mensual (si no, queda vacío)

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos"
COPIAS = CARPETA / "copias"
SALIDA = CARPETA / "dolares.parquet"
MENSUAL = CARPETA / "dolares_mensual.parquet"
INFORME = CARPETA / "informe_dolares.txt"
ALERTAS_CSV = CARPETA / "alertas_dolares.csv"
HISTORIAL = CARPETA / "historial_dolares.csv"
HOY = datetime.now().strftime("%Y-%m-%d")
CONTROLES = {
    "D01": ("tramo_congelado", f"Mismo valor {RACHA_CONGELADA}+ días hábiles seguidos (la fuente repite el último dato)"),
    "D02": ("ccl_vs_mep", f"CCL y MEP difieren más de {DIF_CCL_MEP:.0%} el mismo día"),
    "D03": ("valor_atipico", f"Dato nuevo que cambia más de {SALTO_MAXIMO:.0%} contra el anterior"),
    "D04": ("historico_modificado", "Un dato ya guardado cambió respecto de la corrida anterior"),
    "D05": ("serie_desactualizada", f"Último dato hace más de {DIAS_SIN_ACTUALIZAR} días"),
    "D06": ("fechas_perdidas", "Fechas guardadas que ya no vienen de la fuente (se conservan)"),
    "D07": ("fecha_corrida", "La fuente guarda cada cierre con la fecha del día siguiente: se corrige (y la semana del cambio no se usa)"),
}
SEMANAS_CORRIDAS = 3   # semanas seguidas con el patrón de fin de semana corrido para dar por corrida la fuente


class ErrorFuente(Exception):
    pass


def bajar(casa):
    req = urllib.request.Request(API.format(casa), headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            contenido = r.read()
    except Exception as e:
        raise ErrorFuente(f"No se pudo bajar {casa} de argentinadatos.com ({type(e).__name__}: {e})")
    try:
        datos = json.loads(contenido)
        df = pd.DataFrame(datos)[["fecha", "compra", "venta"]]
    except Exception:
        raise ErrorFuente(f"El formato de la respuesta de {casa} cambió. Pegale este mensaje a Claude.")
    return contenido, df


def corrimiento(crudos):
    """Prueba de fin de semana sobre CCL y MEP crudos (con sábados y domingos). Sin corrimiento, el sábado repite al viernes.
    Con corrimiento, el sábado trae el cierre del viernes (distinto del registro del viernes) y el lunes repite al domingo.
    Devuelve (corte, sabado_anterior) del tramo final de SEMANAS_CORRIDAS+ semanas corridas, o (None, None)."""
    d1 = pd.Timedelta(days=1)
    marcas = None
    for casa in ("contadoconliqui", "bolsa"):
        s = crudos[casa].set_index("fecha")["venta"]
        m = {sab: (s[sab] != s[sab - d1], s[sab + 2 * d1] == s[sab + d1]) for sab in s.index[s.index.dayofweek == 5]
             if {sab - d1, sab + d1, sab + 2 * d1} <= set(s.index)}
        marcas = m if marcas is None else {k: (v[0] or marcas[k][0], v[1] and marcas[k][1]) for k, v in m.items() if k in marcas}
    corrida = pd.Series({k: a and b for k, (a, b) in marcas.items()}).sort_index()
    if corrida.empty or not corrida.iloc[-1]:
        return None, None
    inicio = len(corrida) - (~corrida[::-1]).cumsum().eq(0).sum()   # primer sábado del tramo final corrido
    if len(corrida) - inicio < SEMANAS_CORRIDAS:
        return None, None
    corte = corrida.index[inicio]
    # desde dónde no se sabe: el último sábado ANTERIOR con el patrón viejo claro (sábado = viernes y lunes distinto del domingo)
    limpios = [k for k, (a, b) in marcas.items() if k < corte and not a and not b]
    return corte, (max(limpios) if limpios else corte - pd.Timedelta(days=7))


def rachas_congeladas(s):
    """Fechas que pertenecen a tramos con el mismo valor RACHA_CONGELADA+ días hábiles seguidos."""
    grupo = (s != s.shift()).cumsum()
    tam = s.groupby(grupo).transform("size")
    return s.index[tam >= RACHA_CONGELADA]


def avisar(codigo, alertas, informe):
    if codigo == 0:
        return
    clave = f"{codigo}|" + ",".join(sorted(f"{a['codigo']}:{a['serie']}:{a['fecha']}" for a in alertas))
    marca = CARPETA / "ultimo_aviso_dolares.txt"
    if marca.exists() and marca.read_text(encoding="utf-8") == clave:
        return
    marca.write_text(clave, encoding="utf-8")
    if codigo == 1:
        titulo, icono = "Dólares: hay alertas para revisar", 0x30
        msg = (f"Hay {len(alertas)} alertas nuevas (tipos: {', '.join(sorted({a['codigo'] for a in alertas}))}).\n\n"
               "Abrí datos\\informe_dolares.txt o pedile a Claude que lo revise.")
    else:
        titulo, icono, msg = "Dólares: no se pudo actualizar", 0x10, "\n".join(informe[-5:])[:600]
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, icono | 0x1000)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Actualiza cotizaciones de dólares (argentinadatos.com)")
    ap.add_argument("--aviso", action="store_true", help="cartel de Windows solo si hay novedades")
    args = ap.parse_args()
    informe = [f"=== ACTUALIZACIÓN DÓLARES (argentinadatos.com) | {datetime.now().strftime('%Y-%m-%d %H:%M')} ==="]
    codigo, alertas = 0, []
    try:
        CARPETA.mkdir(exist_ok=True)
        COPIAS.mkdir(exist_ok=True)
        anterior = pd.read_parquet(SALIDA) if SALIDA.exists() else None

        def alerta(cod, serie, fecha, detalle, historica=False):
            alertas.append({"codigo": cod, "serie": serie, "fecha": str(fecha)[:10], "detalle": detalle, "historica": historica})

        # calendario de mercado = días con dato del A3500 del BCRA. La fuente rellena fines de semana (100%) y feriados (99%)
        # con el último valor: esos días se descartan. Después del último dato del BCRA, se usan los días de semana.
        bcra = pd.read_parquet(CARPETA / "bcra_series.parquet")
        a3500 = bcra[bcra["serie"] == "tc_mayorista_a3500"].assign(fecha=lambda x: pd.to_datetime(x["fecha"])).set_index("fecha")["valor"]
        habiles = set(a3500.index)
        ultimo_bcra = a3500.index.max()
        partes, copia, crudos = [], {}, {}
        for casa in CASAS:
            contenido, df = bajar(casa)
            copia[casa] = json.loads(contenido)
            df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
            crudos[casa] = df.dropna(subset=["fecha", "venta"]).drop_duplicates("fecha", keep="last").sort_values("fecha")
        corte, sab_ant = corrimiento(crudos)
        dudosas = set()
        marca_corr = CARPETA / "dolares_corrimiento.txt"   # avisa solo cuando el corrimiento aparece o cambia
        texto_corr = (f"desde el {corte.date()} cada dato es del día anterior: corregido; "
                      f"{(sab_ant + pd.Timedelta(days=1)).date()} a {(corte - pd.Timedelta(days=2)).date()} no se usan") if corte is not None else ""
        if texto_corr != (marca_corr.read_text(encoding="utf-8") if marca_corr.exists() else ""):
            if corte is not None:
                alerta("D07", "todas", corte, texto_corr, historica=True)
            marca_corr.write_text(texto_corr, encoding="utf-8")
        for casa, nombre in CASAS.items():
            df = crudos[casa].copy()
            if corte is not None:
                df.loc[df["fecha"] >= corte, "fecha"] -= pd.Timedelta(days=1)    # el sábado pasa a viernes, etc.
                df = df.drop_duplicates("fecha", keep="last")                     # el viernes corregido reemplaza al original
                dudosas = set(pd.date_range(sab_ant + pd.Timedelta(days=1), corte - pd.Timedelta(days=2)))
            de_mercado = df["fecha"].isin(habiles) | ((df["fecha"] > ultimo_bcra) & (df["fecha"].dt.weekday < 5))
            df = df[de_mercado & (df["fecha"] >= a3500.index.min())].sort_values("fecha")
            df["serie"] = nombre
            partes.append(df)
        d = pd.concat(partes, ignore_index=True)
        d["congelado"] = d["fecha"].isin(dudosas)   # semana del cambio de fecha (D07): no se usa en la brecha ni en promedios
        for nombre, g in d.groupby("serie"):
            s = g.set_index("fecha")["venta"]
            malas = rachas_congeladas(s)
            d.loc[(d["serie"] == nombre) & d["fecha"].isin(malas), "congelado"] = True
            if nombre != "blue":   # el blue viejo viene en pesos enteros y tiene tramos planos: se informa, no se alerta
                nuevas = [f for f in malas if anterior is None or f > pd.Timestamp(anterior["fecha"].max()) - pd.Timedelta(days=60)]
                for f in sorted(set(nuevas))[:1] if nuevas else []:
                    alerta("D01", nombre, f, f"tramo congelado de {RACHA_CONGELADA}+ días hábiles (se excluye de la brecha)")
            ult = s.index.max()
            if (pd.Timestamp(date.today()) - ult).days > DIAS_SIN_ACTUALIZAR:
                alerta("D05", nombre, ult, f"último dato del {ult.date()}")
            if anterior is not None:
                prev = anterior[anterior["serie"] == nombre].set_index("fecha")["venta"]
                prev.index = pd.to_datetime(prev.index)
                comun = s.index.intersection(prev.index)
                dif = (s[comun] - prev[comun]).abs() > 1e-6 * prev[comun].abs().clip(lower=1)
                for f in comun[dif.values][:10]:
                    alerta("D04", nombre, f, f"antes {prev[f]}, ahora {s[f]}", historica=True)
                nuevos = s[s.index > prev.index.max()]
                saltos = (s / s.shift(1) - 1).abs()
                for f in nuevos.index:
                    if saltos.get(f, 0) > SALTO_MAXIMO:
                        alerta("D03", nombre, f, f"cambio de {saltos[f]:.0%} contra el día anterior")
        piv = d.pivot(index="fecha", columns="serie", values="venta")
        ambos = piv[["ccl", "mep"]].dropna()
        difcm = (ambos["ccl"] / ambos["mep"] - 1).abs()
        corte = pd.Timestamp(anterior["fecha"].max()) if anterior is not None else pd.Timestamp("1900-01-01")
        for f in difcm[(difcm > DIF_CCL_MEP) & (difcm.index > corte)].index[:10]:
            alerta("D02", "ccl", f, f"CCL {ambos.loc[f, 'ccl']:.1f} vs MEP {ambos.loc[f, 'mep']:.1f}")
        # nunca perder lo guardado: fechas que estaban y ya no vienen de la fuente se conservan (y se avisa)
        if anterior is not None:
            prev = anterior.assign(fecha=pd.to_datetime(anterior["fecha"]))
            clave_nueva = set(zip(d["serie"], d["fecha"]))
            perdidas = prev[[(s_, f_) not in clave_nueva for s_, f_ in zip(prev["serie"], prev["fecha"])]]
            if len(perdidas):
                for nombre, g in perdidas.groupby("serie"):
                    alerta("D06", nombre, g["fecha"].min(), f"{len(g)} fechas guardadas ya no vienen de la fuente: se conservan", historica=True)
                d = pd.concat([d, perdidas], ignore_index=True).sort_values(["serie", "fecha"])
        d.to_parquet(SALIDA, index=False)
        if copia:
            (COPIAS / f"dolares_{HOY}.json").write_text(json.dumps(copia), encoding="utf-8")

        # ---- mensual y brecha (CCL sin días congelados contra A3500 del BCRA, días hábiles comunes) ----
        ccl = d[(d["serie"] == "ccl") & ~d["congelado"]].set_index("fecha")["venta"]
        j = pd.concat([ccl.rename("ccl"), a3500.rename("a3500")], axis=1, join="inner")
        j["brecha_ccl"] = (j["ccl"] / j["a3500"] - 1) * 100
        mens = d[~d["congelado"]].assign(mes=lambda x: x["fecha"].dt.to_period("M")).groupby(["mes", "serie"])["venta"].mean().unstack()
        mens["brecha_ccl"] = j.groupby(j.index.to_period("M"))["brecha_ccl"].mean()
        mens["dias_brecha"] = j.groupby(j.index.to_period("M"))["brecha_ccl"].size()
        # regla de huecos: con menos de MIN_DIAS_MES días válidos el promedio no se usa (queda vacío; no se rellena)
        mes_actual = pd.Period(date.today(), "M")
        pocos = (mens["dias_brecha"].fillna(0) < MIN_DIAS_MES) & (mens.index != mes_actual)
        mens.loc[pocos, "brecha_ccl"] = np.nan
        dias_serie = d[~d["congelado"]].assign(mes=lambda x: x["fecha"].dt.to_period("M")).groupby(["mes", "serie"]).size().unstack()
        for col in [c for c in dias_serie.columns if c in mens.columns]:
            mens[f"dias_{col}"] = dias_serie[col]
            mens.loc[(mens[f"dias_{col}"].fillna(0) < MIN_DIAS_MES) & (mens.index != mes_actual), col] = np.nan
        mens.index = mens.index.to_timestamp(how="end").date
        mens.index.name = "mes"
        mens.reset_index().to_parquet(MENSUAL, index=False)

        informe.append("")
        informe.append(f"{'serie':6} {'datos':>6}  {'desde':10}  {'último':10}  {'valor':>9}  {'días congelados':>15}")
        for nombre, g in d.groupby("serie"):
            u = g.iloc[-1]
            informe.append(f"{nombre:6} {len(g):6}  {g['fecha'].min().date()}  {u['fecha'].date()}  {u['venta']:9.2f}  {int(g['congelado'].sum()):15}")
        ub = j.iloc[-1]
        informe.append(f"Brecha CCL vs A3500: {ub['brecha_ccl']:.1f}% ({j.index[-1].date()}); "
                       f"promedio del último mes completo: {mens['brecha_ccl'].dropna().iloc[-2]:.1f}%")
        if texto_corr:
            informe.append(f"Fecha corrida de la fuente (D07): {texto_corr}")
        informe.append("Archivos: datos\\dolares.parquet (diario) y datos\\dolares_mensual.parquet (promedio mensual y brecha)")
        informe.append("")
        informe.append("Controles:")
        for cod, (nom, desc) in CONTROLES.items():
            informe.append(f"  {cod} {nom}: {sum(a['codigo'] == cod for a in alertas)} casos  ({desc})")
        informe.append("")
        informe.append("=" * 60)
        if alertas:
            codigo = 1
            pd.DataFrame(alertas).to_csv(ALERTAS_CSV, index=False, encoding="utf-8-sig")
            informe.append(f"ALERTAS: {len(alertas)} para revisar (detalle en datos\\alertas_dolares.csv)")
            for a in alertas[:15]:
                informe.append(f"  - {a['codigo']} {CONTROLES[a['codigo']][0]} | {a['serie']} | {a['fecha']} | {a['detalle']}")
            informe.append("  Los días congelados quedan marcados (columna congelado) y no se usan en la brecha. No se borró nada.")
        else:
            informe.append("ALERTAS: ninguna. Todo en orden.")
            if ALERTAS_CSV.exists():
                ALERTAS_CSV.unlink()
        pd.DataFrame([{"fecha_corrida": datetime.now().strftime("%Y-%m-%d %H:%M"), "alertas": len(alertas)}]).to_csv(
            HISTORIAL, mode="a", header=not HISTORIAL.exists(), index=False, encoding="utf-8-sig")
    except ErrorFuente as e:
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
        avisar(codigo, alertas, informe)
    sys.exit(codigo)


if __name__ == "__main__":
    main()
