"""Prueba en el navegador de la tira: el menú del clic derecho y el reordenar.

Lo que solo se ve en pantalla: que el clic derecho abra el menú con sus acciones
(adentro de una tarjeta que podría recortarlo), que arrastrar una tarjeta la
mueva de lugar y que ese orden llegue al servidor y al archivo —y que el arrastre
**no** termine eligiendo la ventana, que es el error fácil de este patrón—.

No toca el WorksheLL del dueño: se levanta una **instancia de prueba**, con una
copia del proyecto en una carpeta temporal, sus propios layouts (con ventanas que
no existen, así no puede mover nada) y su propio puerto, sin ventana y sin
atajos globales. Se borra al terminar.

    python _prueba_tarjetas_ui.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

AQUI = Path(__file__).parent
fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


def zonas_de_prueba() -> list[dict]:
    """Tres zonas sin ventana real: la tira las dibuja y nada más puede tocarlas.

    El proceso no existe entre las ventanas abiertas, así que aunque alguien
    apriete «Traer al frente» no hay a quién moverle nada.
    """
    return [
        {'etiqueta': nombre, 'proceso': f'zz_prueba_{nombre}.exe', 'titulo': f'Prueba {nombre}',
         'exe': '', 'args': '', 'lnk': '',
         'x': x, 'y': 0.0, 'ancho': 30.0, 'alto': 40.0}
        for x, nombre in ((0.0, 'uno'), (35.0, 'dos'), (70.0, 'tres'))
    ]


# --- la instancia de prueba ---------------------------------------------------
temporal = Path(tempfile.mkdtemp(prefix='workshell_ui_'))
for archivo in ('workshell.py', 'ventanas.py'):
    shutil.copy2(AQUI / archivo, temporal / archivo)  # copy2: se copian tal cual
(temporal / 'layouts.json').write_text(
    json.dumps({'Prueba': {'paneles': ['tarjetas'], 'ventanas': zonas_de_prueba()}},
               indent=2, ensure_ascii=False), encoding='utf-8')

entorno = dict(os.environ, WORKSHELL_SIN_VENTANA='1', WORKSHELL_SIN_ATAJOS='1')
salida = (temporal / 'salida.log').open('w', encoding='utf-8', errors='replace')
proceso = subprocess.Popen([sys.executable, '-u', 'workshell.py'], cwd=temporal,
                           env=entorno, stdout=salida, stderr=subprocess.STDOUT)

puerto = None
for _ in range(120):  # 30 s es de sobra: WorksheLL tarda ~16 s en estar listo
    time.sleep(0.25)
    texto = (temporal / 'salida.log').read_text(encoding='utf-8', errors='replace')
    encontrado = re.search(r'WorkShell en http://localhost:(\d+)', texto)
    if encontrado:
        puerto = int(encontrado.group(1))
        break

if puerto is None:
    print('la instancia de prueba no arrancó')
    print((temporal / 'salida.log').read_text(encoding='utf-8', errors='replace'))
    proceso.terminate()
    sys.exit(1)

URL = f'http://localhost:{puerto}'
print(f'instancia de prueba en {URL} (carpeta {temporal})')


def orden_guardado() -> list[str]:
    """El orden de las zonas tal como quedó en el archivo de la instancia."""
    datos = json.loads((temporal / 'layouts.json').read_text(encoding='utf-8'))
    return [z['etiqueta'] for z in datos['Prueba']['ventanas']]


def orden_en_pantalla(pagina) -> list[str]:
    return pagina.eval_on_selector_all(
        '.tarjeta-nombre', 'nodos => nodos.map(n => n.textContent.trim())')


def centro(caja: dict) -> tuple[float, float]:
    return caja['x'] + caja['width'] / 2, caja['y'] + caja['height'] / 2


try:
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel='chrome', headless=True)
        pagina = navegador.new_page(viewport={'width': 1280, 'height': 900})
        pagina.goto(URL, wait_until='domcontentloaded')
        pagina.wait_for_selector('.tarjeta', timeout=20000)
        pagina.wait_for_timeout(500)

        print('\nla tira')
        cantidad = pagina.locator('.tarjeta').count()
        chequear('una tarjeta por zona', cantidad == 3, f'{cantidad} tarjetas')
        numeros = pagina.eval_on_selector_all(
            '.tarjeta-num', 'nodos => nodos.map(n => n.textContent.trim())')
        chequear('cada tarjeta dice su número', numeros == ['1', '2', '3'], str(numeros))
        chequear('y arranca en el orden guardado',
                 orden_en_pantalla(pagina) == ['uno', 'dos', 'tres'],
                 str(orden_en_pantalla(pagina)))

        print('\nlas animaciones')
        animacion = pagina.eval_on_selector(
            '.tarjeta', 'n => getComputedStyle(n).animationName')
        chequear('las tarjetas entran animadas', animacion == 'tarjeta-entra', animacion)
        retardos = pagina.eval_on_selector_all(
            '.tarjeta', 'nodos => nodos.map(n => getComputedStyle(n).animationDelay)')
        chequear('y en fila, cada una un poco después de la anterior',
                 len(set(retardos)) == len(retardos), str(retardos))
        pagina.hover('.tarjeta >> nth=1')
        pagina.wait_for_timeout(400)  # que termine la transición del hover
        levantada = pagina.eval_on_selector(
            '.tarjeta >> nth=1', 'n => getComputedStyle(n).transform')
        chequear('al pasar por encima se levanta', levantada not in ('', 'none'), levantada)
        pagina.mouse.move(5, 5)  # sacar el mouse de encima
        pagina.wait_for_timeout(300)

        print('\nlos botones de la tira')
        for nombre in ('Auto', 'Opacidad'):
            chequear(f'«{nombre}» está en la tira',
                     pagina.get_by_role('button', name=re.compile(f'^{nombre}')).count() > 0)
        boton = pagina.get_by_role('button', name=re.compile('^Opacidad')).first
        antes = (boton.text_content() or '').strip()
        boton.click()
        pagina.wait_for_timeout(800)
        ahora = pagina.get_by_role('button', name=re.compile('^Opacidad')).first
        despues = (ahora.text_content() or '').strip()
        chequear('«Opacidad» recorre los valores al apretarla', antes != despues,
                 f'{antes} → {despues}')

        print('\nclic derecho sobre una tarjeta')
        caja = pagina.locator('.tarjeta').first.bounding_box()
        x, y = centro(caja)
        pagina.mouse.click(x, y, button='right')
        pagina.wait_for_timeout(700)  # que Quasar abra y posicione el menú
        # `offsetParent` no sirve para saber si un menú de Quasar se ve: va con
        # `position: fixed`, y ahí el navegador devuelve null aunque esté a la
        # vista. Se pregunta por las cajas que ocupa y por su display.
        estado = pagina.evaluate('''() => {
            const menus = [...document.querySelectorAll('.q-menu')];
            const visible = (e) => e.getClientRects().length > 0
                && getComputedStyle(e).display !== 'none'
                && getComputedStyle(e).visibility !== 'hidden';
            const abiertos = menus.filter(visible);
            const menu = abiertos[abiertos.length - 1];
            const caja = menu ? menu.getBoundingClientRect() : null;
            return {
                abiertos: abiertos.length,
                items: menu ? [...menu.querySelectorAll('.q-item')]
                    .map((i) => i.textContent.trim()) : [],
                caja: caja ? `${Math.round(caja.width)}x${Math.round(caja.height)}` : '',
                // Para cuando falle: qué menús hay y dónde vive cada uno.
                detalle: menus.map((m) => (m.parentElement || {}).className + ':'
                    + getComputedStyle(m).display + '/' + getComputedStyle(m).position
                ).join(' | ') || '(ninguno)',
            };
        }''')
        esperados = ['Traer al frente', 'Minimizar', 'Cerrar ventana…', 'Sacar del mapa',
                     'Sacar y no volver a acomodar']
        chequear('el menú abre con sus cinco acciones', estado['items'] == esperados,
                 f'{estado["items"]} · menús: {estado["detalle"]}')
        # La tarjeta no recorta su contenido: si lo hiciera, el menú saldría
        # cortado (o no saldría) y esto es lo que lo caza.
        chequear('y se ve entero, no recortado por la tarjeta',
                 estado['abiertos'] == 1 and estado['caja'] not in ('', '0x0'),
                 f'{estado["abiertos"]} abiertos, {estado["caja"]}')
        pagina.keyboard.press('Escape')
        pagina.wait_for_timeout(400)

        print('\narrastrar una tarjeta para reordenar')
        cajas = [pagina.locator('.tarjeta').nth(i).bounding_box() for i in range(3)]
        inicio = centro(cajas[0])
        destino = centro(cajas[2])
        pagina.mouse.move(*inicio)
        pagina.mouse.down()
        for paso in range(1, 11):  # en pasos: un salto seco no dispara el arrastre
            pagina.mouse.move(inicio[0] + (destino[0] - inicio[0]) * paso / 10,
                              inicio[1], steps=2)
        pagina.mouse.up()
        pagina.wait_for_timeout(1200)  # el viaje de ida y vuelta al servidor

        en_pantalla = orden_en_pantalla(pagina)
        chequear('la tarjeta queda al final', en_pantalla == ['dos', 'tres', 'uno'],
                 ' → '.join(en_pantalla))
        chequear('y el orden se guarda', orden_guardado() == ['dos', 'tres', 'uno'],
                 ' → '.join(orden_guardado()))
        avisos = pagina.locator('.q-notification').count()
        chequear('arrastrar no elige la ventana (el clic no pasó)', avisos == 0,
                 f'{avisos} avisos en pantalla')

        print('\nla tarjeta que se movió sigue siendo la misma')
        # El redibujo del reordenamiento no puede volver a animar la entrada: la
        # tira se redibuja cada vez que elegís una tarjeta, y animarla ahí sería
        # un temblor constante.
        clases = pagina.eval_on_selector_all(
            '.tarjeta', 'nodos => nodos.map(n => n.className)')
        chequear('el redibujo no las anima de nuevo',
                 all('entra' not in clase for clase in clases), str(clases))
        # El número de la tarjeta es su lugar, así que «uno» ahora dice 3: si el
        # redibujo hubiera mezclado las caras, acá se vería.
        numeros = pagina.eval_on_selector_all(
            '.tarjeta-num', 'nodos => nodos.map(n => n.textContent.trim())')
        chequear('los números acompañan el orden nuevo', numeros == ['1', '2', '3'],
                 str(numeros))
        etiquetas = orden_en_pantalla(pagina)
        chequear('y cada tarjeta sigue con su nombre', etiquetas == ['dos', 'tres', 'uno'],
                 str(etiquetas))
        navegador.close()
finally:
    proceso.terminate()
    try:
        proceso.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proceso.kill()
    salida.close()
    shutil.rmtree(temporal, ignore_errors=True)

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
