"""
Single source of truth for order status transitions.

RULE: nowhere else in the codebase should `order.status = X` be assigned directly.
Every status change must go through transition_order() so invalid jumps
(e.g. PAYMENT_CONFIRMED -> DELIVERED) are impossible.
"""
from datetime import datetime

from app.extensions import db
from app.models import OrderStatus as S


# Map of current_status -> list of statuses it is legal to move to next.
ALLOWED_TRANSITIONS = {
    S.PENDING_PAYMENT: [S.PAYMENT_CONFIRMED, S.CANCELLED],
    S.PAYMENT_CONFIRMED: [S.ORDER_RECEIVED, S.REFUNDED],
    S.ORDER_RECEIVED: [S.ORDER_ACCEPTED, S.CANCELLED],
    S.ORDER_ACCEPTED: [S.PREPARING, S.CANCELLED],
    S.PREPARING: [S.READY_FOR_PICKUP, S.CANCELLED],
    S.READY_FOR_PICKUP: [S.RIDER_ASSIGNED, S.CANCELLED],
    S.RIDER_ASSIGNED: [S.ORDER_PICKED_UP, S.CANCELLED],
    S.ORDER_PICKED_UP: [S.OUT_FOR_DELIVERY],
    S.OUT_FOR_DELIVERY: [S.DELIVERED],
    S.DELIVERED: [],
    S.CANCELLED: [S.REFUNDED],
    S.REFUNDED: [],
}

# Human-friendly labels for status buttons / timeline display
STATUS_LABELS = {
    S.PENDING_PAYMENT: "Pending Payment",
    S.PAYMENT_CONFIRMED: "Payment Confirmed",
    S.ORDER_RECEIVED: "Order Received",
    S.ORDER_ACCEPTED: "Order Accepted",
    S.PREPARING: "Preparing",
    S.READY_FOR_PICKUP: "Ready for Pickup",
    S.RIDER_ASSIGNED: "Rider Assigned",
    S.ORDER_PICKED_UP: "Picked Up",
    S.OUT_FOR_DELIVERY: "Out for Delivery",
    S.DELIVERED: "Delivered",
    S.CANCELLED: "Cancelled",
    S.REFUNDED: "Refunded",
}

# Simplified customer-facing timeline (collapses some backend states into one step)
CUSTOMER_TIMELINE_STEPS = [
    ("Order Confirmed", [S.PAYMENT_CONFIRMED, S.ORDER_RECEIVED, S.ORDER_ACCEPTED]),
    ("Preparing", [S.PREPARING]),
    ("Rider Assigned", [S.READY_FOR_PICKUP, S.RIDER_ASSIGNED]),
    ("Picked Up", [S.ORDER_PICKED_UP]),
    ("On the Way", [S.OUT_FOR_DELIVERY]),
    ("Delivered", [S.DELIVERED]),
]


class InvalidTransitionError(Exception):
    pass


def can_transition(current_status, new_status):
    return new_status in ALLOWED_TRANSITIONS.get(current_status, [])


def get_allowed_next_statuses(current_status):
    return ALLOWED_TRANSITIONS.get(current_status, [])


def transition_order(order, new_status, commit=True):
    """
    The ONLY function permitted to change order.status.
    Raises InvalidTransitionError if the transition isn't legal.
    """
    if not can_transition(order.status, new_status):
        raise InvalidTransitionError(
            f"Cannot move order {order.order_number} from {order.status} to {new_status}"
        )
    order.status = new_status
    order.updated_at = datetime.utcnow()
    if commit:
        db.session.commit()
    return order
