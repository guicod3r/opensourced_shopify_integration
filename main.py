"""Cloud Function entrypoints: Shopify webhook receiver + Pub/Sub push worker."""
import os
import json
import hmac
import hashlib
import base64
import logging
import functions_framework
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")
PROJECT_ID     = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT", "")
PUBSUB_TOPIC   = os.environ.get("PUBSUB_TOPIC")


def _verify_webhook(body: bytes, sig_header: str) -> bool:
    digest = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, sig_header)


def _publish(body: bytes, ordering_key: str) -> str:
    from google.cloud import pubsub_v1
    publisher = pubsub_v1.PublisherClient(
        publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
    )
    topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)
    future = publisher.publish(topic_path, body, ordering_key=ordering_key)
    return future.result(timeout=10)

def sync_order(order: dict) -> tuple[str, int]:
    from shopify_client import ShopifyClient
    from sheets_client import SheetsClient
    from order_mapper import map_order_to_dicts

    shopify = ShopifyClient(os.environ["SHOPIFY_STORE"], os.environ["SHOPIFY_TOKEN"])
    sheets = SheetsClient(os.environ["SPREADSHEET_ID"], os.environ.get("SHEET_NAME"))
    shopify.add_products_info(order)
    dicts = map_order_to_dicts(order)

    sheets.upsert_dicts(dicts)
    return f"Synced {len(dicts)} rows for order {order.get('name')}", 200

    # ---------- endpoints http ------------

@functions_framework.http
def sync_orders_http(request):
    """Receiver: verify HMAC, publish to Pub/Sub, return 200."""
    sig = request.headers.get("X-Shopify-Hmac-Sha256", "")
    body = request.get_data()

    if WEBHOOK_SECRET and not _verify_webhook(body, sig):
        logger.warning("Webhook HMAC verification failed")
        return "Unauthorized", 401

    try:
        order = json.loads(body)
    except Exception:
        return "Bad Request", 400

    order_id = str(order.get("id") or order.get("name") or "")
    if not order_id:
        return "Bad Request: missing order id", 400

    msg_id = _publish(body, ordering_key=order_id)
    logger.info(f"Published order {order.get('name')} (msg_id={msg_id})")
    return "OK", 200

@functions_framework.http
def process_order(request):
    """Worker: receives Pub/Sub push, decodes message, calls sync_order."""
    envelope = request.get_json(silent=True)
    if not envelope or "message" not in envelope:
        return "Bad Request: not a Pub/Sub envelope", 400

    msg = envelope["message"]
    data_b64 = msg.get("data", "")
    if not data_b64:
        return "Bad Request: empty message", 400

    try:
        order = json.loads(base64.b64decode(data_b64))
    except Exception as e:
        logger.error(f"Failed to decode Pub/Sub message: {e}")
        return "Bad Request: invalid payload", 400

    logger.info(f"Processing order {order.get('name')} (msg_id={msg.get('messageId')})")
    return sync_order(order)

@functions_framework.http
def process_sheets_request(request):
    from sheets_server import process_request
    return process_request(request)