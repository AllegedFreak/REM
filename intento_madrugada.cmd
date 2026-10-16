@echo off
rem Intento de madrugada (16-oct-2026 03:00): Ministerio de Agricultura (precios del Mercado Central) y reintento
rem del archivo de Internet para el Mercado Central. Lo programó Claude el 15-oct-2026; tarea "REM intento madrugada".
cd /d C:\Users\marco\rem
py -3.13 bajar_magyp_mcba.py > datos\log_intento_madrugada.txt 2>&1
py -3.13 actualizar_mercado_central.py --reintentar >> datos\log_intento_madrugada.txt 2>&1
