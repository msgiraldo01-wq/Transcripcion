from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from repositories.auth_repository import obtener_perfil_por_username
from services.supabase_service import get_supabase_public
from .decorators import rol_required, login_required

bp_auth = Blueprint("auth", __name__, url_prefix="/auth")


@bp_auth.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("inicio"))

    if request.method == "GET":
        return render_template("auth/login.html")

    username = (request.form.get("username") or "").strip().lower()
    password = request.form.get("password") or ""

    if not username or not password:
        flash("Debes ingresar usuario y contraseña.", "warning")
        return render_template("auth/login.html")

    try:
        perfil = obtener_perfil_por_username(username)

        if not perfil:
            flash("El usuario no existe.", "danger")
            return render_template("auth/login.html")

        if not perfil.get("is_active", True):
            flash("Tu usuario está inactivo.", "warning")
            return render_template("auth/login.html")

        email = (perfil.get("email") or "").strip().lower()
        if not email:
            flash("El usuario no tiene correo configurado.", "danger")
            return render_template("auth/login.html")

        # 🔐 Login con Supabase
        supabase = get_supabase_public()
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })

        user = getattr(response, "user", None)
        user_session = getattr(response, "session", None)

        if not user or not user_session:
            flash("Usuario o contraseña incorrectos.", "danger")
            return render_template("auth/login.html")

        # Session
        session["user"] = {
            "id": perfil["id"],
            "username": perfil["username"],
            "full_name": perfil.get("full_name") or "",
            "email": perfil.get("email") or "",
            "role": perfil.get("role", ""),
        }

        session["rol"] = perfil.get("role", "")

        session["supabase_access_token"] = user_session.access_token
        session["supabase_refresh_token"] = user_session.refresh_token

        flash("Bienvenido a Transcripción.", "success")

        return redirect("/")

    except Exception as e:
        print("ERROR LOGIN:", e)
        flash("Usuario o contraseña incorrectos.", "danger")
        return render_template("auth/login.html")


@bp_auth.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("auth.login"))


@bp_auth.route("/mi-perfil")
@login_required
@rol_required(usar_matriz=True)
def mi_perfil():

    if not session.get("user"):
        return redirect(url_for("auth.login"))

    user = session.get("user")

    return render_template(
        "auth/mi_perfil.html",
        user=user
    )


@bp_auth.route("/cambiar-password", methods=["POST"])
@login_required
@rol_required(usar_matriz=True)
def cambiar_password():

    if not session.get("user"):
        return redirect(url_for("auth.mi_perfil"))

    password_actual = request.form.get("password_actual")
    password_nueva = request.form.get("password_nueva")

    if not password_actual or not password_nueva:
        flash("Debes completar todos los campos.", "warning")
        return redirect(url_for("auth.mi_perfil"))

    try:
        email = session["user"]["email"]

        supabase = get_supabase_public()

        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password_actual
        })

        if not getattr(response, "user", None):
            flash("La contraseña actual es incorrecta.", "danger")
            return redirect(url_for("auth.mi_perfil"))

        access_token = response.session.access_token

        supabase.auth.update_user(
            {"password": password_nueva},
            access_token
        )

        flash("Contraseña actualizada correctamente.", "success")

    except Exception as e:
        flash(f"Error al cambiar contraseña: {str(e)}", "danger")

    return redirect(url_for("auth.mi_perfil"))