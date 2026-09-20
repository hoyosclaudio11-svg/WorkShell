"""Motor de composición de ventanas para WorksheLL.

WorksheLL vive adentro de un navegador y no puede mostrar una ventana nativa
(MT5, Codex, un Chrome) dentro de su propia página: eso lo decide el sistema
operativo, no el navegador. Lo que sí puede es acomodarlas sobre el escritorio
real, que es lo que hace este módulo: encontrar la ventana, lanzarla si hace
falta y dejarla en el rectángulo que le toca.

Las zonas se guardan en porcentajes de la pantalla (0-100) para que la misma
disposición sirva aunque cambie la resolución o el monitor.
"""

from __future__ import annotations

import ctypes
import io
import math
import os
import queue
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

import psutil
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

# WorksheLL no dibuja nada, pero las coordenadas de Win32 mienten si el proceso
# no es consciente del DPI: con escalado al 125% una ventana de 1000 px reales
# se lee como 800. Declararlo una vez al importar deja las cuentas en píxeles
# reales para siempre (hoy la máquina está al 100%, pero eso puede cambiar).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    pass

_DWMWA_CLOAKED = 14
_dwmapi = ctypes.windll.dwmapi
_local = threading.local()


def area_trabajo() -> tuple[int, int, int, int]:
    """Rectángulo útil del monitor primario: la pantalla menos la barra de tareas."""
    monitor = win32api.MonitorFromPoint((0, 0))
    izquierda, arriba, derecha, abajo = win32api.GetMonitorInfo(monitor)['Work']
    return izquierda, arriba, derecha - izquierda, abajo - arriba


def _esta_oculta(hwnd: int) -> bool:
    """Las apps de la Store dejan ventanas 'cloaked': existen pero no se ven."""
    valor = ctypes.c_int(0)
    _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(valor), ctypes.sizeof(valor)
    )
    return valor.value != 0


def _datos_ventana(hwnd: int) -> dict:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        proceso = psutil.Process(pid)
        nombre, exe = proceso.name(), proceso.exe()
    except (psutil.Error, OSError):
        # Procesos de otro usuario o del sistema: no hay permiso para leerlos.
        nombre, exe = '', ''
    izquierda, arriba, derecha, abajo = win32gui.GetWindowRect(hwnd)
    return {
        'hwnd': hwnd,
        'proceso': nombre,
        'exe': exe,
        'titulo': win32gui.GetWindowText(hwnd),
        'rect': (izquierda, arriba, derecha - izquierda, abajo - arriba),
        'minimizada': bool(win32gui.IsIconic(hwnd)),
    }


def ventanas_abiertas() -> list[dict]:
    """Ventanas de aplicaciones de verdad: visibles, con título y sin cloaking.

    Deja afuera las herramientas ocultas y los restos de las apps de la Store,
    que si no ensucian cualquier lista con nombres vacíos.
    """
    encontradas: list[dict] = []

    def visitar(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindowTextLength(hwnd) == 0:
            return
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return
        if _esta_oculta(hwnd):
            return
        encontradas.append(_datos_ventana(hwnd))

    win32gui.EnumWindows(visitar, None)
    return encontradas


def _buscar_en(abiertas: list[dict], proceso: str = '', titulo: str = '') -> dict | None:
    """Primera ventana de la lista que matchee proceso y título (substring).

    Si hay varias, prefiere la que ya está a la vista: mover una ventana
    restaurada es menos sorpresivo que rescatar una minimizada.
    """
    proceso = (proceso or '').lower().removesuffix('.exe')
    titulo = (titulo or '').lower()
    candidatas = [
        v for v in abiertas
        if (not proceso or v['proceso'].lower().removesuffix('.exe') == proceso)
        and (not titulo or titulo in v['titulo'].lower())
    ]
    if not candidatas:
        return None
    return min(candidatas, key=lambda v: v['minimizada'])


def buscar(proceso: str = '', titulo: str = '') -> dict | None:
    """Primera ventana abierta que matchee proceso y título."""
    return _buscar_en(ventanas_abiertas(), proceso, titulo)


def posicion_respetada(logrado: tuple, pedido: tuple, tolerancia: int = 8) -> bool:
    """¿La ventana quedó al menos donde se la pidió, aunque el tamaño no?

    La usa `aplicar` para separar dos cosas que se veían iguales: una ventana
    que no acepta el tamaño (tiene un mínimo propio) y una que no se deja mover
    (elevada). La primera no es un fallo: es un dato que el mapa necesita.
    """
    return (abs(logrado[0] - pedido[0]) <= tolerancia
            and abs(logrado[1] - pedido[1]) <= tolerancia)


def mover(ventana: dict, x: int, y: int, ancho: int, alto: int,
          tolerancia: int = 8, intentos: int = 2) -> tuple[bool, str, tuple | None]:
    """Deja la ventana en ese rectángulo. Devuelve (ok, motivo, rect logrado).

    No alcanza con mirar lo que devuelve MoveWindow: Windows Terminal, entre
    otras, se mueve perfecto y igual devuelve 0. La única respuesta confiable
    es volver a medir la ventana y comparar. El rectángulo logrado se devuelve
    aunque haya fallado, porque dice *cómo* falló.

    El segundo intento cubre a las apps que todavía están acomodando su propia
    ventana cuando les pedimos que se muevan: recién lanzadas, el primer pedido
    lo pisa el propio arranque.
    """
    hwnd = ventana['hwnd']
    if not win32gui.IsWindow(hwnd):
        return False, 'la ventana ya no existe'

    # Una ventana maximizada ignora MoveWindow: se le cambia el rectangulo de
    # reposo pero sigue tapando la pantalla entera, y pareceria que WorksheLL
    # no hizo nada. Hay que restaurarla primero, igual que si estuviera
    # minimizada. Con Chrome pasa siempre: restaura el estado maximizado de la
    # sesion anterior y `--window-position` no tiene efecto.
    if win32gui.IsIconic(hwnd) or win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)  # que Windows termine de restaurarla antes de medirla

    medido = None
    for intento in range(intentos):
        try:
            win32gui.MoveWindow(hwnd, x, y, ancho, alto, True)
        except Exception as exc:  # pywintypes.error trae el código de Win32
            return False, f'Windows rechazó el movimiento ({exc})'

        time.sleep(0.2)
        if win32gui.IsIconic(hwnd):
            return False, 'no pude restaurarla (¿ventana elevada?)', None
        izquierda, arriba, derecha, abajo = win32gui.GetWindowRect(hwnd)
        medido = (izquierda, arriba, derecha - izquierda, abajo - arriba)
        if all(abs(r - p) <= tolerancia for r, p in zip(medido, (x, y, ancho, alto))):
            return True, '', medido
        if intento < intentos - 1:
            time.sleep(0.5)

    # Dos fallos que se veían iguales y no lo son: si la posición se respetó y
    # lo que difiere es el tamaño, la app tiene un mínimo propio (Electron y
    # compañía lo fijan) y no hay nada roto — culpar a "ventana elevada" ahí
    # manda a buscar el problema al lado equivocado.
    pedido = (x, y, ancho, alto)
    if posicion_respetada(medido, pedido, tolerancia):
        motivo = f'la app no acepta {ancho}x{alto} (se queda en {medido[2]}x{medido[3]})'
    else:
        motivo = f'quedó en {medido} en vez de {pedido} (¿ventana elevada?)'
    return False, motivo, medido


def lanzar(exe: str = '', args: str = '', lnk: str = '') -> tuple[bool, str]:
    """Abre una app. Devuelve (ok, motivo).

    Con `lnk` se lo deja al shell, que resuelve accesos directos, .bat y
    registro de una sola vez; con `exe` se lo lanza directo.
    """
    if lnk and Path(lnk).exists():
        try:
            os.startfile(lnk)
            return True, ''
        except OSError as exc:
            return False, f'no pude abrir {Path(lnk).name}: {exc}'
    if not exe:
        return False, 'no sé cómo abrirla'
    if not Path(exe).exists():
        return False, f'no existe {exe}'

    orden = [exe, *str(args).split()] if args else [exe]
    try:
        if exe.lower().endswith(('.bat', '.cmd')):
            # Un .bat sin consola propia se muere con el padre.
            subprocess.Popen(orden, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(orden)
        return True, ''
    except OSError as exc:
        return False, f'no pude lanzar {Path(exe).name}: {exc}'


def esperar_nueva(antes: set, timeout: float = 30.0, intervalo: float = 0.5) -> dict | None:
    """Espera a que aparezca una ventana que no estaba en `antes`.

    Es la forma confiable de reconocer lo que acabamos de abrir: el proceso que
    dibuja la ventana no siempre es el que se lanzó (un .bat de consola lo
    hospeda Windows Terminal, que es otro ejecutable).

    El timeout no es adorno: MT5 tarda varios segundos en dibujar su ventana, y
    sin esperarlo la disposición fallaría justo la primera vez, que es cuando
    más se nota.
    """
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        nuevas = [v for v in ventanas_abiertas() if v['hwnd'] not in antes]
        if nuevas:
            return min(nuevas, key=lambda v: v['minimizada'])
        time.sleep(intervalo)
    return None


def _elegir(abiertas: list[dict], zona: dict) -> tuple[dict | None, str]:
    """La ventana de una zona dentro de una lista ya enumerada.

    Afloja el match si hace falta: primero pide proceso y título, y si no
    aparece pero esa app tiene una sola ventana abierta, la toma igual. Los
    títulos cambian solos (una consola pone el comando que está corriendo), y
    acomodar la única ventana que hay es mejor que no hacer nada. Cuando
    afloja, lo dice, para que no parezca magia.
    """
    proceso = zona.get('proceso', '')
    titulo = zona.get('titulo', '')
    if not proceso and not titulo:
        # Una zona vacía no dice a quién buscar. Sin este corte, los dos filtros
        # vacíos matchean cualquier ventana y la zona se queda con una al azar.
        return None, ''

    ventana = _buscar_en(abiertas, proceso, titulo)
    if ventana is not None or not proceso:
        return ventana, ''

    buscado = proceso.lower().removesuffix('.exe')
    del_proceso = [v for v in abiertas
                   if v['proceso'].lower().removesuffix('.exe') == buscado]
    if len(del_proceso) == 1:
        return del_proceso[0], f'{proceso} cambió de título: usé la única ventana que hay'
    return None, ''


def resolver(zona: dict) -> tuple[dict | None, str]:
    """Busca la ventana de una zona."""
    return _elegir(ventanas_abiertas(), zona)


def ubicaciones(zonas: list[dict]) -> list[dict | None]:
    """La ventana de cada zona (o None), con una sola pasada de EnumWindows.

    Enumerar ventanas no es gratis: hay que abrir cada proceso para leerle el
    nombre. Hacerlo una vez por zona (con `resolver`) multiplica ese costo por
    la cantidad de zonas, y esto se llama cada pocos segundos para pintar el
    estado del mapa.
    """
    return [ventana for _, ventana in estado_y_ventanas(zonas)]


def estado_y_ventanas(zonas: list[dict]) -> list[tuple[str, dict | None]]:
    """El estado de cada zona y su ventana, en una sola pasada.

    Es lo que necesita el mapa: el punto de estado y, si las miniaturas están
    prendidas, la ventana a la que hay que capturarle la cara. Enumerar dos
    veces para eso sería pagar dos veces el barrido.
    """
    abiertas = ventanas_abiertas()
    resultado: list[tuple[str, dict | None]] = []
    for zona in zonas:
        ventana, _ = _elegir(abiertas, zona)
        if not (zona.get('proceso') or zona.get('exe') or zona.get('lnk')):
            resultado.append(('sin-asignar', None))
        elif ventana is None:
            resultado.append(('cerrada', None))
        elif ventana['minimizada']:
            resultado.append(('minimizada', ventana))
        else:
            resultado.append(('abierta', ventana))
    return resultado


def estado_zonas(zonas: list[dict]) -> list[str]:
    """Estado de cada zona: 'abierta', 'minimizada', 'cerrada' o 'sin-asignar'.

    'cerrada' quiere decir que WorksheLL no la encuentra, que es justo lo que
    importa saber: es la que no se va a poder acomodar cuando aprietes Aplicar.
    """
    return [estado for estado, _ in estado_y_ventanas(zonas)]


# --- Miniaturas -------------------------------------------------------------
# WorksheLL dibuja el mapa con rectángulos; esto le da la cara de cada ventana,
# para que la zona muestre la ventana de verdad y no solo su nombre.
#
# PrintWindow es el único camino para una página web: los thumbnails de DWM (que
# sí funcionan con las apps aceleradas) necesitan una ventana Win32 propia donde
# dibujarse, y WorksheLL vive adentro de un navegador. Con PW_RENDERFULLCONTENT
# (2) la captura sale bien aunque la ventana esté tapada o en segundo plano, que
# es justo el caso de uso: el MT5 de atrás se captura entero, con su gráfico.
# Verificado el 18 sep 2026 con MT5, Chrome y Windows Terminal.

ANCHO_MINIATURA = 480   # de sobra: una zona del mapa mide ~300 px de ancho
CALIDAD_JPEG = 72


def capturar(ventana: dict, ancho: int = ANCHO_MINIATURA,
             calidad: int = CALIDAD_JPEG) -> bytes | None:
    """La cara de esa ventana ahora mismo, en JPEG. None si no se pudo.

    Una ventana minimizada no se captura: no tiene nada dibujado —su rect es
    (-32000, -32000, 160, 28)— y lo que devuelva PrintWindow no es la ventana.
    """
    if ventana.get('minimizada'):
        return None
    hwnd = ventana['hwnd']
    _, _, ancho_real, alto_real = ventana['rect']
    if ancho_real <= 0 or alto_real <= 0:
        return None

    dc_ventana = win32gui.GetWindowDC(hwnd)
    try:
        dc_origen = win32ui.CreateDCFromHandle(dc_ventana)
        dc_destino = dc_origen.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(dc_origen, ancho_real, alto_real)
            dc_destino.SelectObject(bitmap)
            # El valor de retorno de PrintWindow miente igual que el de
            # MoveWindow: hay apps que dibujan perfecto y devuelven 0. Se usa
            # como corte barato, nada más.
            if not ctypes.windll.user32.PrintWindow(hwnd, dc_destino.GetSafeHdc(), 2):
                return None
            bits = bitmap.GetBitmapBits(True)
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            dc_destino.DeleteDC()
    finally:
        win32gui.ReleaseDC(hwnd, dc_ventana)

    try:
        imagen = Image.frombuffer('RGBA', (ancho_real, alto_real), bits,
                                  'raw', 'BGRA', 0, 1)
    except ValueError:
        # El DC devolvió otro formato (24 bpp): mejor ninguna miniatura que una
        # imagen corrida.
        return None
    if imagen.width > ancho:
        alto_mini = max(1, round(imagen.height * ancho / imagen.width))
        imagen = imagen.resize((ancho, alto_mini), Image.Resampling.LANCZOS)
    salida = io.BytesIO()
    imagen.convert('RGB').save(salida, 'JPEG', quality=calidad)
    return salida.getvalue()


def capturar_zonas(por_zona: list[dict | None]) -> dict[int, bytes]:
    """Miniatura de cada ventana de la lista, indexada por el número de zona.

    Las que no se pueden capturar simplemente no aparecen en el resultado: el
    mapa se queda con lo último que tenía de esa zona, no con un cuadro negro.
    """
    miniatura: dict[int, bytes] = {}
    for i, ventana in enumerate(por_zona):
        if ventana is None:
            continue
        try:
            jpeg = capturar(ventana)
        except Exception as exc:  # una app rara no puede tumbar el mapa
            print(f'no pude capturar {ventana.get("proceso")}: {exc!r}')
            continue
        if jpeg:
            miniatura[i] = jpeg
    return miniatura


def traer_al_frente(ventana: dict) -> tuple[bool, str]:
    """Levanta esa ventana y la deja adelante. Devuelve (ok, motivo).

    Windows solo deja llamar a `SetForegroundWindow` al proceso que ya tiene el
    primer plano, y WorksheLL corre en Python mientras adelante está Chrome, que
    es otro proceso: el pedido se ignoraría en silencio. El truco es engancharse
    un momento al hilo que sí lo tiene (`AttachThreadInput`), pedir el foco desde
    ahí y soltarse.

    Después no se confía en el valor de retorno: se mide quién quedó adelante,
    igual que con `mover`.
    """
    hwnd = ventana['hwnd']
    if not win32gui.IsWindow(hwnd):
        return False, 'la ventana ya no existe'

    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)

        if win32gui.GetForegroundWindow() != hwnd:
            hilo_actual = win32api.GetCurrentThreadId()
            hilo_adelante, _ = win32process.GetWindowThreadProcessId(
                win32gui.GetForegroundWindow()
            )
            if hilo_adelante and hilo_adelante != hilo_actual:
                win32process.AttachThreadInput(hilo_actual, hilo_adelante, True)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    win32process.AttachThreadInput(hilo_actual, hilo_adelante, False)
            else:
                win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:  # pywintypes.error trae el código de Win32
        return False, f'Windows rechazó el foco ({exc})'

    time.sleep(0.1)  # el cambio de primer plano no es instantáneo
    if win32gui.GetForegroundWindow() == hwnd:
        return True, ''
    return False, 'quedó atrás (¿ventana elevada?)'


def ventana_del_panel(titulo: str) -> dict | None:
    """La ventana del propio WorksheLL, por título exacto.

    Por substring no: el 18 sep 2026 un filtro así se llevó puesta una sesión de
    Claude Code titulada «WorkShell con NiceGUI y psutil» —cualquier ventana que
    *mencione* el proyecto matchea—. Acá el riesgo es más benigno, porque solo
    se la minimiza, pero el criterio es el mismo: título exacto.
    """
    buscado = (titulo or '').strip().lower()
    if not buscado:
        return None
    for ventana in ventanas_abiertas():
        if ventana['titulo'].strip().lower() == buscado:
            return ventana
    return None


def sigue_ahi(ventana: dict) -> bool:
    """¿La ventana existe todavía? Barato: no enumera nada."""
    return bool(win32gui.IsWindow(ventana['hwnd']))


def esta_minimizada_ahora(ventana: dict) -> bool:
    """¿Está minimizada en este momento? Barato, para preguntar seguido.

    Distinto de `estado_zonas`, que enumera todas las ventanas: esto es para
    seguir una sola, la del panel, sin pagar el barrido entero.
    """
    return bool(win32gui.IsIconic(ventana['hwnd']))


def apartar(ventana: dict) -> bool:
    """Manda la ventana al fondo de todo: minimizada. Devuelve si quedó así.

    Es lo contrario de `traer_al_frente` y lo que usa «Sumar ventana» para
    dejar el escritorio a la vista sin cerrar nada.
    """
    if not win32gui.IsWindow(ventana['hwnd']):
        return False
    win32gui.ShowWindow(ventana['hwnd'], win32con.SW_MINIMIZE)
    time.sleep(0.25)  # minimizar no es instantáneo: medir enseguida miente
    return bool(win32gui.IsIconic(ventana['hwnd']))


def a_pixeles(zona: dict, x0: int, y0: int, ancho: int, alto: int) -> tuple[int, int, int, int]:
    """Convierte una zona en porcentajes al rectángulo real de la pantalla.

    El piso de tamaño evita que un arrastre corto deje una ventana de 3 px
    que después no se puede volver a agarrar.
    """
    return (
        x0 + round(zona.get('x', 0) / 100 * ancho),
        y0 + round(zona.get('y', 0) / 100 * alto),
        max(200, round(zona.get('ancho', 50) / 100 * ancho)),
        max(120, round(zona.get('alto', 50) / 100 * alto)),
    )


def geometria_en_porcentaje(rect: tuple[int, int, int, int]) -> dict:
    """Rectángulo real (x, y, ancho, alto) → porcentajes del área de trabajo.

    Es la inversa de `a_pixeles`: lo que se usa para releer dónde quedó una
    ventana y guardarlo como zona.

    El recorte a 0-100 no es decorativo: Chrome restaura posiciones viejas y se
    lo vio abrir en x=-995, o sea más de la mitad fuera de pantalla. Una zona
    dibujada fuera del mapa no se puede ni agarrar con el mouse.
    """
    x, y, ancho, alto = rect
    x0, y0, pantalla_ancho, pantalla_alto = area_trabajo()

    def recortar(valor: float, total: float) -> float:
        return round(min(100.0, max(0.0, valor / total * 100)), 2)

    return {
        'x': recortar(x - x0, pantalla_ancho),
        'y': recortar(y - y0, pantalla_alto),
        'ancho': recortar(ancho, pantalla_ancho),
        'alto': recortar(alto, pantalla_alto),
    }


def aplicar(zonas: list[dict]) -> list[dict]:
    """Deja cada ventana en su zona, lanzando lo que falte.

    Corre en un hilo aparte (lanzar y esperar puede tardar decenas de segundos)
    y devuelve un resultado por zona, exitoso o no, para poder contarlo.
    """
    x0, y0, ancho_pantalla, alto_pantalla = area_trabajo()
    resultados = []

    for zona in zonas:
        etiqueta = zona.get('etiqueta') or zona.get('proceso') or 'zona'
        ventana, nota = resolver(zona)

        if ventana is None and (zona.get('exe') or zona.get('lnk')):
            antes = {v['hwnd'] for v in ventanas_abiertas()}
            ok, motivo = lanzar(zona.get('exe', ''), zona.get('args', ''), zona.get('lnk', ''))
            if not ok:
                resultados.append({'etiqueta': etiqueta, 'ok': False, 'motivo': motivo})
                continue
            ventana = esperar_nueva(antes)
            if ventana is not None:
                # Aprender de lo que acabamos de abrir: la proxima vez la
                # encuentra directo, sin lanzar nada.
                zona['proceso'] = ventana['proceso']
                zona['titulo'] = ventana['titulo']
                nota = f'la abrí y la anoté como {ventana["proceso"]}'

        if ventana is None:
            motivo = ('no encontré la ventana' if (zona.get('exe') or zona.get('lnk'))
                      else 'no encontré la ventana y no sé cómo abrirla')
            resultados.append({'etiqueta': etiqueta, 'ok': False, 'motivo': motivo})
            continue

        destino = a_pixeles(zona, x0, y0, ancho_pantalla, alto_pantalla)
        ok, motivo, logrado = mover(ventana, *destino)

        if not ok and logrado is not None and posicion_respetada(logrado, destino):
            # La app no entra en la zona porque tiene un tamaño mínimo. La
            # posición sí se respetó, así que la zona se ajusta a lo que la app
            # permite: el mapa deja de mentir y el próximo Aplicar sale limpio.
            zona.update(geometria_en_porcentaje(logrado))
            resultados.append({
                'etiqueta': etiqueta, 'ok': True,
                'motivo': f'{motivo}: ajusté la zona a {logrado[2]}x{logrado[3]}',
            })
            continue

        resultados.append({'etiqueta': etiqueta, 'ok': ok, 'motivo': motivo or nota})

    return resultados


# El shell y sus parientes: tienen ventana con título, pero no son "apps
# abiertas" — repartir el escritorio o el buscador por la pantalla no quiere
# decir nada.
SHELL = {
    'explorer.exe', 'progman.exe', 'searchhost.exe', 'textinputhost.exe',
    'shellexperiencehost.exe', 'startmenuexperiencehost.exe',
}

# El acceso directo de un navegador abre una ventana en blanco, no la página
# que estaba abierta: para esas ventanas no sirve como "cómo abrirla".
SIN_ACCESO = {'chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe'}


def esta_ignorada(ventana: dict, ignoradas: list[dict], abiertas: list[dict]) -> bool:
    """¿Esa ventana está en la lista de «no me la acomodes»?

    Se compara por proceso, y el título solo si hace falta, aflojando igual que
    `_elegir`: si la app tiene una sola ventana abierta, alcanza con el
    proceso. Los títulos cambian solos —una consola pone el comando que corre,
    un navegador la página— así que exigirlos haría que la regla dejara de
    valer sin que nadie se entere.
    """
    buscado = (ventana.get('proceso') or '').lower().removesuffix('.exe')
    if not buscado:
        return False

    for regla in ignoradas:
        proceso = str(regla.get('proceso', '')).lower().removesuffix('.exe')
        if proceso != buscado:
            continue
        titulo = str(regla.get('titulo', '')).strip().lower()
        if not titulo or titulo in (ventana.get('titulo') or '').lower():
            return True
        del_proceso = [v for v in abiertas
                       if v['proceso'].lower().removesuffix('.exe') == buscado]
        if len(del_proceso) == 1:
            return True  # cambió el título: la intención de no acomodarla sigue
    return False


def para_acomodar(titulo_del_panel: str = '', incluir_minimizadas: bool = False,
                  ignoradas: list[dict] | None = None) -> list[dict]:
    """Las ventanas que tiene sentido repartir por la pantalla.

    Deja afuera el shell (escritorio, buscador), que tiene ventana con título
    pero no es una app que uno quiera acomodar.

    Las minimizadas quedan afuera **salvo que se pidan**: acomodarlas las
    restaura, y eso es una decisión del usuario, no algo para hacer de callado.
    En esta máquina pesa: suele haber más minimizadas que a la vista (10 de 14
    el 18 sep 2026), así que sin ellas "acomodar todo" reparte cuatro ventanas
    y parece que hubiera un tope.

    La ventana del propio panel (`titulo_del_panel`) queda afuera, y no es por
    prolijidad: WorksheLL la mueve, pero **Chrome le devuelve su tamaño** (el de
    la sesión anterior, 1700x1000 acá), así que moverla a una celda la deja
    colgando fuera de la pantalla — pasó el 18 sep 2026 y hubo que rescatar la
    ventana a mano. Y su zona, leída de una ventana así de grande, mide 88% x
    93% y se come el mapa entero. Se queda donde el dueño la tenga; si la quiere
    en una celda, «Capturar al frente» la agrega como zona en un clic.

    La comparación del título es exacta a propósito: por substring también
    entraría cualquier terminal cuyo título mencione WorksheLL, que no es lo
    mismo.
    """
    panel = (titulo_del_panel or '').strip().lower()
    ignoradas = ignoradas or []
    todas = ventanas_abiertas()

    apps = []
    for ventana in todas:
        if ventana['proceso'].lower() in SHELL:
            continue
        if ventana['minimizada'] and not incluir_minimizadas:
            continue
        if panel and ventana['titulo'].strip().lower() == panel:
            continue
        if esta_ignorada(ventana, ignoradas, todas):
            continue
        apps.append(ventana)
    return apps


def grilla(cantidad: int, aspecto: float, objetivo: float | None = None) -> tuple[int, int]:
    """Filas y columnas para repartir `cantidad` ventanas sin aplastarlas.

    `aspecto` es el de la caja a llenar y `objetivo` el que se busca para cada
    celda; por defecto son el mismo, que es lo que quiere una grilla de pantalla
    completa: celdas con la forma de la pantalla.

    El objetivo se compara **en escala logarítmica** porque el aspecto es una
    proporción: quedar al doble o a la mitad de lo buscado es el mismo error. Con
    la diferencia común, una celda de 2.67 contra un objetivo de 1.78 (error
    0.89) empataba con una de 0.44 (error 1.34, y en log 1.4 contra 0.41), y en
    el costado de la disposición con foco ganaba la columna aplastada de 365x820
    en vez de dos ventanas apiladas de 730x410.

    En empate gana la de menos filas, que es la más fácil de leer; por eso se
    recorre de menos a más y solo se cambia si mejora de verdad.
    """
    objetivo = aspecto if objetivo is None else objetivo
    mejor = (1, cantidad)
    mejor_costo = None
    for filas in range(1, cantidad + 1):
        columnas = math.ceil(cantidad / filas)
        # Las filas que hacen falta de verdad: 4x2 entra en 5 ventanas con solo
        # 3 filas, y contar la que sobra deja la última franja vacía.
        filas = math.ceil(cantidad / columnas)
        aspecto_celda = aspecto * filas / columnas
        costo = abs(math.log(aspecto_celda / objetivo))
        if mejor_costo is None or costo < mejor_costo - 1e-9:
            mejor, mejor_costo = (filas, columnas), costo
    return mejor


def reparto(cantidad: int, aspecto: float,
            objetivo: float | None = None) -> list[tuple[float, float, float, float]]:
    """Un rectángulo en porcentaje por ventana, en fila y desde arriba a la izquierda.

    La grilla se elige por aspecto de celda (ver `grilla`) y **llena la caja**: si
    la última fila queda incompleta, esas ventanas se reparten el ancho que sobra
    en partes iguales. Dejar el hueco era peor: en la pantalla se ve como un
    agujero y el dueño lo reportó así el 19 sep 2026 («espacios vacíos por todos
    lados»). Con 5 ventanas en 16:9, la última fila queda de dos celdas más anchas
    en vez de una vacía.

    Los bordes se redondean y cada celda se mide como diferencia entre ellos, así
    entre todas suman la caja exacta: sumar anchos redondeados dejaba una franja
    de 0,02% (un par de píxeles) sin dueño.
    """
    if cantidad <= 0:
        return []
    filas, _columnas = grilla(cantidad, aspecto, objetivo)
    # Las ventanas se reparten entre las filas lo más parejo posible: con 7 en 3
    # filas van 3, 2 y 2. Llenar de a `columnas` daría 3, 3 y 1, y esa última
    # ventana sola se estira a todo el ancho (1920x360) mientras las otras seis
    # quedan de 640 px: justo la diferencia de tamaños que el dueño marcó como
    # «algunas muy chicas, otras muy grandes».
    base, sobrantes = divmod(cantidad, filas)
    por_fila = [base + (1 if fila < sobrantes else 0) for fila in range(filas)]

    borde_y = [round(100 * i / filas, 2) for i in range(filas + 1)]
    rectangulos = []
    for fila, en_fila in enumerate(por_fila):
        borde_x = [round(100 * i / en_fila, 2) for i in range(en_fila + 1)]
        for posicion in range(en_fila):
            rectangulos.append((
                borde_x[posicion], borde_y[fila],
                round(borde_x[posicion + 1] - borde_x[posicion], 2),
                round(borde_y[fila + 1] - borde_y[fila], 2),
            ))
    return rectangulos


def foco(cantidad: int, aspecto: float, indice: int = 0, grande: float = 62.0,
         objetivo: float | None = None) -> list[tuple[float, float, float, float]]:
    """Un rectángulo por ventana, con una grande y el resto acomodado al costado.

    Es la disposición provisoria de «toqué una tarjeta y esa sube»: la elegida se
    queda con la franja grande y las demás se reparten lo que sobra. **No se
    esconden**: siguen a la vista, que es todo el punto del panel. Cuando el
    dueño termina, `reparto` vuelve a repartir parejo.

    El resto va en **grilla**, no en una sola columna: con tres ventanas al lado,
    una columna las deja de 270 px de alto, aplastadas contra el costado.

    `objetivo` es la forma que se busca para las celdas del costado (por defecto,
    la del aspecto de la caja). Conviene pasarle una forma de ventana —16:9— y no
    la de la franja, que en la barra es muy ancha: con dos ventanas al costado,
    buscar la forma de la franja daba dos columnas de 365x820; buscando 16:9 dan
    dos apiladas de 730x410.
    """
    if cantidad <= 0:
        return []
    # Por defecto se busca forma de ventana (16:9), no la de la caja: en una
    # franja ancha, buscar la forma de la franja deja las celdas del costado como
    # columnas de 365x820.
    objetivo = min(aspecto, 1.78) if objetivo is None else objetivo
    indice = max(0, min(indice, cantidad - 1))
    rectangulos = [(0.0, 0.0, 0.0, 0.0)] * cantidad
    rectangulos[indice] = (0.0, 0.0, grande, 100.0)

    otros = [i for i in range(cantidad) if i != indice]
    if otros:
        ancho_lado = round(100.0 - grande, 2)
        proporcion = ancho_lado / 100.0
        # La franja del costado es alta y angosta: su grilla se calcula con el
        # aspecto que tiene de verdad (si no, elegiría una sola columna), pero
        # **buscando celdas con el aspecto de la pantalla**, que es lo que hace
        # usable una ventana. Sin ese objetivo, dos ventanas al costado quedaban
        # de 365x820 en vez de 730x410.
        for cual, (x, y, ancho, alto) in zip(
                otros, reparto(len(otros), aspecto * proporcion, objetivo)):
            rectangulos[cual] = (round(grande + x * proporcion, 2), y,
                                 round(ancho * proporcion, 2), alto)
    return rectangulos


def acceso_para(proceso: str, accesos: list[dict]) -> dict | None:
    """El acceso directo del Escritorio que abre esa app, si hay uno.

    Se compara por nombre de ejecutable, no por título: el .lnk de una app
    apunta al mismo .exe que corre, mientras que el título cambia solo (una
    consola pone el comando, un navegador la página). Los navegadores quedan
    afuera, ver `SIN_ACCESO`.
    """
    nombre = (proceso or '').strip().lower()
    if not nombre or nombre in SIN_ACCESO:
        return None
    for acceso in accesos:
        if Path(acceso.get('exe', '')).name.lower() == nombre:
            return acceso
    return None


def _shell_de_windows():
    """Shell de COM para leer accesos directos, cacheado por hilo.

    COM se inicializa por hilo y WorksheLL atiende desde varios, así que el
    objeto vive en un `threading.local`. A propósito no se llama a
    CoUninitialize: cerrar COM con objetos todavía vivos hace que pywin32
    escupa "releasing IUnknown" en cada consulta, y el proceso es de vida larga.
    """
    shell = getattr(_local, 'shell', None)
    if shell is not None:
        return shell

    try:
        import pythoncom
        from win32com.client import Dispatch
    except ImportError:
        return None

    try:
        pythoncom.CoInitialize()
        shell = Dispatch('WScript.Shell')
    except Exception:
        return None

    _local.shell = shell
    return shell


def carpeta_escritorio() -> Path:
    """La carpeta del Escritorio de verdad, preguntándole a Windows.

    No alcanza con `Path(__file__).parent`: WorksheLL vivía suelto en el
    Escritorio y encontraba los accesos directos de casualidad. Ahora que tiene
    su propia carpeta, hay que apuntar al Escritorio real — y en esta máquina
    además está redirigido a OneDrive, así que tampoco sirve armar la ruta a
    mano (`~/Desktop` no existe).
    """
    shell = _shell_de_windows()
    if shell is not None:
        try:
            carpeta = shell.SpecialFolders('Desktop')
            if carpeta and Path(carpeta).is_dir():
                return Path(carpeta)
        except Exception:
            pass
    return Path.home() / 'Desktop'


def al_frente() -> dict | None:
    """La ventana que el usuario tiene adelante ahora mismo."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd or win32gui.GetWindowTextLength(hwnd) == 0:
        return None
    return _datos_ventana(hwnd)


def accesos_escritorio(carpeta: Path) -> list[dict]:
    """Resuelve los .lnk de una carpeta para poder elegir una app sin tipear rutas."""
    shell = _shell_de_windows()
    if shell is None:
        return []

    accesos = []
    for lnk in sorted(Path(carpeta).glob('*.lnk')):
        try:
            acceso = shell.CreateShortcut(str(lnk))
            destino = acceso.TargetPath
            if not destino:
                continue
            accesos.append({
                'nombre': lnk.stem,
                'lnk': str(lnk),
                'exe': destino,
                'args': acceso.Arguments,
                'proceso': Path(destino).name,
            })
        except Exception:
            continue  # un .lnk roto no puede tumbar la lista entera
    return accesos


# --- Atajos globales de teclado ----------------------------------------------
# WorksheLL puede estar en una franja al pie, o escondido en la barra de tareas
# con las ventanas repartidas por toda la pantalla: no hay dónde hacer clic para
# cambiar de ventana sin traer el panel primero. Un atajo global lo saltea, ande
# donde ande el mouse y tenga el foco quien lo tenga.
#
# `RegisterHotKey` con hwnd nulo ata el atajo **al hilo que lo registra**, y el
# WM_HOTKEY llega a la cola de mensajes de ese hilo: si el hilo se muere o no
# bombea, el atajo queda registrado y no avisa nunca —lo peor de los dos mundos—.
# De ahí el hilo propio con su GetMessage en vez de un timer.
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

_NOMBRES = {'ctrl': MOD_CONTROL, 'control': MOD_CONTROL, 'alt': MOD_ALT,
            'shift': MOD_SHIFT, 'win': MOD_WIN}

# Una sola registración por proceso. WorksheLL ejecuta su script más de una vez
# en el mismo proceso (la página la sirve el handler de 404 de NiceGUI, que la
# arma corriendo el script otra vez), así que sin esto cada carga del panel
# dejaría un hilo y un juego de atajos más, y el segundo registro fallaría
# siempre porque el primero ya los tiene.
_atajos: dict = {'cola': None, 'fallidos': []}


def _combinacion(texto: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+3' → (modificadores, tecla virtual). None si no se entiende.

    La tecla es **una** letra o un número (es lo que necesita WorksheLL: 1..9, 0
    y B). Se exige al menos un modificador: un atajo global sin modificador se
    come la tecla en todo Windows.
    """
    mods, tecla = 0, 0
    for parte in (texto or '').split('+'):
        parte = parte.strip().lower()
        if parte in _NOMBRES:
            mods |= _NOMBRES[parte]
        elif len(parte) == 1 and parte.isalnum():
            tecla = ord(parte.upper())  # 0-9 y A-Z tienen el mismo código
    return (mods, tecla) if mods and tecla else None


def _bombear(combinaciones: dict[int, str], listo: threading.Event) -> None:
    """Registra los atajos y se queda escuchando. Corre en su propio hilo."""
    user32 = ctypes.windll.user32
    fallidos = []
    for ident, texto in combinaciones.items():
        par = _combinacion(texto)
        # MOD_NOREPEAT: sin eso, dejar el dedo apoyado encima dispara decenas de
        # veces, y cada disparo reparte ventanas.
        if par is None or not user32.RegisterHotKey(None, ident, par[0] | MOD_NOREPEAT, par[1]):
            fallidos.append(texto)
    _atajos['fallidos'] = fallidos
    listo.set()

    mensaje = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(mensaje), None, 0, 0) > 0:
        if mensaje.message == WM_HOTKEY:
            _atajos['cola'].put(int(mensaje.wParam))


def atajos_globales(combinaciones: dict[int, str]) -> tuple[queue.Queue, list[str]]:
    """Registra atajos que andan con cualquier ventana adelante.

    `combinaciones` mapea un identificador a la combinación ('Ctrl+Alt+1'); los
    identificadores apretados salen por la cola que devuelve. La otra lista son
    las que no se pudieron registrar —otra app las tiene tomadas, y Windows no lo
    dice de otra forma—. Llamarla dos veces no registra dos veces: la segunda
    devuelve lo mismo que la primera.
    """
    if _atajos['cola'] is None:
        _atajos['cola'] = queue.Queue()
        listo = threading.Event()
        threading.Thread(target=_bombear, args=(dict(combinaciones), listo),
                         daemon=True, name='atajos').start()
        # El registro es inmediato; la espera es por si el hilo no llegara a
        # arrancar, para no quedarse sin cola y sin saber por qué.
        listo.wait(2.0)
    return _atajos['cola'], list(_atajos['fallidos'])


def cursor() -> tuple[int, int]:
    """Dónde está el mouse, en píxeles de pantalla. Barato: no enumera nada."""
    return win32gui.GetCursorPos()


def opacidad(ventana: dict, alfa: int) -> tuple[bool, str]:
    """Deja la ventana translúcida (alfa 0-255). Con 255 vuelve a ser opaca.

    Una página no puede volverse transparente desde adentro —no hay API, y el
    escritorio no se ve—, pero Windows sí sabe hacerlo: `WS_EX_LAYERED` +
    `SetLayeredWindowAttributes` con `LWA_ALPHA` pone la ventana entera con la
    opacidad pedida, y lo de atrás se ve a través. Se usa para la barra al pie,
    que así no tapa lo que tiene abajo.

    Como con `mover`, el valor de retorno no dice la verdad: se vuelve a medir.
    """
    hwnd = ventana['hwnd']
    if not win32gui.IsWindow(hwnd):
        return False, 'la ventana ya no existe'
    alfa = max(0, min(255, int(alfa)))
    try:
        estilo = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if alfa >= 255:
            # Opaca otra vez: se saca la capa, que es como estaba antes.
            if estilo & win32con.WS_EX_LAYERED:
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                       estilo & ~win32con.WS_EX_LAYERED)
                win32gui.RedrawWindow(hwnd, None, None,
                                      win32con.RDW_INVALIDATE | win32con.RDW_FRAME
                                      | win32con.RDW_ALLCHILDREN)
            return True, ''
        if not estilo & win32con.WS_EX_LAYERED:
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                   estilo | win32con.WS_EX_LAYERED)
        win32gui.SetLayeredWindowAttributes(hwnd, 0, alfa, win32con.LWA_ALPHA)
    except Exception as exc:  # pywintypes.error trae el código de Win32
        return False, f'Windows rechazó la capa ({exc})'

    puesto = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if not puesto & win32con.WS_EX_LAYERED:
        return False, 'no quedó como ventana con capa'
    _, medido, _ = win32gui.GetLayeredWindowAttributes(hwnd)
    if medido != alfa:
        return False, f'quedó con alfa {medido} en vez de {alfa}'
    return True, ''


def cerrar(ventana: dict) -> bool:
    """Le pide a la ventana que se cierre, como su botón X.

    Va al `hwnd` exacto y no por título: el 18 sep 2026 un filtro por substring
    se llevó puesta una sesión de Claude Code titulada «WorkShell con NiceGUI y
    psutil» (ver `ventana_del_panel`), y acá la consecuencia sería cerrarla. La
    app puede preguntar antes de irse: es un WM_CLOSE, no un `kill`.
    """
    if not win32gui.IsWindow(ventana['hwnd']):
        return False
    win32gui.PostMessage(ventana['hwnd'], win32con.WM_CLOSE, 0, 0)
    return True
