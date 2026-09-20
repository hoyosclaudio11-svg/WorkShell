"""Compara lo que dice layouts.json con donde estan las ventanas de verdad.

Sirve para saber si una disposicion guardada sigue describiendo la realidad o
quedo vieja. No mueve nada: solo lee.    python _verificar_zonas.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json
import sys
from pathlib import Path

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

datos = json.loads(Path('layouts.json').read_text(encoding='utf-8'))
for nombre, layout in datos.items():
    zonas = layout.get('ventanas', [])
    if not zonas:
        continue
    print(f'\n{nombre}')
    encontradas = ventanas.ubicaciones(zonas)
    for zona, ventana in zip(zonas, encontradas):
        guardado = (zona.get('x'), zona.get('y'), zona.get('ancho'), zona.get('alto'))
        if ventana is None:
            print(f'  {zona.get("etiqueta")[:34]:36} guardado {guardado}')
            print(f'  {"":36} SIN VENTANA')
            continue
        real = ventanas.geometria_en_porcentaje(ventana['rect'])
        real_tupla = (real['x'], real['y'], real['ancho'], real['alto'])
        igual = all(abs(a - b) <= 0.5 for a, b in zip(guardado, real_tupla))
        print(f'  {zona.get("etiqueta")[:34]:36} {"igual" if igual else "DISTINTO"}')
        if not igual:
            print(f'  {"":36} guardado {guardado}')
            print(f'  {"":36} real     {real_tupla}  ({ventana["proceso"]})')
