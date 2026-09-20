"""Prueba del modo barra en el navegador, contra una instancia ya levantada.

Lo que agrega sobre `_prueba_barra.py` (que prueba la lógica con conejillos) es
lo que solo se ve en pantalla: que la tira **entre en 260 px de alto**, que las
tarjetas muestren la cara de verdad y que el encabezado se vaya.

OJO: aprieta «Barra al pie», así que mueve de verdad las ventanas del dueño (las
reparte en la franja) y reescribe sus zonas. Respaldá layouts.json antes.

    python _prueba_barra_ui.py http://localhost:8080
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sys

from playwright.sync_api import sync_playwright

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8080'
ALTO_BARRA = 260
fallos = []


def chequear(nombre, condicion, detalle=''):
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


with sync_playwright() as p:
    navegador = p.chromium.launch(channel='chrome', headless=True)
    # La ventana del panel pasa a medir 1920x260: así se ve lo mismo que va a ver
    # el dueño, y una tira que no entra se nota acá.
    pagina = navegador.new_page(viewport={'width': 1920, 'height': ALTO_BARRA})
    # `networkidle` no sirve: las iframes del panel pueden quedarse cargando.
    pagina.goto(URL, wait_until='domcontentloaded')
    pagina.wait_for_selector('.mapa-zonas .zona', timeout=15000)

    print('\nel botón')
    chequear('«Barra al pie» está en el panel',
             pagina.get_by_role('button', name='Barra al pie', exact=True).count() > 0)

    print('\nentrar al modo barra')
    pagina.get_by_role('button', name='Barra al pie', exact=True).click()
    pagina.wait_for_selector('.tarjeta', timeout=20000)
    pagina.wait_for_timeout(1500)

    tarjetas = pagina.locator('.tarjeta').count()
    chequear('hay una tarjeta por ventana', tarjetas > 0, f'{tarjetas} tarjetas')
    chequear('el encabezado se fue',
             pagina.locator('.titulo').count() == 0
             or not pagina.locator('.titulo').first.is_visible())
    chequear('el mapa no se ve', not pagina.locator('.mapa-zonas').first.is_visible())

    print('\nla tira entra en 260 px')
    alto = pagina.evaluate('() => document.body.scrollHeight')
    chequear('no hay scroll vertical', alto <= ALTO_BARRA + 8,
             f'el contenido mide {alto} px de {ALTO_BARRA}')
    caja = pagina.locator('.tarjeta').first.bounding_box()
    chequear('la tarjeta es chica pero se ve', caja['height'] > 60,
             f'{round(caja["width"])}x{round(caja["height"])} px')

    print('\nlas caras')
    for _ in range(20):
        cargadas = pagina.eval_on_selector_all(
            '.tarjeta-cara', 'nodos => nodos.filter(n => n.naturalWidth > 0).length')
        if cargadas:
            break
        pagina.wait_for_timeout(500)
    chequear('alguna tarjeta muestra su ventana', cargadas > 0, f'{cargadas} cargadas')

    print('\ntocar una tarjeta: esa se agranda')
    # Se elige una cuya ventana esté a la vista: tocar la tarjeta de una que ya
    # no existe solo avisa, y la prueba diría que el foco no funciona.
    cual = pagina.evaluate('''() => {
        const tarjetas = [...document.querySelectorAll('.tarjeta')];
        return tarjetas.findIndex(t => t.querySelector('.estado-abierta'));
    }''')
    chequear('hay una tarjeta de una ventana abierta', cual >= 0, f'índice {cual}')
    if cual >= 0:
        pagina.locator('.tarjeta').nth(cual).click()
        pagina.wait_for_timeout(2500)
    en_foco = pagina.locator('.tarjeta.en-foco').count()
    chequear('la tocada queda marcada', en_foco == 1, f'{en_foco} en foco')
    recuadro = pagina.evaluate('''() => {
        const t = document.querySelector('.tarjeta.en-foco');
        return t ? getComputedStyle(t).borderColor : null;
    }''')
    chequear('y se marca con el borde del foco', recuadro is not None, str(recuadro))

    print('\n«Repartir» vuelve a parejo')
    pagina.get_by_role('button', name='Repartir', exact=True).click()
    pagina.wait_for_timeout(2500)
    chequear('no queda ninguna en foco', pagina.locator('.tarjeta.en-foco').count() == 0)

    print('\n«Panel completo» devuelve todo')
    pagina.get_by_role('button', name='Panel completo', exact=True).click()
    pagina.wait_for_timeout(2000)
    chequear('el encabezado volvió',
             pagina.locator('.titulo').first.is_visible())
    chequear('el mapa volvió', pagina.locator('.mapa-zonas').first.is_visible())
    chequear('la tira se escondió', not pagina.locator('.tarjetas-tira, .tira-tarjetas').first.is_visible()
             if pagina.locator('.tira-tarjetas').count() else True)
    navegador.close()

print('\n' + ('FALLÓ: ' + '; '.join(fallos) if fallos else 'todo bien'))
sys.exit(1 if fallos else 0)
