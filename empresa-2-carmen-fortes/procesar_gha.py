"""
Procesa PDFs en facturas/nuevas/ — corre en GitHub Actions (nube).
Usa pdfplumber para extraer texto de forma fiable.
"""

import re, json, time, shutil
from pathlib import Path

try:
    import pdfplumber
    TIENE_PDFPLUMBER = True
except ImportError:
    TIENE_PDFPLUMBER = False

BASE_DIR = Path(__file__).parent
NUEVAS_DIR = BASE_DIR / "facturas" / "nuevas"
INGRESOS_DIR = BASE_DIR / "facturas" / "ingresos"
GASTOS_DIR = BASE_DIR / "facturas" / "gastos"
JSON_PATH = BASE_DIR / "facturas_datos.json"
EMISOR_PROPIO = "Carmen Fortes Pardo"


def extraer_texto(filepath):
    if TIENE_PDFPLUMBER:
        texto = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texto.append(t)
        return ' '.join(texto)
    return ''


def parsear_importe(texto):
    texto = texto.strip().replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        return float(texto)
    except Exception:
        return 0.0


def extraer_datos(texto, filename):
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
            print(f"  Sin texto extraible. Moviendo a gastos sin datos.")
            datos = {"archivo": f"gastos/{pdf.name}", "tipo": "gasto", "numero": pdf.stem,
                     "fecha": time.strftime("%Y-%m-%d"), "emisor": "", "receptor": "",
                     "concepto": f"PDF sin texto: {pdf.name}", "base_imponible": 0.0,
                     "iva_porcentaje": 21, "iva_cantidad": 0.0, "irpf_porcentaje": 0,
                     "irpf_cantidad": 0, "total": 0.0, "moneda": "EUR"}
        else:
            datos = extraer_datos(texto, pdf.name)
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
