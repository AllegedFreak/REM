# Fórmulas de Datos Con Método

Cómo se arma cada número que publicamos. Una sección por estimación: qué pide el REM, de qué está hecha, la fórmula,
de dónde sale cada parte y cómo se verificó. Creado el 30-oct-2026 en la revisión general previa al sello n.º 2.

Convenciones:
- `t` = mes del relevamiento del REM (el BCRA releva los últimos 3 días hábiles de t). `t-1` = mes anterior, etc.
- "Lo conocido en t" = lo publicado hasta el día del relevamiento. Nunca se usa un dato que todavía no existía.
- `log` = logaritmo natural. "Variación en log" = 100 · (log(a) − log(b)), casi igual a la variación % cuando es chica.
- Huber = regresión robusta (le quita peso a los meses raros). "Ventana acumulada" = se estima con todos los
  relevamientos anteriores a t (mínimo 24 salvo que se diga otra cosa).
- REM anterior = mediana del REM de t-1 para el mismo período.

---

## 1. Inflación mensual (IPC nacional, INDEC) — "I"

**Qué pide el REM:** variación % mensual del IPC de t a t+6 y de los próximos 12 meses.
**Lo que publicamos:** I (`replicar_rem.py`, `datos\registro_inflacion.csv`). Decidido el 16-oct-2026.

**Composición (m0 a m6):** regresión Huber, una por horizonte h, entrenada con la mediana del REM como objetivo
(el REM hace de "maestro"; no entra como dato) y evaluada contra el IPC real:

    I(t, h) = a_h + b1·nuc1 + b2·nuc_2 + b3·nuc_3 + b4·p12 + b5·dev0 + b6·dev1 + b7·emiT12 + b8·ei_prom_m

| Variable | Qué es | Fuente | Período |
|---|---|---|---|
| nuc1, nuc_2, nuc_3 | IPC núcleo, var. % mensual | INDEC `serie_ipc_divisiones.csv` | t-1, t-2, t-3 |
| p12 | IPC general, var. % del mismo mes un año antes (estacionalidad) | INDEC | t+h-12 |
| dev0 | 100·[log(A3500 promedio de t hasta el día 25) − log(A3500 promedio de t-1)] | BCRA API v4, id 5 | t |
| dev1 | 100·[log(A3500 prom. t-1) − log(A3500 prom. t-2)] | BCRA | t-1 |
| emiT12 | suma de 12 meses de los "factores de explicación de la base" ligados al Tesoro, en % de la base | BCRA (`emision_mensual.csv`) | t-12 a t-1 |
| ei_prom_m | expectativa de inflación de los hogares a 12 meses (promedio), llevada a mensual: ((1+EI/100)^(1/12) − 1)·100 | UTDT | t (si no salió, t-1) |

**Con dólar agitado:** si A-dólar espera más de 3% de devaluación para el mes (`UMBRAL_DOLAR`), se usa "I+dólar"
(las mismas variables + `dolar_m` y `dolar_m1`, la devaluación que espera A-dólar para el mes y el anterior).

**Mes en curso (m0):** I corregido con el termómetro de supermercados (sección 10):

    I_m0 = I(t,0) + c + d · termómetro(t)        (c y d estimados con los meses ya evaluados)

**Años (dic/dic) y próximos 24 meses (desde el 30-oct-2026):**

    año en curso = Π meses publicados × Π (1 + I_m0..m6) × (tasa del 12m para los meses que falten)
    r_k = mediana de log(1 + π_{t+k}) / log(1 + π_t) en la clase de referencia C: países con inflación dic/dic entre 20% y
          40%, más baja que el año anterior y resultado fiscal del gobierno general >= 0 (FMI, WEO, 1980 al último año ocurrido)
    año siguiente (k=1) y año +2 (k=2) = (1 + año en curso)^{r_k} − 1
    meses sin pronóstico propio = tasa mensual constante dentro de cada año, la que cierra el año en su valor
    (general G, desde el 30-oct-2026) bajada gradual: log(1+π_m) sobre una línea que une el último mes con pronóstico propio
          con el promedio de lo que falta de cada año (en el centro de esos meses), misma pendiente después; cada año se
          corre en paralelo para conservar su total dic/dic. La núcleo sigue con tasa constante.
    próx. 24 meses = inflación del segundo año (t+13 a t+24, definición del BCRA) = producto de ese camino mensual

**Próximos 12 meses:** Huber en logaritmos: log(1+I12) ~ inf12 + ei_prom_l + fut12, donde inf12 = 100·log(IPC t-1 / IPC t-13),
ei_prom_l = 100·log(1+EI/100), fut12 = devaluación esperada a un año (futuro a 9 meses llevado a 12: 100·log(F9/spot)·12/9).

**Verificación (30-oct-2026):** IPC, núcleo y categorías idénticos al archivo del INDEC (`verificar_todo.py`) y
coincidentes con el anuncio del 13-oct (1,9% general, 1,7% núcleo, 33,4% interanual). Insumos de hoy revisados en
`datos\pronostico_insumos.csv`. **Pendiente:** EI de octubre (ver hallazgos).

## 2. Inflación general "desde abajo" y núcleo (todavía NO publicadas)

`replicar_general.py` y `replicar_nucleo.py`. Para m0-m2:

    G = s_núcleo · N + s_regulados · Reg + s_estacionales · Est

- s_* = peso efectivo al último mes publicado: peso de base (dic-2016: núcleo 67,76%, regulados 20,87%, estacionales
  11,33%) × índice de la categoría / índice general; normalizados a 100%.
- Reg = promedio ponderado (ponderaciones INDEC de cada rubro) de: aumentos nacionales confirmados del calendario
  (`regulados_anunciados.csv`; el anuncio oficial manda) + en m0 combustibles medidos + en m0-m1 prepagas sin anuncio
  = IPC general de dos meses antes; el resto de los rubros al promedio de 3 meses de los regulados.
- Est = promedio de los últimos 12 meses de los estacionales + desvío típico de ese mes del año (3 años anteriores).
- m3 en adelante: I.

## 3. Dólar mayorista (A3500, promedio del mes) — "A-dólar"

**Lo que publicamos:** A-dólar (`replicar_tc.py`), meses t+1 a t+6, próximos 12 meses y diciembre de cada año.

    futuro_prom(h) = √( F(t+h-1) · F(t+h) )         F(m) = ajuste del contrato DLR del mes m (paga el A3500 del último día de m)
                                                   para h = 1, F(t) se reemplaza por el A3500 del día de la curva
    sin contrato (h mayor al más largo, n meses): futuro_prom(h) = spot · (F(n)/spot)^((h − 0,5)/n)
    descuento(h) = promedio de log(REM / futuro_prom) en los últimos 12 relevamientos para ese h
    A-dólar(h) = futuro_prom(h) · exp(descuento(h))

Fuentes: futuros CEM Matba-Rofex/A3 (`dolar_futuro_cem.parquet`), curva del último día hábil; A3500 BCRA id 5.
**Verificación (30-oct-2026):** recalculado a mano h1 = 1.562,30 y h2 = 1.592,59, idénticos al publicado.

## 4. PIB trimestral (var. % desestacionalizada contra el trimestre anterior) — "I-PIB"

**Lo que publicamos:** I-PIB (`replicar_pib.py`), "camino coherente" desde el 30-oct-2026 (decisión de Marcos).

    u = t − 2 (último EMAE publicado)
    p3 = promedio del EMAE desestacionalizado de u, u-1, u-2
    g = (promedio EMAE últimos 12 meses / promedio EMAE de 5 años antes)^(1/60)      (crecimiento mensual de tendencia)
    EMAE(m) = dato publicado si m ≤ u;   p3 · g^(m − u) si m > u                     (un solo camino para todos los trimestres)
    PIB_Q = [promedio de EMAE(m) de los 3 meses de Q / promedio de los 3 meses de Q-1 − 1] · 100
    si Q no tiene ningún mes de EMAE publicado:  I-PIB_Q = (PIB_Q + REM anterior_Q) / 2

Fuentes: EMAE desestacionalizado del INDEC (`emae.parquet`); REM normalizado. Real = primer dato que publica el INDEC
(`pib_desest_versiones.parquet`, 42 versiones).
**Verificación:** el script reproduce la prueba (`probar_pib_horizonte.py`): 2025-26 error 0,26 / 0,39 / 0,47 pp.

## 5. PIB anual (var. % del promedio anual) — "I tend" (en el sitio, NO en el sello)

    niveles: trimestres publicados (última versión del INDEC) → trimestres de I-PIB → después, crecimiento por trimestre
             de (g^3 − 1)·100 (el mismo ritmo de tendencia del EMAE; hoy +0,53%)
    PIB anual(a) = [promedio de los 4 trimestres de a / promedio de los 4 de a−1 − 1] · 100

Decidido el 30-oct-2026 por coherencia (la prueba no distingue entre 0%, promedio de 20 trimestres y tendencia).

## 6. Desocupación (EPH, 31 aglomerados) — "I"

    estacionalidad(q) = promedio, en los años completos sin pandemia (se excluyen II-2020 a I-2021 y todo 2020),
                        del desvío de la tasa del trimestre q respecto del promedio de su año; centrada en 0
    I(Q) = último dato − estacionalidad(trimestre del último dato) + estacionalidad(trimestre de Q)
    IV trim. del año siguiente (desde el 30-oct-2026) = I(IV trim. anterior) + [REM anterior(IV trim. siguiente) − REM anterior(IV trim. en curso)]

Fuente: INDEC, EPH (`desempleo.parquet`). "Año" del REM = IV trimestre. Decidido el 28-oct-2026.
**Verificación (30-oct-2026):** recalculado 7,34 / 7,03 / 8,00 (III-26, IV-26, I-27), idéntico al publicado. Último dato
7,9% (II-2026) confirmado con el anuncio del INDEC del 17-sep.

## 7. Tasa TAMAR (bancos privados, TNA, promedio del mes) — "A-tasa"

    r_now = promedio de la TAMAR de t hasta el día 25 (mínimo 8 días)
    fwd_h = tasa forward del mes t+h implícita en los futuros de dólar: cociente de contratos consecutivos, anualizado
    A(h) = r_now + Huber_h( (fwd_h − r_now), (r_now − tasa de t-1), tasa real )

    plazos largos (definiciones oficiales del BCRA): próx. 12 meses = TAMAR promedio del mes t+12; año = TAMAR promedio
    de diciembre de ese año (el año en curso = el mes de diciembre ya pronosticado)
    (desde el 30-oct-2026, Fisher) TAMAR(m) = [(1 + inflación esperada de m) × (1 + tasa real esperada) − 1] × 365/30
        inflación esperada de m = camino mensual de la sección 1 (I hasta t+6; después, clase de referencia)
        tasa real esperada = (1 + TAMAR del mes en TEM) / (1 + nuestra inflación del mes siguiente) − 1
    (modelo anterior, solo para comparar: A_grupo = r_now + Huber_grupo(forward − r_now, cambio del mes, tasa real))
Fuente: BCRA API v4 id 44 ("Tasa de interés TAMAR de bancos privados", TNA; el REM pide TNA). Hasta nov-2024 el REM
pedía BADLAR (id 7). **Verificación:** r_now 24,25% recalculado (16 días de octubre).

## 8. Resultado primario del SPN (año calendario, billones de $) — "I-fiscal"

**PUBLICADO desde el 30-oct-2026 (OK de Marcos): "partidas", sin meta.**

    mes que falta m (millones de $) = mismo mes del año anterior × crecimiento de su base:
      IVA, Ganancias, Débitos y créditos, resto de ingresos : (IPC_m / IPC_m−12) × (1 + PIB real del año)
      Seguridad social                                       : IPC_m / IPC_m−12
      Derechos de exportación                                : TC_m / TC_m−12            (A3500, promedio mensual)
      Gasto indexado (jubilaciones, AUH, pensiones no contr.) : IPC_m−2 / IPC_m−14        (movilidad con 2 meses de rezago)
      Resto del gasto primario                               : IPC_m / IPC_m−12          (constante en términos reales)
    resultado del año = meses publicados del IMIG + Σ (ingresos − gastos) de los meses proyectados
    IPC y TC: reales hasta el último mes; después, medianas mensuales del último REM, completando los meses sin dato en
    forma geométrica para cerrar con el anual (IPC dic/dic; TC promedio de diciembre). PIB real: mediana anual del REM.
    Backtest (probar_fiscal_partidas.py, relevamientos ene-24 a nov-25, error medio % PIB): partidas+meta 1,46 vs con meta
    1,76; partidas solo 2,46 (el cambio de régimen de 2024 no lo ve ningún modelo; REM 0,34).

Anterior (18 al 30-oct-2026), queda como referencia en el informe:

    proyección sin meta: meses publicados del IMIG + meses que faltan = mismo mes del año anterior × (1 + inflación
                          interanual del último IPC), ingresos y gastos por separado
    I = (k/12) · proyección + (1 − k/12) · meta vigente     (k = meses publicados del año; todo en % del PIB)
    año siguiente: los 12 meses proyectados; sin meta conocida, I = proyección

Fuentes: IMIG (Secretaría de Hacienda, datos.gob.ar 452.3; copias en `datos\copias\imig`); metas en
`datos\metas_fiscales.csv` (a mano); PIB nominal INDEC (si falta el año, el último × crecimiento de los ingresos).
**Verificación (30-oct-2026):** IMIG idéntico al informe oficial (sep +1.427.640, ago +1.990.322 millones); recálculo
13.861 contra 13.874 publicado (diferencia de redondeo de la escala).

## 9. Exportaciones e importaciones (millones de USD) — "A"

    base(m) = valor del mismo mes del año anterior
    g3, g12 = crecimiento interanual de los últimos 3 y 12 meses conocidos; ult = log(último mes / mismo mes un año antes)
    A(m) = base(m) · exp( Huber_h(g3, g12, ult [+ precios de granos y aceites del Banco Mundial en exportaciones]) )
    año en curso: meses conocidos + A encadenado: A(m) = A(m−12) · exp(ŷ_h), con h ≥ 6 usando la relación de h6
    año siguiente, IMPORTACIONES (desde el 30-oct-2026): año en curso · exp( Huber( log(REM año+1 / REM año en curso) ~
                  g3, g12, ult, mes del relevamiento ) )   — "como el REM"; ventana acumulada, mín. 24 informes
    EXPORTACIONES (desde el 30-oct-2026, exportaciones_v2 en replicar_comercio.py):
      sesgo_h = promedio de log(REM / real) de los últimos 12 errores del REM ya conocidos en el horizonte h
      freno   = sin corrección si alguno de los 3 últimos errores del REM para el mes en curso es de signo contrario al sesgo
                y mayor a 2%
      meses 0-3: REM anterior del mismo mes · exp(−sesgo_h)          (con freno)
      meses 4-6: A_stl = [exp(tendencia STL de m−12 + estacionalidad STL más reciente del mes)] · exp(Huber_h(...))
                 (STL robusto, período 12, 15 años)
      año en curso: meses conocidos + los meses de arriba; más allá de t+6, mismo mes del año anterior · relación de h6 de A_stl
      año siguiente: REM anterior del año · exp(−sesgo del REM en el año siguiente, últimos 12 informes con el año cerrado)

Fuente: INDEC, intercambio comercial (datos.gob.ar 74.3). **Verificación:** septiembre 10.036 / 7.060 confirmado.

## 10. Supermercados (termómetro SEPA, % en el mes)

    índice diario = canasta del INDEC con precios SEPA (Secretaría de Comercio), por región, encadenado
    termómetro(t) = promedio del índice de t (días 1 a 25 o hasta hoy) / promedio del mes t−1 completo − 1

## 11. Cálculos auxiliares

- **Brecha:** (CCL / A3500 − 1)·100, días hábiles comunes, sin días "congelados" (8+ días iguales). CCL de argentinadatos.com.
- **Índice de combustibles:** encadenado de las mismas estaciones: entre dos cortes, media geométrica de precio_b/precio_a
  de las estaciones con precio vigente en los dos (cada precio rige 60 días); nafta súper y premium, media geométrica.
  Fuente: Secretaría de Energía (Res. 314/2016).
- **Promedio mensual del A3500:** media simple de los días hábiles del mes.

---

## Revisión del 30-oct-2026: qué se verificó contra la fuente oficial

| Dato del sitio | Nuestro | Fuente oficial | Estado |
|---|---|---|---|
| IPC sep-2026 | 1,93% (núcleo 1,7%, ia 33,4%) | INDEC | ✔ |
| A3500 28-oct | 1.538,47 | BCRA API id 5 | ✔ |
| TAMAR 27-oct | 24,5625 | BCRA API id 44 | ✔ |
| PIB II-2026 | −0,59% | INDEC (17-sep) | ✔ |
| Desocupación II-2026 | 7,9% (un año antes 7,6%) | INDEC (17-sep) | ✔ |
| SIPA jun-2026 | 12,773 millones | datos.gob.ar 151.1 | ✔ |
| Resultado primario ene-sep | 13,7 billones | Hacienda | ✔ |
| Exportaciones / importaciones sep | 10.036 / 7.060 | INDEC (19-oct) | ✔ |
| Dólar y TAMAR promedio sep | 1.513,9 / 23,79 | BCRA | ✔ |
| CCL / MEP | 1.665,4 / 1.573,1 (29-oct) | — | ✘ fecha corrida un día desde sep-2026 |
| Supermercados oct | +2,47% | propio | ⚠ septiembre sin los días 23-30 |
