import subprocess, shutil, re, datetime, sys

RUTA = '/home/deploy/caddy/Caddyfile'

bloque_4a = """\t# analitica: eventos del rastreador y panel (servicio `analitica`, codigo en AureaLegalWEB3/servicios/analitica)
\thandle /api/evento {
\t\trequest_body {
\t\t\tmax_size 16KB
\t\t}
\t\theader Cache-Control "no-store"
\t\treverse_proxy analitica:8082
\t}

\t# /analitica y /analitica/... (NO /analitica.js, que es un archivo estatico)
\t@analitica path /analitica /analitica/*
\thandle @analitica {
\t\theader X-Robots-Tag "noindex, nofollow"
\t\treverse_proxy analitica:8082
\t}"""

bloque_4b = """\t# vista para el mapa de calor del panel: se deja mostrar en un iframe SOLO del mismo dominio
\t@mapa query mapa-de-calor=1
\thandle @mapa {
\t\theader {
\t\t\tCache-Control "no-store"
\t\t\tStrict-Transport-Security "max-age=31536000; includeSubDomains; preload"
\t\t\tX-Content-Type-Options "nosniff"
\t\t\tX-Frame-Options "SAMEORIGIN"
\t\t\tX-Robots-Tag "noindex, nofollow"
\t\t\tReferrer-Policy "strict-origin-when-cross-origin"
\t\t\tContent-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-src https://www.youtube.com https://www.youtube-nocookie.com; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'"
\t\t}
\t\ttry_files {path} {path}.html /index.html
\t\tfile_server
\t}"""

texto = open(RUTA).read()

# 4a: antes del bloque @static (ancla unica ASCII)
ancla_4a = '\t# assets: cache larga'
assert texto.count(ancla_4a) == 1, 'ancla 4a no unica (%d)' % texto.count(ancla_4a)
texto = texto.replace(ancla_4a, bloque_4a + '\n\n' + ancla_4a, 1)

# 4b: antes del comentario final "# todo lo demás..." (ancla ASCII, la 'a' acentuada queda fuera del match)
m = re.search(r'\n\t# todo lo dem', texto)
assert m, 'no encontre el comentario final'
texto = texto[:m.start()+1] + bloque_4b + '\n\n' + texto[m.start()+1:]

# verificacion de FORMA (regla 3 del skill): cada pieza aparece exactamente lo esperado
for s, n in [('@analitica path', 1), ('handle @analitica', 1), ('handle /api/evento', 1), ('@mapa query', 1),
             ('X-Frame-Options "SAMEORIGIN"', 1), ('reverse_proxy analitica:8082', 2)]:
    c = texto.count(s)
    assert c == n, '%s aparece %d veces (esperado %d)' % (s, c, n)

# backup con fecha ANTES de escribir (regla 1)
ts = datetime.datetime.now().strftime('%Y%m%d-%H%M')
bak = RUTA + '.bak-' + ts + '-analitica'
shutil.copy2(RUTA, bak)
open(RUTA, 'w').write(texto)
print('backup:', bak)

# validar (regla 3: validate en verde no es suficiente, por eso las comprobaciones de arriba)
r = subprocess.run(['docker', 'exec', 'aurea-caddy', 'caddy', 'validate',
                    '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'],
                   capture_output=True, text=True)
print('VALIDATE stdout:', r.stdout.strip())
print('VALIDATE stderr:', r.stderr.strip())
print('VALIDATE exit:', r.returncode)

if r.returncode != 0:
    shutil.copy2(bak, RUTA)
    print('REVERTIDO: validate fallo, restaurado', bak)
    sys.exit(1)

r2 = subprocess.run(['docker', 'exec', 'aurea-caddy', 'caddy', 'reload',
                     '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'],
                    capture_output=True, text=True)
print('RELOAD stdout:', r2.stdout.strip())
print('RELOAD stderr:', r2.stderr.strip())
print('RELOAD exit:', r2.returncode)
print('LISTO' if r2.returncode == 0 else 'FALLO RELOAD (revisa y restaura desde %s)' % bak)
