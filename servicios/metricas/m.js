/* Metricas de aurea.legal — sin cookies, sin terceros.
   Mide: visitas, de donde llegan, clics (para el mapa de calor), hasta donde bajan,
   cuanto tiempo se ve cada seccion y el embudo del formulario.
   No mide si el navegador pide "no rastrear", dentro del panel (iframe) ni con ?m=off.
   Nunca lee lo que se escribe en los campos: de un input solo guarda su name. */
(() => {
  'use strict';
  const nav = navigator;
  if (window.top !== window || nav.webdriver) return;
  if (nav.doNotTrack === '1' || window.doNotTrack === '1' || nav.globalPrivacyControl) return;
  if (/[?&]m=off\b/.test(location.search)) return;

  const DESTINO = '/api/m';
  const LOTE = 50;

  const azar = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), b => b.toString(16).padStart(2, '0')).join('');
  let sesion;
  try {   // sessionStorage: vive lo que vive la pestaña, no es una cookie y no sale del navegador
    sesion = sessionStorage.getItem('m_s');
    if (!sesion) { sesion = azar(); sessionStorage.setItem('m_s', sesion); }
  } catch (_) { sesion = azar(); }

  const cola = [];
  const ruta = () => location.pathname;

  function enviar(alSalir) {
    while (cola.length) {
      const cuerpo = JSON.stringify({ s: sesion, t: cola.splice(0, LOTE) });
      if (alSalir && nav.sendBeacon) {
        nav.sendBeacon(DESTINO, new Blob([cuerpo], { type: 'text/plain' }));
      } else {
        fetch(DESTINO, { method: 'POST', body: cuerpo, keepalive: true,
                         headers: { 'Content-Type': 'text/plain' } }).catch(() => {});
      }
    }
  }

  // ── Secciones: misma clave que usa el panel para ubicar los clics ──
  const SECCIONES = 'section, header, footer';
  function seccionDe(el) { return el && el.closest ? el.closest(SECCIONES) : null; }
  function claveDe(sec) {
    if (!sec) return 'pagina';
    if (sec.id) return sec.id;
    return sec.tagName.toLowerCase() + '-' + Array.prototype.indexOf.call(document.querySelectorAll(SECCIONES), sec);
  }

  // ── Vista ──
  const params = new URLSearchParams(location.search);
  cola.push({ k: 'v', p: ruta(), r: document.referrer, u: params.get('utm_source') || '',
              w: innerWidth, h: innerHeight });
  enviar();

  // ── Clics: posición relativa a su sección, así el mapa aguanta cambios de diseño ──
  document.addEventListener('click', e => {
    const sec = seccionDe(e.target);
    let x, y;
    if (sec) {
      const r = sec.getBoundingClientRect();
      x = (e.clientX - r.left) / (r.width || 1);
      y = (e.clientY - r.top) / (r.height || 1);
    } else {
      const d = document.documentElement;
      x = e.pageX / (d.scrollWidth || 1);
      y = e.pageY / (d.scrollHeight || 1);
    }
    const t = e.target.closest && e.target.closest('a, button, [role="button"], input, select, textarea, label, summary');
    let etiqueta = '';
    if (t) {
      const campo = /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName);
      etiqueta = (t.getAttribute('aria-label') || (campo ? 'campo: ' + (t.name || t.type) : t.textContent) || '')
        .replace(/\s+/g, ' ').trim().slice(0, 80);
    }
    cola.push({ k: 'c', p: ruta(), sec: claveDe(sec), x, y, w: innerWidth, l: etiqueta });
    if (cola.length >= 20) enviar();
  }, { capture: true, passive: true });

  // ── Scroll: se manda solo el máximo alcanzado ──
  let scrollMax = 0, scrollAvisado = 0, pendienteScroll = false;
  addEventListener('scroll', () => {
    if (pendienteScroll) return;
    pendienteScroll = true;
    requestAnimationFrame(() => {
      pendienteScroll = false;
      const d = document.documentElement;
      const pct = Math.min(100, Math.round(100 * (scrollY + innerHeight) / (d.scrollHeight || 1)));
      if (pct > scrollMax) scrollMax = pct;
    });
  }, { passive: true });

  // ── Tiempo visible por sección (cuenta si ocupa la mitad de la pantalla o la mitad de sí misma) ──
  const visibles = new Map(), acumulado = new Map();
  let pausado = document.visibilityState !== 'visible';
  function sumar(clave, desde, ahora) { acumulado.set(clave, (acumulado.get(clave) || 0) + (ahora - desde)); }
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(entradas => {
      const ahora = performance.now();
      entradas.forEach(en => {
        const clave = claveDe(en.target);
        const ve = en.intersectionRatio >= 0.5 || en.intersectionRect.height >= innerHeight * 0.5;
        if (ve && !visibles.has(clave)) visibles.set(clave, ahora);
        if (!ve && visibles.has(clave)) {
          if (!pausado) sumar(clave, visibles.get(clave), ahora);
          visibles.delete(clave);
        }
      });
    }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
    document.querySelectorAll(SECCIONES).forEach(s => io.observe(s));
  }

  // ── Embudo del formulario: empezó (primer foco) y envió (mensaje de gracias) ──
  const form = document.getElementById('demoForm');
  if (form) {
    let empezo = false, envio = false;
    form.addEventListener('focusin', () => {
      if (empezo) return;
      empezo = true;
      cola.push({ k: 'f', e: 'inicio' });
      enviar();
    });
    const msg = document.getElementById('demoMsg');
    if (msg && 'MutationObserver' in window) {
      new MutationObserver(() => {
        if (!envio && /gracias/i.test(msg.textContent)) {
          envio = true;
          cola.push({ k: 'f', e: 'enviado' });
          enviar();
        }
      }).observe(msg, { childList: true, characterData: true, subtree: true });
    }
  }

  // ── Volcado periódico y al salir ──
  function volcar() {
    const ahora = performance.now();
    if (!pausado) visibles.forEach((desde, clave) => { sumar(clave, desde, ahora); visibles.set(clave, ahora); });
    acumulado.forEach((ms, clave) => { if (ms >= 300) cola.push({ k: 'x', p: ruta(), sec: clave, ms: Math.round(ms) }); });
    acumulado.clear();
    if (scrollMax > scrollAvisado) { cola.push({ k: 's', p: ruta(), d: scrollMax }); scrollAvisado = scrollMax; }
  }

  setInterval(() => {
    if (pausado) return;
    volcar();
    if (!cola.length) cola.push({ k: 'h' });   // latido: así se sabe cuánto duró la visita
    enviar();
  }, 15000);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      volcar();
      pausado = true;
      enviar(true);
    } else {
      pausado = false;
      const ahora = performance.now();
      visibles.forEach((_, clave) => visibles.set(clave, ahora));
    }
  });
  addEventListener('pagehide', () => { volcar(); enviar(true); });
})();
