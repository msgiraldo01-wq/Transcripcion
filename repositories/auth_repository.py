from services.supabase_service import get_supabase_admin


def obtener_perfil_por_username(username: str):
    supabase = get_supabase_admin()

    response = (
        supabase
        .table("profiles")
        .select("""
            id,
            username,
            full_name,
            email,
            role,
            empresa_id,
            is_active,
            roles:role_id (
                id,
                code,
                name
            )
        """)
        .eq("username", username)
        .limit(1)
        .execute()
    )

    data = response.data or []
    return data[0] if data else None