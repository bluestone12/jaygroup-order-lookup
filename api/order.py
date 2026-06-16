import json
import os
from http.server import BaseHTTPRequestHandler

import requests

API_BASE = "https://api.logistics.jaygroup.com"

FULFILLMENT_STATUS_LABELS = {
    "ReadyToShip": "Ready to Ship",
    "ShipmentConfirmed": "Shipment Confirmed",
    "ShipmentCanceled": "Shipment Canceled",
    "PickedUpByCarrier": "Picked Up by Carrier",
    "Intransit": "In Transit",
    "ShipmentException": "Shipment Exception",
    "OutForDelivery": "Out for Delivery",
    "DeliveryException": "Delivery Exception",
    "ReturnToSender": "Return to Sender",
    "ReturnedToSender": "Returned to Sender",
    "Delivered": "Delivered",
}

ORDER_STATUS_LABELS = {
    "Received": "New / Sent to Warehouse",
    "Held": "On Hold",
    "Processing": "Picking & Packing",
    "Packed": "Packed",
    "PartiallyFulfilled": "Partially Fulfilled",
    "Fulfilled": "Fulfilled",
    "Canceled": "Canceled",
    "Parked": "Parked",
}


def get_access_token():
    resp = requests.post(
        f"{API_BASE}/oauth/token",
        json={
            "client_id": os.environ["JAYGROUP_CLIENT_ID"],
            "client_secret": os.environ["JAYGROUP_CLIENT_SECRET"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def lookup_order(shopify_number):
    token = get_access_token()
    resp = requests.post(
        f"{API_BASE}/orders/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"alternateOrderNumbers": [shopify_number]},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None


def format_response(order):
    if not order:
        return ":x: Order not found. Make sure to include the `#`, e.g. `/order #1064`"

    shopify = order.get("alternateOrderNumber") or "?"
    order_number = order.get("orderNumber")
    status = order.get("status", "")
    status_label = ORDER_STATUS_LABELS.get(status, status)
    received = (order.get("receivedDate") or "")[:10]

    lines = [
        f":package: *Order {shopify}* (JayGroup: `{order_number}`) — received {received}",
        f"*Stage:* {status_label}",
    ]

    fulfillments = order.get("fulfillments") or []
    if fulfillments:
        lines.append("")
        for i, f in enumerate(fulfillments, 1):
            prefix = f"*Fulfillment {i}:*" if len(fulfillments) > 1 else "*Fulfillment:*"
            f_status = FULFILLMENT_STATUS_LABELS.get(
                f.get("fulfillmentStatus", ""), f.get("fulfillmentStatus", "?")
            )
            tracking = f.get("tracking") or {}
            carrier = tracking.get("carrier") or ""
            tracking_number = tracking.get("trackingNumber") or ""
            tracking_url = tracking.get("trackingUrl") or ""

            lines.append(f"{prefix} {f_status}")
            if carrier:
                lines.append(f"  • Carrier: {carrier}")
            if tracking_number and tracking_url:
                lines.append(f"  • Tracking: <{tracking_url}|{tracking_number}>")
            elif tracking_number:
                lines.append(f"  • Tracking: `{tracking_number}`")
    else:
        lines.append("No fulfillment created yet.")

    return "\n".join(lines)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            # Parse form data from Slack
            params = {}
            for part in body.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = requests.utils.unquote(v.replace("+", " "))

            shopify_number = params.get("text", "").strip()

            if not shopify_number:
                text = ":x: Please provide a Shopify order number, e.g. `/order #1064`"
            else:
                try:
                    order = lookup_order(shopify_number)
                    text = format_response(order)
                except Exception as e:
                    text = f":x: Error looking up order: {e}"

            payload = json.dumps({"response_type": "ephemeral", "text": text})
            self._respond(200, payload, "application/json")

        except Exception as e:
            self._respond(200,
                json.dumps({"response_type": "ephemeral", "text": f":x: Unexpected error: {e}"}),
                "application/json")

    def _respond(self, status, body, content_type="text/plain"):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
