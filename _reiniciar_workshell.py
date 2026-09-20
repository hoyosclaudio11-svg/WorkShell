"""Cierra la instancia de WorksheLL que este corriendo: la ventana del navegador
que lo muestra y la consola minimizada que lo hospeda. No lanza la nueva; eso lo
hace workshell.bat (o el mismo comando aparte).

    python _reiniciar_workshell.py

OJO con el criterio de busqueda, que ya se pago una vez: buscar "workshell" en
el titulo a secas tambien matchea cualquier terminal o pestana que *mencione*
WorksheLL — incluida una sesion de Claude Code titulada "WorkShell con NiceGUI y
psutil", que es exactamente la que se cerro por error el 18 sep 2026. Por eso
ahora cada caso pide su criterio exacto y no alcanza con parecerse.
"""
import os
import sys
import time

import psutil
import win32con
import win32gui

import ventanas

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

NAVEGADORES = ('chrome.exe', 'msedge.exe')
# Nombres de otros scripts de esta carpeta que también mencionan workshell.py.
NO_ES_EL_PANEL = ('_prueba', '_reiniciar', '_diag', '_verificar')


def es_la_ventana_del_panel(v: dict) -> bool:
    """La ventana --app: navegador y titulo exacto, sin adornos.

    También entra la que quedó con el título de la URL (`localhost:8080`): es una
    ventana del panel que no llegó a cargar la página —pasa cuando el servidor
    todavía no estaba— y queda ahí, vacía, para siempre. El 19 sep 2026 había
    seis, de tanto relanzar el panel.
    """
    if v['proceso'].lower() not in NAVEGADORES:
        return False
    titulo = v['titulo'].strip()
    return titulo == 'WorkShell' or titulo.lower().startswith('localhost:')


def es_su_consola(v: dict) -> bool:
    """La consola del .bat, si su titulo todavia lleva la ruta del script.

    Es un extra, no la vía principal: el 19 sep 2026 el .bat pasó a redirigir la
    salida a un log y el título dejó de llevar la ruta, así que este criterio no
    alcanzaba y quedaba un WorksheLL viejo escuchando en 8080 mientras el nuevo
    arrancaba en 8082 sin que nadie se enterara.
    """
    return ('workshell.py' in v['titulo'].lower()
            and v['proceso'].lower() in ('cmd.exe', 'windowsterminal.exe'))


def procesos_del_panel() -> list[psutil.Process]:
    """Los procesos de WorksheLL, por línea de comandos.

    Por línea de comandos y no por ventana: el proceso es lo que hay que matar,
    y una consola con el título cambiado lo dejaba vivo.
    """
    encontrados = []
    for proceso in psutil.process_iter(['name', 'cmdline']):
        linea = ' '.join(proceso.info['cmdline'] or []).lower()
        if 'workshell.py' not in linea or proceso.pid == os.getpid():
            continue
        if any(marca in linea for marca in NO_ES_EL_PANEL):
            continue
        encontrados.append(proceso)
    return encontrados


a_cerrar = [v for v in ventanas.ventanas_abiertas()
            if es_la_ventana_del_panel(v) or es_su_consola(v)]

procesos = procesos_del_panel()
if not a_cerrar and not procesos:
    print('no hay ninguna ventana de WorksheLL abierta')

# Primero la ventana del navegador (cierre limpio), después el proceso: si el
# proceso muere primero, la ventana queda un rato mostrando una página muerta.
for v in a_cerrar:
    print(f'cerrando {v["proceso"]:20} hwnd={v["hwnd"]:<10} {v["titulo"][:52]}')
    win32gui.PostMessage(v['hwnd'], win32con.WM_CLOSE, 0, 0)

time.sleep(0.5)
for proceso in procesos:
    print(f'terminando pid {proceso.pid:<8} {" ".join(proceso.info["cmdline"] or [])[:60]}')
    try:
        proceso.terminate()
        proceso.wait(timeout=5)
    except psutil.NoSuchProcess:
        # Ya se había ido: cerrar la consola del .bat se lleva también al
        # python que corría adentro, así que llegar acá es lo normal.
        print(f'  pid {proceso.pid} ya no estaba')
    except psutil.TimeoutExpired:
        print(f'  pid {proceso.pid} no se fue con terminate(): lo mato')
        try:
            proceso.kill()
        except psutil.NoSuchProcess:
            pass

time.sleep(0.5)
print(f'{len(a_cerrar)} ventana(s) cerradas, {len(procesos)} proceso(s) terminados')
