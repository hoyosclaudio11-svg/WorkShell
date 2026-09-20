"""Muestra en píxeles cómo queda la grilla, para mirarla con los ojos.

Sin esto hay que imaginarse los rectángulos en porcentaje, y el ojo es el que
decide si una disposición sirve o no.

    python _ver_grilla.py [ancho] [alto]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sys

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ANCHO = int(sys.argv[1]) if len(sys.argv) > 1 else 1920
ALTO = int(sys.argv[2]) if len(sys.argv) > 2 else 820   # la franja de la barra
aspecto = ANCHO / ALTO


def en_px(rects):
    return [(round(x / 100 * ANCHO), round(y / 100 * ALTO),
             round(w / 100 * ANCHO), round(h / 100 * ALTO)) for x, y, w, h in rects]


print(f'caja {ANCHO}x{ALTO} (aspecto {aspecto:.2f})')
for cantidad in range(2, 7):
    parejo = en_px(ventanas.reparto(cantidad, aspecto, min(aspecto, 1.78)))
    foco = en_px(ventanas.foco(cantidad, aspecto))
    print(f'\n{cantidad} ventanas — parejo:')
    for i, (x, y, w, h) in enumerate(parejo):
        print(f'   {i + 1}: {w:4}x{h:<4} en ({x:4},{y:4})  forma {w / h:.2f}')
    print(f'{cantidad} ventanas — con la primera agrandada:')
    for i, (x, y, w, h) in enumerate(foco):
        marca = ' <- agrandada' if i == 0 else ''
        print(f'   {i + 1}: {w:4}x{h:<4} en ({x:4},{y:4})  forma {w / h:.2f}{marca}')
