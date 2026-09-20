"""Prueba en vivo de la miniatura: una zona con una ventana de conejillo.

El motor ya se prueba en `_prueba_miniaturas.py`; lo que falta probar es el
camino entero de la interfaz: zona nueva → asignarle el conejillo → que el
navegador pida /miniatura/<zona> y la muestre. Se limpia solo: borra la zona que
agregó y cierra el conejillo.

    python _prueba_miniatura_ui.py http://localhost:8080
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8080'
CONEJO = 'Conejo Miniaturas WorksheLL'
fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


def conejillo_abierto() -> bool:
    return any(v['titulo'] == CONEJO for v in ventanas.ventanas_abiertas())


conejo = subprocess.Popen([sys.executable, str(Path(__file__).with_name('_conejo_miniaturas.py')),
                           CONEJO], cwd=str(Path(__file__).parent))
try:
    for _ in range(20):
        if conejillo_abierto():
            break
        time.sleep(0.5)
    print(f'conejillo: {"abierto" if conejillo_abierto() else "NO APARECIÓ"}')

    with sync_playwright() as p:
        navegador = p.chromium.launch(channel='chrome', headless=True)
        pagina = navegador.new_page(viewport={'width': 1600, 'height': 1000})
        # `networkidle` no sirve acá: el panel tiene iframes (terminal, preview)
        # que pueden quedarse cargando para siempre y la espera se vence aunque
        # la página esté lista. Se espera por el DOM y por lo que hace falta.
        pagina.goto(URL, wait_until='domcontentloaded')
        pagina.wait_for_selector('.mapa-zonas .zona', timeout=15000)
        zonas_antes = pagina.locator('.mapa-zonas .zona').count()

        print('\nzona nueva para el conejillo')
        pagina.get_by_role('button', name='Zona nueva', exact=True).click()
        pagina.wait_for_timeout(700)
        chequear('se agregó la zona', pagina.locator('.mapa-zonas .zona').count() == zonas_antes + 1,
                 f'{pagina.locator(".mapa-zonas .zona").count()} zonas')

        # El clic se dispara sobre el nodo (no con el mouse): la zona nueva puede
        # estar debajo de otra, y el manejador escucha por delegación.
        pagina.locator('.mapa-zonas .zona').last.dispatch_event('mousedown')
        pagina.locator('body').dispatch_event('mouseup')
        pagina.wait_for_selector('.q-dialog', timeout=8000)

        print('\nasignarle el conejillo')
        pagina.locator('.q-dialog .q-select').first.click()
        pagina.wait_for_timeout(400)
        pagina.keyboard.type('Conejo Miniaturas')
        pagina.wait_for_timeout(600)
        opcion = pagina.locator('.q-menu .q-item').filter(has_text=re.compile('Conejo Miniaturas')).first
        chequear('el conejillo aparece en la lista de ventanas', opcion.count() > 0)
        opcion.click()
        pagina.wait_for_timeout(300)
        pagina.get_by_role('button', name='Usar', exact=True).first.click()
        pagina.wait_for_selector('.q-dialog', state='detached', timeout=8000)

        print('\nla miniatura del conejillo')
        # Se espera la del conejillo, no «alguna»: las otras zonas pueden cargar
        # antes (o no cargar nunca, si su ventana está minimizada) y la prueba
        # seguiría antes de que la captura del conejillo llegue.
        def ancho_del_conejillo() -> int:
            return pagina.evaluate('''() => {
                for (const z of document.querySelectorAll('.mapa-zonas .zona')) {
                    if (!z.textContent.includes('Conejo Miniaturas')) continue;
                    return z.querySelector('.zona-miniatura')?.naturalWidth || 0;
                }
                return 0;
            }''')

        cargada = 0
        for _ in range(30):
            cargada = ancho_del_conejillo()
            if cargada:
                break
            pagina.wait_for_timeout(500)
        chequear('el navegador decodificó al menos una miniatura', cargada > 0,
                 f'{cargada} cargada(s)')
        # Foto de todas las zonas: sin esto, cuando una sola falla no se sabe si
        # el problema es de esa zona o de todas.
        print('     zonas:', pagina.evaluate('''() => [...document.querySelectorAll('.mapa-zonas .zona')]
            .map(z => {
                const img = z.querySelector('.zona-miniatura');
                return {nombre: z.textContent.trim().slice(0, 22),
                        punto: (z.querySelector('.zona-estado') || {}).className || '',
                        tieneSrc: !!img.getAttribute('src'),
                        ancho: img.naturalWidth};
            })'''))

        # La miniatura del conejillo: su zona es la única con ese título.
        detalle = pagina.evaluate('''() => {
            for (const z of document.querySelectorAll('.mapa-zonas .zona')) {
                if (!z.textContent.includes('Conejo Miniaturas')) continue;
                const img = z.querySelector('.zona-miniatura');
                return {src: img?.getAttribute('src'), ancho: img?.naturalWidth || 0,
                        alto: img?.naturalHeight || 0};
            }
            return null;
        }''')
        chequear('su zona tiene miniatura propia', bool(detalle and detalle['ancho'] > 0),
                 f"{detalle['ancho']}x{detalle['alto']}" if detalle else 'sin zona')
        if detalle and detalle['ancho']:
            # Los bytes viajan adentro del src: no hay ruta que pedir ni estado
            # que compartir entre las instancias del script (ver workshell.py).
            chequear('la imagen va adentro del src, no por una ruta',
                     (detalle['src'] or '').startswith('data:image/jpeg;base64,'),
                     (detalle['src'] or '')[:40])
            chequear('tiene el aspecto del conejillo (520x320 ≈ 1.6)',
                     1.3 < detalle['ancho'] / detalle['alto'] < 1.9,
                     f"{detalle['ancho']}x{detalle['alto']}")

        print('\nlimpieza: se borra la zona que agregó la prueba')
        pagina.locator('.mapa-zonas .zona').last.dispatch_event('mousedown')
        pagina.locator('body').dispatch_event('mouseup')
        pagina.wait_for_selector('.q-dialog', timeout=8000)
        pagina.get_by_role('button', name='Eliminar zona', exact=True).click()
        pagina.wait_for_selector('.q-dialog', state='detached', timeout=8000)
        pagina.wait_for_timeout(500)
        chequear('quedaron las zonas de antes', pagina.locator('.mapa-zonas .zona').count() == zonas_antes,
                 f'{pagina.locator(".mapa-zonas .zona").count()} zonas')
        navegador.close()
finally:
    conejo.terminate()

print('\n' + ('FALLÓ: ' + '; '.join(fallos) if fallos else 'todo bien'))
sys.exit(1 if fallos else 0)
