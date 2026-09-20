"""¿Se puede achicar la ventana del propio WorksheLL a una barra fina?

Es la pregunta que decide el diseño de la tira de tarjetas: si Chrome se deja
achicar, el panel puede vivir como una barra al pie con la pantalla arriba para
las ventanas; si no, la tira va adentro del panel y «esconder» es minimizarlo.

Deja la ventana como estaba (tamaño y minimizado incluidos).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import time

import ventanas

TITULO = 'WorkShell'

panel = ventanas.ventana_del_panel(TITULO)
if panel is None:
    raise SystemExit('no encontré la ventana del panel')
original = panel['rect']
estaba_minimizada = panel['minimizada']
print(f'antes: {original} {"minimizada" if estaba_minimizada else "visible"}')

ancho_pantalla, alto_pantalla = ventanas.area_trabajo()[2:]
print(f'pantalla útil: {ancho_pantalla}x{alto_pantalla}')

ok, logrado, motivo = ventanas.mover(panel, 0, alto_pantalla - 260, ancho_pantalla, 260)
time.sleep(0.4)
real = ventanas.ventana_del_panel(TITULO)['rect']
print(f'mover -> {real}  (devolvió ok={ok} motivo={motivo!r})')
print('¿quedó como barra?', ventanas.posicion_respetada(real, (0, alto_pantalla - 260,
                                                              ancho_pantalla, 260)))

# De vuelta como estaba: tamaño, posición y minimizado.
ventanas.mover(panel, original[0], original[1], original[2], original[3])
if estaba_minimizada:
    time.sleep(0.3)
    ventanas.apartar(ventanas.ventana_del_panel(TITULO))
time.sleep(0.4)
print('devuelta:', ventanas.ventana_del_panel(TITULO)['rect'],
      'minimizada' if ventanas.ventana_del_panel(TITULO)['minimizada'] else 'visible')
