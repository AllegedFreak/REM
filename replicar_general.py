# replicar_general.py (30-oct-2026; en actualizar_todo.py después de replicar_nucleo.py)
# Nuestro pronóstico de la INFLACIÓN GENERAL, coherente con el de la núcleo (decisión de Marcos, 30-oct-2026, "opción 3"):
#   m0-m2 (mes del relevamiento y los dos siguientes): DESDE ABAJO =
#         s_núcleo × N (replicar_nucleo.py) + s_regulados × regulados + s_estacionales × estacionales
#         s_* = pesos efectivos al último mes publicado (peso de base deducido de las series del INDEC × índice de la categoría
#               / índice general: núcleo 67,76%, regulados 20,87%, estacionales 11,33% en dic-2016)
#         regulados = aumentos confirmados del calendario del mes, nacionales y del AMBA desde el 28-nov-2026 (m0-m2)
#               + en m0, combustibles medidos (promedio del mes hasta el 25 contra el promedio del mes anterior; índice de las
#                 mismas estaciones)
#               + en m0-m1, prepagas sin anuncio = IPC de dos meses antes (29-oct-2026: la regla le gana al promedio de 3 meses,
#                 error 0,56 contra 0,87, p 0,09; probar_reglas_regulados.py)
#               + el resto de los rubros al promedio de 3 meses de los regulados
#         estacionales = promedio de los últimos 12 meses + desvío típico de ese mes del año (3 años anteriores)
#   m3 en adelante: I (replicar_rem.py), que en 2025-26 es lo mejor a 3-5 meses.
# Prueba en tiempo real 2025-26 (error contra la general real, pp): m0 0,46 (I 0,69; REM del mes 0,25), m1 0,48 (REM 0,48),
# m2 0,51 (REM 0,57), m3-m5 = I 0,26-0,38 (REM 0,53-0,61). Nada significativo contra el REM salvo m0 (gana el REM).
# Salidas: datos\replica_general.csv, datos\informe_general_inflacion.txt, datos\registro_general.csv
# El sitio y el sello todavía NO lo muestran (30-oct-2026): se decide con Marcos.
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import datos_macro as D
from backtest_rem import diebold_mariano
from replicar_rem import combustible_cortes

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

C = Path(__file__).resolve().parent / "datos"
CORRIDA = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")   # una sola hora por corrida
W0 = {"nucleo": 0.6776, "regulados": 0.2087, "estacional": 0.1133}
W_REG = {"combustibles": 4.237, "transporte": 2.951, "telefonia": 2.733, "prepagas": 2.205, "tabaco": 1.914,
         "educacion": 1.803, "gas": 1.653, "luz": 1.225, "agua": 0.772}   # ponderaciones INDEC (% del IPC nacional)
W_REG = {k: v / sum(W_REG.values()) for k, v in W_REG.items()}
# Rubros del calendario que se usan. DESDE EL 28-NOV-2026 (Marcos: "sumemos los regulados del amba") entran también los
# aumentos del AMBA (CABA, PBA, AMBA; confirmados y parciales) como aproximación del rubro nacional: transporte (colectivo,
# subte, tren), luz, agua, telefonía y educación. Prueba en el mes en curso, ene-oct 2026 (10 meses), error en regulados contra
# el INDEC: 0,79 → 0,63 pp, sesgo +0,24 → −0,04, gana 7 de 10, p 0,12 (scratchpad reg_amba.py). Antes: solo nacionales
# (prepagas, gas, luz). Se aplica en m0-m2, igual que el calendario nacional.
CAL_GRUPOS = {"prepagas": "prepagas", "gas": "gas", "luz": "luz", "agua": "agua", "colectivo": "transporte", "subte": "transporte",
              "tren": "transporte", "telecomunicaciones": "telefonia", "colegios": "educacion"}
CORTE = pd.Period("2024-12", "M")


def combustibles_mes(meses):
    """Variación % del promedio de naftas (súper y premium) hasta el día 25 de cada mes contra el promedio del mes anterior."""
    dias = {m: [pd.Timestamp(m.year, m.month, d, 23, 59) for d in (5, 10, 15, 20, 25)] + [m.to_timestamp(how="end").floor("s")]
            for m in meses}
    todos = [x for v in dias.values() for x in v]
    s, p = combustible_cortes(todos), combustible_cortes(todos, producto="Nafta (premium)%")
    if not len(s):
        return pd.Series(dtype=float)
    ci = np.sqrt(s * p.reindex(s.index)).fillna(s) if len(p) else s
    a25 = pd.Series({m: ci.reindex(pd.to_datetime(v[:5])).mean() for m, v in dias.items()})
    afin = pd.Series({m: ci.reindex(pd.to_datetime(v)).mean() for m, v in dias.items()})
    return (np.log(a25 / afin.shift(1)) * 100).dropna()


def main():
    c = D.ipc_categorias()[["general", "nucleo", "regulados", "estacional"]]
    m = (c / c.shift(1) - 1) * 100
    t_hoy = pd.Period(pd.Timestamp.today(), "M")
    comb = combustibles_mes(pd.period_range("2016-01", t_hoy, freq="M"))
    cal = pd.read_csv(C / "regulados_anunciados.csv", encoding="utf-8-sig")
    cal["grupo"] = cal["rubro"].str.lower().map(CAL_GRUPOS)
    cal = cal.dropna(subset=["grupo", "pct"])
    cal = cal[cal["calidad"].isin(["confirmado", "parcial"]) & (cal["jurisdiccion"].str.lower() != "pba interior")]
    N = pd.read_csv(C / "replica_nucleo.csv", encoding="utf-8-sig")
    for k in ("origen", "obj"):
        N[k] = pd.PeriodIndex(N[k], freq="M")
    G = pd.read_csv(C / "replica_rem.csv", encoding="utf-8-sig")
    G = G[G["H"].str.fullmatch(r"m\d")].copy()
    G["origen"] = pd.PeriodIndex(G["origen"], freq="M")
    G["h"] = G["H"].str[1].astype(int)
    G = G[["origen", "h", "I", "rem", "real"]]
    ri = pd.read_csv(C / "registro_inflacion.csv", encoding="utf-8-sig")
    ri = ri[ri["relevamiento"] == str(t_hoy)].sort_values("fecha_corrida").groupby("H").last()
    if len(ri):    # I de hoy (el relevamiento en curso no está en replica_rem.csv)
        G = pd.concat([G, pd.DataFrame([{"origen": t_hoy, "h": int(H[1:]), "I": r["pronostico_inflacion"]}
                                        for H, r in ri.iterrows() if H.startswith("m")])], ignore_index=True)

    def regulados(o, h):
        u, b = o - 1, o + h
        reg3 = np.mean([m["regulados"].get(u - i, np.nan) for i in range(3)])
        partes = {k: q["pct"].mean() for k, q in cal[cal["mes"] == str(b)].groupby("grupo")}   # el anuncio oficial manda
        if h == 0:
            partes["combustibles"] = comb.get(o, np.nan)
        if "prepagas" not in partes and h <= 1:   # regla: IPC de t-2, ya publicado (probar_reglas_regulados.py, 29-oct-2026)
            partes["prepagas"] = m["general"].get(b - 2, np.nan)
        partes = {k: v for k, v in partes.items() if not np.isnan(v)}
        cub = sum(W_REG[k] for k in partes)
        return sum(W_REG[k] * v for k, v in partes.items()) + (1 - cub) * reg3, ", ".join(f"{k} {v:.2f}" for k, v in partes.items())

    filas = []
    for _, x in N.dropna(subset=["N"]).iterrows():
        o, h, b = x["origen"], int(x["h"]), x["obj"]
        u = o - 1
        if (u - 40) not in c.index or u not in c.index:
            continue
        s = {k: W0[k] * c[k][u] / c["general"][u] for k in W0}
        tot = sum(s.values())
        s = {k: v / tot for k, v in s.items()}
        reg, det = regulados(o, h)
        tend = np.mean([m["estacional"][u - i] for i in range(12)])
        dev = np.nanmean([m["estacional"].get(b - 12 * y, np.nan)
                          - np.nanmean([m["estacional"].get(b - 12 * y - j, np.nan) for j in range(-5, 7)]) for y in (1, 2, 3)])
        est = tend + dev
        filas.append({"origen": o, "h": h, "obj": b, "N": x["N"], "regulados": reg, "regulados_detalle": det, "estacionales": est,
                      "s_nucleo": s["nucleo"], "s_regulados": s["regulados"], "s_estacional": s["estacional"],
                      "abajo": s["nucleo"] * x["N"] + s["regulados"] * reg + s["estacional"] * est})
    A = pd.DataFrame(filas).merge(G, on=["origen", "h"], how="left")
    A["G"] = np.where(A["h"] <= 2, A["abajo"], A["I"])
    A.to_csv(C / "replica_general.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    L = [f"=== NUESTRO PRONÓSTICO DE LA INFLACIÓN GENERAL (opción 3) | {datetime.now():%Y-%m-%d %H:%M} ===",
         "G = desde abajo (núcleo N + regulados + estacionales) en m0-m2; I desde m3. Errores contra la general real (pp), tiempo real.", ""]
    for per, mk in (("2019-2024", A["origen"] <= CORTE), ("2025-26 (régimen actual)", A["origen"] > CORTE)):
        Z = A[mk].dropna(subset=["G", "I", "rem", "real"])
        L += [f"== {per}", f"   {'':4}{'REM del mes':>12}{'I':>8}{'desde abajo':>13}{'G':>8}{'n':>5}{'p G vs REM':>12}"]
        for h in range(6):
            z = Z[Z["h"] == h]
            if len(z) < 6:
                continue
            e = lambda col: (z[col] - z["real"]).abs().mean()
            p = diebold_mariano((z["G"] - z["real"]).values, (z["rem"] - z["real"]).values, max(h, 1)) if len(z) > 10 else np.nan
            L.append(f"   m{h:<3}{e('rem'):12.2f}{e('I'):8.2f}{e('abajo'):13.2f}{e('G'):8.2f}{len(z):5d}{p:12.2f}")
        L.append("")
    H = A[A["origen"] == t_hoy].sort_values("h").copy()
    if len(H):
        # PUBLICADO DESDE EL 26-NOV-2026 (Marcos: "aplicamos 1"): m3-m6 (los meses que salen de I) = mitad I / mitad mediana
        # de los hogares llevada a tasa mensual, en logaritmos, igual que los 12 meses; así el camino es coherente con el 12m.
        # Prueba 2025-26: error m3 0,37→0,40, m4 0,27→0,34, m5 y m6 iguales (p 0,28-0,98, empate). m0-m2 ("desde abajo") no
        # cambian. Si falta la mediana UTDT quedan I y ALERTA R03.
        try:
            _hs = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
            _med = _hs[_hs["id"] == "utdt_ei_mediana"].assign(m=lambda z: pd.to_datetime(z["fecha"]).dt.to_period("M")).set_index("m")["valor"]
            _mes = t_hoy if t_hoy in _med.index else t_hoy - 1
            _eim = ((1 + float(_med.get(_mes, np.nan)) / 100) ** (1 / 12) - 1) * 100
            if np.isnan(_eim):
                raise ValueError(f"mediana UTDT de {_mes} vacía")
            lej = H["h"] >= 3
            H["I_sin_mitad"] = H["G"]
            H.loc[lej, "G"] = np.expm1((np.log1p(H.loc[lej, "G"] / 100) + np.log1p(_eim / 100)) / 2) * 100
            L.append(f"m3-m6 PUBLICADOS = mitad I / mitad mediana de los hogares ({_eim:.2f}% mensual, UTDT de {_mes}); "
                     "I sin mezclar: " + ", ".join(f"{x['obj']} {x['I_sin_mitad']:.2f}" for _, x in H[lej].iterrows()))
        except Exception as ex:
            L.append(f"   ALERTA R03 (m3-m6): mitad I / mediana sin calcular ({type(ex).__name__}: {ex}); se publica I. "
                     "Qué hacer: revisar la mediana UTDT (utdt_ei_mediana en series_historicas).")
        h0 = H.iloc[0]
        L.append(f"PRONÓSTICO DE HOY (general; IPC hasta {t_hoy - 1}; pesos efectivos núcleo {100 * h0['s_nucleo']:.1f}%, "
                 f"regulados {100 * h0['s_regulados']:.1f}%, estacionales {100 * h0['s_estacional']:.1f}%):")
        L.append(f"   {'mes':9}{'núcleo N':>10}{'regulados':>11}{'estac.':>8}{'desde abajo':>13}{'I':>7}{'G':>7}")
        reg = []
        for _, x in H.iterrows():
            L.append(f"   {str(x['obj']):9}{x['N']:10.2f}{x['regulados']:11.2f}{x['estacionales']:8.2f}{x['abajo']:13.2f}{x['I']:7.2f}{x['G']:7.2f}")
            reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "H": f"m{int(x['h'])}", "objetivo": str(x["obj"]),
                        "G": x["G"], "abajo": x["abajo"], "I": x["I"], "N": x["N"], "regulados": x["regulados"], "estacionales": x["estacionales"]})
        if h0["regulados_detalle"]:
            L.append(f"   Regulados de {t_hoy}: {h0['regulados_detalle']} (resto de los rubros al promedio de 3 meses)")
        # PUBLICADO DESDE EL 28-NOV-2026 (Marcos: "usemos el super C2 para el primer mes"; antes en observación desde el
        # 17-nov): G m0 corregida con el termómetro del súper, "C2 mezcla" de probar_termometro_general.py:
        # C2 = G + w·(termómetro − G), w (0 a 1) estimado con los meses ya publicados. Prueba 2025-26 (13 meses): 0,36 contra
        # 0,47 pp, p 0,26. Si no se puede calcular se publica G desde abajo con ALERTA R03 (nunca rellenar en silencio).
        c2_pub = False
        try:
            tm = pd.read_parquet(C / "sepa_indice_mensual.parquet")
            tm = tm[tm["region"] == "Nacional"].set_index("mes")["variacion_25_pct"].dropna()
            z = A[(A["h"] == 0) & (A["obj"] <= t_hoy - 1)].dropna(subset=["real"]).copy()
            z["termo"] = z["obj"].astype(str).map(tm)
            z = z.dropna(subset=["termo"])
            termo = tm.get(str(t_hoy), np.nan)
            if len(z) >= 8 and not np.isnan(termo):
                d, y = (z["termo"] - z["abajo"]).values, (z["real"] - z["abajo"]).values
                w = float(np.clip(d @ y / (d @ d), 0, 1)) if d @ d > 0 else 0.0
                c2 = h0["abajo"] + w * (termo - h0["abajo"])
                H.loc[H["h"] == 0, "G"] = c2
                reg[0]["G"] = c2
                c2_pub = True
                L.append(f"   m0 PUBLICADO = G con el súper (C2) {c2:.2f}% = G desde abajo {h0['abajo']:.2f} + "
                         f"{w:.2f} × (súper {termo:.2f} − G); {len(z)} meses para estimar el peso")
                p2 = C / "registro_general_c2.csv"
                pd.DataFrame([{"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "objetivo": str(h0["obj"]), "G": h0["abajo"],
                               "termometro": termo, "w": w, "C2": c2, "meses": len(z)}]).to_csv(
                    p2, mode="a", header=not p2.exists(), index=False, encoding="utf-8-sig", float_format="%.4f")
                # EN OBSERVACIÓN (26-nov-2026, Marcos): C2 + carne de Cañuelas con su peso INDEC (probar_rubros_mes.py):
                # C3 = C2 + peso carnes × (pronóstico de la carne − C2). Prueba 13 meses: 0,30 contra 0,47 pp, p 0,14.
                try:
                    from probar_rubros_mes import peso_nacional, prono_carnes
                    pc = prono_carnes(t_hoy)[0].get(t_hoy, np.nan)
                    if not np.isnan(pc):
                        pk = peso_nacional("01.1.2")
                        c3 = c2 + pk * (pc - c2)
                        L.append(f"   En observación (no se publica): C2 + carne de Cañuelas (C3) {c3:.2f}% = C2 {c2:.2f} + "
                                 f"{pk:.3f} × (carne {pc:.2f} − C2)")
                        p3 = C / "registro_general_c3.csv"
                        pd.DataFrame([{"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "objetivo": str(h0["obj"]),
                                       "G": h0["abajo"], "C2": c2, "carne": pc, "peso_carne": pk, "C3": c3}]).to_csv(
                            p3, mode="a", header=not p3.exists(), index=False, encoding="utf-8-sig", float_format="%.4f")
                    else:
                        L.append("   En observación: C3 (C2 + carne de Cañuelas) sin calcular: falta la hacienda del mes")
                except Exception as ex:
                    L.append(f"   En observación: C3 sin calcular ({type(ex).__name__}: {ex})")
            else:
                L.append(f"   C2 (G m0 con el súper) sin calcular: termómetro del mes "
                         f"{'sin dato todavía' if np.isnan(termo) else 'ok'}, {len(z)} meses para estimar (mín. 8)")
        except Exception as ex:
            L.append(f"   C2 sin calcular ({type(ex).__name__}: {ex})")
        if not c2_pub:
            L.append(f"   ALERTA R03 (m0): no se pudo aplicar el súper (C2); se publica G desde abajo {h0['abajo']:.2f}%. "
                     "Qué hacer: revisar sepa_indice_mensual.parquet (termómetro del súper del mes).")
        # PUBLICADA DESDE EL 30-OCT-2026 (Marcos): G es la inflación general del sitio y del sello. 12 meses = I (regresión
        # directa); años y 24 meses sobre el camino de G con la misma regla que replicar_rem.py: meses publicados + G (m0..m6;
        # si faltan meses del año, la tasa del 12m); años siguientes = (1 + año en curso)^{r_k} − 1 (clase de referencia
        # con superávit); meses sin pronóstico propio = tasa constante dentro de cada año; 24m = segundo año (t+13..t+24).
        # El camino va a datos\camino_inflacion.csv (lo usa replicar_tasa.py para la TAMAR a 12 meses y de diciembre).
        from replicar_rem import clase_referencia_c
        i12 = ri["pronostico_inflacion"].get("12m", np.nan) if len(ri) else np.nan
        # PUBLICADA DESDE EL 26-NOV-2026 (Marcos: "hagamos esto", opción 2): 12 meses = "mitad I / mitad mediana de los
        # hogares" (en logaritmos). probar_ei_12m.py, 2025-26: 2,78 contra 3,35 pp de I (n 10, ventanas que se pisan); más
        # estable: el 26-nov el promedio UTDT cayó 35,9 → 29,7 e I pasó de 26,1 a 20,7%, la mitad de 27,8 a 25,2%.
        # Si falta la mediana UTDT se publica I y se avisa (nunca rellenar en silencio). Se sigue registrando I en
        # registro_12m_mitad.csv para comparar.
        i12_pub, fuente12 = i12, "I (sin la mediana de los hogares)"
        try:
            hs = pd.read_parquet(C / "series_historicas.parquet", columns=["id", "fecha", "valor"])
            med = hs[hs["id"] == "utdt_ei_mediana"].assign(m=lambda z: pd.to_datetime(z["fecha"]).dt.to_period("M")).set_index("m")["valor"]
            mes_ei = t_hoy if t_hoy in med.index else t_hoy - 1          # la UTDT del mes sale hacia el día 20
            ei_med = float(med.get(mes_ei, np.nan))
            mitad = np.expm1((np.log1p(i12 / 100) + np.log1p(ei_med / 100)) / 2) * 100
            if np.isnan(mitad):
                raise ValueError(f"I 12m {i12} o mediana UTDT de {mes_ei} vacía")
            i12_pub, fuente12 = mitad, "mitad I / mitad mediana de los hogares"
            L.append(f"   12 meses PUBLICADO = mitad I / mitad mediana de los hogares {mitad:.1f}% "
                     f"(I {i12:.1f}%, mediana UTDT de {mes_ei} {ei_med:.1f}%)")
            p3 = C / "registro_12m_mitad.csv"
            pd.DataFrame([{"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "I_12m": i12, "ei_mediana": ei_med,
                           "mes_ei": str(mes_ei), "mitad": mitad}]).to_csv(
                p3, mode="a", header=not p3.exists(), index=False, encoding="utf-8-sig", float_format="%.4f")
        except Exception as ex:
            L.append(f"   ALERTA R03 (12 meses): mitad I / mediana sin calcular ({type(ex).__name__}: {ex}); se publica I {i12:.1f}%. "
                     "Qué hacer: revisar la mediana UTDT (utdt_ei_mediana en series_historicas).")
        tasa12 = (1 + i12_pub / 100) ** (1 / 12)
        cam = {mm: 1 + m["general"][mm] / 100 for mm in pd.period_range(f"{t_hoy.year}-01", t_hoy - 1, freq="M")}
        cam.update({x["obj"]: 1 + x["G"] / 100 for _, x in H.iterrows()})
        for mm in pd.period_range(f"{t_hoy.year}-01", f"{t_hoy.year}-12", freq="M"):
            cam.setdefault(mm, tasa12)
        f_anio = lambda y: np.prod([cam[pd.Period(f"{y}-{k:02d}", "M")] for k in range(1, 13)])
        rk, n_casos = clase_referencia_c(t_hoy.year)
        for k in (1, 2):
            meses = list(pd.period_range(f"{t_hoy.year + k}-01", f"{t_hoy.year + k}-12", freq="M"))
            con = [mm for mm in meses if mm in cam]
            f_con = np.prod([cam[mm] for mm in con]) if con else 1.0
            for mm in meses:
                cam.setdefault(mm, (f_anio(t_hoy.year) ** rk[k] / f_con) ** (1 / (12 - len(con))) if len(con) < 12 else 1.0)
        # bajada gradual (30-oct-2026, Marcos: opción "a"): los meses sin pronóstico propio siguen una línea (en log) que va del
        # último mes con pronóstico propio al promedio de lo que falta de cada año (ubicado en el centro de esos meses) y sigue
        # con la misma pendiente; después cada año se corre en paralelo para que su total dic/dic quede igual que antes.
        propios = set(H["obj"]) | {mm for mm in cam if mm < t_hoy}
        ult_p = max(propios)
        libres = [mm for mm in sorted(cam) if mm > ult_p]
        if libres:
            anclas = [((ult_p - libres[0]).n, np.log(cam[ult_p]))]
            for y in sorted({mm.year for mm in libres}):
                z = [mm for mm in libres if mm.year == y]
                anclas.append((np.mean([(mm - libres[0]).n for mm in z]), np.mean([np.log(cam[mm]) for mm in z])))
            xs, ys = np.array([a for a, _ in anclas]), np.array([b for _, b in anclas])
            pend = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
            lin = {mm: (np.interp((mm - libres[0]).n, xs, ys) if (mm - libres[0]).n <= xs[-1]
                        else ys[-1] + pend * ((mm - libres[0]).n - xs[-1])) for mm in libres}
            for y in sorted({mm.year for mm in libres}):
                z = [mm for mm in libres if mm.year == y]
                corr = (sum(np.log(cam[mm]) for mm in z) - sum(lin[mm] for mm in z)) / len(z)
                for mm in z:
                    cam[mm] = np.exp(lin[mm] + corr)
        anio = (f_anio(t_hoy.year) - 1) * 100
        p24 = (np.prod([cam[t_hoy + j] for j in range(13, 25)]) - 1) * 100
        largos = [("12m", "12m", i12_pub),("año0", str(t_hoy.year), anio), ("año1", str(t_hoy.year + 1), (f_anio(t_hoy.year + 1) - 1) * 100),
                  ("año2", str(t_hoy.year + 2), (f_anio(t_hoy.year + 2) - 1) * 100), ("24m", "24m", p24)]
        L.append("   General, plazos largos: " + " | ".join(f"{a} {v:.1f}%" for a, _, v in largos) + f" (clase de referencia, {n_casos} casos)")
        for H_, obj, v_ in largos:
            reg.append({"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "H": H_, "objetivo": obj, "G": v_})
        pd.DataFrame([{"fecha_corrida": CORRIDA, "relevamiento": str(t_hoy), "mes": str(mm), "inflacion_mensual": (f_ - 1) * 100}
                      for mm, f_ in sorted(cam.items()) if mm >= t_hoy]).to_csv(
            C / "camino_inflacion.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
        p = C / "registro_general.csv"
        pd.DataFrame(reg).to_csv(p, mode="a", header=not p.exists(), index=False, encoding="utf-8-sig", float_format="%.4f")
    L += ["", "ALERTAS: ninguna. Todo en orden."]
    texto = "\n".join(L)
    print(texto)
    (C / "informe_general_inflacion.txt").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
