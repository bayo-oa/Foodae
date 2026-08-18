from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.decorators import role_required
from app.models import User, Restaurant, Order, OrderStatus, AdminActionLog, Role
from app.order_state_machine import STATUS_LABELS

admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")


def _log_action(action, target_type, target_id, notes=None):
    db.session.add(AdminActionLog(
        admin_id=current_user.id, action=action, target_type=target_type,
        target_id=target_id, notes=notes
    ))


@admin_bp.route("/dashboard")
@login_required
@role_required(Role.ADMIN)
def dashboard():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "total_users": User.query.filter(User.role != Role.ADMIN).count(),
        "active_restaurants": Restaurant.query.filter_by(is_approved=True, is_open=True).count(),
        "active_riders": User.query.filter_by(role=Role.RIDER, rider_is_approved=True, is_active=True).count(),
        "orders_today": Order.query.filter(Order.created_at >= today_start).count(),
        "orders_in_progress": Order.query.filter(
            Order.status.notin_([OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.REFUNDED,
                                  OrderStatus.PENDING_PAYMENT])
        ).count(),
        "revenue_today": db.session.query(func.coalesce(func.sum(Order.total), 0)).filter(
            Order.created_at >= today_start, Order.status != OrderStatus.CANCELLED
        ).scalar(),
        "cancelled_orders": Order.query.filter_by(status=OrderStatus.CANCELLED).count(),
    }
    return render_template("admin/dashboard.html", stats=stats)


@admin_bp.route("/users")
@login_required
@role_required(Role.ADMIN)
def users():
    query = request.args.get("q", "").strip()
    role_filter = request.args.get("role", "")
    q = User.query.filter(User.role != Role.ADMIN)
    if query:
        q = q.filter((User.email.ilike(f"%{query}%")) | (User.full_name.ilike(f"%{query}%")))
    if role_filter:
        q = q.filter_by(role=role_filter)
    all_users = q.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=all_users, query=query, role_filter=role_filter)


@admin_bp.route("/users/<user_id>/toggle-active", methods=["POST"])
@login_required
@role_required(Role.ADMIN)
def toggle_user_active(user_id):
    user = User.query.get_or_404(user_id)
    if user.role == Role.ADMIN:
        abort(403)
    user.is_active = not user.is_active
    _log_action("suspend" if not user.is_active else "reactivate", "user", user.id)
    db.session.commit()
    flash(f"{user.full_name} is now {'active' if user.is_active else 'suspended'}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/restaurants")
@login_required
@role_required(Role.ADMIN)
def restaurants():
    all_restaurants = Restaurant.query.order_by(Restaurant.created_at.desc()).all()
    return render_template("admin/restaurants.html", restaurants=all_restaurants)


@admin_bp.route("/restaurants/<restaurant_id>/toggle-approval", methods=["POST"])
@login_required
@role_required(Role.ADMIN)
def toggle_restaurant_approval(restaurant_id):
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    restaurant.is_approved = not restaurant.is_approved
    _log_action("approve" if restaurant.is_approved else "unapprove", "restaurant", restaurant.id)
    db.session.commit()
    flash(f"{restaurant.name} is now {'approved' if restaurant.is_approved else 'unapproved'}.", "success")
    return redirect(url_for("admin.restaurants"))


@admin_bp.route("/riders")
@login_required
@role_required(Role.ADMIN)
def riders():
    all_riders = User.query.filter_by(role=Role.RIDER).order_by(User.created_at.desc()).all()
    return render_template("admin/riders.html", riders=all_riders)


@admin_bp.route("/riders/<rider_id>/toggle-approval", methods=["POST"])
@login_required
@role_required(Role.ADMIN)
def toggle_rider_approval(rider_id):
    rider = User.query.get_or_404(rider_id)
    if rider.role != Role.RIDER:
        abort(403)
    rider.rider_is_approved = not rider.rider_is_approved
    _log_action("approve" if rider.rider_is_approved else "unapprove", "rider", rider.id)
    db.session.commit()
    flash(f"{rider.full_name} is now {'approved' if rider.rider_is_approved else 'unapproved'}.", "success")
    return redirect(url_for("admin.riders"))


@admin_bp.route("/orders")
@login_required
@role_required(Role.ADMIN)
def orders():
    status_filter = request.args.get("status", "")
    q = Order.query
    if status_filter:
        q = q.filter_by(status=status_filter)
    all_orders = q.order_by(Order.created_at.desc()).limit(200).all()
    return render_template("admin/orders.html", orders=all_orders, status_filter=status_filter,
                            STATUS_LABELS=STATUS_LABELS, all_statuses=STATUS_LABELS.keys())


@admin_bp.route("/orders/<order_id>")
@login_required
@role_required(Role.ADMIN)
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("admin/order_detail.html", order=order, STATUS_LABELS=STATUS_LABELS)
