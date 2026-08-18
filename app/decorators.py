from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user


def role_required(*allowed_roles):
    """
    Enforce role-based access control server-side.
    Usage: @role_required('VENDOR') or @role_required('VENDOR', 'ADMIN')
    Never rely on hiding UI elements alone -- every protected route must use this.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in allowed_roles:
                abort(403)
            if not current_user.is_active:
                flash("Your account has been suspended. Contact support.", "danger")
                return redirect(url_for("auth.logout"))
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def owns_restaurant_or_403(restaurant):
    """Call inside a vendor route to ensure the logged-in vendor owns this restaurant."""
    if restaurant is None or restaurant.owner_user_id != current_user.id:
        abort(403)
