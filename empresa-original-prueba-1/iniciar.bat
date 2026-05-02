@echo off
title [1] Estudio Creativo Vega SL — Dashboard Activo
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   [EMPRESA 1] ESTUDIO CREATIVO VEGA SL
echo ============================================================
echo.
echo  Sincronizando archivos con la nube...
git -C "%~dp0\.." pull origin main
echo.
echo  Abriendo dashboard online...
start "" "https://estebansf-13.github.io/dashboard-facturas/empresa-original-prueba-1/dashboard-facturacion.html"

echo.
echo ============================================================
echo  VIGILANTE DE FACTURAS ACTIVO — EMPRESA 1
echo ============================================================
echo.
echo  Carpeta vigilada: facturas\nuevas\
echo.
echo  Para procesar una factura:
echo    1. Copia el PDF dentro de  facturas\nuevas\
echo    2. Se sube automaticamente a la nube
echo    3. El dashboard online se actualiza en 1-2 minutos
echo.
echo  Deja esta ventana ABIERTA mientras trabajes.
echo  Para parar: cierra esta ventana o pulsa Ctrl+C
echo ============================================================
echo.

python watcher.py
