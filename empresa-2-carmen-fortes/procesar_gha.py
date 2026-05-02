"""
Procesa PDFs en facturas/nuevas/ — corre en GitHub Actions (nube).

Cascada de métodos (cada uno solo se usa si el anterior no encuentra el total):
1. pdfplumber   — PDFs digitales con texto, rápido y fiable
2. Tesseract    — PDFs escaneados con buena calidad
3. Gemini Vision — fotos, escaneados difíciles, cualquier documento (GRATIS)
"""

import re, json, time, shutil, os
from pathlib import Path

try:
    import pdfplumber
    TIENE_PDFPLUMBER = True
except ImportError:
    TIENE_PDFPLUMBER = False

try:
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import ImageEnhance, ImageFilter
    TIENE_OCR = True
except ImportError:
    TIENE_OCR = False

try:
    import google.generativeai as genai
    TIENE_GEMINI = True
except ImportError:
    TIENE_GEMINI = False

BASE_DIR = Path(__file__).parent
NUEVAS_DIR = BASE_DIR / "facturas" / "nuevas"
INGRESOS_DIR = BASE_DIR / "facturas" / "ingresos"
GASTOS_DIR = BASE_DIR / "facturas" / "gastos"
JSON_PATH = BASE_DIR / "facturas_datos.json"
EMISOR_PROPIO = "Carmen Fortes Pardo"

PROMPT_VISION = (
    "Analiza esta factura y devuelve ÚNICAMENTE un JSON con estos campos "
    "(sin texto adicional, sin bloques markdown):\n"
    "{\n"
    '  "numero": "número de factura",\n'
    '  "fecha": "YYYY-MM-DD",\n'
    '  "emisor": "nombre completo de quien emite la factura (el proveedor)",\n'
    '  "receptor": "nombre completo de quien recibe la factura (el cliente)",\n'
    '  "concepto": "descripción del servicio o producto",\n'
    '  "base_imponible": 0.00,\n'
    '  "iva_porcentaje": 21,\n'
    '  "iva_cantidad": 0.00,\n'
    '  "irpf_porcentaje": 0,\n'
    '  "irpf_cantidad": 0.00,\n'
    '  "total": 0.00\n'
    "}\n"
    "Los importes deben ser números decimales con punto. Si no hay IRPF usa 0."
)


# ── 1. Extracción de texto (PDFs digitales) ──────────────────────────────────

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


# ── 2. OCR con Tesseract (escaneados de buena calidad) ───────────────────────

def extraer_con_ocr(filepath):
    if not TIENE_OCR:
        return ''
    try:
        images = convert_from_path(str(filepath), dpi=300, fmt='png')
        textos = []
        for img in images[:3]:
            # Mejorar contraste antes de pasar a Tesseract
            img = ImageEnhance.Contrast(img.convert('L')).enhance(2.0)
            t = pytesseract.image_to_string(img, lang='spa+eng', config='--psm 6')
            if t.strip():
                textos.append(t)
        return ' '.join(textos)
    except Exception as e:
        print(f"  Error en OCR: {e}")
        return ''


# ── 3. Gemini Vision (fotos, cualquier documento — gratis) ───────────────────

def extraer_con_gemini(filepath):
    if not TIENE_GEMINI:
        return None
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  GEMINI_API_KEY no configurada — saltando visión.")
        return None
    try:
        from pdf2image import convert_from_path

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")

        images = convert_from_path(str(filepath), dpi=200, fmt='png')
        pil_pages = [img for img in images[:3]]

        response = model.generate_content(pil_pages + [PROMPT_VISION])
        text = response.text.strip()
        text = re.sub(r'^```[a-z]*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        return json.loads(text.strip())
    except Exception as e:
        print(f"  Error en Gemini: {e}")
        return None


# ── Parseo de importes ────────────────────────────────────────────────────────

def parsear_importe(texto):
    texto = texto.strip().replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        return float(texto)
    except Exception:
        return 0.0


# ── Extracción de campos con regex (para texto de pdfplumber/Tesseract) ──────

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

    for pat in [r'(?:Emisor|Proveedor|De|Empresa)[\s:]+([^\n]+)',
                r'^([A-ZÁÉÍÓÚÑ][^\n]{3,40}(?:SL|SA|SAU|SLU|Ltd))']:
        m = re.search(pat, texto, re.IGNORECASE | re.MULTILINE)
        if m:
            d["emisor"] = m.group(1).strip()
            break

    for pat in [r'(?:Cliente|Facturar a|Receptor|Para)[\s:]+([^\n]+)',
                r'(?:CIF|NIF)[\s:/]+[A-Z0-9\-]+\s+([^\n]+)']:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            d["receptor"] = m.group(1).strip()
            break

    m = re.search(r'(?:Concepto|Descripci[oó]n|Servicio)[\s:]+([^\n]+)', texto, re.IGNORECASE)
    if m:
        d["concepto"] = m.group(1).strip()

    m = re.search(r'Base\s+[Ii]mponible[\s:€]*([0-9.,]+)', texto, re.IGNORECASE)
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

    # Calcular IVA si falta
    if d["base_imponible"] == 0 and d["total"] > 0:
        d["base_imponible"] = round(d["total"] / 1.21, 2)
        d["iva_cantidad"] = round(d["total"] - d["base_imponible"], 2)
    elif d["iva_cantidad"] == 0 and d["base_imponible"] > 0 and d["total"] > d["base_imponible"]:
        d["iva_cantidad"] = round(d["total"] - d["base_imponible"], 2)

    # Clasificar con heurística robusta para OCR sucio
    emisor_lower = d["emisor"].lower()
    receptor_lower = d["receptor"].lower()
    nombre_lower = EMISOR_PROPIO.lower()
    palabras_ocr_basura = ['factura', 'número', 'fecha', 'cantidad', 'precio', 'importe', 'nif', 'cif', 'total']

    if nombre_lower in emisor_lower:
        # Verificar que no sea basura de OCR (campo demasiado largo o con palabras sospechosas)
        es_basura = (len(d["emisor"]) > 60 or
                     any(p in emisor_lower for p in palabras_ocr_basura) or
                     any(c.isdigit() for c in d["emisor"][:15]))
        if not es_basura:
            d["tipo"] = "ingreso"
    elif nombre_lower in receptor_lower:
        d["tipo"] = "gasto"

    return d


def datos_desde_vision(vision, pdf_name):
    """Convierte la respuesta JSON de Gemini al formato interno."""
    datos = {
        "archivo": f"gastos/{pdf_name}",
        "tipo": "gasto",
        "numero": str(vision.get("numero") or Path(pdf_name).stem),
        "fecha": vision.get("fecha") or time.strftime("%Y-%m-%d"),
        "emisor": vision.get("emisor", ""),
        "receptor": vision.get("receptor", ""),
        "concepto": vision.get("concepto", ""),
        "base_imponible": float(vision.get("base_imponible") or 0),
        "iva_porcentaje": int(vision.get("iva_porcentaje") or 21),
        "iva_cantidad": float(vision.get("iva_cantidad") or 0),
        "irpf_porcentaje": int(vision.get("irpf_porcentaje") or 0),
        "irpf_cantidad": float(vision.get("irpf_cantidad") or 0),
        "total": float(vision.get("total") or 0),
        "moneda": "EUR"
    }
    if EMISOR_PROPIO.lower() in datos["emisor"].lower():
        datos["tipo"] = "ingreso"
    elif EMISOR_PROPIO.lower() in datos["receptor"].lower():
        datos["tipo"] = "gasto"
    return datos


# ── Main ──────────────────────────────────────────────────────────────────────

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
        datos = None

        # ── Paso 1: pdfplumber ──
        texto = extraer_texto(str(pdf))
        if texto.strip():
            datos = extraer_datos(texto)
            datos["numero"] = datos["numero"] or pdf.stem
            if datos["total"] > 0:
                print(f"  pdfplumber OK — Total: {datos['total']} EUR")

        # ── Paso 2: Tesseract (si no hay total todavía) ──
        if not datos or datos["total"] == 0:
            print("  Total 0 o sin texto. Aplicando Tesseract OCR...")
            texto_ocr = extraer_con_ocr(str(pdf))
            if texto_ocr.strip():
                datos_ocr = extraer_datos(texto_ocr)
                datos_ocr["numero"] = datos_ocr["numero"] or pdf.stem
                if datos_ocr["total"] > 0:
                    datos = datos_ocr
                    print(f"  Tesseract OK — Total: {datos['total']} EUR")

        # ── Paso 3: Gemini Vision (si sigue sin total) ──
        if not datos or datos["total"] == 0:
            print("  Tesseract sin resultado. Usando Gemini Vision...")
            vision = extraer_con_gemini(str(pdf))
            if vision:
                datos = datos_desde_vision(vision, pdf.name)
                print(f"  Gemini OK — Total: {datos['total']} EUR")

        # ── Fallback: guardar sin datos ──
        if not datos or datos["total"] == 0:
            print("  Ningún método encontró el total. Guardando sin datos.")
            if not datos:
                datos = {
                    "archivo": f"gastos/{pdf.name}", "tipo": "gasto",
                    "numero": pdf.stem, "fecha": time.strftime("%Y-%m-%d"),
                    "emisor": "", "receptor": "",
                    "concepto": f"Revisar manualmente: {pdf.name}",
                    "base_imponible": 0.0, "iva_porcentaje": 21,
                    "iva_cantidad": 0.0, "irpf_porcentaje": 0,
                    "irpf_cantidad": 0, "total": 0.0, "moneda": "EUR"
                }

        dest_dir = INGRESOS_DIR if datos["tipo"] == "ingreso" else GASTOS_DIR
        datos["archivo"] = f"{'ingresos' if datos['tipo'] == 'ingreso' else 'gastos'}/{pdf.name}"

        data["facturas"] = [f for f in data["facturas"] if f.get("numero") != datos["numero"]]
        data["facturas"].append(datos)
        data["fecha_generacion"] = time.strftime("%Y-%m-%d")

        shutil.move(str(pdf), str(dest_dir / pdf.name))
        print(f"  Tipo: {datos['tipo']} | Total: {datos['total']} EUR | → {dest_dir.name}/")
        procesados += 1

    if procesados > 0:
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(JSON_PATH, 'r', encoding='utf-8') as f:
            json.load(f)
        print(f"\n{procesados} factura(s) procesada(s). JSON validado. Dashboard actualizado.")


if __name__ == "__main__":
    main()
