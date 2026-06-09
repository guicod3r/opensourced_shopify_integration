"""Shopify Admin REST API client."""
import json
import logging
import os
import requests
import ssl
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

API_VERSION = "2026-01"
MISSING = "[sin dato]"

"""
Forces TLS 1.2+ for Shopify API connections.

Added after observing TLS negotiation issues in some deployment
environments where the default SSL configuration was not accepted.
"""
class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
            **pool_kwargs
        )

class ShopifyClient:
    def __init__(self, store: str, token: str):
        self.base_url = f"https://{store}.myshopify.com/admin/api/{API_VERSION}"
        self.headers = {
            "X-Shopify-Access-Token": token,
            "User-Agent": "ShopifySyncDemo/1.0"
        }
        self.session = requests.Session()
        self.session.mount("https://", TLSAdapter())

    @classmethod
    def from_env(cls) -> "ShopifyClient":
        
        return cls(os.environ["SHOPIFY_STORE"], os.environ["SHOPIFY_TOKEN"])

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self.session.get(f"{self.base_url}/{path}", headers=self.headers, params=params, timeout= 10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, params: dict | None = None):
        r = self.session.post(f"{self.base_url}/{path}", headers=self.headers, json=params, timeout= 10)
        r.raise_for_status()
        return r.status_code

    def fetch_new_orders(self, since_id: str | None, limit: int = 5) -> list[dict]:
        params = {"status": "any", "limit": limit, "order": "id asc"}
        if since_id:
            params["since_id"] = since_id
        orders = self._get("orders.json", params).get("orders", [])
        logger.info(f"Fetched {len(orders)} new orders from Shopify")
        return orders

    def add_products_info(self, order: dict):
        product_ids = [item.get("product_id", "") for item in order.get("line_items", [])]
        
        ids = ",".join(str(i) for i in product_ids if i)
        if not ids:
            logger.log(2,"no hay ids de producto.")
            return
        products = self._get("products.json", {"ids": ids, "fields": "id,product_type,options,variants,title"})

        for product in products.get("products",[]):
            order[f"_product_{product.get("id")}"] = product

    def get_last_order(self) -> str:
    data = self._get("orders.json", {"status": "any", "limit": 1, "order": "id desc"})
    return json.dumps(data, indent=1)


    def get_order_by_number(self, number: str) -> str:
        data = self._get("orders.json", {"status": "any", "name": f"#{number}"})
        return json.dumps(data, indent=1)
            
def get_products_by_name(query: str):
    client = ShopifyClient.from_env()
    url = f"{client.base_url}/graphql.json"
    
    headers = client.headers.copy()
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"

    graphql_query = """
    query buscarProductos($filtro: String!) {
      products(first: 10, query: $filtro) {
        edges {
          node {
            title
            featuredImage {
              url
            }
            variants(first: 10) {
              edges {
                node {
                  title
                  price
                  inventoryQuantity
                  image {
                    url
                  }
                  inventoryItem {
                    id
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    filtro_formateado = f"title:*{query}*"

    payload = {
        "query": graphql_query,
        "variables": {
            "filtro": filtro_formateado
        }
    }

    try:
        response = client.session.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        datos = response.json()
        
        if "errors" in datos:
            print(f"Error interno de Shopify GraphQL: {datos['errors']}")
            raise Exception(f"Error en la consulta de Shopify: {datos['errors'][0]['message']}")
            
        return parsear_respuesta_graphql(datos)

    except requests.exceptions.RequestException as e:
        print(f"Error de red o conexión al servidor de Shopify: {e}")
        raise e


def parsear_respuesta_graphql(datos_shopify):
    resultados = []
    
    # Navega las capas del JSON de Shopify GraphQL
    for edge in datos_shopify.get('data', {}).get('products', {}).get('edges', []):
        nodo_producto = edge.get('node', {})
        product_title = nodo_producto.get('title', '')
        
        featured_image = nodo_producto.get('featuredImage')
        default_img_url = featured_image.get('url') if featured_image else "Sin imagen"
        
        for v_edge in nodo_producto.get('variants', {}).get('edges', []):
            v_nodo = v_edge.get('node', {})
            
            v_image = v_nodo.get('image')
            img_url = v_image.get('url') if v_image else default_img_url
            
            title_str = v_nodo.get('title', '')
            
            inventory_item_id_str = v_nodo.get('inventoryItem', {}).get('id', '')
            inventory_item_id = int(inventory_item_id_str.split('/')[-1]) if inventory_item_id_str else None
            
            resultados.append({
                "product_title": product_title,      # Título del producto
                "variant_title": title_str,          # Título de la variante
                "inventory_item_id": inventory_item_id,            # ID limpio (numérico) de la variante
                "stock": v_nodo.get('inventoryQuantity'), # Stock disponible
                "image": img_url                     # URL de la imagen
            })
            
    return resultados

def update_stock(productoId: str, cantidad: int):
    client = ShopifyClient.from_env() 
    payload = {
    "location_id": os.environ["SHOPIFY_LOCATION_ID"],
    "inventory_item_id": productoId,
    "available_adjustment": cantidad}
    response = client._post("inventory_levels/adjust.json", payload)
    return {"status": response}

    
# ── Debug helpers ─────────────────────────────────────────────────────


def get_last_order() -> str:
    data = ShopifyClient.from_env()._get("orders.json", {"status": "any", "limit": 1, "order": "id desc"})
    return json.dumps(data, indent=1)


def get_order_by_number( number: str) -> str:
    shopify = ShopifyClient.from_env()
    data = shopify._get("orders.json", {"status": "any", "name": f"#{number}"})
    shopify.add_products_info(data["orders"][0])
    return json.dumps(data, indent=1)

def get_product_by_id(prod_id: str):
    data = ShopifyClient.from_env()._get(f"products/{prod_id}.json", {"status": "any"})
    return json.dumps(data, indent=1)

def get_orders_products(number: str):
    order = json.loads(get_order_by_number(number)).get("orders","")[0]
    products = [get_product_by_id(line_item.get("product_id","")) for line_item in order.get("line_items",[])]
    
    print(f" cantidad de productos: {len(products)} \n")
    for p in products: print(p)

