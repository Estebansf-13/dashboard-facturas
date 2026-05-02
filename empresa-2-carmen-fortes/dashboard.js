// === DASHBOARD DINÁMICO — Auto-recarga desde facturas_datos.json ===

let lastJSON = '';
const MESES = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
const MESES_CORTO = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];

function fmt(n) {
  return n.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtInt(n) {
  return n.toLocaleString('es-ES', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function mesLabel(key) {
  const [y, m] = key.split('-');
  return MESES_CORTO[parseInt(m) - 1] + " '" + y.slice(2);
}

function trimestre(fecha) {
  const m = parseInt(fecha.split('-')[1]);
  const y = fecha.split('-')[0];
  const q = Math.ceil(m / 3);
  return `Q${q} ${y}`;
}

async function cargarDatos() {
  try {
    const r = await fetch('facturas_datos.json?t=' + Date.now());
    if (!r.ok) throw new Error(`No se pudo cargar el archivo de datos (HTTP ${r.status})`);
    const text = await r.text();
    if (text === lastJSON) return;
    let data;
    try {
      data = JSON.parse(text);
    } catch (_) {
      throw new Error('El archivo de datos está dañado. Contacta con soporte.');
    }
    lastJSON = text;
    ocultarError();
    renderDashboard(data);
    mostrarRecarga();
  } catch (e) {
    mostrarError(e.message);
    console.error('Error cargando datos:', e);
  }
}

function mostrarError(msg) {
  const el = document.getElementById('d-empresa');
  if (el) { el.textContent = '⚠ ' + msg; el.style.color = '#ff6b6b'; }
}

function ocultarError() {
  const el = document.getElementById('d-empresa');
  if (el) { el.style.color = ''; }
}

function mostrarRecarga() {
  const ind = document.getElementById('reload-indicator');
  if (ind) {
    ind.style.opacity = '1';
    setTimeout(() => { ind.style.opacity = '0'; }, 1500);
  }
}

function renderDashboard(data) {
  const facturas = data.facturas;

  // Actualizar nombre de empresa siempre
  document.getElementById('d-empresa').textContent = data.emisor_propio.nombre + ' · CIF ' + data.emisor_propio.cif;

  // Estado vacío: sin facturas todavía
  if (facturas.length === 0) {
    document.getElementById('d-periodo').innerHTML = '<strong>Sin facturas todavía</strong>';
    document.getElementById('d-conteo').innerHTML = '0 facturas procesadas';
    document.getElementById('kpi-ing').innerHTML = '0,00<span class="currency">€</span>';
    document.getElementById('kpi-ing-sub').textContent = '0 facturas emitidas';
    document.getElementById('kpi-gas').innerHTML = '0,00<span class="currency">€</span>';
    document.getElementById('kpi-gas-sub').textContent = '0 facturas recibidas';
    document.getElementById('kpi-ben').innerHTML = '0,00<span class="currency">€</span>';
    document.getElementById('kpi-ben-sub').textContent = 'Margen 0,00 %';
    document.getElementById('kpi-iva').innerHTML = '0,00<span class="currency">€</span>';
    document.getElementById('kpi-iva-sub').textContent = '0,00 € repercutido − 0,00 € soportado';
    document.getElementById('kpi-irpf').innerHTML = '0,00<span class="currency">€</span>';
    document.getElementById('kpi-cobrado').innerHTML = '0,00<span class="currency">€</span>';
    document.getElementById('chart').innerHTML = '<div style="width:100%;display:flex;align-items:center;justify-content:center;color:var(--text-faint);font-size:13px;">Sin datos aún — mete facturas en facturas/nuevas/</div>';
    document.getElementById('chart-labels').innerHTML = '';
    document.getElementById('trim-body').innerHTML = '<tr><td colspan="4" style="color:var(--text-faint);text-align:center;padding:20px">Sin facturas</td></tr>';
    document.getElementById('clientes').innerHTML = '<p style="color:var(--text-faint);font-size:13px;text-align:center;padding:20px 0">Sin clientes todavía</p>';
    document.getElementById('alertas').innerHTML = '<div class="alert info"><span class="icon">i</span><div><strong>Dashboard listo.</strong> Mete los PDFs de las facturas en la carpeta <strong>facturas/nuevas/</strong> y el dashboard se actualizará automáticamente.</div></div>';
    document.getElementById('tabla-body').innerHTML = '<tr><td colspan="9" style="color:var(--text-faint);text-align:center;padding:20px">Sin facturas procesadas todavía</td></tr>';
    document.getElementById('footer-fecha').textContent = 'Dashboard activo · Sin facturas todavía · Auto-recarga activa';
    return;
  }

  const ingresos = facturas.filter(f => f.tipo === 'ingreso');
  const gastos = facturas.filter(f => f.tipo === 'gasto');

  const totalIng = ingresos.reduce((s, f) => s + f.base_imponible, 0);
  const totalGas = gastos.reduce((s, f) => s + f.base_imponible, 0);
  const beneficio = totalIng - totalGas;
  const margen = totalIng > 0 ? (beneficio / totalIng * 100) : 0;

  const ivaRep = ingresos.reduce((s, f) => s + f.iva_cantidad, 0);
  const ivaSop = gastos.reduce((s, f) => s + f.iva_cantidad, 0);
  const ivaLiq = ivaRep - ivaSop;

  const irpf = Math.abs(ingresos.reduce((s, f) => s + f.irpf_cantidad, 0));
  const totalCobrado = ingresos.reduce((s, f) => s + f.total, 0);
  const ticketMedio = ingresos.length > 0 ? totalIng / ingresos.length : 0;

  // Rango de fechas
  const fechas = facturas.map(f => f.fecha).sort();
  const fechaMin = fechas[0], fechaMax = fechas[fechas.length - 1];
  const mMin = parseInt(fechaMin.split('-')[1]) - 1, yMin = fechaMin.split('-')[0];
  const mMax = parseInt(fechaMax.split('-')[1]) - 1, yMax = fechaMax.split('-')[0];
  const rangoTexto = `${MESES[mMin]} ${yMin} — ${MESES[mMax]} ${yMax}`;

  // Header
  document.getElementById('d-periodo').innerHTML = '<strong>Periodo:</strong> ' + rangoTexto;
  document.getElementById('d-conteo').innerHTML = `<strong>${facturas.length} facturas procesadas</strong> (${ingresos.length} ingresos + ${gastos.length} gastos)`;

  // KPIs
  document.getElementById('kpi-ing').innerHTML = fmt(totalIng) + '<span class="currency">€</span>';
  document.getElementById('kpi-ing-sub').textContent = `${ingresos.length} facturas emitidas · ticket medio ${fmtInt(ticketMedio)} €`;
  document.getElementById('kpi-gas').innerHTML = fmt(totalGas) + '<span class="currency">€</span>';
  document.getElementById('kpi-gas-sub').textContent = `${gastos.length} facturas recibidas`;
  document.getElementById('kpi-ben').innerHTML = fmt(beneficio) + '<span class="currency">€</span>';
  document.getElementById('kpi-ben-sub').textContent = `Margen ${fmt(margen).replace(',', ',')} %`;
  document.getElementById('kpi-iva').innerHTML = fmt(ivaLiq) + '<span class="currency">€</span>';
  document.getElementById('kpi-iva-sub').textContent = `${fmt(ivaRep)} € repercutido − ${fmt(ivaSop)} € soportado`;
  document.getElementById('kpi-irpf').innerHTML = fmt(irpf) + '<span class="currency">€</span>';
  document.getElementById('kpi-cobrado').innerHTML = fmt(totalCobrado) + '<span class="currency">€</span>';

  // === GRÁFICO MENSUAL ===
  const meses = {};
  facturas.forEach(f => {
    const key = f.fecha.substring(0, 7);
    if (!meses[key]) meses[key] = { ing: 0, gas: 0 };
    if (f.tipo === 'ingreso') meses[key].ing += f.base_imponible;
    else meses[key].gas += f.base_imponible;
  });
  const sortedKeys = Object.keys(meses).sort();
  const maxVal = Math.max(...sortedKeys.map(k => Math.max(meses[k].ing, meses[k].gas)));

  let chartHTML = '';
  let labelsHTML = '';
  sortedKeys.forEach(k => {
    const pIng = maxVal > 0 ? (meses[k].ing / maxVal * 100) : 0;
    const pGas = maxVal > 0 ? (meses[k].gas / maxVal * 100) : 0;
    chartHTML += `<div class="month"><div class="bars">
      <div class="bar income" style="height:${pIng.toFixed(2)}%"><div class="tooltip">Ingresos: ${fmt(meses[k].ing)} €</div></div>
      <div class="bar expense" style="height:${Math.max(pGas, 0.3).toFixed(2)}%"><div class="tooltip">Gastos: ${fmt(meses[k].gas)} €</div></div>
    </div></div>`;
    labelsHTML += `<div class="month-label">${mesLabel(k)}</div>`;
  });
  document.getElementById('chart').innerHTML = chartHTML;
  document.getElementById('chart-labels').innerHTML = labelsHTML;

  // === TRIMESTRES IVA ===
  const trims = {};
  facturas.forEach(f => {
    const q = trimestre(f.fecha);
    if (!trims[q]) trims[q] = { rep: 0, sop: 0 };
    if (f.tipo === 'ingreso') trims[q].rep += f.iva_cantidad;
    else trims[q].sop += f.iva_cantidad;
  });
  const sortedTrims = Object.keys(trims).sort((a, b) => {
    const [qa, ya] = [a[1], a.slice(3)], [qb, yb] = [b[1], b.slice(3)];
    return ya === yb ? qa.localeCompare(qb) : ya.localeCompare(yb);
  });
  let trimHTML = '';
  sortedTrims.forEach(q => {
    const liq = trims[q].rep - trims[q].sop;
    trimHTML += `<tr><td><strong>${q}</strong></td><td class="num">${fmt(trims[q].rep)} €</td><td class="num">${fmt(trims[q].sop)} €</td><td class="num"><strong>${fmt(liq)} €</strong></td></tr>`;
  });
  trimHTML += `<tr style="border-top:2px solid var(--border)"><td><strong>Total</strong></td><td class="num"><strong>${fmt(ivaRep)} €</strong></td><td class="num"><strong>${fmt(ivaSop)} €</strong></td><td class="num"><strong style="color:var(--yellow)">${fmt(ivaLiq)} €</strong></td></tr>`;
  document.getElementById('trim-body').innerHTML = trimHTML;

  // === TOP CLIENTES ===
  const clientes = {};
  ingresos.forEach(f => {
    if (!clientes[f.receptor]) clientes[f.receptor] = { total: 0, count: 0 };
    clientes[f.receptor].total += f.base_imponible;
    clientes[f.receptor].count++;
  });
  const sortedCli = Object.entries(clientes).sort((a, b) => b[1].total - a[1].total);
  const maxCli = sortedCli.length > 0 ? sortedCli[0][1].total : 1;
  let cliHTML = '';
  sortedCli.forEach(([nombre, d]) => {
    const pct = (d.total / totalIng * 100);
    const barW = (d.total / maxCli * 100);
    const fLabel = d.count === 1 ? '1 factura' : `${d.count} facturas`;
    cliHTML += `<div class="cliente-row">
      <div class="cliente-info">
        <div class="cliente-nombre">${nombre} <span class="cliente-importe">${fmtInt(d.total)} € · ${fLabel}</span></div>
        <div class="cliente-bar-wrap"><div class="cliente-bar" style="width:${barW.toFixed(2)}%"></div></div>
      </div>
      <div class="cliente-pct">${fmt(pct)} %</div>
    </div>`;
  });
  document.getElementById('clientes').innerHTML = cliHTML;

  // === ALERTAS ===
  let alertHTML = '';
  if (sortedTrims.length >= 2) {
    const last = sortedTrims[sortedTrims.length - 1];
    const prev = sortedTrims[sortedTrims.length - 2];
    const ingLast = Object.entries(meses).filter(([k]) => trimestre(k + '-01') === last).reduce((s, [, v]) => s + v.ing, 0);
    const ingPrev = Object.entries(meses).filter(([k]) => trimestre(k + '-01') === prev).reduce((s, [, v]) => s + v.ing, 0);
    if (ingPrev > 0) {
      const crec = ((ingLast - ingPrev) / ingPrev * 100);
      if (crec > 0) {
        alertHTML += `<div class="alert success"><span class="icon">↗</span><div><strong>Tendencia ascendente.</strong> ${last} suma ${fmtInt(ingLast)} € frente a ${fmtInt(ingPrev)} € del trimestre anterior. Crecimiento del +${fmt(crec)} %.</div></div>`;
      } else {
        alertHTML += `<div class="alert warning"><span class="icon">↘</span><div><strong>Tendencia descendente.</strong> ${last} suma ${fmtInt(ingLast)} € frente a ${fmtInt(ingPrev)} € del trimestre anterior. Caída del ${fmt(crec)} %.</div></div>`;
      }
    }
  }
  if (sortedCli.length > 0) {
    const topPct = (sortedCli[0][1].total / totalIng * 100);
    if (topPct > 30) {
      alertHTML += `<div class="alert warning"><span class="icon">!</span><div><strong>Concentración de cliente.</strong> ${sortedCli[0][0]} representa el ${fmt(topPct)} % de toda la facturación.${topPct < 40 ? ' Por debajo del umbral crítico del 40 %, pero conviene diversificar.' : ' ¡Supera el umbral crítico del 40 %!'}</div></div>`;
    }
  }
  if (irpf > 0) {
    alertHTML += `<div class="alert info"><span class="icon">i</span><div><strong>IRPF retenido por clientes:</strong> ${fmt(irpf)} € en el periodo. Recuérdate de incluirlo en la próxima declaración para deducirlo de tu cuota del IRPF.</div></div>`;
  }
  if (sortedKeys.length > 0) {
    let best = sortedKeys[0], worst = sortedKeys[0];
    sortedKeys.forEach(k => {
      if (meses[k].ing > meses[best].ing) best = k;
      if (meses[k].ing < meses[worst].ing) worst = k;
    });
    const [yB, mB] = best.split('-');
    const [yW, mW] = worst.split('-');
    alertHTML += `<div class="alert info"><span class="icon">i</span><div><strong>Mejor mes:</strong> ${MESES[parseInt(mB)-1]} ${yB} con ${fmtInt(meses[best].ing)} € · <strong>peor mes:</strong> ${MESES[parseInt(mW)-1]} ${yW} con ${fmtInt(meses[worst].ing)} €.</div></div>`;
  }
  if (sortedTrims.length > 0) {
    const lastQ = sortedTrims[sortedTrims.length - 1];
    const liq = trims[lastQ].rep - trims[lastQ].sop;
    alertHTML += `<div class="alert info"><span class="icon">i</span><div><strong>Próxima liquidación de IVA (${lastQ}):</strong> ${fmt(liq)} € a ingresar.</div></div>`;
  }
  document.getElementById('alertas').innerHTML = alertHTML;

  // === TABLA DETALLE ===
  const sorted = [...facturas].sort((a, b) => a.fecha.localeCompare(b.fecha));
  let tablaHTML = '';
  sorted.forEach(f => {
    const [y, m, d] = f.fecha.split('-');
    const fechaFmt = `${d}/${m}/${y.slice(2)}`;
    const esIngreso = f.tipo === 'ingreso';
    const badge = esIngreso ? '<span class="badge ingreso">Ingreso</span>' : '<span class="badge gasto">Gasto</span>';
    const nombre = esIngreso ? f.receptor : f.emisor;
    const nombreCorto = nombre.replace(/ SL$| SA$| SAU$| Ltd$/, '').substring(0, 25);
    const concepto = f.concepto.substring(0, 30);
    const irpfCell = f.irpf_cantidad < 0 ? `<td class="num neg">−${fmt(Math.abs(f.irpf_cantidad))}</td>` : '<td class="num">—</td>';
    tablaHTML += `<tr><td>${badge}</td><td>${fechaFmt}</td><td>${f.numero}</td><td>${nombreCorto}</td><td>${concepto}</td><td class="num">${fmt(f.base_imponible)}</td><td class="num">${fmt(f.iva_cantidad)}</td>${irpfCell}<td class="num"><strong>${fmt(f.total)}</strong></td></tr>`;
  });
  document.getElementById('tabla-body').innerHTML = tablaHTML;

  // Footer
  const hoy = new Date();
  document.getElementById('footer-fecha').textContent = `Generado el ${hoy.getDate()} de ${MESES[hoy.getMonth()]} de ${hoy.getFullYear()} · Datos de ${facturas.length} facturas · Auto-recarga activa`;
}

// Carga inicial + auto-recarga cada 3 segundos
cargarDatos();
setInterval(cargarDatos, 3000);
