import hashlib
import hmac
import json

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.extensions import db, limiter
from app.decorators import role_required
from app.models import (
    Restaurant, FoodCategory, FoodItem, CartItem, Address, Order, OrderItem,
    Payment, PaymentStatus, OrderStatus, Role
)
from app.order_state_machine import transition_order, CUSTOMER_TIMELINE_STEPS, STATUS_LABELS, InvalidTransitionError
from app.payments import get_payment_provider
from app.utils import generate_order_number

customer_bp = Blueprint("customer", __name__, template_folder="../../templates/customer")


# ---------------------------------------------------------------------------
# Homepage / discovery
# ---------------------------------------------------------------------------

def get_the_restaurant():
    """
    This is a single-restaurant storefront, not a multi-vendor marketplace.
    There should only ever be one restaurant record. Prefer an approved one;
    fall back to the first restaurant that exists at all (e.g. before the
    admin has approved it yet), so the owner can still preview the site.
    """
    restaurant = Restaurant.query.filter_by(is_approved=True).order_by(Restaurant.created_at.asc()).first()
    if restaurant is None:
        restaurant = Restaurant.query.order_by(Restaurant.created_at.asc()).first()
    return restaurant


@customer_bp.route("/")
def homepage():
    restaurant = get_the_restaurant()
    if restaurant is None:
        # No restaurant has been set up yet -- show a simple placeholder
        # instead of an empty/broken page.
        return render_template("customer/no_restaurant.html")

    categories = FoodCategory.query.filter_by(restaurant_id=restaurant.id).order_by(FoodCategory.sort_order).all()
    uncategorized = FoodItem.query.filter_by(
        restaurant_id=restaurant.id, category_id=None, is_available=True
    ).all()
    return render_template(
        "customer/landing.html",
        restaurant=restaurant,
        categories=categories,
        uncategorized=uncategorized,
    )


@customer_bp.route("/restaurant/<restaurant_id>")
def restaurant_page(restaurant_id):
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    if not restaurant.is_visible_to_customers() and (
        not current_user.is_authenticated or current_user.id != restaurant.owner_user_id
    ):
        abort(404)
    categories = FoodCategory.query.filter_by(restaurant_id=restaurant.id).order_by(FoodCategory.sort_order).all()
    uncategorized = FoodItem.query.filter_by(restaurant_id=restaurant.id, category_id=None, is_available=True).all()
    return render_template("customer/restaurant.html", restaurant=restaurant, categories=categories,
                            uncategorized=uncategorized)


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

@customer_bp.route("/cart/add/<food_item_id>", methods=["POST"])
@login_required
@role_required(Role.CUSTOMER)
def add_to_cart(food_item_id):
    item = FoodItem.query.get_or_404(food_item_id)
    if not item.is_available:
        flash("This item is currently unavailable.", "danger")
        return redirect(url_for("customer.restaurant_page", restaurant_id=item.restaurant_id))

    quantity = max(1, int(request.form.get("quantity", 1) or 1))
    instructions = request.form.get("special_instructions", "").strip()

    existing_cart = CartItem.query.filter_by(user_id=current_user.id).first()
    if existing_cart and existing_cart.restaurant_id != item.restaurant_id:
        flash("Your cart has items from another restaurant. Clear your cart first to order from here.", "warning")
        return redirect(url_for("customer.restaurant_page", restaurant_id=item.restaurant_id))

    same_item = CartItem.query.filter_by(user_id=current_user.id, food_item_id=item.id).first()
    if same_item:
        same_item.quantity += quantity
    else:
        db.session.add(CartItem(
            user_id=current_user.id, restaurant_id=item.restaurant_id, food_item_id=item.id,
            quantity=quantity, special_instructions=instructions
        ))
    db.session.commit()
    flash(f"Added {item.name} to cart.", "success")
    return redirect(url_for("customer.restaurant_page", restaurant_id=item.restaurant_id))


@customer_bp.route("/cart")
@login_required
@role_required(Role.CUSTOMER)
def view_cart():
    cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
    restaurant = cart_items[0].food_item.restaurant if cart_items else None
    subtotal = sum(ci.food_item.price * ci.quantity for ci in cart_items)
    delivery_fee = restaurant.delivery_fee if restaurant else 0
    total = subtotal + delivery_fee
    return render_template("customer/cart.html", cart_items=cart_items, restaurant=restaurant,
                            subtotal=subtotal, delivery_fee=delivery_fee, total=total)


@customer_bp.route("/cart/update/<cart_item_id>", methods=["POST"])
@login_required
@role_required(Role.CUSTOMER)
def update_cart_item(cart_item_id):
    ci = CartItem.query.get_or_404(cart_item_id)
    if ci.user_id != current_user.id:
        abort(403)
    action = request.form.get("action")
    if action == "increase":
        ci.quantity += 1
        db.session.commit()
    elif action == "decrease":
        ci.quantity -= 1
        if ci.quantity <= 0:
            db.session.delete(ci)
        db.session.commit()
    elif action == "remove":
        db.session.delete(ci)
        db.session.commit()
    return redirect(url_for("customer.view_cart"))


@customer_bp.route("/cart/clear", methods=["POST"])
@login_required
@role_required(Role.CUSTOMER)
def clear_cart():
    CartItem.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash("Cart cleared.", "info")
    return redirect(request.referrer or url_for("customer.homepage"))


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

@customer_bp.route("/checkout", methods=["GET", "POST"])
@login_required
@role_required(Role.CUSTOMER)
def checkout():
    cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
    if not cart_items:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("customer.homepage"))

    restaurant = cart_items[0].food_item.restaurant
    subtotal = sum(ci.food_item.price * ci.quantity for ci in cart_items)

    if subtotal < restaurant.minimum_order_amount:
        flash(f"Minimum order for {restaurant.name} is ₦{restaurant.minimum_order_amount}.", "danger")
        return redirect(url_for("customer.view_cart"))

    addresses = Address.query.filter_by(user_id=current_user.id).all()

    if request.method == "POST":
        address_id = request.form.get("address_id")
        new_address_text = request.form.get("new_address", "").strip()
        instructions = request.form.get("special_instructions", "").strip()

        if new_address_text:
            addr = Address(
                user_id=current_user.id,
                label=request.form.get("new_address_label", "Home").strip() or "Home",
                full_address=new_address_text,
                phone=request.form.get("new_address_phone", "").strip(),
            )
            db.session.add(addr)
            db.session.commit()
            address_id = addr.id
        elif not address_id:
            flash("Please select or add a delivery address.", "danger")
            return render_template("customer/checkout.html", cart_items=cart_items, restaurant=restaurant,
                                    subtotal=subtotal, delivery_fee=restaurant.delivery_fee,
                                    total=subtotal + restaurant.delivery_fee, addresses=addresses)

        address = Address.query.get_or_404(address_id)
        if address.user_id != current_user.id:
            abort(403)

        # Order is created here, at the point payment is initiated -- not earlier.
        # This keeps "cart browsing" cheap and ensures we never have abandoned
        # order rows for carts that were never taken to checkout.
        order = Order(
            order_number=generate_order_number(),
            customer_id=current_user.id,
            restaurant_id=restaurant.id,
            delivery_address_id=address.id,
            status=OrderStatus.PENDING_PAYMENT,
            subtotal=subtotal,
            delivery_fee=restaurant.delivery_fee,
            total=subtotal + restaurant.delivery_fee,
            special_instructions=instructions,
        )
        db.session.add(order)
        db.session.flush()  # get order.id before commit

        for ci in cart_items:
            db.session.add(OrderItem(
                order_id=order.id,
                food_item_id=ci.food_item_id,
                food_name_snapshot=ci.food_item.name,
                quantity=ci.quantity,
                unit_price=ci.food_item.price,
                special_instructions=ci.special_instructions,
            ))

        reference = f"{order.order_number}-{order.id[:8]}"
        payment = Payment(
            order_id=order.id, user_id=current_user.id, transaction_ref=reference,
            amount=order.total, currency="NGN", provider="paystack", status=PaymentStatus.PENDING,
        )
        db.session.add(payment)

        # Clear the cart now -- the order has its own snapshot of items.
        CartItem.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()

        return redirect(url_for("customer.initiate_payment", order_id=order.id))

    return render_template("customer/checkout.html", cart_items=cart_items, restaurant=restaurant,
                            subtotal=subtotal, delivery_fee=restaurant.delivery_fee,
                            total=subtotal + restaurant.delivery_fee, addresses=addresses)


# ---------------------------------------------------------------------------
# Payment (Paystack)
# ---------------------------------------------------------------------------

@customer_bp.route("/payment/<order_id>/initiate")
@login_required
@role_required(Role.CUSTOMER)
def initiate_payment(order_id):
    order = Order.query.get_or_404(order_id)
    if order.customer_id != current_user.id:
        abort(403)
    if order.status != OrderStatus.PENDING_PAYMENT:
        return redirect(url_for("customer.payment_callback", order_id=order.id))

    provider = get_payment_provider("paystack")
    amount_kobo = int(order.total * 100)
    callback_url = f"{current_app.config['APP_BASE_URL']}{url_for('customer.payment_callback', order_id=order.id)}"

    try:
        result = provider.initialize(
            email=current_user.email,
            amount_kobo=amount_kobo,
            reference=order.payment.transaction_ref,
            callback_url=callback_url,
        )
    except Exception as e:
        current_app.logger.error(f"Paystack init failed for order {order.id}: {e}")
        flash("Could not start payment. Please try again.", "danger")
        return redirect(url_for("customer.view_cart"))

    return redirect(result["authorization_url"])


@customer_bp.route("/payment/<order_id>/callback")
@login_required
@role_required(Role.CUSTOMER)
def payment_callback(order_id):
    order = Order.query.get_or_404(order_id)
    if order.customer_id != current_user.id:
        abort(403)

    # If the webhook already processed this (race with Paystack redirect), just show result.
    if order.status == OrderStatus.PENDING_PAYMENT:
        provider = get_payment_provider(order.payment.provider)
        try:
            result = provider.verify(order.payment.transaction_ref)
        except Exception as e:
            current_app.logger.error(f"Paystack verify failed for order {order.id}: {e}")
            result = {"success": False}

        if result.get("success"):
            order.payment.status = PaymentStatus.SUCCESSFUL
            transition_order(order, OrderStatus.PAYMENT_CONFIRMED, commit=False)
            transition_order(order, OrderStatus.ORDER_RECEIVED)
        else:
            order.payment.status = PaymentStatus.FAILED
            db.session.commit()

    return render_template("customer/payment_result.html", order=order)


@customer_bp.route("/api/payments/webhook", methods=["POST"])
@limiter.limit("60 per minute")
def payment_webhook():
    """
    Paystack webhook -- source of truth for payment confirmation.
    Frontend/redirect callback above is a convenience for the user; this endpoint
    is what actually confirms payment even if the customer closes their browser.
    """
    secret = current_app.config["PAYSTACK_SECRET_KEY"].encode()
    signature = request.headers.get("x-paystack-signature", "")
    computed = hmac.new(secret, request.data, hashlib.sha512).hexdigest()

    if not hmac.compare_digest(computed, signature):
        current_app.logger.warning("Rejected webhook with invalid signature.")
        abort(400)

    event = request.get_json(silent=True) or {}
    if event.get("event") != "charge.success":
        return jsonify({"received": True}), 200

    reference = event.get("data", {}).get("reference")
    payment = Payment.query.filter_by(transaction_ref=reference).first()
    if payment is None:
        return jsonify({"received": True}), 200

    order = payment.order
    if order.status != OrderStatus.PENDING_PAYMENT:
        return jsonify({"received": True}), 200  # already processed

    provider = get_payment_provider(payment.provider)
    result = provider.verify(reference)
    if result.get("success"):
        payment.status = PaymentStatus.SUCCESSFUL
        transition_order(order, OrderStatus.PAYMENT_CONFIRMED, commit=False)
        transition_order(order, OrderStatus.ORDER_RECEIVED)
    else:
        payment.status = PaymentStatus.FAILED
        db.session.commit()

    return jsonify({"received": True}), 200


# ---------------------------------------------------------------------------
# Tracking & history
# ---------------------------------------------------------------------------

@customer_bp.route("/orders")
@login_required
@role_required(Role.CUSTOMER)
def order_history():
    orders = Order.query.filter_by(customer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("customer/order_history.html", orders=orders, STATUS_LABELS=STATUS_LABELS)


@customer_bp.route("/orders/<order_id>/track")
@login_required
@role_required(Role.CUSTOMER)
def track_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.customer_id != current_user.id:
        abort(403)
    return render_template("customer/track_order.html", order=order, timeline_steps=CUSTOMER_TIMELINE_STEPS,
                            STATUS_LABELS=STATUS_LABELS)


@customer_bp.route("/api/orders/<order_id>/status")
@login_required
@role_required(Role.CUSTOMER)
def order_status_json(order_id):
    """
    Polled every ~15-20s from the tracking page. Plain polling (not WebSockets)
    keeps this cheap to run on a single free-tier instance; that interval balances
    freshness against server load / battery use on the client.
    """
    order = Order.query.get_or_404(order_id)
    if order.customer_id != current_user.id:
        abort(403)
    return jsonify({
        "status": order.status,
        "status_label": STATUS_LABELS.get(order.status, order.status),
        "rider_name": order.rider.full_name if order.rider else None,
        "rider_phone": order.rider.phone if order.rider else None,
    })


@customer_bp.route("/orders/<order_id>/rate", methods=["POST"])
@login_required
@role_required(Role.CUSTOMER)
def rate_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.customer_id != current_user.id:
        abort(403)
    if order.status != OrderStatus.DELIVERED:
        flash("You can only rate delivered orders.", "danger")
        return redirect(url_for("customer.order_history"))
    if order.rating is not None:
        flash("You've already rated this order.", "info")
        return redirect(url_for("customer.order_history"))

    try:
        rating = int(request.form.get("rating", 0))
    except ValueError:
        rating = 0
    if rating < 1 or rating > 5:
        flash("Rating must be between 1 and 5.", "danger")
        return redirect(url_for("customer.order_history"))

    order.rating = rating
    order.rating_comment = request.form.get("comment", "").strip()
    db.session.commit()
    flash("Thanks for your feedback!", "success")
    return redirect(url_for("customer.order_history"))
