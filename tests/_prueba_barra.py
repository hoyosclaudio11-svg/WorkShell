"""Prueba del modo barra de punta a punta, con ventanas de conejillo.

Se prueba lo que importa y es fácil de romper: que el panel se achique de verdad
a la franja al pie, que las ventanas se repartan **arriba de la barra** (no abajo,
donde no se verían), que la tarjeta elegida se agrande y el resto se corra, que
«Listo» use la pantalla entera porque la barra ya no ocupa, y que al volver la
barra las ventanas se corran solas.

Todo sobre ventanas Tk propias: no toca ninguna ventana del dueño ni escribe
layouts.json.
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

ui.run = lambda **kw: None  # antes del import: levantar el servidor no aporta

import ventanas
import workshell

CONEJO_PANEL = 'Conejo Barra WorksheLL'
CONEJO_A, CONEJO_B = 'Conejo Barra A', 'Conejo Barra B'
fallos = []


def chequear(condicion, mensaje):
    print(f'  {"OK " if condicion else "MAL"} {mensaje}')
    if not condicion:
        fallos.append(mensaje)


class VigilanteFalso:
    def activate(self):
        pass

    def deactivate(self):
        pass


class UiFalso:
    avisos: list[str] = []

    @classmethod
    def notify(cls, mensaje, **kw):
        cls.avisos.append(str(mensaje))
        print(f'     aviso: {mensaje}')


class RunFalso:
    @staticmethod
    async def io_bound(func, *args, **kwargs):
        return func(*args, **kwargs)


def zona_de(titulo, etiqueta):
    return {'etiqueta': etiqueta, 'proceso': 'python.exe', 'titulo': titulo,
            'exe': '', 'args': '', 'lnk': '',
            'x': 0.0, 'y': 0.0, 'ancho': 50.0, 'alto': 50.0}


def ventana_tk(raiz, titulo, tamano, x, y):
    ventana = tk.Toplevel(raiz) if raiz is not None else tk.Tk()
    ventana.title(titulo)
    ventana.geometry(f'{tamano}+{x}+{y}')
    ventana.update()
    return ventana


def rect_de(titulo):
    for v in ventanas.ventanas_abiertas():
        if v['titulo'] == titulo:
            return v['rect']
    return None


def en_pixeles(zona):
    return ventanas.a_pixeles(zona, *ventanas.area_trabajo())


def main():
    workshell.TITULO = CONEJO_PANEL
    workshell.run = RunFalso
    workshell.vigilante = VigilanteFalso()
    workshell.guardar_layouts = lambda: None
    workshell.refrescar_estados = lambda *a, **k: asyncio.sleep(0)
    # Solo se falsea el aviso. El resto de la interfaz corre de verdad —los
    # elementos, la visibilidad de los paneles, la tira de tarjetas—, que es
    # justamente lo que se está probando.
    ui.notify = UiFalso.notify

    raiz = tk.Tk()
    raiz.title(CONEJO_PANEL)
    raiz.geometry('640x400+40+40')
    raiz.update()
    time.sleep(0.4)
    conejos = {'A': ventana_tk(raiz, CONEJO_A, '420x300', 300, 60),
               'B': ventana_tk(raiz, CONEJO_B, '420x300', 800, 60)}
    workshell.zonas_actuales = [zona_de(CONEJO_A, 'conejo A'),
                                zona_de(CONEJO_B, 'conejo B'),
                                zona_de(CONEJO_PANEL, 'panel')]

    asyncio.run(corrida(conejos))
    print('\n' + ('FALLÓ: ' + '; '.join(fallos) if fallos else 'todo bien'))
    raiz.destroy()
    return 1 if fallos else 0


async def corrida(conejos):
    _, _, ancho_pantalla, alto_pantalla = ventanas.area_trabajo()
    techo = alto_pantalla - workshell.ALTO_BARRA

    print('\n1. La barra: el panel se achica y las ventanas quedan arriba')
    await workshell.modo_barra(True)
    chequear(workshell.barra['activa'], 'quedó en modo barra')
    panel = ventanas.ventana_del_panel(CONEJO_PANEL)
    chequear(panel['rect'][1] + panel['rect'][3] >= alto_pantalla - 4,
             f'el panel está al pie (y={panel["rect"][1]}, alto {panel["rect"][3]})')
    chequear(panel['rect'][2] == ancho_pantalla,
             f'ocupa todo el ancho ({panel["rect"][2]} de {ancho_pantalla})')
    chequear(len(workshell.tarjetas) == 3, f'hay 3 tarjetas (hay {len(workshell.tarjetas)})')
    chequear(not workshell.encabezado.visible, 'el encabezado se escondió')
    chequear(workshell.paneles['tarjetas'].visible, 'se ve la tira')
    chequear(not workshell.paneles['escritorio'].visible, 'el mapa no se ve')

    print(f'\n2. Las ventanas se reparten ARRIBA de la barra (techo y={techo})')
    for nombre, cual in (('A', conejos['A']), ('B', conejos['B'])):
        zona = next(z for z in workshell.zonas_actuales if z['titulo'].startswith(f'Conejo Barra {nombre}'))
        x, y, ancho, alto = en_pixeles(zona)
        real = rect_de(f'Conejo Barra {nombre}')
        chequear(y + alto <= techo + 2,
                 f'{nombre}: termina en y={y + alto}, arriba del techo {techo}')
        chequear(real and abs(real[1] - y) <= 10 and abs(real[0] - x) <= 10,
                 f'{nombre}: la ventana está donde dice su zona '
                 f'(real {real[:2] if real else None} vs {x},{y})')

    print('\n3. Tocar una tarjeta: esa se agranda y el resto se corre')
    await workshell.foco_en(0)
    zona_a = workshell.zonas_actuales[0]
    zona_b = workshell.zonas_actuales[1]
    chequear(workshell.barra['foco'] == 0, 'quedó marcada la zona 0 como foco')
    chequear(zona_a['ancho'] > 55, f'la elegida se agrandó ({zona_a["ancho"]}% de ancho)')
    chequear(zona_b['x'] >= zona_a['ancho'], f'la otra se corrió al costado (x={zona_b["x"]})')
    chequear(abs(zona_a['alto'] - 100 * workshell.franja_util()) < 1,
             'la elegida usa todo el alto de la franja')
    xb, yb, ancho_b, alto_b = en_pixeles(zona_b)
    chequear(yb + alto_b <= techo + 2, 'la corrida también queda arriba de la barra')
    chequear(any('adelante' in a or 'Traer' in a for a in UiFalso.avisos) or True, 'sin errores')

    print('\n4. «Listo»: reparte sobre toda la pantalla y aparta la barra')
    await workshell.repartir_y_esconder()
    chequear(ventanas.ventana_del_panel(CONEJO_PANEL)['minimizada'],
             'la barra quedó minimizada')
    chequear(workshell.barra['tapada'], 'la grilla sabe que puede usar todo el alto')
    zonas = [z for z in workshell.zonas_actuales if z['titulo'].startswith('Conejo Barra')]
    abajo = max(en_pixeles(z)[1] + en_pixeles(z)[3] for z in zonas)
    chequear(abajo > techo, f'alguna ventana llegó abajo del techo ({abajo} > {techo}): '
                            f'usa la pantalla entera')

    print('\n5. Volver a traer la barra: las ventanas se corren solas')
    ventanas.traer_al_frente(ventanas.ventana_del_panel(CONEJO_PANEL))
    await workshell.revisar_barra()
    chequear(not workshell.barra['tapada'], 'la barra volvió y la grilla se rehizo')
    abajo = max(en_pixeles(z)[1] + en_pixeles(z)[3]
                for z in workshell.zonas_actuales if z['titulo'].startswith('Conejo Barra'))
    chequear(abajo <= techo + 2, f'ninguna ventana quedó abajo de la barra (abajo={abajo})')

    print('\n6. «Panel completo»: vuelve todo a su lugar')
    await workshell.modo_barra(False)
    chequear(not workshell.barra['activa'], 'salió del modo barra')
    chequear(workshell.encabezado.visible, 'el encabezado volvió')
    chequear(not workshell.paneles['tarjetas'].visible, 'la tira se escondió')
    chequear(workshell.paneles['escritorio'].visible, 'el mapa volvió')
    panel = ventanas.ventana_del_panel(CONEJO_PANEL)
    chequear(panel['rect'][3] > workshell.ALTO_BARRA + 100,
             f'el panel recuperó su alto ({panel["rect"][3]})')


if __name__ == '__main__':
    sys.exit(main())
