@echo off
REM Relanza WorksheLL dejando su salida en _workshell.log: la consola minimizada
REM no se puede leer, y sin eso un error del panel es invisible.
cd /d "%~dp0"
REM -u es imprescindible: con la salida a un archivo, Python la guarda en un
REM buffer de 8 KB y los print no aparecen hasta llenarlo. Un log que se escribe
REM de a ratos no sirve para diagnosticar nada.
start "WorkShell" /min cmd /c "call python -u workshell.py > _workshell.log 2>&1"
exit /b 0
