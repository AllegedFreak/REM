# emitir_pronostico.py
# "Pronóstico sellado": una vez por mes (primer día del relevamiento del REM), antes de que el BCRA publique el REM de ese mes, congela
# nuestro pronóstico registrado y deja pruebas de fecha que no controlamos nosotros:
#   1. PDF con todos los pronósticos (y CSV con los mismos datos), con fecha y hora de registro;
#   2. huella SHA-256 del PDF y del CSV;
#   3. sello OpenTimestamps de ambos (anclado en Bitcoin unas horas después; ver sello_ots.py);
#   4. copia en el Archivo de Internet (web.archive.org) cuando el sitio esté publicado;
#   5. imagen PNG y texto de la publicación de LinkedIn (publicacion_linkedin.txt) con la huella del PDF.
# Otro sello del mismo relevamiento (30-oct-2026, Marcos): n.º 1B, 1C... con archivos pronostico_AAAA-MM_B.* y
# publicacion_linkedin_B.txt en la misma carpeta; nunca pisa los del sello anterior.
# La publicación la hace Marcos desde su cuenta de LinkedIn (decisión 24-oct-2026: LinkedIn, no X); después se comparte esa
# misma publicación cuando sale el REM y cuando sale el dato real. Un sello por mes, numerado, nunca se pisa.
# Registro: datos\sellos\registro_sellos.csv (post_url, post_rem_url y post_real_url se completan con
# --linkedin / --linkedin-rem / --linkedin-real).
# Uso: py -3.13 emitir_pronostico.py --emitir       (SOLO a mano, cuando Marcos y Claude deciden emitir, después de revisar todo;
#                                                     la fecha está agendada. Decisión de Marcos, 30-oct-2026)
#      py -3.13 emitir_pronostico.py [--aviso]       (en actualizar_todo: NO emite; solo completa los sellos de Bitcoin y del
#                                                     Archivo de Internet de los ya emitidos)
#      py -3.13 emitir_pronostico.py --prueba        (arma todo en datos\sellos\_prueba, sin numerar, sellar ni registrar)
#      py -3.13 emitir_pronostico.py --linkedin AAAA-MM URL   (y --linkedin-rem / --linkedin-real)
import hashlib
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
C = BASE / "datos"
SELLOS = C / "sellos"
REGISTRO = SELLOS / "registro_sellos.csv"
SITIO = "https://www.datosconmetodo.com"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
# El BCRA hace el relevamiento del REM en los últimos 3 días hábiles del mes y lo publica a comienzos del mes siguiente.
# Se sella el PRIMER día de esa ventana (decisión de Marcos, 24-oct-2026: oct-2026 = miércoles 28).
DIAS_VENTANA = 3
NO_HABILES = {"2026-12-24", "2026-12-31", "2027-12-24", "2027-12-31"}     # completar con feriados de fin de mes si caen


def dia_emision(mes):
    """Primer día hábil de la ventana del relevamiento (los últimos DIAS_VENTANA hábiles del mes)."""
    p = pd.Period(mes, "M")
    dias = pd.bdate_range(p.start_time, p.end_time.normalize())
    habiles = [d for d in dias if d.strftime("%Y-%m-%d") not in NO_HABILES]
    return habiles[-DIAS_VENTANA]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
MES3 = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
COLUMNAS = ["n", "relevamiento", "registrado", "pdf", "sha256_pdf", "csv", "sha256_csv", "ots_pdf", "ots_csv", "bitcoin",
            "wayback_pdf", "post_texto", "post_url", "post_rem_url", "post_real_url"]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def numero(reg, rel):
    """Número del sello: uno por relevamiento (1, 2, 3...); otro sello del mismo relevamiento lleva una letra: 1B, 1C...
    (Marcos, 30-oct-2026: "Pronóstico n.º 1B · oct-2026"). Devuelve (número, sufijo del nombre de archivo)."""
    ya = reg[reg["relevamiento"] == str(rel)]
    if not len(ya):
        return str(reg["relevamiento"].nunique() + 1), ""
    letra = chr(ord("A") + len(ya))
    return f"{''.join(c for c in str(ya['n'].iloc[0]) if c.isdigit())}{letra}", f"_{letra}"


def registro():
    if REGISTRO.exists():
        return pd.read_csv(REGISTRO, encoding="utf-8-sig", dtype=str).fillna("")
    return pd.DataFrame(columns=COLUMNAS)


def mes_txt(p):
    return f"{MESES[p.month - 1]} de {p.year}"


def per_txt(s):
    s = str(s)
    if len(s) == 7 and s[4] == "-":
        return f"{MES3[int(s[5:7]) - 1]}-{s[:4]}"
    return s


def fmt(v, d, pre="", suf=""):
    if v is None or pd.isna(v):
        return "—"
    txt = f"{abs(v):,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if v < 0 else ''}{pre}{txt}{suf}"


FORMATO = {"inflacion": (2, "", "%"), "dolar": (0, "$", ""), "pib": (2, "", "%"), "tasa": (2, "", "%"), "fiscal": (0, "", ""),
           "nucleo": (2, "", "%"), "pib_anual": (2, "", "%"),
           "exportaciones": (0, "", ""), "importaciones": (0, "", ""), "desempleo": (2, "", "%")}   # desocupación con 2 decimales (7,03 y 6,95; 30-oct-2026)


def edge(html, salida, pdf=True, ancho=1200, alto=675):
    args = [str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-pdf-header-footer", "--virtual-time-budget=5000"]
    args += [f"--print-to-pdf={salida}"] if pdf else [f"--window-size={ancho},{alto}", f"--screenshot={salida}"]
    subprocess.run(args + [html.as_uri()], capture_output=True, timeout=120)
    if not Path(salida).exists():
        raise RuntimeError(f"Edge no generó {Path(salida).name}")


CSS = """
:root{--tinta:#0b0b0b;--tinta2:#52514e;--tenue:#6f6d68;--linea:#e1e0d9;--acento:#2a78d6;--rem:#eb6834;--papel:#fcfcfb}
*{box-sizing:border-box} body{margin:0;font-family:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;color:var(--tinta);background:var(--papel)}
.mono{font-family:"IBM Plex Mono",Consolas,monospace}
.marca{display:flex;align-items:center;gap:10px;font-weight:600;font-size:18px}
.marca b{box-shadow:inset 0 -3px 0 var(--acento)} .marca span{font-weight:400;color:var(--tinta2)}
"""
LOGO = ('<svg width="28" height="28" viewBox="0 0 32 32"><rect x="1.5" y="1.5" width="29" height="29" rx="6" fill="none" stroke="#0b0b0b" stroke-width="2"/>'
        '<rect x="8" y="17" width="3.5" height="7" rx="1"/><rect x="14.25" y="12" width="3.5" height="12" rx="1"/>'
        '<rect x="20.5" y="15" width="3.5" height="9" rx="1"/><circle cx="22.25" cy="8.5" r="2.25" fill="#2a78d6"/></svg>')


def html_erratas(rel, sufijo):
    """Fe de erratas dentro del PDF (Marcos, 30-oct-2026): texto en datos\\sellos\\fe_de_erratas_AAAA-MM_X.txt, si existe.
    1.ª línea = título; líneas con '- ' = puntos de una lista; el resto, párrafos. Queda sellada con el PDF."""
    p = SELLOS / f"fe_de_erratas_{rel}{sufijo}.txt"
    if not sufijo or not p.exists():
        return ""
    lineas = [x.rstrip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    cuerpo, lista = [], []
    for x in lineas[1:]:
        if x.startswith("- "):
            lista.append(f"<li>{x[2:]}</li>")
            continue
        if lista:
            cuerpo.append(f"<ol>{''.join(lista)}</ol>")
            lista = []
        cuerpo.append(f"<p>{x}</p>")
    if lista:
        cuerpo.append(f"<ol>{''.join(lista)}</ol>")
    return f"<div class='erratas'><h3>{lineas[0]}</h3>{''.join(cuerpo)}</div>"


def html_pdf(n, rel, registrado, tarjetas, sha_csv, erratas=""):
    bloques = []
    for t in tarjetas:
        d, pre, suf = FORMATO.get(t["clave"], (2, "", ""))
        filas = "".join(f"<tr><td>{per_txt(f['periodo'])}</td><td class='num nuestro'>{fmt(f['nuestro'], d, pre, suf)}</td>"
                        f"<td class='num'>{fmt(f['rem'], d, pre, suf)}</td></tr>" for f in t["filas"])
        bloques.append(f"<div class='bloque'><h3>{t['titulo']}</h3><p class='uni'>{t['unidad']}</p>"
                       f"<table><thead><tr><th>Período</th><th class='num'>Datos Con Método</th><th class='num'>REM anterior*</th></tr></thead>"
                       f"<tbody>{filas}</tbody></table></div>")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Pronóstico n.º {n} - {mes_txt(rel)}</title>
<style>{CSS}
@page{{size:A4;margin:14mm 12mm}} body{{background:#fff;font-size:11px}}
header{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid var(--tinta);padding-bottom:8px;margin-bottom:12px}}
h1{{font-size:20px;margin:6px 0 2px}} .sub{{color:var(--tinta2);margin:0}}
.grilla{{display:grid;grid-template-columns:1fr 1fr;gap:10px 18px}}
.bloque{{break-inside:avoid;border:1px solid var(--linea);border-radius:6px;padding:8px 10px}}
h3{{font-size:13px;margin:0}} .uni{{color:var(--tenue);margin:0 0 4px;font-size:10px}}
table{{width:100%;border-collapse:collapse}} th{{text-align:left;font-size:9.5px;color:var(--tinta2);border-bottom:1px solid #c3c2b7;padding:2px 3px}}
td{{padding:2px 3px;border-bottom:1px solid var(--linea)}} .num{{text-align:right;font-family:"IBM Plex Mono",Consolas,monospace}}
.nuestro{{font-weight:600}} .mod{{color:var(--tenue);font-size:9px;margin:4px 0 0}}
.pie{{margin-top:12px;border-top:1px solid var(--linea);padding-top:8px;color:var(--tinta2);font-size:9.5px;line-height:1.45}}
.hash{{font-family:"IBM Plex Mono",Consolas,monospace;font-size:9px;word-break:break-all;color:var(--tinta)}}
.erratas{{break-inside:avoid;margin-top:12px;border:1px solid var(--tinta);border-radius:6px;padding:8px 12px;font-size:10.5px;line-height:1.45}}
.erratas h3{{margin-bottom:4px}} .erratas p{{margin:4px 0}} .erratas ol{{margin:4px 0;padding-left:18px}}
</style></head><body>
<header><div><div class="marca">{LOGO}Datos <span>Con</span> <b>Método</b></div>
<h1>Pronóstico n.º {n} · relevamiento de {mes_txt(rel)}</h1>
<p class="sub">Registrado el {registrado} (hora de Buenos Aires), antes de que el BCRA publique el REM de {mes_txt(rel)}.</p></div>
<div class="mono" style="text-align:right;color:var(--tinta2)">www.datosconmetodo.com<br>Marcos Cavanagh</div></header>
<div class="grilla">{''.join(bloques)}</div>
{erratas}
<div class="pie">
<p>* REM anterior: mediana del último Relevamiento de Expectativas de Mercado publicado por el BCRA para el mismo período (solo como referencia).
Nuestros pronósticos usan únicamente datos públicos disponibles a la fecha de registro. Algunos modelos aprenden de los REM
ya publicados en meses anteriores; ninguno usa el REM del mes del relevamiento, que todavía no se conoce.</p>
<p>Huella SHA-256 de los mismos datos en CSV (publicado en el sitio): <span class="hash">{sha_csv}</span></p>
<p>La huella SHA-256 de este PDF se publica en www.datosconmetodo.com y se sella en Bitcoin con OpenTimestamps: si se cambiara una sola letra
de este documento, la huella sería otra. Cómo verificarlo: www.datosconmetodo.com/#sellos. Ejercicio estadístico; no es una
recomendación de inversión. Datos propios bajo licencia CC BY 4.0.</p></div></body></html>"""


def destacados(tarjetas):
    """Los números de la publicación y de la imagen: (etiqueta, valor con formato)."""
    # una por variable sellada, la estimación más cercana en el tiempo (primera fila de cada tarjeta; Marcos, 30-oct-2026)
    nombre = {"inflacion": "Inflación", "nucleo": "Inflación núcleo", "dolar": "Dólar", "pib": "PIB", "pib_anual": "PIB anual",
              "desempleo": "Desocupación", "tasa": "TAMAR", "importaciones": "Importaciones", "exportaciones": "Exportaciones",
              "fiscal": "Resultado primario"}
    unidad = {"importaciones": "millones de US$", "exportaciones": "millones de US$", "fiscal": "miles de millones de $"}
    out = []
    for t in tarjetas:
        if not t["filas"]:
            continue
        f = t["filas"][0]
        d, pre, suf = FORMATO.get(t["clave"], (2, "", ""))
        out.append((f"{nombre.get(t['clave'], t['titulo'])} {per_txt(f['periodo'])}", fmt(f["nuestro"], d, pre, suf),
                    unidad.get(t["clave"], "")))
    return out


def html_tarjeta(n, rel, registrado, items, sha_pdf):
    cajas = "".join(f"<div class='caja'><div class='et'>{e}</div><div class='v'>{v}</div><div class='u'>{u}</div></div>" for e, v, u in items)
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><style>{CSS}
body{{width:1200px;height:675px;padding:42px 52px;display:flex;flex-direction:column;justify-content:space-between}}
h1{{font-size:38px;margin:14px 0 4px;letter-spacing:-.01em}} .sub{{color:var(--tinta2);font-size:19px;margin:0}}
.cajas{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}
.caja{{border:1px solid var(--linea);border-radius:10px;padding:12px 14px;background:#fff}}
.et{{font-size:15px;color:var(--tinta2)}} .v{{font-size:32px;font-weight:600;margin-top:6px;color:var(--tinta)}}
.u{{font-size:13px;color:var(--tenue);min-height:16px}}
.pie{{display:flex;flex-direction:column;gap:4px;font-size:15px;color:var(--tinta2)}} .pie .mono{{font-size:14px;color:var(--tinta)}}
</style></head><body>
<div><div class="marca" style="font-size:24px">{LOGO}Datos <span>Con</span> <b>Método</b></div>
<h1>Pronóstico n.º {n} · {mes_txt(rel)}</h1><p class="sub">Registrado el {registrado}, antes de que el BCRA publique el REM. Sellado en Bitcoin (OpenTimestamps).</p></div>
<div class="cajas">{cajas}</div>
<div class="pie"><span>PDF completo y verificación: datosconmetodo.com/#sellos</span><span class="mono">SHA-256 del PDF: {sha_pdf}</span></div>
</body></html>"""


def texto_linkedin(n, rel, registrado, items, url_pdf, sha_pdf):
    """Publicación de LinkedIn (sin límite práctico de largo; LinkedIn admite 3.000 caracteres)."""
    lineas = "\n".join(f"• {e}: {v}{' ' + u if u else ''}" for e, v, u in items)
    fecha, hora = registrado[:10], registrado[11:16]
    fecha = f"{fecha[8:]}/{fecha[5:7]}/{fecha[:4]}"
    return (f"Pronóstico n.º {n} de Datos Con Método · relevamiento del REM de {MESES[rel.month - 1]} de {rel.year}\n\n"
            f"Registramos nuestro pronóstico el {fecha} a las {hora}, antes de que el BCRA publique el REM de las consultoras, "
            "y lo dejamos sellado: ni nosotros podemos cambiarlo después.\n\n"
            f"Nuestros números:\n{lineas}\n\n"
            "Cómo verificarlo:\n"
            f"• PDF con todos los pronósticos: {url_pdf}\n"
            f"• Huella SHA-256 del PDF: {sha_pdf}\n"
            "• Sello en la cadena de Bitcoin (OpenTimestamps) y verificación paso a paso: https://www.datosconmetodo.com/#sellos\n\n"
            "Cuando salga el REM y cuando se conozca el dato real, vamos a volver a compartir esta publicación con la comparación.\n\n"
            "Hecho solo con datos públicos. Ejercicio estadístico; no es una recomendación de inversión.\n\n"
            "#Economía #Argentina #Inflación #DatosAbiertos")


def wayback(url):
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"https://web.archive.org/save/{url}", headers={"User-Agent": "Mozilla/5.0"}), timeout=120)
        return r.geturl() if "web.archive.org/web/" in r.geturl() else ""
    except Exception:
        return ""


def sitio_publicado():
    try:
        urllib.request.urlopen(urllib.request.Request(SITIO, headers={"User-Agent": "Mozilla/5.0"}), timeout=15)
        return True
    except Exception:
        return False


def emitir(prueba=False, todo=False):
    import publicar_web
    from sello_ots import sellar
    hoy = pd.Timestamp.now()
    rel = pd.Period(hoy, "M")
    reg = registro()
    n, sufijo = numero(reg, rel)
    tarjetas = publicar_web.pronosticos()
    tarjetas = [t for t in tarjetas if str(t.get("relevamiento")) == str(rel) and (todo or t.get("sellar", True))]   # PIB anual y núcleo: no se sellan hasta que Marcos diga
    # (--prueba --todo: muestra cómo quedaría con todo, sin sellar ni registrar)
    for t in tarjetas:                                   # resultado primario en miles de millones de $, como lo pide el REM (30-oct-2026)
        if t["clave"] == "fiscal":
            t["unidad"] = "miles de millones de $, año calendario"
            t["filas"] = [dict(f, nuestro=f["nuestro"] * 1000, rem=f["rem"] * 1000) for f in t["filas"]]
    if not tarjetas:
        raise RuntimeError(f"no hay pronósticos registrados para el relevamiento {rel}")
    registrado = max(str(t["corrida"]) for t in tarjetas)
    carpeta = SELLOS / ("_prueba" if prueba else str(rel))
    if carpeta.exists() and prueba:
        shutil.rmtree(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"pronostico_{rel}{sufijo}"                 # el 1B no pisa los archivos del 1
    filas = [{"variable": t["titulo"], "unidad": t["unidad"], "periodo": f["periodo"], "datos_con_metodo": f["nuestro"],
              "rem_anterior": f["rem"], "registrado": t["corrida"], "relevamiento": str(rel)}   # sin la columna modelo (Marcos, 30-oct-2026)
             for t in tarjetas for f in t["filas"]]
    csv = carpeta / f"{nombre}.csv"
    pd.DataFrame(filas).to_csv(csv, index=False, encoding="utf-8-sig")
    sha_csv = sha256(csv)
    html = carpeta / f"{nombre}.html"
    html.write_text(html_pdf(n, rel, registrado, tarjetas, sha_csv, html_erratas(rel, sufijo)), encoding="utf-8")
    pdf = carpeta / f"{nombre}.pdf"
    edge(html, pdf, pdf=True)
    sha_pdf = sha256(pdf)
    items = destacados(tarjetas)
    tar = carpeta / f"{nombre}_tarjeta.html"
    tar.write_text(html_tarjeta(n, rel, registrado, items, sha_pdf), encoding="utf-8")
    png = carpeta / f"{nombre}.png"
    edge(tar, png, pdf=False)
    url_pdf = f"{SITIO}/sellos/{rel}/{pdf.name}"
    post = texto_linkedin(n, rel, registrado, items, url_pdf, sha_pdf)
    (carpeta / f"publicacion_linkedin{sufijo}.txt").write_text(post, encoding="utf-8")
    fila = {"n": str(n), "relevamiento": str(rel), "registrado": registrado, "pdf": pdf.name, "sha256_pdf": sha_pdf, "csv": csv.name,
            "sha256_csv": sha_csv, "ots_pdf": "", "ots_csv": "", "bitcoin": "", "wayback_pdf": "", "post_texto": post,
            "post_url": "", "post_rem_url": "", "post_real_url": ""}
    if not prueba:
        for clave, archivo in (("ots_pdf", pdf), ("ots_csv", csv)):
            try:
                fila[clave] = sellar(archivo)[0].name
            except Exception as e:
                fila[clave] = f"ERROR {type(e).__name__}"
        reg = pd.concat([reg, pd.DataFrame([fila])], ignore_index=True)
        reg.to_csv(REGISTRO, index=False, encoding="utf-8-sig")
    return fila, carpeta


def mantenimiento():
    """Completa los sellos de Bitcoin pendientes y, si el sitio ya está publicado, guarda los PDF en el Archivo de Internet."""
    from sello_ots import completar
    reg = registro()
    if not len(reg):
        return []
    notas = []
    publicado = None
    for i, x in reg.iterrows():
        carpeta = SELLOS / x["relevamiento"]
        if x["bitcoin"] != "sí" and x["ots_pdf"].endswith(".ots"):
            try:
                ok = completar(carpeta / x["ots_pdf"])
                if x["ots_csv"].endswith(".ots"):
                    completar(carpeta / x["ots_csv"])
                if ok:
                    reg.loc[i, "bitcoin"] = "sí"
                    notas.append(f"Pronóstico n.º {x['n']}: sello anclado en Bitcoin")
            except Exception:
                pass
        if not x["wayback_pdf"]:
            if publicado is None:
                publicado = sitio_publicado()
            if publicado:
                w = wayback(f"{SITIO}/sellos/{x['relevamiento']}/{x['pdf']}")
                if w:
                    reg.loc[i, "wayback_pdf"] = w
                    notas.append(f"Pronóstico n.º {x['n']}: guardado en el Archivo de Internet")
    reg.to_csv(REGISTRO, index=False, encoding="utf-8-sig")
    return notas


def main():
    args = sys.argv[1:]
    aviso = "--aviso" in args
    L = [f"=== PRONÓSTICO SELLADO | {datetime.now():%Y-%m-%d %H:%M} ==="]
    for flag, col in (("--linkedin", "post_url"), ("--linkedin-rem", "post_rem_url"), ("--linkedin-real", "post_real_url")):
        if flag in args:
            mes, url = args[args.index(flag) + 1], args[args.index(flag) + 2]
            reg = registro()
            reg.loc[reg["relevamiento"] == mes, col] = url
            reg.to_csv(REGISTRO, index=False, encoding="utf-8-sig")
            print(f"Guardado {col} del pronóstico de {mes}: {url}")
            return
    if "--prueba" in args:
        fila, carpeta = emitir(prueba=True, todo="--todo" in args)
        print(f"Prueba armada en {carpeta}\n\n{fila['post_texto']}\n\n({len(fila['post_texto'])} caracteres)")
        return
    nuevo = None
    hoy = pd.Timestamp.now()
    rel = str(pd.Period(hoy, "M"))
    reg = registro()
    desde = dia_emision(rel)
    # EMISIÓN AUTOMÁTICA DESACTIVADA (decisión de Marcos, 29-oct-2026): el sello n.º 1 salió solo y con errores (sin
    # exportaciones). Desde ahora se sella SOLO a mano y con el OK de Marcos: py -3.13 emitir_pronostico.py --emitir
    if "--emitir" not in args:
        L.append(f"Emisión automática desactivada: para sellar el pronóstico de {rel}, revisarlo y correr a mano "
                 f"'py -3.13 emitir_pronostico.py --emitir' (con el OK de Marcos). Sello del mes: "
                 f"{'ya emitido' if rel in set(reg['relevamiento']) else 'pendiente'}.")
    elif hoy.normalize() >= desde:                       # si el relevamiento ya tiene sello, sale el 1B, 1C... (30-oct-2026)
        rem_pub = str(pd.Timestamp(pd.read_parquet(C / "rem_normalizada.parquet", columns=["mes_relevamiento"])["mes_relevamiento"].max()).to_period("M"))
        if rem_pub >= rel:
            L.append(f"No se emite: el REM de {rel} ya está publicado (llegamos tarde este mes).")
        else:
            nuevo, carpeta = emitir()
            L += [f"Pronóstico sellado n.º {nuevo['n']} (relevamiento {rel}) emitido. Archivos en {carpeta}",
                  f"Huella SHA-256 del PDF: {nuevo['sha256_pdf']}", f"OpenTimestamps: {nuevo['ots_pdf'] or 'sin sello'}",
                  "PUBLICAR EN LINKEDIN (texto abajo, con la imagen PNG de la carpeta):", "", nuevo["post_texto"], ""]
    else:
        L.append(f"Sin emisión hoy (se emite desde el {desde:%d-%m-%Y}, primer día del relevamiento; sello del mes: {'ya emitido' if rel in set(reg['relevamiento']) else 'pendiente'}).")
    L += mantenimiento()
    texto = "\n".join(L)
    print(texto)
    (C / "informe_sellos.txt").write_text(texto, encoding="utf-8")
    if aviso and nuevo:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"Pronóstico n.º {nuevo['n']} sellado.\n\nPublicalo en LinkedIn con la imagen de:\n{carpeta}\n\n"
                                                "El texto está en publicacion_linkedin*.txt", "Datos Con Método: pronóstico sellado", 0x40)
        except Exception:
            pass


if __name__ == "__main__":
    main()
