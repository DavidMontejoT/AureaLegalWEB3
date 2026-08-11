# Aurea Legal — Auditoría de Producción

**Fecha:** 2026-08-11  
**URL producción:** https://aurea.legal  
**Repositorio:** DavidMontejoT/AureaLegalWEB3 (branch: master)  
**Servidor:** VPS 154.29.72.136, Caddy en `/srv/aurealegal/`

---

## 1. Estado actual del servidor

### Estructura servida
```
/srv/aurealegal/
├── index.html          ← versión dark Liquid Glass (la anterior a este rediseño)
├── assets/
│   └── hero-bg.jpg
├── recursos.html
├── privacidad.html
├── terminos.html
├── centinela-*.png
├── contralitigador-*.png
├── david_montejo.png
├── Giancarlo.jpeg
└── playbooks/
    └── Guia-LegalTech.html
```

### Caddy
- Container: `sectorve-caddy-1`
- Caddyfile bind-mount: `/home/deploy/SectorVe/Caddyfile`
- Dominio: `aurea.legal`
- `try_files` para rutas sin extensión

---

## 2. Diferencias producción vs nuevo index.html

| Elemento | Producción (live) | Nuevo index.html |
|---|---|---|
| **Tema** | Dark Liquid Glass | Light Azure Abyss |
| **Typography** | Playfair Display + Inter | Inter + Playfair Display |
| **CSS** | Vanilla CSS ~2200 líneas | Tailwind CDN + CSS custom |
| **Nav links** | Proyectos, Inmobiliario, Arquitectura, Agente, Equipo, Recursos + "Habla con el agente" | Investigación, Proyectos, Arquitectura, Agente, Equipo, Recursos + "Agenda consultoría" |
| **Hero** | Panel glass centrado + bg blur | Headline centrada light + streaks |
| **Secciones** | Hallazgos, Proyectos (2), Real Estate, 7 Pilares, Chat, Equipo, CTA | Hallazgos, Filosofía, Cómo Funciona, Proyectos (2), 7 Pilares, Chat, Equipo, CTA |
| **Real Estate** | Sección completa AureaMax Lead | No incluida |
| **Chat** | Dark glass con sidebar + modo voz + GSAP | Light glass con sidebar + JS vanilla |
| **Footer** | 5 columnas (Brand, Proyectos, Recursos, Sectores, Legal) + social icons | 5 columnas (Brand, Proyectos, Recursos, Sectores, Legal) + social icons ✓ |
| **Demo modal** | Modal multi-paso con credenciales | Form simple directo |
| **GSAP** | Dependencia completa (ScrollTrigger, etc.) | No usado (CSS + vanilla JS) |
| **Contadores** | Count-up animado con GSAP | Estáticos |
| **Parallax blobs** | 3 blobs flotantes | Streaks + edge glow + center light |

---

## 3. Pendientes para deploy

1. **Real Estate section** — producción tiene sección AureaMax Lead con chat inmobiliario simulado. El nuevo index no la incluye.
2. **Inmobiliario nav link** — producción tiene link "Inmobiliario" que apunta a `#realestate`. El nuevo no lo tiene.
3. **Demo modal** — producción tiene modal multi-paso con credenciales. El nuevo usa form simple.
4. **Counter animations** — producción anima stats con count-up. El nuevo los muestra estáticos.
5. **GSAP** — si se quiere recuperar parallax avanzado, animaciones de entrada, o tilt 3D en cards.
6. **`/recursos`** — la ruta en producción sirve `recursos.html`. Confirmar que Caddy lo resuelve sin `.html`.

---

## 4. Comando de deploy

```bash
# Desde el repo local
rsync -avz --delete \
  index.html recursos.html privacidad.html terminos.html \
  assets/ *.png *.jpeg *.jpg \
  playbooks/ \
  deploy@154.29.72.136:/srv/aurealegal/

# Si hay cambios en el Caddyfile
docker restart sectorve-caddy-1
```

---

## 5. Archivos locales (AureaLegalWEB3)

```
index.html              ← NUEVO: light theme, 8 secciones, chat, footer rico
recursos.html           ← sin cambios (dark theme, requiere adaptación futura)
privacidad.html         ← sin cambios
terminos.html           ← sin cambios
playbooks/
  Guia-LegalTech.html   ← sin cambios
assets/
  hero-bg.jpg           ← usado en diseño anterior, no en el nuevo
centinela-1..4.png      ← screenshots Centinela
contralitigador-1..5.png ← screenshots Contralitigador
contralitigador-main.png
david_montejo.png       ← avatar equipo
Giancarlo.jpeg          ← avatar equipo
testimonio-1..3.png     ← no usados actualmente
hero.png                ← no usado actualmente
```

---

## 6. Notas

- El nuevo diseño eliminó la dependencia de GSAP (~120KB). Todo es CSS animations + vanilla JS.
- El chat funciona con fallback local si `api.aurea.legal/chat` no responde.
- Los archivos `recursos.html`, `privacidad.html`, `terminos.html` siguen con el tema dark antiguo. Habrá que adaptarlos al light theme para consistencia visual.
- `_stitch_review/` es temporal (archivos del zip de diseño), se puede eliminar.
