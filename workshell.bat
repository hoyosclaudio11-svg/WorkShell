@echo off
setlocal

REM WorksheLL: levanta el terminal web (ttyd) y despues el panel.
REM Pensado tambien para la carpeta shell:startup, por eso no depende del
REM directorio actual: si no encuentra workshell.py al lado, usa la ruta fija.

set "PROYECTO=%~dp0"
if not exist "%PROYECTO%workshell.py" set "PROYECTO=C:\Users\chito\OneDrive - Plan Sarmiento\Escritorio\WorkShell\"

if not exist "%PROYECTO%workshell.py" (
    echo [X] No encuentro workshell.py en "%PROYECTO%"
    pause
    exit /b 1
)

cd /d "%PROYECTO%"

REM --- 0. Si ya esta corriendo, no arrancar otro ---
REM Sin esto, cada doble clic deja un WorksheLL mas: el segundo no puede tomar el
REM 8080 y se va al 8081 o al 8082, donde nadie lo mira, y quedan ventanas y
REM consolas huerfanas apiladas (el 19 sep 2026 habia seis Chrome vacios y media
REM docena de consolas de tanto relanzar a mano).
netstat -ano | findstr /r /c:":8080 .*LISTENING" >nul
if not errorlevel 1 (
    echo WorksheLL ya esta corriendo en http://localhost:8080
    echo Si no ves la ventana, buscala en la barra de tareas o traela con el
    echo boton "Traer al frente" de alguna zona.
    exit /b 0
)

REM --- 1. ttyd: alimenta el panel Terminal en localhost:7681 ---
REM -i 127.0.0.1 lo deja solo en esta maquina (si no, ttyd escucha en toda la red
REM y -W entrega una consola escribible a cualquiera que la alcance).
REM -W permite tipear; sin eso la terminal es de solo lectura.
REM Si el 7681 ya esta tomado, el ttyd vivo es ese: arrancar otro solo deja una
REM consola con el error de puerto ocupado.
where ttyd >nul 2>&1
if errorlevel 1 (
    echo [!] ttyd no esta en el PATH: el panel Terminal va a quedar vacio.
    echo     Instalalo con:  winget install tsl0922.ttyd
) else (
    netstat -ano | findstr /r /c:":7681 .*LISTENING" >nul
    if errorlevel 1 (
        start "ttyd" /min cmd /k ttyd -i 127.0.0.1 -p 7681 -W cmd.exe
    ) else (
        echo ttyd ya escucha en 7681: no arranco otro.
    )
)

REM --- 2. WorksheLL, en su propia consola minimizada ---
REM cmd /k (y no /c) para que la ventana quede abierta si WorksheLL se muere.
REM El `call` es obligatorio: python es un .bat del shim de pyenv y sin call
REM el control no vuelve nunca.
REM
REM La salida va al log ademas de la consola, y -u es imprescindible: con la
REM salida a un archivo, Python la guarda en un buffer de 8 KB y los print no
REM aparecen hasta llenarlo. Un log que se escribe de a ratos no sirve para
REM diagnosticar nada (paso el 19 sep 2026, buscando un error que ya estaba
REM escrito). Si WorksheLL se muere, el traceback esta en _workshell.log.
start "WorkShell" /min cmd /k "call python -u workshell.py > _workshell.log 2>&1"

endlocal
exit /b 0
