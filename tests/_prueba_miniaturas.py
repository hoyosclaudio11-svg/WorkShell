"""Prueba de las miniaturas: que la captura sirva y que no cueste una barbaridad.

Solo lee: no mueve ninguna ventana. Trabaja sobre las zonas de layouts.json, que
son ventanas de verdad de esta máquina; si alguna no está abierta, la saltea.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import io
import json
import time
from pathlib import Path

from PIL import Image

import ventanas

ESCRITORIO = Path(__file__).parent
fallos = []


def chequear(condicion, mensaje: str) -> None:
    print(f'  {"OK " if condicion else "MAL"} {mensaje}')
    if not condicion:
        fallos.append(mensaje)


def main():
    datos = json.loads((ESCRITORIO / 'layouts.json').read_text(encoding='utf-8'))
    zonas = [z for layout in datos.values() for z in layout.get('ventanas', [])]
    print(f'{len(zonas)} zonas en layouts.json')

    estados = ventanas.estado_y_ventanas(zonas)
    for zona, (estado, _) in zip(zonas, estados):
        print(f'  {estado:11} {zona.get("etiqueta", "")[:44]}')

    print('\ncaptura:')
    por_zona = [v for _, v in estados]
    arranque = time.perf_counter()
    miniaturas = ventanas.capturar_zonas(por_zona)
    costo = time.perf_counter() - arranque
    abiertas = sum(1 for estado, _ in estados if estado == 'abierta')
    print(f'  {len(miniaturas)} miniaturas de {abiertas} ventanas a la vista '
          f'en {costo * 1000:.0f} ms')

    for i, jpeg in miniaturas.items():
        imagen = Image.open(io.BytesIO(jpeg))
        chica = imagen.convert('RGB').resize((40, 40))
        colores = len(set(chica.getdata()))
        etiqueta = zonas[i].get('etiqueta', '')[:34]
        print(f'  [{i}] {imagen.width}x{imagen.height} {len(jpeg) / 1024:5.1f} KB '
              f'{colores:4} colores  {etiqueta}')
        chequear(imagen.width <= ventanas.ANCHO_MINIATURA,
                 f'zona {i}: no más ancha que {ventanas.ANCHO_MINIATURA} px')
        chequear(colores > 20, f'zona {i}: la imagen tiene contenido, no es un plano')

    for i, (estado, _) in enumerate(estados):
        if estado == 'abierta':
            chequear(i in miniaturas, f'zona {i}: se capturó la que está a la vista')
        else:
            chequear(i not in miniaturas,
                     f'zona {i}: no se inventó una miniatura para una {estado}')

    # Una minimizada no se captura: su rect es (-32000, -32000, 160, 28).
    falsa = {'hwnd': 0, 'proceso': 'x.exe', 'titulo': 'x',
             'rect': (-32000, -32000, 160, 28), 'minimizada': True}
    chequear(ventanas.capturar(falsa) is None, 'una minimizada devuelve None')

    if costo > 2.0:
        chequear(False, f'capturar {len(miniaturas)} ventanas tardó {costo:.1f} s: '
                        'es demasiado para repetirlo cada pocos segundos')

    print('\n' + ('FALLÓ: ' + '; '.join(fallos) if fallos else 'todo bien'))


if __name__ == '__main__':
    main()
