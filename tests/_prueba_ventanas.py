"""Pruebas del motor de composicion de ventanas y del estado de las zonas.

No toca nada de lo que el dueño tenga abierto: abre su propia ventana (un Tk con
titulo unico), la mueve, la minimiza y la cierra al final. Se corre con:

    python _prueba_ventanas.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import sys
import time
from pathlib import Path

import win32con
import win32gui

import ventanas

# La consola de esta maquina es cp1252 y se cae con las flechas y los acentos.
# El conejillo corre con pythonw, que no tiene consola: ahi stdout es None.
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Dos conejillos: uno normal y uno que no se deja achicar (como Freebuff, que
# tiene un tamaño mínimo propio y rechaza cualquier pedido más chico).
TITULO = 'CONEJILLO-WORKSHELL'
TITULO_MINIMO = 'CONEJILLO-MINIMO-WORKSHELL'

# Modo conejillo: el mismo archivo, lanzado aparte, abre la ventana de prueba y
# se queda a vivir hasta que lo maten.
if '--conejillo' in sys.argv or '--conejillo-minimo' in sys.argv:
    import tkinter as tk

    raiz = tk.Tk()
    if '--conejillo-minimo' in sys.argv:
        raiz.title(TITULO_MINIMO)
        raiz.geometry('900x600+700+100')
        raiz.minsize(700, 480)  # el minimo que Windows va a hacer respetar
    else:
        raiz.title(TITULO)
        raiz.geometry('500x300+120+120')
    raiz.mainloop()
    sys.exit()

fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


def esperar_ventana(titulo: str = TITULO, timeout: float = 20.0):
    """Espera a que un conejillo dibuje su ventana."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        encontrada = ventanas.buscar('', titulo)
        if encontrada is not None:
            return encontrada
        time.sleep(0.3)
    return None


def lanzar_conejillo(modo: str = '--conejillo') -> subprocess.Popen:
    # pythonw: sin consola propia, asi el unico proceso con ventana es el Tk.
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    orden = [str(pythonw if pythonw.exists() else sys.executable),
             str(Path(__file__).resolve()), modo]
    return subprocess.Popen(orden)


proceso = lanzar_conejillo()
proceso_minimo = lanzar_conejillo('--conejillo-minimo')
error = None
try:
    print('buscando la ventana del conejillo…')
    v = esperar_ventana()
    chequear('la encuentro por titulo', v is not None)
    if v is None:
        raise SystemExit('sin conejillo no hay nada que probar')

    print('\nestado de las zonas')
    zona_ok = {'etiqueta': 'conejillo', 'proceso': v['proceso'], 'titulo': v['titulo']}
    zona_fantasma = {'etiqueta': 'fantasma', 'proceso': 'noexiste.exe', 'titulo': 'nada'}
    zona_vacia = {'etiqueta': '', 'proceso': '', 'exe': '', 'lnk': ''}
    estados = ventanas.estado_zonas([zona_ok, zona_fantasma, zona_vacia])
    chequear('abierta', estados[0] == 'abierta', str(estados))
    chequear('cerrada', estados[1] == 'cerrada', str(estados))
    chequear('sin asignar', estados[2] == 'sin-asignar', str(estados))

    print('\nuna sola pasada para todas las zonas')
    encontradas = ventanas.ubicaciones([zona_ok, zona_fantasma, zona_vacia])
    chequear('devuelve la ventana de la primera', encontradas[0] is not None)
    chequear('y None en las otras dos', encontradas[1] is None and encontradas[2] is None)

    print('\nmover y releer la posicion')
    x0, y0, ancho_pantalla, alto_pantalla = ventanas.area_trabajo()
    destino = (x0 + 100, y0 + 80, 600, 400)
    ok, motivo, logrado = ventanas.mover(v, *destino)
    chequear('mover', ok, motivo)
    chequear('devuelve el rectangulo logrado', logrado is not None, str(logrado))
    movida = ventanas.buscar('', TITULO)
    porcentajes = ventanas.geometria_en_porcentaje(movida['rect'])
    vuelta = ventanas.a_pixeles(porcentajes, x0, y0, ancho_pantalla, alto_pantalla)
    chequear('porcentajes → pixeles vuelve al mismo lugar',
             all(abs(a - b) <= 8 for a, b in zip(vuelta, destino)),
             f'{vuelta} vs {destino}')

    print('\nminimizada')
    win32gui.ShowWindow(movida['hwnd'], win32con.SW_MINIMIZE)
    time.sleep(0.8)
    dormida = ventanas.buscar('', TITULO)
    chequear('el estado la ve minimizada',
             ventanas.estado_zonas([zona_ok])[0] == 'minimizada')
    # Esta es la razon por la que ni Releer ni Sincronizar leen una minimizada:
    # su rectangulo no es su posicion, es la marca de "estoy minimizada".
    chequear('su rectangulo es basura, no una posicion',
             dormida['rect'][0] <= -30000 and dormida['rect'][1] <= -30000,
             str(dormida['rect']))

    print('\ntraer al frente')
    ok, motivo = ventanas.traer_al_frente(dormida)
    chequear('queda adelante de verdad',
             ok and win32gui.GetForegroundWindow() == dormida['hwnd'],
             motivo or f'adelante es {win32gui.GetForegroundWindow()}')

    # El caso Freebuff: una app con tamaño minimo propio no se deja achicar. La
    # posicion si se respeta, asi que no es un fallo: es un dato, y la zona se
    # ajusta a lo que la app permite en vez de quedar mintiendo en el mapa.
    print('\nventana que no se deja achicar')
    minimo = esperar_ventana(TITULO_MINIMO)
    chequear('el conejillo con mínimo abrió', minimo is not None)
    if minimo is not None:
        zona_chica = {
            'etiqueta': 'conejillo-minimo', 'proceso': minimo['proceso'],
            'titulo': minimo['titulo'], 'x': 50.0, 'y': 20.0, 'ancho': 10.0, 'alto': 10.0,
        }
        resultado = ventanas.aplicar([zona_chica])[0]
        chequear('la da por acomodada, no por fallada', resultado['ok'], resultado['motivo'])
        chequear('dice que la app no acepta ese tamaño',
                 'no acepta' in resultado['motivo'], resultado['motivo'])
        chequear('y no culpa a una ventana elevada',
                 'elevada' not in resultado['motivo'], resultado['motivo'])
        chequear('ajusta la zona a lo que la app permite',
                 zona_chica['ancho'] > 10.0, f"quedó en {zona_chica['ancho']}% de ancho")
        segunda = ventanas.aplicar([zona_chica])[0]
        chequear('el segundo Aplicar sale limpio',
                 segunda['ok'] and not segunda['motivo'], segunda['motivo'] or 'sin motivo')

    print('\nreparto en grilla')
    chequear('2 ventanas en 16:9 van lado a lado', ventanas.grilla(2, 16 / 9) == (1, 2),
             str(ventanas.grilla(2, 16 / 9)))
    chequear('4 van en 2x2', ventanas.grilla(4, 16 / 9) == (2, 2),
             str(ventanas.grilla(4, 16 / 9)))
    chequear('6 van en 2 filas de 3', ventanas.grilla(6, 16 / 9) == (2, 3),
             str(ventanas.grilla(6, 16 / 9)))
    cuatro = ventanas.reparto(4, 16 / 9)
    chequear('4 celdas cubren la pantalla justo',
             cuatro == [(0.0, 0.0, 50.0, 50.0), (50.0, 0.0, 50.0, 50.0),
                        (0.0, 50.0, 50.0, 50.0), (50.0, 50.0, 50.0, 50.0)], str(cuatro))
    cinco = ventanas.reparto(5, 16 / 9)
    # La última fila se estira a lo ancho para no dejar huecos (regla del 19 sep
    # 2026, la que sostiene `_prueba_reparto.py`): con 5, la segunda fila son dos
    # celdas de medio ancho, no dos de un tercio pegadas a la izquierda.
    chequear('5 llena la caja: la última fila se estira',
             cinco[3] == (0.0, 50.0, 50.0, 50.0) and cinco[4] == (50.0, 50.0, 50.0, 50.0)
             and len(cinco) == 5, str(cinco[-2:]))

    print('\nfiltros para acomodar')
    win32gui.ShowWindow(ventanas.buscar('', TITULO)['hwnd'], win32con.SW_MINIMIZE)
    time.sleep(0.8)
    acomodables = ventanas.para_acomodar(TITULO_MINIMO)
    titulos = [v['titulo'] for v in acomodables]
    chequear('deja afuera el escritorio y el shell',
             all(v['proceso'].lower() not in ventanas.SHELL for v in acomodables))
    chequear('deja afuera las minimizadas',
             all(not v['minimizada'] for v in acomodables) and TITULO not in titulos,
             str(titulos[:4]))
    con_dormidas = ventanas.para_acomodar(TITULO_MINIMO, incluir_minimizadas=True)
    chequear('pero las incluye si se las pide',
             any(v['minimizada'] for v in con_dormidas)
             and len(con_dormidas) > len(acomodables),
             f'{len(acomodables)} sin dormidas · {len(con_dormidas)} con')
    chequear('la ventana del panel queda afuera: no se acomoda a sí misma',
             all(v['titulo'] != TITULO_MINIMO for v in acomodables), str(titulos[-3:]))
    chequear('y la reconoce solo por título exacto, no por substring',
             any(v['titulo'] == TITULO_MINIMO
                 for v in ventanas.para_acomodar('CONEJILLO-MINIMO')),
             'con un substring no debería descartar ninguna ventana')

    print('\nla lista de «no acomodar»')
    # Los dos conejillos corren bajo pythonw.exe: se cierra el que ya no se usa
    # para que quede una sola ventana de ese proceso (el afloje por proceso
    # único necesita que sea una) y además sin minimizar, así lo que se prueba
    # es la regla y no que esté dormida.
    proceso.terminate()
    proceso.wait(timeout=10)
    time.sleep(0.8)
    de_pantalla = ventanas.buscar('', TITULO_MINIMO)
    chequear('queda un solo conejillo, y despierto',
             de_pantalla is not None and not de_pantalla['minimizada'])
    abiertas_ahora = ventanas.ventanas_abiertas()
    regla = {'proceso': de_pantalla['proceso'], 'titulo': de_pantalla['titulo'],
             'etiqueta': 'no me acomodes'}
    chequear('una regla por proceso y título la reconoce',
             ventanas.esta_ignorada(de_pantalla, [regla], abiertas_ahora))
    chequear('una regla de otro proceso no la toca',
             not ventanas.esta_ignorada(de_pantalla, [{'proceso': 'otro.exe'}],
                                        abiertas_ahora))
    # El título de una consola cambia solo (pone el comando que corre): la regla
    # tiene que seguir valiendo, que es para lo que está el afloje.
    con_titulo_viejo = dict(regla, titulo='un título que ya no existe')
    solas = [v for v in abiertas_ahora if v['proceso'] == de_pantalla['proceso']]
    chequear('y si la app tiene una sola ventana, alcanza con el proceso',
             len(solas) == 1 and ventanas.esta_ignorada(de_pantalla, [con_titulo_viejo],
                                                        abiertas_ahora),
             f'{len(solas)} ventana(s) de {de_pantalla["proceso"]}')
    chequear('«Acomodar todo» la deja afuera',
             all(v['titulo'] != de_pantalla['titulo']
                 for v in ventanas.para_acomodar('', False, [regla])),
             str([v['titulo'][:20] for v in ventanas.para_acomodar('', False, [regla])]))

    print('\nel acceso directo por ejecutable')
    # El Escritorio de verdad, preguntado a Windows: `Path('.')` era el Escritorio
    # cuando el proyecto vivía ahí, y desde que se mudó a su carpeta (19 sep 2026)
    # esto no encontraba un solo acceso directo.
    accesos = ventanas.accesos_escritorio(ventanas.carpeta_escritorio())
    sin_navegador = next(
        (a for a in accesos
         if Path(a['exe']).name.lower() not in ('chrome.exe', 'msedge.exe',
                                                'firefox.exe', 'brave.exe')),
        None,
    )
    chequear('hay accesos directos en el Escritorio para probar', sin_navegador is not None,
             f'{len(accesos)} accesos')
    if sin_navegador is not None:
        chequear('encuentra el acceso de una app por su ejecutable',
                 ventanas.acceso_para(Path(sin_navegador['exe']).name, accesos) is sin_navegador,
                 Path(sin_navegador['exe']).name)
    chequear('un proceso sin acceso devuelve None',
             ventanas.acceso_para('noexiste.exe', accesos) is None)
    chequear('un navegador no usa su acceso (abriría una ventana en blanco)',
             ventanas.acceso_para('chrome.exe', [
                 {'nombre': 'Chrome', 'exe': r'C:\x\chrome.exe', 'lnk': 'x',
                  'args': '', 'proceso': 'chrome.exe'}]) is None)

except BaseException as exc:  # incluido SystemExit: el arnes no tapa el error real
    error = exc
finally:
    for criatura in (proceso, proceso_minimo):
        if criatura.poll() is None:  # puede haberlo cerrado una prueba antes
            criatura.terminate()
        criatura.wait(timeout=10)
    print(f'\nconejillos cerrados · {len(fallos)} fallas'
          + (f': {fallos}' if fallos else ''))

if error is not None:
    import traceback
    traceback.print_exception(type(error), error, error.__traceback__)
    sys.exit(2)
sys.exit(1 if fallos else 0)
