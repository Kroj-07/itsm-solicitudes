import os


def get_context(event):
    """
    Extrae (tenant_id, usuario_id, rol) del contexto del authorizer.

    Fallback SOLO en dev para pruebas sin authorizer:
    exportar DEV_TENANT_ID / DEV_USUARIO_ID / DEV_ROL.
    """
    ctx = event.get("requestContext") or {}
    auth = ctx.get("authorizer") or {}

    tenant_id = auth.get("tenant_id")
    usuario_id = auth.get("usuario_id")
    rol = auth.get("rol")

    if not (tenant_id and usuario_id and rol):
        # Fallback controlado
        if os.environ.get("STAGE") == "dev":
            tenant_id = tenant_id or os.environ.get("DEV_TENANT_ID")
            usuario_id = usuario_id or os.environ.get("DEV_USUARIO_ID")
            rol = rol or os.environ.get("DEV_ROL")

    if not (tenant_id and usuario_id and rol):
        raise PermissionError("Falta contexto de authorizer (tenant_id/usuario_id/rol)")

    return tenant_id, usuario_id, rol


def es_admin(rol: str) -> bool:
    return rol == "ADMIN"


def es_agente_o_admin(rol: str) -> bool:
    return rol in ("ADMIN", "AGENTE")