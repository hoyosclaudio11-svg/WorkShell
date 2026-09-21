"""Prueba de «Guardar como…» con la disposición de ahora, con ventanas de conejillo.

Desde el 21 sep 2026 «Guardar como…» no copia las coordenadas que el layout
tenía guardadas: relee dónde están las ventanas ahora mismo y esa disposición
es la que queda en la organización nueva — sin tocar las coordenadas del
layout original. Acá se prueba justo eso, que es donde vive la lógica: la
relectura (`releer_en`), la copia independiente y lo que cada caso conserva
(minimizada y ausente mantienen su coordenada guardada).

El diálogo en sí no se maneja: probarlo exigiría un cliente de NiceGUI y el
resto de `confirmar` es el mismo pegamento que ya estaba. Todo pasa sobre
ventanas Tk propias: no se toca ninguna ventana del dueño ni se escribe
layouts.json (`guardar_layouts` queda no-op).

El truco para no levantar el servidor: `ui.run` se reemplaza por un no-op
antes de importar WorksheLL (mismo truco que `_prueba_sumar.py`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import asyncio
import os
import sys
import tkinter as tk
import time

os.environ['WORKSHELL_SIN_VENTANA'] = '1'  # que no intente abrir Chrome

from nicegui import ui

ui.run = lambda **kw: None  # antes del import: levantarlo no aporta nada acá

import ventanas
import workshell

CONEJO_PANEL = 'Conejo Panel WorksheLL'
CONEJO_A = 'Conejo A'
CONEJO_C = 'Conejo C'
CONEJO_FANTASMA = 'Conejo Fantasma'

fallos = []


def chequear(condicion, mensaje: str) -> None:
    print(f'  {"OK " if condicion else "MAL"} {mensaje}')
    if not condicion:
        fallos.append(mensaje)


class UiFalso:
    """`ui.notify` sin cliente de NiceGUI: se registra lo que se hubiera avisado."""

    avisos: list[str] = []

    @classmethod
    def notify(cls, mensaje, **kw):
        cls.avisos.append(str(mensaje))
        print(f'     aviso: {mensaje}')


class RunFalso:
    """`run.io_bound` sin servidor: el de NiceGUI espera al loop del panel, que
    acá no corre, y cualquier await se quedaría esperando para siempre."""

    @staticmethod
    async def io_bound(func, *args, **kwargs):
        return func(*args, **kwargs)


def zona_de(titulo: str, etiqueta: str, x: float, y: float,
            ancho: float, alto: float) -> dict:
    return {'etiqueta': etiqueta, 'proceso': 'python.exe', 'titulo': titulo,
            'exe': '', 'args': '', 'lnk': '',
            'x': x, 'y': y, 'ancho': ancho, 'alto': alto}


def rect_de(titulo: str) -> tuple[int, int, int, int] | None:
    for v in ventanas.ventanas_abiertas():
        if v['titulo'] == titulo:
            return v['rect']
    return None


def guardar_como_simulado(nombre: str) -> None:
    """El cuerpo de `confirmar`, sin el diálogo.

    Es a propósito calcado del de workshell.py: si algún día se separa en una
    función propia, esta prueba pasa a llamarla directamente.
    """
    nuevo = workshell.copia_del_layout('Base')
    asignadas = [z for z in nuevo['ventanas']
                 if z.get('proceso') or z.get('exe') or z.get('lnk')]
    releidas = minimizadas = ausentes = 0
    if asignadas:
        releidas, minimizadas, ausentes = asyncio.run(workshell.releer_en(asignadas))
    workshell.layouts[nombre] = nuevo
    UiFalso.notify(f'Layout «{nombre}» guardado con {len(nuevo["ventanas"])} zonas · '
                   f'{releidas} con la posición de ahora · '
                   f'{minimizadas + ausentes} conservaron su coordenada guardada')


def main():
    # WorksheLL, pero sin tocar nada del dueño: ni la interfaz ni layouts.json.
    workshell.ui = UiFalso
    workshell.run = RunFalso
    workshell.guardar_layouts = lambda: None
    workshell.dibujar_zonas = lambda: None

    # La organización «Base» tiene coordenadas viejas a propósito: lo que se
    # guarda tiene que salir de la pantalla, no de este diccionario.
    workshell.layouts = {
        'Base': {
            'paneles': ['escritorio'],
            'ventanas': [
                zona_de(CONEJO_A, 'conejo A', 0.0, 0.0, 50.0, 50.0),
                zona_de(CONEJO_C, 'conejo C', 25.0, 25.0, 40.0, 40.0),
                zona_de(CONEJO_FANTASMA, 'fantasma', 50.0, 50.0, 30.0, 30.0),
            ],
        },
    }
    workshell.zonas_actuales = workshell.layouts['Base']['ventanas']

    raiz = tk.Tk()
    raiz.title(CONEJO_PANEL)
    raiz.geometry('300x200+60+60')
    raiz.update()
    conejo_a = tk.Toplevel(raiz)
    conejo_a.title(CONEJO_A)
    conejo_a.geometry('400x300+600+240')
    conejo_a.update()
    conejo_c = tk.Toplevel(raiz)
    conejo_c.title(CONEJO_C)
    conejo_c.geometry('300x200+80+500')
    conejo_c.update()
    # La minimizada reporta (-32000, -32000, ...): leerla mandaría la zona al
    # rincón, así que tiene que conservar su coordenada guardada.
    conejo_c.iconify()
    raiz.update()
    time.sleep(0.4)

    try:
        corrida(raiz, conejo_a)
    finally:
        raiz.destroy()

    print('\n' + ('FALLÓ: ' + '; '.join(fallos) if fallos else 'todo bien'))
    return 1 if fallos else 0


def corrida(raiz, conejo_a):
    real_a = rect_de(CONEJO_A)
    chequear(real_a is not None, 'el conejo A existe y se lo encuentra el barrido')
    chequear(any(v['minimizada'] for v in ventanas.ventanas_abiertas()
                 if v['titulo'] == CONEJO_C),
             'el conejo C está minimizado para el barrido')

    print('\n1. La copia es independiente del layout original')
    nuevo = workshell.copia_del_layout('Base')
    chequear(nuevo['ventanas'][0] is not workshell.layouts['Base']['ventanas'][0],
             'las zonas de la copia no son los diccionarios del original')

    print('\n2. «Guardar como…» captura la disposición de la pantalla')
    guardar_como_simulado('Ahora')
    avisos = ' '.join(UiFalso.avisos)
    chequear(any('guardado con 3 zonas' in a for a in UiFalso.avisos),
             f'aviso lo que guardó ({avisos})')
    guardada = {z['titulo']: z for z in workshell.layouts['Ahora']['ventanas']}

    esperada = ventanas.geometria_en_porcentaje(real_a)
    zona_a = guardada[CONEJO_A]
    caja = ventanas.a_pixeles(zona_a, *ventanas.area_trabajo())
    chequear(ventanas.posicion_respetada(real_a, caja, tolerancia=10),
             f'conejo A: quedó con la posición real de ahora '
             f'(zona {zona_a["x"]},{zona_a["y"]} vs pantalla {esperada["x"]},{esperada["y"]})')

    zona_c = guardada[CONEJO_C]
    chequear((zona_c['x'], zona_c['y'], zona_c['ancho'], zona_c['alto'])
             == (25.0, 25.0, 40.0, 40.0),
             'conejo C minimizada: conservó su coordenada guardada')

    zona_f = guardada[CONEJO_FANTASMA]
    chequear((zona_f['x'], zona_f['y'], zona_f['ancho'], zona_f['alto'])
             == (50.0, 50.0, 30.0, 30.0),
             'conejo fantasma (no está abierta): conservó su coordenada guardada')

    print('\n3. El layout original no se entera')
    original = {z['titulo']: z for z in workshell.layouts['Base']['ventanas']}
    chequear((original[CONEJO_A]['x'], original[CONEJO_A]['y']) == (0.0, 0.0),
             'la zona del conejo A en «Base» sigue con su coordenada vieja')
    chequear((original[CONEJO_C]['x'], original[CONEJO_C]['y']) == (25.0, 25.0)
             and (original[CONEJO_FANTASMA]['x'], original[CONEJO_FANTASMA]['y'])
             == (50.0, 50.0),
             'las demás zonas de «Base» tampoco se movieron')
    chequear(workshell.zonas_actuales is workshell.layouts['Base']['ventanas'],
             'la organización en uso sigue siendo «Base»')


if __name__ == '__main__':
    sys.exit(main())
