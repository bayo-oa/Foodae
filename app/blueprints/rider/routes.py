from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.decorators import role_required
from app.models import Order, OrderStatus, DeliveryAssignment, Role
from app.order_state_machine import transition_order, InvalidTransitionError, STATUS_LABELS

rider_bp = Blueprint("rider", __name__, template_folder="../../templates/rider")


@rider_bp.route("/complete-profile", methods=["GET", "POST"])
@login_required
@role_required(Role.RIDER)
def complete_profile():
    if request.method == "POST":
        current_user.phone = request.form.get("phone", "").strip()
        current_user.vehicle_type = request.form.get("vehicle_type", "").strip()
        db.session.commit()
        flash("Profile saved.", "success")
        return redirect(url_for("rider.dashboard"))
    return render_template("rider/complete_profile.html")


@rider_bp.route("/dashboard")
@login_required
@role_required(Role.RIDER)
def dashboard():
    if not current_user.phone or not current_user.vehicle_type:
        return redirect(url_for("rider.complete_profile"))

    # Available pool: ready for pickup and not yet assigned to anyone
    available = Order.query.filter_by(status=OrderStatus.READY_FOR_PICKUP).order_by(Order.created_at.asc()).all()

    # This rider's active delivery (assigned but not yet delivered)
    active = Order.query.filter(
        Order.rider_id == current_user.id,
        Order.status.in_([OrderStatus.RIDER_ASSIGNED, OrderStatus.ORDER_PICKED_UP, OrderStatus.OUT_FOR_DELIVERY])
    ).all()

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    completed_today = Order.query.filter(
        Order.rider_id == current_user.id, Order.status == OrderStatus.DELIVERED,
        Order.updated_at >= today_start
    ).count()

    return render_template("rider/dashboard.html", available=available, active=active,
                            completed_today=completed_today, STATUS_LABELS=STATUS_LABELS)


@rider_bp.route("/deliveries/<order_id>/accept", methods=["POST"])
@login_required
@role_required(Role.RIDER)
def accept_delivery(order_id):
    order = Order.query.get_or_404(order_id)
    if order.status != OrderStatus.READY_FOR_PICKUP:
        flash("This delivery is no longer available.", "warning")
        return redirect(url_for("rider.dashboard"))
    if order.rider_id is not None:
        flash("This delivery was just taken by another rider.", "warning")
        return redirect(url_for("rider.dashboard"))

    order.rider_id = current_user.id
    db.session.add(DeliveryAssignment(order_id=order.id, rider_id=current_user.id, status="ASSIGNED"))
    try:
        transition_order(order, OrderStatus.RIDER_ASSIGNED)
        flash(f"You accepted order {order.order_number}.", "success")
    except InvalidTransitionError as e:
        db.session.rollback()
        flash(str(e), "danger")
    return redirect(url_for("rider.dashboard"))


@rider_bp.route("/deliveries/<order_id>/status", methods=["POST"])
@login_required
@role_required(Role.RIDER)
def update_delivery_status(order_id):
    order = Order.query.get_or_404(order_id)
    if order.rider_id != current_user.id:
        abort(403)

    new_status = request.form.get("new_status")
    try:
        transition_order(order, new_status)
        assignment = order.delivery_assignment
        if new_status == OrderStatus.ORDER_PICKED_UP and assignment:
            assignment.picked_up_at = datetime.utcnow()
            assignment.status = "PICKED_UP"
        elif new_status == OrderStatus.DELIVERED and assignment:
            assignment.delivered_at = datetime.utcnow()
            assignment.status = "DELIVERED"
        db.session.commit()
        flash(f"Order moved to {STATUS_LABELS.get(new_status, new_status)}.", "success")
    except InvalidTransitionError as e:
        flash(str(e), "danger")

    return redirect(url_for("rider.dashboard"))


@rider_bp.route("/history")
@login_required
@role_required(Role.RIDER)
def history():
    completed = Order.query.filter_by(rider_id=current_user.id, status=OrderStatus.DELIVERED) \
        .order_by(Order.updated_at.desc()).all()

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    # Simple earnings rule for MVP: rider earns the order's delivery_fee per completed delivery.
    today_earnings = sum(o.delivery_fee for o in completed if o.updated_at >= today_start)
    week_earnings = sum(o.delivery_fee for o in completed if o.updated_at >= week_start)

    return render_template("rider/history.html", completed=completed,
                            today_earnings=today_earnings, week_earnings=week_earnings)
