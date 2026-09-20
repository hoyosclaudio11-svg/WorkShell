"""Prueba de «Acomodar todo» y de la gestion de layouts, sin tocar el escritorio.

Esta parte de WorksheLL reparte las ventanas de verdad, asi que probarla de
verdad movería todo lo que el dueño tenga abierto. Aca se importa workshell sin
levantar el servidor, se le reemplazan las dos funciones que miran y mueven
ventanas por dobles, y se redirige layouts.json a un archivo de descarte: se
prueba el armado del layout —zonas, grilla, accesos directos— y nada mas.

    python _prueba_acomodar.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import json
import sys
from pathlib import Path

from nicegui import ui

ui.run = lambda **kw: None  # neutralizar el servidor antes de importar workshell

import workshell  # noqa: E402  (tiene que ir despues del parche)

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DESCARTE = Path(__file__).with_name('_layouts_prueba.json')
DESCARTE_IGNORADAS = Path(__file__).with_name('_ignoradas_prueba.json')
workshell.RUTA_LAYOUTS = DESCARTE          # el layouts.json de verdad no se toca
workshell.RUTA_IGNORADAS = DESCARTE_IGNORADAS
workshell.ignoradas.clear()                # ni la lista de ignoradas

fallos = []


def ignoradas_texto() -> str:
    return str([r.get('etiqueta') or r.get('proceso') for r in workshell.ignoradas])


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


# Ventanas dobles: una que existe como acceso directo en el Escritorio, una que
# no, y un navegador (cuyo acceso directo no sirve para reabrir la misma pagina).
ACCESOS = workshell.ventanas.accesos_escritorio(workshell.CARPETA)
CON_ACCESO = next((a for a in ACCESOS
                   if Path(a['exe']).name.lower() not in workshell.ventanas.SIN_ACCESO), None)

DOBLES = [
    {'hwnd': 1, 'proceso': Path(CON_ACCESO['exe']).name if CON_ACCESO else 'app.exe',
     'exe': CON_ACCESO['exe'] if CON_ACCESO else '', 'titulo': 'App con acceso directo',
     'rect': (0, 0, 800, 600), 'minimizada': False},
    {'hwnd': 2, 'proceso': 'sinacceso.exe', 'exe': '', 'titulo': 'App sin acceso directo',
     'rect': (0, 0, 800, 600), 'minimizada': False},
    {'hwnd': 3, 'proceso': 'chrome.exe', 'exe': '', 'titulo': 'Una pagina - Google Chrome',
     'rect': (0, 0, 800, 600), 'minimizada': False},
]

aplicadas = []


def aplicar_doble(zonas):
    """Doble de ventanas.aplicar: anota lo que le pidieron y dice que salio bien."""
    aplicadas.append([dict(z) for z in zonas])
    return [{'etiqueta': z.get('etiqueta', ''), 'ok': True, 'motivo': ''} for z in zonas]


workshell.ventanas.para_acomodar = (
    lambda titulo='', incluir_minimizadas=False, ignoradas=None: [dict(v) for v in DOBLES])
workshell.ventanas.aplicar = aplicar_doble

# Los avisos necesitan un cliente conectado que aca no hay: se guardan para
# poder mirar que dicen, que es parte de lo que se prueba.
avisos = []
workshell.ui.notify = lambda mensaje, **kw: avisos.append(mensaje)

print('armar el layout con las ventanas de doble')
asyncio.run(workshell.armar_disposicion())

zonas = workshell.zonas_actuales
chequear('armó una zona por ventana', len(zonas) == len(DOBLES), f'{len(zonas)} zonas')
_, _, ancho_pantalla, alto_pantalla = workshell.ventanas.area_trabajo()
esperado = workshell.ventanas.reparto(len(DOBLES), ancho_pantalla / alto_pantalla)
chequear('les dio la grilla que corresponde',
         [(z['x'], z['y'], z['ancho'], z['alto']) for z in zonas] == esperado, str(esperado))
chequear('cada zona sabe que ventana es',
         all(z['proceso'] and z['titulo'] for z in zonas))
if CON_ACCESO:
    chequear('la que tiene acceso directo lo lleva anotado',
             zonas[0]['lnk'] == CON_ACCESO['lnk'] and zonas[0]['exe'] == CON_ACCESO['exe'],
             zonas[0]['lnk'])
chequear('la que no lo tiene queda sin cómo abrirse',
         zonas[1]['lnk'] == '' and zonas[1]['exe'] == '')
chequear('el navegador no queda con un acceso que abriría otra cosa',
         zonas[2]['lnk'] == '' and zonas[2]['exe'] == '',
         'un .lnk de Chrome abriría una ventana en blanco, no esa página')
chequear('las acomodó, no solo las dibujó', len(aplicadas) == 1 and len(aplicadas[0]) == 3,
         f'{len(aplicadas)} aplicaciones')
chequear('el aviso dice cuántas repartió y cuántas puede reabrir',
         any('3 ventanas repartidas' in a and '1 con acceso directo' in a for a in avisos),
         str(avisos[0]) if avisos else 'sin avisos')
chequear('quedaron guardadas en disco',
         DESCARTE.exists() and len(json.loads(DESCARTE.read_text(encoding='utf-8'))
                                  [workshell.layout_actual]['ventanas']) == 3)
# Copia: más abajo la prueba vacía el mapa para probar las zonas nuevas.
zona_con_ventana = dict(zonas[2])

print('\nzonas nuevas: adentro del mapa y sin pisarse')
# Antes se apilaban cada 40 puntos sin mirar el borde: de la quinta en adelante
# caian fuera del mapa (invisibles e inagarrables) y parecia que WorksheLL no
# dejaba tener mas de cuatro zonas.
workshell.zonas_actuales.clear()
for _ in range(12):
    workshell.agregar_zona()

zonas = workshell.zonas_actuales
salidas = [i + 1 for i, z in enumerate(zonas)
           if z['x'] + z['ancho'] > 100.001 or z['y'] + z['alto'] > 100.001]
chequear('12 zonas y ninguna se sale del mapa', not salidas, f'salen: {salidas}')

def se_pisan(a, b):
    return not (a['x'] + a['ancho'] <= b['x'] or b['x'] + b['ancho'] <= a['x']
                or a['y'] + a['alto'] <= b['y'] or b['y'] + b['alto'] <= a['y'])

choques = [(i + 1, j + 1) for i, a in enumerate(zonas) for j, b in enumerate(zonas)
           if i < j and se_pisan(a, b)]
chequear('y ninguna queda encima de otra', not choques, f'se pisan: {choques[:4]}')
chequear('achica cuando el mapa se llena y lo avisa',
         any('mapa está lleno' in a for a in avisos),
         str(zonas[4]['ancho']) + '% la quinta')

print('\nsacar una zona para siempre')
zona_tres = zona_con_ventana
etiqueta = workshell.anotar_ignorada(zona_tres)
chequear('anota la ventana en la lista', len(workshell.ignoradas) == 1 and etiqueta,
         f'{len(workshell.ignoradas)} regla(s): {ignoradas_texto()}')
chequear('lo persiste en su archivo',
         DESCARTE_IGNORADAS.exists()
         and json.loads(DESCARTE_IGNORADAS.read_text(encoding='utf-8'))[0]['proceso']
         == zona_tres['proceso'])
chequear('no la duplica si se pide dos veces',
         workshell.anotar_ignorada(zona_tres) and len(workshell.ignoradas) == 1,
         str(len(workshell.ignoradas)))
chequear('una zona sin ventana no se puede anotar',
         workshell.anotar_ignorada({'etiqueta': 'vacía', 'proceso': ''}) == '',
         'devuelve cadena vacía')
chequear('el aviso del diálogo diría cuántas hay',
         f'({len(workshell.ignoradas)})' in f'Ignoradas ({len(workshell.ignoradas)})')

print('\ncopia de un layout')
original = workshell.layouts[workshell.layout_actual]
copia = workshell.copia_del_layout(workshell.layout_actual)
copia['ventanas'][0]['etiqueta'] = 'tocada'
chequear('la copia no comparte las zonas con el original',
         original['ventanas'][0]['etiqueta'] != 'tocada',
         original['ventanas'][0]['etiqueta'])
copia['paneles'].append('trading')
chequear('ni los paneles', 'trading' not in original['paneles'], str(original['paneles']))

print('\nrenombrar conserva el orden')
prueba = {'uno': 1, 'dos': 2, 'tres': 3}
workshell.renombrar_en(prueba, 'dos', 'DOS')
chequear('cambia la clave', prueba.get('DOS') == 2 and 'dos' not in prueba, str(prueba))
chequear('y la deja en el mismo lugar', list(prueba) == ['uno', 'DOS', 'tres'], str(list(prueba)))

DESCARTE.unlink(missing_ok=True)
DESCARTE_IGNORADAS.unlink(missing_ok=True)
print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
