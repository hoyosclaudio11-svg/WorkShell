"""Entra al modo barra, reparte y deja que WorksheLL cuente qué pasó.

Es para diagnosticar: el resultado del acomodo queda en `_workshell.log`, que es
donde ahora se ve qué ventana no se dejó mover (antes solo salía un cartel que se
iba a los segundos).

    python _reproducir_acomodo.py [url]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sys
import time

from playwright.sync_api import sync_playwright

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8080'

with sync_playwright() as p:
    navegador = p.chromium.launch(channel='chrome', headless=True)
    pagina = navegador.new_page(viewport={'width': 1920, 'height': 260})
    pagina.goto(URL, wait_until='domcontentloaded')
    pagina.wait_for_selector('.mapa-zonas .zona', timeout=15000)
    print('zonas en el mapa:', pagina.locator('.mapa-zonas .zona').count())
    for i in range(pagina.locator('.mapa-zonas .zona-estado').count()):
        print('   ', pagina.locator('.mapa-zonas .zona-estado').nth(i).get_attribute('title'))

    print('\nentrando al modo barra…')
    pagina.get_by_role('button', name='Barra al pie', exact=True).click()
    pagina.wait_for_selector('.tarjeta', timeout=20000)
    time.sleep(6)  # que termine el reparto

    print('repartiendo parejo…')
    pagina.get_by_role('button', name='Repartir', exact=True).click()
    time.sleep(6)

    # El caso que quedó mal: tocar una tarjeta y que la agrandada no se mueva.
    cual = pagina.evaluate('''() => [...document.querySelectorAll('.tarjeta')]
        .findIndex(t => t.querySelector('.estado-abierta'))''')
    if cual >= 0:
        nombre = pagina.locator('.tarjeta').nth(cual).inner_text().strip()
        print(f'toque la tarjeta {cual}: {nombre!r}')
        pagina.locator('.tarjeta').nth(cual).click()
        time.sleep(7)
        print('   zonas del mapa:',
              pagina.evaluate('''() => [...document.querySelectorAll('.mapa-zonas .zona')]
                  .map(z => z.getAttribute('style'))'''))
    else:
        print('no hay ninguna tarjeta de ventana abierta para tocar')

    print('saliendo del modo barra…')
    pagina.get_by_role('button', name='Panel completo', exact=True).click()
    time.sleep(4)
    navegador.close()

print('\nlisto: mirá el log de WorksheLL')
