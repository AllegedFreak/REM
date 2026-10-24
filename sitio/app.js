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
  const leer = async (url) => {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${url}: ${r.status}`);
    return r.json();
  };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const FREC = { D: "Diaria", M: "Mensual", T: "Trimestral", A: "Anual" };
  const ESTADO_TXT = { verde: "Al día", amarillo: "Algo atrasada", rojo: "Atrasada", gris: "Histórica" };
  const puntoEstado = (col, txt) => el("span", { class: "estado" }, el("span", { class: `punto ${col}`, "aria-hidden": "true" }), txt || ESTADO_TXT[col] || col);

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
  const DEC = { inflacion: 2, dolar: 0, pib: 2, tasa: 2, fiscal: 1, exportaciones: 0, importaciones: 0 };

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
    if (R.semaforo) {
      const s = R.semaforo.semaforo || "";
      const col = { VERDE: "verde", AMARILLO: "amarillo", ROJO: "rojo" }[s] || "gris";
      const v = el("p", { class: "tile-val" }, puntoEstado(col, s.charAt(0) + s.slice(1).toLowerCase()));
      v.querySelector(".estado").style.fontSize = "1.4rem";
      v.querySelector(".punto").style.cssText = "width:16px;height:16px";
      tiles.append(tile("Semáforo de alertas", v, R.semaforo.senales_en_alerta ? `${R.semaforo.senales_en_alerta} señales fuera de lo normal` : "ninguna señal fuera de lo normal"));
    }
    // los datos de hoy: último valor de las variables clave
    const hoy = $("#hoy");
    for (const x of R.indicadores || []) {
      const fecha = /^\d{4}-\d{2}-\d{2}$/.test(x.fecha) ? fechaTxt(x.fecha) : /^\d{4}-\d{2}$/.test(x.fecha) ? mesTxt(x.fecha) : x.fecha.replace("T", " T");
      const pre = { dolar: "$", ccl: "$", mep: "$", fiscal: "B$ " }[x.clave] || "";
      const suf = x.unidad.startsWith("%") || x.clave === "tamar" ? "%" : "";
      const val = `${pre}${fmt(x.valor, x.dec)}${suf}`;
      // con pronóstico del REM: la tarjeta lleva a nuestro pronóstico; sin REM (CCL, MEP, súper): sin vínculo
      const destino = { inflacion: "inflacion", dolar: "dolar", pib: "pib", fiscal: "fiscal", tamar: "tasa" }[x.clave];
      hoy.append(el(destino ? "a" : "div", destino
        ? { class: "tile tile-hoy tile-link", href: `#pron-${destino}`, title: "Ver nuestro pronóstico y el del REM" }
        : { class: "tile tile-hoy" },
        el("p", { class: "tile-et" }, x.titulo, el("span", { class: "tile-fecha", text: fecha })),
        el("p", { class: "tile-val", text: val }),
        el("p", { class: "tile-uni", text: x.unidad.startsWith("%") ? x.unidad.slice(1).trim() : x.clave === "tamar" ? "TNA" : x.unidad }),
        x.detalle ? el("p", { class: "tile-sub", text: x.detalle }) : null,
        el("p", { class: "tile-fuente", text: `Fuente: ${x.fuente}` })));
    }
    $("#actualizado").textContent = `Actualizado: ${R.generado} · Último REM publicado: ${mesTxt(R.rem_ultimo)}`;
    $("#pie-act").textContent = `Última actualización: ${R.generado}`;
  }

  // ---------- pronósticos de hoy
  // valor con su signo: $ en el dólar, B$ en el resultado primario, % en inflación, PIB y tasa (dif con signo + o −)
  function fv(clave, v, d, conSigno = false) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const pre = { dolar: "$", fiscal: "B$ " }[clave] || "";
    const suf = ["inflacion", "pib", "tasa"].includes(clave) ? "%" : "";
    const signo = conSigno ? (v > 0 ? "+" : v < 0 ? "−" : "") : (v < 0 ? "−" : "");
    return `${signo}${pre}${fmt(Math.abs(v), d)}${suf}`;
  }
  function tarjetas(R) {
    const cont = $("#tarjetas");
    $("#pron-intro").textContent = `Nuestro pronóstico para el próximo informe del REM, junto a la mediana del último REM publicado (${mesTxt(R.rem_ultimo)}) para el mismo período. "Dif." es nuestro menos el REM.`;
    for (const t of R.pronosticos) {
      const d = DEC[t.clave] ?? 2;
      const prim = t.filas.find((f) => f.nuestro !== null) || t.filas[0];
      const tb = el("table", { class: "tabla" },
        el("thead", {}, el("tr", {}, el("th", { text: "Período" }), el("th", { class: "num" }, el("span", { class: "marca-nuestro", text: "Nuestro" })),
          el("th", { class: "num" }, el("span", { class: "marca-rem", text: "REM ant." })), el("th", { class: "num", text: "Dif." }))));
      const tbody = el("tbody");
      for (const f of t.filas) {
        const dif = f.nuestro !== null && f.rem !== null ? f.nuestro - f.rem : null;
        tbody.append(el("tr", {}, el("td", { text: periodoTxt(f.periodo) }), el("td", { class: "num", text: fv(t.clave, f.nuestro, d) }),
          el("td", { class: "num", text: fv(t.clave, f.rem, d) }), el("td", { class: "num dif", text: dif === null ? "—" : fv(t.clave, dif, d, true) })));
      }
      tb.append(tbody);
      cont.append(el("article", { class: "tarjeta", id: `pron-${t.clave}` },
        el("h3", { text: t.titulo }), el("p", { class: "unidad", text: t.unidad }),
        el("p", { class: "destacado" }, el("span", { class: "num-grande", text: fv(t.clave, prim.nuestro, d) }), el("span", { class: "per", text: periodoTxt(prim.periodo) })),
        el("div", { class: "desborde" }, tb),
        el("p", { class: "modelo", text: t.modelo }),
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
        el("tr", {}, el("th", { text: "" }), el("th", { class: "num", colspan: "3", text: "2025-26" }), el("th", { class: "num", colspan: "2", text: "Toda la muestra" })),
        el("tr", {}, el("th", { text: "Horizonte" }), el("th", { class: "num" }, el("span", { class: "marca-nuestro", text: "Nuestro" })),
          el("th", { class: "num" }, el("span", { class: "marca-rem", text: "REM" })), el("th", { class: "num sub-col", text: "Más cerca" }),
          el("th", { class: "num", text: "Nuestro" }), el("th", { class: "num", text: "REM" }))));
      const tbody = el("tbody");
      let gana = 0, total = 0;
      for (const f of b.filas) {
        const a = f.actual, t = f.todo;
        if (a) { total++; if (a.nuestro < a.rem) gana++; }
        tbody.append(el("tr", {},
          el("td", { text: etiquetaH(b.clave, f.h) }),
          el("td", { class: "num", text: a ? fmt(a.nuestro, d) + unidadErr : "—" }),
          el("td", { class: "num", text: a ? fmt(a.rem, d) + unidadErr : "—" }),
          el("td", { class: "num", text: a ? `${fmt(a.gana_pct, 0)}% (n ${a.n})` : "—" }),
          el("td", { class: "num", text: t ? fmt(t.nuestro, d) + unidadErr : "—" }),
          el("td", { class: "num", text: t ? fmt(t.rem, d) + unidadErr : "—" })));
      }
      tb.append(tbody);
      const ver = total ? (gana === total ? ["gana", "▲ Mejor que el REM en 2025-26"] : gana === 0 ? ["pierde", "▼ El REM fue mejor en 2025-26"]
        : ["pierde", `◆ Mejor que el REM en ${gana} de ${total} horizontes (2025-26)`]) : null;
      const canvas = el("canvas", { role: "img", "aria-label": `${b.titulo}: dato real, REM y nuestro pronóstico` });
      const art = el("article", { class: "bloque" },
        el("div", {}, el("h3", { text: b.titulo }), el("p", { class: "unidad", text: `Error medio en ${b.unidad}` }),
          ver ? el("p", { class: "veredicto" }, el("span", { class: `insignia ${ver[0]}`, text: ver[1] })) : null,
          el("div", { class: "tabla-scroll", style: "border:0" }, tb),
          el("p", { class: "nota", text: "\"Más cerca\": % de veces que nuestro pronóstico quedó más cerca del dato real que el REM." })),
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
        data: { labels: gr.x.map(periodoTxt), datasets: [serie("Dato real", gr.real, css("--real"), 2), serie("REM", gr.rem, css("--rem"), 2), serie("Nuestro", gr.nuestro, css("--acento"), 2)] },
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
          el("div", { class: "formatos" }, enlace("pdf", "PDF"), enlace("png", "Imagen"), enlace("csv", "CSV"), enlace("ots_pdf", "Sello del PDF (.ots)"), enlace("ots_csv", "Sello del CSV (.ots)")),
          el("p", { class: "hash" }, "SHA-256 del PDF: ", el("code", { text: x.sha256_pdf })),
          el("p", { class: "fuente-prod" }, `Bitcoin: ${x.bitcoin === "sí" ? "anclado" : "pendiente (se completa unas horas después)"} · `,
            x.wayback_pdf ? el("a", { href: x.wayback_pdf, rel: "noopener", text: "Archivo de Internet" }) : "Archivo de Internet: pendiente"),
          el("p", { class: "tweets" }, tw(x.post_url, "Publicación en LinkedIn"), " · ", tw(x.post_rem_url, "Comparación con el REM"), " · ", tw(x.post_real_url, "Comparación con el dato real")))));
    }
  }

  // ---------- método: qué modelo usa cada variable
  function modelos(R) {
    const ul = $("#modelos");
    for (const t of R.pronosticos) ul.append(el("li", {}, el("strong", { text: `${t.titulo}: ` }), t.modelo + "."));
  }

  // ---------- arranque
  (async function () {
    try {
      const [R, F, CATALOGO] = await Promise.all([leer("data/resumen.json"), leer("data/fuentes.json"), leer("data/catalogo.json")]);
      portada(R, F);
      tarjetas(R);
      bloques(R);
      listaSellos(R);
      datos(F, CATALOGO);
      modelos(R);
    } catch (e) {
      $("#tiles").append(el("p", { class: "cargando", text: `No se pudieron cargar los datos (${e.message}).` }));
    }
  })();
})();
