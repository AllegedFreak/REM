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
    return e;
  };
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
      tile("Fuentes al día", el("p", { class: "tile-val", text: `${al} de ${vivas}` }), "según su frecuencia de publicación"),
    );
    if (R.semaforo) {
      const s = R.semaforo.semaforo || "";
      const col = { VERDE: "verde", AMARILLO: "amarillo", ROJO: "rojo" }[s] || "gris";
      const v = el("p", { class: "tile-val" }, puntoEstado(col, s.charAt(0) + s.slice(1).toLowerCase()));
      v.querySelector(".estado").style.fontSize = "1.4rem";
      v.querySelector(".punto").style.cssText = "width:16px;height:16px";
      tiles.append(tile("Semáforo de alertas", v, R.semaforo.senales_en_alerta ? `${R.semaforo.senales_en_alerta} señales fuera de lo normal` : "ninguna señal fuera de lo normal"));
    }
    if (R.termometro) {
      tiles.append(tile(`Súper, ${mesTxt(R.termometro.relevamiento)}`, el("p", { class: "tile-val", text: `${fmt(R.termometro.termometro_pct, 2)}%` }),
        "precios de supermercados (SEPA) en lo que va del mes"));
    }
    $("#actualizado").textContent = `Actualizado: ${R.generado} · Último REM publicado: ${mesTxt(R.rem_ultimo)}`;
    $("#pie-act").textContent = `Última actualización: ${R.generado}`;
  }

  // ---------- pronósticos de hoy
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
        tbody.append(el("tr", {}, el("td", { text: periodoTxt(f.periodo) }), el("td", { class: "num", text: fmt(f.nuestro, d) }),
          el("td", { class: "num", text: fmt(f.rem, d) }), el("td", { class: "num dif", text: dif === null ? "—" : (dif > 0 ? "+" : "") + fmt(dif, d) })));
      }
      tb.append(tbody);
      cont.append(el("article", { class: "tarjeta" },
        el("h3", { text: t.titulo }), el("p", { class: "unidad", text: t.unidad }),
        el("p", { class: "destacado" }, el("span", { class: "num-grande", text: fmt(prim.nuestro, d) }), el("span", { class: "per", text: periodoTxt(prim.periodo) })),
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

  // ---------- fuentes
  let FUENTES = [];
  function tablaFuentes(filtro = "todas") {
    const tb = $("#tabla-fuentes");
    tb.textContent = "";
    const orden = { rojo: 0, amarillo: 1, verde: 2, gris: 3 };
    const lista = FUENTES.filter((f) => filtro === "todas" || (filtro === "verde" && f.estado === "verde") ||
      (filtro === "atrasadas" && (f.estado === "rojo" || f.estado === "amarillo")) || (filtro === "sin" && !f.descarga))
      .sort((a, b) => (orden[a.estado] - orden[b.estado]) || a.nombre.localeCompare(b.nombre));
    for (const f of lista) {
      const nombre = f.url ? el("a", { href: f.url, rel: "noopener", class: "fuente-nom", text: f.nombre }) : el("span", { class: "fuente-nom", text: f.nombre });
      const prod = [f.productor, f.intermediario ? `vía ${f.intermediario}` : null, f.n_series ? `${f.n_series} series` : null].filter(Boolean).join(" · ");
      const ult = f.ultimo_dato ? (f.frecuencia === "D" ? fechaTxt(f.ultimo_dato) : f.frecuencia === "A" ? f.ultimo_dato.slice(0, 4) : mesTxt(f.ultimo_dato)) : "—";
      const desc = f.descarga ? el("span", { text: "Sí" }) : el("details", { class: "cond" }, el("summary", { text: "No" }),
        el("p", { text: f.condiciones || "Condiciones de uso sin confirmar." }));
      tb.append(el("tr", {},
        el("td", {}, puntoEstado(f.estado, f.estado_txt)),
        el("td", {}, nombre, prod ? el("div", { class: "fuente-prod", text: prod }) : null,
          f.cita ? el("details", { class: "cond" }, el("summary", { text: "Cómo citar" }), el("p", { text: f.cita })) : null),
        el("td", { class: "num", text: ult }),
        el("td", { class: "num", text: f.actualizado ? fechaTxt(f.actualizado.slice(0, 10)) + f.actualizado.slice(10) : "—" }),
        el("td", { text: FREC[f.frecuencia] || "—" }),
        el("td", {}, desc)));
    }
  }
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach((x) => x.classList.toggle("activo", x === c));
    tablaFuentes(c.dataset.f);
  }));

  // ---------- descargas
  let CAT = [], VISTAS = 0;
  const SEL = new Map();
  const MAX_SEL = 40, PASO = 150;
  const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  function filtrar() {
    const q = norm($("#busca").value).split(/\s+/).filter(Boolean);
    const fu = $("#f-fuente").value, fr = $("#f-frec").value;
    return CAT.filter((s) => (!fu || s.fuente === fu) && (!fr || s.frecuencia === fr) &&
      q.every((w) => s._txt.includes(w)));
  }
  function listar(reset = true) {
    const ul = $("#lista-series");
    const res = filtrar();
    if (reset) { ul.textContent = ""; VISTAS = 0; }
    const hasta = Math.min(res.length, VISTAS + PASO);
    for (let i = VISTAS; i < hasta; i++) {
      const s = res[i];
      const cb = el("input", { type: "checkbox", value: s.id, "aria-label": s.nombre });
      cb.checked = SEL.has(s.id);
      cb.addEventListener("change", () => {
        if (cb.checked) {
          if (SEL.size >= MAX_SEL) { cb.checked = false; $("#aviso").textContent = `Máximo ${MAX_SEL} series por archivo.`; return; }
          SEL.set(s.id, s);
        } else SEL.delete(s.id);
        actualizarSel();
      });
      const fechas = el("span", { class: "serie-fechas", title: "Primer y último dato" }, `${s.desde} → ${s.hasta}`, el("br"), el("span", { class: "serie-meta", text: `${nf(0).format(s.n)} datos · ${FREC[s.frecuencia] || ""}` }));
      ul.append(el("li", {}, el("label", {}, cb, el("span", { class: `punto ${s.estado}`, title: s.estado_txt, "aria-label": s.estado_txt }),
        el("span", {}, el("span", { class: "serie-nom", text: s.nombre }), el("br"), el("span", { class: "serie-meta", text: `${s.fuente}${s.unidad ? " · " + s.unidad : ""}` })),
        fechas)));
    }
    VISTAS = hasta;
    $("#conteo").textContent = `${nf(0).format(res.length)} series encontradas${res.length > VISTAS ? ` · mostrando ${nf(0).format(VISTAS)}` : ""}`;
    $("#mas").hidden = res.length <= VISTAS;
  }
  function actualizarSel() {
    $("#n-sel").textContent = SEL.size;
    $("#bajar").disabled = SEL.size === 0;
    $("#bajar").textContent = SEL.size ? `Descargar .xlsx (${SEL.size})` : "Descargar .xlsx";
    if (SEL.size) {
      const des = [...SEL.values()].map((s) => s.desde).sort()[0], has = [...SEL.values()].map((s) => s.hasta).sort().pop();
      $("#desde").min = des; $("#desde").max = has; $("#hasta").min = des; $("#hasta").max = has;
      if (!$("#desde").value) $("#desde").placeholder = des;
    }
    $("#aviso").textContent = "";
  }
  async function bajar() {
    if (!window.XLSX) { $("#aviso").textContent = "Cargando el armador de Excel, probá de nuevo en un segundo."; return; }
    const desde = $("#desde").value || "0000-01-01", hasta = $("#hasta").value || "9999-12-31";
    if (desde > hasta) { $("#aviso").textContent = "La fecha 'desde' es posterior a 'hasta'."; return; }
    $("#bajar").disabled = true;
    $("#aviso").textContent = "Armando el archivo…";
    try {
      const series = [...SEL.values()];
      const datos = await Promise.all(series.map((s) => leer(`data/s/${encodeURIComponent(s.id)}.json`)));
      const filas = new Map();
      datos.forEach((d, j) => d.d.forEach((f, i) => {
        if (f < desde || f > hasta) return;
        if (!filas.has(f)) filas.set(f, new Array(series.length).fill(null));
        filas.get(f)[j] = d.v[i];
      }));
      const fechas = [...filas.keys()].sort();
      const aoa = [["fecha", ...series.map((s) => s.nombre)], ["(id)", ...series.map((s) => s.id)]];
      for (const f of fechas) aoa.push([f, ...filas.get(f)]);
      const wb = XLSX.utils.book_new();
      const ws = XLSX.utils.aoa_to_sheet(aoa);
      ws["!cols"] = [{ wch: 12 }, ...series.map(() => ({ wch: 18 }))];
      XLSX.utils.book_append_sheet(wb, ws, "datos");
      const meta = [["id", "nombre", "fuente", "unidad", "frecuencia", "primer dato", "último dato", "url de la fuente"],
        ...series.map((s) => [s.id, s.nombre, s.fuente, s.unidad || "", FREC[s.frecuencia] || s.frecuencia, s.desde, s.hasta, s.url || ""])];
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(meta), "series");
      const leeme = [["Datos Con Método"], [`Descargado: ${new Date().toISOString().slice(0, 16).replace("T", " ")}`], [`Período pedido: ${desde.replace("0000-01-01", "inicio")} a ${hasta.replace("9999-12-31", "último dato")}`],
        ["www.datosconmetodo.com · Marcos Cavanagh"],
        ["Series de Datos Con Método (fuente 'Datos Con Método ...'): licencia CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/deed.es). Citar: Datos Con Método (www.datosconmetodo.com)."],
        ["Series de otras fuentes: conservan la licencia de su productor (ver hoja 'series' y la sección Fuentes del sitio). Citá la fuente original."], ["Celdas vacías: la serie no tiene dato en esa fecha (no se rellenan)."]];
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(leeme), "leeme");
      XLSX.writeFile(wb, `datos_con_metodo_${new Date().toISOString().slice(0, 10)}.xlsx`);
      $("#aviso").textContent = `Listo: ${series.length} series, ${nf(0).format(fechas.length)} fechas.`;
    } catch (e) {
      $("#aviso").textContent = `No se pudo armar el archivo (${e.message}).`;
    } finally { $("#bajar").disabled = SEL.size === 0; }
  }
  function descargas(cat, R) {
    CAT = cat.map((s) => ({ ...s, _txt: norm(`${s.nombre} ${s.fuente} ${s.id} ${s.unidad || ""}`) }));
    const fuentes = [...new Set(CAT.map((s) => s.fuente))].sort((a, b) => a.localeCompare(b));
    for (const f of fuentes) $("#f-fuente").append(el("option", { value: f, text: f }));
    let t;
    $("#busca").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => listar(true), 150); });
    $("#f-fuente").addEventListener("change", () => listar(true));
    $("#f-frec").addEventListener("change", () => listar(true));
    $("#mas").addEventListener("click", () => listar(false));
    $("#limpiar").addEventListener("click", () => { SEL.clear(); actualizarSel(); listar(true); });
    $("#bajar").addEventListener("click", bajar);
    listar(true);
    const ul = $("#tablas");
    for (const x of R.tablas || []) {
      ul.append(el("li", {}, el("a", { href: `descargas/${x.archivo}`, download: "", text: x.titulo }), " ",
        el("span", { class: "peso", text: `.xlsx · ${nf(0).format(x.peso_kb)} KB` }), el("p", { text: x.desc })));
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
      FUENTES = F;
      portada(R, F);
      tarjetas(R);
      bloques(R);
      tablaFuentes();
      descargas(CATALOGO, R);
      modelos(R);
    } catch (e) {
      $("#tiles").append(el("p", { class: "cargando", text: `No se pudieron cargar los datos (${e.message}).` }));
    }
  })();
})();
