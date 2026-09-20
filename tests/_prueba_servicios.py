"""Prueba del panel de servicios y del trading: leer, no romper, y no mentir.

Tres cosas se prueban acá:
 · que la lista de servicios se pueda corregir desde su archivo y que un archivo
   roto no se lleve puesto el arranque del panel;
 · que un servicio caído se vea como caído sin que nada explote —el caso normal:
   el panel tiene que decir «no contesta», no llenarse de errores ni quedarse
   colgado esperando a un puerto que no existe—;
 · que los números del MT5, si está corriendo, se lean de verdad (los servicios
   ya publican por HTTP y no hace falta hablar con el terminal).

    python _prueba_servicios.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
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


temporal = Path(tempfile.mkdtemp(prefix='workshell_servicios_'))
workshell.RUTA_SERVICIOS = temporal / 'servicios.json'

print('la lista de servicios')
lista = workshell.cargar_servicios()
chequear('si no existe, se escribe con la de fábrica',
         workshell.RUTA_SERVICIOS.exists() and len(lista) == len(workshell.SERVICIOS_DEFAULT),
         f'{len(lista)} servicios')
chequear('y trae los que importan',
         {'FreeLLMAPI', 'MT5 trader', 'ttyd'} <= {s['nombre'] for s in lista},
         ', '.join(s['nombre'] for s in lista))
chequear('todos tienen puerto y nombre', all(s.get('puerto') and s.get('nombre') for s in lista))

workshell.RUTA_SERVICIOS.write_text('{ roto', encoding='utf-8')
chequear('un archivo roto no rompe el arranque',
         len(workshell.cargar_servicios()) == len(workshell.SERVICIOS_DEFAULT))
workshell.RUTA_SERVICIOS.write_text(json.dumps([
    {'nombre': 'Inventado', 'puerto': 59999},
    {'sin': 'nombre ni puerto'},  # se descarta
    'esto no es un diccionario',  # se descarta
]), encoding='utf-8')
propia = workshell.cargar_servicios()
chequear('una lista propia manda, y lo que no sirve se descarta',
         [s['nombre'] for s in propia] == ['Inventado'], str(propia))

print('\n¿está levantado?')
chequear('un puerto cerrado se ve cerrado', not workshell.puerto_abierto(59999),
         'puerto 59999')
chequear('uno abierto se ve abierto', workshell.puerto_abierto(8080), 'puerto 8080 (WorksheLL)')
chequear('y pregunta rápido: no se queda esperando',
         workshell.puerto_abierto(59999, espera=0.2) is False)
estados = workshell.estado_servicios([{'nombre': 'a', 'puerto': 8080},
                                      {'nombre': 'b', 'puerto': 59999}])
chequear('el barrido devuelve uno por servicio', estados == [True, False], str(estados))

print('\nun servicio caído no rompe nada')
chequear('pedir datos a un puerto muerto devuelve vacío',
         workshell.pedir_json('http://127.0.0.1:59999/estado') == {})
chequear('y a una URL que no es JSON, también',
         workshell.pedir_json('http://127.0.0.1:8080/') == {})
ok, motivo = workshell.levantar_servicio({'nombre': 'sin lanzador', 'puerto': 1})
chequear('sin «lanzar» en el archivo, lo dice en vez de fallar', not ok and 'lanzar' in motivo,
         motivo)

print('\nlos números del MT5')
cuenta = {}
if workshell.puerto_abierto(8060):
    datos = workshell.estado_mt5()
    cuenta = datos.get('cuenta') or {}
    chequear('la cuenta llega con equity y balance',
             'equity' in cuenta and 'balance' in cuenta, str(sorted(cuenta)[:6]))
    chequear('y las posiciones son una lista', isinstance(datos.get('posiciones'), list),
             f'{len(datos.get("posiciones") or [])} abiertas')
    estado = datos.get('estado') or {}
    if estado:
        chequear('el plantel dice cuántos agentes vivos',
                 'agents_alive' in estado, f'{estado.get("agents_alive")} de {estado.get("max_agents")}')
else:
    print('  (el gateway del MT5 no está levantado: se prueba solo que no rompa)')
    chequear('sin gateway, el estado viene vacío y no explota',
             workshell.estado_mt5() == {'cuenta': {}, 'posiciones': [], 'estado': {}})

print('\nlos números se escriben como acá')
chequear('miles con punto y decimales con coma',
         workshell.plata(8786.944) == '8.786,94', workshell.plata(8786.944))
chequear('y millones', workshell.plata(1234567.5) == '1.234.567,50', workshell.plata(1234567.5))
chequear('un valor que no es número no rompe', workshell.plata(None) == '—',
         workshell.plata(None))

print('\nlos paneles se dibujan sin romperse')
workshell.dibujar_publicados(workshell.estado_publicaciones())
chequear('el de publicaciones', True, workshell.resumen_publicacion.text)
workshell.dibujar_servicios()
chequear('el de servicios', len(workshell.filas_servicios) == len(workshell.servicios),
         f'{len(workshell.filas_servicios)} filas')
workshell.dibujar_mt5({'cuenta': cuenta, 'posiciones': [], 'estado': {}})
chequear('el del MT5 con datos', True, workshell.mt5_titulo.text[:40])
workshell.dibujar_mt5({})
chequear('y el del MT5 sin datos dice que no contesta',
         'No contesta' in workshell.mt5_titulo.text, workshell.mt5_titulo.text)

import shutil  # noqa: E402

shutil.rmtree(temporal, ignore_errors=True)

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
