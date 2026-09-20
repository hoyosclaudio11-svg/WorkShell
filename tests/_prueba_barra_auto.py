"""Prueba del que se esconde sola: la barra se corre y vuelve con el mouse.

Son dos reglas de tiempo y una de posición, y las tres se pueden probar sin
mover nada de verdad: se le miente a `ventanas.cursor` (dónde está el mouse) y a
`ventanas.area_trabajo` (el tamaño de la pantalla), y se reemplazan las dos
acciones —esconder y traer— por anotadores. Así se mira la máquina de estados
sin que se mueva ninguna ventana del dueño.

    python _prueba_barra_auto.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

# Antes de importar: el registro de atajos globales vive en el import.
os.environ['WORKSHELL_SIN_ATAJOS'] = '1'

from nicegui import ui  # noqa: E402

ui.run = lambda **kw: None  # neutralizar el servidor antes de importar workshell

import workshell  # noqa: E402  (tiene que ir después del parche)
import ventanas  # noqa: E402

# Las preferencias que toca esta prueba (la opacidad) no pueden terminar en el
# panel.json del dueño: se escriben en una carpeta temporal.
workshell.RUTA_PANEL = Path(tempfile.mkdtemp(prefix='workshell_auto_')) / 'panel.json'

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


ANCHO, ALTO = 1920, 1080
raton = {'x': 100, 'y': 100}
hecho: list[str] = []

ventanas.area_trabajo = lambda: (0, 0, ANCHO, ALTO)
ventanas.cursor = lambda: (raton['x'], raton['y'])


async def esconder() -> None:
    hecho.append('esconder')
    workshell.barra['tapada'] = True
    workshell.barra['borde_desde'], workshell.barra['borde_armado'] = None, False


async def mostrar() -> None:
    hecho.append('mostrar')
    workshell.barra['tapada'] = False
    workshell.barra['borde_desde'], workshell.barra['borde_armado'] = None, False
    workshell.marcar_uso()


# Las acciones de verdad mueven ventanas: acá se anotan y se cambia el estado,
# que es lo que mira el vigilante.
workshell.repartir_y_esconder = esconder
workshell.mostrar_barra = mostrar

# La espera del borde es de 0,8 s: para la prueba alcanza con que sea medible.
workshell.ESPERA_BORDE = 0.05


def paso() -> None:
    asyncio.run(workshell.vigilar_barra())


def en_barra() -> None:
    """Deja el estado como si la barra estuviera a la vista, abajo."""
    workshell.barra.update({'activa': True, 'tapada': False, 'foco': None,
                            'auto': 25.0, 'ultimo_uso': time.monotonic(),
                            'borde_desde': None, 'borde_armado': False,
                            'panel': {'rect': (0, ALTO - workshell.ALTO_BARRA,
                                               ANCHO, workshell.ALTO_BARRA)}})


print('apagada no mira nada')
workshell.barra.update({'activa': False, 'tapada': False, 'panel': None})
hecho.clear()
raton.update({'x': 100, 'y': 100})
paso()
chequear('sin modo barra no hace nada', not hecho, str(hecho))

print('\ncon la barra a la vista')
en_barra()
hecho.clear()

raton.update({'x': 900, 'y': ALTO - 100})  # encima de la barra
workshell.barra['ultimo_uso'] = time.monotonic() - 60  # y vencida de tiempo
paso()
chequear('el mouse encima de la barra la mantiene', not hecho, str(hecho))
chequear('y corre el reloj', time.monotonic() - workshell.barra['ultimo_uso'] < 1,
         f'{time.monotonic() - workshell.barra["ultimo_uso"]:.2f} s')

raton.update({'x': 900, 'y': 300})  # lejos, y sin usarla hace rato
workshell.barra['ultimo_uso'] = time.monotonic() - 60
paso()
chequear('sin usarla se esconde', hecho == ['esconder'], str(hecho))

print('\ncon la barra escondida: solo vuelve si el mouse baja al borde')
hecho.clear()
raton.update({'x': 900, 'y': 300})  # lejos del borde: se arma
paso()
chequear('lejos del borde no vuelve', not hecho, str(hecho))
chequear('pero queda armada', workshell.barra['borde_armado'])

raton.update({'x': 900, 'y': ALTO - 1})  # contra el borde
paso()
chequear('al borde, todavía no: tiene que esperar', not hecho, str(hecho))

time.sleep(workshell.ESPERA_BORDE + 0.03)
paso()
chequear('dejándolo ahí, vuelve', hecho == ['mostrar'], str(hecho))
chequear('y queda sin esconder', not workshell.barra['tapada'])

print('\nno vuelve sola apenas se esconde')
hecho.clear()
en_barra()
workshell.barra['tapada'] = True
# El mouse sigue contra el borde porque el que la escondió estuvo ahí: con el
# borde armado volvería en el acto, y esconderla no serviría de nada.
raton.update({'x': 900, 'y': ALTO - 1})
workshell.barra['ultimo_uso'] = time.monotonic() - 60
paso()
time.sleep(workshell.ESPERA_BORDE + 0.03)
paso()
chequear('recién escondida no vuelve', not hecho, str(hecho))

print('\nla preferencia manda')
en_barra()
hecho.clear()
workshell.barra['auto'] = 0.0  # apagado: se queda hasta que la escondas vos
raton.update({'x': 900, 'y': 300})
workshell.barra['ultimo_uso'] = time.monotonic() - 3600
paso()
chequear('con «Auto: no» no se esconde nunca', not hecho, str(hecho))

hecho.clear()
workshell.barra['auto'] = 25.0
raton.update({'x': 900, 'y': 300})
workshell.barra['ultimo_uso'] = time.monotonic() - 30
paso()
chequear('con «Auto: 25 s» vuelve a esconderse a los 25', hecho == ['esconder'], str(hecho))

print('\nla transparencia de la barra')
# Lo que se prueba acá es cuándo y con qué valor se pide; que Windows y Chrome la
# apliquen de verdad se mide en `_prueba_opacidad.py`, contra una ventana real.
pedidos: list[int] = []


def opacidad_falsa(panel: dict, alfa: int) -> tuple[bool, str]:
    # Común y no `async`: `run.io_bound` corre la función en un hilo aparte y
    # espera un resultado, no una corrutina.
    pedidos.append(alfa)
    return True, ''


ventanas.opacidad = opacidad_falsa
workshell.barra['panel'] = {'hwnd': 1, 'rect': (0, 0, 1920, 260)}
workshell.barra['opacidad'] = 191
workshell.barra['activa'] = True
asyncio.run(workshell.aplicar_opacidad())
chequear('en modo barra pide la transparencia elegida', pedidos == [191], str(pedidos))

workshell.barra['activa'] = False
asyncio.run(workshell.aplicar_opacidad())
chequear('fuera del modo barra vuelve a opaca', pedidos == [191, 255], str(pedidos))

workshell.barra['activa'] = True
asyncio.run(workshell.alternar_opacidad())
# El recorrido es sólida → 87% → 75% → 60% y vuelta: desde 191 (75%) sigue 153.
chequear('el botón recorre las transparencias', pedidos[-1] == 153,
         f'de 191 pasó a {pedidos[-1]}')
chequear('y la guarda como preferencia', workshell.ajustes['opacidad'] == 153,
         str(workshell.ajustes['opacidad']))
workshell.barra['opacidad'] = 60  # un valor que no es del recorrido
asyncio.run(workshell.alternar_opacidad())
chequear('un valor raro no deja el botón sin salida',
         workshell.barra['opacidad'] in workshell.OPACIDADES,
         str(workshell.barra['opacidad']))
workshell.barra['activa'] = False
workshell.barra['panel'] = None
asyncio.run(workshell.aplicar_opacidad())
chequear('sin ventana del panel no se rompe', True)

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
