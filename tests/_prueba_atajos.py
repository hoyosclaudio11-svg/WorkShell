"""Prueba de los atajos globales: registrar, apretar de verdad, y que llegue.

`RegisterHotKey` no avisa cuando otra app ya tiene la combinación: devuelve 0 y
seguir. Así que la única prueba que dice algo es apretar la tecla —con
`keybd_event`, el mismo camino que el teclado— y ver si sale por la cola. La
otra mitad es el caso feo: una combinación tomada tiene que aparecer en la lista
de fallidos, porque si no la tecla no hace nada y nadie sabe por qué.

Usa Ctrl+Alt+J y Ctrl+Alt+K, que WorksheLL no toca (él usa 1..9, 0 y B): si
WorksheLL está corriendo, esta prueba no le pisa ninguna tecla.

    python _prueba_atajos.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctypes
import sys
import time

import win32api
import win32con

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


def apretar(vk: int, espera: float = 0.4) -> None:
    """Ctrl+Alt+<vk>, como si lo hubiera apretado el dueño."""
    for tecla in (win32con.VK_CONTROL, win32con.VK_MENU, vk):
        win32api.keybd_event(tecla, 0, 0, 0)
    time.sleep(0.05)
    for tecla in (vk, win32con.VK_MENU, win32con.VK_CONTROL):
        win32api.keybd_event(tecla, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(espera)  # que el WM_HOTKEY llegue y el hilo lo deje en la cola


print('cómo se leen las combinaciones')
casos = {
    'Ctrl+Alt+1': (ventanas.MOD_CONTROL | ventanas.MOD_ALT, 0x31),
    'ctrl+shift+B': (ventanas.MOD_CONTROL | ventanas.MOD_SHIFT, 0x42),
    'Ctrl+Alt+0': (ventanas.MOD_CONTROL | ventanas.MOD_ALT, 0x30),
}
for texto, esperado in casos.items():
    chequear(f'«{texto}»', ventanas._combinacion(texto) == esperado,
             str(ventanas._combinacion(texto)))
for texto in ('F9', 'Ctrl', 'Ctrl+Alt', '', 'Ctrl+Alt+F9'):
    chequear(f'«{texto or "(vacío)"}» no se acepta', ventanas._combinacion(texto) is None,
             str(ventanas._combinacion(texto)))

print('\nuna combinación que ya tiene otra app se reporta, no se ignora')
# Se toma Ctrl+Alt+J desde este mismo proceso, que es lo que hace cualquier otra
# app: el registro de ventanas tiene que darse cuenta y decirlo.
user32 = ctypes.windll.user32
tomada = user32.RegisterHotKey(None, 9001, ventanas.MOD_CONTROL | ventanas.MOD_ALT, 0x4A)
chequear('la prueba pudo tomar Ctrl+Alt+J para sí', bool(tomada))

cola, fallidos = ventanas.atajos_globales({1: 'Ctrl+Alt+J', 2: 'Ctrl+Alt+K'})
chequear('la tomada queda en la lista de fallidos', fallidos == ['Ctrl+Alt+J'], str(fallidos))
chequear('la libre no', 'Ctrl+Alt+K' not in fallidos, str(fallidos))

print('\napretar la tecla de verdad')
apretar(0x4B)  # Ctrl+Alt+K
recibidos = []
while not cola.empty():
    recibidos.append(cola.get())
chequear('la tecla llegó con su identificador', recibidos == [2], str(recibidos))

apretar(0x4A)  # Ctrl+Alt+J: la tiene la prueba, no ventanas: no puede llegar
sobrantes = []
while not cola.empty():
    sobrantes.append(cola.get())
chequear('la que no se pudo registrar no avisa nada', sobrantes == [], str(sobrantes))

print('\nregistrar dos veces no duplica nada')
# WorksheLL ejecuta su script más de una vez en el mismo proceso: sin esto
# quedaría un hilo y un juego de atajos por cada carga del panel.
otra_cola, otros_fallidos = ventanas.atajos_globales({7: 'Ctrl+Alt+L'})
chequear('la segunda llamada devuelve la misma cola', otra_cola is cola)
chequear('y los fallidos que ya sabía', otros_fallidos == fallidos, str(otros_fallidos))
# La segunda llamada traía Ctrl+Alt+L: si lo hubiera registrado, apretarlo
# dejaría un 7 en la cola. Como no lo registró, no puede llegar nada.
apretar(0x4C, espera=0.3)
sobrantes = []
while not cola.empty():
    sobrantes.append(cola.get())
chequear('la segunda llamada no registró nada nuevo', sobrantes == [], str(sobrantes))

user32.UnregisterHotKey(None, 9001)

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
