"""Prueba de punta a punta del panel Escritorio con Playwright.

Corre contra una instancia de WorksheLL ya levantada (le pasas la URL) y no
mueve ninguna ventana del sistema: solo lee el DOM, abre el dialogo de una zona,
aprieta «Releer» y arrastra una zona dentro del mapa.

    python _prueba_panel.py http://localhost:8082
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sys

from playwright.sync_api import sync_playwright

# La consola de esta maquina es cp1252 y se cae con las flechas y los acentos.
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8080'
ESTADOS = ('abierta', 'minimizada', 'cerrada', 'sin-asignar')
fallos = []


def estilos(pagina) -> list[str]:
    """El atributo style de cada zona, en el orden del mapa."""
    return pagina.eval_on_selector_all(
        '.mapa-zonas .zona', 'nodos => nodos.map(n => n.getAttribute("style"))')


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


with sync_playwright() as p:
    navegador = p.chromium.launch(channel='chrome', headless=True)
    pagina = navegador.new_page(viewport={'width': 1600, 'height': 1000})
    # `networkidle` no sirve: las iframes del panel (terminal, preview) pueden
    # quedarse cargando y la espera se vence con la página ya lista.
    pagina.goto(URL, wait_until='domcontentloaded')
    pagina.wait_for_selector('.mapa-zonas .zona', timeout=15000)

    print('puntos de estado')
    # El primer repaso de estados va a los 0,5 s de conectarse el cliente, así que
    # hay que esperar a que *todos* los puntos tengan su estado decidido: contar
    # los nodos da igual desde el arranque y la prueba seguiría antes de tiempo.
    pagina.wait_for_function(
        'document.querySelectorAll(".mapa-zonas .zona-estado").length === '
        'document.querySelectorAll(".mapa-zonas .zona").length && '
        '[...document.querySelectorAll(".mapa-zonas .zona-estado")]'
        '.every(n => /estado-/.test(n.className))',
        timeout=15000,
    )
    puntos = pagina.eval_on_selector_all(
        '.mapa-zonas .zona-estado',
        'nodos => nodos.map(n => ({clase: n.className, ayuda: n.getAttribute("title")}))',
    )
    chequear('hay un punto por zona', len(puntos) > 0, f'{len(puntos)} puntos')
    # Una zona sin asignar no lleva punto (el borde punteado ya lo dice), así que
    # lo que se exige es que *todas* tengan su estado decidido, sea cual sea.
    for i, punto in enumerate(puntos):
        estado = next((e for e in ESTADOS if f'estado-{e}' in punto['clase']), None)
        chequear(f'zona {i + 1} pintada', estado is not None,
                 f"{estado or 'sin estado'} · {punto['ayuda']}")
    chequear('y al menos una reporta un estado real del sistema',
             any(f'estado-{e}' in p['clase'] for p in puntos for e in ('abierta', 'minimizada')),
             str([p['clase'] for p in puntos]))

    print('\nminiaturas de las ventanas')
    # Las capturas las pide el navegador a /miniatura/<zona>; hay que darle un
    # momento: el primer barrido de estados sale a los 0,5 s y la captura recién
    # después, en su propio hilo.
    def cargadas() -> int:
        return pagina.eval_on_selector_all(
            '.mapa-zonas .zona-miniatura', 'nodos => nodos.filter(n => n.naturalWidth > 0).length')

    boton_miniaturas = pagina.get_by_role('button', name='Miniaturas:')
    chequear('«Miniaturas» está en el panel', boton_miniaturas.count() > 0)
    # El CSS pone el texto en mayúsculas, así que se compara sin distinguirlas.
    chequear('viene prendido', 'sí' in boton_miniaturas.first.inner_text().lower(),
             boton_miniaturas.first.inner_text())
    # Solo puede haber miniatura de una ventana que esté a la vista: si todas
    # están minimizadas o cerradas —como cuando el dueño dejó todo dormido— no
    # hay nada que capturar, y eso es correcto, no una falla.
    cuantas = 0
    if any('estado-abierta' in p['clase'] for p in puntos):
        for _ in range(24):
            cuantas = cargadas()
            if cuantas:
                break
            pagina.wait_for_timeout(500)
        chequear('alguna zona muestra su ventana de verdad', cuantas > 0,
                 f'{cuantas} imagen(es) cargadas')
    else:
        chequear('sin ninguna ventana a la vista no hay miniaturas, y está bien',
                 cargadas() == 0)
    chequear('la miniatura va debajo de la barra del nombre, no la tapa',
             pagina.eval_on_selector_all(
                 '.mapa-zonas .zona', 'nodos => nodos.every(n => {'
                 '  const img = n.querySelector(".zona-miniatura");'
                 '  return !img || getComputedStyle(n.querySelector(".zona-barra")).position === "absolute";'
                 '})'))

    zonas_antes = pagina.eval_on_selector_all('.mapa-zonas .zona', 'n => n')

    print('\nbotones del panel')
    for etiqueta in ('Aplicar', 'Acomodar todo', 'Capturar al frente', 'Zona nueva',
                     'Releer', 'Sumar ventana…'):
        hay = pagina.get_by_role('button', name=etiqueta, exact=True).count() > 0
        chequear(f'«{etiqueta}»', hay)
    # «Sumar ventana…» no se aprieta acá: aparta el panel de verdad y deja al
    # dueño esperando una ventana nueva. Se prueba en _prueba_sumar.py.
    # El de ignoradas lleva el contador en el texto, así que no se compara exacto.
    chequear('«Ignoradas»', pagina.get_by_role('button', name='Ignoradas').count() > 0)

    print('\nla lista de «no acomodar»')
    pagina.get_by_role('button', name='Ignoradas').first.click()
    pagina.wait_for_selector('.q-dialog', timeout=8000)
    texto_ignoradas = pagina.locator('.q-dialog').inner_text().lower()
    chequear('explica para qué sirve la lista',
             'no se acomodan solas' in texto_ignoradas
             or 'no hay ninguna' in texto_ignoradas,
             texto_ignoradas.replace('\n', ' ')[:110])
    pagina.get_by_role('button', name='Listo', exact=True).click()
    pagina.wait_for_selector('.q-dialog', state='detached', timeout=8000)

    print('\nlayouts: guardar como / renombrar / eliminar')
    for etiqueta in ('Guardar como…', 'Renombrar', 'Eliminar'):
        hay = pagina.get_by_role('button', name=etiqueta, exact=True).count() > 0
        chequear(f'«{etiqueta}»', hay)
    pagina.get_by_role('button', name='Guardar como…', exact=True).click()
    pagina.wait_for_selector('.q-dialog', timeout=8000)
    chequear('«Guardar como…» pide un nombre',
             pagina.locator('.q-dialog input[type="text"]').count() > 0)
    pagina.get_by_role('button', name='Cancelar', exact=True).click()
    pagina.wait_for_selector('.q-dialog', state='detached', timeout=8000)

    # «Acomodar todo» se prueba hasta la confirmación y se cancela: apretarlo de
    # verdad reparte TODAS las ventanas de la máquina, incluido lo que el dueño
    # tenga abierto, y eso es su decisión, no la de una prueba.
    print('\n«Acomodar todo» avisa antes de reemplazar')
    pagina.get_by_role('button', name='Acomodar todo', exact=True).click()
    pagina.wait_for_selector('.q-dialog', timeout=8000)
    # El título va en mayúsculas por CSS, así que se compara sin distinguirlas.
    texto = pagina.locator('.q-dialog').inner_text().lower()
    chequear('dice cuántas zonas reemplaza', 'reemplaza las' in texto,
             texto.replace('\n', ' ')[:110])
    chequear('dice cuántas ventanas encontró a la vista', 'a la vista' in texto,
             texto.replace('\n', ' ')[:110])
    casilla = pagina.locator('.q-dialog .q-checkbox')
    if casilla.count() > 0:
        chequear('ofrece incluir las minimizadas, y viene tildado',
                 'minimizadas' in texto and casilla.first.get_attribute('aria-checked') == 'true',
                 texto.replace('\n', ' ')[:110])
    chequear('y ofrece cancelar',
             pagina.get_by_role('button', name='Cancelar', exact=True).count() > 0)
    pagina.get_by_role('button', name='Cancelar', exact=True).click()
    pagina.wait_for_selector('.q-dialog', state='detached', timeout=8000)
    chequear('cancelar no toca las zonas', len(zonas_antes) == len(
        pagina.eval_on_selector_all('.mapa-zonas .zona', 'n => n')), str(len(zonas_antes)))

    print('\ndialogo de la zona')
    # Se dispara el mousedown directo sobre la zona en vez de clickear con el
    # mouse: si dos zonas se superponen, el clic real lo recibe la de arriba y la
    # prueba dependería de cómo el dueño tenga armado su mapa. El manejador del
    # arrastre escucha por delegación, así que un evento sobre el nodo alcanza.
    pagina.locator('.mapa-zonas .zona').first.dispatch_event('mousedown')
    pagina.locator('body').dispatch_event('mouseup')
    pagina.wait_for_selector('.q-dialog', timeout=8000)
    for etiqueta in ('Traer al frente', 'Aplicar esta zona', 'Releer posición',
                     'Sacar y no volver a acomodar'):
        hay = pagina.get_by_role('button', name=etiqueta, exact=True).count() > 0
        chequear(f'«{etiqueta}»', hay)
    # Ese último no se aprieta: modifica el layout del dueño y su lista de
    # ignoradas. Lo que se prueba acá es que esté donde tiene que estar.
    pagina.keyboard.press('Escape')
    pagina.wait_for_selector('.q-dialog', state='detached', timeout=8000)

    print('\nreleer posiciones')
    antes = pagina.eval_on_selector_all(
        '.mapa-zonas .zona',
        'nodos => nodos.map(n => n.style.left + "/" + n.style.width)',
    )
    pagina.get_by_role('button', name='Releer', exact=True).click()
    aviso = pagina.locator('.q-notification', has_text='zonas releídas').first
    aviso.wait_for(timeout=20000)
    chequear('avisa cuantas releyó', True, aviso.inner_text().replace('\n', ' '))

    print('\narrastre de una zona')
    # Se busca un punto donde el mouse realmente reciba la zona: si dos zonas se
    # superponen, el clic lo recibe la de arriba y la prueba terminaría
    # arrastrando otra. El arrastre tiene que ser con el mouse de verdad (el
    # manejador toma el punto inicial del mousedown y los siguientes del cursor,
    # así que un evento sintético suelto lo descoordina).
    elegida = pagina.evaluate('''() => {
        for (const z of document.querySelectorAll('.mapa-zonas .zona')) {
            const c = z.getBoundingClientRect();
            const x = c.left + c.width / 2, y = c.top + 20;
            if (document.elementFromPoint(x, y)?.closest('.zona') === z) {
                return {x, y, estilo: z.getAttribute('style')};
            }
        }
        return null;
    }''')
    chequear('hay una zona que recibe el clic (ninguna la tapa)', elegida is not None)
    if elegida is not None:
        estilos_antes = estilos(pagina)
        # Se arrastra hacia donde hay lugar: pegado al borde, el clamp lo
        # frenaría y la prueba diría que no se movió cuando en realidad no podía.
        izquierda = float(elegida['estilo'].split('left:')[1].split('%')[0])
        arriba = float(elegida['estilo'].split('top:')[1].split('%')[0])
        destino_x = elegida['x'] + (-90 if izquierda > 30 else 90)
        destino_y = elegida['y'] + (-30 if arriba > 30 else 30)

        pagina.mouse.move(elegida['x'], elegida['y'])
        pagina.mouse.down()
        pagina.mouse.move(destino_x, destino_y, steps=12)
        pagina.mouse.up()

        estilos_despues = estilos(pagina)
        cambiadas = [i for i, (a, b) in enumerate(zip(estilos_antes, estilos_despues)) if a != b]
        chequear('el arrastre movió una sola zona, la que se agarró', len(cambiadas) == 1,
                 f'cambiaron {len(cambiadas)}')
        if cambiadas:
            i = cambiadas[0]
            chequear('y la movió de verdad (no la dejó igual)',
                     estilos_despues[i] != estilos_antes[i],
                     estilos_despues[i].replace(';', ' · '))

    print('\nla zona arrastrada no abrió su dialogo')
    chequear('sin dialogo encima', pagina.locator('.q-dialog').count() == 0)

    navegador.close()

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
