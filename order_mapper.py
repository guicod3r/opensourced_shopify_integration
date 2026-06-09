"""Map Shopify orders to sheet rows. Owns column schema."""
from enum import StrEnum
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)

TIMEZONE = "America/Montevideo"
MISSING = "[sin dato]"


class Col(StrEnum):
    FECHA = "fecha"
    PUNTO_VENTA = "punto_venta"
    PEDIDO = "pedido"
    ESTADO = "estado"
    PAGO = "pago"
    CANTIDAD = "cantidad"
    CLIENTE = "cliente"
    TELEFONO = "telefono"
    CATEGORIA = "categoria"
    PRODUCTO = "producto"
    COLOR = "color"
    TALLE = "talle"
    MONTO = "monto"
    FORMA_PAGO = "forma_pago"
    DIRECCION = "direccion"
    ENVIO = "envio"
    VARIANTE = "variante"
    CONFIRMAR = "confirmar"
    COMENTARIOS = "comentarios"


COLUMN_ORDER = [
    Col.FECHA, Col.PUNTO_VENTA, Col.PEDIDO, Col.ESTADO, Col.PAGO,
    Col.CANTIDAD, Col.CLIENTE, Col.TELEFONO, Col.CATEGORIA,
    Col.PRODUCTO, Col.COLOR, Col.TALLE, Col.MONTO, Col.FORMA_PAGO,
    Col.DIRECCION, Col.ENVIO, Col.VARIANTE, Col.CONFIRMAR, Col.COMENTARIOS,
]


def dict_to_row(d: dict) -> list:
    """Convert dict keyed by Col to ordered list for Sheets API."""
    return [d.get(c, "") for c in COLUMN_ORDER]


def format_date(iso_date: str) -> str:
    if not iso_date:
        return MISSING
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        dt = dt.astimezone(ZoneInfo(TIMEZONE))
        return dt.strftime("%d/%m/%Y")
    except Exception as e:
        logger.warning(f"Could not parse date '{iso_date}': {e}")
        return MISSING

def _color(product: dict, variant_id : str):
    index = -1
    for option in product.get("options",[]):
        if option.get("name","") == "Color":
            index = option.get("position", -1)
    if index == -1: return MISSING

    for variant in product.get("variants",[]):
        if variant.get("id","") == variant_id:
            return variant.get("title","").split(" / ")[index-1]
    return MISSING

def _talle(product: dict, variant_id : str):
    index = -1
    for option in product.get("options",[]):
        if option.get("name","") == "Talle":
            index = option.get("position", -1)
    if index == -1: return MISSING

    for variant in product.get("variants",[]):
        if variant.get("id","") == variant_id:
            return variant.get("title","").split(" / ")[index-1]
    return MISSING


def _cliente(order: dict) -> str:
    shipping = order.get("shipping_address") or {}
    billing = order.get("billing_address") or {}
    customer = order.get("customer") or {}
    return (
        f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()
        or f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
        or f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        or MISSING
    )


def _telefono(order: dict) -> str:
    shipping = order.get("shipping_address") or {}
    customer = order.get("customer") or {}
    return (
        shipping.get("phone")
        or customer.get("phone")
        or customer.get("default_address", {}).get("phone", "")
        or MISSING
    )


def _direccion(order: dict) -> str:
    s = order.get("shipping_address") or {}
    parts = [s.get("address1", ""), s.get("address2", ""), s.get("city", "")]
    return ", ".join(p for p in parts if p).strip(", ") or MISSING


def _envio_code(order: dict) -> str:
    lines = order.get("shipping_lines", [])
    if not lines:
        return "pickup"
    
    code = lines[0].get("code", "") or ""
    title = lines[0].get("title", "") or ""
    combined = (code + " " + title).lower()
    
    if "pedidosya" in combined or "peya" in combined:
        return "peya"
    if "dac" in combined:
        return "dac"
    return "pickup"


def _forma_pago(order: dict) -> str:
    gateways = order.get("payment_gateway_names") or [""]
    g = gateways[0]
    if "Mercado Pago" in g:
        return "mercado pago"
    if "dLocal" in g:
        return "dlocal"
    return "transferencia"


def _base_dict(order: dict) -> dict:
    """Order-level fields (same for every row of the order)."""
    return {
        Col.FECHA: format_date(order.get("updated_at", "")),
        Col.PUNTO_VENTA: "WEB",
        Col.PEDIDO: order.get("name") or MISSING,
        Col.PAGO: order.get("financial_status") or "pending",
        Col.CLIENTE: _cliente(order),
        Col.TELEFONO: _telefono(order),
        Col.DIRECCION: _direccion(order),
        Col.ENVIO: _envio_code(order),
        Col.FORMA_PAGO: _forma_pago(order),
        Col.COMENTARIOS: order.get("note", "")
    }


def _estado(order: dict, item: dict) -> str:
    if order.get("cancelled_at"):
        return "cancelado"
    if order.get("refunds", []):
        return "reembolsado"

    fs = order.get("fulfillment_status")
    if fs:
        return fs
    fulfillable = item.get("fulfillable_quantity", 0)
    qty = item.get("quantity", 0)
    if fulfillable < qty:
        return "sin stock"
    return "pending"


def map_order_to_dicts(order: dict) -> list[dict]:
    """One dict per line_item + shipping row if applicable."""

    base = _base_dict(order)
    rows = []
    line_items = order.get("line_items", [])
    for item in line_items:

        product =  order.get(f"_product_{item.get("product_id","")}",{})
        color = _color(product, item.get("variant_id",""))
        talle = _talle(product, item.get("variant_id",""))

        price_str = item.get("price_set", {}).get("shop_money", {}).get("amount") or item.get("price") or "0"
        
        # discount_allocations es una lista en Shopify
        allocations = item.get("discount_allocations") or []
        descuento_total = sum(float(alloc.get("amount", "0")) for alloc in allocations) if isinstance(allocations, list) else 0.0
        
        qty = item.get("quantity", 1)
        
        try:
            monto_unitario = float(price_str)
            if qty > 0 and descuento_total > 0:
                # precio unitario final con el descuento aplicado
                monto_unitario = monto_unitario * qty - descuento_total
            final_monto = str(int(monto_unitario))
        except Exception:
            # último recurso: intentar igualmente convertir a entero
            try:
                final_monto = str(int(float(price_str)))
            except Exception:
                final_monto = price_str or MISSING

        row = {
            **base,
            Col.ESTADO: _estado(order, item),
            Col.CATEGORIA: product.get("product_type", "error: sin categoria"),
            Col.PRODUCTO: product.get("title") or MISSING,
            Col.COLOR: color,
            Col.TALLE: talle,
            Col.CANTIDAD: qty,
            Col.MONTO: final_monto
        }
        rows.append(row)

    if base[Col.ENVIO] != "pickup":
        shipping_lines = order.get("shipping_lines", [])
        price = shipping_lines[0].get("price") if shipping_lines else None
        try:
            price_fmt = str(int(float(price))) if price else MISSING
        except Exception:
            price_fmt = price or MISSING
        rows.append({
            **base,
            Col.ESTADO: order.get("fulfillment_status") or "pending",
            Col.CATEGORIA: "envio",
            Col.MONTO: price_fmt,
        })
    return rows