"""
Comprueba si hay PDFs en facturas/nuevas/, los procesa y actualiza GitHub.
El Programador de tareas de Windows lo ejecuta cada 5 minutos en silencio.
"""

import os, re, json, time, zlib, shutil, subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent
NUEVAS_DIR = BASE_DIR / "facturas" / "nuevas"
INGRESOS_DIR = BASE_DIR / "facturas" / "ingresos"
GASTOS_DIR = BASE_DIR / "facturas" / "gastos"
JSON_PATH = BASE_DIR / "facturas_datos.json"
LOG_PATH = BASE_DIR / "procesar.log"
EMISOR_PROPIO = "Estudio Creativo Vega SL"


def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


def ascii85_decode(data):
    s = data
    if s.endswith(b'~>'):
        s = s[:-2]
    n = b_val = 0
    out = bytearray()
    for c in s:
        if c in (32, 9, 10, 13):
            continue
        if c == 122:
            out.extend(b'\x00\x00\x00\x00')
            continue
        c -= 33
        if c < 0 or c > 84:
            continue
        b_val = b_val * 85 + c
        n += 1
        if n == 5:
            out.extend(b_val.to_bytes(4, 'big'))
            n = b_val = 0
    if n:
        for _ in range(5 - n):
            b_val = b_val * 85 + 84
        out.extend(b_val.to_bytes(4, 'big')[:n - 1])
    return bytes(out)


def extraer_texto_pdf(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    texto_total = []
    for pattern in [rb'stream\n(.*?)endstream', rb'stream\r\n(.*?)endstream']:
        for stream_data in re.findall(pattern, data, re.DOTALL):
            stream_data = stream_data.strip()
            try:
                decompressed = zlib.decompress(ascii85_decode(stream_data))
                for part in re.findall(rb'\(([^)]*)\)', decompressed):
                    t = part.decode('latin-1', errors='replace').strip()
                    if t and t != ' ':
                        texto_total.append(t)
            except Exception:
                try:
                    decompressed = zlib.decompress(stream_data)
                    for part in re.findall(rb'\(([^)]*)\)', decompressed):
                        t = part.decode('latin-1', errors='replace').strip()
                        if t and t != ' ':
                            texto_total.append(t)
                except Exception:
                    pass
    return ' '.join(texto_total)


def parsear_importe(texto):
    texto = texto.strip().replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    return float(texto)


def extraer_datos(texto, filename):
    d = {"archivo": "", "tipo": "gasto", "numero": "", "fecha": "", "emisor": "",
         "receptor": "", "concepto": "", "base_imponible": 0.0, "iva_porcentaje": 21,
         "iva_cantidad": 0.0, "irpf_porcentaje": 0, "irpf_cantidad": 0, "total": 0.0, "moneda": "EUR"}
    m = re.search(r'FACTURA\s+N[°ºo]\s+(\S+)', texto, re.IGNORECASE) or re.search(r'Numero\s+de\s+factura:\s+(\S+)', texto, re.IGNORECASE)
    if m: d["numero"] = m.group(1)
    m = re.search(r'Fecha\s+de\s+emisi[oó]n:\s+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', texto, re.IGNORECASE) or re.search(r'Fecha:\s+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', texto, re.IGNORECASE)
    if m: d["fecha"] = f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    m = re.search(r'EMISOR\s+\(proveedor\):\s+(.*?)(?:CIF|NIF|$)', texto, re.IGNORECASE)
    if m:
        parts = []
        for w in m.group(1).strip().split():
            if w in ('CIF/VAT:', 'CIF:', 'NIF:') or re.match(r'^[A-Z]-?\d', w): break
            parts.append(w)
        d["emisor"] = ' '.join(parts).strip()
    if not d["emisor"]:
        m = re.search(r'(?:Emisor|Proveedor|De):\s*([A-ZÁ-Ú][\w\s]+(?:SL|SA|SAU|Ltd))', texto, re.IGNORECASE)
        if m: d["emisor"] = m.group(1).strip()
    m = re.search(r'FACTURAR\s+A:\s+(.*?)(?:CIF|NIF|Calle|$)', texto, re.IGNORECASE)
    if m:
        parts = []
        for w in m.group(1).strip().split():
            if w in ('CIF:', 'NIF:', 'Calle') or re.match(r'^[A-Z]-?\d', w): break
            parts.append(w)
        d["receptor"] = ' '.join(parts).strip()
    m = re.search(r'Base\s+imponible\s+.*?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m: d["base_imponible"] = parsear_importe(m.group(1))
    m = re.search(r'IVA\s+(\d+)%\s+.*?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m:
        d["iva_porcentaje"] = int(m.group(1))
        d["iva_cantidad"] = parsear_importe(m.group(2))
    m = re.search(r'IRPF\s+[(-]?(\d+)%\)?\s+.*?[(-]?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m:
        d["irpf_porcentaje"] = -int(m.group(1))
        d["irpf_cantidad"] = -parsear_importe(m.group(2))
    m = re.search(r'TOTAL\s+(?:FACTURA)?\s+.*?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m: d["total"] = parsear_importe(m.group(1))
    if EMISOR_PROPIO.lower() in d["emisor"].lower():
        d["tipo"] = "ingreso"
    return d


def main():
    NUEVAS_DIR.mkdir(parents=True, exist_ok=True)
    INGRESOS_DIR.mkdir(parents=True, exist_ok=True)
    GASTOS_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = list(NUEVAS_DIR.glob("*.pdf")) + list(NUEVAS_DIR.glob("*.PDF"))
    if not pdfs:
        return

    log(f"Encontrados {len(pdfs)} PDF(s) nuevos")

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    procesados = 0
    for pdf in pdfs:
        try:
            texto = extraer_texto_pdf(str(pdf))
            if not texto:
                log(f"  Sin texto en {pdf.name} — saltando")
                continue
            datos = extraer_datos(texto, pdf.name)
            datos["numero"] = datos["numero"] or pdf.stem
            dest_dir = INGRESOS_DIR if datos["tipo"] == "ingreso" else GASTOS_DIR
            datos["archivo"] = f"{'ingresos' if datos['tipo'] == 'ingreso' else 'gastos'}/{pdf.name}"
            data["facturas"] = [f for f in data["facturas"] if f["numero"] != datos["numero"]]
            data["facturas"].append(datos)
            data["fecha_generacion"] = time.strftime("%Y-%m-%d")
            shutil.move(str(pdf), str(dest_dir / pdf.name))
            log(f"  Procesado: {pdf.name} → {datos['tipo']} {datos['total']}€")
            procesados += 1
        except Exception as e:
            log(f"  Error en {pdf.name}: {e}")

    if procesados == 0:
        return

    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Si corre en GitHub Actions el workflow se encarga del push
    if os.environ.get('GITHUB_ACTIONS'):
        log(f"  JSON actualizado — el workflow de GitHub hará el commit")
        return

    try:
        rel_json = JSON_PATH.relative_to(BASE_DIR.parent)
        subprocess.run(['git', 'add', str(rel_json)], cwd=BASE_DIR.parent, check=True)
        subprocess.run(['git', 'commit', '-m', f'Empresa 1: {procesados} factura(s) nueva(s)'], cwd=BASE_DIR.parent, check=True)
        subprocess.run(['git', 'push'], cwd=BASE_DIR.parent, check=True)
        log(f"  Dashboard online actualizado")
    except Exception as e:
        log(f"  Error al subir a GitHub: {e}")


if __name__ == "__main__":
    main()
