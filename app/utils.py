import os
import uuid

from flask import current_app, url_for

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file_storage, subfolder=""):
    """
    Save an uploaded image to local disk and return its public URL.

    NOTE FOR PRODUCTION: Render's filesystem is ephemeral -- files saved here
    will be WIPED on every deploy/restart. Before going live, swap this function's
    body for an upload to Cloudinary (or attach a Render persistent disk and point
    UPLOAD_FOLDER at it). Every other part of the app just calls save_upload() and
    stores the returned URL, so the swap is isolated to this one function.
    """
    if file_storage is None or file_storage.filename == "":
        return None
    if not _allowed(file_storage.filename):
        raise ValueError("Unsupported file type. Use png, jpg, jpeg, webp, or gif.")

    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"

    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], subfolder)
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)
    file_storage.save(filepath)

    rel_path = f"uploads/{subfolder}/{filename}" if subfolder else f"uploads/{filename}"
    return url_for("static", filename=rel_path)


def generate_order_number():
    return "ORD-" + uuid.uuid4().hex[:8].upper()
