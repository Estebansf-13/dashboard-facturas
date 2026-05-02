@echo off
title [1] Empresa Original Prueba 1 — Dashboard Activo
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   [EMPRESA 1] EMPRESA ORIGINAL PRUEBA 1
echo ============================================================
echo.
echo  Arrancando servidor web local en puerto 8888...
start /min "" python -m http.server 8888

timeout /t 2 /nobreak >nul

echo  Abriendo dashboard en el navegador...
start "" http://localhost:8888/dashboard-facturacion.html

echo.
echo ============================================================
echo  VIGILANTE DE FACTURAS ACTIVO — EMPRESA 1
echo ============================================================
echo.
echo  Carpeta vigilada: facturas\nuevas\
echo.
echo  Para procesar una factura:
echo    1. Copia el PDF dentro de  facturas\nuevas\
echo    2. El dashboard se actualiza solo en el navegador
echo.
echo  Deja esta ventana ABIERTA mientras trabajes.
echo  Para parar: cierra esta ventana o pulsa Ctrl+C
echo ============================================================
echo.

python watcher.py
