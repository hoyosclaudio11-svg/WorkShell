import asyncio
import base64
import datetime
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import psutil
from fastapi import Response
from nicegui import app, run, ui

import ventanas

psutil.cpu_percent()  # descarta la primera lectura (siempre 0.0)

# Navegadores Chromium, en orden de preferencia. El modo --app da una ventana
# propia: sin barra de direcciones, sin favoritos y sin pestanas.
NAVEGADORES = (
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
)

RUTA_LAYOUTS = Path(__file__).with_name('layouts.json')
# Las ventanas que «Acomodar todo» no debe tocar. Van en su propio archivo y no
# adentro de un layout a propósito: no son una disposición, son una preferencia
# sobre las apps de esta máquina, y valen para todos los layouts.
RUTA_IGNORADAS = Path(__file__).with_name('ignoradas.json')
# Las preferencias del panel (hoy: si las miniaturas están prendidas). Mismo
# criterio que las ignoradas: no es una disposición, es cómo querés que se vea
# el panel en esta máquina, así que vale para todos los layouts.
RUTA_PANEL = Path(__file__).with_name('panel.json')
# La libreta del panel: lo que anotás para hoy. Va en su propio archivo y no
# adentro de panel.json porque no es una preferencia de cómo se ve el panel sino
# tu trabajo, y ese se respalda y se lee aparte.
RUTA_PENDIENTES = Path(__file__).with_name('pendientes.json')
# Los servicios de la casa (FreeLLMAPI, Cerebro, MT5, los paneles): su lista y
# cómo se levanta cada uno. También al lado del script, y editable a mano.
RUTA_SERVICIOS = Path(__file__).with_name('servicios.json')
# El Escritorio, para buscar ahí los accesos directos de las apps. Se pregunta a
# Windows en vez de usar la carpeta del script: WorksheLL vivía suelto en el
# Escritorio y funcionaba de casualidad, y ahora tiene su propia carpeta.
CARPETA = ventanas.carpeta_escritorio()

# El título de la ventana del panel, en un solo lugar: lo usa `ui.run` para
# titularla y «Acomodar todo» para reconocer cuál ventana es WorksheLL y dejarla
# afuera de la grilla (si entra, el mapa termina adentro de una celda).
TITULO = 'WorkShell'

# Cada disposición dice dos cosas: qué paneles de WorksheLL se ven y en qué
# rectángulo va cada ventana del escritorio. Las ventanas son la mitad del
# centro de trabajo, así que viven acá y no en un archivo aparte: cambiar de
# layout mueve las dos cosas a la vez.
LAYOUTS_DEFAULT = {
    'Desarrollo': {'paneles': ['metricas', 'terminal', 'preview', 'escritorio'], 'ventanas': []},
    'Trading': {'paneles': ['metricas', 'trading', 'escritorio'], 'ventanas': []},
}

PANTALLA_VACIA = {'paneles': [], 'ventanas': []}

# Cada cuánto se prueba un lugar libre del mapa al sumar una zona. Más chico que
# el tamaño de una zona: así también encuentra los huecos irregulares que dejan
# las zonas que el usuario movió a mano.
PASO_BUSQUEDA = 4.0

# El modo barra: cuántos segundos aguanta a la vista sin que la uses antes de
# esconderse sola (0 = nunca), y cuánto hay que dejar el mouse contra el borde de
# abajo para pedirla de vuelta. El segundo es una espera y no un roce a
# propósito: el borde de abajo es también el camino a la barra de tareas, y
# volver cada vez que el mouse pasa por ahí sería peor que no volver nunca.
SEGUNDOS_AUTO = 25.0
ESPERA_BORDE = 0.8
# La barra al pie se ve a través suyo, para no tapar lo que tiene abajo: 222 de
# 255 es un 87%, que se nota sin que el texto se vuelva ilegible. El botón
# «Opacidad» recorre estos valores y 255 (sólida) apaga el efecto. Verificado
# sobre un Chrome de verdad en `_prueba_opacidad.py`: la mezcla medida es la que
# dice el alfa.
OPACIDADES = (255, 222, 191, 153)
# Cada cuánto se mira dónde está el mouse. Es barato (una llamada, sin enumerar
# ventanas), pero no gratis: 4 veces por segundo alcanza para que se sienta
# inmediato y no se note.
PASO_RATON = 0.25


def numero(valor, por_defecto: float) -> float:
    """Convierte a número sin explotar: layouts.json se edita a mano."""
    try:
        return float(valor)
    except (TypeError, ValueError):
        return por_defecto


def dentro_del_mapa(zona: dict) -> dict:
    """Trae la zona adentro del mapa. Una zona afuera no se ve ni se puede borrar.

    No es hipotético: la versión anterior apilaba las zonas nuevas cada 40
    puntos sin mirar el borde, así que la quinta quedaba en y=80 (media afuera)
    y la séptima en y=120 — invisible, y sin forma de seleccionarla para
    eliminarla porque el clic no la alcanza. También entra por acá una zona
    leída de una ventana más grande que la pantalla. El archivo se reescribe
    recién cuando el usuario toca algo, no al arrancar.
    """
    ancho = min(100.0, max(5.0, numero(zona.get('ancho'), 40.0)))
    alto = min(100.0, max(5.0, numero(zona.get('alto'), 40.0)))
    zona['ancho'], zona['alto'] = round(ancho, 2), round(alto, 2)
    zona['x'] = round(min(max(0.0, numero(zona.get('x'), 0.0)), 100.0 - ancho), 2)
    zona['y'] = round(min(max(0.0, numero(zona.get('y'), 0.0)), 100.0 - alto), 2)
    return zona


def normalizar_layout(valor) -> dict:
    """Acepta el formato viejo (lista de paneles) y el nuevo (paneles + ventanas).

    Los layouts guardados antes de que existiera el Escritorio se migran
    sumándolo: si no, la función nueva quedaría invisible justo para quien ya
    tenía sus layouts armados. El archivo se reescribe recién cuando el usuario
    toca algo, no al arrancar.
    """
    if isinstance(valor, list):
        return {'paneles': [*valor, 'escritorio'], 'ventanas': []}
    if isinstance(valor, dict):
        return {
            'paneles': list(valor.get('paneles', [])),
            'ventanas': [dentro_del_mapa(z) for z in valor.get('ventanas', [])
                         if isinstance(z, dict)],
        }
    return dict(PANTALLA_VACIA)


def puerto_libre(inicial=8080, intentos=20):
    """Devuelve el primer puerto libre a partir de `inicial`."""
    for puerto in range(inicial, inicial + intentos):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', puerto)) != 0:  # nadie escucha
                return puerto
    raise RuntimeError(f'Sin puertos libres entre {inicial} y {inicial + intentos}')


def abrir_ventana(url: str, ancho: int = 1700, alto: int = 1000) -> bool:
    """Abre la app en modo --app: ventana propia sin chrome de navegador.

    El tamaño y la posición se piden explícitos y recortados al área de
    trabajo: sin eso Chrome restaura dónde estaba la última vez, y se lo vio
    abrir en x=-995 — o sea, más de la mitad de WorksheLL fuera de pantalla.
    """
    x0, y0, pantalla_ancho, pantalla_alto = ventanas.area_trabajo()
    ancho = min(ancho, pantalla_ancho)
    alto = min(alto, pantalla_alto)
    x = x0 + (pantalla_ancho - ancho) // 2
    y = y0 + (pantalla_alto - alto) // 2

    for ruta in NAVEGADORES:
        if Path(ruta).exists():
            subprocess.Popen([
                ruta, f'--app={url}',
                f'--window-size={ancho},{alto}',
                f'--window-position={x},{y}',
            ])
            return True
    return False


def abrir_cuando_responda(url: str, puerto: int, espera: float = 90.0) -> None:
    """Espera a que el servidor acepte conexiones y recien ahi abre la ventana.

    Si abrieramos antes, el navegador mostraria un error de conexion.
    """
    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        try:
            with socket.create_connection(('127.0.0.1', puerto), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        print('el servidor no arranco a tiempo; no abro la ventana')
        return

    if abrir_ventana(url):
        print(f'ventana abierta en {url}')
    else:
        print(f'no encontre Chrome ni Edge; abri {url} a mano')


def cargar_layouts() -> dict:
    """Lee layouts.json. Si no existe lo crea con los defaults.

    Si existe pero esta roto no lo pisa: avisa y usa los defaults en memoria,
    para no borrarle a nadie un archivo que quiza queria arreglar a mano.
    """
    if RUTA_LAYOUTS.exists():
        try:
            datos = json.loads(RUTA_LAYOUTS.read_text(encoding='utf-8'))
            if isinstance(datos, dict) and datos:
                return {nombre: normalizar_layout(valor) for nombre, valor in datos.items()}
            print(f'{RUTA_LAYOUTS.name} no tiene layouts validos: uso los defaults (no lo toco)')
        except (OSError, json.JSONDecodeError) as exc:
            print(f'{RUTA_LAYOUTS.name} ilegible ({exc}): uso los defaults (no lo toco)')
        return {n: normalizar_layout(v) for n, v in LAYOUTS_DEFAULT.items()}

    RUTA_LAYOUTS.write_text(
        json.dumps(LAYOUTS_DEFAULT, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    print(f'{RUTA_LAYOUTS.name} no existia: creado con los layouts por defecto')
    return {n: normalizar_layout(v) for n, v in LAYOUTS_DEFAULT.items()}


def guardar_layouts() -> None:
    """Deja las zonas y los paneles en disco, sin tocar el archivo si falla."""
    try:
        RUTA_LAYOUTS.write_text(
            json.dumps(layouts, indent=2, ensure_ascii=False), encoding='utf-8'
        )
    except OSError as exc:
        print(f'no pude guardar {RUTA_LAYOUTS.name}: {exc}')
        ui.notify(f'No pude guardar {RUTA_LAYOUTS.name}: {exc}', type='negative')


def cargar_ignoradas() -> list[dict]:
    """Lee ignoradas.json. Si no existe, no está: la lista arranca vacía."""
    if not RUTA_IGNORADAS.exists():
        return []
    try:
        datos = json.loads(RUTA_IGNORADAS.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'{RUTA_IGNORADAS.name} ilegible ({exc}): sigo sin ninguna ignorada')
        return []
    if not isinstance(datos, list):
        print(f'{RUTA_IGNORADAS.name} no tiene una lista: lo ignoro')
        return []
    return [r for r in datos if isinstance(r, dict) and r.get('proceso')]


def guardar_ignoradas() -> None:
    try:
        RUTA_IGNORADAS.write_text(
            json.dumps(ignoradas, indent=2, ensure_ascii=False), encoding='utf-8'
        )
    except OSError as exc:
        print(f'no pude guardar {RUTA_IGNORADAS.name}: {exc}')
        ui.notify(f'No pude guardar {RUTA_IGNORADAS.name}: {exc}', type='negative')


def cargar_ajustes() -> dict:
    """Lee panel.json. Si no está o está roto, usa los valores de fábrica."""
    por_defecto = {'miniaturas': True, 'barra_auto': SEGUNDOS_AUTO,
                   'opacidad': OPACIDADES[1]}
    if not RUTA_PANEL.exists():
        return por_defecto
    try:
        datos = json.loads(RUTA_PANEL.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'{RUTA_PANEL.name} ilegible ({exc}): uso los valores por defecto')
        return por_defecto
    if not isinstance(datos, dict):
        return por_defecto
    ajustes = {clave: datos.get(clave, valor) for clave, valor in por_defecto.items()}
    # `barra_auto` son segundos: si el archivo trae cualquier otra cosa, vale el
    # de fábrica, antes que romper el arranque del panel.
    try:
        ajustes['barra_auto'] = float(ajustes['barra_auto'])
    except (TypeError, ValueError):
        ajustes['barra_auto'] = SEGUNDOS_AUTO
    # Y la opacidad tiene que ser uno de los valores del recorrido: si no, el
    # botón no sabría por dónde seguir.
    if ajustes['opacidad'] not in OPACIDADES:
        ajustes['opacidad'] = OPACIDADES[1]
    return ajustes


def guardar_ajustes() -> None:
    try:
        RUTA_PANEL.write_text(
            json.dumps(ajustes, indent=2, ensure_ascii=False), encoding='utf-8'
        )
    except OSError as exc:
        print(f'no pude guardar {RUTA_PANEL.name}: {exc}')


def cargar_pendientes() -> list[dict]:
    """Lee la libreta. Si no está o está rota, arranca vacía.

    Se descarta lo que no tenga texto —una entrada a medio escribir no puede
    romper el panel— pero nada más: lo demás lo completa `agregar_pendiente`.
    """
    if not RUTA_PENDIENTES.exists():
        return []
    try:
        datos = json.loads(RUTA_PENDIENTES.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'{RUTA_PENDIENTES.name} ilegible ({exc}): arranco sin pendientes')
        return []
    if not isinstance(datos, list):
        return []
    return [p for p in datos if isinstance(p, dict) and p.get('texto')]


def guardar_pendientes() -> None:
    try:
        RUTA_PENDIENTES.write_text(json.dumps(pendientes, indent=2, ensure_ascii=False),
                                   encoding='utf-8')
    except OSError as exc:
        print(f'no pude guardar {RUTA_PENDIENTES.name}: {exc}')


def hoy() -> str:
    return time.strftime('%Y-%m-%d')


# «17:30 llamar a X»: lo que arranca con una hora es un recordatorio y WorksheLL
# avisa cuando llega (ver `avisar_recordatorios`). Sin hora es un pendiente más.
_HORA = re.compile(r'^\s*(\d{1,2})[:.](\d{2})\s+(.+)$')


def agregar_pendiente(texto: str) -> dict | None:
    """Anota algo en la libreta y lo devuelve. None si el texto venía vacío."""
    texto = (texto or '').strip()
    if not texto:
        return None
    hora = ''
    encontrado = _HORA.match(texto)
    if encontrado:
        hh, mm = int(encontrado.group(1)), int(encontrado.group(2))
        if hh < 24 and mm < 60:
            hora = f'{hh:02d}:{mm:02d}'
            texto = encontrado.group(3).strip()
    anotado = {'texto': texto, 'creado': time.strftime('%Y-%m-%d %H:%M'),
               'dia': hoy(), 'hora': hora, 'hecho': '', 'avisado': False}
    pendientes.append(anotado)
    guardar_pendientes()
    return anotado


def ordenar_pendientes() -> list[tuple[int, dict]]:
    """Los pendientes con su índice, en el orden en que se muestran.

    Lo de hoy primero, después lo que quedó de días anteriores, y lo hecho al
    final: la libreta es para lo que tenés que hacer, no un archivo.
    """
    de_hoy = hoy()

    def clave(par: tuple[int, dict]) -> tuple:
        _, p = par
        return (bool(p.get('hecho')),                      # hechos al final
                p.get('dia', '') != de_hoy,                # hoy antes que lo viejo
                p.get('dia', ''),                          # lo viejo, por fecha
                p.get('hora') or '99:99',                  # con hora, por horario
                p.get('creado', ''))

    return sorted(enumerate(pendientes), key=clave)


def quitar_pendiente(idx: int, hecho: bool = True) -> None:
    """Marca (o desmarca) un pendiente, o lo borra con `hecho=None`."""
    if not 0 <= idx < len(pendientes):
        return
    if hecho is None:
        pendientes.pop(idx)
    else:
        pendientes[idx]['hecho'] = time.strftime('%Y-%m-%d %H:%M') if hecho else ''
        pendientes[idx]['avisado'] = False  # si vuelve a estar pendiente, vuelve a avisar
    guardar_pendientes()


async def avisar_recordatorios() -> None:
    """Avisa los recordatorios de hoy que ya llegaron a su hora.

    Corre cada medio minuto y no se apoya en que WorksheLL esté a la vista: si el
    recordatorio era a las 17:30 y el panel estaba minimizado, el aviso sale
    igual cuando vuelva. `avisado` queda anotado en el archivo, así que reiniciar
    WorksheLL no lo repite.
    """
    ahora = time.strftime('%H:%M')
    avisados = 0
    for p in pendientes:
        if (p.get('hecho') or p.get('avisado') or not p.get('hora')
                or p.get('dia') != hoy() or p['hora'] > ahora):
            continue
        p['avisado'] = True
        avisados += 1
        ui.notify(f'⏰ {p["hora"]} · {p["texto"]}', type='warning', position='top',
                  timeout=0, close_button='Entendido')
    if avisados:
        guardar_pendientes()
        dibujar_pendientes()


# --- Los servicios de la casa -------------------------------------------------
# WorksheLL pasa a decir qué está levantado y a levantar lo que falta. Nace de
# una falla que se repite: FreeLLMAPI caído y la publicación que no sale, sin
# ningún cartel que lo diga (el síntoma es «hoy no publicó»). La lista vive en
# `servicios.json` para poder agregar o corregir servicios sin tocar el código.

SERVICIOS_DEFAULT = [
    {'nombre': 'FreeLLMAPI', 'puerto': 3001, 'url': 'http://localhost:3001',
     'carpeta': r'C:\Users\chito\FreeLLMAPI',
     'lanzar': r'start "" /min "C:\Users\chito\FreeLLMAPI\iniciar_freellmapi.bat"',
     'nota': 'sin esto no publica ni genera imágenes'},
    {'nombre': 'Cerebro', 'puerto': 8765, 'url': 'http://localhost:8765/docs',
     'carpeta': r'C:\cerebro',
     'lanzar': r'start "" /min "C:\cerebro\venv\Scripts\python.exe" brain_api\run_api.py',
     'nota': 'memoria y herramientas'},
    {'nombre': 'MT5 gateway', 'puerto': 8060, 'url': 'http://localhost:8060/docs',
     'carpeta': r'C:\Users\chito\OneDrive - Plan Sarmiento\Escritorio\MT5MultiAgente',
     'lanzar': 'run.cmd gateway', 'nota': 'el único que habla con el terminal'},
    {'nombre': 'MT5 trader', 'puerto': 8070, 'url': 'http://localhost:8070/docs',
     'carpeta': r'C:\Users\chito\OneDrive - Plan Sarmiento\Escritorio\MT5MultiAgente',
     'lanzar': 'run.cmd trader', 'nota': 'el plantel de agentes'},
    {'nombre': 'MT5 dashboard', 'puerto': 8050, 'url': 'http://localhost:8050',
     'carpeta': r'C:\Users\chito\OneDrive - Plan Sarmiento\Escritorio\MT5MultiAgente',
     'lanzar': 'run.cmd dashboard', 'nota': 'la vista del trading'},
    {'nombre': 'Panel DelMonte', 'puerto': 5001, 'url': 'http://localhost:5001',
     'carpeta': r'E:\DelMonte\panel',
     'lanzar': r'start "" /min "E:\DelMonte\panel\lanzar_panel_web_silent.bat"',
     'nota': 'métricas de los diarios'},
    {'nombre': 'madre-pc', 'puerto': 8766, 'url': 'http://localhost:8766',
     'carpeta': r'E:\madre-pc', 'lanzar': r'start "" /min "E:\madre-pc\madre_todo.cmd"',
     'nota': 'el supervisor'},
    {'nombre': 'ttyd', 'puerto': 7681, 'url': 'http://localhost:7681',
     'carpeta': '', 'lanzar': 'start "" /min ttyd -i 127.0.0.1 -p 7681 -W cmd.exe',
     'nota': 'la terminal del panel'},
]


def cargar_servicios() -> list[dict]:
    """Lee servicios.json. Si no está, lo escribe con la lista de fábrica.

    Se escribe en vez de solo usarla en memoria para que la lista se pueda
    corregir: es un archivo del proyecto, al lado de layouts.json.
    """
    if not RUTA_SERVICIOS.exists():
        try:
            RUTA_SERVICIOS.write_text(json.dumps(SERVICIOS_DEFAULT, indent=2,
                                                 ensure_ascii=False), encoding='utf-8')
            print(f'escribí {RUTA_SERVICIOS.name} con los servicios de fábrica')
        except OSError as exc:
            print(f'no pude escribir {RUTA_SERVICIOS.name}: {exc}')
        return [dict(s) for s in SERVICIOS_DEFAULT]
    try:
        datos = json.loads(RUTA_SERVICIOS.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'{RUTA_SERVICIOS.name} ilegible ({exc}): uso la lista de fábrica')
        return [dict(s) for s in SERVICIOS_DEFAULT]
    if not isinstance(datos, list):
        return [dict(s) for s in SERVICIOS_DEFAULT]
    return [s for s in datos if isinstance(s, dict) and s.get('nombre') and s.get('puerto')]


def puerto_abierto(puerto: int, host: str = '127.0.0.1', espera: float = 0.4) -> bool:
    """¿Hay alguien escuchando en ese puerto?

    Se conecta —en vez de leer `netstat`— porque los servicios de acá escuchan en
    0.0.0.0 y en 127.0.0.1, y una lista de puertos parseada a mano se equivoca
    con el primero. Un connect contesta la pregunta de verdad.
    """
    try:
        with socket.create_connection((host, int(puerto)), timeout=espera):
            return True
    except (OSError, ValueError):
        return False


def estado_servicios(servicios: list[dict]) -> list[bool]:
    """Los puertos de todos, de una pasada (corre en un hilo aparte)."""
    return [puerto_abierto(s['puerto']) for s in servicios]


def levantar_servicio(servicio: dict) -> tuple[bool, str]:
    """Corre el lanzador de ese servicio, sin esperar a que termine.

    `shell=True` porque los lanzadores son .bat y `start`: es como si lo
    escribieras en una consola. La carpeta importa —`run.cmd gateway` solo
    funciona parado en el proyecto— y por eso cada servicio la declara.
    """
    comando = (servicio.get('lanzar') or '').strip()
    if not comando:
        return False, 'no sé cómo levantarlo (no tiene «lanzar» en servicios.json)'
    try:
        subprocess.Popen(comando, cwd=servicio.get('carpeta') or None, shell=True)
    except OSError as exc:
        return False, f'no pude lanzarlo ({exc})'
    return True, ''


# --- El trading, en el panel --------------------------------------------------
# Los números del MT5 MultiAgente, que ya los publica por HTTP: la cuenta en el
# gateway (8060) y el plantel de agentes en el trader (8070). WorksheLL no habla
# con el terminal ni con la base: si el servicio no contesta, el panel lo dice.
MT5_GATEWAY = 'http://127.0.0.1:8060'
MT5_TRADER = 'http://127.0.0.1:8070'


def pedir_json(url: str, espera: float = 2.5) -> dict:
    """Un GET a un servicio local. Devuelve {} si no contesta.

    Que un servicio esté caído no puede romper el panel ni llenarlo de errores:
    el estado vacío ya dice lo que hay que saber.
    """
    try:
        with urllib.request.urlopen(url, timeout=espera) as respuesta:
            datos = json.loads(respuesta.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f'{url}: no pude leerlo ({exc})')
        return {}
    return datos if isinstance(datos, dict) else {}


def estado_mt5() -> dict:
    """Lo que el multiagente dice de sí mismo: cuenta, posiciones y plantel."""
    return {
        'cuenta': pedir_json(f'{MT5_GATEWAY}/account'),
        'posiciones': pedir_json(f'{MT5_GATEWAY}/positions').get('positions', []),
        'estado': pedir_json(f'{MT5_TRADER}/state'),
    }


def plata(valor) -> str:
    """Un número con separador de miles y coma decimal, como se escribe acá."""
    try:
        return f'{float(valor):,.2f}'.replace(',', '\x00').replace('.', ',').replace('\x00', '.')
    except (TypeError, ValueError):
        return '—'


# --- ¿Publicaron hoy? ---------------------------------------------------------
# La pregunta que hoy se contesta corriendo /revisar a mano, y por la que el
# dueño pidió que el panel «sirva de verdad». Dos fuentes, ninguna inventada:
#   · los sitios de WordPress dejan su última publicación en `cooldown/<sid>.json`,
#     que escribe el pipeline: es lo más limpio, sin parsear texto;
#   · los demás solo dejan rastro en su log, así que se buscan sus marcas del día.
# Lo que **no** se reporta como falla importa tanto como lo que sí: hay fallas
# crónicas y ya conocidas (thumbnails 403 del canal de fútbol, 429 de FreeLLMAPI,
# el publicador de WordPress del Escáner roto desde el 16 sep, GA4 con una
# dimensión que ya no existe). Un panel que grita todos los días por lo mismo
# deja de mirarse — es exactamente el error que tiene el chequeo de las 04:00.

AUTOMATIZACION = Path(r'E:\DelMonte\automatizacion')
ESCANER = Path(r'E:\Escaner Cuantico Multiactivo')

# Lo conocido que no se reporta: si aparece en la línea, esa falla ya se sabe.
# `FALLO tras 3 intentos de auditoria` es del Web Analyzer: la auditoría no está
# disponible, el generador degrada a post educativo y **publica igual** (el
# «=== Resultado: OK ===» de la misma corrida). Marcarlo en rojo sería gritar
# todos los días por algo que no impide publicar, que es justo lo que hace
# inservible al chequeo de las 04:00.
FALLAS_CONOCIDAS = ('429', 'RateLimit', 'link_url', 'Thumbnail', 'thumbnail',
                    'publicador-wp exit=1', 'calidad editorial',
                    'FALLO tras 3 intentos')

# Cada canal: de dónde sale si publicó, y qué marcas son falla en ese log.
CANALES_PUBLICACION = (
    {'nombre': 'River Plate Info', 'cooldown': 'river',
     'log': AUTOMATIZACION / 'logs' / 'publicador.log',
     'publico': ('[river] Publicado (ID:',)},
    {'nombre': 'Diario Albiceleste', 'cooldown': 'diario-albiceleste',
     'log': AUTOMATIZACION / 'logs' / 'publicador.log',
     'publico': ('[diario-albiceleste] Publicado (ID:',)},
    {'nombre': 'Revista Espectáculo', 'cooldown': 'revista-espectaculo',
     'log': AUTOMATIZACION / 'logs' / 'publicador.log',
     'publico': ('[revista-espectaculo] Publicado (ID:',)},
    {'nombre': 'El Podio MP', 'log': AUTOMATIZACION / 'logs' / 'elpodiomp.log',
     'publico': ('Artículo publicado (ID:', 'Publicado OK (ID:')},
    {'nombre': 'Web Analyzer', 'log': Path(r'E:\DelMonte\web-analyzer') / 'generador.log',
     'publico': ('Publicado: ',)},
    {'nombre': 'YouTube (Roldán y Fútbol)',
     'log': AUTOMATIZACION / 'logs' / 'roldan.log',
     'publico': ('SUBIÓ OK',), 'por_canal': '(canal: '},
    {'nombre': 'Escáner Cuántico',
     'log': ESCANER / 'logs' / 'sync_diario.log', 'publico': (),
     'pasos': True},
)

_NIVEL_MALO = (' ERROR', '[ERROR]', 'CRITICAL')


def leer_cola(ruta: Path, lineas: int = 600, atras: int = 250_000) -> list[str]:
    """Las últimas líneas de un log, sin leerlo entero.

    `publicador.log` rota a los 5 MB y un día de publicaciones es largo: se pide
    el final del archivo y se corta ahí. La primera línea puede venir partida
    por el medio; no importa, porque se toman las últimas.
    """
    try:
        with ruta.open('rb') as archivo:
            archivo.seek(0, os.SEEK_END)
            tamano = archivo.tell()
            archivo.seek(max(0, tamano - atras))
            texto = archivo.read().decode('utf-8', errors='replace')
    except OSError as exc:
        print(f'{ruta.name}: no pude leerlo ({exc})')
        return []
    return texto.splitlines()[-lineas:]


def de_hoy(linea: str, fecha_iso: str, fecha_dma: str) -> bool:
    """¿Esta línea del log es de hoy? Los formatos no son todos iguales."""
    limpia = linea.lstrip()
    return limpia.startswith((f'[{fecha_iso}', fecha_iso, f'[{fecha_dma}'))


def paso_de(linea: str) -> str:
    """El nombre del paso de una línea tipo «… publicador-wp exit=1»."""
    partes = linea.split()
    for i, parte in enumerate(partes):
        if parte.startswith('exit=') and i:
            return partes[i - 1]
    return '?'


def hora_de(linea: str) -> str:
    """La hora de una línea de log, para poder decir «publicó a las 06:03»."""
    encontrado = re.search(r'(\d{1,2}):(\d{2}):\d{2}', linea)
    return f'{int(encontrado.group(1)):02d}:{encontrado.group(2)}' if encontrado else ''


def ultima_publicacion(sid: str) -> str:
    """Cuándo publicó por última vez ese sitio, según su archivo de cooldown."""
    ruta = AUTOMATIZACION / 'cooldown' / f'{sid}.json'
    try:
        datos = json.loads(ruta.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return ''
    return str(datos.get('ultima_publicacion') or '')


def cuando_fue(momento: str) -> tuple[str, str]:
    """Un ISO de cooldown → (fecha, hora), vacíos si no se entiende."""
    try:
        cuando = datetime.datetime.fromisoformat(momento)
    except (TypeError, ValueError):
        return '', ''
    return cuando.strftime('%Y-%m-%d'), cuando.strftime('%H:%M')


def estado_publicaciones() -> list[dict]:
    """Un renglón por canal: si publicó hoy, y si algo falló, por qué.

    Corre en un hilo aparte: lee varios archivos del disco, y el panel no se
    puede quedar esperando por eso.
    """
    fecha_iso, fecha_dma, hoy_txt = hoy(), time.strftime('%d/%m/%Y'), hoy()
    filas = []
    for canal in CANALES_PUBLICACION:
        lineas = leer_cola(canal['log']) if canal.get('log') else []
        del_dia = [l for l in lineas if de_hoy(l, fecha_iso, fecha_dma)]

        publicados: list[str] = []
        for linea in del_dia:
            if any(marca in linea for marca in canal.get('publico', ())):
                # Sin repetir horas: una corrida deja varias líneas de la misma
                # publicación y «09:00 · 09:00» no dice nada.
                hora = hora_de(linea)
                if hora and hora not in publicados:
                    publicados.append(hora)
        fallas = [l for l in del_dia
                  if any(nivel in l for nivel in _NIVEL_MALO)
                  and not any(sabida in l for sabida in FALLAS_CONOCIDAS)]

        if canal.get('cooldown'):
            fecha, hora = cuando_fue(ultima_publicacion(canal['cooldown']))
            if fecha == hoy_txt:
                publicados, detalle = [hora], hora
            else:
                publicados = []
                detalle = f'{fecha} {hora}'.strip()
        elif canal.get('pasos'):
            # Un canal que no publica nada él mismo: lo que importa es si su
            # corrida de hoy terminó bien, paso por paso. Los pasos que fallan
            # por causas conocidas se cuentan como fallados —no se esconden— pero
            # no ponen el renglón en rojo: el detalle dice cuáles.
            pasos = [l for l in del_dia if 'exit=' in l]
            if not pasos:
                filas.append({'nombre': canal['nombre'], 'estado': 'sin datos',
                              'detalle': 'todavía no corrió hoy', 'cuantas': 0})
                continue
            bien = [l for l in pasos if l.rstrip().endswith('exit=0')]
            malos = [l for l in pasos if l not in bien]
            conocidos = [l for l in malos if any(s in l for s in FALLAS_CONOCIDAS)]
            fallas = [l for l in malos if l not in conocidos]
            publicados = bien
            detalle = f'{len(bien)} de {len(pasos)} pasos OK'
            if conocidos:
                detalle += (' · falla conocida: '
                            + ', '.join(paso_de(l) for l in conocidos))
        else:
            detalle = ' · '.join(publicados[-3:])

        if fallas:
            estado = 'falló'
            # El final de la línea de la falla: es lo que dice qué pasó.
            detalle = ' '.join(fallas[-1].split()[-9:])[:110]
        elif publicados:
            estado = 'ok'
        else:
            estado = 'sin publicar'

        filas.append({'nombre': canal['nombre'], 'estado': estado,
                      'detalle': detalle, 'cuantas': len(publicados)})
    return filas


layouts = cargar_layouts()
ignoradas: list[dict] = cargar_ignoradas()
ajustes = cargar_ajustes()
# La libreta: cada pendiente es {'texto', 'creado', 'dia', 'hora', 'hecho',
# 'avisado'}. `dia` es el día para el que es (hoy al anotarlo) y `hora` la de un
# recordatorio, vacía si no lo es.
pendientes: list[dict] = cargar_pendientes()
# Los servicios que WorksheLL vigila, y el estado de cada uno: el punto de la
# fila y los botones se actualizan sin redibujar (como los puntos del mapa).
servicios: list[dict] = cargar_servicios()
filas_servicios: list[dict] = []
paneles: dict[str, ui.card] = {}
zonas_actuales: list[dict] = []
layout_actual = next(iter(layouts))

# El punto de estado de cada zona, en el mismo orden que `zonas_actuales`, y el
# ultimo estado que se pinto. Se guardan las referencias para poder cambiar el
# punto solo, sin redibujar el mapa: redibujarlo en cada refresco cortaria un
# arrastre en curso y haria parpadear todo.
puntos_estado: list[ui.element] = []
estados_pintados: list[str] = []
# Las miniaturas: el <img> de cada zona y el último JPEG que se capturó de esa
# ventana, en el mismo orden que `zonas_actuales`. Se guardan los bytes —y no se
# vuelven a pedir— para que redibujar el mapa (arrastrar una zona, por ejemplo)
# no deje las zonas en blanco hasta el próximo refresco.
miniaturas_pintadas: list[ui.element] = []
# La tira de tarjetas, una por zona y en el mismo orden: la tarjeta, su cara y
# su punto. Se guardan las referencias por el mismo motivo que en el mapa —
# refrescar sin redibujar— y porque la tira y el mapa muestran lo mismo: las
# dos se pintan desde `miniaturas`.
tarjetas: list[dict] = []
# Si las tarjetas entran con la animación de entrada: sí en el primer dibujo y al
# entrar al modo barra, no en los redibujos (la tira se redibuja con cada clic
# en una tarjeta, y animarla ahí sería un temblor constante).
animar_tira = True
# Cada entrada es ((proceso, titulo) de la ventana capturada, JPEG): la clave va
# adentro y no solo el número de zona porque las zonas se borran, se agregan y
# se cambia de layout, y los números se corren. Sin eso, al cambiar de
# organización el mapa mostraría un rato las caras de la anterior.
miniaturas: dict[int, tuple[tuple[str, str], bytes]] = {}
# Las zonas cuya última captura ya no es de ahora (su ventana se minimizó): se
# conservan apagadas, porque de una minimizada no hay nada que capturar.
miniaturas_viejas: set[int] = set()
# Sube con cada tanda de capturas: es lo que hace que el navegador vuelva a
# pedir la imagen en vez de mostrar la que tenía en caché.
miniaturas_version = 0
# La captura tarda bastante más que enumerar ventanas. Si una tanda se demora
# más que el intervalo del timer, la siguiente no se apila: se saltea.
capturando = False

# El estado del modo «Sumar ventana»: si está esperando, desde cuándo, qué
# ventanas había antes de apartarse y a qué hora se vio por primera vez cada una
# de las nuevas. Un solo diccionario en vez de cuatro globales que van juntas.
busqueda: dict = {'activa': False, 'desde': 0.0, 'antes': set(), 'vistas': {}}

# El modo barra: WorksheLL se achica a una franja al pie y le deja la pantalla a
# las ventanas, con una tarjeta por ventana para elegir cuál sube. Lo pidió el
# dueño el 19 sep 2026, después de «Sumar ventana»: *"en la parte baja todas mis
# pestañas pero en tarjetas... y las tarjetas se esconden para tener toda la
# pantalla a disposición"*.
#
#   activa  el modo está prendido: la ventana es una barra y se ve la tira
#   tapada  además está minimizada, así que la grilla puede usar toda la pantalla
#   foco    la zona que está agrandada, o None para repartir parejo
#   dormido el estado de «estaba minimizada» en el repaso anterior, para darse
#           cuenta de que el dueño la trajo de vuelta
#
# Y lo que hace que se corra sola cuando no la usás: cuándo se la usó por última
# vez, desde cuándo el mouse está contra el borde de abajo, y si el mouse salió
# de ese borde alguna vez desde que se escondió (sin eso volvería sola apenas se
# esconde: el mouse que la escondió sigue apoyado ahí).
ALTO_BARRA = 260
barra: dict = {'activa': False, 'tapada': False, 'foco': None,
               'rect_panel': None, 'dormido': False,
               'auto': SEGUNDOS_AUTO, 'ultimo_uso': 0.0,
               'borde_desde': None, 'borde_armado': False,
               'opacidad': ajustes['opacidad']}  # preferencia: va a panel.json


def marcar_uso() -> None:
    """Empieza a contar de nuevo el rato que la barra se queda a la vista.

    Se llama con cada cosa que sea «la estoy usando»: elegir una tarjeta,
    repartir, traerla de vuelta. Sin esto, usar la barra no correría el reloj y
    se escondería igual a los `SEGUNDOS_AUTO` de haber aparecido.
    """
    barra['ultimo_uso'] = time.monotonic()

# Cuánto tiene que quedarse quieta una ventana nueva para dar por terminado el
# «ya abrí lo que quería», cuánto se espera como mucho, y cada cuánto se mira.
ASENTAR = 3.0
TOPE_BUSQUEDA = 180.0
PASO_VIGILANCIA = 1.0
# Sube cada vez que se redibuja el mapa. Un refresco de estados que vuelve de su
# hilo y encuentra otra generacion ya no sirve: los indices que traia apuntan a
# otras zonas.
generacion_mapa = 0


def mostrar_paneles(pedidos: list[str], contexto: str = '') -> None:
    """Deja a la vista los paneles pedidos, y solo esos.

    En modo barra manda la barra: se ve la tira de tarjetas y nada más, aunque el
    layout pida otra cosa (en 260 px de alto no entra ningún otro panel).
    """
    if barra['activa']:
        pedidos = ['tarjetas']
    faltantes = [clave for clave in pedidos if clave not in paneles]
    if faltantes:
        print(f'{contexto}paneles desconocidos {faltantes} (ignorados)')
    for clave, card in paneles.items():
        card.set_visibility(clave in pedidos)


def aplicar_layout(nombre: str) -> None:
    """Muestra los paneles del layout elegido y carga sus zonas de ventanas."""
    global zonas_actuales, layout_actual
    layout_actual = nombre
    elegido = layouts.get(nombre, PANTALLA_VACIA)
    mostrar_paneles(elegido['paneles'], f'layout "{nombre}": ')
    zonas_actuales = elegido['ventanas']
    dibujar_zonas()


async def cambiar_layout(nombre: str) -> None:
    """Cambia de layout y repinta los puntos enseguida.

    Si no, el mapa queda hasta 5 s sin decir nada, que es lo que tarda el timer
    de estados en dar la primera vuelta.
    """
    aplicar_layout(nombre)
    await refrescar_estados()


def refrescar():
    cpu_pct = psutil.cpu_percent()
    ram_pct = psutil.virtual_memory().percent
    cpu_valor.text = f'{cpu_pct:.0f}'
    ram_valor.text = f'{ram_pct:.0f}'
    cpu_relleno.style(f'width: {cpu_pct:.1f}%')
    ram_relleno.style(f'width: {ram_pct:.1f}%')


class ResourceGuard:
    """Vigila la RAM y baja la frecuencia de refresco cuando el equipo se ahoga.

    Los umbrales son de memoria **disponible**, no de porcentaje. Medido el 18
    sep 2026: esta maquina tiene 15,9 GB y en reposo ya esta en 75-82% de uso,
    o sea unos 3,5 GB disponibles. Con el umbral de recuperacion del 70% que
    tenia antes hacia falta bajar de 11 GB usados, cosa que no pasa ni estando
    en reposo: una vez que entraba en alerta no volvia mas. En absoluto, el
    reposo queda comodo por encima del umbral y la histeresis funciona.

    Con histeresis igual: avisa al cruzar un umbral, no en cada chequeo, y no
    oscila si la memoria queda justo en el limite.
    """

    ALERTA_GB = 1.2         # menos de esto disponible: el equipo esta ahogado
    RECUPERACION_GB = 2.2   # por encima de esto: respira otra vez
    INTERVALO_NORMAL = 3.0
    INTERVALO_ALERTA = 15.0

    def __init__(self, timer_metricas: ui.timer, intervalo: float = 10.0):
        self.timer_metricas = timer_metricas
        self.en_alerta = False
        self.timer = ui.timer(intervalo, self.revisar)

    @staticmethod
    def disponible_gb() -> float:
        return psutil.virtual_memory().available / 1024 ** 3

    @staticmethod
    def mas_golosos(cantidad: int = 3) -> str:
        """Los procesos que mas RAM ocupan, para que el aviso sirva de algo.

        Sin esto el aviso dice que falta memoria pero no de quien, y hay que
        salir a averiguarlo a mano justo cuando la maquina esta ahogada.
        """
        consumos = []
        for proceso in psutil.process_iter(['name', 'memory_info']):
            try:
                consumos.append((proceso.info['memory_info'].rss, proceso.info['name']))
            except (psutil.Error, AttributeError):
                continue  # proceso de otro usuario o del sistema: sin permiso
        mayores = sorted(consumos, reverse=True)[:cantidad]
        return ', '.join(f'{nombre} {rss / 1024 ** 3:.1f} GB' for rss, nombre in mayores)

    def revisar(self) -> None:
        ram = psutil.virtual_memory().percent
        disponible = self.disponible_gb()

        if not self.en_alerta and disponible < self.ALERTA_GB:
            self.en_alerta = True
            self.timer_metricas.interval = self.INTERVALO_ALERTA
            ui.notify(
                f'RAM al {ram:.0f}% (quedan {disponible:.1f} GB): bajo las métricas '
                f'a {self.INTERVALO_ALERTA:.0f}s · {self.mas_golosos()}',
                type='warning', position='top', timeout=12000,
            )
        elif self.en_alerta and disponible > self.RECUPERACION_GB:
            self.en_alerta = False
            self.timer_metricas.interval = self.INTERVALO_NORMAL
            ui.notify(
                f'RAM al {ram:.0f}% (quedan {disponible:.1f} GB): vuelvo a métricas '
                f'cada {self.INTERVALO_NORMAL:.0f}s',
                type='positive', position='top', timeout=8000,
            )


# --- Atajos globales ---------------------------------------------------------
# Ctrl+Alt+1..9 suben la ventana N de la organización, que es el número que
# muestra su tarjeta (y el que queda después de reordenar la tira). Ctrl+Alt+0
# reparte parejo y Ctrl+Alt+B esconde o trae la barra. Andan con cualquier
# ventana adelante, que es el punto: repartidas por la pantalla y con WorksheLL
# en una franja al pie, no hay dónde hacer clic para cambiar de ventana sin
# traer el panel primero.
#
# El registro se hace al importar y no al final: si alguna combinación ya la tiene
# otra app, la leyenda del panel lo dice desde el primer dibujo.
ATAJOS = {n: f'Ctrl+Alt+{n}' for n in range(1, 10)}
ATAJOS[10] = 'Ctrl+Alt+0'  # repartir parejo
ATAJOS[11] = 'Ctrl+Alt+B'  # esconder la barra / traerla

# Dos formas de que no se registren, y las dos importan: `WORKSHELL_SIN_ATAJOS=1`
# para una instancia de prueba (una segunda WorksheLL no puede quedarse con las
# teclas de la que ya está corriendo y menos mover ventanas de verdad), y no ser
# el programa principal: una prueba que importa WorksheLL —o cualquier script que
# lo use de biblioteca— no puede quedarse con el teclado de la máquina.
if os.environ.get('WORKSHELL_SIN_ATAJOS') == '1' or __name__ != '__main__':
    cola_atajos, atajos_fallidos = None, []
    if os.environ.get('WORKSHELL_SIN_ATAJOS') == '1':
        print('WORKSHELL_SIN_ATAJOS=1: no registro atajos globales')
else:
    cola_atajos, atajos_fallidos = ventanas.atajos_globales(ATAJOS)
    if atajos_fallidos:
        print(f'atajos que ya usa otra app: {atajos_fallidos}')


async def atender_atajo(ident: int) -> None:
    """Qué hace cada tecla global."""
    if 1 <= ident <= 9:
        if ident - 1 >= len(zonas_actuales):
            ui.notify(f'Ctrl+Alt+{ident}: esta organización no tiene esa ventana',
                      type='warning')
            return
        await foco_en(ident - 1)
    elif ident == 10:
        marcar_uso()
        await repartir_ventanas()
    else:
        await alternar_barra()


async def revisar_atajos() -> None:
    """Atiende las teclas apretadas desde el último repaso.

    El hilo que escucha (ver `ventanas.atajos_globales`) no puede tocar la
    interfaz: deja el identificador en la cola y acá se hace lo que corresponda,
    en el hilo de la interfaz y con el estado a la vista.
    """
    if cola_atajos is None:
        return
    while not cola_atajos.empty():
        await atender_atajo(cola_atajos.get())


# --- estilos ---
# `resize: both` necesita overflow != visible; el iframe con flex:1 sigue el alto del card.
ESTILO_PANEL = (
    'width: 32vw; height: 65vh; '
    'min-width: 240px; min-height: 220px; '
    'resize: both; overflow: hidden;'
)
ESTILO_METRICAS = (
    'width: 17rem; min-width: 170px; align-self: flex-start; '
    'resize: both; overflow: hidden;'
)
# El mapa de zonas necesita ancho: es una pantalla en miniatura, y con el ancho
# de los otros paneles las zonas no se pueden ni agarrar con el mouse.
#
# La altura es `auto` a proposito. El mapa mantiene el 16:9 (si no, un 60% de
# ancho no caeria donde corresponde en la pantalla real), asi que con una altura
# fija de 65vh el panel quedaba mas alto que su contenido y sobraba un hueco
# muerto abajo. Con la altura pegada al contenido, el panel mide lo que mide el
# mapa. `align-self` evita que la fila lo estire a la altura de los vecinos.
ESTILO_ESCRITORIO = (
    'width: 46vw; height: auto; align-self: flex-start; '
    'min-width: 320px; '
    'resize: both; overflow: hidden;'
)
# La tira de tarjetas es la vista del modo barra: ocupa todo el ancho disponible
# (la ventana pasa a medir 1920x260) y no lleva alto fijo, porque lo que manda es
# lo que mida la ventana.
ESTILO_TIRA = (
    'width: 100%; height: auto; align-self: flex-start; '
    'min-width: 320px; overflow: hidden;'
)
ESTILO_IFRAME = 'flex: 1; width: 100%; border: 0;'

# Un acento por panel. Validados como paleta categorica sobre fondo oscuro con el
# validador de dataviz (--pairs all): banda de luminosidad, piso de croma,
# separacion en daltonismo (peor par ΔE 8.0 deutan) y contraste, todo en verde.
#
# El turquesa del Escritorio se eligio midiendo candidatos contra los otros
# cuatro: queda a ΔE 17.9 (protanopia), 10.8 (deuteranopia) y 17.3 (vision
# normal) del acento mas parecido, con contraste 8.04 sobre la tarjeta. Se
# descartaron violeta y naranja, que se confunden con el azul (ΔE 0.41) y con
# el oro (ΔE 0.66) respectivamente.
ACENTOS = {
    'metricas': '#B08A24',   # oro
    'terminal': '#178A63',   # esmeralda
    'preview': '#CE4E93',    # magenta
    'trading': '#4C8DFF',    # azul
    'escritorio': '#3FBFB0',  # turquesa
}

# El cliente de ttyd trae su fondo incrustado y sus opciones -t no se aplican
# (probado: ni fontSize cambia), asi que el panel Terminal adopta ese mismo gris
# y el terminal se funde con su marco en vez de chocar.
FONDOS = {'terminal': '#2B2B2B'}


def con_alfa(color: str, alfa: float) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f'rgba({r}, {g}, {b}, {alfa})'


def aclarar(color: str, mezcla: float = 0.45) -> str:
    """Mezcla hacia blanco: los titulos necesitan mas contraste que el borde."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (round(c + (255 - c) * mezcla) for c in (r, g, b))
    return f'#{r:02X}{g:02X}{b:02X}'

# --- estetica ---
# Paleta validada con el validador de dataviz (modo oscuro, superficie #15151C):
# #B08A24 y #4C8DFF pasan banda de luminosidad, piso de croma, contraste y
# separacion para daltonismo (ΔE 29.5 en protanopia). El dorado brillante queda
# solo para titulo y bordes, que son decoracion, no marcas de datos.
FUENTES = (
    # Sin favicon, el navegador pide /favicon.ico en cada carga y se come un 404
    # al pedo (que en NiceGUI además tiene consecuencias, ver `no_existe`).
    '<link rel="icon" href="data:,">'
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700'
    '&family=Inter:wght@200;300;400;600&display=swap" rel="stylesheet">'
)

CSS = '''
:root {
  --oro: #B08A24;
  --oro-claro: #D9B65C;
  --azul: #4C8DFF;
  --azul-claro: #8FBAFF;
  --fondo: #0A0A0E;
  --tarjeta: #15151C;
  --borde: rgba(176, 138, 36, 0.28);
  --texto: #F2EFE6;
  --apagado: #8C8778;
}

body {
  background:
    radial-gradient(1100px 520px at 50% -10%, rgba(176, 138, 36, 0.10), transparent 70%),
    radial-gradient(900px 520px at 100% 105%, rgba(76, 141, 255, 0.08), transparent 70%),
    var(--fondo) !important;
  color: var(--texto) !important;
  font-family: 'Inter', system-ui, sans-serif !important;
}

.titulo {
  font-family: 'Cinzel', serif;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  background: linear-gradient(180deg, #FBF0C4 0%, #E3C260 42%, #B08A24 78%, #8A6A18 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  filter: drop-shadow(0 2px 14px rgba(176, 138, 36, 0.35));
}

.q-card {
  background: var(--fondo-panel, var(--tarjeta)) !important;
  border: 1px solid var(--acento-borde, var(--borde)) !important;
  border-radius: 14px !important;
  box-shadow: 0 14px 34px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05) !important;
}

.panel-titulo {
  font-family: 'Cinzel', serif;
  font-size: 0.86rem;
  font-weight: 600;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--acento-claro, var(--oro-claro));
}

.metrica-etiqueta {
  font-size: 0.66rem;
  font-weight: 600;
  letter-spacing: 0.24em;
  text-transform: uppercase;
}
.etiqueta-oro { color: var(--oro-claro); }
.etiqueta-azul { color: var(--azul-claro); }

.metrica-valor {
  font-size: 2.05rem;
  font-weight: 200;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--texto);
}
.metrica-unidad { font-size: 0.95rem; font-weight: 300; color: var(--apagado); }

.metrica-pista {
  height: 4px;
  border-radius: 999px;
  background: rgba(255,255,255,0.07);
  overflow: hidden;
}
.metrica-relleno {
  height: 100%;
  border-radius: 999px;
  width: 0%;
  transition: width 0.55s cubic-bezier(0.4, 0, 0.2, 1);
}
.relleno-oro  { background: linear-gradient(90deg, #6E5411, var(--oro) 65%, #C9A227 100%); }
.relleno-azul { background: linear-gradient(90deg, #1B3E8C, var(--azul) 65%, #7FB0FF 100%); }

.boton-oro {
  background: linear-gradient(180deg, #E8C97A, #B08A24) !important;
  color: #17130A !important;
  font-weight: 600 !important;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: 0.7rem !important;
  border-radius: 8px !important;
}
.boton-oro:hover { filter: brightness(1.1); }

.q-field--outlined .q-field__control { background: rgba(255,255,255,0.03); }
.q-field--outlined .q-field__control:before { border-color: rgba(176,138,36,0.35) !important; }

/* --- mapa de zonas del Escritorio --- */
/* Las zonas heredan --acento-borde y --acento-claro de su tarjeta, asi que el
   turquesa del panel llega solo y un panel nuevo no necesita CSS nuevo. */
.mapa-zonas {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  max-height: 100%;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  overflow: hidden;
  user-select: none;
}

.zona {
  position: absolute;
  padding: 0;
  border: 1px solid var(--acento-borde, var(--borde));
  border-radius: 6px;
  background: var(--acento-fondo, rgba(63, 191, 176, 0.14));
  color: var(--texto);
  font-size: 0.68rem;
  line-height: 1.15;
  overflow: hidden;
  cursor: grab;
}
.zona:hover { background: var(--acento-fondo-fuerte, rgba(63, 191, 176, 0.24)); }
.zona:active { cursor: grabbing; }
.zona-vacia { border-style: dashed; opacity: 0.6; }

/* La cara de la ventana, abajo de la barra. Sin `src` no se dibuja: una zona
   sin miniatura se ve como antes, con su fondo del acento. */
.zona-miniatura {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  pointer-events: none;
  display: none;
}
.zona-miniatura[src] { display: block; }
/* De una ventana minimizada no hay nada que capturar, asi que conserva su
   ultima cara pero apagada: el punto ambar ya dice que no esta a la vista, y
   una imagen a full mentiria. */
.zona-miniatura-vieja { opacity: 0.35; filter: grayscale(0.6); }

/* La barra del nombre, arriba de la miniatura. Con fondo propio porque arriba
   de una captura clara (una pagina en blanco de Chrome) el texto suelto
   desaparecia. */
.zona-barra {
  position: absolute;
  top: 0; left: 0; right: 0;
  display: flex;
  align-items: flex-start;
  padding: 4px 7px;
  background: linear-gradient(rgba(8, 20, 22, 0.88), rgba(8, 20, 22, 0.28));
}

.zona-nombre {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  pointer-events: none;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
}

/* El punto de estado dice si esa ventana existe ahora mismo.
   Colores de la paleta de estado de la skill dataviz (good / warning) mas el
   gris apagado para «cerrada», que no es una alarma: es un dato que falta.
   Validados con su validador contra la superficie real de la zona (#1B2D31 en
   reposo, #1F3E40 en hover): los tres pasan 3:1 y el peor par queda a ΔE 10.7
   en deuteranopia, por encima del objetivo 8. El verde ademas queda a ΔE 16.8
   del turquesa del acento, asi que el punto no se lee como el acento.
   Igual no alcanza con el color: verde y ambar son justo el par que se
   confunde con daltonismo, asi que cambia tambien la forma — lleno, medio
   lleno, hueco — y el title de cada punto lo dice con palabras. */
.zona-estado {
  flex: none;
  width: 8px;
  height: 8px;
  margin: 3px 0 0 6px;
  border-radius: 50%;
  pointer-events: none;
}
.estado-abierta {
  background: #0CA30C;
  box-shadow: 0 0 5px rgba(12, 163, 12, 0.5);
}
.estado-minimizada {
  background: linear-gradient(90deg, #FAB219 50%, transparent 50%);
  box-shadow: inset 0 0 0 1px #FAB219;
}
.estado-cerrada {
  background: transparent;
  box-shadow: inset 0 0 0 1px #898781;
}
/* Falló: cuadrado rojo y no un círculo más. Medido, el rojo contra el verde de
   «abierta» queda a ΔE 3.4 en deuteranopía (y contra el ámbar a 21.7): o sea que
   el color solo no alcanza para distinguir «publicó» de «falló». La forma sí, y
   el texto del renglón lo dice con palabras. */
.estado-fallo {
  background: #D64F4F;
  border-radius: 2px;
  box-shadow: 0 0 5px rgba(214, 79, 79, 0.45);
}
/* Sin asignar no lleva punto: el borde punteado ya dice que la zona esta vacia. */
.estado-sin-asignar { display: none; }

.zona-handle {
  position: absolute;
  right: 0; bottom: 0;
  width: 14px; height: 14px;
  cursor: nwse-resize;
  border-right: 2px solid var(--acento-claro, #3FBFB0);
  border-bottom: 2px solid var(--acento-claro, #3FBFB0);
}

.leyenda-zona { font-size: 0.66rem; color: var(--apagado); }

.mapa-vacio {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  text-align: center;
  font-size: 0.72rem;
  color: var(--apagado);
  pointer-events: none;
}

/* --- la tira de tarjetas del modo barra --- */
/* Una tarjeta por ventana, con su cara en vivo. Se parece a una zona del mapa
   a propósito: es la misma información en otro formato, y comparten el punto de
   estado y el acento del panel. */
.tira-tarjetas {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
}
/* Sin `overflow: hidden`: adentro de la tarjeta vive el menú del clic derecho, y
   recortar la tarjeta lo recortaría a él. Las esquinas de arriba las redondea la
   cara, que es la que llegaba hasta el borde. */
.tarjeta {
  flex: none;
  width: 188px;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--acento-borde, var(--borde));
  border-radius: 8px;
  background: var(--acento-fondo, rgba(63, 191, 176, 0.14));
  cursor: pointer;
  transition: transform 0.16s ease-out, box-shadow 0.16s ease-out,
              background 0.16s ease-out, border-color 0.16s ease-out;
}
/* Se levanta al pasar por encima: dice cuál vas a elegir antes de apretarla. */
.tarjeta:hover {
  background: var(--acento-fondo-fuerte, rgba(63, 191, 176, 0.24));
  transform: translateY(-3px);
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.45);
}

/* Entran una tras otra —el retardo por posición lo pone Python— así se ve de
   dónde sale cada una en vez de aparecer la tira entera de golpe. Solo en el
   primer dibujo y al entrar al modo barra: la tira se redibuja con cada clic en
   una tarjeta, y animarla ahí sería un temblor constante. */
@keyframes tarjeta-entra {
  from { opacity: 0; transform: translateY(14px) scale(0.96); }
  to { opacity: 1; transform: none; }
}
.tarjeta.entra { animation: tarjeta-entra 0.3s cubic-bezier(0.2, 0.8, 0.3, 1) backwards; }

/* La elegida da un golpe de escala: es la que acaba de subir a la pantalla. */
@keyframes tarjeta-foco {
  0% { transform: scale(0.94); }
  60% { transform: scale(1.02); }
  100% { transform: none; }
}
.tarjeta.en-foco { animation: tarjeta-foco 0.34s ease-out; }

/* El punto late cuando cambia de estado: si una ventana se cerró o se minimizó,
   el ojo lo agarra sin tener que comparar colores con la memoria. Son tres
   animaciones iguales con nombre distinto porque una animación solo se reinicia
   cuando cambia su nombre: con una sola, pasar de abierta a minimizada no
   latiría. */
.zona-estado.estado-abierta { animation: late-abierta 0.45s ease-out; }
.zona-estado.estado-minimizada { animation: late-minimizada 0.45s ease-out; }
.zona-estado.estado-cerrada { animation: late-cerrada 0.45s ease-out; }
@keyframes late-abierta { from { transform: scale(1.9); opacity: 0.35; } to { transform: none; } }
@keyframes late-minimizada { from { transform: scale(1.9); opacity: 0.35; } to { transform: none; } }
@keyframes late-cerrada { from { transform: scale(1.9); opacity: 0.35; } to { transform: none; } }

/* --- lo publicado --- */
.publicado-resumen { font-size: 0.8rem; }
.chip-publicacion {
  font-size: 0.7rem;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--borde);
  color: var(--apagado);
}
.chip-publicacion.chip-al-dia { color: #0CA30C; border-color: rgba(12, 163, 12, 0.45); }
.chip-publicacion.chip-con-falla { color: #D64F4F; border-color: rgba(214, 79, 79, 0.5); }

/* --- los servicios y el trading --- */
.servicio-fila { padding: 1px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.06); }
.servicio-nombre { font-size: 0.78rem; line-height: 1.25; }
.mt5-titulo { font-size: 0.72rem; color: var(--apagado); }
.mt5-numero { font-size: 0.86rem; font-variant-numeric: tabular-nums; }

/* --- la libreta --- */
.libreta-grupo {
  font-size: 0.6rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--oro-claro);
  margin-top: 3px;
}
.libreta-fila { padding: 1px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.06); }
.libreta-texto { font-size: 0.78rem; line-height: 1.25; }
.libreta-hecha .libreta-texto { text-decoration: line-through; opacity: 0.5; }
.libreta-tilde { margin: 0; }

/* A quien le molesta el movimiento, no se le mueve nada. */
@media (prefers-reduced-motion: reduce) {
  .tarjeta, .tarjeta.entra, .tarjeta.en-foco, .zona-estado {
    animation: none !important;
    transition: none !important;
  }
  .tarjeta:hover { transform: none; }
}
/* La que se está arrastrando para reordenar: se ve levantada y no cualquier otra. */
.tarjeta.arrastrando {
  opacity: 0.65;
  box-shadow: 0 0 0 2px var(--oro);
  cursor: grabbing;
}
.tarjeta-cara {
  width: 100%;
  height: 84px;
  object-fit: cover;
  object-position: top center;
  background: rgba(0, 0, 0, 0.25);
  display: block;
  pointer-events: none;
  border-radius: 7px 7px 0 0;
}
/* El número de la tarjeta: es su lugar en la tira y, hasta el 9, su tecla. */
.tarjeta-num {
  flex: none;
  min-width: 1.05rem;
  padding: 0 3px;
  border-radius: 4px;
  background: rgba(176, 138, 36, 0.22);
  color: var(--oro-claro, var(--oro));
  font-size: 0.6rem;
  line-height: 1.35;
  text-align: center;
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}
.tarjeta-pie {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 7px 5px;
}
.tarjeta-nombre {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.66rem;
  pointer-events: none;
}
/* La tarjeta elegida es la que está agrandada en pantalla: se marca con el oro
   del panel y un borde, no solo con color de fondo. */
.tarjeta.en-foco {
  border-color: var(--oro);
  box-shadow: 0 0 0 1px var(--oro);
}

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(176,138,36,0.3); border-radius: 999px; }
'''

# TradingView manda `frame-ancestors 'none'` en www: el chart completo NO se puede embeber.
# El endpoint de widgets sí, así que el panel usa ese (símbolo e intervalo configurables).
URL_TRADING = (
    'https://s.tradingview.com/widgetembed/?symbol=NASDAQ%3AAAPL'
    '&interval=60&theme=dark&style=1&locale=es'
)


def panel(clave: str, titulo: str, estilo: str = ESTILO_PANEL) -> ui.card:
    """Card con el acento propio de su panel: borde y titulo en ese color."""
    acento = ACENTOS.get(clave, ACENTOS['metricas'])
    fondo = FONDOS.get(clave)
    card = ui.card().style(
        f'{estilo} '
        f'--acento: {acento}; '
        f'--acento-borde: {con_alfa(acento, 0.5)}; '
        f'--acento-claro: {aclarar(acento)}; '
        f'--acento-fondo: {con_alfa(acento, 0.14)}; '
        f'--acento-fondo-fuerte: {con_alfa(acento, 0.24)};'
        + (f' --fondo-panel: {fondo};' if fondo else '')
    )
    with card:
        ui.label(titulo).classes('panel-titulo')
    paneles[clave] = card
    return card


def metrica(etiqueta: str, clase_etiqueta: str, clase_relleno: str):
    """Stat tile: la etiqueta lleva el color, el numero va en tinta de texto."""
    with ui.element('div').classes('w-full flex flex-col gap-2'):
        ui.label(etiqueta).classes(f'metrica-etiqueta {clase_etiqueta}')
        with ui.element('div').classes('flex items-baseline gap-1'):
            valor = ui.label('--').classes('metrica-valor')
            ui.label('%').classes('metrica-unidad')
        with ui.element('div').classes('metrica-pista w-full'):
            relleno = ui.element('div').classes(f'metrica-relleno {clase_relleno}')
    return valor, relleno


# --- UI ---
ui.dark_mode(True)
ui.add_head_html(FUENTES)
ui.add_css(CSS)

# El encabezado se guarda para poder esconderlo: en modo barra la ventana mide
# 260 px de alto y el título con el selector se comería la tira.
with ui.row().classes('w-full items-center justify-between') as encabezado:
    with ui.row().classes('items-center gap-3'):
        ui.label(TITULO).classes('titulo text-4xl')
        # El resumen de lo publicado, al lado del título: es lo que se mira de
        # reojo sin abrir ningún panel. El detalle está en «Publicaron hoy».
        chip_publicacion = ui.label('').classes('chip-publicacion')
        selector = ui.select(
            list(layouts),
            value=next(iter(layouts)),
            label='Layout',
            on_change=lambda e: cambiar_layout(e.value),
        ).classes('w-56').props('dense outlined')
        ui.button('Guardar como…', on_click=lambda: guardar_como()) \
            .props('dense flat').classes('boton-oro')
        ui.button('Renombrar', on_click=lambda: renombrar_layout()) \
            .props('dense flat').classes('boton-oro')
        ui.button('Eliminar', on_click=lambda: borrar_layout()) \
            .props('dense flat').classes('boton-oro')

with ui.row().classes('w-full items-stretch gap-4 flex-wrap'):
    with panel('metricas', 'Métricas', ESTILO_METRICAS):
        cpu_valor, cpu_relleno = metrica('CPU', 'etiqueta-oro', 'relleno-oro')
        ram_valor, ram_relleno = metrica('RAM', 'etiqueta-azul', 'relleno-azul')

    # La libreta: anotar algo sin abrir otra app, con lo de hoy arriba. Nace de
    # lo que pidió el dueño el 19 sep 2026: «que el panel me ayude de verdad».
    with panel('pendientes', 'Pendientes'):
        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            nueva_tarea = ui.input(placeholder='Anotar algo… · 17:30 llamar a X') \
                .props('dense outlined').classes('flex-1')
            ui.button('Anotar', on_click=lambda: anotar()) \
                .props('dense unelevated').classes('boton-oro')
        nueva_tarea.on('keydown.enter', lambda: anotar())
        lista_pendientes = ui.column().classes('w-full gap-1')
        ui.label('Queda guardado en pendientes.json y sobrevive al reinicio · si '
                 'arranca con una hora, WorksheLL te avisa a esa hora'
                 ).classes('leyenda-zona')

    # Los servicios de la casa: qué está levantado y qué no. El caso que lo
    # justifica es FreeLLMAPI: cuando se cae, la publicación no sale y no hay
    # ningún cartel que lo diga —el síntoma es «hoy no publicó»—.
    with panel('servicios', 'Servicios'):
        ui.button('Levantar lo que falta', on_click=lambda: pedir_levantar_faltantes()) \
            .props('dense flat').classes('boton-oro') \
            .tooltip('Muestra qué está caído y lo levanta: cada servicio con su '
                     'lanzador de servicios.json')
        lista_servicios = ui.column().classes('w-full gap-1')

    # ¿Publicaron hoy? La misma pregunta que se contestaba corriendo /revisar a
    # mano. Los renglones salen de los archivos de estado y de los logs, no de
    # una lista escrita a mano (ver `estado_publicaciones`).
    with panel('publicados', 'Publicaron hoy'):
        with ui.row().classes('w-full items-center gap-2 flex-wrap'):
            resumen_publicacion = ui.label('—').classes('publicado-resumen')
            ui.button('Revisar ahora', on_click=lambda: actualizar_publicaciones()) \
                .props('dense flat size=sm').classes('boton-oro') \
                .tooltip('Vuelve a leer los logs y los archivos de estado')
        lista_publicados = ui.column().classes('w-full gap-1')

    # El trading, de reojo: los números que el MT5 MultiAgente ya publica por
    # HTTP. No es para operar —eso es el dashboard— sino para no cambiar de
    # ventana cada vez que querés saber cómo va.
    with panel('mt5', 'MT5 MultiAgente'):
        mt5_titulo = ui.label('—').classes('mt5-titulo')
        with ui.row().classes('w-full items-center gap-4 flex-wrap') as mt5_numeros:
            pass
        lista_posiciones = ui.column().classes('w-full gap-0')
        ui.button('Abrir el dashboard',
                  on_click=lambda: abrir_en_navegador('http://localhost:8050')) \
            .props('dense flat').classes('boton-oro') \
            .tooltip('Abre la vista completa del trading en el navegador')

    with panel('terminal', 'Terminal'):
        ui.element('iframe').props('src="http://localhost:7681"').style(ESTILO_IFRAME)

    with panel('preview', 'Preview'):
        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            url = ui.input(placeholder='https://...').props('dense outlined').classes('flex-1')
            boton = ui.button('Cargar').props('dense unelevated').classes('boton-oro')
        vista = ui.element('iframe').style(ESTILO_IFRAME)

    with panel('trading', 'Trading'):
        ui.element('iframe').props(f'src="{URL_TRADING}"').style(ESTILO_IFRAME)

    with panel('escritorio', 'Escritorio', ESTILO_ESCRITORIO):
        with ui.element('div').classes('mapa-zonas') as mapa:
            pass
        with ui.row().classes('w-full items-center gap-2 flex-wrap'):
            ui.button('Sumar ventana…', on_click=lambda: sumar_ventana()) \
                .props('dense unelevated').classes('boton-oro') \
                .tooltip('Aparta el panel y te deja el escritorio: abrí lo que '
                         'quieras y vuelve solo, con todo repartido de nuevo')
            ui.button('Barra al pie', on_click=lambda: modo_barra(True)) \
                .props('dense flat').classes('boton-oro') \
                .tooltip('Achica el panel a una franja abajo, con una tarjeta por '
                         'ventana: la pantalla queda para ellas')
            ui.button('Aplicar', on_click=lambda: aplicar_disposicion()) \
                .props('dense unelevated').classes('boton-oro')
            ui.button('Acomodar todo', on_click=lambda: acomodar_todo()) \
                .props('dense unelevated').classes('boton-oro')
            ui.button('Releer', on_click=lambda: sincronizar_posiciones()) \
                .props('dense flat').classes('boton-oro')
        with ui.row().classes('w-full items-center gap-2 flex-wrap'):
            ui.button('Zona nueva', on_click=lambda: agregar_zona()) \
                .props('dense flat').classes('boton-oro')
            ui.button('Capturar al frente', on_click=lambda: capturar_al_frente()) \
                .props('dense flat').classes('boton-oro')
            boton_miniaturas = ui.button(
                'Miniaturas: sí' if ajustes['miniaturas'] else 'Miniaturas: no',
                on_click=lambda: alternar_miniaturas()) \
                .props('dense flat').classes('boton-oro')
            boton_ignoradas = ui.button(
                f'Ignoradas ({len(ignoradas)})' if ignoradas else 'Ignoradas',
                on_click=lambda: ver_ignoradas()) \
                .props('dense flat').classes('boton-oro')
        ui.label('¿Abrís algo nuevo? «Sumar ventana…»: el panel se aparta, te deja el '
                 'escritorio y vuelve solo con la ventana ya repartida'
                 ).classes('leyenda-zona')
        ui.label('Arrastrá para mover · la esquina para redimensionar · clic para asignar una app'
                 ).classes('leyenda-zona')
        ui.label('El punto dice si su ventana está abierta: lleno sí, medio minimizada, '
                 'hueco no la encuentro').classes('leyenda-zona')
        ui.label('Cada zona muestra la ventana de verdad, en vivo; se apaga con '
                 '«Miniaturas» si te come CPU').classes('leyenda-zona')
        ui.label('«Acomodar todo» reparte lo que está abierto y le busca el acceso directo '
                 'a cada app · «Aplicar» las lleva a su zona y abre lo que falte · «Releer» '
                 'trae sus posiciones reales').classes('leyenda-zona')
        ui.label('¿Te gustó cómo quedaron? «Guardar como…» anota esta disposición en una '
                 'organización nueva, y «Aplicar» la vuelve a armar igual cuando quieras'
                 ).classes('leyenda-zona')
        ui.label('Una zona se saca con clic → «Eliminar zona»; si además no querés que '
                 '«Acomodar todo» la reponga, usá «Sacar y no volver a acomodar» (la lista '
                 'se ve en «Ignoradas»)').classes('leyenda-zona')
        ui.label('«Barra al pie» achica el panel a una franja abajo: elegís ventanas por '
                 'tarjeta y la pantalla queda para ellas').classes('leyenda-zona')
        ui.label('Ctrl+Alt+1…9 suben la ventana de ese número · Ctrl+Alt+0 reparte '
                 'parejo · Ctrl+Alt+B esconde o trae la barra (andan con cualquier '
                 'ventana adelante)').classes('leyenda-zona')
        if atajos_fallidos:
            ui.label(f'Ojo: no pude registrar {", ".join(atajos_fallidos)} — los '
                     f'tiene tomados otra app').classes('leyenda-zona text-warning')

    # --- La tira de tarjetas (modo barra) ---
    # Es la vista que se ve cuando WorksheLL es una franja al pie: una tarjeta por
    # ventana, con su cara en vivo, para elegir cuál sube. En el panel completo
    # queda oculta salvo que un layout la pida.
    with panel('tarjetas', 'Ventanas', ESTILO_TIRA):
        with ui.element('div').classes('tira-tarjetas') as tira:
            pass
        with ui.row().classes('w-full items-center gap-2 flex-wrap'):
            ui.button('Repartir', on_click=lambda: repartir_ventanas()) \
                .props('dense unelevated').classes('boton-oro') \
                .tooltip('Vuelve a repartir parejo, sin ninguna agrandada')
            ui.button('Listo, escondé la barra', on_click=lambda: repartir_y_esconder()) \
                .props('dense unelevated').classes('boton-oro') \
                .tooltip('Reparte sobre toda la pantalla y minimiza la barra: '
                         'vuelve sola cuando la traigas')
            ui.button('Panel completo', on_click=lambda: modo_barra(False)) \
                .props('dense flat').classes('boton-oro') \
                .tooltip('Devuelve el panel a su tamaño, con todos sus paneles')
            boton_auto = ui.button(
                f'Auto: {barra["auto"]:.0f} s' if barra['auto'] else 'Auto: no',
                on_click=lambda: alternar_auto()) \
                .props('dense flat').classes('boton-oro') \
                .tooltip('La barra se corre sola cuando no la usás y vuelve si '
                         'dejás el mouse contra el borde de abajo. Apagado, se '
                         'queda hasta que la escondas vos')
            boton_opacidad = ui.button(
                'Opacidad: sólida' if barra['opacidad'] >= 255
                else f'Opacidad: {barra["opacidad"] / 255 * 100:.0f}%',
                on_click=lambda: alternar_opacidad()) \
                .props('dense flat').classes('boton-oro') \
                .tooltip('La barra se ve a través suyo, así no tapa lo que tiene '
                         'abajo. Recorre sólida → 87% → 75% → 60%')
            ui.label('Clic derecho en una tarjeta: traer al frente, minimizar, '
                     'cerrar, sacarla del mapa · arrastralas para cambiar el orden'
                     ).classes('leyenda-zona')


# --- Escritorio: zonas de ventanas ---
# El navegador no puede mostrar una ventana nativa adentro (MT5, Codex, un
# Chrome): eso lo decide el sistema operativo. Asi que WorksheLL no las
# contiene, las acomoda. El mapa es la pantalla en miniatura y cada zona guarda
# un rectangulo en porcentajes, que sigue sirviendo si cambia la resolucion.
#
# El arrastre vive en JavaScript y avisa por eventos; Python solo guarda el
# resultado. Hacerlo al reves (redibujar en cada cuadro) daria un arrastre a
# los saltos, porque cada ida y vuelta al servidor cuesta.
def clave_de(zona: dict) -> tuple[str, str]:
    """Con qué ventana se corresponde una zona, para no pintarle la cara de otra."""
    return (zona.get('proceso', ''), zona.get('titulo', ''))


def dibujar_servicios() -> None:
    """Dibuja una fila por servicio. El estado lo pinta `actualizar_servicios`."""
    lista_servicios.clear()
    filas_servicios.clear()
    with lista_servicios:
        for i, servicio in enumerate(servicios):
            with ui.row().classes('w-full items-center gap-2 flex-nowrap servicio-fila'):
                punto = ui.element('div').classes('zona-estado')
                with ui.column().classes('gap-0 flex-1 min-w-0'):
                    ui.label(servicio['nombre']).classes('servicio-nombre') \
                        .tooltip(servicio.get('nota') or servicio['nombre'])
                    if servicio.get('nota'):
                        ui.label(servicio['nota']).classes('leyenda-zona')
                ui.label(str(servicio['puerto'])).classes('leyenda-zona')
                abrir = ui.button('Abrir', on_click=lambda s=servicio: abrir_servicio(s)) \
                    .props('dense flat size=sm').classes('boton-oro')
                levantar = ui.button('Levantar', on_click=lambda i=i: levantar(i)) \
                    .props('dense flat size=sm').classes('boton-oro')
                filas_servicios.append({'punto': punto, 'abrir': abrir,
                                        'levantar': levantar, 'arriba': None})
    # Los puntos los pinta `actualizar_servicios`, que corre enseguida y cada
    # 10 s (ver los timers): acá no se pregunta nada, para no demorar el dibujo.


async def actualizar_servicios() -> None:
    """Pinta el estado de cada servicio.

    No redibuja la lista: solo el punto y los botones, por el mismo motivo que
    los puntos del mapa (un redibujo corta lo que estés haciendo).
    """
    if not filas_servicios:
        return
    if 'servicios' not in layouts.get(layout_actual, PANTALLA_VACIA)['paneles']:
        return  # el panel no se ve: no gastes las conexiones
    estados = await run.io_bound(estado_servicios, [dict(s) for s in servicios])
    for fila, arriba in zip(filas_servicios, estados):
        if fila['arriba'] == arriba:
            continue  # solo se toca el DOM cuando cambia algo
        fila['arriba'] = arriba
        fila['punto'].classes(
            replace=f'zona-estado estado-{"abierta" if arriba else "cerrada"}')
        fila['punto'].props(f'title="{"escuchando en el puerto" if arriba else "no está levantado"}"')
        fila['abrir'].set_enabled(arriba)
        fila['levantar'].set_enabled(not arriba)


def abrir_servicio(servicio: dict) -> None:
    """Abre la URL del servicio en el navegador de siempre."""
    url = (servicio.get('url') or '').strip()
    if url:
        abrir_en_navegador(url)


def abrir_en_navegador(url: str) -> None:
    """Abre una URL afuera del panel.

    Desde el servidor y no con `window.open`: WorksheLL corre en modo app (sin
    barra ni pestañas) y una ventana emergente ahí adentro queda rara.
    """
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001  (webbrowser levanta de todo)
        ui.notify(f'No pude abrir {url}: {exc}', type='warning')


async def levantar(idx: int) -> None:
    """Corre el lanzador de ese servicio y avisa."""
    if not 0 <= idx < len(servicios):
        return
    servicio = servicios[idx]
    ok, motivo = await run.io_bound(levantar_servicio, dict(servicio))
    if not ok:
        ui.notify(f'{servicio["nombre"]}: {motivo}', type='warning')
        return
    print(f'levanté {servicio["nombre"]} ({servicio["puerto"]})')
    ui.notify(f'{servicio["nombre"]}: lo lancé. Dale unos segundos y mirá el punto',
              type='info', timeout=6000)
    # Los servicios tardan en atarse al puerto (MT5 abre el terminal y espera):
    # dos repasos, uno corto y uno largo, y el punto dice la verdad.
    ui.timer(6.0, actualizar_servicios, once=True)
    ui.timer(20.0, actualizar_servicios, once=True)


def pedir_levantar_faltantes() -> None:
    """Pregunta antes de levantar: son programas abriéndose en tu máquina."""
    with ui.dialog() as dialogo, ui.card().classes('w-[32rem] max-w-full gap-3'):
        ui.label('Levantar los servicios caídos').classes('panel-titulo')
        ui.label('Mirá cuáles son antes de que se abran.').classes('leyenda-zona')
        with ui.column().classes('w-full gap-0') as detalle:
            pass
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=dialogo.close).props('dense flat')
            ui.button('Levantarlos', on_click=lambda: hacerlo()) \
                .props('dense unelevated').classes('boton-oro')

    async def hacerlo() -> None:
        dialogo.close()
        await levantar_faltantes()

    async def cargar() -> None:
        estados = await run.io_bound(estado_servicios, [dict(s) for s in servicios])
        caidos = [s for s, arriba in zip(servicios, estados)
                  if not arriba and s.get('lanzar')]
        detalle.clear()
        with detalle:
            if not caidos:
                ui.label('No hay ninguno caído: están todos levantados.'
                         ).classes('leyenda-zona')
                return
            for s in caidos:
                ui.label(f'· {s["nombre"]}  ({s["puerto"]})').classes('libreta-texto')
        return caidos

    dialogo.open()
    asyncio.create_task(cargar())


async def levantar_faltantes() -> None:
    """Levanta de una todos los que no están: el «arrancá el ecosistema»."""
    estados = await run.io_bound(estado_servicios, [dict(s) for s in servicios])
    caidos = [s for s, arriba in zip(servicios, estados) if not arriba and s.get('lanzar')]
    if not caidos:
        ui.notify('Están todos levantados', type='positive')
        return
    for servicio in caidos:
        ok, motivo = await run.io_bound(levantar_servicio, dict(servicio))
        print(f'levantar {servicio["nombre"]}: {"ok" if ok else motivo}')
    ui.notify('Levanté: ' + ', '.join(s['nombre'] for s in caidos),
              type='info', timeout=8000)
    ui.timer(25.0, actualizar_servicios, once=True)


# Cómo se ve cada estado de publicación: el punto verde ya quiere decir «está
# abierta / publicó» en el resto del panel, así que se reusa en vez de inventar
# otro código de colores.
CLASE_DE_PUBLICACION = {'ok': 'abierta', 'sin publicar': 'minimizada',
                        'falló': 'fallo', 'sin datos': 'cerrada'}


async def actualizar_publicaciones() -> None:
    """Relee los logs y los archivos de estado.

    Cada minuto alcanza: las corridas son cada horas, no cada segundo. Lo que sí
    importa es que el resumen de la cabecera esté al día, porque es lo que se
    mira de reojo sin abrir el panel.
    """
    filas = await run.io_bound(estado_publicaciones)
    dibujar_publicados(filas)


def dibujar_publicados(filas: list[dict]) -> None:
    """Un renglón por canal: el punto dice si publicó, el texto cuándo o por qué no."""
    lista_publicados.clear()
    publicaron = [f for f in filas if f['estado'] == 'ok']
    fallaron = [f for f in filas if f['estado'] == 'falló']
    resumen_publicacion.text = (f'{len(publicaron)} de {len(filas)} publicaron hoy'
                                + (f' · {len(fallaron)} con falla' if fallaron else ''))
    resumen_publicacion.classes(
        replace='publicado-resumen' + (' text-negative' if fallaron else ' text-positive'))
    detalle = ('\n'.join(
        f'{f["nombre"]}: {f["estado"]}' + (f' — {f["detalle"]}' if f['detalle'] else '')
        for f in filas) or 'sin datos todavía')
    resumen_publicacion.tooltip(detalle)
    chip_publicacion.text = resumen_publicacion.text
    chip_publicacion.classes(replace='chip-publicacion'
                             + (' chip-con-falla' if fallaron
                                else ' chip-al-dia' if publicaron else ''))
    chip_publicacion.tooltip(detalle)
    with lista_publicados:
        for fila in filas:
            with ui.row().classes('w-full items-center gap-2 flex-nowrap servicio-fila'):
                punto = ui.element('div').classes(
                    'zona-estado estado-' + CLASE_DE_PUBLICACION.get(fila['estado'], 'cerrada'))
                punto.props(f'title="{fila["estado"]}'
                            + (f': {fila["detalle"]}' if fila['detalle'] else '') + '"')
                with ui.column().classes('gap-0 flex-1 min-w-0'):
                    ui.label(fila['nombre']).classes('servicio-nombre')
                    if fila['detalle']:
                        ui.label(fila['detalle']).classes('leyenda-zona')


async def actualizar_mt5() -> None:
    """Trae los números del multiagente. Si no contesta, el panel lo dice."""
    if 'mt5' not in layouts.get(layout_actual, PANTALLA_VACIA)['paneles']:
        return
    dibujar_mt5(await run.io_bound(estado_mt5))


def dibujar_mt5(datos: dict) -> None:
    """Cuenta, plantel y posiciones abiertas, en tres renglones."""
    cuenta = datos.get('cuenta') or {}
    estado = datos.get('estado') or {}
    posiciones = datos.get('posiciones') or []

    if not cuenta and not estado:
        mt5_titulo.text = 'No contesta: revisá el gateway (8060) y el trader (8070)'
        mt5_numeros.clear()
        lista_posiciones.clear()
        return

    partes = [f'{cuenta.get("currency", "")} {cuenta.get("login", "")}'.strip(),
              ', '.join(estado.get('symbols') or [])]
    mt5_titulo.text = ' · '.join(p for p in partes if p) or 'MT5'
    mt5_numeros.clear()
    with mt5_numeros:
        if cuenta:
            numero('Equity', plata(cuenta.get('equity')), 'text-white')
            numero('Balance', plata(cuenta.get('balance')), 'text-white')
            numero('Flotante', plata(cuenta.get('profit')),
                   'text-positive' if (cuenta.get('profit') or 0) >= 0 else 'text-negative')
        if estado:
            numero('Agentes', f'{estado.get("agents_alive", 0)}/{estado.get("max_agents", 0)}',
                   'text-white')
            numero('Riesgo abierto', plata(estado.get('open_risk_amount')), 'text-white')
            numero('Mes', plata(estado.get('realized_month')),
                   'text-positive' if (estado.get('realized_month') or 0) >= 0
                   else 'text-negative')

    lista_posiciones.clear()
    with lista_posiciones:
        if not posiciones:
            ui.label('Sin posiciones abiertas.').classes('leyenda-zona')
            return
        ui.label(f'Posiciones abiertas ({len(posiciones)})').classes('libreta-grupo')
        for p in posiciones:
            ganancia = p.get('profit') or 0
            with ui.row().classes('w-full items-center gap-2 flex-nowrap servicio-fila'):
                # El volumen llega como float y así sale 3.4699999999999998: se
                # redondea a dos decimales y se le sacan los ceros de más.
                volumen = f'{float(p.get("volume") or 0):.2f}'.rstrip('0').rstrip('.')
                ui.label(f'{p.get("symbol", "?")} · {p.get("side", "?")} '
                         f'{volumen}').classes('servicio-nombre')
                ui.label(plata(ganancia)).classes(
                    'mt5-numero ' + ('text-positive' if ganancia >= 0 else 'text-negative'))


def numero(etiqueta: str, valor: str, clase: str) -> None:
    """Un número con su etiqueta arriba, para la fila de métricas del MT5."""
    with ui.column().classes('gap-0'):
        ui.label(etiqueta).classes('leyenda-zona')
        ui.label(valor).classes(f'mt5-numero {clase}')


def anotar() -> None:
    """Lo que escribiste en el cuadro de la libreta."""
    anotado = agregar_pendiente(nueva_tarea.value)
    if anotado is None:
        return
    nueva_tarea.value = ''
    dibujar_pendientes()
    if anotado['hora']:
        ui.notify(f'Anotado: te aviso a las {anotado["hora"]}', type='info')


def dibujar_pendientes() -> None:
    """Dibuja la libreta en tres grupos: hoy, lo que quedó de antes, y lo hecho.

    Se redibuja entera con cada cambio: son unas pocas filas, y así el orden
    (que depende de qué está hecho y de qué día es hoy) nunca queda viejo.
    """
    lista_pendientes.clear()
    with lista_pendientes:
        if not pendientes:
            ui.label('Nada anotado todavía: escribí arriba y apretá Anotar.'
                     ).classes('leyenda-zona')
            return
        grupos = (
            ('Hoy', lambda p: not p.get('hecho') and p.get('dia') == hoy()),
            ('De antes', lambda p: not p.get('hecho') and p.get('dia') != hoy()),
            ('Hechos', lambda p: bool(p.get('hecho'))),
        )
        for titulo, cual in grupos:
            filas = [(i, p) for i, p in ordenar_pendientes() if cual(p)]
            if not filas:
                continue
            ui.label(f'{titulo} ({len(filas)})').classes('libreta-grupo')
            for i, p in filas:
                clases = 'libreta-fila' + (' libreta-hecha' if p.get('hecho') else '')
                with ui.row().classes('w-full items-center gap-2 flex-nowrap ' + clases):
                    ui.checkbox(value=bool(p.get('hecho')),
                                on_change=lambda e, i=i: marcar(i, bool(e.value))) \
                        .props('dense').classes('libreta-tilde')
                    with ui.column().classes('gap-0 flex-1 min-w-0'):
                        ui.label(p['texto']).classes('libreta-texto')
                        # La hora del recordatorio, o el día si es de antes: es lo
                        # que hace falta saber de un vistazo.
                        cuando = p.get('hora') or (p.get('dia', '')
                                                   if p.get('dia') != hoy() else '')
                        if cuando:
                            ui.label(cuando).classes('leyenda-zona')
                    ui.button(icon='delete', on_click=lambda i=i: borrar(i)) \
                        .props('dense flat round size=sm color=grey') \
                        .tooltip('Borrar de la libreta')


def marcar(idx: int, hecho: bool) -> None:
    quitar_pendiente(idx, hecho)
    dibujar_pendientes()


def borrar(idx: int) -> None:
    quitar_pendiente(idx, None)
    dibujar_pendientes()


def rotulo_zona(idx: int) -> str:
    """Cómo se nombra una zona en un aviso: su etiqueta, o su ventana, o su número."""
    if not 0 <= idx < len(zonas_actuales):
        return f'Zona {idx + 1}'
    zona = zonas_actuales[idx]
    return zona.get('etiqueta') or zona.get('proceso') or f'Zona {idx + 1}'


def captura_de(i: int) -> bytes | None:
    """El JPEG de la zona `i`, si sigue siendo el de esa misma ventana.

    La comprobación importa porque las zonas se borran, se agregan y se cambia de
    layout, y los números se corren: sin esto, al cambiar de organización el mapa
    mostraría un rato las caras de la anterior.
    """
    datos = miniaturas.get(i)
    if datos is None or i >= len(zonas_actuales) or datos[0] != clave_de(zonas_actuales[i]):
        return None
    return datos[1]


def fuente(jpeg: bytes) -> str:
    """El `src` con los bytes adentro.

    Como `data:` y no por una ruta propia: WorksheLL tiene más de una instancia
    viva en el mismo proceso —la página la sirve el handler de 404 de NiceGUI,
    que vuelve a ejecutar el script— y una ruta la atendería la instancia
    equivocada, la que no capturó nada. El 19 sep 2026 pasó exactamente eso.
    """
    return f'src="data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"'


def pintar_miniaturas() -> None:
    """Pone en cada zona y en cada tarjeta la captura que ya haya. No captura.

    Se llama también después de redibujar: así una zona que se arrastra, o la
    tira entera cuando cambia el foco, no se quedan en blanco hasta el próximo
    refresco.
    """
    for i, imagen in enumerate(miniaturas_pintadas):
        jpeg = captura_de(i)
        if jpeg is None:
            imagen.props(remove='src')
            continue
        imagen.props(fuente(jpeg))
        apagada = ' zona-miniatura-vieja' if i in miniaturas_viejas else ''
        imagen.classes(replace=f'zona-miniatura{apagada}')
    for i, tarjeta in enumerate(tarjetas):
        jpeg = captura_de(i)
        if jpeg is None:
            tarjeta['imagen'].props(remove='src')
        else:
            tarjeta['imagen'].props(fuente(jpeg))


def renumerar_miniaturas(anteriores: list[dict]) -> None:
    """Reacomoda el caché de caras cuando las zonas cambian de número.

    El caché está numerado por zona, y reordenar la tira (o sacar una del medio)
    corre los números: sin esto, cada zona mostraría la cara de la de al lado
    hasta el próximo refresco. La clave (proceso, título) dice de quién es cada
    captura, así que se rearma por clave en vez de por número.
    """
    global miniaturas, miniaturas_viejas
    buscadas: dict[tuple[str, str], int] = {}
    for i, datos in miniaturas.items():
        if i < len(anteriores) and datos[0] == clave_de(anteriores[i]):
            buscadas.setdefault(datos[0], i)
    correspondencia = {}
    nuevo = {}
    for i, zona in enumerate(zonas_actuales):
        viejo = buscadas.get(clave_de(zona))
        if viejo is not None:
            nuevo[i] = miniaturas[viejo]
            correspondencia[i] = viejo
    miniaturas = nuevo
    miniaturas_viejas = {nuevo_i for nuevo_i, viejo in correspondencia.items()
                         if viejo in miniaturas_viejas}


def dibujar_tarjetas() -> None:
    """Redibuja la tira del modo barra: una tarjeta por zona."""
    global animar_tira
    tira.clear()
    tarjetas.clear()
    with tira:
        if not zonas_actuales:
            ui.label('Todavía no hay ventanas en esta organización.').classes('leyenda-zona')
        for i, zona in enumerate(zonas_actuales):
            clases = 'tarjeta en-foco' if barra['foco'] == i else 'tarjeta'
            if animar_tira:
                # El retardo por posición es lo que las hace entrar en fila.
                clases += ' entra'
            tarjeta = ui.element('div').classes(clases) \
                .on('click', lambda i=i: foco_en(i))
            if animar_tira:
                tarjeta.style(f'animation-delay: {min(i * 45, 400)}ms')
            with tarjeta as raiz:
                # Clic derecho: lo que antes obligaba a ir a buscar la ventana
                # por el Alt+Tab —minimizarla, cerrarla, sacarla del mapa— sin
                # salir de la tira.
                with ui.context_menu():
                    ui.menu_item('Traer al frente', lambda i=i: foco_en(i))
                    ui.menu_item('Minimizar', lambda i=i: minimizar_zona(i))
                    ui.menu_item('Cerrar ventana…', lambda i=i: pedir_cerrar(i))
                    ui.menu_item('Sacar del mapa', lambda i=i: quitar_zona(i))
                    ui.menu_item('Sacar y no volver a acomodar',
                                 lambda i=i: quitar_zona(i, ignorar=True))
                imagen = ui.element('img').classes('tarjeta-cara')
                with ui.element('div').classes('tarjeta-pie'):
                    # El número es el orden en la tira y, hasta el 9, la tecla
                    # que la sube: la tarjeta dice cuál es sin que haya que
                    # acordarse.
                    numero = i + 1 if i < 9 else None
                    placa = ui.label(str(i + 1)).classes('tarjeta-num')
                    if numero:
                        placa.tooltip(f'Ctrl+Alt+{i + 1} la trae adelante')
                    ui.label(zona.get('etiqueta') or 'vacía').classes('tarjeta-nombre')
                    punto = ui.element('div').classes('zona-estado tarjeta-punto')
                if numero:
                    raiz.tooltip(f'Ctrl+Alt+{i + 1} la sube · clic derecho para '
                                 f'más acciones')
                tarjetas.append({'raiz': raiz, 'imagen': imagen, 'punto': punto})
    # El redibujo que sigue (un clic, un reordenamiento) ya no anima la entrada:
    # las tarjetas se quedan quietas donde están.
    animar_tira = False


def dibujar_zonas() -> None:
    """Redibuja el mapa desde el modelo."""
    global generacion_mapa
    generacion_mapa += 1
    mapa.clear()
    puntos_estado.clear()
    estados_pintados.clear()
    miniaturas_pintadas.clear()
    with mapa:
        if not zonas_actuales:
            # Sin zonas no hay nada que arrastrar, asi que la leyenda de abajo
            # no explica como empezar. El mapa vacio lo dice en su lugar.
            with ui.element('div').classes('mapa-vacio'):
                ui.label('Tocá «Zona nueva» y después clic en la zona '
                         'para asignarle una ventana')

        for zona in zonas_actuales:
            vacia = not (zona.get('proceso') or zona.get('exe') or zona.get('lnk'))
            with ui.element('div').classes('zona zona-vacia' if vacia else 'zona').style(
                f'left: {zona.get("x", 0)}%; top: {zona.get("y", 0)}%; '
                f'width: {zona.get("ancho", 50)}%; height: {zona.get("alto", 50)}%;'
            ):
                # La cara de la ventana va abajo de todo: sin `src` no se ve, así
                # que una zona sin miniatura se dibuja como siempre.
                miniaturas_pintadas.append(ui.element('img').classes('zona-miniatura'))
                with ui.element('div').classes('zona-barra'):
                    ui.label(zona.get('etiqueta') or 'vacía').classes('zona-nombre')
                    puntos_estado.append(ui.element('div').classes('zona-estado'))
                estados_pintados.append('')
                ui.element('div').classes('zona-handle')
    # La tira del modo barra muestra las mismas zonas: se redibuja con ellas para
    # que las dos vistas nunca digan cosas distintas.
    dibujar_tarjetas()
    # Un redibujo borra los nodos viejos: las miniaturas que ya había se vuelven
    # a poner desde los bytes guardados, sin capturar de nuevo.
    pintar_miniaturas()


def guardar_zona(zona: dict) -> None:
    """Guarda el modelo en disco. El mapa en pantalla ya esta al dia."""
    etiqueta = zona.get('etiqueta') or 'zona'
    print(f'zona "{etiqueta}": {zona.get("x")}%,{zona.get("y")}% '
          f'{zona.get("ancho")}x{zona.get("alto")}%')
    guardar_layouts()


ESTADO_AYUDA = {
    'abierta': 'abierta ahora',
    'minimizada': 'está minimizada',
    'cerrada': 'no la encuentro (¿la cerraste?)',
    'sin-asignar': 'sin asignar',
}


async def refrescar_estados() -> None:
    """Pinta el punto de cada zona: abierta, minimizada, cerrada o sin asignar.

    Y de paso, si las miniaturas están prendidas, le saca la cara a cada
    ventana. Enumera las ventanas abiertas una sola vez para las dos cosas:
    enumerar abre cada proceso para leerle el nombre, es barato pero no gratis,
    así que va a un hilo aparte para no congelar la interfaz mientras cuenta.
    """
    if not puntos_estado:
        return
    if (not barra['activa']
            and 'escritorio' not in layouts.get(layout_actual, PANTALLA_VACIA)['paneles']):
        return  # ni el mapa ni la tira se ven: no gastes el barrido

    if barra['activa']:
        # Barato (pregunta por una sola ventana, la del panel) y va primero: si el
        # dueño trajo la barra de vuelta hay que correr las ventanas enseguida.
        await revisar_barra()

    generacion = generacion_mapa
    # Copia del modelo: el hilo no debe leer los diccionarios justo mientras el
    # arrastre les esta escribiendo el rectangulo nuevo.
    pares = await run.io_bound(ventanas.estado_y_ventanas,
                               [dict(z) for z in zonas_actuales])
    if generacion != generacion_mapa:
        return  # redibujaron el mapa mientras contaba: este refresco ya no sirve

    for i, (punto, (estado, _)) in enumerate(zip(puntos_estado, pares)):
        if estados_pintados[i] == estado:
            continue  # solo se toca el DOM cuando algo cambia de verdad
        estados_pintados[i] = estado
        punto.classes(replace=f'zona-estado estado-{estado}')
        punto.props(f'title="{ESTADO_AYUDA.get(estado, estado)}"')
        if i < len(tarjetas):  # la tira dice lo mismo que el mapa
            tarjeta = tarjetas[i]['punto']
            tarjeta.classes(replace=f'zona-estado tarjeta-punto estado-{estado}')
            tarjeta.props(f'title="{ESTADO_AYUDA.get(estado, estado)}"')

    if ajustes['miniaturas']:
        # Los puntos ya están pintados: la captura sale después y por su cuenta,
        # porque tarda mucho más que contar ventanas.
        await capturar_miniaturas(pares, generacion)


async def capturar_miniaturas(pares: list[tuple[str, dict | None]],
                              generacion: int) -> None:
    """Le saca una foto a cada ventana y la deja en su zona.

    Recibe los pares (estado, ventana) del barrido de estados: ya traen las
    ventanas, así que acá no se enumera nada más. Lo que no se pudo capturar no
    se inventa: una ventana cerrada se queda sin imagen, y una minimizada
    conserva la última que tuvo, apagada.
    """
    global miniaturas, miniaturas_viejas, miniaturas_version, capturando
    if capturando:
        return  # la tanda anterior todavía no volvió: no se apilan
    capturando = True
    try:
        nuevas = await run.io_bound(ventanas.capturar_zonas,
                                    [ventana for _, ventana in pares])
    finally:
        capturando = False
    if generacion != generacion_mapa:
        return  # redibujaron el mapa mientras capturaba: los índices son otros

    claves = [clave_de(z) for z in zonas_actuales]
    anteriores, miniaturas, miniaturas_viejas = miniaturas, {}, set()
    for i, (estado, _) in enumerate(pares):
        if i >= len(claves):
            continue
        if i in nuevas:
            miniaturas[i] = (claves[i], nuevas[i])
        elif (estado == 'minimizada' and i in anteriores
                and anteriores[i][0] == claves[i]):
            # De una minimizada no hay nada que capturar. Su última cara, media
            # apagada, dice más que un hueco: se ve qué era y que sigue ahí.
            miniaturas[i] = anteriores[i]
            miniaturas_viejas.add(i)
    miniaturas_version += 1
    pintar_miniaturas()


def al_arrastrar(datos: dict) -> None:
    """El arrastre termino: guarda el rectangulo nuevo."""
    idx = datos.get('idx')
    if not isinstance(idx, int) or not 0 <= idx < len(zonas_actuales):
        return
    zona = zonas_actuales[idx]
    for clave, limite in (('x', 0), ('y', 0), ('ancho', 5), ('alto', 5)):
        if clave in datos:
            zona[clave] = round(max(limite, float(datos[clave])), 2)
    guardar_zona(zona)


def al_hacer_clic(datos: dict) -> None:
    """Un clic sin arrastre: abrir el dialogo para asignarle una app a la zona."""
    idx = datos.get('idx')
    if isinstance(idx, int) and 0 <= idx < len(zonas_actuales):
        abrir_zona(idx, zonas_actuales[idx])


def al_reordenar(datos: dict) -> None:
    """Soltaron una tarjeta en otro lugar de la tira: ese es el orden nuevo.

    El navegador manda de dónde salió y dónde quedó —ya movida en el DOM—, y el
    modelo se reordena igual. El orden es el mismo que el del mapa y el de las
    teclas Ctrl+Alt+N, así que las tres cosas siguen diciendo lo mismo.

    No mueve ninguna ventana: reordenar la tira es cómo querés elegirlas, no
    dónde van en la pantalla.
    """
    de, a = datos.get('de'), datos.get('a')
    if not (isinstance(de, int) and isinstance(a, int)) or de == a:
        return
    if not (0 <= de < len(zonas_actuales) and 0 <= a < len(zonas_actuales)):
        return

    anteriores = [dict(z) for z in zonas_actuales]
    zonas_actuales.insert(a, zonas_actuales.pop(de))
    # La agrandada cambia de número, no de ventana: si el que se corrió pasó por
    # encima suyo, su índice se mueve con ella.
    if barra['foco'] is not None:
        if barra['foco'] == de:
            barra['foco'] = a
        elif de < barra['foco'] <= a:
            barra['foco'] -= 1
        elif a <= barra['foco'] < de:
            barra['foco'] += 1

    renumerar_miniaturas(anteriores)
    dibujar_zonas()
    guardar_layouts()
    print(f'tira: «{rotulo_zona(a)}» pasa al lugar {a + 1}')


def abrir_zona(idx: int, zona: dict) -> None:
    """Dialogo de asignacion: una ventana abierta o un acceso directo."""
    abiertas = ventanas.ventanas_abiertas()
    accesos = ventanas.accesos_escritorio(CARPETA)

    opciones_ventanas = {
        i: f'{v["proceso"]} · {(v["titulo"] or "(sin titulo)")[:46]}'
        for i, v in enumerate(abiertas)
    }
    opciones_accesos = {i: a['nombre'] for i, a in enumerate(accesos)}

    with ui.dialog() as dialogo, ui.card().classes('w-[42rem] max-w-full gap-4'):
        ui.label(zona.get('etiqueta') or f'Zona {idx + 1}').classes('panel-titulo')

        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            elegida = ui.select(opciones_ventanas, label='Ventana abierta ahora',
                                with_input=True).props('dense outlined').classes('flex-1')
            ui.button('Usar', on_click=lambda: usar_ventana(elegida.value)) \
                .props('dense unelevated').classes('boton-oro')

        with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
            acceso = ui.select(opciones_accesos, label='Acceso directo del Escritorio '
                               '(para que ademas sepa abrirla)',
                               with_input=True).props('dense outlined').classes('flex-1')
            ui.button('Usar', on_click=lambda: usar_acceso(acceso.value)) \
                .props('dense unelevated').classes('boton-oro')

        # Acciones sobre la ventana de esta zona, para no tener que ir a
        # buscarla por la barra de tareas ni moverla a mano.
        with ui.row().classes('w-full items-center gap-2 flex-wrap'):
            ui.button('Traer al frente', on_click=lambda: traer()) \
                .props('dense flat').classes('boton-oro')
            ui.button('Aplicar esta zona', on_click=lambda: aplicar()) \
                .props('dense flat').classes('boton-oro')
            ui.button('Releer posición', on_click=lambda: releer()) \
                .props('dense flat').classes('boton-oro')
            if zona.get('proceso'):
                # Borrar la zona ya es definitivo, pero «Acomodar todo» vuelve a
                # armar desde las ventanas abiertas: sin esto, la que sacaste
                # reaparece la próxima vez que repartas.
                ui.button('Sacar y no volver a acomodar', on_click=lambda: ignorar()) \
                    .props('dense flat').classes('boton-oro')

        with ui.row().classes('w-full items-center gap-2'):
            ui.button('Vaciar zona', on_click=lambda: vaciar()) \
                .props('dense flat').classes('boton-oro')
            ui.button('Eliminar zona', on_click=lambda: eliminar()) \
                .props('dense flat').classes('boton-oro')
            ui.space()
            ui.button('Listo', on_click=dialogo.close).props('dense flat')

        def cerrar(mensaje: str, tipo: str = 'positive') -> None:
            dialogo.close()
            dibujar_zonas()
            guardar_layouts()
            ui.notify(mensaje, type=tipo)

        def rotulo() -> str:
            return zona.get('etiqueta') or zona.get('proceso') or f'Zona {idx + 1}'

        def buscar_la_ventana() -> dict | None:
            """Se llama en un hilo aparte: enumerar ventanas cuesta unos ms."""
            return ventanas.resolver(zona)[0]

        async def traer() -> None:
            """Levanta la ventana de esta zona sin ir a buscarla al Alt+Tab."""
            ventana = await run.io_bound(buscar_la_ventana)
            if ventana is None:
                ui.notify(f'{rotulo()}: no la encuentro abierta', type='warning')
                return
            ok, motivo = await run.io_bound(ventanas.traer_al_frente, ventana)
            ui.notify(f'{rotulo()} al frente' if ok else f'{rotulo()}: {motivo}',
                      type='positive' if ok else 'negative')
            await refrescar_estados()

        async def aplicar() -> None:
            resultados = await run.io_bound(ventanas.aplicar, [zona])
            guardar_layouts()  # aplicar() aprende proceso y titulo de lo que abrio
            dibujar_zonas()
            resultado = resultados[0]
            ui.notify(f'{rotulo()}: acomodada' if resultado['ok']
                      else f'{rotulo()}: {resultado["motivo"]}',
                      type='positive' if resultado['ok'] else 'negative')
            await refrescar_estados()

        async def releer() -> None:
            """Trae la posicion real de la ventana y redibuja la zona ahi.

            Es el camino inverso a Aplicar: si la moviste a mano y quedo bien,
            esto la guarda tal como esta.
            """
            ventana = await run.io_bound(buscar_la_ventana)
            if ventana is None:
                ui.notify(f'{rotulo()}: no la encuentro abierta', type='warning')
                return
            if ventana['minimizada']:
                # Una minimizada reporta (-32000, -32000, 160, 28): leerla
                # mandaria la zona al rincon y le borraria el tamano.
                ui.notify(f'{rotulo()}: está minimizada, no puedo leer dónde va',
                          type='warning')
                return
            zona.update(ventanas.geometria_en_porcentaje(ventana['rect']))
            dibujar_zonas()
            guardar_zona(zona)
            ui.notify(f'{rotulo()}: posición releída', type='positive')

        def usar_ventana(i) -> None:
            if i is None:
                return
            v = abiertas[i]
            zona.update({
                'etiqueta': (v['titulo'] or v['proceso'])[:40],
                'proceso': v['proceso'],
                'titulo': v['titulo'],
                # No se guarda el ejecutable: saber cual ventana es no es lo
                # mismo que saber como abrirla (lanzar cmd.exe no devuelve esta
                # consola). Para eso esta la lista de accesos directos.
                'exe': '', 'args': '', 'lnk': '',
            })
            cerrar(f'Zona {idx + 1} → {v["proceso"]}')

        def usar_acceso(i) -> None:
            if i is None:
                return
            a = accesos[i]
            zona.update({
                'etiqueta': a['nombre'],
                'lnk': a['lnk'],
                'exe': a['exe'],
                'args': a.get('args', ''),
                'proceso': a.get('proceso', ''),
                'titulo': '',
            })
            cerrar(f'Zona {idx + 1} → {a["nombre"]}')

        def ignorar() -> None:
            """Saca la zona y anota la ventana para que no vuelva a entrar sola."""
            etiqueta = anotar_ignorada(zona) or rotulo()
            zonas_actuales.pop(idx)
            cerrar(f'«{etiqueta}» sale de la grilla y no vuelve', 'info')
            actualizar_boton_ignoradas()
            ui.notify('Se cambia cuando quieras en «Ignoradas»', type='info', timeout=5000)

        def vaciar() -> None:
            zona.update({'etiqueta': '', 'proceso': '', 'titulo': '',
                         'exe': '', 'args': '', 'lnk': ''})
            cerrar(f'Zona {idx + 1} vaciada', 'info')

        def eliminar() -> None:
            zonas_actuales.pop(idx)
            cerrar(f'Zona {idx + 1} eliminada', 'info')

    dialogo.open()


def _ocupado(x: float, y: float, ancho: float, alto: float) -> bool:
    """¿Ese rectángulo se superpone con alguna zona que ya está?"""
    return any(
        not (x + ancho <= z.get('x', 0) or z.get('x', 0) + z.get('ancho', 0) <= x
             or y + alto <= z.get('y', 0) or z.get('y', 0) + z.get('alto', 0) <= y)
        for z in zonas_actuales
    )


def _primer_lugar_libre(ancho: float, alto: float) -> tuple[float, float] | None:
    """Recorre el mapa en pasos finos y devuelve el primer lugar libre que entra.

    El paso es más chico que la zona a propósito: así encuentra también los
    huecos irregulares que dejan las zonas que el usuario movió a mano, en vez
    de exigir que caigan en una grilla.
    """
    y = 0.0
    while y + alto <= 100.0:
        x = 0.0
        while x + ancho <= 100.0:
            if not _ocupado(x, y, ancho, alto):
                return x, y
            x += PASO_BUSQUEDA
        y += PASO_BUSQUEDA
    return None


def siguiente_hueco(ancho: float = 40, alto: float = 40) -> tuple[float, float, float, float]:
    """Primer lugar libre del mapa. Devuelve (x, y, ancho, alto).

    Dos cosas que costaron caro:

    Antes se apilaban corridas unos puntos y quedaban una encima de la otra: al
    arrastrar, agarrabas la de arriba creyendo que movías la de abajo. Y no se
    miraba el borde del mapa: a partir de la quinta zona caían fuera (y=80 con
    alto 40, y=120 la séptima), donde no se ven ni se pueden agarrar con el
    mouse. Parecía que WorksheLL no dejaba tener más de cuatro zonas.

    Por eso el tamaño puede volver más chico que el pedido: si el mapa ya está
    lleno a 40, una zona de 20 que se ve y se puede arrastrar es mejor que una
    de 40 que no está.
    """
    for tamano in ((ancho, alto), (28.0, 28.0), (20.0, 20.0), (14.0, 14.0)):
        lugar = _primer_lugar_libre(*tamano)
        if lugar is not None:
            return (lugar[0], lugar[1], tamano[0], tamano[1])
    return 0.0, 0.0, 14.0, 14.0  # mapa lleno del todo: encima, pero a la vista


def agregar_zona() -> None:
    """Suma una zona en el primer lugar libre del mapa."""
    x, y, ancho, alto = siguiente_hueco()
    zonas_actuales.append({
        'etiqueta': '', 'proceso': '', 'titulo': '', 'exe': '', 'args': '', 'lnk': '',
        'x': x, 'y': y, 'ancho': ancho, 'alto': alto,
    })
    dibujar_zonas()
    guardar_layouts()
    if (ancho, alto) != (40.0, 40.0):
        ui.notify(f'El mapa está lleno: la puse de {ancho:.0f}% para que entre',
                  type='info', timeout=5000)


def capturar_al_frente() -> None:
    """Toma la ventana que este adelante dentro de unos segundos.

    Con retardo a proposito: apenas se toca el boton, la ventana al frente es
    WorksheLL mismo (el clic acaba de pasar por ahi), asi que hay que dar tiempo
    a cambiar de ventana antes de leer.
    """
    ui.notify('Poné al frente la ventana que querés capturar…', type='info', timeout=2500)
    ui.timer(3.0, capturar_ahora, once=True)


def capturar_ahora() -> None:
    ventana_al_frente = ventanas.al_frente()
    if ventana_al_frente is None:
        ui.notify('No pude leer la ventana al frente', type='warning')
        return

    # No se descarta a WorksheLL mismo: dejarlo en un cuarto de pantalla es
    # justamente lo que hace falta para que las ventanas que acomoda se vean.
    etiqueta = (ventana_al_frente['titulo'] or ventana_al_frente['proceso'])[:40]
    zonas_actuales.append({
        'etiqueta': etiqueta,
        'proceso': ventana_al_frente['proceso'],
        'titulo': ventana_al_frente['titulo'],
        'exe': '', 'args': '', 'lnk': '',
        **ventanas.geometria_en_porcentaje(ventana_al_frente['rect']),
    })
    dibujar_zonas()
    guardar_layouts()
    ui.notify(f'Capturé {etiqueta}', type='positive')


async def releer_en(zonas: list[dict]) -> tuple[int, int, int]:
    """Relee la posición real de la ventana de cada zona y la escribe en la zona.

    Devuelve (releidas, minimizadas, ausentes) para que cada llamada avise lo
    que le importa. Es la parte que comparten «Releer» —actualizar la
    organización en uso— y «Guardar como…», que desde el 21 sep 2026 captura la
    disposición tal como está en el escritorio y no las coordenadas que el
    layout tenía guardadas.

    Las zonas que recibe son las que termina escribiendo. El barrido corre en
    un hilo sobre una copia (instantánea) para no leer el modelo justo mientras
    un arrastre le está escribiendo el rectángulo.
    """
    encontradas = await run.io_bound(ventanas.ubicaciones, [dict(z) for z in zonas])

    releidas = minimizadas = ausentes = 0
    for zona, ventana in zip(zonas, encontradas):
        if ventana is None:
            ausentes += 1
        elif ventana['minimizada']:
            # Reporta (-32000, -32000, 160, 28): si se leyera, la zona se iría
            # al rincón y perdería su tamaño.
            minimizadas += 1
        else:
            zona.update(ventanas.geometria_en_porcentaje(ventana['rect']))
            releidas += 1
    return releidas, minimizadas, ausentes


async def sincronizar_posiciones() -> None:
    """Relee dónde está cada ventana asignada y mueve todas las zonas ahí.

    Es el camino inverso a «Aplicar»: sirve para acomodar las ventanas a mano,
    como queden cómodas, y guardar esa disposición de una sola vez en lugar de
    zona por zona.
    """
    asignadas = [z for z in zonas_actuales if z.get('proceso') or z.get('exe') or z.get('lnk')]
    if not asignadas:
        ui.notify('Este layout todavía no tiene ventanas asignadas', type='warning')
        return

    releidas, minimizadas, ausentes = await releer_en(asignadas)

    if releidas:
        dibujar_zonas()
        guardar_layouts()

    partes = [f'{releidas} de {len(asignadas)} zonas releídas']
    if minimizadas:
        partes.append(f'{minimizadas} minimizada(s)')
    if ausentes:
        partes.append(f'{ausentes} sin ventana')
    ui.notify(' · '.join(partes),
              type='positive' if releidas == len(asignadas) else 'warning')


def copia_del_layout(nombre: str) -> dict:
    """Una copia independiente de un layout, para no editar el original.

    Las zonas se copian una por una a propósito: compartir los diccionarios
    haría que mover una zona en la copia moviera también la del original.
    """
    elegido = layouts.get(nombre, PANTALLA_VACIA)
    return {
        'paneles': list(elegido['paneles']),
        'ventanas': [dict(zona) for zona in elegido['ventanas']],
    }


def renombrar_en(cuales: dict, anterior: str, nuevo: str) -> None:
    """Le cambia el nombre a una clave sin cambiarle el orden a las demás.

    Se rearma el diccionario en el lugar (y no se crea uno nuevo) porque el
    selector y `aplicar_layout` miran este mismo objeto: reemplazarlo dejaría a
    cada uno con su copia.
    """
    reordenado = {nuevo if clave == anterior else clave: valor
                  for clave, valor in cuales.items()}
    cuales.clear()
    cuales.update(reordenado)


def usar_layout(nombre: str) -> None:
    """Deja el selector y el mapa apuntando a ese layout."""
    selector.options = list(layouts)
    selector.value = nombre
    selector.update()
    aplicar_layout(nombre)
    guardar_layouts()


async def guardar_como() -> None:
    """Guarda la organización actual como un layout nuevo, con nombre.

    Es lo que permite tener varias: «Desarrollo», «Trading», «Edición»… Cada
    una guarda sus paneles, sus zonas y —si las tiene— sus accesos directos,
    que son los que hacen que Aplicar pueda reabrir lo que esté cerrado.

    Antes de copiar relee dónde están las ventanas ahora mismo: lo que queda
    guardado es la disposición tal como está en el escritorio («acomodá a mano,
    Guardar como, y después Aplicar la vuelve a armar igual»). La relectura va
    sobre la copia a propósito: el layout original conserva sus coordenadas.
    """
    with ui.dialog() as dialogo, ui.card().classes('gap-4'):
        ui.label('Guardar la organización actual como…').classes('panel-titulo')
        ui.label('Se guardan las ventanas tal como están ahora en el escritorio'
                 ).classes('leyenda-zona')
        nombre = ui.input(label='Nombre', value=f'{layout_actual} 2') \
            .props('dense outlined').classes('w-72')
        aviso = ui.label('').classes('leyenda-zona text-negative')

        async def confirmar() -> None:
            elegido = (nombre.value or '').strip()
            if not elegido:
                aviso.text = 'Poné un nombre.'
                return
            if elegido in layouts:
                aviso.text = f'Ya existe «{elegido}»: elegí otro nombre.'
                return
            nuevo = copia_del_layout(layout_actual)
            asignadas = [z for z in nuevo['ventanas']
                         if z.get('proceso') or z.get('exe') or z.get('lnk')]
            releidas = minimizadas = ausentes = 0
            if asignadas:
                releidas, minimizadas, ausentes = await releer_en(asignadas)
            layouts[elegido] = nuevo
            zonas = len(nuevo['ventanas'])
            dialogo.close()
            usar_layout(elegido)
            partes = [f'Layout «{elegido}» guardado con {zonas} zonas']
            if asignadas:
                partes.append(f'{releidas} con la posición de ahora')
                if minimizadas or ausentes:
                    partes.append(f'{minimizadas + ausentes} conservaron su '
                                  f'coordenada guardada')
            ui.notify(' · '.join(partes), type='positive')

        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=dialogo.close).props('dense flat')
            ui.button('Guardar', on_click=confirmar) \
                .props('dense unelevated').classes('boton-oro')
    dialogo.open()


async def renombrar_layout() -> None:
    """Le cambia el nombre al layout actual, conservando su lugar en la lista."""
    with ui.dialog() as dialogo, ui.card().classes('gap-4'):
        ui.label(f'Renombrar «{layout_actual}»').classes('panel-titulo')
        nombre = ui.input(label='Nombre nuevo', value=layout_actual) \
            .props('dense outlined').classes('w-72')
        aviso = ui.label('').classes('leyenda-zona text-negative')

        def confirmar() -> None:
            elegido = (nombre.value or '').strip()
            if not elegido:
                aviso.text = 'Poné un nombre.'
                return
            if elegido == layout_actual:
                dialogo.close()
                return
            if elegido in layouts:
                aviso.text = f'Ya existe «{elegido}»: elegí otro nombre.'
                return
            anterior = layout_actual
            renombrar_en(layouts, anterior, elegido)
            dialogo.close()
            usar_layout(elegido)
            ui.notify(f'«{anterior}» ahora se llama «{elegido}»', type='positive')

        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=dialogo.close).props('dense flat')
            ui.button('Renombrar', on_click=confirmar) \
                .props('dense unelevated').classes('boton-oro')
    dialogo.open()


async def borrar_layout() -> None:
    """Elimina el layout actual, con confirmación: no se puede deshacer."""
    if len(layouts) <= 1:
        ui.notify('Tiene que quedar al menos un layout', type='warning')
        return

    with ui.dialog() as dialogo, ui.card().classes('gap-4'):
        ui.label(f'Eliminar «{layout_actual}»').classes('panel-titulo')
        ui.label(f'Se van sus {len(zonas_actuales)} zonas y sus paneles. '
                 f'No se puede deshacer.').classes('leyenda-zona')

        def confirmar() -> None:
            anterior = layout_actual
            del layouts[anterior]
            dialogo.close()
            usar_layout(next(iter(layouts)))
            ui.notify(f'Layout «{anterior}» eliminado', type='info')

        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=dialogo.close).props('dense flat')
            ui.button('Eliminar', on_click=confirmar) \
                .props('dense unelevated').classes('boton-oro')
    dialogo.open()


def anotar_ignorada(zona: dict) -> str:
    """Suma la ventana de esa zona a la lista de «no acomodar». Devuelve su etiqueta.

    Vive fuera del diálogo para poder probarse: adentro de un cierre no hay
    forma de llamarla desde una prueba sin manejar la interfaz.
    """
    proceso = zona.get('proceso', '')
    if not proceso:
        return ''
    titulo = zona.get('titulo', '')
    etiqueta = zona.get('etiqueta') or proceso
    ya_estaba = any(str(r.get('proceso', '')).lower() == proceso.lower()
                    and r.get('titulo', '') == titulo for r in ignoradas)
    if not ya_estaba:
        ignoradas.append({'proceso': proceso, 'titulo': titulo, 'etiqueta': etiqueta})
        guardar_ignoradas()
    return etiqueta


def actualizar_boton_miniaturas() -> None:
    boton_miniaturas.text = 'Miniaturas: sí' if ajustes['miniaturas'] else 'Miniaturas: no'


async def alternar_miniaturas() -> None:
    """Prende o apaga la cara de las ventanas en el mapa.

    Se guarda en panel.json porque es una preferencia, y se puede apagar: para
    lo que no hace falta ver, capturar cada pocos segundos es gasto al pedo.
    """
    ajustes['miniaturas'] = not ajustes['miniaturas']
    guardar_ajustes()
    actualizar_boton_miniaturas()
    if ajustes['miniaturas']:
        await refrescar_estados()  # que aparezcan ya, sin esperar el tick
    else:
        miniaturas.clear()
        miniaturas_viejas.clear()
        pintar_miniaturas()


def actualizar_boton_ignoradas() -> None:
    """El botón dice cuántas hay: si no, la lista es invisible y parece no existir."""
    boton_ignoradas.text = f'Ignoradas ({len(ignoradas)})' if ignoradas else 'Ignoradas'


async def ver_ignoradas() -> None:
    """Muestra y edita la lista de ventanas que «Acomodar todo» no reparte.

    Borrar una zona ya es definitivo, pero «Acomodar todo» vuelve a armar desde
    las ventanas abiertas: esta lista es lo que hace que una ventana sacada no
    reaparezca la próxima vez.
    """
    with ui.dialog() as dialogo, ui.card().classes('w-[38rem] max-w-full gap-4'):
        ui.label('Ventanas que no se acomodan solas').classes('panel-titulo')
        if not ignoradas:
            ui.label('No hay ninguna: «Acomodar todo» reparte todo lo que esté '
                     'abierto. Se agregan desde el diálogo de una zona, con '
                     '«Sacar y no volver a acomodar».').classes('leyenda-zona')

        for cual, regla in enumerate(list(ignoradas)):
            with ui.row().classes('w-full items-center gap-2 flex-nowrap'):
                with ui.column().classes('gap-0').style('flex: 1; min-width: 0'):
                    ui.label(regla.get('etiqueta') or regla.get('proceso'))
                    ui.label(f"{regla.get('proceso')} · "
                             f"{(regla.get('titulo') or 'sin título')[:44]}") \
                        .classes('leyenda-zona')
                ui.button('Volver a acomodar', on_click=lambda c=cual: quitar(c)) \
                    .props('dense flat').classes('boton-oro')

        def quitar(cual: int) -> None:
            if not 0 <= cual < len(ignoradas):
                return
            fuera = ignoradas.pop(cual)
            guardar_ignoradas()
            dialogo.close()
            actualizar_boton_ignoradas()
            ui.notify(f'«{fuera.get("etiqueta") or fuera.get("proceso")}» vuelve a '
                      f'acomodarse', type='positive')

        def vaciar_todas() -> None:
            ignoradas.clear()
            guardar_ignoradas()
            dialogo.close()
            actualizar_boton_ignoradas()
            ui.notify('Lista vacía: se vuelve a acomodar todo', type='info')

        with ui.row().classes('w-full justify-end gap-2'):
            if ignoradas:
                ui.button('Vaciar la lista', on_click=lambda: vaciar_todas()) \
                    .props('dense flat').classes('boton-oro')
            ui.space()
            ui.button('Listo', on_click=dialogo.close).props('dense flat')
    dialogo.open()


async def acomodar_todo() -> None:
    """Arma el layout con las ventanas que están abiertas ahora y las acomoda.

    Es el punto de partida del panel: en vez de dibujar zona por zona, reparte
    todo lo abierto en una grilla — una zona por ventana — y le busca a cada una
    el acceso directo del Escritorio, que es lo que después permite que Aplicar
    la reabra sola.

    Pregunta antes de tocar nada, porque son dos decisiones que no son mías: si
    se reemplazan las zonas que ya hay (son trabajo del dueño, no un caché) y si
    entran también las minimizadas. Esa segunda importa acá: esta máquina suele
    tener más minimizadas que a la vista, y sin ellas «acomodar todo» reparte
    cuatro ventanas y parece que hubiera un tope.
    """
    reglas = [dict(r) for r in ignoradas]  # instantánea para el hilo
    a_la_vista = await run.io_bound(ventanas.para_acomodar, TITULO, False, reglas)
    con_dormidas = await run.io_bound(ventanas.para_acomodar, TITULO, True, reglas)
    dormidas = len(con_dormidas) - len(a_la_vista)

    with ui.dialog() as dialogo, ui.card().classes('gap-4'):
        ui.label('Repartir lo que está abierto').classes('panel-titulo')
        ui.label(f'Encontré {len(a_la_vista)} ventanas a la vista'
                 + (f' y {dormidas} minimizada(s).' if dormidas else '.')
                 ).classes('leyenda-zona')
        if reglas:
            ui.label(f'{len(reglas)} marcada(s) como «no acomodar»: no entran '
                     f'(se cambia con el botón «Ignoradas»).').classes('leyenda-zona')
        if zonas_actuales:
            ui.label(f'Reemplaza las {len(zonas_actuales)} zonas de «{layout_actual}»: '
                     f'si querés conservarlas, guardá una copia con «Guardar como…».') \
                .classes('leyenda-zona')

        incluir = None
        if dormidas:
            incluir = ui.checkbox(f'Incluir las {dormidas} minimizadas '
                                  f'(se van a restaurar)', value=True)

        async def repartir() -> None:
            dialogo.close()
            await armar_disposicion(bool(incluir.value) if incluir is not None else False)

        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=dialogo.close).props('dense flat')
            ui.button('Repartir', on_click=repartir) \
                .props('dense unelevated').classes('boton-oro')
    dialogo.open()


async def armar_disposicion(incluir_minimizadas: bool = False) -> None:
    """Reparte lo abierto en grilla, una zona por ventana, y lo acomoda."""
    _, _, ancho_pantalla, alto_pantalla = ventanas.area_trabajo()
    abiertas = await run.io_bound(ventanas.para_acomodar, TITULO, incluir_minimizadas,
                                  [dict(r) for r in ignoradas])
    if not abiertas:
        ui.notify('No encontré ventanas para acomodar', type='warning')
        return

    rectangulos = ventanas.reparto(len(abiertas), ancho_pantalla / alto_pantalla)
    accesos = await run.io_bound(ventanas.accesos_escritorio, CARPETA)

    zonas_actuales.clear()
    con_acceso = 0
    for ventana, (x, y, ancho, alto) in zip(abiertas, rectangulos):
        acceso = ventanas.acceso_para(ventana['proceso'], accesos)
        if acceso is not None:
            con_acceso += 1
        zonas_actuales.append({
            'etiqueta': (ventana['titulo'] or ventana['proceso'])[:40],
            'proceso': ventana['proceso'],
            'titulo': ventana['titulo'],
            # Saber cual ventana es no es saber como abrirla: el .lnk es lo que
            # hace que la proxima vez Aplicar la abra sola.
            'exe': acceso['exe'] if acceso else '',
            'args': acceso.get('args', '') if acceso else '',
            'lnk': acceso['lnk'] if acceso else '',
            'x': x, 'y': y, 'ancho': ancho, 'alto': alto,
        })
    dibujar_zonas()

    partes = [f'{len(abiertas)} ventanas repartidas']
    if incluir_minimizadas:
        partes.append('incluidas las minimizadas')
    if con_acceso:
        partes.append(f'{con_acceso} con acceso directo para reabrir')
    else:
        partes.append('ninguna con acceso directo: no voy a poder reabrirlas')
    ui.notify(' · '.join(partes), type='positive', timeout=7000)

    await aplicar_disposicion()


async def aplicar_zonas(cuales: list[dict]) -> None:
    """Lanza lo que falte y acomoda cada ventana en su zona, avisando qué pasó.

    Todo queda además en el log (`_workshell.log`): el aviso de la pantalla se va
    a los segundos, y una ventana que no se deja mover deja el hueco a la vista
    mucho más tiempo que el aviso. El 19 sep 2026 el dueño vio una franja vacía
    de 1190x820 sin ninguna forma de saber cuál de las cuatro ventanas había
    fallado.
    """
    # En un hilo aparte: abrir apps y esperarlas puede tardar decenas de
    # segundos y la interfaz tiene que seguir viva mientras tanto.
    resultados = await run.io_bound(ventanas.aplicar, cuales)
    guardar_layouts()  # aplicar() aprende proceso y titulo de lo que abrio

    for r in resultados:
        if not r['ok']:
            print(f'acomodar: FALLÓ {r["etiqueta"]}: {r["motivo"]}')
            ui.notify(f'{r["etiqueta"]}: {r["motivo"]}', type='negative', timeout=9000)
        elif r['motivo']:
            print(f'acomodar: {r["etiqueta"]}: {r["motivo"]}')
            ui.notify(f'{r["etiqueta"]}: {r["motivo"]}', type='info', timeout=6000)
        else:
            print(f'acomodar: {r["etiqueta"]}: en su zona')

    acomodadas = sum(1 for r in resultados if r['ok'])
    if acomodadas:
        ui.notify(f'{acomodadas} de {len(resultados)} ventanas acomodadas',
                  type='positive' if acomodadas == len(resultados) else 'warning')

    await refrescar_estados()  # los puntos ya pueden decir quién quedó abierta
    return resultados


async def aplicar_disposicion() -> None:
    """Lanza lo que falte y acomoda cada ventana en su zona."""
    con_ventana = [z for z in zonas_actuales if z.get('proceso') or z.get('exe') or z.get('lnk')]
    if not con_ventana:
        ui.notify('Este layout todavía no tiene ventanas asignadas', type='warning')
        return
    await aplicar_zonas(zonas_actuales)


# --- «Sumar ventana»: ir al escritorio y volver con lo que se abrió ---------
# Lo que pidió el dueño (18 sep 2026): no tener que ir al mapa, buscar la ventana
# nueva en una lista y reacomodar todo a mano cada vez que abre algo. En vez de
# eso: un botón que aparta el panel, deja el escritorio a la vista para abrir lo
# que quiera, y vuelve solo con la ventana nueva ya adentro de la grilla.


def es_el_panel(zona: dict) -> bool:
    """¿Esta zona es la ventana del propio WorksheLL?

    No se acomoda a sí mismo: Chrome le devuelve su tamaño (1700x1000 acá) y
    terminaría colgando fuera de la pantalla, con una zona del 88% x 93% que
    tapa todas las demás del mapa. Ver `ventanas.para_acomodar`.
    """
    return zona.get('titulo', '').strip().lower() == TITULO.lower()


def franja_util() -> float:
    """Qué fracción del alto de la pantalla pueden usar las ventanas.

    Con la barra al pie, esa franja de abajo es de WorksheLL: las ventanas se
    reparten en lo que queda, que es lo que se ve. Tapada (minimizada) no ocupa
    nada, así que la grilla puede usar la pantalla entera.
    """
    if not barra['activa'] or barra['tapada']:
        return 1.0
    alto = ventanas.area_trabajo()[3]
    return max(0.25, (alto - ALTO_BARRA) / alto)


def rectangulos_para(cantidad: int,
                     con_foco: bool = False) -> list[tuple[float, float, float, float]]:
    """Los rectángulos de la grilla, en % del área de trabajo.

    La grilla se calcula sobre la franja útil y después se estira a la pantalla:
    si no, con la barra al pie la última fila de ventanas quedaría abajo de la
    barra (la grilla se reparte sobre un alto que no está disponible).
    """
    _, _, ancho, alto = ventanas.area_trabajo()
    franja = franja_util()
    aspecto = ancho / (alto * franja)
    # La forma que se busca para cada celda: la de la pantalla, pero sin pasar de
    # 16:9. Con la barra al pie la franja es muy ancha (1920x820, 2.34:1) y
    # buscar esa forma da celdas chatas; una ventana se usa mejor cerca de 16:9.
    objetivo = min(aspecto, 1.78)
    base = (ventanas.foco(cantidad, aspecto, objetivo=objetivo) if con_foco
            else ventanas.reparto(cantidad, aspecto, objetivo))
    return [(x, round(y * franja, 2), ancho_celda, round(alto_celda * franja, 2))
            for x, y, ancho_celda, alto_celda in base]


async def sumar_ventana() -> None:
    """Aparta el panel y espera a que aparezca algo nuevo en pantalla.

    Vuelve solo: cuando la última ventana nueva se queda quieta unos segundos, o
    cuando se acaba el tiempo. También vuelve si el dueño trae el panel adelante
    a mano: eso es un «no, dejá».
    """
    if busqueda['activa']:
        ui.notify('Ya estoy esperando: abrí lo que quieras y vuelvo solo', type='info')
        return
    panel = await run.io_bound(ventanas.ventana_del_panel, TITULO)
    if panel is None:
        ui.notify('No encuentro la ventana del panel para apartarla', type='warning')
        return

    busqueda.update({
        'activa': True,
        'desde': time.monotonic(),
        'antes': {v['hwnd'] for v in await run.io_bound(ventanas.ventanas_abiertas)},
        'vistas': {},
    })
    ui.notify('Andá al escritorio y abrí lo que quieras: vuelvo solo, con la '
              'ventana nueva ya acomodada', timeout=4000)
    await asyncio.sleep(1.6)  # que el aviso llegue a leerse antes de irse

    if not await run.io_bound(ventanas.apartar, panel):
        busqueda['activa'] = False
        ui.notify('No pude apartar el panel del medio', type='negative')
        return
    vigilante.activate()


async def vigilar_ventana_nueva() -> None:
    """Mira cada segundo si apareció algo nuevo, mientras el panel está apartado."""
    if not busqueda['activa']:
        vigilante.deactivate()
        return

    panel = await run.io_bound(ventanas.ventana_del_panel, TITULO)
    if panel is None or not panel['minimizada']:
        # Lo cerró, o lo trajo adelante a mano: se terminó la espera.
        terminar_busqueda('El panel volvió: no sumo nada')
        return

    abiertas = await run.io_bound(ventanas.ventanas_abiertas)
    ahora = time.monotonic()
    reglas = [dict(r) for r in ignoradas]
    for ventana in abiertas:
        if ventana['hwnd'] in busqueda['antes'] or ventana['hwnd'] == panel['hwnd']:
            continue  # ya estaba abierta antes de salir: no es nueva
        if ventana['proceso'].lower() in ventanas.SHELL:
            continue
        if ventanas.esta_ignorada(ventana, reglas, abiertas):
            continue  # «no me la acomodes» también vale para esto
        busqueda['vistas'].setdefault(ventana['hwnd'], ahora)

    nuevas = [v for v in abiertas if v['hwnd'] in busqueda['vistas']]
    if nuevas and ahora - max(busqueda['vistas'].values()) >= ASENTAR:
        # Se quedó quieta: ya no está abriendo más cosas. Es el momento de volver.
        await volver_con(nuevas)
    elif ahora - busqueda['desde'] >= TOPE_BUSQUEDA:
        terminar_busqueda('Pasó el tiempo y no encontré ninguna ventana nueva')


def terminar_busqueda(mensaje: str) -> None:
    busqueda['activa'] = False
    vigilante.deactivate()
    ui.notify(mensaje, type='info', timeout=6000)


async def volver_con(nuevas: list[dict]) -> None:
    """Trae el panel adelante, suma esas ventanas al mapa y reparte de nuevo."""
    panel = await run.io_bound(ventanas.ventana_del_panel, TITULO)
    busqueda['activa'] = False
    vigilante.deactivate()
    if panel is not None:
        await run.io_bound(ventanas.traer_al_frente, panel)
    await sumar_y_repartir(nuevas)


async def sumar_y_repartir(nuevas: list[dict]) -> None:
    """Agrega una zona por ventana nueva y rearma la grilla con lo que se ve."""
    accesos = await run.io_bound(ventanas.accesos_escritorio, CARPETA)
    for ventana in nuevas:
        acceso = ventanas.acceso_para(ventana['proceso'], accesos)
        zonas_actuales.append({
            'etiqueta': (ventana['titulo'] or ventana['proceso'])[:40],
            'proceso': ventana['proceso'],
            'titulo': ventana['titulo'],
            # Igual que «Acomodar todo»: el .lnk es lo que hace que la próxima
            # vez «Aplicar» la pueda abrir sola.
            'exe': acceso['exe'] if acceso else '',
            'args': acceso.get('args', '') if acceso else '',
            'lnk': acceso['lnk'] if acceso else '',
            'x': 0.0, 'y': 0.0, 'ancho': 50.0, 'alto': 50.0,  # lo pisa la grilla
        })
    acomodadas = await repartir_ventanas()
    if not acomodadas:
        ui.notify('Sumé las ventanas nuevas, pero no había nada a la vista '
                  'para repartir', type='warning')
        return
    nombres = ', '.join(z['etiqueta'][:22] for z in
                        zonas_actuales[len(zonas_actuales) - len(nuevas):])
    ui.notify(f'Volví con {len(nuevas)}: {nombres} · {acomodadas} ventanas '
              f'repartidas', type='positive', timeout=8000)


async def repartir_ventanas(foco: int | None = None) -> int:
    """Una celda para cada zona con ventana a la vista, y las mueve.

    Las minimizadas y las cerradas quedan donde estaban: no ocupan lugar en la
    pantalla, y restaurarlas es decisión del dueño (por eso «Acomodar todo» lo
    pregunta en vez de hacerlo). El propio panel tampoco entra en la grilla, por
    lo mismo que en `para_acomodar`.

    Con `foco` (el número de zona), esa va primera y se queda con la franja
    grande mientras el resto se acomoda al costado: es la disposición provisoria
    de tocar una tarjeta. Devuelve cuántas acomodó.
    """
    pares = await run.io_bound(ventanas.estado_y_ventanas,
                               [dict(z) for z in zonas_actuales])
    indices = [i for i, (estado, _) in enumerate(pares)
               if estado == 'abierta' and not es_el_panel(zonas_actuales[i])]
    if foco in indices:
        indices.remove(foco)
        indices.insert(0, foco)
    else:
        foco = None  # esa zona ya no está a la vista: se reparte parejo
    if not indices:
        return 0

    rectangulos = rectangulos_para(len(indices), con_foco=foco is not None)
    print(f'reparto: {len(indices)} ventana(s) a la vista'
          + (f', con la zona {foco + 1} agrandada' if foco is not None else ', parejo'))
    for i, (x, y, ancho, alto) in zip(indices, rectangulos):
        zonas_actuales[i].update({'x': x, 'y': y, 'ancho': ancho, 'alto': alto})
    barra['foco'] = foco
    dibujar_zonas()  # la tira se redibuja con la tarjeta en foco marcada
    guardar_layouts()
    # Solo se mueven las que están a la vista: `aplicar` también abre lo que
    # falta, y acá no se pidió abrir nada.
    resultados = await aplicar_zonas([zonas_actuales[i] for i in indices])

    if foco is not None and resultados and not resultados[0]['ok']:
        # La agrandada no se dejó poner (¿elevada? ¿tamaño mínimo?). Dejar su
        # celda vacía es lo peor que puede pasar: es la más grande de la
        # pantalla, y el 19 sep 2026 quedó así, un hueco de 1190x820 con las
        # otras tres aplastadas al costado. Mejor repartir parejo entre las que
        # sí se pudieron mover.
        print(f'reparto: la agrandada no se pudo acomodar '
              f'({resultados[0]["motivo"]}); reparto parejo en su lugar')
        await repartir_ventanas()
    return len(indices)


async def foco_en(idx: int) -> None:
    """Sube esa ventana: la agranda, corre el resto y la trae adelante."""
    if not 0 <= idx < len(zonas_actuales):
        return
    ventana, _ = await run.io_bound(ventanas.resolver, dict(zonas_actuales[idx]))
    if ventana is None:
        ui.notify(f'{zonas_actuales[idx].get("etiqueta") or "esa ventana"}: '
                  f'no la encuentro abierta', type='warning')
        return

    # Al frente ANTES de repartir, y no después: si estaba minimizada, traerla la
    # restaura, y así el reparto la ve a la vista y le da su celda. `mover` no
    # toca el orden de las ventanas, así que adelante se queda adelante.
    ok, motivo = await run.io_bound(ventanas.traer_al_frente, ventana)
    if not ok:
        ui.notify(f'No pude traerla adelante: {motivo}', type='warning')
    marcar_uso()  # elegir una tarjeta es usarla: que no se esconda en el acto
    await repartir_ventanas(foco=idx)


# --- Lo que se hace desde el clic derecho de una tarjeta ---------------------
# Elegir una tarjeta ya era un clic; lo demás (minimizarla, cerrarla, sacarla
# del mapa) obligaba a ir a buscarla por el Alt+Tab. Ahora sale del clic derecho
# en la misma tira, que es donde está la mano cuando estás eligiendo.


async def minimizar_zona(idx: int) -> None:
    """Manda esa ventana al fondo sin cerrarla."""
    if not 0 <= idx < len(zonas_actuales):
        return
    ventana, _ = await run.io_bound(ventanas.resolver, dict(zonas_actuales[idx]))
    if ventana is None:
        ui.notify(f'{rotulo_zona(idx)}: no la encuentro abierta', type='warning')
        return
    ok = await run.io_bound(ventanas.apartar, ventana)
    ui.notify(f'{rotulo_zona(idx)}: {"minimizada" if ok else "no se dejó minimizar"}',
              type='info' if ok else 'warning')
    await refrescar_estados()  # que el punto ámbar aparezca ya, sin esperar el tick


def pedir_cerrar(idx: int) -> None:
    """Pregunta antes de cerrar: es el botón X de una ventana con trabajo adentro."""
    if not 0 <= idx < len(zonas_actuales):
        return
    with ui.dialog() as dialogo, ui.card().classes('w-[30rem] max-w-full gap-3'):
        ui.label(f'¿Cerrar «{rotulo_zona(idx)}»?').classes('panel-titulo')
        ui.label('Es el botón X de esa ventana: si tiene algo sin guardar, te va '
                 'a preguntar ella. La zona queda en el mapa, así que el próximo '
                 '«Aplicar» la vuelve a abrir.').classes('leyenda-zona')
        with ui.row().classes('w-full justify-end gap-2'):
            ui.button('Cancelar', on_click=dialogo.close).props('dense flat')
            ui.button('Cerrar', on_click=lambda: hacerlo()) \
                .props('dense unelevated').classes('boton-oro')

    async def hacerlo() -> None:
        dialogo.close()
        ventana, _ = await run.io_bound(ventanas.resolver, dict(zonas_actuales[idx]))
        if ventana is None:
            ui.notify(f'{rotulo_zona(idx)}: ya no está abierta', type='info')
            return
        if await run.io_bound(ventanas.cerrar, ventana):
            ui.notify(f'Le pedí a {rotulo_zona(idx)} que se cierre', type='info')
        else:
            ui.notify(f'{rotulo_zona(idx)}: la ventana ya no existe', type='warning')
    dialogo.open()


def quitar_zona(idx: int, ignorar: bool = False) -> None:
    """Saca la zona del mapa y de la tira.

    Con `ignorar` además la anota en la lista de «no volver a acomodar»: es la
    diferencia entre sacarla ahora y que «Acomodar todo» no la reponga la próxima
    vez. La ventana no se toca: deja de estar en el mapa, no se cierra.
    """
    if not 0 <= idx < len(zonas_actuales):
        return
    zona = zonas_actuales[idx]
    etiqueta = rotulo_zona(idx)
    anteriores = [dict(z) for z in zonas_actuales]
    zonas_actuales.pop(idx)
    if barra['foco'] == idx:
        # La que estaba agrandada ya no está en el mapa: no hay ninguna que lo
        # esté, aunque la ventana siga en pantalla donde quedó.
        barra['foco'] = None
    elif barra['foco'] is not None and barra['foco'] > idx:
        barra['foco'] -= 1
    renumerar_miniaturas(anteriores)
    if ignorar:
        anotar_ignorada(zona)
        actualizar_boton_ignoradas()
    dibujar_zonas()
    guardar_layouts()
    ui.notify(f'«{etiqueta}» sale del mapa: la ventana queda donde está'
              + (' y «Acomodar todo» no la vuelve a poner' if ignorar else ''),
              type='info', timeout=6000)


async def mostrar_barra() -> None:
    """Devuelve la barra al pie y reparte las ventanas en lo que queda.

    Corre cuando el mouse se queda contra el borde de abajo, y también vale como
    acción suelta: la barra vuelve y las ventanas se corren para no quedarle
    abajo (es el camino inverso a «Listo»).
    """
    panel = barra.get('panel') or await run.io_bound(ventanas.ventana_del_panel, TITULO)
    if panel is None:
        return
    barra['panel'] = panel
    barra['borde_desde'], barra['borde_armado'] = None, False
    if barra['tapada']:
        ok, motivo = await run.io_bound(ventanas.traer_al_frente, panel)
        if not ok:
            ui.notify(f'No pude traer la barra de vuelta: {motivo}', type='warning')
            return
        barra['tapada'] = False
        barra['dormido'] = False  # ya no está minimizada: lo que sigue lo ve `revisar_barra`
    # Por si la ventana del panel es otra (la cerraron y la volvieron a abrir): la
    # transparencia es de la ventana, no de WorksheLL, y se pierde con ella.
    await aplicar_opacidad()
    marcar_uso()
    await repartir_ventanas(foco=barra['foco'])


async def vigilar_barra() -> None:
    """Esconde la barra cuando no la usás y la devuelve si el mouse baja al borde.

    Es el «toda la pantalla a disposición» hasta el final: no hay que apretar
    «Listo» cada vez. Solo pregunta dónde está el mouse —una llamada, sin
    enumerar ventanas— y ni se asoma si el modo barra está apagado.

    El borde de abajo pide **espera** y no un roce: por ahí se pasa yendo a la
    barra de tareas, y volver cada vez sería peor que no volver nunca. Y la
    barra solo vuelve si el mouse salió del borde desde que se escondió, porque
    el que la escondió sigue apoyado ahí.
    """
    if not barra['activa'] or barra['panel'] is None:
        return
    x, y = ventanas.cursor()
    _, _, _, alto = ventanas.area_trabajo()

    if barra['tapada']:
        if y < alto - 3:
            barra['borde_armado'], barra['borde_desde'] = True, None
        elif barra['borde_armado']:
            barra['borde_desde'] = barra['borde_desde'] or time.monotonic()
            if time.monotonic() - barra['borde_desde'] >= ESPERA_BORDE:
                await mostrar_barra()
        return

    caja = barra['panel'].get('rect') or ()
    if len(caja) == 4 and caja[0] <= x <= caja[0] + caja[2] and caja[1] <= y <= caja[1] + caja[3]:
        marcar_uso()  # el mouse encima de la tira: la estás usando
        return
    if barra['auto'] and time.monotonic() - barra['ultimo_uso'] >= barra['auto']:
        await repartir_y_esconder()


async def alternar_barra() -> None:
    """Ctrl+Alt+B: la que está a la vista se corre, la escondida vuelve."""
    if not barra['activa']:
        await modo_barra(True)
    elif barra['tapada']:
        await mostrar_barra()
    else:
        await repartir_y_esconder()


def actualizar_boton_auto() -> None:
    """El botón dice los segundos: si no, no hay forma de saber si está prendido."""
    boton_auto.text = f'Auto: {barra["auto"]:.0f} s' if barra['auto'] else 'Auto: no'


async def aplicar_opacidad() -> None:
    """Pone (o saca) la transparencia de la barra.

    Una página no puede volverse translúcida desde adentro, así que se le pide a
    Windows por la ventana del panel (ver `ventanas.opacidad`). Fuera del modo
    barra vuelve a ser opaca: el panel entero, con sus paneles y su mapa, tiene
    que leerse, no adivinarse.
    """
    panel = barra.get('panel')
    if panel is None:
        return
    alfa = int(barra['opacidad']) if barra['activa'] else 255
    ok, motivo = await run.io_bound(ventanas.opacidad, panel, alfa)
    if not ok:
        # No se avisa en pantalla: si Windows no lo permite, la barra se ve
        # opaca y ya. El motivo queda en el log para poder investigarlo.
        print(f'transparencia: no pude dejar el panel en alfa {alfa}: {motivo}')


async def alternar_opacidad() -> None:
    """Recorre las transparencias de la barra y la guarda como preferencia."""
    if barra['opacidad'] not in OPACIDADES:
        barra['opacidad'] = OPACIDADES[0]
    siguiente = OPACIDADES[(OPACIDADES.index(barra['opacidad']) + 1) % len(OPACIDADES)]
    barra['opacidad'] = siguiente
    ajustes['opacidad'] = siguiente
    guardar_ajustes()
    actualizar_boton_opacidad()
    marcar_uso()
    await aplicar_opacidad()


def actualizar_boton_opacidad() -> None:
    boton_opacidad.text = ('Opacidad: sólida' if barra['opacidad'] >= 255
                           else f'Opacidad: {barra["opacidad"] / 255 * 100:.0f}%')


async def alternar_auto() -> None:
    """Prende o apaga que la barra se corra sola.

    Es una preferencia —cómo querés que se comporte el panel en esta máquina— y
    no una disposición, así que va a panel.json como las miniaturas.
    """
    barra['auto'] = 0.0 if barra['auto'] else SEGUNDOS_AUTO
    ajustes['barra_auto'] = barra['auto']
    guardar_ajustes()
    actualizar_boton_auto()
    marcar_uso()
    if barra['auto']:
        aviso = (f'La barra se corre sola a los {barra["auto"]:.0f} s sin que la '
                 f'uses, y vuelve si dejás el mouse contra el borde de abajo')
    else:
        aviso = 'La barra se queda hasta que la escondas vos (Ctrl+Alt+B)'
    ui.notify(aviso, type='info', timeout=6000)


async def modo_barra(activar: bool) -> None:
    """Achica WorksheLL a una franja al pie, o lo devuelve a su tamaño.

    La ventana del panel **no** entra en la grilla de las ventanas (ver
    `es_el_panel`): acá se la lleva a una franja de `ALTO_BARRA` y las ventanas
    se reparten arriba, en lo que queda de pantalla.
    """
    panel = await run.io_bound(ventanas.ventana_del_panel, TITULO)
    if panel is None:
        ui.notify('No encuentro la ventana del panel para achicarla', type='warning')
        return
    _, _, ancho, alto = ventanas.area_trabajo()

    if activar:
        global animar_tira
        animar_tira = True  # la tira entra animada al aparecer la barra
        if not barra['activa']:
            barra['rect_panel'] = panel['rect']  # para poder volver
        barra.update({'activa': True, 'tapada': False, 'panel': panel})
        mostrar_paneles([])  # en barra: la tira de tarjetas y nada más
        encabezado.set_visibility(False)  # el título y el selector no entran
        # `mover` devuelve (ok, motivo, logrado): el rectángulo logrado es el que
        # usa el vigilante para saber si el mouse está encima de la barra, y no
        # se puede volver a preguntar sin enumerar todas las ventanas.
        ok, motivo, logrado = await run.io_bound(
            ventanas.mover, panel, 0, alto - ALTO_BARRA, ancho, ALTO_BARRA)
        if not ok:
            ui.notify(f'No pude achicar el panel: {motivo}', type='warning')
        elif logrado:
            barra['panel']['rect'] = logrado
        await aplicar_opacidad()  # la barra se ve a través suyo
    else:
        barra.update({'activa': False, 'tapada': False, 'foco': None})
        encabezado.set_visibility(True)
        mostrar_paneles(layouts.get(layout_actual, PANTALLA_VACIA)['paneles'])
        x, y, ancho_previo, alto_previo = barra['rect_panel'] or (0, 0, 1700, 1000)
        # Recortado al área de trabajo: Chrome restaura posiciones viejas y se lo
        # vio abrir en x=-148, con el borde del panel fuera de pantalla. Volver a
        # su tamaño no puede dejarlo peor de lo que estaba.
        x = max(0, min(x, ancho - ancho_previo))
        y = max(0, min(y, alto - alto_previo))
        await run.io_bound(ventanas.mover, panel, x, y, ancho_previo, alto_previo)
        await aplicar_opacidad()  # de vuelta al panel completo, opaco

    marcar_uso()
    dibujar_zonas()
    await repartir_ventanas(foco=barra['foco'])


async def repartir_y_esconder() -> None:
    """Reparte parejo sobre toda la pantalla y aparta la barra del medio.

    Es el «listo» de la tira: la barra no ocupa lugar mientras está minimizada,
    así que la grilla se calcula sobre la pantalla entera. Vuelve sola cuando el
    dueño trae la barra de vuelta (ver `revisar_barra`).
    """
    panel = barra.get('panel') or await run.io_bound(ventanas.ventana_del_panel, TITULO)
    barra['foco'] = None
    barra['tapada'] = True
    # Se desarma el pedido por el borde: el mouse que la escondió sigue apoyado
    # abajo, y con el borde armado la barra volvería sola en el acto.
    barra['borde_desde'], barra['borde_armado'] = None, False
    marcar_uso()
    if panel is not None:
        await run.io_bound(ventanas.apartar, panel)
        barra['panel'] = panel
        barra['dormido'] = True
    await repartir_ventanas()
    ui.notify('Repartido. La barra vuelve con Ctrl+Alt+B o bajando el mouse '
              'al borde de abajo.', timeout=6000)


async def revisar_barra() -> None:
    """Si el dueño trajo la barra de vuelta, corre las ventanas para que no tape.

    Con la barra minimizada la grilla usa toda la pantalla; al volver ocupa los
    últimos 260 px, así que hay que repartir de nuevo en lo que queda. Sin esto,
    la última fila de ventanas queda abajo de la barra. Se pregunta por
    `IsIconic` sobre la ventana que ya conocemos: no enumera nada.
    """
    panel = barra.get('panel')
    if panel is None or not ventanas.sigue_ahi(panel):
        panel = await run.io_bound(ventanas.ventana_del_panel, TITULO)
        if panel is None:
            return
        barra['panel'] = panel

    dormida = ventanas.esta_minimizada_ahora(panel)
    volvio = barra['dormido'] and not dormida
    barra['dormido'] = dormida
    if volvio and barra['tapada']:
        barra['tapada'] = False
        await repartir_ventanas(foco=barra['foco'])


ui.on('zona_geometria', lambda e: al_arrastrar(e.args))
ui.on('zona_clic', lambda e: al_hacer_clic(e.args))
ui.on('tarjeta_orden', lambda e: al_reordenar(e.args))

# El arrastre se maneja entero en JavaScript sobre `document`, con delegacion:
# asi sigue funcionando cuando Python redibuja el mapa y los nodos son nuevos.
# Solo avisa al servidor cuando termina, y distingue un clic de un arrastre por
# la distancia recorrida: si no, soltar una zona abriria su dialogo encima.
ARRASTRE_JS = '''
document.addEventListener('mousedown', (evento) => {
  const zona = evento.target.closest('.zona');
  if (!zona) return;
  const mapa = zona.closest('.mapa-zonas');
  if (!mapa) return;

  const caja = mapa.getBoundingClientRect();
  const porcentaje = (e) => ({
    x: (e.clientX - caja.left) / caja.width * 100,
    y: (e.clientY - caja.top) / caja.height * 100,
  });

  const geometria = (z) => ({
    x: parseFloat(z.style.left), y: parseFloat(z.style.top),
    w: parseFloat(z.style.width), h: parseFloat(z.style.height),
  });

  // Imantar a los bordes de las otras zonas: sin esto quedan franjas de 1-2%
  // entre ventana y ventana, que en pantalla se ven como un error.
  const guias = (excluir) => {
    const vs = [0, 100], hs = [0, 100];
    document.querySelectorAll('.mapa-zonas .zona').forEach((z) => {
      if (z === excluir) return;
      const g = geometria(z);
      vs.push(g.x, g.x + g.w);
      hs.push(g.y, g.y + g.h);
    });
    return { vs, hs };
  };
  const iman = (valor, lista, tolerancia = 1.5) => {
    for (const guia of lista) if (Math.abs(valor - guia) < tolerancia) return guia;
    return valor;
  };

  const inicio = { ...porcentaje(evento), ...geometria(zona) };
  const redimensionando = evento.target.classList.contains('zona-handle');
  let movido = false;
  evento.preventDefault();

  const mover = (e) => {
    const p = porcentaje(e);
    const dx = p.x - inicio.x, dy = p.y - inicio.y;
    if (Math.abs(dx) > 0.3 || Math.abs(dy) > 0.3) movido = true;

    const g = guias(zona);
    if (redimensionando) {
      const ancho = iman(inicio.w + dx, g.vs.map((v) => v - inicio.x));
      const alto = iman(inicio.h + dy, g.hs.map((v) => v - inicio.y));
      zona.style.width = Math.max(5, Math.min(ancho, 100 - inicio.x)) + '%';
      zona.style.height = Math.max(5, Math.min(alto, 100 - inicio.y)) + '%';
    } else {
      const x = iman(inicio.x + dx, g.vs);
      const y = iman(inicio.y + dy, g.hs);
      zona.style.left = Math.min(Math.max(x, 0), 100 - inicio.w) + '%';
      zona.style.top = Math.min(Math.max(y, 0), 100 - inicio.h) + '%';
    }
  };

  const soltar = () => {
    document.removeEventListener('mousemove', mover);
    document.removeEventListener('mouseup', soltar);
    const g = geometria(zona);
    const idx = Array.from(document.querySelectorAll('.mapa-zonas .zona')).indexOf(zona);
    if (!movido) {
      if (typeof emitEvent === 'function') emitEvent('zona_clic', { idx });
      return;
    }
    zona.style.left = g.x + '%'; zona.style.top = g.y + '%';
    zona.style.width = g.w + '%'; zona.style.height = g.h + '%';
    if (typeof emitEvent === 'function') {
      emitEvent('zona_geometria', { idx, x: g.x, y: g.y, ancho: g.w, alto: g.h });
    }
  };

  document.addEventListener('mousemove', mover);
  document.addEventListener('mouseup', soltar);
});
'''

ui.add_body_html(f'<script>{ARRASTRE_JS}</script>')

# Reordenar la tira: se arrastra una tarjeta y las demás se corren solas, como
# en cualquier lista. Se reordena en el DOM mientras se arrastra (que es lo que
# da la sensación de que la tarjeta pesa) y recién al soltar se avisa al
# servidor, que reordena el modelo y guarda. Mismo esquema que el arrastre del
# mapa: delegación sobre `document`, para que siga andando cuando Python
# redibuja la tira y los nodos son nuevos.
ARRASTRE_TARJETAS_JS = '''
let tarjetaRecienArrastrada = false;

document.addEventListener('mousedown', (evento) => {
  if (evento.button !== 0) return;  // el clic derecho es el menú, no un arrastre
  const tarjeta = evento.target.closest('.tarjeta');
  if (!tarjeta) return;
  const tira = tarjeta.closest('.tira-tarjetas');
  if (!tira) return;

  const inicio = { x: evento.clientX, y: evento.clientY };
  const tarjetas = () => Array.from(tira.querySelectorAll('.tarjeta'));
  const de = tarjetas().indexOf(tarjeta);
  let arrastrando = false;

  const mover = (e) => {
    if (!arrastrando) {
      // Un temblor de 3 px no es un arrastre: si no, el clic que elige la
      // ventana no llegaría nunca.
      if (Math.abs(e.clientX - inicio.x) < 8 && Math.abs(e.clientY - inicio.y) < 8) return;
      arrastrando = true;
      tarjeta.classList.add('arrastrando');
    }
    e.preventDefault();  // que no se seleccione el texto de al lado
    // Se ubica por el punto medio de las otras: la tarjeta pasa al lado donde el
    // mouse ya cruzó la mitad de la vecina.
    const otras = tarjetas().filter((t) => t !== tarjeta);
    const siguiente = otras.find(
      (t) => e.clientX < t.getBoundingClientRect().left + t.offsetWidth / 2);
    if (siguiente) tira.insertBefore(tarjeta, siguiente);
    else tira.appendChild(tarjeta);
  };

  const soltar = () => {
    document.removeEventListener('mousemove', mover);
    document.removeEventListener('mouseup', soltar);
    if (!arrastrando) return;
    tarjeta.classList.remove('arrastrando');
    tarjetaRecienArrastrada = true;
    const a = tarjetas().indexOf(tarjeta);
    if (typeof emitEvent === 'function') emitEvent('tarjeta_orden', { de, a });
  };

  document.addEventListener('mousemove', mover);
  document.addEventListener('mouseup', soltar);
});

// Un arrastre no puede terminar eligiendo la tarjeta: el clic que sigue se corta
// en la fase de captura, antes de que lo vea el manejador de la tarjeta.
document.addEventListener('click', (e) => {
  if (!tarjetaRecienArrastrada) return;
  tarjetaRecienArrastrada = false;
  e.stopPropagation();
  e.preventDefault();
}, true);
'''

ui.add_body_html(f'<script>{ARRASTRE_TARJETAS_JS}</script>')

refrescar()  # pinta los valores ya, sin esperar el primer tick del timer
dibujar_pendientes()  # y la libreta, con lo que ya estaba anotado
dibujar_servicios()  # las filas; los puntos los prende el primer repaso

timer_metricas = ui.timer(ResourceGuard.INTERVALO_NORMAL, refrescar)
guardia = ResourceGuard(timer_metricas)

# El estado de las zonas cada 5 s: es lo que hace que el mapa diga algo del mundo
# real y no solo lo que se guardo la ultima vez. El primer repaso va enseguida,
# para que el mapa no arranque sin puntos.
ui.timer(5.0, refrescar_estados)
ui.timer(0.5, refrescar_estados, once=True)

# El vigilante de «Sumar ventana»: nace apagado y solo mira mientras el panel
# está apartado, esperando que aparezca algo nuevo. Un segundo es el paso justo:
# más seguido es gastar enumerando ventanas, más lento se siente trabado.
vigilante = ui.timer(PASO_VIGILANCIA, vigilar_ventana_nueva, active=False)

# El vigilante de la barra: dónde está el mouse, para correrla cuando no la usás
# y devolverla cuando baja al borde. Es barato —una llamada, sin enumerar
# ventanas— así que puede ir seguido; y solo hace algo en modo barra.
ui.timer(PASO_RATON, vigilar_barra)

# Los recordatorios de la libreta: cada medio minuto alcanza para avisar a la
# hora que dice el pendiente (y si WorksheLL arranca más tarde, el aviso sale
# igual, porque lo que compara es la hora, no cuándo se anotó).
ui.timer(30.0, avisar_recordatorios)

# Los servicios cada 10 s (un connect a cada puerto es baratísimo) y el trading
# cada 5: son los dos números que más cambian y los que más se miran de reojo.
ui.timer(10.0, actualizar_servicios)
ui.timer(0.6, actualizar_servicios, once=True)  # el estado ya, sin esperar 10 s
ui.timer(5.0, actualizar_mt5)

# Lo publicado cada minuto: las corridas son cada horas, pero el archivo de
# estado se escribe al terminar cada una y quiero que el panel lo vea enseguida.
ui.timer(60.0, actualizar_publicaciones)
ui.timer(2.0, actualizar_publicaciones, once=True)

# Las teclas globales: el hilo que escucha deja el número en la cola y acá se
# atiende, en el hilo de la interfaz. Si la cola está vacía no hace nada más que
# preguntar, así que 0,25 s se siente inmediato sin costar nada.
ui.timer(0.25, revisar_atajos)


def cargar():
    destino = (url.value or '').strip()
    if not destino:
        return
    if not destino.startswith(('http://', 'https://')):
        destino = f'https://{destino}'
    url.value = destino
    vista.props(f'src="{destino}"')


boton.on('click', cargar)
url.on('keydown.enter', cargar)

aplicar_layout(selector.value)

# --- servidor ---
# Las miniaturas viajan por su propia ruta y no adentro de la pagina: son ~15 KB
# por zona y por refresco, y mandarlas por el websocket de NiceGUI seria pasar
# la foto entera cada 5 segundos por el mismo canal que los eventos del panel.
# Las miniaturas NO viajan por una ruta propia, y no es por gusto: en WorksheLL
# la página la sirve el handler de 404 de NiceGUI, que arma la página índice
# **ejecutando el script otra vez**. O sea que hay más de una WorksheLL viva en
# el mismo proceso —la del arranque y la que dibuja la página—, cada una con su
# propio `miniaturas`. Una ruta registrada por la primera contesta con el
# diccionario de la primera (vacío) mientras el mapa se pinta desde el de la
# segunda. Paso el 19 sep 2026: el mapa mostraba la miniatura y la ruta decía
# «no hay ninguna».
#
# Yendo adentro del `src` como data URI no hay nada que compartir: los bytes que
# se capturaron son los mismos que se pintan, en la misma instancia.


puerto = puerto_libre(8080)
direccion = f'http://localhost:{puerto}'
print(f'WorkShell en {direccion}')

# show=False: la ventana la abrimos nosotros en modo --app, no una pestana comun.
# WORKSHELL_SIN_VENTANA=1 lo levanta sin abrir nada: sirve para probarlo y para
# dejarlo corriendo de fondo en una maquina donde la ventana molesta.
if os.environ.get('WORKSHELL_SIN_VENTANA') == '1':
    print('WORKSHELL_SIN_VENTANA=1: no abro la ventana')
else:
    threading.Thread(
        target=abrir_cuando_responda, args=(direccion, puerto), daemon=True
    ).start()

# host explicito: el default de NiceGUI es 0.0.0.0, o sea WorksheLL entero
# servido a cualquiera que este en la red. Ahora que ademas mueve ventanas,
# eso no es solo una fuga de lectura. ttyd ya estaba encerrado en 127.0.0.1;
# esto lo pone a la par.
ui.run(port=puerto, host='127.0.0.1', title=TITULO, show=False, reload=False)
