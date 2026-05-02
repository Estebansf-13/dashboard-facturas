# Empresa Original Prueba 1 — Dashboard de Facturación

## Contexto
Estás trabajando en el dashboard de facturación de **Empresa Original Prueba 1**.
Esta es la empresa principal del usuario (Estudio Creativo Vega SL).

## Archivos de esta empresa
- `dashboard-facturacion.html` + `dashboard.js` — el dashboard visual
- `facturas_datos.json` — todos los datos de facturas (NO borrar)
- `watcher.py` — vigila facturas/nuevas/ en local y hace push a GitHub
- `procesar.py` — procesa PDFs manualmente en local
- `procesar_gha.py` — procesa PDFs en GitHub Actions (nube)
- `subir.html` — página para subir facturas desde el navegador
- `iniciar.bat` — arranca el servidor local (puerto 8888)
- `facturas/ingresos/` — facturas emitidas (PDFs)
- `facturas/gastos/` — facturas recibidas (PDFs)
- `facturas/nuevas/` — carpeta de entrada para nuevas facturas

## URLs online (GitHub Pages)
- Dashboard: `https://estebansf-13.github.io/dashboard-facturas/empresa-original-prueba-1/dashboard-facturacion.html`
- Subir factura: `https://estebansf-13.github.io/dashboard-facturas/empresa-original-prueba-1/subir.html`

## Reglas importantes
- NUNCA tocar archivos de `empresa-2-carmen-fortes/`
- El `facturas_datos.json` contiene datos reales — no borrar ni resetear
- El emisor propio es `"Estudio Creativo Vega SL"` (usado para clasificar ingresos vs gastos)
- Los scripts de git usan `BASE_DIR.parent` como raíz del repositorio
- Puerto local: 8888

## Flujo de trabajo
1. El usuario mete un PDF en `facturas/nuevas/` o lo sube desde `subir.html`
2. El watcher (local) o GitHub Actions (nube) lo detecta y procesa
3. `facturas_datos.json` se actualiza
4. El dashboard se refresca automáticamente
