/* Datos Con Método — comportamiento de la página. Lee data/*.json (los arma publicar_web.py). */
(function () {
  "use strict";

  // ---------- utilidades
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, attrs = {}, ...hijos) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "html") e.innerHTML = v;
      else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v);
    }
    for (const h of hijos) if (h !== null && h !== undefined) e.append(h.nodeType ? h : document.createTextNode(h));
    if (tag === "a") externo(e);
    return e;
  };
  // los enlaces que salen del sitio se abren en una pestaña nueva
  function externo(a) {
    const h = a.getAttribute("href") || "";
    if (/^https?:\/\//i.test(h) && !h.includes(location.host || "@@")) {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    }
  }
  document.querySelectorAll("a[href]").forEach(externo);
  const nf = (d) => new Intl.NumberFormat("es-AR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : nf(d).format(v));
  const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const mesTxt = (s) => {                       // "2026-09" o "2026-09-30" -> "sep-2026"
    if (!s) return "—";
    const m = /^(\d{4})-(\d{2})/.exec(s);
    return m ? `${MESES[+m[2] - 1]}-${m[1]}` : s;
  };
  const fechaTxt = (s) => {                     // "2026-10-23" -> "23-oct-2026"
    if (!s) return "—";
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    return m ? `${+m[3]}-${MESES[+m[2] - 1]}-${m[1]}` : s;
  };
  const periodoTxt = (s) => (/^\d{4}-\d{2}$/.test(s) ? mesTxt(s) : s);
  // reintenta: mientras el sitio se actualiza (dos veces por día) un archivo puede faltar unos segundos
  const leer = async (url, intentos = 4) => {
    for (let i = 1; ; i++) {
      try {
        const r = await fetch(url, { cache: "no-cache" });
        if (!r.ok) throw new Error(`${url}: ${r.status}`);
        return await r.json();
      } catch (e) {
        if (i >= intentos) throw e;
        await new Promise((ok) => setTimeout(ok, 1500 * i));
      }
    }
  };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const FREC = { D: "Diaria", M: "Mensual", T: "Trimestral", A: "Anual" };
  const ESTADO_TXT = { verde: "Al día", amarillo: "Algo atrasada", rojo: "Atrasada", gris: "Histórica" };
  const puntoEstado = (col, txt) => el("span", { class: "estado" }, el("span", { class: `punto ${col}`, "aria-hidden": "true" }), txt || ESTADO_TXT[col] || col);

  // ---------- menú desplegable "Pronósticos" (se posiciona fijo debajo del botón: la barra de menú se desplaza en el celular)
  (function () {
    const grupo = $("#grupo-pron"), btn = $("#btn-pron"), menu = $("#menu-pron");
    if (!grupo) return;
    let cierre = null;
    const ubicar = () => {
      const r = btn.getBoundingClientRect();
      menu.style.top = `${r.bottom + 6}px`;
      menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    };
    const abrir = () => { clearTimeout(cierre); menu.classList.add("abierto"); btn.setAttribute("aria-expanded", "true"); ubicar(); };
    const cerrar = () => { clearTimeout(cierre); menu.classList.remove("abierto"); btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", (e) => { e.stopPropagation(); menu.classList.contains("abierto") ? cerrar() : abrir(); });
    if (window.matchMedia("(hover: hover)").matches) {
      grupo.addEventListener("mouseenter", abrir);
      grupo.addEventListener("mouseleave", () => { cierre = setTimeout(cerrar, 200); });
    }
    menu.addEventListener("click", (e) => { if (e.target.closest("a")) cerrar(); });
    document.addEventListener("click", (e) => { if (!grupo.contains(e.target)) cerrar(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu.classList.contains("abierto")) { cerrar(); btn.focus(); } });
    window.addEventListener("resize", () => { if (menu.classList.contains("abierto")) ubicar(); });
    window.addEventListener("scroll", () => { if (menu.classList.contains("abierto")) ubicar(); }, { passive: true });
    $(".nav").addEventListener("scroll", cerrar, { passive: true });
  })();

  // ---------- tema claro / oscuro
  const raiz = document.documentElement;
  try { const t = localStorage.getItem("dcm-tema"); if (t) raiz.dataset.theme = t; } catch (e) { /* sin almacenamiento */ }
  $("#tema").addEventListener("click", () => {
    const oscuro = raiz.dataset.theme ? raiz.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    raiz.dataset.theme = oscuro ? "light" : "dark";
    try { localStorage.setItem("dcm-tema", raiz.dataset.theme); } catch (e) { /* nada */ }
    redibujar();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redibujar);

  // ---------- decimales por variable
  const DEC = { inflacion: 2, nucleo: 2, dolar: 0, pib: 2, pib_anual: 2, desempleo: 2, tasa: 2, fiscal: 1, exportaciones: 0, importaciones: 0 };

  // ---------- portada
  function portada(R, F) {
    const al = F.filter((f) => f.estado === "verde").length;
    const vivas = F.filter((f) => f.estado !== "gris").length;
    const tiles = $("#tiles");
    const tile = (et, val, sub) => el("div", { class: "tile" }, el("p", { class: "tile-et", text: et }), val, sub ? el("p", { class: "tile-sub", text: sub }) : null);
    tiles.append(
      tile("Series abiertas", el("p", { class: "tile-val", text: nf(0).format(R.conteo.series) }), `${nf(0).format(R.conteo.datos)} datos`),
      tile("Series al día", el("p", { class: "tile-val", text: `${nf(0).format(R.conteo.series_al_dia)} de ${nf(0).format(R.conteo.series_vigentes)}` }),
        `${al} de ${vivas} fuentes al día · ${nf(0).format(R.conteo.series_historicas)} series históricas no cuentan`),
    );
    // semáforo de datos: qué parte de los datos que usan los pronósticos está al día (verde >= 90%, amarillo >= 75%, rojo < 75%)
    if (R.semaforo_datos) {
      const d = R.semaforo_datos;
      const v = el("p", { class: "tile-val" }, puntoEstado(d.color, `${d.color.charAt(0).toUpperCase()}${d.color.slice(1)}`));
      v.querySelector(".estado").style.fontSize = "1.4rem";
      v.querySelector(".punto").style.cssText = "width:16px;height:16px";
      const sub = `${fmt(d.pct, 0)}% de los datos al día` + (d.atrasados.length ? ` · atrasado: ${d.atrasados.join(", ")}` : "");
      const t = tile("Datos de los pronósticos", v, sub);
      t.title = "Verde: 90% o más de los datos que usan los pronósticos están al día. Amarillo: entre 75% y 90%. Rojo: menos de 75%.";
      tiles.append(t);
    }
    // los datos de hoy: último valor de las variables clave
    const hoy = $("#hoy");
    for (const x of R.indicadores || []) {
      const fecha = /^\d{4}-\d{2}-\d{2}$/.test(x.fecha) ? fechaTxt(x.fecha) : /^\d{4}-\d{2}$/.test(x.fecha) ? mesTxt(x.fecha) : x.fecha.replace("T", " T");
      const pre = { dolar: "$", ccl: "$", mep: "$", fiscal: "B$ " }[x.clave] || "";
      const suf = x.unidad.startsWith("%") || x.clave === "tamar" ? "%" : "";
      const val = `${pre}${fmt(x.valor, x.dec)}${suf}`;
      // enlaces al pie: "Ver pronóstico" (solo las que pide el REM) y "Ver serie histórica" (la serie en la sección Datos)
      const destino = { inflacion: "inflacion", dolar: "dolar", pib: "pib", desempleo: "desempleo", fiscal: "fiscal", tamar: "tasa" }[x.clave];
      const serie = SERIE_HOY[x.clave];
      const pie = el("p", { class: "tile-fuente" }, `Fuente: ${x.fuente}`);
      if (destino) pie.append(" · ", el("a", { href: `#pron-${destino}`, text: "Ver pronóstico →" }));
      if (serie) {
        // lleva a la página propia de la serie (gráfico, últimos datos, CSV): indexable por buscadores
        pie.append(" · ", el("a", { href: `serie/${encodeURIComponent(serie)}.html`, text: "Ver serie histórica →" }));
      }
      hoy.append(el("div", { class: "tile tile-hoy" },
        el("p", { class: "tile-et" }, x.titulo, el("span", { class: "tile-fecha", text: fecha })),
        el("p", { class: "tile-val", text: val }),
        el("p", { class: "tile-uni", text: x.unidad.startsWith("%") ? x.unidad.slice(1).trim() : x.clave === "tamar" ? "TNA" : x.unidad }),
        x.detalle ? el("p", { class: "tile-sub", text: x.detalle }) : null,
        pie));
    }
    $("#actualizado").textContent = `Actualizado: ${R.generado}`;
    $("#pie-act").textContent = `Última actualización: ${R.generado}`;
  }

  // ---------- pronósticos de hoy
  // valor con su signo: $ en el dólar, B$ en el resultado primario, % en inflación, PIB y tasa (dif con signo + o −)
  function fv(clave, v, d, conSigno = false) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const pre = { dolar: "$", fiscal: "B$ " }[clave] || "";
    const suf = ["inflacion", "nucleo", "pib", "pib_anual", "desempleo", "tasa"].includes(clave) ? "%" : "";
    const signo = conSigno ? (v > 0 ? "+" : v < 0 ? "−" : "") : (v < 0 ? "−" : "");
    return `${signo}${pre}${fmt(Math.abs(v), d)}${suf}`;
  }
  function tarjetas(R) {
    const cont = $("#tarjetas");
    // el próximo REM es el del mes de la corrida (el relevamiento en curso); la referencia, el último REM publicado (8-nov-2026)
    const proximo = mesTxt(R.generado);
    $("#pron-tit").textContent = `Pronóstico para el próximo REM (${proximo})`;
    $("#pron-intro").textContent = `Estimaciones de Datos Con Método para el REM de ${proximo} vs. la mediana del último REM publicado (${mesTxt(R.rem_ultimo)}) para el mismo período.`;
    for (const t of R.pronosticos) {
      const d = DEC[t.clave] ?? 2;
      const prim = t.filas.find((f) => f.nuestro !== null) || t.filas[0];
      const tb = el("table", { class: "tabla" },
        el("thead", {}, el("tr", {}, el("th", { text: "Período" }), el("th", { class: "num", title: "Datos Con Método" }, el("span", { class: "marca-nuestro", text: "DCM*" })),
          el("th", { class: "num", title: "Mediana del último REM publicado por el BCRA" }, el("span", { class: "marca-rem", text: `Último REM (${mesTxt(R.rem_ultimo)})` })))));
      const tbody = el("tbody");
      for (const f of t.filas) {
        tbody.append(el("tr", {}, el("td", { text: periodoTxt(f.periodo) }), el("td", { class: "num", text: fv(t.clave, f.nuestro, d) }),
          el("td", { class: "num", text: fv(t.clave, f.rem, d) })));
      }
      tb.append(tbody);
      cont.append(el("article", { class: "tarjeta", id: `pron-${t.clave}` },
        el("h3", { text: t.titulo }), el("p", { class: "unidad", text: t.unidad }),
        el("p", { class: "destacado" }, el("span", { class: "num-grande", text: fv(t.clave, prim.nuestro, d) }), el("span", { class: "per", text: periodoTxt(prim.periodo) })),
        el("div", { class: "desborde" }, tb),
        t.nota ? el("p", { class: "nota", text: t.nota }) : null,
        el("p", { class: "registro", text: `Registrado ${t.corrida} · informe de ${mesTxt(t.relevamiento)}` })));
    }
  }

  // ---------- cómo nos fue
  const graficos = [];
  const ETIQ_H = (h) => {
    const s = String(h);
    if (/^m\d$/.test(s)) return s === "m0" ? "mes en curso" : `a ${s.slice(1)} ${s === "m1" ? "mes" : "meses"}`;
    if (s === "12m") return "a 12 meses";
    if (s === "año") return "año en curso";
    if (s === "año+1") return "año siguiente";
    return null;
  };
  function etiquetaH(clave, h) {
    // desocupación: horizonte contado desde el último trimestre publicado; el "año" del REM es el IV trimestre
    if (clave === "desempleo") return { h1: "1.er trim. sin dato", h2: "2.º trim. sin dato", h3: "3.er trim. sin dato", "año": "IV trim. del año" }[String(h)] || h;
    const g = ETIQ_H(h);
    if (g) return g;
    if (clave === "pib") return { "-1": "trim. anterior", "0": "trim. en curso", "1": "trim. siguiente" }[String(h)] || h;
    if (clave === "fiscal") return { "0": "año en curso", "1": "año siguiente" }[String(h)] || h;
    const n = +h;
    return `a ${n} ${n === 1 ? "mes" : "meses"}`;
  }
  function bloques(R) {
    const cont = $("#bloques");
    for (const b of R.historial) {
      const d = b.error === "pct" ? 1 : 2;
      const unidadErr = b.error === "pct" ? "%" : "";
      const tb = el("table", { class: "tabla" });
      tb.append(el("thead", {},
        el("tr", {}, el("th", { text: "" }), el("th", { class: "num", colspan: "3", text: "2025-26 · prueba hacia atrás (simulada)" })),
        el("tr", {}, el("th", { text: "Horizonte" }), el("th", { class: "num", title: "Datos Con Método" }, el("span", { class: "marca-nuestro", text: "DCM*" })),
          el("th", { class: "num" }, el("span", { class: "marca-rem", text: "REM" })),
          el("th", { class: "num sub-col", title: "Porcentaje de veces que el pronóstico de DCM* quedó más cerca del dato real que el REM", text: "% de veces más cerca del dato que el REM" }))));
      const tbody = el("tbody");
      for (const f of b.filas) {
        const a = f.actual;
        tbody.append(el("tr", {},
          el("td", { text: etiquetaH(b.clave, f.h) }),
          el("td", { class: "num", text: a ? fmt(a.nuestro, d) + unidadErr : "—" }),
          el("td", { class: "num", text: a ? fmt(a.rem, d) + unidadErr : "—" }),
          el("td", { class: "num", text: a ? `${fmt(a.gana_pct, 0)}% (n ${a.n})` : "—" })));
      }
      tb.append(tbody);
      // sin insignia de "mejor que el REM": la tabla es una prueba hacia atrás simulada, no pronósticos sellados (Marcos, 30-oct-2026)
      const canvas = el("canvas", { role: "img", "aria-label": `${b.titulo}: dato real, REM y pronóstico de Datos Con Método` });
      const art = el("article", { class: "bloque" },
        el("div", {}, el("h3", { text: b.titulo }), el("p", { class: "unidad", text: `Error medio en ${b.unidad}` }),
          el("div", { class: "tabla-scroll", style: "border:0" }, tb),
          el("p", { class: "nota", text: "Prueba hacia atrás (simulada): en cada fecha pasada se rehízo el pronóstico usando solo los datos publicados hasta ese momento. No son pronósticos sellados en su día. \"% de veces más cerca del dato que el REM\": porcentaje de comparaciones en que el pronóstico de DCM* quedó más cerca del dato real que la mediana del REM; n = cantidad de comparaciones." })),
        el("div", {}, el("div", { class: "grafico" }, canvas), el("p", { class: "nota", text: b.nota })));
      cont.append(art);
      graficos.push({ canvas, b, chart: null });
    }
    redibujar();
  }
  function redibujar() {
    if (!window.Chart) return;
    const tinta2 = css("--tinta-2"), linea = css("--linea"), sup = css("--superficie");
    for (const g of graficos) {
      if (g.chart) g.chart.destroy();
      const gr = g.b.grafico;
      const d = DEC[g.b.clave] ?? 2;
      const serie = (label, data, color, ancho, guion) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: ancho,
        pointRadius: 0, pointHoverRadius: 4, pointHoverBorderColor: sup, pointHoverBorderWidth: 2, borderDash: guion || [], spanGaps: false, tension: 0 });
      g.chart = new Chart(g.canvas, {
        type: "line",
        data: { labels: gr.x.map(periodoTxt), datasets: [serie("Dato real", gr.real, css("--real"), 2), serie("REM", gr.rem, css("--rem"), 2), serie("DCM*", gr.nuestro, css("--acento"), 2)] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: true, position: "top", align: "start", labels: { color: tinta2, boxWidth: 14, boxHeight: 2, font: { family: "IBM Plex Sans" } } },
            tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${fmt(c.parsed.y, d)}` } },
          },
          scales: {
            x: { grid: { display: false }, ticks: { color: tinta2, maxRotation: 0, autoSkip: true, maxTicksLimit: 7, font: { family: "IBM Plex Mono", size: 11 } }, border: { color: css("--eje") } },
            y: { grid: { color: linea }, ticks: { color: tinta2, font: { family: "IBM Plex Mono", size: 11 }, callback: (v) => fmt(v, d > 1 ? 1 : d) }, border: { display: false } },
          },
        },
      });
    }
  }

  // ---------- datos: fuentes con sus series y tablas, buscador arriba
  let FUENTES = [], CAT = [];
  const POR_FUENTE = new Map();
  const SEL = new Map();
  const MAX_SEL = 40, PASO = 25;
  const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const peso = (kb) => (kb >= 1024 ? `${nf(1).format(kb / 1024)} MB` : `${nf(0).format(kb)} KB`);
  const hoyTxt = () => new Date().toISOString().slice(0, 10);
  const fechaDato = (f, fr) => (!f ? "—" : fr === "D" ? fechaTxt(f) : fr === "A" ? f.slice(0, 4) : mesTxt(f));

  const filasTxt = (n, grande) => el("span", { class: `filas${grande ? " filas-grande" : ""}`,
    title: grande ? "Más de 100.000 filas: no entra cómodo en Excel" : "" }, `${nf(0).format(n)} filas`);
  const formatos = (x) => el("div", { class: "formatos" }, ...x.archivos.map((a) =>
    el("a", { class: "fmt", href: `descargas/${a.archivo}`, download: "" }, a.formato, el("span", { class: "fmt-peso", text: peso(a.peso_kb) }))));

  function filtros() {
    return { q: norm($("#busca").value).split(/\s+/).filter(Boolean), fuente: $("#f-fuente").value, frec: $("#f-frec").value, estado: $("#f-estado").value };
  }
  const hayFiltro = (F) => F.q.length || F.fuente || F.frec || F.estado;
  function coincide(s, F) {
    if (F.frec && s.frecuencia !== F.frec) return false;
    if (F.estado === "atrasadas" ? !(s.estado === "rojo" || s.estado === "amarillo") : F.estado && s.estado !== F.estado) return false;
    return F.q.every((w) => s._txt.includes(w));
  }

  // descarga de una o varias series (armado en el navegador)
  async function datosDe(series, desde, hasta) {
    const datos = await Promise.all(series.map((s) => leer(`data/s/${encodeURIComponent(s.id)}.json`)));
    const filas = new Map();
    datos.forEach((d, j) => d.d.forEach((f, i) => {
      if (f < desde || f > hasta) return;
      if (!filas.has(f)) filas.set(f, new Array(series.length).fill(null));
      filas.get(f)[j] = d.v[i];
    }));
    const fechas = [...filas.keys()].sort();
    return { fechas, filas };
  }
  const metaFilas = (series) => [["id", "nombre", "fuente", "unidad", "frecuencia", "primer dato", "último dato", "dirección de la fuente", "cómo citar"],
    ...series.map((s) => [s.id, s.nombre, s.institucion || s.fuente, s.unidad || "", FREC[s.frecuencia] || s.frecuencia, s.desde, s.hasta, s.url || "", s.cita || ""])];
  const leemeTxt = (series, desde, hasta) => ["Datos Con Método (www.datosconmetodo.com) · Marcos Cavanagh",
    `Descargado: ${new Date().toISOString().slice(0, 16).replace("T", " ")}`,
    `Período: ${desde.replace("0000-01-01", "inicio")} a ${hasta.replace("9999-12-31", "último dato")}`,
    "Celdas vacías: la serie no tiene dato en esa fecha (no se rellenan).",
    "Cada serie conserva la licencia de su productor. Las series de Datos Con Método: licencia CC BY 4.0.", "", "Cómo citar:",
    ...series.map((s) => `  ${s.cita || s.nombre}`)].join("\r\n");
  async function bajarSeries(series, formato, desde = "0000-01-01", hasta = "9999-12-31") {
    const { fechas, filas } = await datosDe(series, desde, hasta);
    const encabezado = ["fecha", ...series.map((s) => s.nombre)];
    const base = series.length === 1 ? series[0].id : `datos_con_metodo_${hoyTxt()}`;
    if (formato === "xlsx") {
      if (!window.XLSX) throw new Error("todavía se está cargando el armador de Excel");
      const wb = XLSX.utils.book_new();
      const ws = XLSX.utils.aoa_to_sheet([encabezado, ...fechas.map((f) => [f, ...filas.get(f)])]);
      ws["!cols"] = [{ wch: 12 }, ...series.map(() => ({ wch: 18 }))];
      XLSX.utils.book_append_sheet(wb, ws, "datos");
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(metaFilas(series)), "series");
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(leemeTxt(series, desde, hasta).split("\r\n").map((l) => [l])), "leeme");
      XLSX.writeFile(wb, `${base}.xlsx`);
    } else {
      if (!window.JSZip) throw new Error("todavía se está cargando el armador de ZIP");
      const csv = (aoa) => "﻿" + aoa.map((r) => r.map((v) => (v === null || v === undefined ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v)).join(",")).join("\r\n");
      const zip = new JSZip();
      zip.file("datos.csv", csv([encabezado, ...fechas.map((f) => [f, ...filas.get(f)])]));
      zip.file("series.csv", csv(metaFilas(series)));
      zip.file("LEEME.txt", leemeTxt(series, desde, hasta));
      const blob = await zip.generateAsync({ type: "blob", compression: "DEFLATE" });
      const a = el("a", { href: URL.createObjectURL(blob), download: `${base}.zip` });
      document.body.append(a); a.click(); a.remove();
    }
    return fechas.length;
  }

  function filaSerie(s) {
    const cb = el("input", { type: "checkbox", "aria-label": `Elegir ${s.nombre}` });
    cb.checked = SEL.has(s.id);
    cb.addEventListener("change", () => {
      if (cb.checked) {
        if (SEL.size >= MAX_SEL) { cb.checked = false; $("#aviso").textContent = `Máximo ${MAX_SEL} series por archivo.`; return; }
        SEL.set(s.id, s);
      } else SEL.delete(s.id);
      actualizarSel();
    });
    const boton = (fmt, txt) => {
      const b = el("button", { type: "button", class: "fmt", title: `Bajar esta serie en ${txt}` }, txt);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await bajarSeries([s], fmt); } catch (e) { alert(`No se pudo armar el archivo (${e.message}).`); }
        b.disabled = false;
      });
      return b;
    };
    return el("tr", {},
      el("td", { class: "td-check" }, cb),
      el("td", {}, el("span", { class: `punto ${s.estado}`, title: s.estado_txt, role: "img", "aria-label": s.estado_txt })),
      el("td", {}, el("div", { class: "serie-nom", text: s.nombre }), el("div", { class: "serie-meta", text: [s.unidad, `código ${s.id}`].filter(Boolean).join(" · ") }),
        el("details", { class: "cond" }, el("summary", { text: "Cómo citar" }), el("p", { text: s.cita || "" }))),
      el("td", { class: "num serie-fechas" }, `${s.desde} → ${s.hasta}`, el("br"), el("span", { class: "serie-meta", text: `${nf(0).format(s.n)} datos · ${FREC[s.frecuencia] || ""}` })),
      el("td", {}, el("div", { class: "formatos formatos-serie" }, boton("zip", "ZIP"), boton("xlsx", "XLSX"))));
  }

  function bloqueExtra(clave, x) {
    if (clave === "canasta") {
      const ul = el("ul", { class: "lista-meses" });
      for (const m of x) ul.append(el("li", {}, el("strong", { class: m.archivos.length ? "" : "sin", text: mesTxt(m.mes) }),
        el("span", { class: "peso", text: m.archivos.length ? `${m.dias} días` : "sin datos (hueco de la fuente)" }),
        m.archivos.length ? filasTxt(m.filas, m.grande) : null, m.archivos.length ? formatos(m) : null));
      return el("div", { class: "extra" }, el("h4", { text: "Canasta del INDEC en supermercados, últimos 12 meses" }),
        el("p", { class: "nota" }, "Precio de cada producto que corresponde a una variedad de la canasta del INDEC, por día, región, cadena y bandera. ",
          el("a", { href: "descargas/canasta/LEEME_canasta.txt", text: "Columnas y criterios (LEEME)" }), "."), ul);
    }
    if (clave === "sepa_crudos") {
      const tb = el("tbody");
      for (const r of x) tb.append(el("tr", {}, el("td", { text: r.periodo, style: "white-space:nowrap" }),
        el("td", {}, r.url ? el("a", { href: r.url, rel: "noopener", text: r.quien }) : el("span", { text: r.quien }), el("div", { class: "fuente-prod", text: r.nota })),
        el("td", { class: "num", text: r.peso }), el("td", { text: r.licencia })));
      return el("div", { class: "extra" }, el("h4", { text: "Datos crudos del SEPA: dónde está la historia" }),
        el("div", { class: "tabla-scroll" }, el("table", { class: "tabla" },
          el("thead", {}, el("tr", {}, el("th", { text: "Período" }), el("th", { text: "Dónde está" }), el("th", { class: "num", text: "Tamaño" }), el("th", { text: "Licencia" }))), tb)));
    }
    return null;
  }

  function cuerpoFuente(f, series, F) {
    const cuerpo = el("div", { class: "fuente-cuerpo" });
    if (f.nota) cuerpo.append(el("p", { class: "fuente-nota", text: f.nota }));
    const datosF = [f.via ? `Vía: ${f.via}` : null, f.licencia ? `Licencia: ${f.licencia}` : null].filter(Boolean);
    if (datosF.length) cuerpo.append(el("p", { class: "fuente-prod", text: datosF.join(" · ") }));
    if (f.condiciones) cuerpo.append(el("details", { class: "cond" }, el("summary", { text: "Condiciones de uso" }), el("p", { text: f.condiciones })));
    if (f.url) cuerpo.append(el("p", { class: "fuente-prod" }, "Página de la fuente: ", el("a", { href: f.url, rel: "noopener", text: f.url.replace(/^https?:\/\//, "") })));
    if (!f.descarga) cuerpo.append(el("p", { class: "fuente-prod", text: "Sin descarga: ver la aclaración." }));
    if (f.tablas.length) {
      const ul = el("ul", { class: "lista-tablas" });
      for (const t of f.tablas) ul.append(el("li", {}, el("strong", { class: "tabla-tit", text: t.titulo }), filasTxt(t.filas, t.grande),
        el("p", { text: t.desc }), formatos(t)));
      cuerpo.append(el("h4", { text: "Tablas completas (el archivo entero, sin elegir fechas)" }), ul);
    }
    for (const [k, x] of Object.entries(f.extra || {})) { const b = bloqueExtra(k, x); if (b) cuerpo.append(b); }
    if (series.length) {
      const tbody = el("tbody");
      const tabla = el("div", { class: "tabla-scroll" }, el("table", { class: "tabla tabla-series" },
        el("thead", {}, el("tr", {}, el("th", { text: "" }), el("th", { text: "" }), el("th", { text: "Serie" }), el("th", { class: "num", text: "Primer → último dato" }), el("th", { text: "Bajar" }))), tbody));
      let vistas = 0;
      const mas = el("button", { type: "button", class: "boton-sec" });
      const mostrar = () => {
        const hasta = Math.min(series.length, vistas + PASO);
        for (let i = vistas; i < hasta; i++) tbody.append(filaSerie(series[i]));
        vistas = hasta;
        mas.hidden = vistas >= series.length;
        mas.textContent = `Mostrar más (${nf(0).format(series.length - vistas)} restantes)`;
      };
      mas.addEventListener("click", mostrar);
      mostrar();
      cuerpo.append(el("h4", { text: hayFiltro(F) ? `Series que coinciden (${nf(0).format(series.length)})` : `Series (${nf(0).format(series.length)})` }),
        el("p", { class: "nota", text: "Cada serie se baja completa con ZIP o XLSX. Para elegir el período o juntar varias series en un archivo, marcalas: abajo aparece la barra con las fechas." }),
        tabla, mas);
    }
    return cuerpo;
  }

  function render() {
    const F = filtros();
    const cont = $("#lista-fuentes");
    cont.textContent = "";
    let nSeries = 0, nFuentes = 0;
    for (const f of FUENTES) {
      if (F.fuente && f.id !== F.fuente) continue;
      const todas = POR_FUENTE.get(f.id) || [];
      const series = todas.filter((s) => coincide(s, F));
      const nombreCoincide = F.q.length && F.q.every((w) => norm(`${f.nombre} ${f.institucion}`).includes(w));
      if (hayFiltro(F) && !series.length && !(nombreCoincide && !F.frec && !F.estado) && !(F.fuente && !F.q.length && !F.frec && !F.estado)) continue;
      const mostrarSeries = series.length ? series : nombreCoincide || !hayFiltro(F) ? todas : series;
      nSeries += mostrarSeries.length; nFuentes++;
      const sub = [f.institucion !== f.nombre ? f.institucion : null, f.n_series ? `${nf(0).format(f.n_series)} series` : null,
        f.tablas.length ? `${f.tablas.length} ${f.tablas.length === 1 ? "tabla" : "tablas"}` : null, f.dato_tipico ? `la mayoría hasta ${mesTxt(f.dato_tipico)}` : null].filter(Boolean).join(" · ");
      const det = el("details", { class: "fuente-card" });
      const sum = el("summary", {},
        el("span", { class: "fc-estado" }, puntoEstado(f.estado, f.estado_txt)),
        el("span", { class: "fc-nombre" }, el("span", { class: "fuente-nom", text: f.nombre }), sub ? el("span", { class: "fuente-prod", text: sub }) : null),
        el("span", { class: "fc-dato" }, el("span", { class: "fc-et", text: "Último dato" }), fechaDato(f.ultimo_dato, f.frecuencia)),
        el("span", { class: "fc-dato" }, el("span", { class: "fc-et", text: "Actualizado" }), f.actualizado ? fechaTxt(f.actualizado.slice(0, 10)) : "—"),
        el("span", { class: "fc-dato" }, el("span", { class: "fc-et", text: "Frecuencia" }), FREC[f.frecuencia] || "—"),
        el("span", { class: "fc-dato" }, el("span", { class: "fc-et", text: "Descarga" }), f.descarga ? "Sí" : "No"));
      det.append(sum);
      let armado = false;
      const armar = () => { if (!armado) { det.append(cuerpoFuente(f, mostrarSeries, F)); armado = true; } };
      det.addEventListener("toggle", () => { if (det.open) armar(); });
      if (hayFiltro(F) && (F.q.length || F.fuente)) { det.open = true; armar(); }
      cont.append(det);
    }
    $("#conteo").textContent = hayFiltro(F)
      ? `${nf(0).format(nSeries)} series en ${nFuentes} ${nFuentes === 1 ? "fuente" : "fuentes"}`
      : `${nf(0).format(CAT.length)} series y ${FUENTES.reduce((a, f) => a + f.tablas.length, 0)} tablas en ${FUENTES.length} fuentes. Tocá una fuente para ver sus series.`;
  }

  function actualizarSel() {
    $("#n-sel").textContent = SEL.size;
    $("#armado").hidden = SEL.size === 0;
    if (SEL.size) {
      const v = [...SEL.values()];
      const des = v.map((s) => s.desde).sort()[0], has = v.map((s) => s.hasta).sort().pop();
      for (const id of ["#desde", "#hasta"]) { $(id).min = des; $(id).max = has; }
    }
    $("#aviso").textContent = "";
  }
  async function bajarSeleccion(fmt) {
    const desde = $("#desde").value || "0000-01-01", hasta = $("#hasta").value || "9999-12-31";
    if (desde > hasta) { $("#aviso").textContent = "La fecha 'desde' es posterior a 'hasta'."; return; }
    $("#aviso").textContent = "Armando el archivo…";
    try {
      const n = await bajarSeries([...SEL.values()], fmt, desde, hasta);
      $("#aviso").textContent = `Listo: ${SEL.size} series, ${nf(0).format(n)} fechas.`;
    } catch (e) { $("#aviso").textContent = `No se pudo armar el archivo (${e.message}).`; }
  }

  // serie histórica de cada tarjeta de "Variables de hoy" (código del catálogo)
  const SERIE_HOY = {
    inflacion: "indec_ipc_general_indice_var_mensual", dolar: "bcra_api_tc_mayorista_a3500", ccl: "argentinadatos_dolar_ccl",
    mep: "argentinadatos_dolar_mep", pib: "indec_pib_desest_pib", super: "dcm_sepa_mensual_nacional",
    fiscal: "datosgob_452.3_RESULTADO_RIO_0_M_18_54", tamar: "bcra_api_tamar_tna",
    desempleo: "indec_eph_desocupacion", sipa: "datosgob_151.1_TL_SIN_TAC_2012_M_15",
  };
  // busca la serie por su código en la sección Datos (abre su fuente) y baja hasta ahí; la dirección queda compartible
  function verSerie(id) {
    $("#busca").value = id;
    $("#f-fuente").value = "";
    $("#f-frec").value = "";
    $("#f-estado").value = "";
    render();
    try { history.replaceState(null, "", `?q=${encodeURIComponent(id)}#datos`); } catch (e) { /* sin historial */ }
    $("#datos").scrollIntoView({ behavior: "smooth" });
  }
  function datos(F, cat) {
    FUENTES = F;
    const inst = Object.fromEntries(F.map((f) => [f.id, f.institucion]));
    CAT = cat.map((s) => ({ ...s, institucion: inst[s.fuente_id], _txt: norm(`${s.nombre} ${s.fuente} ${inst[s.fuente_id] || ""} ${s.id} ${s.unidad || ""}`) }));
    for (const s of CAT) { if (!POR_FUENTE.has(s.fuente_id)) POR_FUENTE.set(s.fuente_id, []); POR_FUENTE.get(s.fuente_id).push(s); }
    for (const f of [...F].sort((a, b) => a.nombre.localeCompare(b.nombre))) $("#f-fuente").append(el("option", { value: f.id, text: f.nombre }));
    let t;
    $("#busca").addEventListener("input", () => { clearTimeout(t); t = setTimeout(render, 200); });
    for (const id of ["#f-fuente", "#f-frec", "#f-estado"]) $(id).addEventListener("change", render);
    $("#limpiar").addEventListener("click", () => { SEL.clear(); actualizarSel(); render(); });
    $("#bajar-xlsx").addEventListener("click", () => bajarSeleccion("xlsx"));
    $("#bajar-zip").addEventListener("click", () => bajarSeleccion("zip"));
    // búsqueda compartible: ?q=texto&fuente=<id> en la dirección
    const u = new URLSearchParams(location.search);
    if (u.get("q")) $("#busca").value = u.get("q");
    if (u.get("fuente")) $("#f-fuente").value = u.get("fuente");
    render();
  }

  // ---------- pronósticos del mes en curso
  function mesEnCurso(R) {
    const cont = $("#mes-curso");
    for (const x of R.mes_en_curso || []) {
      cont.append(el("div", { class: "tile tile-hoy" },
        el("p", { class: "tile-et" }, x.titulo, el("span", { class: "tile-fecha", text: x.periodo })),
        el("p", { class: "tile-val", text: x.valor }),
        el("p", { class: "tile-uni", text: x.unidad }),
        x.nota ? el("p", { class: "tile-sub", text: x.nota }) : null));
    }
  }

  // ---------- el relato: REM nuevo vs nosotros, y datos reales nuevos vs nosotros y el REM
  // barra con el rango de las consultoras (mín-máx), su 80% central (P10-P90), la mediana, nuestro valor y (si hay) el dato real
  function barraRango(r, nuestro, real) {
    const vals = [r.min, r.max, nuestro, real].filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
    if (r.min === null || r.max === null || vals.length < 3) return null;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const x = (v) => `${(100 * (v - lo)) / (hi - lo)}%`;
    const b = el("div", { class: "rango", role: "img", "aria-label": "Rango de las consultoras con la mediana del REM y el pronóstico de Datos Con Método" });
    b.append(el("span", { class: "r-linea", style: `left:${x(r.min)};width:calc(${x(r.max)} - ${x(r.min)})` }));
    if (r.p10 !== null && r.p90 !== null) b.append(el("span", { class: "r-banda", style: `left:${x(r.p10)};width:calc(${x(r.p90)} - ${x(r.p10)})` }));
    if (r.p50 !== null) b.append(el("span", { class: "r-rem", style: `left:${x(r.p50)}`, title: "Mediana del REM" }));
    if (real !== null && real !== undefined) b.append(el("span", { class: "r-real", style: `left:${x(real)}`, title: "Dato real" }));
    b.append(el("span", { class: "r-dcm", style: `left:${x(nuestro)}`, title: "Pronóstico de Datos Con Método" }));
    return b;
  }
  function historia(f, conReal) {
    const num = (et, v, cls) => el("div", { class: `h-num ${cls}` }, el("span", { class: "h-et", text: et }), el("span", { class: "h-v", text: v }));
    const ganador = conReal ? { nosotros: "Más cerca: DCM*", rem: "Más cerca: el REM", empate: "Empate" }[f.gana] : null;
    return el("article", { class: `historia${conReal ? ` gana-${f.gana}` : ""}` },
      el("div", { class: "h-cab" }, el("h3", { text: f.titulo }), ganador ? el("span", { class: "h-gana", text: ganador }) : null),
      el("div", { class: "h-nums" },
        conReal ? num(`Dato real (${f.fuente})`, f.real, "n-real") : null,
        num("REM (mediana)", f.rem, "n-rem"),
        num("DCM*", f.nuestro, "n-dcm")),
      barraRango(f.rango, f.v_nuestro, conReal ? f.v_real : null),
      el("p", { class: "h-txt", text: f.texto }),
      conReal ? el("p", { class: "h-cuando", text: f.cuando }) : null);
  }
  function relato(R) {
    const u = R.ultimo_rem;
    if (u) {
      $("#ur-titular").textContent = u.titular;
      const m = /^(NOTA TRANSITORIA:)\s*(.*)$/.exec(u.nota);
      if (m) $("#ur-nota").append(el("strong", { text: m[1] }), ` ${m[2]}`); else $("#ur-nota").textContent = u.nota;
      for (const f of u.filas) $("#ur-filas").append(historia(f, false));
    }
    const r = R.ultimos_reales;
    if (r) {
      $("#re-titular").textContent = r.titular;
      for (const f of r.filas) $("#re-filas").append(historia(f, true));
    }
  }

  // ---------- pronósticos sellados
  function listaSellos(R) {
    const cont = $("#lista-sellos");
    const L = R.sellos || [];
    if (!L.length) {
      cont.append(el("p", { class: "nota", text: "Todavía no hay pronósticos sellados. El primero se emite el 28 de octubre de 2026, primer día del relevamiento del REM de octubre." }));
      return;
    }
    for (const x of L) {
      const a = x.archivos || {};
      const enlace = (k, t) => (a[k] ? el("a", { class: "fmt", href: a[k], download: "" }, t) : null);
      const tw = (u, t) => (u ? el("a", { href: u, rel: "noopener", text: t }) : el("span", { class: "sin", text: `${t}: pendiente` }));
      cont.append(el("article", { class: "sello" },
        a.png ? el("a", { href: a.pdf || a.png, class: "sello-img" }, el("img", { src: a.png, alt: `Pronóstico n.º ${x.n}`, loading: "lazy" })) : null,
        el("div", {},
          el("h3", { text: `Pronóstico n.º ${x.n} · ${mesTxt(x.relevamiento)}` }),
          el("p", { class: "fuente-prod", text: `Registrado el ${x.registrado} (hora de Buenos Aires)` }),
          x.reemplazado_por ? el("p", { class: "nota", text: `Reemplazado por el Pronóstico n.º ${x.reemplazado_por} del mismo relevamiento. La fe de erratas está en el PDF del n.º ${x.reemplazado_por}; este sello queda publicado sin cambios.` }) : null,
          el("div", { class: "formatos" }, enlace("pdf", "PDF"), enlace("png", "Imagen"), enlace("csv", "CSV"), enlace("ots_pdf", "Sello del PDF (.ots)"), enlace("ots_csv", "Sello del CSV (.ots)")),
          el("p", { class: "hash" }, "SHA-256 del PDF: ", el("code", { text: x.sha256_pdf })),
          el("p", { class: "fuente-prod" }, `Bitcoin: ${x.bitcoin === "sí" ? "anclado" : "pendiente (se completa unas horas después)"} · `,
            x.wayback_pdf ? el("a", { href: x.wayback_pdf, rel: "noopener", text: "Archivo de Internet" }) : "Archivo de Internet: pendiente"),
          el("p", { class: "tweets" }, tw(x.post_url, "Publicación en LinkedIn"), " · ", tw(x.post_rem_url, "Comparación con el REM"), " · ", tw(x.post_real_url, "Comparación con el dato real")))));
    }
  }

  // ---------- arranque
  (async function () {
    try {
      const [R, F, CATALOGO] = await Promise.all([leer("data/resumen.json"), leer("data/fuentes.json"), leer("data/catalogo.json")]);
      portada(R, F);
      mesEnCurso(R);
      tarjetas(R);
      relato(R);
      listaSellos(R);
      datos(F, CATALOGO);
    } catch (e) {
      $("#tiles").append(el("p", { class: "cargando", text: `No se pudieron cargar los datos (${e.message}).` }));
    }
  })();
})();
