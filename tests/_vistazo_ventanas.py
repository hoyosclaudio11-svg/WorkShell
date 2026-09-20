"""Vistazo rápido: dónde está cada ventana de la organización actual.

Sirve para entender por qué una zona quedó rara (una ventana maximizada lee
0,0,100x100 y su zona tapa el mapa entero).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sys

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_, _, ancho, alto = ventanas.area_trabajo()
print(f'pantalla útil: {ancho}x{alto}')
for v in ventanas.ventanas_abiertas():
    x, y, w, h = v['rect']
    marca = ''
    if v['minimizada']:
        marca = ' [minimizada]'
    elif w >= ancho - 8 and h >= alto - 8:
        marca = ' [maximizada]'
    print(f'  {v["proceso"]:22} {x:6},{y:5} {w:5}x{h:<5}{marca} {v["titulo"][:44]}')
