"""Propiedades de la grilla, para que no vuelvan los huecos.

El 19 sep 2026 el dueño reportó "espacios vacíos por todos lados": la grilla
dejaba la última fila incompleta y la celda grande del foco quedaba vacía si esa
ventana no se dejaba mover. Esto fija las reglas que tienen que valer siempre:

    - las celdas entran en la caja,
    - no se superponen,
    - entre todas llenan la caja (nada de huecos),
    - y ninguna queda más aplastada que una banda razonable.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sys

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

fallos = []


def chequear(condicion, mensaje):
    print(f'  {"OK " if condicion else "MAL"} {mensaje}')
    if not condicion:
        fallos.append(mensaje)


def area(rects):
    return sum(w * h for _, _, w, h in rects)


def se_superponen(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx + 0.01 or bx + bw <= ax + 0.01
                or ay + ah <= by + 0.01 or by + bh <= ay + 0.01)


def revisar(nombre, rects, cantidad, aspecto):
    chequear(len(rects) == cantidad, f'{nombre}: hay {cantidad} celdas')
    chequear(all(0 <= x and 0 <= y and x + w <= 100.01 and y + h <= 100.01
                 for x, y, w, h in rects),
             f'{nombre}: todas entran en la caja')
    choques = [(i, j) for i in range(len(rects)) for j in range(i + 1, len(rects))
               if se_superponen(rects[i], rects[j])]
    chequear(not choques, f'{nombre}: ninguna se superpone {choques or ""}')
    total = area(rects)
    chequear(abs(total - 10000) < 1, f'{nombre}: llenan la caja ({total:.2f} de 10000)')
    # El aspecto real de una celda es (w/h) * aspecto_pantalla: los rectángulos
    # vienen en porcentaje, y un 50% de ancho no es lo mismo que un 50% de alto.
    # La banda es ancha a propósito: con 3 ventanas, la de abajo se estira a lo
    # ancho (4:1) para no dejar el hueco, y eso es mejor que un agujero. El piso
    # es para la única ventana del costado en la disposición con foco, que no
    # tiene con qué repartirse el alto (730x820).
    peores = [(round(w / h * aspecto, 2), (x, y, w, h)) for x, y, w, h in rects
              if not 0.6 <= w / h * aspecto <= 5.0]
    chequear(not peores, f'{nombre}: ningún aspecto desquiciado {peores[:3]}')


for aspecto in (1.78, 2.34, 1.6):
    print(f'\npantalla {aspecto}:1')
    for cantidad in range(1, 9):
        revisar(f'  {cantidad} ventanas parejo', ventanas.reparto(cantidad, aspecto),
                cantidad, aspecto)

print('\nfoco (una grande y el resto al costado)')
for cantidad in range(2, 8):
    revisar(f'  {cantidad} ventanas con foco', ventanas.foco(cantidad, 1.78),
            cantidad, 1.78)

print('\nfoco en la franja de la barra (1920x820)')
for cantidad in range(2, 8):
    revisar(f'  {cantidad} en la franja', ventanas.foco(cantidad, 1920 / 820),
            cantidad, 1920 / 820)

print('\n' + ('FALLÓ: ' + '; '.join(fallos) if fallos else 'todo bien'))
sys.exit(1 if fallos else 0)
