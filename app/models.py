import uuid
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


def gen_uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# User & roles
# ---------------------------------------------------------------------------

class Role:
    CUSTOMER = "CUSTOMER"
    VENDOR = "VENDOR"
    RIDER = "RIDER"
    ADMIN = "ADMIN"
    ALL = [CUSTOMER, VENDOR, RIDER, ADMIN]
    SELF_REGISTERABLE = [CUSTOMER, VENDOR, RIDER]  # ADMIN is never self-registered


class User(db.Model, UserMixin):
    __tablename__ = "user"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(32), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=Role.CUSTOMER)
    full_name = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Rider-specific profile fields (kept on user for MVP simplicity)
    vehicle_type = db.Column(db.String(50), nullable=True)
    rider_is_approved = db.Column(db.Boolean, default=False)

    restaurant = db.relationship("Restaurant", back_populates="owner", uselist=False)
    addresses = db.relationship("Address", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def get_id(self):
        # Flask-Login requirement
        return self.id

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


# ---------------------------------------------------------------------------
# Restaurant / menu
# ---------------------------------------------------------------------------

class Restaurant(db.Model):
    __tablename__ = "restaurant"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    owner_user_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False, unique=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    logo_url = db.Column(db.String(500), nullable=True)
    cover_image_url = db.Column(db.String(500), nullable=True)
    address = db.Column(db.String(500), nullable=True)
    opening_hours = db.Column(db.String(255), nullable=True)  # simple text for MVP e.g. "Mon-Sun 9am-9pm"
    delivery_fee = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    minimum_order_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    is_open = db.Column(db.Boolean, default=True, nullable=False)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship("User", back_populates="restaurant")
    categories = db.relationship("FoodCategory", back_populates="restaurant", cascade="all, delete-orphan")
    food_items = db.relationship("FoodItem", back_populates="restaurant", cascade="all, delete-orphan")
    orders = db.relationship("Order", back_populates="restaurant")

    def is_visible_to_customers(self):
        return self.is_open and self.is_approved


class FoodCategory(db.Model):
    __tablename__ = "food_category"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    restaurant_id = db.Column(db.String(36), db.ForeignKey("restaurant.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    restaurant = db.relationship("Restaurant", back_populates="categories")
    food_items = db.relationship("FoodItem", back_populates="category")


class FoodItem(db.Model):
    __tablename__ = "food_item"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    restaurant_id = db.Column(db.String(36), db.ForeignKey("restaurant.id"), nullable=False)
    category_id = db.Column(db.String(36), db.ForeignKey("food_category.id"), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    image_url = db.Column(db.String(500), nullable=True)
    is_available = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    restaurant = db.relationship("Restaurant", back_populates="food_items")
    category = db.relationship("FoodCategory", back_populates="food_items")


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

class Address(db.Model):
    __tablename__ = "address"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    label = db.Column(db.String(50), nullable=False, default="Home")  # Home / Office / School / etc.
    full_address = db.Column(db.String(500), nullable=False)
    phone = db.Column(db.String(32), nullable=True)
    is_default = db.Column(db.Boolean, default=False)

    user = db.relationship("User", back_populates="addresses")


# ---------------------------------------------------------------------------
# Cart (simple DB-backed cart keyed by customer, single-restaurant at a time)
# ---------------------------------------------------------------------------

class CartItem(db.Model):
    __tablename__ = "cart_item"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    user_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    restaurant_id = db.Column(db.String(36), db.ForeignKey("restaurant.id"), nullable=False)
    food_item_id = db.Column(db.String(36), db.ForeignKey("food_item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    special_instructions = db.Column(db.String(500), nullable=True)

    food_item = db.relationship("FoodItem")


# ---------------------------------------------------------------------------
# Order lifecycle
# ---------------------------------------------------------------------------

class OrderStatus:
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    ORDER_RECEIVED = "ORDER_RECEIVED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    RIDER_ASSIGNED = "RIDER_ASSIGNED"
    ORDER_PICKED_UP = "ORDER_PICKED_UP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class Order(db.Model):
    __tablename__ = "order"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    order_number = db.Column(db.String(20), unique=True, nullable=False)
    customer_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    restaurant_id = db.Column(db.String(36), db.ForeignKey("restaurant.id"), nullable=False)
    rider_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=True)
    delivery_address_id = db.Column(db.String(36), db.ForeignKey("address.id"), nullable=False)

    status = db.Column(db.String(30), nullable=False, default=OrderStatus.PENDING_PAYMENT)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    delivery_fee = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    special_instructions = db.Column(db.String(500), nullable=True)
    cancellation_reason = db.Column(db.String(500), nullable=True)

    rating = db.Column(db.Integer, nullable=True)  # 1-5, set once, only after DELIVERED
    rating_comment = db.Column(db.String(1000), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = db.relationship("User", foreign_keys=[customer_id])
    rider = db.relationship("User", foreign_keys=[rider_id])
    restaurant = db.relationship("Restaurant", back_populates="orders")
    delivery_address = db.relationship("Address")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payment = db.relationship("Payment", back_populates="order", uselist=False, cascade="all, delete-orphan")
    delivery_assignment = db.relationship(
        "DeliveryAssignment", back_populates="order", uselist=False, cascade="all, delete-orphan"
    )


class OrderItem(db.Model):
    __tablename__ = "order_item"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    order_id = db.Column(db.String(36), db.ForeignKey("order.id"), nullable=False)
    food_item_id = db.Column(db.String(36), db.ForeignKey("food_item.id"), nullable=False)
    food_name_snapshot = db.Column(db.String(255), nullable=False)  # preserve name even if item edited later
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    special_instructions = db.Column(db.String(500), nullable=True)

    order = db.relationship("Order", back_populates="items")
    food_item = db.relationship("FoodItem")


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

class PaymentStatus:
    PENDING = "PENDING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class Payment(db.Model):
    __tablename__ = "payment"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    order_id = db.Column(db.String(36), db.ForeignKey("order.id"), nullable=False, unique=True)
    user_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    transaction_ref = db.Column(db.String(100), unique=True, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(10), nullable=False, default="NGN")
    provider = db.Column(db.String(30), nullable=False, default="paystack")
    status = db.Column(db.String(20), nullable=False, default=PaymentStatus.PENDING)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship("Order", back_populates="payment")


# ---------------------------------------------------------------------------
# Delivery assignment
# ---------------------------------------------------------------------------

class DeliveryAssignment(db.Model):
    __tablename__ = "delivery_assignment"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    order_id = db.Column(db.String(36), db.ForeignKey("order.id"), nullable=False, unique=True)
    rider_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="ASSIGNED")
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    picked_up_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", back_populates="delivery_assignment")
    rider = db.relationship("User")


# ---------------------------------------------------------------------------
# Admin audit log
# ---------------------------------------------------------------------------

class AdminActionLog(db.Model):
    __tablename__ = "admin_action_log"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    admin_id = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(db.String(36), nullable=False)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    admin = db.relationship("User")
