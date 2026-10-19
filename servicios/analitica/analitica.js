/* Analítica de aurea.legal — rastreador propio, sin cookies ni terceros.
   Manda a /api/evento (servicio servicios/analitica): la vista, hasta dónde se hace scroll,
   cuánto tiempo se ve cada sección, los clics (para el mapa de calor) y el embudo del formulario.
   No guarda nada en el navegador y respeta "Do Not Track" / "Global Privacy Control". */
(function () {
  'use strict';
  var nav = navigator;
  if (nav.doNotTrack === '1' || window.doNotTrack === '1' || nav.globalPrivacyControl) return;
  if (window.top !== window) return;                       // el panel muestra la página en un iframe
  if (/[?&]mapa-de-calor=1/.test(location.search)) return;
  if (!('sendBeacon' in nav) || !window.JSON) return;

  var URL_EVENTOS = '/api/evento';
  var carga = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);   // solo vive en esta pestaña
  var cola = [];
  var utm = (location.search.match(/[?&]utm_(?:source|campaign)=([^&]+)/) || [])[1] || '';

  function base(extra) {
    var b = { c: carga, p: location.pathname, w: window.innerWidth, e: cola.splice(0, 60) };
    for (var k in extra) b[k] = extra[k];
    return b;
  }
  function enviar(extra, alSalir) {
    if (!cola.length && !extra) return;
    var cuerpo = JSON.stringify(base(extra || {}));
    if (alSalir || !window.fetch) { nav.sendBeacon(URL_EVENTOS, cuerpo); return; }
    fetch(URL_EVENTOS, { method: 'POST', body: cuerpo, keepalive: true, headers: { 'Content-Type': 'text/plain' } })
      .catch(function () {});
  }

  // 1) La vista
  var ref = document.referrer;
  enviar({ v: true, r: ref, u: decodeURIComponent(utm) });

  // 2) Scroll máximo (0–100)
  var scrollMax = 0;
  function medirScroll() {
    var alto = document.documentElement.scrollHeight;
    var pct = Math.round(Math.min(100, 100 * (window.scrollY + window.innerHeight) / alto));
    if (pct > scrollMax) scrollMax = pct;
  }
  addEventListener('scroll', medirScroll, { passive: true });
  medirScroll();

  // 3) Tiempo visible de cada sección (40% de ella en pantalla, o media pantalla si es muy alta)
  var tiempos = {}, desde = {};
  function nombreSeccion(el) { return el.id || (el.querySelector('h2') || {}).textContent || ''; }
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entradas) {
      var ahora = performance.now();
      entradas.forEach(function (en) {
        var n = nombreSeccion(en.target).trim().slice(0, 40);
        if (!n) return;
        var visible = en.isIntersecting &&
          en.intersectionRect.height >= Math.min(en.boundingClientRect.height * 0.4, window.innerHeight * 0.5);
        if (visible && !desde[n]) desde[n] = ahora;
        if (!visible && desde[n]) { tiempos[n] = (tiempos[n] || 0) + (ahora - desde[n]) / 1000; delete desde[n]; }
      });
    }, { threshold: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1] });
    [].forEach.call(document.querySelectorAll('main section, main [id].project'), function (s) { io.observe(s); });
  }
  function cerrarTiempos() {
    var ahora = performance.now();
    for (var n in desde) { tiempos[n] = (tiempos[n] || 0) + (ahora - desde[n]) / 1000; desde[n] = ahora; }
  }

  // 4) Clics: posición relativa al documento, para el mapa de calor
  document.addEventListener('click', function (ev) {
    var doc = document.documentElement;
    var objetivo = ev.target.closest && ev.target.closest('a, button, [data-analitica], input, select, label');
    var etiqueta = '';
    if (objetivo) {
      etiqueta = objetivo.getAttribute('data-analitica') || objetivo.getAttribute('aria-label') ||
                 (objetivo.tagName === 'INPUT' || objetivo.tagName === 'SELECT' ? 'Campo: ' + (objetivo.name || objetivo.type) : '') ||
                 (objetivo.textContent || '').replace(/\s+/g, ' ').trim();
      etiqueta = etiqueta.slice(0, 60);
    }
    cola.push({ t: 'clic', x: ev.pageX / doc.scrollWidth, y: ev.pageY, h: doc.scrollHeight, o: etiqueta });
  }, { capture: true, passive: true });

  // 5) Embudo del formulario: el primer foco en cualquier formulario y la respuesta de /api/demo-request
  //    (vale para cualquier versión de la página, sin tocar su código)
  var empezado = false;
  document.addEventListener('focusin', function (ev) {
    if (empezado || !ev.target.closest || !ev.target.closest('form')) return;
    empezado = true;
    cola.push({ t: 'form', n: 'inicio', v: 1 });
    enviar();
  });
  function resultado(ok) { cola.push({ t: 'form', n: ok ? 'envio_ok' : 'envio_error', v: 1 }); enviar(); }
  if (window.fetch) {
    var fetchOriginal = window.fetch;
    window.fetch = function (recurso, opciones) {
      var url = typeof recurso === 'string' ? recurso : (recurso && recurso.url) || '';
      var p = fetchOriginal.apply(this, arguments);
      if (/\/api\/demo-request(\?|$)/.test(url)) {
        p.then(function (r) { resultado(r.ok); }, function () { resultado(false); });
      }
      return p;
    };
  }

  // Envío por lotes: cada 15 s si hay algo y siempre al salir de la página
  setInterval(function () { if (cola.length) enviar(); }, 15000);
  function alSalir() {
    medirScroll(); cerrarTiempos();
    cola.push({ t: 'scroll', v: scrollMax });
    for (var n in tiempos) if (tiempos[n] >= 0.5) cola.push({ t: 'seccion', n: n, v: Math.round(tiempos[n] * 10) / 10 });
    tiempos = {};
    while (cola.length) enviar(null, true);
  }
  addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') alSalir(); });
  addEventListener('pagehide', alSalir);
})();
