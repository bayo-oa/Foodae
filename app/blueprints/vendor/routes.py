from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.decorators import role_required, owns_restaurant_or_403
from app.models import Restaurant, FoodCategory, FoodItem, Order, OrderStatus, Role
from app.order_state_machine import (
    transition_order, get_allowed_next_statuses, STATUS_LABELS, InvalidTransitionError
)
from app.utils import save_upload

vendor_bp = Blueprint("vendor", __name__, template_folder="../../templates/vendor")


def _current_restaurant():
    if current_user.restaurant is None:
        abort(404)
    return current_user.restaurant


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

@vendor_bp.route("/create-restaurant", methods=["GET", "POST"])
@login_required
@role_required(Role.VENDOR)
def create_restaurant():
    if current_user.restaurant is not None:
        return redirect(url_for("vendor.dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Restaurant name is required.", "danger")
            return render_template("vendor/create_restaurant.html")

        logo_url = None
        cover_url = None
        try:
            logo_url = save_upload(request.files.get("logo"), subfolder="restaurants")
            cover_url = save_upload(request.files.get("cover_image"), subfolder="restaurants")
        except ValueError as e:
            flash(str(e), "danger")
            return render_template("vendor/create_restaurant.html")

        restaurant = Restaurant(
            owner_user_id=current_user.id,
            name=name,
            description=request.form.get("description", "").strip(),
            address=request.form.get("address", "").strip(),
            logo_url=logo_url,
            cover_image_url=cover_url,
            is_approved=False,
            is_open=True,
        )
        db.session.add(restaurant)
        db.session.commit()
        flash("Restaurant created. It will appear to customers once approved by an admin.", "success")
        return redirect(url_for("vendor.dashboard"))

    return render_template("vendor/create_restaurant.html")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@vendor_bp.route("/dashboard")
@login_required
@role_required(Role.VENDOR)
def dashboard():
    restaurant = _current_restaurant()
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    base_q = Order.query.filter_by(restaurant_id=restaurant.id)
    stats = {
        "today_orders": base_q.filter(Order.created_at >= today_start).count(),
        "pending_orders": base_q.filter(Order.status == OrderStatus.ORDER_RECEIVED).count(),
        "completed_orders": base_q.filter(Order.status == OrderStatus.DELIVERED).count(),
        "cancelled_orders": base_q.filter(Order.status == OrderStatus.CANCELLED).count(),
        "today_revenue": db.session.query(func.coalesce(func.sum(Order.total), 0))
            .filter(Order.restaurant_id == restaurant.id, Order.created_at >= today_start,
                    Order.status != OrderStatus.CANCELLED).scalar(),
    }
    return render_template("vendor/dashboard.html", restaurant=restaurant, stats=stats)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@vendor_bp.route("/settings", methods=["GET", "POST"])
@login_required
@role_required(Role.VENDOR)
def settings():
    restaurant = _current_restaurant()
    owns_restaurant_or_403(restaurant)

    if request.method == "POST":
        restaurant.name = request.form.get("name", restaurant.name).strip()
        restaurant.description = request.form.get("description", "").strip()
        restaurant.address = request.form.get("address", "").strip()
        restaurant.opening_hours = request.form.get("opening_hours", "").strip()
        try:
            restaurant.delivery_fee = float(request.form.get("delivery_fee", 0) or 0)
            restaurant.minimum_order_amount = float(request.form.get("minimum_order_amount", 0) or 0)
        except ValueError:
            flash("Delivery fee and minimum order must be numbers.", "danger")
            return render_template("vendor/settings.html", restaurant=restaurant)
        restaurant.is_open = request.form.get("is_open") == "on"

        try:
            new_logo = save_upload(request.files.get("logo"), subfolder="restaurants")
            if new_logo:
                restaurant.logo_url = new_logo
            new_cover = save_upload(request.files.get("cover_image"), subfolder="restaurants")
            if new_cover:
                restaurant.cover_image_url = new_cover
        except ValueError as e:
            flash(str(e), "danger")
            return render_template("vendor/settings.html", restaurant=restaurant)

        db.session.commit()
        flash("Settings updated.", "success")
        return redirect(url_for("vendor.settings"))

    return render_template("vendor/settings.html", restaurant=restaurant)


# ---------------------------------------------------------------------------
# Menu management
# ---------------------------------------------------------------------------

@vendor_bp.route("/menu")
@login_required
@role_required(Role.VENDOR)
def menu():
    restaurant = _current_restaurant()
    categories = FoodCategory.query.filter_by(restaurant_id=restaurant.id).order_by(FoodCategory.sort_order).all()
    uncategorized = FoodItem.query.filter_by(restaurant_id=restaurant.id, category_id=None).all()
    return render_template("vendor/menu.html", restaurant=restaurant, categories=categories, uncategorized=uncategorized)


@vendor_bp.route("/menu/category/add", methods=["POST"])
@login_required
@role_required(Role.VENDOR)
def add_category():
    restaurant = _current_restaurant()
    name = request.form.get("name", "").strip()
    if name:
        max_order = db.session.query(func.coalesce(func.max(FoodCategory.sort_order), 0)) \
            .filter_by(restaurant_id=restaurant.id).scalar()
        db.session.add(FoodCategory(restaurant_id=restaurant.id, name=name, sort_order=max_order + 1))
        db.session.commit()
        flash("Category added.", "success")
    return redirect(url_for("vendor.menu"))


@vendor_bp.route("/menu/category/<category_id>/delete", methods=["POST"])
@login_required
@role_required(Role.VENDOR)
def delete_category(category_id):
    restaurant = _current_restaurant()
    category = FoodCategory.query.get_or_404(category_id)
    if category.restaurant_id != restaurant.id:
        abort(403)
    db.session.delete(category)
    db.session.commit()
    flash("Category deleted.", "info")
    return redirect(url_for("vendor.menu"))


@vendor_bp.route("/menu/item/add", methods=["POST"])
@login_required
@role_required(Role.VENDOR)
def add_food_item():
    restaurant = _current_restaurant()
    name = request.form.get("name", "").strip()
    price = request.form.get("price", "")
    category_id = request.form.get("category_id") or None

    if not name or not price:
        flash("Name and price are required.", "danger")
        return redirect(url_for("vendor.menu"))

    try:
        price_val = float(price)
    except ValueError:
        flash("Price must be a number.", "danger")
        return redirect(url_for("vendor.menu"))

    if category_id:
        category = FoodCategory.query.get(category_id)
        if category is None or category.restaurant_id != restaurant.id:
            abort(403)

    try:
        image_url = save_upload(request.files.get("image"), subfolder="food")
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("vendor.menu"))

    item = FoodItem(
        restaurant_id=restaurant.id,
        category_id=category_id,
        name=name,
        description=request.form.get("description", "").strip(),
        price=price_val,
        image_url=image_url,
        is_available=True,
    )
    db.session.add(item)
    db.session.commit()
    flash("Food item added.", "success")
    return redirect(url_for("vendor.menu"))


@vendor_bp.route("/menu/item/<item_id>/toggle", methods=["POST"])
@login_required
@role_required(Role.VENDOR)
def toggle_food_item(item_id):
    restaurant = _current_restaurant()
    item = FoodItem.query.get_or_404(item_id)
    if item.restaurant_id != restaurant.id:
        abort(403)
    item.is_available = not item.is_available
    db.session.commit()
    return redirect(url_for("vendor.menu"))


@vendor_bp.route("/menu/item/<item_id>/edit", methods=["POST"])
@login_required
@role_required(Role.VENDOR)
def edit_food_item(item_id):
    restaurant = _current_restaurant()
    item = FoodItem.query.get_or_404(item_id)
    if item.restaurant_id != restaurant.id:
        abort(403)

    item.name = request.form.get("name", item.name).strip()
    item.description = request.form.get("description", "").strip()
    try:
        item.price = float(request.form.get("price", item.price))
    except ValueError:
        flash("Price must be a number.", "danger")
        return redirect(url_for("vendor.menu"))

    try:
        new_image = save_upload(request.files.get("image"), subfolder="food")
        if new_image:
            item.image_url = new_image
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("vendor.menu"))

    db.session.commit()
    flash("Food item updated.", "success")
    return redirect(url_for("vendor.menu"))


@vendor_bp.route("/menu/item/<item_id>/delete", methods=["POST"])
@login_required
@role_required(Role.VENDOR)
def delete_food_item(item_id):
    restaurant = _current_restaurant()
    item = FoodItem.query.get_or_404(item_id)
    if item.restaurant_id != restaurant.id:
        abort(403)
    db.session.delete(item)
    db.session.commit()
    flash("Food item deleted.", "info")
    return redirect(url_for("vendor.menu"))


# ---------------------------------------------------------------------------
# Order management
# ---------------------------------------------------------------------------

@vendor_bp.route("/orders")
@login_required
@role_required(Role.VENDOR)
def orders():
    restaurant = _current_restaurant()
    status_filter = request.args.get("status", "active")

    q = Order.query.filter_by(restaurant_id=restaurant.id)
    if status_filter == "new":
        q = q.filter(Order.status == OrderStatus.ORDER_RECEIVED)
    elif status_filter == "accepted":
        q = q.filter(Order.status == OrderStatus.ORDER_ACCEPTED)
    elif status_filter == "preparing":
        q = q.filter(Order.status == OrderStatus.PREPARING)
    elif status_filter == "ready":
        q = q.filter(Order.status.in_([OrderStatus.READY_FOR_PICKUP, OrderStatus.RIDER_ASSIGNED,
                                        OrderStatus.ORDER_PICKED_UP, OrderStatus.OUT_FOR_DELIVERY]))
    elif status_filter == "completed":
        q = q.filter(Order.status == OrderStatus.DELIVERED)
    elif status_filter == "cancelled":
        q = q.filter(Order.status == OrderStatus.CANCELLED)
    elif status_filter == "active":
        q = q.filter(Order.status.notin_([OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.REFUNDED,
                                           OrderStatus.PENDING_PAYMENT]))

    order_list = q.order_by(Order.created_at.desc()).all()
    return render_template("vendor/orders.html", restaurant=restaurant, orders=order_list,
                            status_filter=status_filter, get_allowed_next_statuses=get_allowed_next_statuses,
                            STATUS_LABELS=STATUS_LABELS)


@vendor_bp.route("/orders/<order_id>")
@login_required
@role_required(Role.VENDOR)
def order_detail(order_id):
    restaurant = _current_restaurant()
    order = Order.query.get_or_404(order_id)
    if order.restaurant_id != restaurant.id:
        abort(403)
    return render_template("vendor/order_detail.html", order=order,
                            get_allowed_next_statuses=get_allowed_next_statuses, STATUS_LABELS=STATUS_LABELS)


@vendor_bp.route("/orders/<order_id>/status", methods=["POST"])
@login_required
@role_required(Role.VENDOR)
def update_order_status(order_id):
    restaurant = _current_restaurant()
    order = Order.query.get_or_404(order_id)
    if order.restaurant_id != restaurant.id:
        abort(403)

    new_status = request.form.get("new_status")
    reason = request.form.get("reason", "").strip()

    try:
        if new_status == OrderStatus.CANCELLED and reason:
            order.cancellation_reason = reason
        transition_order(order, new_status)
        flash(f"Order moved to {STATUS_LABELS.get(new_status, new_status)}.", "success")
    except InvalidTransitionError as e:
        flash(str(e), "danger")

    return redirect(url_for("vendor.order_detail", order_id=order.id))
