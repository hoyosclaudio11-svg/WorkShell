"""Prueba de la libreta: anotar, ordenar, marcar, borrar y el recordatorio.

Lo que importa: que lo anotado sobreviva (se guarda en disco y se vuelve a leer
igual), que «lo de hoy» quede arriba y lo hecho abajo, y que un recordatorio
avise a su hora y **una sola vez** —si avisara en cada repaso, sería inusable—.
Se importa workshell sin levantar el servidor y se redirige el archivo a una
carpeta temporal: la libreta del dueño no se toca.

    python _prueba_libreta.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
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

temporal = Path(tempfile.mkdtemp(prefix='workshell_libreta_'))
workshell.RUTA_PENDIENTES = temporal / 'pendientes.json'

# `ui.notify` necesita un cliente conectado (busca el slot actual para saber a
# quién avisarle) y acá no hay ninguno: se reemplaza por un anotador, que además
# deja ver *qué* avisó, que es lo que se quiere probar.
avisos: list[str] = []
workshell.ui.notify = lambda mensaje, **kw: avisos.append(mensaje)

fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


def reiniciar() -> None:
    """Deja la libreta vacía como si WorksheLL arrancara de nuevo."""
    workshell.pendientes.clear()


print('anotar')
reiniciar()
chequear('un texto vacío no se anota', workshell.agregar_pendiente('   ') is None)
anotado = workshell.agregar_pendiente('llamar al hosting')
chequear('se anota', bool(anotado) and anotado['texto'] == 'llamar al hosting',
         str(anotado))
chequear('y queda para hoy', anotado['dia'] == workshell.hoy(), anotado['dia'])
chequear('sin hora no es recordatorio', anotado['hora'] == '')

con_hora = workshell.agregar_pendiente('17:30 mandar el informe')
chequear('«17:30 …» es un recordatorio', con_hora['hora'] == '17:30', str(con_hora))
chequear('y la hora no queda en el texto', con_hora['texto'] == 'mandar el informe',
         con_hora['texto'])
chequear('«9.05 …» con punto también',
         workshell.agregar_pendiente('9.05 desayunar')['hora'] == '09:05')
chequear('una hora imposible no es hora',
         workshell.agregar_pendiente('99:99 cosas')['hora'] == '')
chequear('«17:30» solo, sin texto, se anota tal cual',
         workshell.agregar_pendiente('17:30')['texto'] == '17:30')

print('\nse guarda y se vuelve a leer igual')
guardado = json.loads(workshell.RUTA_PENDIENTES.read_text(encoding='utf-8'))
chequear('el archivo tiene lo anotado', len(guardado) == len(workshell.pendientes),
         f'{len(guardado)} en disco vs {len(workshell.pendientes)} en memoria')
en_memoria = [p['texto'] for p in workshell.pendientes]
workshell.pendientes[:] = workshell.cargar_pendientes()
chequear('y al releerlo da lo mismo', [p['texto'] for p in workshell.pendientes] == en_memoria,
         str([p['texto'] for p in workshell.pendientes]))

workshell.RUTA_PENDIENTES.write_text('{esto no es json', encoding='utf-8')
chequear('un archivo roto no rompe el arranque', workshell.cargar_pendientes() == [])
workshell.RUTA_PENDIENTES.write_text(json.dumps(workshell.pendientes), encoding='utf-8')

print('\nel orden: lo de hoy arriba, lo hecho abajo')
reiniciar()
hoy = workshell.hoy()
ayer = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))
workshell.pendientes.extend([
    {'texto': 'hecho hoy', 'creado': f'{hoy} 08:00', 'dia': hoy, 'hora': '',
     'hecho': f'{hoy} 09:00', 'avisado': False},
    {'texto': 'viejo pendiente', 'creado': f'{ayer} 08:00', 'dia': ayer, 'hora': '',
     'hecho': '', 'avisado': False},
    {'texto': 'sin hora', 'creado': f'{hoy} 10:00', 'dia': hoy, 'hora': '',
     'hecho': '', 'avisado': False},
    {'texto': 'a las 16', 'creado': f'{hoy} 07:00', 'dia': hoy, 'hora': '16:00',
     'hecho': '', 'avisado': False},
    {'texto': 'a las 09', 'creado': f'{hoy} 06:00', 'dia': hoy, 'hora': '09:00',
     'hecho': '', 'avisado': False},
])
orden = [p['texto'] for _, p in workshell.ordenar_pendientes()]
chequear('primero los de hoy, con hora y por horario', orden[:2] == ['a las 09', 'a las 16'],
         ' → '.join(orden))
chequear('después los de hoy sin hora', orden[2] == 'sin hora', ' → '.join(orden))
chequear('después lo que quedó de antes', orden[3] == 'viejo pendiente', ' → '.join(orden))
chequear('y lo hecho al final', orden[4] == 'hecho hoy', ' → '.join(orden))

print('\nmarcar y borrar')
reiniciar()
workshell.agregar_pendiente('primero')
workshell.agregar_pendiente('segundo')
workshell.quitar_pendiente(0, True)          # marcar hecho
chequear('marcar no borra', len(workshell.pendientes) == 2 and
         bool(workshell.pendientes[0]['hecho']), str(workshell.pendientes[0]))
workshell.quitar_pendiente(0, False)         # desmarcar
chequear('desmarcar lo devuelve a pendiente', workshell.pendientes[0]['hecho'] == '')
chequear('y lo deja listo para volver a avisar',
         workshell.pendientes[0]['avisado'] is False)
workshell.quitar_pendiente(1, None)          # borrar
chequear('borrar lo saca', [p['texto'] for p in workshell.pendientes] == ['primero'],
         str([p['texto'] for p in workshell.pendientes]))
workshell.quitar_pendiente(9, True)
chequear('un índice que no existe no rompe nada', len(workshell.pendientes) == 1)

print('\nel recordatorio avisa a su hora, una sola vez')
reiniciar()
ahora = time.strftime('%H:%M')
temprano = time.strftime('%H:%M', time.localtime(time.time() - 3600))
tarde = time.strftime('%H:%M', time.localtime(time.time() + 3600))
workshell.agregar_pendiente(f'{temprano} ya pasó')
workshell.agregar_pendiente(f'{tarde} todavía no')
workshell.agregar_pendiente(f'{ahora} justo ahora')
avisos.clear()
asyncio.run(workshell.avisar_recordatorios())
avisados = [p['texto'] for p in workshell.pendientes if p['avisado']]
chequear('avisa el que ya llegó', 'ya pasó' in avisados, str(avisados))
chequear('y el de esta hora', 'justo ahora' in avisados, str(avisados))
chequear('el que todavía no llegó no avisa', 'todavía no' not in avisados, str(avisados))
chequear('el aviso dice la hora y el texto',
         any('⏰' in a and 'ya pasó' in a for a in avisos), str(avisos))
chequear('y avisa una vez por recordatorio', len(avisos) == 2, str(avisos))
asyncio.run(workshell.avisar_recordatorios())
chequear('y no lo repite en el repaso siguiente',
         len([p for p in workshell.pendientes if p['avisado']]) == len(avisados),
         str([p['texto'] for p in workshell.pendientes if p['avisado']]))

print('\nlo que quedó de antes no avisa hoy')
reiniciar()
workshell.pendientes.append({'texto': 'de ayer', 'creado': f'{ayer} 08:00', 'dia': ayer,
                             'hora': temprano, 'hecho': '', 'avisado': False})
asyncio.run(workshell.avisar_recordatorios())
chequear('un recordatorio de otro día no suena', not workshell.pendientes[0]['avisado'])

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
