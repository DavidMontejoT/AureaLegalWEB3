# Guía de contribución · Aurea Legal

> _Menos capas, mismo rigor._ — David Montejo

Esta guía define cómo trabajamos en Aurea Legal: branches, commits, pull requests,
code review y el uso de AI para acelerar sin perder calidad. No es burocracia — es
el estándar mínimo para que el equipo se mueva rápido y el código no se rompa.

---

## 1. Ramas (branches)

Nombramos las ramas con prefijo + descripción corta en kebab-case:

| Prefijo | Cuándo usarlo | Ejemplo |
|---------|---------------|---------|
| `feat/` | Nueva funcionalidad | `feat/demo-modal-per-user` |
| `fix/` | Corrección de bug | `fix/nav-link-playbooks` |
| `chore/` | Tareas de mantenimiento, deps, limpieza | `chore/cleanup-legacy-html` |
| `docs/` | Documentación | `docs/readme-deploy` |
| `design/` | Cambios visuales o de branding | `design/liquid-glass-v3` |

Reglas:
- Una rama = un cambio atómico. Si tu rama hace dos cosas no relacionadas, la partes en dos.
- Las ramas salen de `master` (o `main`) y vuelven a `master`.
- Borramos la rama después del merge. La historia vive en el commit, no en 40 ramas muertas.

---

## 2. Commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/) en **español**.
El formato:

```
<tipo>(<alcance>): <descripción en imperativo>
```

Tipos: `feat`, `fix`, `chore`, `docs`, `design`, `refactor`, `perf`.

Ejemplos:
```
feat(team): agrega a Giancarlo Vilcapoma como CTO
fix(nav): corrige slug roto en link de Playbooks
design(hero): migra hero section a Liquid Glass v3
chore(repo): elimina archivos HTML legacy del root
```

Reglas:
- **Un commit = un cambio lógico.** Si escribiste "y también" en el mensaje, son dos commits.
- **Imperativo presente.** "agrega", no "agregué" ni "agregado".
- **No commits WIP a master.** Si necesitas guardar trabajo parcial, haz `git stash` o usa una rama draft.

Si estás usando AI para generar el mensaje, dale contexto del diff y pídele que siga
este formato. Más abajo está el prompt.

---

## 3. Pull Requests

### 3.1 Antes de abrir el PR

- [ ] Hiciste rebase sobre `master` (no merge de master en tu rama)
- [ ] Probaste el cambio manualmente — no asumas que "debería funcionar"
- [ ] Si es HTML/CSS/JS, lo abriste en el navegador y verificaste visualmente
- [ ] Si es un cambio de infraestructura (DNS, Caddy, Docker), lo probaste en el VPS o documentaste por qué no se puede probar antes del merge
- [ ] Hiciste squash de commits "wip", "typo", "test" en commits con sentido
- [ ] El diff es limpio — no incluiste archivos de prueba, backups, ni ruido

### 3.2 Descripción del PR

Todo PR debe responder **qué, por qué y cómo** en la descripción. No asumas que el
revisor tiene contexto. Usa esta estructura:

```markdown
## Qué

Una o dos frases que describan el cambio. Alguien que no sabe nada del proyecto
debería entender qué pasó.

## Por qué

El problema que resuelve, el issue que cierra, o la decisión de producto detrás.
Incluye screenshots del antes/después si es un cambio visual.

## Cómo

Detalles técnicos relevantes: qué archivos se tocaron, por qué esa solución y no
otra, trade-offs que aceptaste. Si consideraste alternativas y las descartaste,
dilo aquí.

## Cómo probar

Pasos exactos para que el revisor reproduzca el cambio:
1. Ir a /ruta
2. Hacer clic en X
3. Verificar que Y

## Screenshots / Evidencia

| Antes | Después |
|-------|---------|
| (imagen) | (imagen) |
```

### 3.3 Linked issues

Si el PR cierra un issue, usa `Closes #N` en la descripción para que GitHub lo
vincule automáticamente.

---

## 4. Code Review

### 4.1 Para el autor

- **El PR no es una entrega, es una conversación.** El revisor no es tu jefe, es
  tu par. Su trabajo es encontrar problemas antes de que lleguen a producción.
- **No te pongas a la defensiva.** Si el revisor no entendió algo, el código (o
  la descripción) no es suficientemente claro. Arregla el código, no discutas.
- **Responde a todos los comentarios.** Un "listo" o "hecho" es suficiente. Si
  no estás de acuerdo, explica por qué con datos, no con opiniones.
- **No hagas force-push después de que el review empezó.** Agrega commits nuevos.
  El squash se hace al final.

### 4.2 Para el revisor

- **Revisa el diff, no la persona.** "Este loop es O(n²)" no "escribiste mal
  este loop". Sé directo pero respetuoso.
- **Distingue entre bloqueante y sugerencia.** Usa `nit:` o `sugerencia:` para
  cosas que son preferencia personal, no bugs.
- **No dejes pasar cosas rotas por ser amable.** El código que mergeamos hoy es
  el incidente de mañana.
- **Aprueba cuando esté listo.** No hagas "LGTM pero..." — si tienes peros, son
  comentarios, no aprobación.

### 4.3 Velocidad del review

- Los PR pequeños (<200 líneas) deberían revisarse en menos de 4 horas.
- Los PR grandes se revisan en menos de 24 horas.
- Si un PR lleva más de 48 horas sin review, el autor debe hacer ping en el chat
  del equipo.

---

## 5. AI Prompts para PRs

Estos prompts están diseñados para que cualquier herramienta de AI (Claude,
ChatGPT, Copilot, etc.) genere PRs con el estándar de Aurea Legal. Pega el prompt
**completo**, reemplazando `{{TIPO}}` según corresponda.

### 5.1 PR de feature (`feat`)

```
Eres un senior engineer en Aurea Legal, una legaltech AI-native. Revisa el
siguiente diff y genera la descripción de un Pull Request en español con esta
estructura exacta:

## Qué
[1-2 frases claras, sin jerga innecesaria. Alguien externo al proyecto debe
entender qué se construyó.]

## Por qué
[El problema de usuario o técnico que motiva este cambio. Si cierra un issue,
menciona "Closes #N". Contexto de producto si aplica.]

## Cómo
[Decisiones técnicas relevantes: por qué esta implementación y no otra,
trade-offs, archivos clave modificados. Sé honesto si hay deuda técnica
aceptada.]

## Cómo probar
[Pasos exactos, numerados, para que el revisor reproduzca el cambio. Incluye
rutas, clics, datos de prueba.]

## Screenshots
[Si el cambio es visual, indica dónde irían los screenshots antes/después.]

Reglas:
- Usa Conventional Commits en español para el título: feat(scope): descripción
- Lenguaje directo, sin adjetivos vacíos ("robusto", "escalable", "innovador")
- Si hay trade-offs conocidos, dilo explícitamente
- No inventes funcionalidad que no está en el diff

Aquí está el diff:
```

### 5.2 PR de bugfix (`fix`)

```
Eres un senior engineer en Aurea Legal, una legaltech AI-native. Revisa el
siguiente diff y genera la descripción de un Pull Request de **bugfix** en
español con esta estructura exacta:

## Qué
[1-2 frases describiendo el bug y su corrección.]

## Bug
- **Comportamiento anterior:** [qué pasaba antes — sé específico]
- **Comportamiento nuevo:** [qué pasa ahora]
- **Cómo reproducir:** [pasos exactos para ver el bug sin el fix]

## Causa raíz
[Qué estaba mal realmente — no solo el síntoma. ¿Condición de carrera? ¿Valor
nulo no manejado? ¿Selector CSS incorrecto?]

## Cómo
[Qué archivos se tocaron y por qué esta solución en particular.]

## Cómo probar
1. [pasos para confirmar que el bug ya no existe]
2. [pasos para confirmar que no se rompió nada relacionado]

Reglas:
- Usa Conventional Commits en español: fix(scope): descripción
- La causa raíz importa más que el síntoma
- Si el fix introduce riesgo conocido, dilo

Aquí está el diff:
```

### 5.3 PR de diseño (`design`)

```
Eres un senior design-engineer en Aurea Legal. El sistema de diseño es "Liquid
Glass" — fondo #040811, tipografía Playfair Display + Inter, glass cards con
backdrop-filter blur(28px), acentos sky #B2DEFF y acento #1da1f2. Revisa el
siguiente diff y genera la descripción de un PR de diseño en español:

## Qué
[1-2 frases describiendo el cambio visual.]

## Antes / Después
| Antes | Después |
|-------|---------|
| (screenshot) | (screenshot) |

## Decisiones de diseño
[Por qué este approach visual. Qué referencias usaste. Qué alternativas
descartaste.]

## Tokens afectados
[Lista los tokens CSS o clases que cambian: colores, glass, tipografía, etc.]

## Cómo probar
1. Abrir [ruta] en [navegador/resolución]
2. Verificar que [elemento] usa los tokens correctos
3. Verificar en mobile y dark/light mode

Reglas:
- Usa Conventional Commits: design(scope): descripción
- Todo cambio visual debe tener screenshots antes/después
- Respeta los tokens del sistema Liquid Glass — no hardcodees colores

Aquí está el diff:
```

---

## 6. Anti-patrones

Esto es lo que **no** hacemos:

| Anti-patrón | Por qué es dañino | Qué hacer en vez |
|-------------|-------------------|------------------|
| "Refactor mientras agrego feature" en el mismo PR | Hace el diff gigante e imposible de revisar | Rama separada de refactor, mergear primero |
| Commits tipo "wip", "test", "asdf" | La historia del repo se vuelve basura | Squash antes de pushear a remoto |
| PRs con 20+ archivos sin descripción | Nadie los revisa bien, bugs pasan desapercibidos | Partir en PRs más chicos o documentar excepcionalmente bien |
| "Funciona en mi máquina" | El VPS no es tu máquina | Probar en entorno similar a prod o documentar diferencias |
| Mergear sin review porque "es chiquito" | Los bugs más caros vienen de cambios de 1 línea | Todo PR lleva review, sin excepciones |
| Deploy a prod un viernes a las 6pm | Nadie quiere debuggear un sábado | Lunes a jueves, antes de las 4pm |
| Commits en inglés y español mezclados | Inconsistencia que hace el log ilegible | Todo en español, consistente con Conventional Commits |

---

## 7. Herramientas

- **Git:** `git rebase master`, `git push --force-with-lease` (nunca `--force` solo)
- **AI para commits:** usa los prompts de la sección 5 con Claude, ChatGPT o tu
  herramienta preferida
- **Screenshots:** Cmd+Shift+4 en macOS, pégalos directamente en la descripción
  del PR en GitHub
- **Pre-commit:** si el proyecto crece y agregamos linting/formateo, usaremos
  `pre-commit` hooks. Por ahora, revisión manual.

---

## 8. Referencia rápida

```
git checkout -b feat/mi-cambio          # crear rama
git add -p                              # stage interactivo (recomendado)
git commit -m "feat(scope): descripción" # commit con formato
git rebase master                       # actualizar contra master
git push origin feat/mi-cambio          # subir rama
# Abrir PR en GitHub con la descripción generada por AI
# Esperar review, responder comentarios
# Merge → borrar rama
```
