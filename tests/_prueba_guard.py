"""Prueba del ResourceGuard con la memoria falseada.

La rama de alerta solo corre bajo presion real de RAM, que es justo cuando no
queres enterarte de que tiene un error. Aca se importa workshell sin levantar el
servidor y se le miente a psutil para recorrer los tres casos.

    python _prueba_guard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sys

from nicegui import ui

ui.run = lambda **kw: None  # neutralizar el servidor antes de importar workshell

import workshell  # noqa: E402  (tiene que ir despues del parche)

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

fallos = []


def chequear(nombre: str, condicion: bool, detalle='') -> None:
    print(f'  [{"OK" if condicion else "FALLA"}] {nombre}' + (f' — {detalle}' if detalle else ''))
    if not condicion:
        fallos.append(nombre)


class MemoriaFalsa:
    def __init__(self, disponible_gb: float, porcentaje: float):
        self.available = int(disponible_gb * 1024 ** 3)
        self.percent = porcentaje
        self.total = int(15.9 * 1024 ** 3)


memoria_real = workshell.psutil.virtual_memory


def con_memoria(disponible_gb: float):
    porcentaje = 100 - disponible_gb / 15.9 * 100
    workshell.psutil.virtual_memory = lambda: MemoriaFalsa(disponible_gb, porcentaje)


guardia = workshell.guardia
print('los procesos que mas RAM ocupan ahora mismo')
print(f'  {guardia.mas_golosos()}')

print('\nhisteresis')
con_memoria(4.0)  # como en reposo
guardia.revisar()
chequear('en reposo no se queja', not guardia.en_alerta,
         f'4,0 GB disponibles · métricas cada {guardia.timer_metricas.interval:.0f}s')

con_memoria(1.0)
guardia.revisar()
chequear('con 1,0 GB entra en alerta', guardia.en_alerta,
         f'métricas cada {guardia.timer_metricas.interval:.0f}s')

con_memoria(1.5)
guardia.revisar()
chequear('con 1,5 GB se queda en alerta (no oscila)', guardia.en_alerta,
         f'métricas cada {guardia.timer_metricas.interval:.0f}s')

con_memoria(2.5)
guardia.revisar()
chequear('con 2,5 GB vuelve a la normalidad', not guardia.en_alerta,
         f'métricas cada {guardia.timer_metricas.interval:.0f}s')

print('\nel umbral que estaba antes')
# El 70% de 15,9 GB son 4,8 GB disponibles: en reposo esta maquina tiene ~3,5,
# asi que ese umbral nunca se alcanzaba y el guard no volvia nunca. Se mide
# contra la memoria de verdad, no contra la falseada.
workshell.psutil.virtual_memory = memoria_real
chequear('el reposo de esta maquina queda por encima del umbral nuevo',
         guardia.disponible_gb() > guardia.RECUPERACION_GB,
         f'{guardia.disponible_gb():.2f} GB disponibles vs {guardia.RECUPERACION_GB} GB')

print(f'\n{len(fallos)} fallas' + (f': {fallos}' if fallos else ''))
sys.exit(1 if fallos else 0)
