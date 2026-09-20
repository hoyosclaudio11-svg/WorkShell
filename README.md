# WorkShell

[![CI](https://github.com/hoyosclaudio11-svg/WorkShell/actions/workflows/ci.yml/badge.svg)](https://github.com/hoyosclaudio11-svg/WorkShell/actions/workflows/ci.yml)

Panel de trabajo local: acomoda las ventanas nativas del escritorio en zonas
definidas en porcentaje, y muestra en vivo lo que ya existe en la máquina —
pendientes con recordatorios, qué publicó cada canal hoy, servicios con su
puerto (y botón para relanzarlos) y el estado de MT5.

## Cómo arrancar

Doble clic a `workshell.bat` (también sirve para la carpeta `shell:startup`).
El .bat:

1. No arranca si ya hay un WorksheLL escuchando en el 8080 (guard contra
   instancias apiladas).
2. Levanta `ttyd` en el 7681 si no está (alimenta el panel Terminal; solo en
   127.0.0.1).
3. Lanza `python -u workshell.py` en una consola minimizada, con la salida a
   `_workshell.log` (el traceback queda ahí si se muere).

Panel: http://localhost:8080

## Estructura

```
workshell.py            El panel (NiceGUI): zonas, grilla, tarjetas, atajos
ventanas.py             Capa Win32: listar/mover/capturar ventanas
workshell.bat           Arranque con guard y ttyd
_reiniciar_workshell.py Cierra la instancia viva (navegador + consola)
_relanzar_con_log.bat   Relanza con log fresco
_conejo_miniaturas.py   Conejo de indias para pruebas de miniaturas
tests/                  Pruebas manuales (_prueba_*.py, diagnósticos _ver/_vistazo)
                        y test_datos.py (unittest, corre solo)
respaldos/              Snapshots de layouts.json tomados a mano antes de cambios
layouts.json            Disposiciones: qué paneles se ven y qué ventana va a cada zona
ignoradas.json          Ventanas que «Acomodar todo» nunca toca
panel.json              Preferencias del panel (miniaturas, barra, opacidad)
servicios.json          Los servicios de la casa y cómo levantar cada uno
pendientes.json         La libreta (se crea al primera nota)
```

El núcleo vive en la raíz a propósito: `workshell.bat` lo busca al lado, los
JSON de datos se cargan con `Path(__file__).with_name(...)` y los atajos
globales apuntan acá. Moverlo implica tocar todo eso juntos.

## Requisitos

```
pip install -r requirements.txt
```

Python 3.10+ en Windows. `ttyd` es opcional (sin él, el panel Terminal queda
vacío): `winget install tsl0922.ttyd`.

## Pruebas

Automáticas (no tocan el escritorio, solo validan los datos):

```
python -m unittest discover -s tests -v
```

Las `_prueba_*.py` de `tests/` son scripts manuales e interactivos: abren
ventanas de verdad o usan Playwright contra un WorksheLL vivo. Se corren una a
una desde la raíz:

```
python tests/_prueba_acomodar.py
```
