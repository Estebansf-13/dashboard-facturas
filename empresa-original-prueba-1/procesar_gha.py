"""
Procesa PDFs e imágenes en facturas/nuevas/ — corre en GitHub Actions (nube).

Cascada de métodos:
1. pdfplumber   — PDFs digitales con texto (rápido y gratis)
2. Gemini Vision — fotos, escaneados, cualquier documento (gratis, muy preciso)
3. Tesseract    — fallback si no hay clave Gemini

Formatos soportados: .pdf .PDF .jpg .jpeg .png .heic .heif .webp
"""

import re, json, time, shutil, os, io
from pathlib import Path
from PIL import Image
Image.MAX_IMAGE_PIXELS = None  # evitar error decompression bomb en fotos grandes

try:
    import pdfplumber
    TIENE_PDFPLUMBER = True
except ImportError:
    TIENE_PDFPLUMBER = False

try:
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import ImageEnhance
    TIENE_OCR = True
except ImportError:
    TIENE_OCR = False

try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    TIENE_GEMINI = True
except ImportError:
    TIENE_GEMINI = False

BASE_DIR = Path(__file__).parent
NUEVAS_DIR = BASE_DIR / "facturas" / "nuevas"
INGRESOS_DIR = BASE_DIR / "facturas" / "ingresos"
GASTOS_DIR = BASE_DIR / "facturas" / "gastos"
JSON_PATH = BASE_DIR / "facturas_datos.json"
EMISOR_PROPIO = "Estudio Creativo Vega SL"

EXTENSIONES_IMAGEN = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.bmp', '.tiff', '.tif'}
EXTENSIONES_PDF = {'.pdf'}

PROMPT_VISION = """Eres un experto en facturas españolas. Analiza esta factura y extrae los datos exactos.

Devuelve ÚNICAMENTE un JSON válido (sin texto adicional, sin bloques markdown ```):
{
  "numero": "número de factura o albarán (string)",
  "fecha": "YYYY-MM-DD",
  "emisor": "nombre completo de quien emite la factura (el vendedor/proveedor)",
  "receptor": "nombre completo de quien recibe la factura (el comprador/cliente)",
  "concepto": "descripción breve del servicio o producto principal",
  "base_imponible": 0.00,
  "iva_porcentaje": 21,
  "iva_cantidad": 0.00,
  "irpf_porcentaje": 0,
  "irpf_cantidad": 0.00,
  "total": 0.00
}

Reglas:
- Los importes son números decimales con PUNTO (no coma): 430.51, no 430,51
- Si no hay IRPF, usa 0
- El total es el importe final a pagar (incluyendo IVA, menos IRPF si lo hay)
- Si hay Recargo de Equivalencia (R.E.), súmalo al IVA total
- La fecha en formato YYYY-MM-DD (ej: 2025-11-24)
- Si no encuentras algún campo, usa "" para texto o 0.00 para números
- NUNCA inventes datos: si no está claro, usa 0.00
"""


# ── 1. Extracción de texto digital (pdfplumber) ───────────────────────────────

def extraer_texto_pdf(filepath):
    if not TIENE_PDFPLUMBER:
        return ''
    try:
        texto = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texto.append(t)
        return ' '.join(texto)
    except Exception as e:
        print(f"  Error pdfplumber: {e}")
        return ''


# ── 2. Gemini Vision (SDK nuevo: google-genai + gemini-2.0-flash) ─────────────

def _pil_a_bytes(img):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=85)
    return buf.getvalue()

def _cargar_imagenes_pdf(filepath):
    try:
        from pdf2image import convert_from_path
        imgs = convert_from_path(str(filepath), dpi=150, fmt='jpeg')
        return imgs[:3]
    except Exception as e:
        print(f"  Error convirtiendo PDF a imagen: {e}")
        return []

def _cargar_imagen_directa(filepath):
    try:
        img = Image.open(str(filepath)).convert('RGB')
        return [img]
    except Exception as e:
        print(f"  Error cargando imagen: {e}")
        return []

def _limpiar_json_gemini(text):
    text = text.strip()
    text = re.sub(r'^```[a-z]*\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    text = re.sub(r'(\d),(\d{2})(?=[\s,\n\}])', r'\1.\2', text)
    return json.loads(text)

def extraer_con_gemini(filepath):
    if not TIENE_GEMINI:
        return None
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  GEMINI_API_KEY no configurada — saltando visión.")
        return None

    ext = Path(filepath).suffix.lower()
    imagenes = _cargar_imagen_directa(filepath) if ext in EXTENSIONES_IMAGEN else _cargar_imagenes_pdf(filepath)

    if not imagenes:
        print("  No se pudieron cargar las imágenes del documento.")
        return None

    try:
        client = google_genai.Client(api_key=api_key)

        partes = [PROMPT_VISION]
        for img in imagenes:
            partes.append(genai_types.Part.from_bytes(
                data=_pil_a_bytes(img),
                mime_type='image/jpeg'
            ))

        response = client.models.generate_content(
            model='gemini-2.0-flash-lite',
            contents=partes
        )
        text = response.text.strip()
        print(f"  Respuesta Gemini: {text[:200]}...")

        datos = _limpiar_json_gemini(text)

        for campo in ['base_imponible', 'iva_cantidad', 'irpf_cantidad', 'total']:
            val = datos.get(campo, 0)
            if isinstance(val, str):
                val = val.replace(',', '.').replace(' ', '').replace('€', '')
                try:
                    val = float(val)
                except ValueError:
                    val = 0.0
            datos[campo] = float(val or 0)

        for campo in ['iva_porcentaje', 'irpf_porcentaje']:
            val = datos.get(campo, 0)
            try:
                datos[campo] = int(float(str(val).replace(',', '.')))
            except (ValueError, TypeError):
                datos[campo] = 21 if campo == 'iva_porcentaje' else 0

        return datos

    except json.JSONDecodeError as e:
        print(f"  Error parseando JSON de Gemini: {e}")
        print(f"  Texto recibido: {text[:500]}")
        return None
    except Exception as e:
        print(f"  Error en Gemini: {e}")
        return None


# ── 3. OCR con Tesseract (fallback) ──────────────────────────────────────────

def extraer_con_tesseract(filepath):
    if not TIENE_OCR:
        return ''
    try:
        from PIL import ImageEnhance as IE
        ext = Path(filepath).suffix.lower()
        if ext in EXTENSIONES_IMAGEN:
            img = Image.open(str(filepath)).convert('L')
            img = IE.Contrast(img).enhance(2.0)
            return pytesseract.image_to_string(img, lang='spa+eng', config='--psm 6')
        else:
            images = convert_from_path(str(filepath), dpi=150, fmt='jpeg')
            textos = []
            for img in images[:3]:
                img = IE.Contrast(img.convert('L')).enhance(2.0)
                t = pytesseract.image_to_string(img, lang='spa+eng', config='--psm 6')
                if t.strip():
                    textos.append(t)
            return ' '.join(textos)
    except Exception as e:
        print(f"  Error en Tesseract: {e}")
        return ''


# ── Parseo de importes ────────────────────────────────────────────────────────

def parsear_importe(texto):
    texto = str(texto).strip().replace(' ', '').replace('€', '')
    if ',' in texto and '.' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif ',' in texto:
        texto = texto.replace(',', '.')
    try:
        return float(texto)
    except Exception:
        return 0.0


# ── Extracción con regex (pdfplumber / Tesseract) ─────────────────────────────

def extraer_datos_texto(texto):
    d = {
        "numero": "", "fecha": "", "emisor": "", "receptor": "", "concepto": "",
        "base_imponible": 0.0, "iva_porcentaje": 21, "iva_cantidad": 0.0,
        "irpf_porcentaje": 0, "irpf_cantidad": 0.0, "total": 0.0
    }

    m = re.search(r'(?:FACTURA|Factura)\s*[Nn][°ºo.\s]*(\S+)', texto, re.IGNORECASE)
    if not m:
        m = re.search(r'[Nn][úu]mero\s+de\s+factura[:\s]+(\S+)', texto, re.IGNORECASE)
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

    m = re.search(r'[Bb]ase\s+[Ii]mponible[\s:€]*([0-9.,]+)', texto)
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
    elif d["iva_cantidad"] == 0 and d["base_imponible"] > 0 and d["total"] > d["base_imponible"]:
        d["iva_cantidad"] = round(d["total"] - d["base_imponible"], 2)

    return d


# ── Construcción del registro final ──────────────────────────────────────────

def construir_registro(datos_brutos, nombre_archivo):
    emisor = str(datos_brutos.get("emisor") or "").strip()
    receptor = str(datos_brutos.get("receptor") or "").strip()
    nombre_lower = EMISOR_PROPIO.lower()

    if nombre_lower in emisor.lower() and len(emisor) < 60:
        tipo = "ingreso"
    elif nombre_lower in receptor.lower():
        tipo = "gasto"
    else:
        tipo = "gasto"

    return {
        "archivo": f"{'ingresos' if tipo == 'ingreso' else 'gastos'}/{nombre_archivo}",
        "tipo": tipo,
        "numero": str(datos_brutos.get("numero") or Path(nombre_archivo).stem),
        "fecha": str(datos_brutos.get("fecha") or time.strftime("%Y-%m-%d")),
        "emisor": emisor,
        "receptor": receptor,
        "concepto": str(datos_brutos.get("concepto") or ""),
        "base_imponible": float(datos_brutos.get("base_imponible") or 0),
        "iva_porcentaje": int(datos_brutos.get("iva_porcentaje") or 21),
        "iva_cantidad": float(datos_brutos.get("iva_cantidad") or 0),
        "irpf_porcentaje": int(datos_brutos.get("irpf_porcentaje") or 0),
        "irpf_cantidad": float(datos_brutos.get("irpf_cantidad") or 0),
        "total": float(datos_brutos.get("total") or 0),
        "moneda": "EUR"
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    NUEVAS_DIR.mkdir(parents=True, exist_ok=True)
    INGRESOS_DIR.mkdir(parents=True, exist_ok=True)
    GASTOS_DIR.mkdir(parents=True, exist_ok=True)

    todas_exts = EXTENSIONES_PDF | EXTENSIONES_IMAGEN
    archivos = [f for f in NUEVAS_DIR.iterdir()
                if f.is_file() and f.suffix.lower() in todas_exts]

    if not archivos:
        print("No hay facturas nuevas.")
        return

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    procesados = 0
    for archivo in archivos:
        print(f"\nProcesando: {archivo.name}")
        ext = archivo.suffix.lower()
        datos = None

        if ext in EXTENSIONES_PDF:
            texto = extraer_texto_pdf(str(archivo))
            if texto.strip():
                datos_texto = extraer_datos_texto(texto)
                if datos_texto["total"] > 0:
                    datos = datos_texto
                    print(f"  pdfplumber OK — Total: {datos['total']} EUR")

        if not datos or datos.get("total", 0) == 0:
            print("  Usando Gemini Vision...")
            vision = extraer_con_gemini(str(archivo))
            if vision and float(vision.get("total") or 0) > 0:
                datos = vision
                print(f"  Gemini OK — Total: {datos['total']} EUR")
            elif vision:
                datos = vision
                print(f"  Gemini respondió pero total=0.")

        if not datos or datos.get("total", 0) == 0:
            print("  Intentando Tesseract...")
            texto_ocr = extraer_con_tesseract(str(archivo))
            if texto_ocr.strip():
                datos_ocr = extraer_datos_texto(texto_ocr)
                if datos_ocr["total"] > 0:
                    datos = datos_ocr
                    print(f"  Tesseract OK — Total: {datos['total']} EUR")

        if not datos:
            datos = {
                "numero": archivo.stem, "fecha": time.strftime("%Y-%m-%d"),
                "emisor": "", "receptor": "",
                "concepto": f"Revisar manualmente: {archivo.name}",
                "base_imponible": 0.0, "iva_porcentaje": 21,
                "iva_cantidad": 0.0, "irpf_porcentaje": 0,
                "irpf_cantidad": 0.0, "total": 0.0
            }

        registro = construir_registro(datos, archivo.name)
        dest_dir = INGRESOS_DIR if registro["tipo"] == "ingreso" else GASTOS_DIR

        data["facturas"] = [f for f in data["facturas"]
                            if f.get("numero") != registro["numero"]
                            or f.get("archivo") == registro["archivo"]]
        data["facturas"].append(registro)
        data["fecha_generacion"] = time.strftime("%Y-%m-%d")

        shutil.move(str(archivo), str(dest_dir / archivo.name))
        print(f"  Tipo: {registro['tipo']} | Total: {registro['total']} EUR | → {dest_dir.name}/")
        procesados += 1

    if procesados > 0:
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(JSON_PATH, 'r', encoding='utf-8') as f:
            json.load(f)
        print(f"\n{procesados} factura(s) procesada(s). JSON validado.")


if __name__ == "__main__":
    main()