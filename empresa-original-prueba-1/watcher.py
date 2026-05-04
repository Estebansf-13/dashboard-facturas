"""
WATCHER DE FACTURAS — Vigila facturas/nuevas/ y actualiza el dashboard automáticamente.

Uso: python watcher.py

Flujo:
1. Deja caer un PDF en facturas/nuevas/
2. Este script lo detecta, extrae los datos del PDF
3. Actualiza facturas_datos.json
4. Mueve el PDF a facturas/ingresos/ o facturas/gastos/
5. El dashboard (abierto en el navegador) se actualiza solo
"""

import os
import re
import json
import time
import zlib
import shutil
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Rutas
BASE_DIR = Path(__file__).parent
NUEVAS_DIR = BASE_DIR / "facturas" / "nuevas"
INGRESOS_DIR = BASE_DIR / "facturas" / "ingresos"
GASTOS_DIR = BASE_DIR / "facturas" / "gastos"
ARCHIVADAS_DIR = BASE_DIR / "facturas" / "archivadas"
JSON_PATH = BASE_DIR / "facturas_datos.json"

# Datos del emisor propio (para clasificar ingreso vs gasto)
EMISOR_PROPIO = "Estudio Creativo Vega SL"


def ascii85_decode(data: bytes) -> bytes:
    """Decodifica datos en formato ASCII85."""
    s = data
    if s.endswith(b'~>'):
        s = s[:-2]
    n = b_val = 0
    out = bytearray()
    for c in s:
        if c in (32, 9, 10, 13):
            continue
        if c == 122:  # 'z'
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


def extraer_texto_pdf(filepath: str) -> str:
    """Extrae texto de un PDF decodificando sus streams."""
    with open(filepath, 'rb') as f:
        data = f.read()

    texto_total = []

    # Buscar streams con diferentes patrones
    for pattern in [rb'stream\n(.*?)endstream', rb'stream\r\n(.*?)endstream']:
        streams = re.findall(pattern, data, re.DOTALL)
        for stream_data in streams:
            stream_data = stream_data.strip()
            try:
                # ASCII85 + FlateDecode
                decoded = ascii85_decode(stream_data)
                decompressed = zlib.decompress(decoded)
                # Extraer texto entre paréntesis (objetos de texto PDF)
                text_parts = re.findall(rb'\(([^)]*)\)', decompressed)
                for part in text_parts:
                    t = part.decode('latin-1', errors='replace').strip()
                    if t and t != ' ':
                        texto_total.append(t)
            except Exception:
                try:
                    # Intento directo con zlib
                    decompressed = zlib.decompress(stream_data)
                    text_parts = re.findall(rb'\(([^)]*)\)', decompressed)
                    for part in text_parts:
                        t = part.decode('latin-1', errors='replace').strip()
                        if t and t != ' ':
                            texto_total.append(t)
                except Exception:
                    pass

    return ' '.join(texto_total)


def parsear_importe(texto: str) -> float:
    """Convierte '1.234,56' o '1234.56' a float."""
    texto = texto.strip().replace(' ', '')
    # Formato español: 1.234,56
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    return float(texto)


def extraer_datos_factura(texto: str, filename: str) -> dict:
    """Parsea el texto extraído y devuelve un dict con los datos de la factura."""
    datos = {
        "archivo": "",
        "tipo": "gasto",
        "numero": "",
        "fecha": "",
        "emisor": "",
        "receptor": "",
        "concepto": "",
        "base_imponible": 0.0,
        "iva_porcentaje": 21,
        "iva_cantidad": 0.0,
        "irpf_porcentaje": 0,
        "irpf_cantidad": 0,
        "total": 0.0,
        "moneda": "EUR"
    }

    # Número de factura
    m = re.search(r'FACTURA\s+N[°ºo]\s+(\S+)', texto, re.IGNORECASE)
    if m:
        datos["numero"] = m.group(1)
    else:
        m = re.search(r'Numero\s+de\s+factura:\s+(\S+)', texto, re.IGNORECASE)
        if m:
            datos["numero"] = m.group(1)

    # Fecha
    m = re.search(r'Fecha\s+de\s+emisi[oó]n:\s+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', texto, re.IGNORECASE)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        datos["fecha"] = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    else:
        m = re.search(r'Fecha:\s+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', texto, re.IGNORECASE)
        if m:
            d, mo, y = m.group(1), m.group(2), m.group(3)
            datos["fecha"] = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"

    # Emisor (proveedor)
    m = re.search(r'EMISOR\s+\(proveedor\):\s+(.*?)(?:CIF|NIF|$)', texto, re.IGNORECASE)
    if m:
        emisor_raw = m.group(1).strip()
        # Limpiar
        emisor_parts = []
        for word in emisor_raw.split():
            if word in ('CIF/VAT:', 'CIF:', 'NIF:') or re.match(r'^[A-Z]-?\d', word):
                break
            emisor_parts.append(word)
        datos["emisor"] = ' '.join(emisor_parts).strip()

    # Si no encontró emisor con ese patrón, buscar alternativo
    if not datos["emisor"]:
        m = re.search(r'(?:Emisor|Proveedor|De):\s*([A-ZÁ-Ú][\w\s]+(?:SL|SA|SAU|Ltd))', texto, re.IGNORECASE)
        if m:
            datos["emisor"] = m.group(1).strip()

    # Receptor (FACTURAR A)
    m = re.search(r'FACTURAR\s+A:\s+(.*?)(?:CIF|NIF|Calle|$)', texto, re.IGNORECASE)
    if m:
        receptor_raw = m.group(1).strip()
        receptor_parts = []
        for word in receptor_raw.split():
            if word in ('CIF:', 'NIF:', 'Calle') or re.match(r'^[A-Z]-?\d', word):
                break
            receptor_parts.append(word)
        datos["receptor"] = ' '.join(receptor_parts).strip()

    if not datos["receptor"]:
        m = re.search(r'(?:Cliente|Para|Receptor):\s*([A-ZÁ-Ú][\w\s]+(?:SL|SA|SAU|Ltd))', texto, re.IGNORECASE)
        if m:
            datos["receptor"] = m.group(1).strip()

    # Concepto
    m = re.search(r'CONCEPTO\s+IMPORTE\s+(.*?)(?:Base\s+imponible|\d+[,\.]\d{2}\s+EUR)', texto, re.IGNORECASE)
    if m:
        concepto_raw = m.group(1).strip()
        # Limpiar espacios extra
        datos["concepto"] = ' '.join(concepto_raw.split())

    if not datos["concepto"]:
        m = re.search(r'(?:Concepto|Descripci[oó]n):\s+(.+?)(?:\d+[,\.]\d{2}|$)', texto, re.IGNORECASE)
        if m:
            datos["concepto"] = m.group(1).strip()

    # Base imponible
    m = re.search(r'Base\s+imponible\s+.*?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m:
        datos["base_imponible"] = parsear_importe(m.group(1))

    # IVA
    m = re.search(r'IVA\s+(\d+)%\s+.*?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m:
        datos["iva_porcentaje"] = int(m.group(1))
        datos["iva_cantidad"] = parsear_importe(m.group(2))

    # IRPF
    m = re.search(r'IRPF\s+[(-]?(\d+)%\)?\s+.*?[(-]?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m:
        datos["irpf_porcentaje"] = -int(m.group(1))
        datos["irpf_cantidad"] = -parsear_importe(m.group(2))

    # Total
    m = re.search(r'TOTAL\s+(?:FACTURA)?\s+.*?([\d.,]+)\s*EUR', texto, re.IGNORECASE)
    if m:
        datos["total"] = parsear_importe(m.group(1))

    # Clasificar: ingreso o gasto
    if EMISOR_PROPIO.lower() in datos["emisor"].lower():
        datos["tipo"] = "ingreso"
    elif EMISOR_PROPIO.lower() in datos["receptor"].lower():
        datos["tipo"] = "gasto"
    else:
        # Si el receptor es nuestro CIF, es gasto
        datos["tipo"] = "gasto"

    return datos


def publicar_en_github(numero: str):
    """Sube el JSON actualizado a GitHub para que el dashboard online se refresque."""
    try:
        rel_json = JSON_PATH.relative_to(BASE_DIR.parent)
        subprocess.run(['git', 'add', str(rel_json)], cwd=BASE_DIR.parent, check=True)
        subprocess.run(['git', 'commit', '-m', f'Empresa 1: factura {numero} añadida'], cwd=BASE_DIR.parent, check=True)
        subprocess.run(['git', 'push'], cwd=BASE_DIR.parent, check=True)
        print(f"  Dashboard online actualizado: https://estebansf-13.github.io/dashboard-facturas/empresa-original-prueba-1/dashboard-facturacion.html")
    except Exception as e:
        print(f"  No se pudo subir a GitHub: {e}")


def actualizar_json(datos_factura: dict):
    """Añade la factura al JSON y guarda."""
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Verificar que no existe ya (por número)
    for f_existente in data["facturas"]:
        if f_existente["numero"] == datos_factura["numero"]:
            print(f"  ⚠ Factura {datos_factura['numero']} ya existe, se actualiza.")
            data["facturas"].remove(f_existente)
            break

    data["facturas"].append(datos_factura)
    data["fecha_generacion"] = time.strftime("%Y-%m-%d")

    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def procesar_factura(filepath: str):
    """Procesa un PDF de factura: extrae datos, actualiza JSON, mueve archivo."""
    filename = os.path.basename(filepath)
    print(f"\n{'='*60}")
    print(f"📄 Nueva factura detectada: {filename}")
    print(f"{'='*60}")

    # 1. Extraer texto
    texto = extraer_texto_pdf(filepath)
    if not texto:
        print("  ❌ No se pudo extraer texto del PDF")
        return

    print(f"  📝 Texto extraído ({len(texto)} caracteres)")

    # 2. Parsear datos
    datos = extraer_datos_factura(texto, filename)
    print(f"  📋 Datos extraídos:")
    print(f"     Nº: {datos['numero']}")
    print(f"     Fecha: {datos['fecha']}")
    print(f"     Tipo: {datos['tipo'].upper()}")
    print(f"     Emisor: {datos['emisor']}")
    print(f"     Receptor: {datos['receptor']}")
    print(f"     Concepto: {datos['concepto']}")
    print(f"     Base: {datos['base_imponible']} €")
    print(f"     IVA {datos['iva_porcentaje']}%: {datos['iva_cantidad']} €")
    print(f"     IRPF: {datos['irpf_cantidad']} €")
    print(f"     Total: {datos['total']} €")

    # 3. Mover archivo
    if datos["tipo"] == "ingreso":
        dest_dir = INGRESOS_DIR
    else:
        dest_dir = GASTOS_DIR

    dest_path = dest_dir / filename
    datos["archivo"] = f"{datos['tipo']}s/{filename}" if datos['tipo'] == 'ingreso' else f"gastos/{filename}"

    shutil.move(filepath, str(dest_path))
    print(f"  📁 Movido a: {dest_path}")

    # 4. Actualizar JSON
    actualizar_json(datos)
    print(f"  ✅ JSON actualizado")

    # 5. Subir a GitHub
    publicar_en_github(datos["numero"])


def archivar_factura(filepath: str):
    """Elimina del JSON la factura cuyo PDF ha sido movido a archivadas/."""
    filename = os.path.basename(filepath)
    print(f"\n{'='*60}")
    print(f"Factura archivada: {filename}")
    print(f"{'='*60}")

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    antes = len(data["facturas"])
    data["facturas"] = [f for f in data["facturas"] if os.path.basename(f.get("archivo", "")) != filename]
    despues = len(data["facturas"])

    if antes == despues:
        print(f"  No se encontró {filename} en el JSON — nada que eliminar.")
        return

    data["fecha_generacion"] = time.strftime("%Y-%m-%d")
    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  Eliminada del dashboard. Subiendo a GitHub...")
    try:
        rel_json = JSON_PATH.relative_to(BASE_DIR.parent)
        subprocess.run(['git', 'add', str(rel_json)], cwd=BASE_DIR.parent, check=True)
        subprocess.run(['git', 'commit', '-m', f'Empresa 1: factura {filename} archivada'], cwd=BASE_DIR.parent, check=True)
        subprocess.run(['git', 'push'], cwd=BASE_DIR.parent, check=True)
        print(f"  Dashboard actualizado.")
    except Exception as e:
        print(f"  No se pudo subir a GitHub: {e}")


class ArchivarFacturaHandler(FileSystemEventHandler):
    """Handler que reacciona cuando un PDF aparece en facturas/archivadas/."""

    def __init__(self):
        self.procesados = set()

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = event.src_path
        if not filepath.lower().endswith('.pdf'):
            return
        if filepath in self.procesados:
            return
        time.sleep(1)
        self.procesados.add(filepath)
        try:
            archivar_factura(filepath)
        except Exception as e:
            print(f"  Error archivando {filepath}: {e}")


class NuevaFacturaHandler(FileSystemEventHandler):
    """Handler que reacciona cuando aparece un nuevo PDF en facturas/nuevas/."""

    def __init__(self):
        self.procesados = set()

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = event.src_path
        if not filepath.lower().endswith('.pdf'):
            return
        if filepath in self.procesados:
            return

        # Esperar a que el archivo termine de copiarse
        time.sleep(2)

        self.procesados.add(filepath)
        try:
            procesar_factura(filepath)
        except Exception as e:
            print(f"  ❌ Error procesando {filepath}: {e}")


def main():
    NUEVAS_DIR.mkdir(parents=True, exist_ok=True)
    INGRESOS_DIR.mkdir(parents=True, exist_ok=True)
    GASTOS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVADAS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Sincronizando con GitHub...")
    try:
        subprocess.run(['git', 'pull', 'origin', 'main'], cwd=BASE_DIR.parent, check=True, capture_output=True)
        print("Archivos actualizados.")
    except Exception:
        print("(Sin conexion o ya al dia)")

    print("=" * 60)
    print("WATCHER DE FACTURAS — Modo automatico")
    print("=" * 60)
    print(f"📂 Vigilando: {NUEVAS_DIR}")
    print(f"📊 JSON:      {JSON_PATH}")
    print()
    print("Deja caer un PDF en facturas/nuevas/ y se procesará solo.")
    print("Pulsa Ctrl+C para detener.")
    print("=" * 60)

    handler = NuevaFacturaHandler()
    archivar_handler = ArchivarFacturaHandler()
    observer = Observer()
    observer.schedule(handler, str(NUEVAS_DIR), recursive=False)
    observer.schedule(archivar_handler, str(ARCHIVADAS_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n🛑 Watcher detenido.")
    observer.join()


if __name__ == "__main__":
    main()
