"""¿Se puede volver translúcida la ventana del panel (un Chrome en modo app)?

La página no puede: una ventana de Chrome no se vuelve transparente desde
adentro, no hay API para eso y el fondo del escritorio no se ve. Lo que sí se
puede es pedírselo a Windows: `WS_EX_LAYERED` + `SetLayeredWindowAttributes` con
`LWA_ALPHA` deja la ventana entera con la opacidad que uno quiera.

La duda es si Chrome lo respeta (dibuja con aceleración por hardware, y hay
ventanas que ignoran la capa). Así que se prueba de verdad: un Chrome de
descarte —perfil temporal propio, título único, un verde saturado de fondo—, se
le pone opacidad y **se mira la pantalla**. Que la API conteste que sí no alcanza:
lo que decide es si el color cambió.

    python _prueba_opacidad.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import win32con
import win32gui
from PIL import ImageGrab

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

TITULO = 'Prueba de opacidad WorksheLL'
VERDE = (0, 192, 0)
fallos = []

CHROME = (
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
)


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


def color_de(rect: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """El color del medio de ese rectángulo, leído de la pantalla."""
    x, y, ancho, alto = rect
    foto = ImageGrab.grab(bbox=(x, y, x + ancho, y + alto))
    return foto.convert('RGB').getpixel((ancho // 2, alto // 2))


temporal = Path(tempfile.mkdtemp(prefix='workshell_opacidad_'))
(temporal / 'prueba.html').write_text(
    # Un verde saturado: si la ventana se mezcla con lo de atrás, se nota.
    f'<html><head><title>{TITULO}</title></head>'
    '<body style="margin:0;background:#00C000;width:100vw;height:100vh"></body></html>',
    encoding='utf-8')

navegador = next((ruta for ruta in CHROME if Path(ruta).exists()), CHROME[0])
# Perfil propio: un Chrome aparte, que no toca la sesión del dueño ni reusa una
# ventana ya abierta (con la sesión de siempre, Chrome ignora tamaño y posición).
proceso = subprocess.Popen([
    navegador, f'--user-data-dir={temporal / "perfil"}',
    f'--app=file:///{(temporal / "prueba.html").as_posix()}',
    '--window-size=700,500', '--window-position=200,200',
    '--no-first-run', '--no-default-browser-check',
])

ventana = None
for _ in range(80):  # Chrome tarda unos segundos en dibujar su ventana
    ventana = ventanas.buscar(titulo=TITULO)
    if ventana:
        break
    time.sleep(0.5)

if ventana is None:
    print('el Chrome de prueba no abrió')
    proceso.terminate()
    sys.exit(1)

try:
    print(f'ventana: {ventana["proceso"]} · {ventana["titulo"][:44]} · {ventana["rect"]}')
    win32gui.ShowWindow(ventana['hwnd'], win32con.SW_RESTORE)
    # Y al frente: la captura de pantalla lee lo que está arriba, no esta
    # ventana. Sin esto se mide el color de lo que hubiera encima —la primera
    # corrida leyó un gris verdoso del fondo— y la prueba no dice nada.
    ok, motivo = ventanas.traer_al_frente(ventana)
    # `SetForegroundWindow` se ignora si el dueño está usando la máquina (Windows
    # no le da el primer plano a un proceso que no lo tiene), así que esto no
    # puede ser una falla: sin la ventana adelante no se puede medir el color
    # —la captura lee lo que está arriba— y lo honesto es decirlo y saltear esa
    # parte. Lo que sí se mide siempre es la capa y el alfa, que no dependen de
    # que se vea.
    al_frente = win32gui.GetForegroundWindow() == ventana['hwnd']
    medir_color = al_frente
    if not medir_color:
        print(f'  (no pude traerla al frente —{motivo or "la máquina está en uso"}—:')
        print('   se prueban la capa y el alfa, no el color en pantalla)')
    time.sleep(1.5)

    print('\nantes: opaca')
    if medir_color:
        chequear('se ve el verde puro de la página', color_de(ventana['rect']) == VERDE,
                 str(color_de(ventana['rect'])))

    print('\ncon opacidad al 65%')
    # El color de lo que hay detrás, leído al lado de la ventana y a la misma
    # altura: con eso se puede calcular la mezcla que la transparencia debería
    # dar, en vez de conformarse con que el color haya cambiado.
    x, y, _, _ = ventana['rect']
    fondo = color_de((x - 70, y + 200, 20, 20)) if medir_color else None
    alfa = 165 / 255
    esperado = (tuple(round(alfa * v + (1 - alfa) * f) for v, f in zip(VERDE, fondo))
                if fondo else None)

    ok, motivo = ventanas.opacidad(ventana, 165)
    chequear('la API dice que sí', ok, motivo)
    estilo = win32gui.GetWindowLong(ventana['hwnd'], win32con.GWL_EXSTYLE)
    chequear('quedó marcada como layered', bool(estilo & win32con.WS_EX_LAYERED),
             hex(estilo))
    # Esto no depende de verla: es lo que Windows dice que hizo.
    _, alfa_puesto, _ = win32gui.GetLayeredWindowAttributes(ventana['hwnd'])
    chequear('y el alfa que dice Windows es el pedido', alfa_puesto == 165, str(alfa_puesto))
    time.sleep(1.0)  # que Chrome redibuje con la capa puesta
    if medir_color:
        mezclado = color_de(ventana['rect'])
        chequear('el color cambió: se ve lo de atrás', mezclado != VERDE, str(mezclado))
        # La prueba de fondo: no que se vea distinto, sino que se vea la mezcla
        # que dice el alfa. Es lo que distingue «transparencia del 65%» de «otro
        # color».
        desvio = max(abs(m - e) for m, e in zip(mezclado, esperado))
        chequear('y es exactamente la mezcla del 65% con el fondo', desvio <= 15,
                 f'{mezclado} vs {esperado} calculado (fondo {fondo}, desvío {desvio})')

    print('\nvolver a opaca')
    ok, motivo = ventanas.opacidad(ventana, 255)
    chequear('la API dice que sí', ok, motivo)
    chequear('y la capa se saca del todo',
             not win32gui.GetWindowLong(ventana['hwnd'], win32con.GWL_EXSTYLE)
             & win32con.WS_EX_LAYERED)
    time.sleep(1.0)
    if medir_color:
        chequear('vuelve el verde puro', color_de(ventana['rect']) == VERDE,
                 str(color_de(ventana['rect'])))
finally:
    try:
        win32gui.PostMessage(ventana['hwnd'], win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass
    time.sleep(2.0)
    if proceso.poll() is None:
        proceso.terminate()
    shutil.rmtree(temporal, ignore_errors=True)

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
