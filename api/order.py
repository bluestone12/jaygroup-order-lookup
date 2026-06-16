import hashlib
import hmac
import json
import os
import time

import requests
from flask import Flask, Response, request

app = Flask(__name__)

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


def verify_slack_signature(body: bytes) -> bool:
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False

    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


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


def lookup_order(shopify_number: str):
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


def format_response(order) -> str:
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


def slack_response(text):
    return Response(
        json.dumps({"response_type": "ephemeral", "text": text}),
        status=200,
        mimetype="application/json",
    )


@app.route("/api/order", methods=["POST"])
def handle():
    body = request.get_data()

    if not verify_slack_signature(body):
        return Response("Invalid signature", status=403)

    shopify_number = request.form.get("text", "").strip()

    if not shopify_number:
        return slack_response(":x: Please provide a Shopify order number, e.g. `/order #1064`")

    try:
        order = lookup_order(shopify_number)
        return slack_response(format_response(order))
    except Exception as e:
        return slack_response(f":x: Error looking up order: {e}")
