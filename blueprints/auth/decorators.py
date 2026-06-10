from functools import wraps
from flask import session, redirect, url_for, request, jsonify, render_template


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        user = session.get("user")

        if not user:
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({
                    "error": "No has iniciado sesión.",
                    "redirect": url_for("auth.login")
                }), 401
            return redirect(url_for("auth.login"))

        return view_func(*args, **kwargs)
    return wrapped_view


def rol_required(*roles_permitidos, usar_matriz=False):
    """
    Control de acceso por rol.

    Ejemplos:
        @rol_required("admin")
        @rol_required("admin", "medico")
        @rol_required()              # solo exige sesión
    """
    def wrapper(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            user = session.get("user")

            if not user:
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({
                        "error": "No has iniciado sesión.",
                        "redirect": url_for("auth.login")
                    }), 401
                return redirect(url_for("auth.login"))

            role_actual = (user.get("role") or "").strip().lower()
            autorizado = False

            # Validación por nombre de rol
            if roles_permitidos:
                roles_norm = {str(r).strip().lower() for r in roles_permitidos}
                if role_actual in roles_norm:
                    autorizado = True
            else:
                # Sin roles especificados: basta con tener sesión
                autorizado = True

            if not autorizado:
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({
                        "error": "No tienes permisos para acceder a este recurso."
                    }), 403
                return render_template("acceso_denegado.html"), 403

            return view_func(*args, **kwargs)
        return wrapped_view
    return wrapper