"""Prueba de «Sumar ventana» de punta a punta, con ventanas de conejillo.

Todo pasa sobre ventanas Tk propias (el «panel» y las apps que se van abriendo):
no se toca ninguna ventana del dueño ni se escribe layouts.json. Se prueba el
camino entero —apartar el panel, ver aparecer la ventana nueva, volver y
repartir en grilla— salteando solo los avisos de la interfaz.

El truco para no levantar el servidor: `ui.run` se reemplaza por un no-op antes
de importar WorksheLL (mismo truco que `_prueba_guard.py`).
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
CONEJO_B = 'Conejo B'

fallos = []


def chequear(condicion, mensaje: str) -> None:
    print(f'  {"OK " if condicion else "MAL"} {mensaje}')
    if not condicion:
        fallos.append(mensaje)


class VigilanteFalso:
    """El timer de NiceGUI: acá se llama a mano, así que solo anota si está prendido."""

    def __init__(self):
        self.prendido = False

    def activate(self):
        self.prendido = True

    def deactivate(self):
        self.prendido = False


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


def zona_de(titulo: str, etiqueta: str) -> dict:
    return {'etiqueta': etiqueta, 'proceso': 'python.exe', 'titulo': titulo,
            'exe': '', 'args': '', 'lnk': '',
            'x': 0.0, 'y': 0.0, 'ancho': 50.0, 'alto': 50.0}


def ventana_tk(raiz, titulo: str, tamano: str, x: int, y: int):
    ventana = tk.Toplevel(raiz) if raiz is not None else tk.Tk()
    ventana.title(titulo)
    ventana.geometry(f'{tamano}+{x}+{y}')
    ventana.update()
    return ventana


def rect_de(titulo: str) -> tuple[int, int, int, int] | None:
    for v in ventanas.ventanas_abiertas():
        if v['titulo'] == titulo:
            return v['rect']
    return None


def main():
    # WorksheLL, pero sin tocar nada del dueño: ni la interfaz ni layouts.json.
    workshell.TITULO = CONEJO_PANEL
    workshell.ui = UiFalso
    workshell.run = RunFalso
    workshell.vigilante = VigilanteFalso()
    workshell.guardar_layouts = lambda: None
    workshell.dibujar_zonas = lambda: None
    workshell.refrescar_estados = lambda *a, **k: asyncio.sleep(0)

    raiz = tk.Tk()
    raiz.title(CONEJO_PANEL)
    raiz.geometry('300x200+60+60')
    raiz.update()
    time.sleep(0.4)
    conejos = {'A': ventana_tk(raiz, CONEJO_A, '300x200', 400, 60)}
    workshell.zonas_actuales = [zona_de(CONEJO_PANEL, 'panel'),
                                zona_de(CONEJO_A, 'conejo A')]

    panel = ventanas.ventana_del_panel(CONEJO_PANEL)
    chequear(panel is not None, 'encuentra la ventana del panel por título exacto')
    chequear(ventanas.ventana_del_panel('Conejo Panel WorksheLL con ruido') is None,
             'no la confunde con una ventana que solo la menciona')

    asyncio.run(corrida(raiz, conejos, panel))
    print('\n' + ('FALLÓ: ' + '; '.join(fallos) if fallos else 'todo bien'))
    raiz.destroy()
    return 1 if fallos else 0


async def corrida(raiz, conejos, panel):
    print('\n1. «Sumar ventana»: el panel se aparta')
    await workshell.sumar_ventana()
    chequear(ventanas.ventana_del_panel(CONEJO_PANEL)['minimizada'],
             'el panel quedó minimizado (el escritorio a la vista)')
    chequear(workshell.busqueda['activa'], 'quedó esperando una ventana nueva')
    chequear(workshell.vigilante.prendido, 'prendió el vigilante')
    chequear(not any('Conejo B' in z['etiqueta'] for z in workshell.zonas_actuales),
             'todavía no hay ninguna zona para lo que no se abrió')

    print('\n2. Se abre una ventana nueva (el conejo B)')
    conejos['B'] = ventana_tk(raiz, CONEJO_B, '300x200', 700, 60)
    chequear(rect_de(CONEJO_B) is not None, 'la ventana nueva existe')

    print('\n3. El vigilante la ve, espera a que se asiente y vuelve')
    for intento in range(24):
        await workshell.vigilar_ventana_nueva()
        if not workshell.busqueda['activa']:
            break
        await asyncio.sleep(0.5)
    print(f'  (volvió en el intento {intento + 1}, {intento * 0.5:.1f} s)')

    chequear(not workshell.busqueda['activa'], 'terminó la espera')
    chequear(not workshell.vigilante.prendido, 'apagó el vigilante')
    chequear(not ventanas.ventana_del_panel(CONEJO_PANEL)['minimizada'],
             'el panel volvió de su minimizado')
    adelante = ventanas.al_frente()
    chequear(adelante is not None and adelante['titulo'] == CONEJO_PANEL,
             f'el panel quedó adelante (adelante está: '
             f'{adelante["titulo"] if adelante else "nada"})')

    print('\n4. La ventana nueva entró al mapa y se repartió todo')
    titulos = [z['titulo'] for z in workshell.zonas_actuales]
    chequear(CONEJO_B in titulos, 'hay una zona para la ventana nueva')
    chequear(len(workshell.zonas_actuales) == 3,
             f'siguen las 3 zonas (panel + A + B): hay {len(workshell.zonas_actuales)}')

    zonas = {z['titulo']: z for z in workshell.zonas_actuales}
    for titulo, conejo in ((CONEJO_A, conejos['A']), (CONEJO_B, conejos['B'])):
        zona = zonas[titulo]
        real = rect_de(titulo)
        dentro = (0 <= zona['x'] <= 100 and 0 <= zona['y'] <= 100
                  and 0 < zona['ancho'] <= 100 and 0 < zona['alto'] <= 100)
        chequear(dentro, f'{titulo}: la zona quedó dentro del mapa '
                         f'({zona["x"]},{zona["y"]} {zona["ancho"]}x{zona["alto"]})')
        if real:
            caja = ventanas.a_pixeles(zona, *ventanas.area_trabajo())
            chequear(ventanas.posicion_respetada(real, caja, tolerancia=10),
                     f'{titulo}: la ventana está donde dice su zona '
                     f'(real {real[:2]} vs {caja[:2]})')

    celdas = {(round(zonas[t]['x']), round(zonas[t]['y']))
              for t in (CONEJO_A, CONEJO_B)}
    chequear(len(celdas) == 2, 'cada conejo está en su propia celda, sin pisarse')

    print('\n5. Lo que NO se toca')
    chequear(zonas[CONEJO_PANEL]['x'] == 0.0 and zonas[CONEJO_PANEL]['y'] == 0.0,
             'el panel no entra en su propia grilla (se quedaría colgando)')
    chequear(any('Volví con 1' in a for a in UiFalso.avisos),
             'avisó con cuántas volvió')


if __name__ == '__main__':
    sys.exit(main())
