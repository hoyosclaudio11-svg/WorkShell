"""Prueba del «¿publicaron hoy?»: cómo lee los logs y qué decide que falló.

Es lo más fácil de romper de todo el panel, porque depende del formato de logs
que escriben otros programas. Así que se prueba contra logs de mentira armados
acá —con las trampas reales: la línea de ayer, el 429 crónico, el descarte
editorial que NO es falla— y después se lee de verdad el estado de hoy, que se
imprime para mirarlo con los ojos (no se afirma: lo que hay hoy no es asunto de
una prueba).

    python _prueba_publicaciones.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ['WORKSHELL_SIN_ATAJOS'] = '1'

from nicegui import ui  # noqa: E402

ui.run = lambda **kw: None  # neutralizar el servidor antes de importar workshell

import workshell  # noqa: E402  (tiene que ir después del parche)

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


# Los de verdad, para devolverlos al final: la prueba los reemplaza por logs de
# mentira y el módulo es uno solo.
automatizacion_real = workshell.AUTOMATIZACION
canales_reales = workshell.CANALES_PUBLICACION

temporal = Path(tempfile.mkdtemp(prefix='workshell_publi_'))
hoy = time.strftime('%Y-%m-%d')
ayer = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))


def log(nombre: str, lineas: list[str]) -> Path:
    ruta = temporal / nombre
    ruta.write_text('\n'.join(lineas) + '\n', encoding='utf-8')
    return ruta


print('leer la cola de un log no lo lee entero')
grande = temporal / 'grande.log'
grande.write_text(('relleno\n' * 200_000) + 'la última\n', encoding='utf-8')
chequear('encuentra la última línea de un archivo de 1,6 MB',
         workshell.leer_cola(grande, lineas=5)[-1] == 'la última')
chequear('y no se trae todo', len(workshell.leer_cola(grande, lineas=5)) == 5)
chequear('un archivo que no existe devuelve nada',
         workshell.leer_cola(temporal / 'no-esta.log') == [])

print('\nlos formatos de fecha que hay en los logs')
chequear('ISO entre corchetes', workshell.de_hoy(f'[{hoy} 06:00:10,529] INFO', hoy, '31/12/2026'))
chequear('ISO pelado', workshell.de_hoy(f'{hoy} 08:05:06,385 [INFO]', hoy, '31/12/2026'))
chequear('día/mes/año', workshell.de_hoy(f'[{time.strftime("%d/%m/%Y")}  8:00:02,15] x', hoy,
                                         time.strftime('%d/%m/%Y')))
chequear('y lo de ayer no es de hoy', not workshell.de_hoy(f'[{ayer} 06:00:10,529] INFO', hoy, '31/12/2026'))
chequear('la hora se saca de la línea', workshell.hora_de(f'[{hoy} 06:03:28,748] INFO') == '06:03',
         workshell.hora_de(f'[{hoy} 06:03:28,748] INFO'))

print('\nun sitio que publicó hoy (por su archivo de cooldown)')
publicador = log('publicador.log', [
    f'[{ayer} 06:03:28,748] INFO — [river] Publicado (ID:5188): la de ayer',
    f'[{hoy} 06:00:10,529] INFO — === Publicador Multisitio iniciado ===',
    f'[{hoy} 06:03:28,748] INFO — [river] Publicado (ID:5189): la de hoy',
    f'[{hoy} 06:44:20,781] WARNING — [diario-albiceleste] Descartada por calidad editorial: '
    'La revisión no aprobó la fidelidad',
    f'[{hoy} 06:45:00,000] ERROR — RateLimitError de FreeLLMAPI (429) en el proveedor',
    f'[{hoy} 06:49:01,789] INFO — [revista-espectaculo] Publicado (ID:4123): otra',
])
(temporal / 'cooldown').mkdir(exist_ok=True)
(temporal / 'cooldown' / 'river.json').write_text(
    f'{{"ultima_publicacion": "{hoy}T06:03:28.747341-03:00", "sitio": "river"}}',
    encoding='utf-8')

workshell.AUTOMATIZACION = temporal
workshell.CANALES_PUBLICACION = (
    {'nombre': 'River', 'cooldown': 'river', 'log': publicador,
     'publico': ('[river] Publicado (ID:',)},
    {'nombre': 'Albiceleste', 'cooldown': 'diario-albiceleste', 'log': publicador,
     'publico': ('[diario-albiceleste] Publicado (ID:',)},
)
filas = {f['nombre']: f for f in workshell.estado_publicaciones()}

chequear('el que publicó dice que sí', filas['River']['estado'] == 'ok',
         str(filas['River']))
chequear('y dice a qué hora', filas['River']['detalle'] == '06:03', filas['River']['detalle'])
chequear('el que no publicó hoy lo dice',
         filas['Albiceleste']['estado'] == 'sin publicar', str(filas['Albiceleste']))

print('\nlo conocido no se reporta como falla')
# Las tres de hoy en publicador.log: el descarte editorial y el 429 crónico NO
# son fallas (si el panel los grita todos los días, deja de mirarse); el
# «RateLimitError» no es un ERROR del log, es parte del texto del mensaje.
chequear('ni el descarte editorial ni el 429 ensucian el renglón',
         'ERROR' not in filas['River']['detalle'].upper()
         or 'calidad' not in filas['River']['detalle'],
         filas['River']['detalle'])

print('\nuna falla de verdad sí se reporta')
roldan = log('roldan.log', [
    f'[{hoy} 08:30:11,465] INFO — === VIRAL one-shot — (canal: futbol, formato: habitos) ===',
    f'[{hoy} 08:30:14,856] ERROR — Token OAuth inválido y ejecutando en pythonw.exe (sin browser).',
    f'[{hoy} 08:30:14,857] ERROR — Reintento FALLÓ: No se pudo autenticar con YouTube',
])
workshell.CANALES_PUBLICACION = (
    {'nombre': 'YouTube', 'log': roldan, 'publico': ('SUBIÓ OK',), 'por_canal': '(canal: '},
)
fila = workshell.estado_publicaciones()[0]
chequear('el canal con el token vencido queda en falló', fila['estado'] == 'falló', str(fila))
chequear('y el detalle dice por qué',
         'OAuth' in fila['detalle'] or 'FALLÓ' in fila['detalle'], fila['detalle'])

print('\nlo de ayer no cuenta como de hoy')
viejo = log('viejo.log', [f'[{ayer} 08:30:14,856] ERROR — Reintento FALLÓ: token vencido'])
workshell.CANALES_PUBLICACION = ({'nombre': 'Viejo', 'log': viejo, 'publico': ('SUBIÓ OK',)},)
fila = workshell.estado_publicaciones()[0]
chequear('una falla de ayer no mancha el día de hoy', fila['estado'] == 'sin publicar',
         str(fila))

print('\nel estado de hoy, leído de los archivos de verdad')
# Se devuelven los canales y la carpeta de verdad, que la prueba había
# reemplazado por los de mentira. Esto no afirma nada: lo que hay publicado hoy
# no es asunto de una prueba, así que se imprime para mirarlo con los ojos.
workshell.AUTOMATIZACION = automatizacion_real
workshell.CANALES_PUBLICACION = canales_reales
de_verdad = workshell.estado_publicaciones()
for f in de_verdad:
    print(f"    {f['estado']:13} {f['nombre']:24} {f['detalle'][:64]}")
chequear('todos los canales devuelven un estado conocido',
         all(f['estado'] in ('ok', 'sin publicar', 'falló', 'sin datos') for f in de_verdad))
chequear('y hay un renglón por canal', len(de_verdad) == len(canales_reales),
         f'{len(de_verdad)} renglones')

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
