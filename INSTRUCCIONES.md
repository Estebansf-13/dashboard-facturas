# Cómo usar este kit

## Requisitos

1. **Visual Studio Code** instalado (descarga en https://code.visualstudio.com)
2. **Extensión de Claude Code** instalada desde el marketplace de VS Code (busca "Claude Code" en extensiones)
3. Tus facturas en PDF

## Paso 1 — Abrir este proyecto

1. Abre VS Code
2. Archivo → Abrir carpeta → selecciona esta carpeta (`kit-dashboard-facturas`)
3. Abre Claude Code desde el panel lateral de VS Code

## Paso 2 — Meter tus facturas

Pon tus PDFs en las carpetas:
- `facturas/ingresos/` — las facturas que tú emites (lo que cobras)
- `facturas/gastos/` — las facturas que recibes (lo que pagas)

Si solo tienes facturas de un tipo, ponlas todas en `facturas/ingresos/` o `facturas/gastos/`.

## Paso 2 — Abrir en Claude Code

Abre Claude Code y navega hasta esta carpeta.

## Paso 3 — Pedir el análisis

Escribe algo como:

> Analiza mis facturas

Claude lee cada PDF, extrae los datos y genera un dashboard con:
- Facturación total (bruto y neto)
- Balance ingresos vs gastos
- IVA repercutido y soportado
- Evolución mensual con gráficos
- Top clientes por facturación
- Desglose trimestral (para declaraciones de IVA)
- Tabla de detalle de todas las facturas
- Alertas (meses sin facturar, dependencia de clientes, etc.)

## Paso 4 — Revisar

Claude te muestra un resumen de lo que extrajo y te pregunta si los datos son correctos antes de generar el dashboard. Si alguna factura se leyó mal, puedes corregirla.

## Privacidad

Todo se procesa en tu ordenador. Ningún dato de tus facturas sale de tu máquina.

## Estructura

```
kit-dashboard-facturas/
├── CLAUDE.md                           <- Claude lo lee automáticamente
├── INSTRUCCIONES.md                    <- Este archivo
├── .claude/
│   └── skills/
│       └── 04-dashboard-facturas.md    <- La skill
└── facturas/
    ├── ingresos/                       <- Pon aquí tus facturas emitidas (PDF)
    └── gastos/                         <- Pon aquí tus facturas recibidas (PDF)
```
