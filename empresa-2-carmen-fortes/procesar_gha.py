"""
Procesa PDFs e imágenes en facturas/nuevas/ — corre en GitHub Actions (nube).

Cascada de métodos:
1. pdfplumber   — PDFs digitales con texto (rápido y gratis)
2. Gemini Vision — fotos, escaneados, cualquier documento (gratis, muy preciso)
3. Tesseract    — fallback si no hay clave Gemini

Formatos soportados: .pdf .PDF .jpg .jpeg .png .heic .heif .webp
"""

import re, json, time, shutil, os, io, smtplib
from email.mime.text import MIMEText
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
EMISOR_PROPIO = "Carmen Fortes Pardo"
NIF_PROPIO = "22972441"  # NIF sin letra para comparar flexible

EXTENSIONES_IMAGEN = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.bmp', '.tiff', '.tif'}
EXTENSIONES_PDF = {'.pdf'}

PROMPT_VISION = """Eres un experto en facturas españolas. Analiza esta factura y extrae los datos exactos.

Devuelve ÚNICAMENTE un JSON válido (sin texto adicional, sin bloques markdown ```):
{
  "numero": "número de factura o albarán (string)",
  "fecha": "YYYY-MM-DD",
  "emisor": "nombre completo de quien EMITE la factura (el vendedor/proveedor, el que cobra)",
  "receptor": "nombre completo de quien RECIBE la factura (el comprador/cliente, el que paga)",
  "concepto": "descripción breve del servicio o producto principal",
  "base_imponible": 0.00,
  "iva_porcentaje": 21,
  "iva_cantidad": 0.00,
  "irpf_porcentaje": 0,
  "irpf_cantidad": 0.00,
  "total": 0.00
}

Reglas IMPORTANTES para identificar emisor y receptor en facturas españolas:
- EMISOR = quien aparece en la CABECERA/ENCABEZADO de la factura (arriba, con sus datos fiscales, NIF, logo). Es quien VENDE o presta el servicio.
- RECEPTOR = quien aparece como CLIENTE o DESTINATARIO de la factura (sección "Datos del cliente", "Facturar a", "Cliente:"). Es quien PAGA.
- Si aparece "Carmen Fortes Pardo" con NIF 22972441H en la CABECERA → es el EMISOR.
- Si aparece "Carmen Fortes Pardo" en la sección de CLIENTE → es el RECEPTOR.
- Si la factura es una MINUTA DE HONORARIOS de Carmen Fortes Pardo → ella es el EMISOR.
- Los importes son números decimales con PUNTO (no coma): 430.51, no 430,51
- Si no hay IRPF, usa 0
- El total es el importe final a pagar (incluyendo IVA, menos IRPF si lo hay)
- Si hay Recargo de Equivalencia (R.E.), súmalo al IVA total
- La fecha en formato YYYY-MM-DD (ej: 2025-11-24)
- Si no encuentras algún campo, usa "" para texto o 0.00 para números
- NUNCA inventes datos: si no está claro, usa 0.00

FORMATO ESPECIAL — MINUTA DE HONORARIOS (procuradores, abogados):
En este tipo de documentos los campos aparecen SIN porcentaje explícito:
  "Base Imponible de Honorarios y Gastos   527,46"  → base_imponible = 527.46
  "+ I.V.A. sobre Honorarios y Gastos      110,77"  → iva_cantidad = 110.77 (calcula iva_porcentaje = round(110.77/527.46*100) = 21)
  "- IRPF sobre Honorarios y Gastos         79,12"  → irpf_cantidad = 79.12 (calcula irpf_porcentaje = round(79.12/527.46*100) = 15)
  "Total Minuta                            559,11"  → total = 559.11
ATENCIÓN: "Total Derechos" o "Total Honorarios" NO es el total final — ignóralos.
Verifica siempre que base_imponible + iva_cantidad - irpf_cantidad = total (con ±0.10 € de tolerancia).
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
    """Convierte imagen PIL a bytes JPEG para enviar a Gemini."""
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

def _limpiar_datos_gemini(datos):
    """Normaliza tipos numéricos en la respuesta de Gemini."""
    for campo in ['base_imponible', 'iva_cantidad', 'irpf_cantidad', 'total']:
        val = datos.get(campo, 0)
        if isinstance(val, str):
            val = parsear_importe(val)
        else:
            try:
                val = float(val or 0)
            except (TypeError, ValueError):
                val = 0.0
        datos[campo] = val
    for campo in ['iva_porcentaje', 'irpf_porcentaje']:
        val = datos.get(campo, 0)
        try:
            datos[campo] = int(float(str(val).replace(',', '.')))
        except (ValueError, TypeError):
            datos[campo] = 21 if campo == 'iva_porcentaje' else 0
    return datos


def validar_coherencia(datos):
    """True si base_imponible + iva_cantidad - irpf_cantidad ≈ total (±0.10 €)."""
    base = float(datos.get("base_imponible") or 0)
    iva = float(datos.get("iva_cantidad") or 0)
    irpf = abs(float(datos.get("irpf_cantidad") or 0))
    total = float(datos.get("total") or 0)
    if total <= 0 or base <= 0:
        return False
    return abs(base + iva - irpf - total) <= 0.10


def extraer_con_gemini_pdf(filepath):
    """Envía el PDF directamente a Gemini como archivo — lee texto Y layout visual.
    Funciona para PDFs digitales, escaneados y con fondos gráficos complejos.
    No necesita pdf2image ni poppler."""
    if not TIENE_GEMINI:
        return None
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        with open(str(filepath), 'rb') as f:
            pdf_bytes = f.read()
        client = google_genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[
                PROMPT_VISION,
                genai_types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf')
            ]
        )
        text = response.text.strip()
        print(f"  Gemini PDF: {text[:200]}...")
        datos = _limpiar_json_gemini(text)
        return _limpiar_datos_gemini(datos)
    except json.JSONDecodeError as e:
        print(f"  Error JSON Gemini PDF: {e}")
        return None
    except Exception as e:
        print(f"  Error Gemini PDF: {e}")
        return None


def extraer_con_gemini_texto(texto):
    """Fallback: envía texto extraído por pdfplumber a Gemini (sin pdf2image)."""
    if not TIENE_GEMINI:
        return None
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        client = google_genai.Client(api_key=api_key)
        prompt = PROMPT_VISION + f"\n\nTexto completo de la factura:\n\n{texto}"
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt
        )
        text = response.text.strip()
        print(f"  Gemini texto: {text[:200]}...")
        datos = _limpiar_json_gemini(text)
        return _limpiar_datos_gemini(datos)
    except json.JSONDecodeError as e:
        print(f"  Error JSON Gemini texto: {e}")
        return None
    except Exception as e:
        print(f"  Error Gemini texto: {e}")
        return None


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
            model='gemini-2.0-flash',
            contents=partes
        )
        text = response.text.strip()
        print(f"  Respuesta Gemini: {text[:200]}...")

        datos = _limpiar_json_gemini(text)
        return _limpiar_datos_gemini(datos)

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
    texto = str(texto).strip()
    negativo = texto.startswith('(') and texto.endswith(')')
    texto = re.sub(r'\s*(euros?|eur)\s*', '', texto, flags=re.IGNORECASE)
    texto = texto.replace('€', '').replace(' ', '').strip('()')
    if ',' in texto and '.' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif ',' in texto:
        texto = texto.replace(',', '.')
    try:
        val = float(texto)
        return -val if negativo else val
    except Exception:
        return 0.0


# ── Extracción con regex (pdfplumber / Tesseract) ─────────────────────────────

def extraer_datos_texto(texto):
    d = {
        "numero": "", "fecha": "", "emisor": "", "receptor": "", "concepto": "",
        "base_imponible": 0.0, "iva_porcentaje": 21, "iva_cantidad": 0.0,
        "irpf_porcentaje": 0, "irpf_cantidad": 0.0, "total": 0.0
    }

    # Número: busca "Nº", "No.", "N°", "Número de factura:" — nunca captura NIFs (que van sin "N")
    for pat in [
        r'[Nn][úu]mero\s+de\s+(?:factura|albar[aá]n)[:\s]+(\S+)',
        r'(?:FACTURA|Factura|ALBAR[AÁ]N)\s+[Nn][°ºo\.]\s*(\S+)',
        r'(?:FACTURA|Factura)\s+N[°ºo]\s*[:.\s]*(\S+)',
        r'(?:FACTURA|Factura)\s+([0-9]{1,6})\b',
    ]:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            d["numero"] = m.group(1).rstrip('.,')
            break

    # Fecha: primera fecha con formato dd/mm/aaaa o dd-mm-aaaa
    m = re.search(r'\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b', texto)
    if m:
        d["fecha"] = f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"

    # Emisor
    for pat in [
        r'EMISOR\s*\([^)]*\)[:\s]+([^\n]+)',
        r'(?:Emisor|Proveedor|De|Empresa)[:\s]+([^\n]+)',
        r'^([A-ZÁÉÍÓÚÑ][^\n]{3,40}(?:SL|SA|SAU|SLU|Ltd))',
    ]:
        m = re.search(pat, texto, re.IGNORECASE | re.MULTILINE)
        if m:
            d["emisor"] = m.group(1).strip()
            break

    # Receptor
    for pat in [
        r'FACTURAR\s+A[:\s]+([^\n]+)',
        r'(?:Cliente|Receptor|Destinatario|Para)[:\s]+([^\n]+)',
    ]:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            d["receptor"] = m.group(1).strip()
            break

    # Concepto
    m = re.search(r'(?:Concepto|Descripci[oó]n|Servicio)[:\s]+([^\n]+)', texto, re.IGNORECASE)
    if m:
        d["concepto"] = m.group(1).strip()

    # Base imponible — acepta "Base imponible:", "BASE IMPONIBLE", y
    # "Base Imponible de Honorarios y Gastos" (minuta de procurador/abogado)
    m = re.search(r'[Bb]ase\.?\s*[Ii]mponible[^0-9\n]*([0-9]+[.,][0-9]+)', texto)
    if not m:
        m = re.search(r'\bBASE[:\s€]+([0-9]+[.,][0-9]+)', texto)
    if m:
        d["base_imponible"] = parsear_importe(m.group(1))

    # IVA con porcentaje: "IVA 21% 378,00" / "I.V.A. 21%: 378,00"
    m = re.search(r'I\.?V\.?A\.?\s*(\d+)\s*%[^\S\n]*[:\s€]*([0-9]+(?:[.,][0-9]+)?)', texto, re.IGNORECASE)
    if m:
        d["iva_porcentaje"] = int(m.group(1))
        d["iva_cantidad"] = parsear_importe(m.group(2))
    else:
        # Minuta de honorarios: "+ I.V.A. sobre Honorarios y Gastos   110,77" (sin porcentaje)
        m = re.search(r'I\.?V\.?A\.[^0-9\n]*([0-9]+[.,][0-9]+)', texto, re.IGNORECASE)
        if m:
            d["iva_cantidad"] = parsear_importe(m.group(1))

    # IRPF con porcentaje: "IRPF -15% 79,12" / "IRPF (15%): 79,12"
    m = re.search(r'(?:Retenci[oó]n\s+)?IRPF\s*[\-\(]?\s*(\d+)\s*%\)?[^\S\n]*[:\s€]*([0-9]+(?:[.,][0-9]+)?)', texto, re.IGNORECASE)
    if m:
        d["irpf_porcentaje"] = int(m.group(1))
        d["irpf_cantidad"] = parsear_importe(m.group(2))
    else:
        # Sin porcentaje: "- IRPF sobre Honorarios y Gastos 79,12", "Retención IRPF: 79,12", "IRPF Retenido 79"
        m = re.search(r'(?:Retenci[oó]n\s+)?IRPF\b[^0-9\n%]*([0-9]+(?:[.,][0-9]+)?)', texto, re.IGNORECASE)
        if m:
            d["irpf_cantidad"] = parsear_importe(m.group(1))

    # TOTAL — prioriza keywords específicos antes que TOTAL genérico; admite enteros (sin decimales)
    m = re.search(
        r'TOTAL[^\S\n]+(?:FACTURA|NETO|A\s+PAGAR|MINUTA|IMPORTE)[^\S\n€]*([0-9]+(?:[.,][0-9]+)?)',
        texto, re.IGNORECASE
    )
    if not m:
        m = re.search(r'TOTAL\s*:[^\S\n€]*([0-9]+(?:[.,][0-9]+)?)', texto, re.IGNORECASE)
    if not m:
        m = re.search(r'\bTOTAL\b[^\S\n€]*([0-9]+(?:[.,][0-9]+)?)', texto, re.IGNORECASE)
    if m:
        d["total"] = parsear_importe(m.group(1))

    # Calcular porcentajes cuando solo tenemos cantidades (formato minuta)
    if d["iva_cantidad"] > 0 and d["base_imponible"] > 0 and d["iva_porcentaje"] == 21:
        pct = round(d["iva_cantidad"] / d["base_imponible"] * 100)
        if pct in [4, 10, 21]:
            d["iva_porcentaje"] = pct
    if d["irpf_cantidad"] > 0 and d["base_imponible"] > 0 and d["irpf_porcentaje"] == 0:
        pct = round(d["irpf_cantidad"] / d["base_imponible"] * 100)
        if 1 <= pct <= 20:
            d["irpf_porcentaje"] = pct

    # Reconstruir campos que falten — solo si no tenemos base NI IVA (fallback de último recurso)
    if d["base_imponible"] == 0 and d["iva_cantidad"] == 0 and d["total"] > 0:
        d["base_imponible"] = round(d["total"] / 1.21, 2)
        d["iva_cantidad"] = round(d["total"] - d["base_imponible"], 2)
        d["_base_estimada"] = True  # base reconstruida asumiendo 21% — puede ser incorrecto
    elif d["iva_cantidad"] == 0 and d["base_imponible"] > 0 and d["total"] > d["base_imponible"]:
        d["iva_cantidad"] = round(d["total"] - d["base_imponible"] - abs(d["irpf_cantidad"]), 2)
    elif d["total"] == 0 and d["base_imponible"] > 0:
        d["total"] = round(d["base_imponible"] + d["iva_cantidad"] - d["irpf_cantidad"], 2)

    return d


# ── Construcción del registro final ──────────────────────────────────────────

def es_incierto(datos):
    """True solo cuando los tres métodos fallaron por completo (fallback de emergencia)."""
    concepto = str(datos.get("concepto") or "")
    return concepto.startswith("Revisar manualmente")


def enviar_email_revision(facturas_pendientes):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_pass:
        print("  GMAIL_USER/GMAIL_APP_PASSWORD no configuradas — saltando email.")
        return

    destinatario = "esteban.saurafortes@gmail.com"
    lineas = "\n".join(f"  • {f['archivo']} — {f['concepto']}" for f in facturas_pendientes)
    cuerpo = f"""Hola Carmen,

El sistema ha procesado {len(facturas_pendientes)} factura(s) que necesitan revisión manual porque no se pudieron leer con suficiente claridad:

{lineas}

Accede al dashboard para ver los detalles y corregir los datos:
https://estebansf-13.github.io/dashboard-facturas/empresa-2-carmen-fortes/dashboard-facturacion.html

Las facturas pendientes aparecen marcadas con ⚠ REVISAR en la tabla y no están incluidas en los totales hasta que las confirmes.

— Sistema automático Dashboard Carmen Fortes Pardo
"""
    msg = MIMEText(cuerpo, 'plain', 'utf-8')
    msg['Subject'] = f'⚠ {len(facturas_pendientes)} factura(s) pendiente(s) de revisión — Carmen Fortes'
    msg['From'] = gmail_user
    msg['To'] = destinatario

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, destinatario, msg.as_string())
        print(f"  Email enviado a {destinatario}")
    except Exception as e:
        print(f"  Error enviando email: {e}")


def _es_propio(texto):
    """True si el texto contiene el nombre o NIF de Carmen Fortes Pardo."""
    t = texto.lower()
    return EMISOR_PROPIO.lower() in t or NIF_PROPIO in t

def construir_registro(datos_brutos, nombre_archivo):
    emisor = str(datos_brutos.get("emisor") or "").strip()
    receptor = str(datos_brutos.get("receptor") or "").strip()

    # ingreso si Carmen es la emisora; gasto si es la receptora o si no se detecta
    if _es_propio(emisor) and not _es_propio(receptor):
        tipo = "ingreso"
    elif _es_propio(receptor) and not _es_propio(emisor):
        tipo = "gasto"
    elif _es_propio(emisor) and _es_propio(receptor):
        # ambos son Carmen (raro) — tratar como ingreso
        tipo = "ingreso"
    else:
        tipo = "gasto"  # sin información suficiente → gasto por defecto

    irpf_pct = abs(int(datos_brutos.get("irpf_porcentaje") or 0))
    irpf_cant = abs(float(datos_brutos.get("irpf_cantidad") or 0))

    registro = {
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
        "irpf_porcentaje": irpf_pct,
        "irpf_cantidad": -irpf_cant if irpf_cant > 0 else 0.0,
        "total": float(datos_brutos.get("total") or 0),
        "moneda": "EUR",
        "estado": "confirmado"
    }

    # Marcar pendiente si base es 0 (Gemini no la extrajo) o fue estimada al 21%
    if (registro["base_imponible"] <= 0 and registro["total"] > 0) or datos_brutos.get("_base_estimada"):
        registro["estado"] = "pendiente_revision"

    return registro


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

        # Paso 1: Gemini con PDF nativo (lee texto + layout visual)
        if ext in EXTENSIONES_PDF:
            print("  Enviando PDF nativo a Gemini...")
            datos = extraer_con_gemini_pdf(str(archivo))
            if datos and validar_coherencia(datos):
                print(f"  Gemini PDF OK — Total: {datos['total']} EUR")
            else:
                if datos and float(datos.get("total") or 0) > 0:
                    print(f"  Gemini PDF: datos incoherentes (base+IVA-IRPF≠total) — reintentando con visión...")
                datos = None

        # Paso 2: Gemini Vision con imagen (PDF → JPEG → Gemini)
        # Necesario para PDFs con tablas complejas o gráficos donde el texto extraído es confuso
        if not datos:
            print("  Gemini Vision (imagen)...")
            vision = extraer_con_gemini(str(archivo))
            if vision and validar_coherencia(vision):
                datos = vision
                print(f"  Gemini Vision OK — Total: {datos['total']} EUR")
            elif vision and float(vision.get("total") or 0) > 0:
                datos = vision
                print(f"  Gemini Vision OK (sin base explícita) — Total: {datos['total']} EUR")

        # Paso 3: Tesseract (solo si Gemini no está disponible — sin GEMINI_API_KEY)
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
        if es_incierto(datos):
            registro["estado"] = "pendiente_revision"
            print(f"  ⚠ Datos incompletos — marcado para revisión manual.")
        dest_dir = INGRESOS_DIR if registro["tipo"] == "ingreso" else GASTOS_DIR

        # Deduplicar por ruta de archivo (no por número, que puede coincidir entre proveedores)
        data["facturas"] = [f for f in data["facturas"]
                            if f.get("archivo") != registro["archivo"]]
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

        pendientes = [f for f in data["facturas"] if f.get("estado") == "pendiente_revision"]
        if pendientes:
            print(f"\n{len(pendientes)} factura(s) requieren revisión — enviando email...")
            enviar_email_revision(pendientes)


if __name__ == "__main__":
    main()
