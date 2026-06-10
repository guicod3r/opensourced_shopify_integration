const COL_FECHA = 1;
const COL_PUNTO_VENTA = 2;
const COL_PEDIDO = 3;
const COL_ESTADO = 4;
const COL_ESTADO_PAGO = 5;
const COL_CANTIDAD = 6;
const COL_CLIENTE = 7;
const COL_CI = 8;
const COL_TELEFONO = 9;
const COL_CATEGORIA = 10;
const COL_PRODUCTO = 11;
const COL_COLOR = 12;
const COL_TALLE = 13;
const COL_VARIANTE = 18;
const COL_CONFIRMAR = 19;
const COL_COMENTARIO = 20;
const COL_DEVOLVER = 21;
const HOJA = "test"

/**
 * Agrega el menú personalizado al abrir la planilla.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('🛍️ Tienda')
    .addItem('Buscar producto', 'abrirSidebar')
    .addToUi();
}

/**
 * Abre el panel lateral con la UI de búsqueda.
 */
function abrirSidebar() {
  const html = HtmlService.createHtmlOutputFromFile('Sidebar')
    .setTitle('Búsqueda Shopify')
    .setWidth(350);
  SpreadsheetApp.getUi().showSidebar(html);
}

/**
 * Obtiene el token OIDC usando la cuenta del script para autenticar contra Cloud Functions.
 */
function getCloudFunctionToken() {
  const oauthToken = ScriptApp.getOAuthToken();

  const serviceAccount = 'sa@account.com';
  const urlCF = PropertiesService.getScriptProperties().getProperty('CLOUD_FUNCTION_URL');

  const url = `https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${serviceAccount}:generateIdToken`;

  const payload = {
    "audience": urlCF,
    "includeEmail": true
  };

  const options = {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: `Bearer ${oauthToken}`
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(url, options);

  if (response.getResponseCode() !== 200) {
    throw new Error('Error IAM Credentials: ' + response.getContentText());
  }

  return JSON.parse(response.getContentText()).token;
}

/**
 * Llamado desde Sidebar.html. 
 * Busca productos a través del proxy seguro en la Cloud Function.
 */
function buscarProductos(query) {
  if (!query || query.length < 2) return [];

  const CF_URL = PropertiesService.getScriptProperties().getProperty('CLOUD_FUNCTION_URL');
  
  const token = getCloudFunctionToken();
  try {
    const response = UrlFetchApp.fetch(`${CF_URL}/${HOJA}/search`, {
      method: 'POST',
      contentType: 'application/json',
      headers: { 'Authorization': `Bearer ${token}` },
      payload: JSON.stringify({ "query": query }),
      muteHttpExceptions: true
    });

    if (response.getResponseCode() !== 200) {
      console.error("Error CF:", response.getContentText());
      throw new Error("HTTP " + response.getResponseCode());
    }
    return JSON.parse(response.getContentText());
  } catch (e) {
    console.error("Error al buscar Productos:", e);
    throw new Error("No se pudo conectar a Shopify: " + e.message);
  }
}

function obtenerFecha() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "dd/MM/yyyy");
}

/**
 * Busca la primera fila donde la columna de FECHA esté vacía usando búsqueda binaria (O(log n)).
 */
function obtenerPrimeraFilaVacia(hoja) {
  const maxRows = hoja.getMaxRows();
  const valoresFecha = hoja.getRange(1, COL_FECHA, maxRows, 1).getValues();

  let inicio = 0;
  let fin = valoresFecha.length - 1;
  let primeraVacia = valoresFecha.length; // Por defecto la siguiente al final

  while (inicio <= fin) {
    let medio = Math.floor((inicio + fin) / 2);

    if (valoresFecha[medio][0] === "" || valoresFecha[medio][0] == null) {
      primeraVacia = medio;
      fin = medio - 1;
    } else {
      inicio = medio + 1;
    }
  }

  // Las filas en Google Sheets son 1-indexed
  return primeraVacia + 1;
}

/**
 * Llamado desde Sidebar.html. Escribe el producto seleccionado en la primera fila disponible.
 */
function escribirProductoEnFila(nombre, id_variante, talle, color) {
  const hoja = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  if (hoja.getName() != "Ventas") {
    console.error("No se puede buscar productos en esta hoja");
    return;
  }

  const filaDestino = obtenerPrimeraFilaVacia(hoja);

  let row = new Array(21).fill("");
  row[COL_PRODUCTO - 1] = nombre;
  row[COL_VARIANTE - 1] = id_variante;
  row[COL_TALLE - 1] = talle;
  row[COL_COLOR - 1] = color;
  row[COL_FECHA - 1] = obtenerFecha();
  row[COL_PUNTO_VENTA - 1] = "LOCAL";

  hoja.getRange(filaDestino, 1, 1, COL_DEVOLVER).setValues([row]);
}

/**
 * Listener disparado cuando se edita una celda (para confirmacion / devolucion).
 */
function onEditTrigger(e) {
  if (!e || !e.range) return;
  const range = e.range;
  const hoja = range.getSheet();
  const fila = range.getRow();
  const columna = range.getColumn();

  if (range.getNumRows() > 1 || range.getNumColumns() > 1 || columna !== COL_CONFIRMAR && columna !== COL_DEVOLVER) return;

  hoja.getRange(fila, 1, 1, COL_COMENTARIO).setBackground('#FFF3CD');
  hoja.getRange(fila, COL_COMENTARIO).setValue('⏳ Sincronizando...');

  if (columna == COL_CONFIRMAR) confirmar(range);
  else if (columna == COL_DEVOLVER && hoja.getRange(fila, COL_CONFIRMAR).getValue() == true) devolver(range) //checkea que este marcado como confirmado para devolver
}

function confirmar(range) {
  const hoja = range.getSheet();
  const fila = range.getRow();
  const varianteId = hoja.getRange(fila, COL_VARIANTE).getValue();
  const cantidad = hoja.getRange(fila, COL_CANTIDAD).getValue();
  if (!varianteId || !cantidad) {
    registrarResultado(hoja, fila, false, 'Faltan datos: Variante ID o Cantidad están vacíos');
    return;
  }

  const resultado = actualizarStock(varianteId, -cantidad);//resta

  if (resultado.success) {
    registrarResultado(hoja, fila, true);
    bloquearCeldas(`A${fila}:T${fila}`)
  } else {
    registrarResultado(hoja, fila, false, resultado.error);
  }
}

function devolver(range) {
  const hoja = range.getSheet();
  const fila = range.getRow();
  const varianteId = hoja.getRange(fila, COL_VARIANTE).getValue();
  const cantidad = hoja.getRange(fila, COL_CANTIDAD).getValue()

  const resultado = actualizarStock(varianteId, cantidad);

  if (resultado.success) {
    desbloquearCeldas(range);
    hoja.getRange(fila, 1, 1, COL_COMENTARIO).setBackground('#C3E6CB');
    hoja.getRange(fila, COL_COMENTARIO).setValue('↩️ Devuelto');
    bloquearCeldas(`A${fila}:U${fila}`);
  } else {
    desbloquearCeldas(range);
    hoja.getRange(fila, COL_DEVOLVER).setValue(false);
    hoja.getRange(fila, COL_COMENTARIO).setValue('❌ ' + resultado.error);
  }
}

/**
 * Petición a la Cloud Function para descontar.
 * Devuelve un objeto con el resultado de la operación.
 */
function actualizarStock(varianteId, cantidad) {
  const CF_URL = PropertiesService.getScriptProperties().getProperty('CLOUD_FUNCTION_URL');
  const token = getCloudFunctionToken();

  try {
    const response = UrlFetchApp.fetch(`${CF_URL}/${HOJA}/update`, {
      method: 'POST',
      contentType: 'application/json',
      headers: { 'Authorization': `Bearer ${token}` },
      payload: JSON.stringify({ varianteId: varianteId, cantidad: cantidad }),
      muteHttpExceptions: true
    });

    const status = response.getResponseCode();
    if (status === 200) {
      return { success: true };
    } else {
      let errorMsg = `HTTP ${status}`;
      try {
        const body = JSON.parse(response.getContentText());
        if (body.error) errorMsg = body.error;
      } catch (e) {
        Logger.log("Error parsing response: " + e.message);
      }
      return { success: false, error: errorMsg };
    }

  } catch (err) {
    return { success: false, error: err.message };
  }
}

/**
 * Actualiza la UI de la fila dependiendo del resultado.
 */
function registrarResultado(hoja, fila, exito, detalle = '') {
  if (exito) {
    hoja.getRange(fila, 1, 1, COL_COMENTARIO).setBackground('#D4EDDA');
    hoja.getRange(fila, COL_COMENTARIO).setValue('✅ Éxito');
  } else {
    hoja.getRange(fila, 1, 1, COL_COMENTARIO).setBackground('#F8D7DA');
    hoja.getRange(fila, COL_COMENTARIO).setValue('❌ ' + detalle);
    hoja.getRange(fila, COL_CONFIRMAR).setValue(false);
  }
}

//--------------creo que va siendo hora de hacer un utilyties.js

function bloquearCeldas(range) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var celda = sheet.getRange(range);

  var proteccion = celda.protect().setDescription("Celda bloqueada por el sistema");

  var usuariosConAcceso = proteccion.getEditors();
  proteccion.removeEditors(usuariosConAcceso);

  if (proteccion.canDomainEdit()) {
    proteccion.setDomainEdit(false);
  }
}

function desbloquearCeldas(range) {
  const hoja = range.getSheet();
  const fila = range.getRow();
  const protecciones = hoja.getProtections(SpreadsheetApp.ProtectionType.RANGE);
  protecciones.forEach(p => {
    const r = p.getRange();
    if (r.getRow() === fila) p.remove();
  });
}
