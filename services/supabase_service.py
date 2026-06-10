from supabase import create_client
from flask import current_app


def get_supabase_public():
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_ANON_KEY"]
    )


def get_supabase_admin():
    return create_client(
        current_app.config["SUPABASE_URL"],
        current_app.config["SUPABASE_SERVICE_ROLE_KEY"]
    )