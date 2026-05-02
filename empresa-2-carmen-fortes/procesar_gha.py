"""
Procesa PDFs en facturas/nuevas/ — corre en GitHub Actions (nube).
1. Intenta extraer texto con pdfplumber (PDFs digitales).
2. Si no hay texto (PDF escaneado/foto), usa Claude Vision para leerlo visualmente.
"""

import re, json, time, shutil, os, io, base64
from pathlib import Path

try:
    import pdfplumber
    TIENE_PDFPLUMBER = True
except ImportError:
    TIENE_PDFPLUMBER = False

try:
    import anthropic
    from pdf2image import convert_from_path
    TIENE_VISION = True
except ImportError:
    TIENE_VISION = False

BASE_DIR = Path(__file__).parent
NUEVAS_DIR = BASE_DIR / "facturas" / "nuevas"
INGRESOS_DIR = BASE_DIR / "facturas" / "ingresos"
GASTOS_DIR = BASE_DIR / "facturas" / "gastos"
JSON_PATH = BASE_DIR / "facturas_datos.json"
EMISOR_PROPIO = "Carmen Fortes Pardo"


def extraer_texto(filepath):
    if not TIENE_PDFPLUMBER:
        return ''
    texto = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texto.append(t)
    return ' '.join(texto)


def extraer_con_vision(filepath):
    """Convierte el PDF a imágenes y usa Claude Vision para leer la factura."""
    if not TIENE_VISION:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ANTHROPIC_API_KEY no configurada.")
        return None

    try:
        images = convert_from_path(str(filepath), dpi=200, fmt='png')
        content = []
        for img in images[:3]:
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(buf.getvalue()).decode()
                }
            })
        content.append({
            "type": "text",
            "text": (
                "Analiza esta factura y devuelve ÚNICAMENTE un JSON con estos campos "
                "(sin texto adicional, sin bloques markdown):\n"
                "{\n"
                '  "numero": "número de factura",\n'
                '  "fecha": "YYYY-MM-DD",\n'
                '  "emisor": "nombre completo del emisor o empresa proveedora",\n'
                '  "receptor": "nombre completo del receptor o cliente",\n'
                '  "concepto": "descripción principal del servicio o producto",\n'
                '  "base_imponible": 0.00,\n'
                '  "iva_porcentaje": 21,\n'
                '  "iva_cantidad": 0.00,\n'
                '  "irpf_porcentaje": 0,\n'
                '  "irpf_cantidad": 0.00,\n'
                '  "total": 0.00\n'
                "}\n"
                "Los importes deben ser números decimales con punto. "
                "Si no hay IRPF, usa 0."
            )
        })

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}]
        )

        text = response.content[0].text.strip()
        text = re.sub(r'^```[a-z]*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        return json.loads(text.strip())

    except Exception as e:
        print(f"  Error en visión: {e}")
        return None


def parsear_importe(texto):
    texto = texto.strip().replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        return float(texto)
    except Exception:
        return 0.0


def extraer_datos(texto):
    d = {
        "archivo": "", "tipo": "gasto", "numero": "", "fecha": "",
        "emisor": "", "receptor": "", "concepto": "",
        "base_imponible": 0.0, "iva_porcentaje": 21, "iva_cantidad": 0.0,
        "irpf_porcentaje": 0, "irpf_cantidad": 0, "total": 0.0, "moneda": "EUR"
    }

    m = re.search(r'(?:FACTURA|Factura)\s*N[°ºo.\s]*(\S+)', texto, re.IGNORECASE)
    if not m:
        m = re.search(r'N[úu]mero\s+de\s+factura[:\s]+(\S+)', texto, re.IGNORECASE)
    if m:
        d["numero"] = m.group(1).rstrip('.,')

    m = re.search(r'(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})', texto)
    if m:
        d["fecha"] = f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"

    for pat in [r'(?:Emisor|Proveedor|De|Empresa)[\s:]+([^\n]+)', r'^([A-ZÁÉÍÓÚÑ][^\n]{3,40}(?:SL|SA|SAU|SLU|Ltd))']:
        m = re.search(pat, texto, re.IGNORECASE | re.MULTILINE)
        if m:
            d["emisor"] = m.group(1).strip()
            break

    for pat in [r'(?:Cliente|Facturar a|Receptor|Para)[\s:]+([^\n]+)', r'(?:CIF|NIF)[\s:/]+[A-Z0-9\-]+\s+([^\n]+)']:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            d["receptor"] = m.group(1).strip()
            break

    m = re.search(r'(?:Concepto|Descripci[oó]n|Servicio)[\s:]+([^\n]+)', texto, re.IGNORECASE)
    if m:
        d["concepto"] = m.group(1).strip()

    m = re.search(r'Base\s+imponible[\s:€]*([0-9.,]+)', texto, re.IGNORECASE)
    if m:
        d["base_imponible"] = parsear_importe(m.group(1))

    m = re.search(r'IVA\s*(\d+)\s*%[\s:€]*([0-9.,]+)', texto, re.IGNORECASE)
    if m:
        d["iva_porcentaje"] = int(m.group(1))
        d["iva_cantidad"] = parsear_importe(m.group(2))

    m = re.search(r'IRPF\s*-?\s*(\d+)\s*%[\s:€]*([0-9.,]+)', texto, re.IGNORECASE)
    if m:
        d["irpf_porcentaje"] = -int(m.group(1))
        d["irpf_cantidad"] = -parsear_importe(m.group(2))

    m = re.search(r'TOTAL[\s:€]*([0-9.,]+)', texto, re.IGNORECASE)
    if m:
        d["total"] = parsear_importe(m.group(1))

    if d["base_imponible"] == 0 and d["total"] > 0:
        d["base_imponible"] = round(d["total"] / 1.21, 2)
        d["iva_cantidad"] = round(d["total"] - d["base_imponible"], 2)

    if EMISOR_PROPIO.lower() in d["emisor"].lower():
        d["tipo"] = "ingreso"
    elif EMISOR_PROPIO.lower() in d["receptor"].lower():
        d["tipo"] = "gasto"

    return d


def datos_desde_vision(vision, pdf_name):
    datos = {
        "archivo": f"gastos/{pdf_name}",
        "tipo": "gasto",
        "numero": vision.get("numero") or Path(pdf_name).stem,
        "fecha": vision.get("fecha", time.strftime("%Y-%m-%d")),
        "emisor": vision.get("emisor", ""),
        "receptor": vision.get("receptor", ""),
        "concepto": vision.get("concepto", ""),
        "base_imponible": float(vision.get("base_imponible", 0)),
        "iva_porcentaje": int(vision.get("iva_porcentaje", 21)),
        "iva_cantidad": float(vision.get("iva_cantidad", 0)),
        "irpf_porcentaje": int(vision.get("irpf_porcentaje", 0)),
        "irpf_cantidad": float(vision.get("irpf_cantidad", 0)),
        "total": float(vision.get("total", 0)),
        "moneda": "EUR"
    }
    if EMISOR_PROPIO.lower() in datos["emisor"].lower():
        datos["tipo"] = "ingreso"
    elif EMISOR_PROPIO.lower() in datos["receptor"].lower():
        datos["tipo"] = "gasto"
    return datos


def main():
    NUEVAS_DIR.mkdir(parents=True, exist_ok=True)
    INGRESOS_DIR.mkdir(parents=True, exist_ok=True)
    GASTOS_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = list(NUEVAS_DIR.glob("*.pdf")) + list(NUEVAS_DIR.glob("*.PDF"))
    if not pdfs:
        print("No hay PDFs nuevos.")
        return

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    procesados = 0
    for pdf in pdfs:
        print(f"\nProcesando: {pdf.name}")
        texto = extraer_texto(str(pdf))

        if not texto.strip():
            print("  PDF sin texto extraíble. Usando Claude Vision...")
            vision = extraer_con_vision(str(pdf))
            if vision:
                datos = datos_desde_vision(vision, pdf.name)
                print(f"  Visión OK — Total: {datos['total']} EUR")
            else:
                print("  Visión no disponible. Guardando sin datos.")
                datos = {
                    "archivo": f"gastos/{pdf.name}", "tipo": "gasto",
                    "numero": pdf.stem, "fecha": time.strftime("%Y-%m-%d"),
                    "emisor": "", "receptor": "",
                    "concepto": f"PDF sin texto: {pdf.name}",
                    "base_imponible": 0.0, "iva_porcentaje": 21,
                    "iva_cantidad": 0.0, "irpf_porcentaje": 0,
                    "irpf_cantidad": 0, "total": 0.0, "moneda": "EUR"
                }
        else:
            datos = extraer_datos(texto)
            datos["numero"] = datos["numero"] or pdf.stem

        dest_dir = INGRESOS_DIR if datos["tipo"] == "ingreso" else GASTOS_DIR
        datos["archivo"] = f"{'ingresos' if datos['tipo'] == 'ingreso' else 'gastos'}/{pdf.name}"

        data["facturas"] = [f for f in data["facturas"] if f["numero"] != datos["numero"]]
        data["facturas"].append(datos)
        data["fecha_generacion"] = time.strftime("%Y-%m-%d")

        shutil.move(str(pdf), str(dest_dir / pdf.name))
        print(f"  Tipo: {datos['tipo']} | Total: {datos['total']} EUR | Movido a {dest_dir.name}/")
        procesados += 1

    if procesados > 0:
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n{procesados} factura(s) procesada(s). Dashboard actualizado.")


if __name__ == "__main__":
    main()
