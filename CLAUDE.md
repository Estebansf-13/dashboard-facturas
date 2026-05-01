# Dashboard de Facturas

Este proyecto lee tus facturas en PDF y genera un dashboard visual con el análisis financiero completo de tu negocio.

## Comportamiento al iniciar

Cuando el usuario abra esta carpeta y escriba cualquier cosa, responde:

> **Bienvenido al analizador de facturas**
>
> Voy a leer tus facturas PDF y generar un dashboard visual con toda la información financiera de tu negocio: ingresos, gastos, IVA, balance, evolución mensual, clientes y más.
>
> Mete tus facturas PDF en la carpeta `facturas/`:
> - `facturas/ingresos/` — facturas que tú emites (lo que cobras)
> - `facturas/gastos/` — facturas que recibes (lo que pagas)
> - Si solo tienes ingresos, ponlas todas en `facturas/ingresos/`
>
> **¿Ya tienes las facturas en la carpeta?** Dime y empiezo a analizarlas.

Después usa la skill `dashboard-facturas` automáticamente.

## Qué hace

1. Lee cada factura PDF extrayendo: fecha, número, emisor, receptor, concepto, base imponible, IVA, IRPF, total
2. Calcula métricas: facturación total, balance, IVA trimestral, tendencias, top clientes
3. Genera un dashboard HTML visual y lo abre en el navegador
4. Guarda los datos en JSON por si quieres revisarlos

## No necesita nada instalado

Claude lee los PDFs directamente con sus herramientas nativas. Sin Python, sin librerías.

## Privacidad

Tus facturas se procesan localmente en tu ordenador. Ningún dato sale de tu máquina.
