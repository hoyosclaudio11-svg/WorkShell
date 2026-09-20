"""Prueba de lo que se hace desde la tira: reordenar y sacar zonas.

Lo que importa acá es que los números sigan queriendo decir lo mismo después de
tocar el orden: la zona que estaba agrandada (el foco), el caché de caras —que
está numerado por zona, no por ventana— y la lista guardada. Se importa workshell
sin levantar el servidor y **sin tocar los archivos del dueño**: los guardados se
redirigen a una carpeta temporal.

    python _prueba_tarjetas.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import sys
import tempfile
from pathlib import Path

# Antes de importar: el registro de atajos globales vive en el import, y una
# prueba no puede quedarse con Ctrl+Alt+1..9 (ni, si WorksheLL está corriendo,
# pisarle las teclas al que está en uso).
os.environ['WORKSHELL_SIN_ATAJOS'] = '1'

from nicegui import ui  # noqa: E402

ui.run = lambda **kw: None  # neutralizar el servidor antes de importar workshell

import workshell  # noqa: E402  (tiene que ir después del parche)

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Nada de esto puede escribir los layouts.json / ignoradas.json de verdad: la
# prueba reordena y saca zonas, que es exactamente lo que los reescribe.
temporal = Path(tempfile.mkdtemp(prefix='workshell_prueba_'))
workshell.RUTA_LAYOUTS = temporal / 'layouts.json'
workshell.RUTA_IGNORADAS = temporal / 'ignoradas.json'

fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


def zona(nombre: str) -> dict:
    """Una zona de mentira con la ventana que dice su nombre."""
    return {'etiqueta': nombre, 'proceso': f'{nombre.lower()}.exe', 'titulo': nombre,
            'exe': '', 'args': '', 'lnk': '', 'x': 0.0, 'y': 0.0, 'ancho': 50.0, 'alto': 50.0}


def orden() -> list[str]:
    return [z['etiqueta'] for z in workshell.zonas_actuales]


def armar(*nombres: str) -> None:
    """Deja la tira con esas zonas, con una cara capturada para cada una."""
    workshell.zonas_actuales.clear()
    workshell.zonas_actuales.extend(zona(n) for n in nombres)
    workshell.miniaturas.clear()
    workshell.miniaturas_viejas.clear()
    for i, z in enumerate(workshell.zonas_actuales):
        workshell.miniaturas[i] = (workshell.clave_de(z), f'jpeg de {z["etiqueta"]}'.encode())


def cara(i: int) -> str:
    """Qué ventana muestra la zona `i`: la prueba de que el caché se renumeró."""
    datos = workshell.captura_de(i)
    return datos.decode().removeprefix('jpeg de ') if datos else '(ninguna)'


print('reordenar: la tarjeta va al lugar donde la soltaste')
armar('A', 'B', 'C', 'D')
# Los índices que manda el navegador: dónde estaba y dónde quedó, ya movida en el
# DOM. Mover la primera al tercer lugar deja B, C, A, D → a = 2.
workshell.al_reordenar({'de': 0, 'a': 2})
chequear('A del primer lugar al tercero', orden() == ['B', 'C', 'A', 'D'], ' → '.join(orden()))
chequear('y las caras viajan con su ventana',
         [cara(i) for i in range(4)] == ['B', 'C', 'A', 'D'],
         ', '.join(cara(i) for i in range(4)))

armar('A', 'B', 'C', 'D')
workshell.al_reordenar({'de': 3, 'a': 0})
chequear('la última al frente', orden() == ['D', 'A', 'B', 'C'], ' → '.join(orden()))
chequear('y las caras también', [cara(i) for i in range(4)] == ['D', 'A', 'B', 'C'],
         ', '.join(cara(i) for i in range(4)))

armar('A', 'B', 'C')
workshell.al_reordenar({'de': 1, 'a': 1})
chequear('soltarla donde estaba no cambia nada', orden() == ['A', 'B', 'C'], ' → '.join(orden()))

workshell.al_reordenar({'de': 9, 'a': 0})
workshell.al_reordenar({'de': 0, 'a': 9})
workshell.al_reordenar({'de': 'uno', 'a': 0})
chequear('un índice que no existe no rompe nada', orden() == ['A', 'B', 'C'], ' → '.join(orden()))

print('\nreordenar: la agrandada cambia de número, no de ventana')
armar('A', 'B', 'C', 'D')
workshell.barra['foco'] = 2  # C está agrandada
workshell.al_reordenar({'de': 0, 'a': 2})  # A se mete delante de ella
chequear('el foco sigue a la misma ventana', workshell.barra['foco'] == 1,
         f'foco {workshell.barra["foco"]} → {orden()[workshell.barra["foco"]]}')

armar('A', 'B', 'C', 'D')
workshell.barra['foco'] = 1  # B
workshell.al_reordenar({'de': 3, 'a': 0})  # D pasa al frente
chequear('el foco se corre cuando le pasan por encima', workshell.barra['foco'] == 2,
         f'foco {workshell.barra["foco"]} → {orden()[workshell.barra["foco"]]}')

armar('A', 'B', 'C', 'D')
workshell.barra['foco'] = 0  # A, la que se arrastra
workshell.al_reordenar({'de': 0, 'a': 3})
chequear('el foco viaja con la que arrastraste', workshell.barra['foco'] == 3,
         f'foco {workshell.barra["foco"]} → {orden()[workshell.barra["foco"]]}')
workshell.barra['foco'] = None

print('\nsacar una zona del mapa')
armar('A', 'B', 'C', 'D')
workshell.barra['foco'] = 2
workshell.quitar_zona(1)
chequear('sale del orden', orden() == ['A', 'C', 'D'], ' → '.join(orden()))
chequear('el foco se corre con ella', workshell.barra['foco'] == 1,
         f'foco {workshell.barra["foco"]} → {orden()[workshell.barra["foco"]]}')
chequear('y las caras quedan con su ventana', [cara(i) for i in range(3)] == ['A', 'C', 'D'],
         ', '.join(cara(i) for i in range(3)))

armar('A', 'B', 'C')
workshell.barra['foco'] = 1
workshell.quitar_zona(1)
chequear('si sacás la agrandada no queda ninguna agrandada',
         workshell.barra['foco'] is None)
chequear('la ventana no se toca: solo sale del mapa', orden() == ['A', 'C'], ' → '.join(orden()))

print('\nsacar y no volver a acomodar')
workshell.ignoradas.clear()
armar('A', 'B')
workshell.quitar_zona(1, ignorar=True)
chequear('la anota en la lista de ignoradas',
         [(r.get('proceso'), r.get('titulo')) for r in workshell.ignoradas] == [('b.exe', 'B')],
         str(workshell.ignoradas))
chequear('y la zona igual sale', orden() == ['A'], ' → '.join(orden()))

print('\nlas zonas se guardan donde se pidió (no en el archivo del dueño)')
chequear('el guardado fue al temporal',
         workshell.RUTA_LAYOUTS.parent == temporal and workshell.RUTA_LAYOUTS.exists(),
         str(workshell.RUTA_LAYOUTS))

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
