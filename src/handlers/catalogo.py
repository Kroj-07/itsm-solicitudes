import time
import random
import string

from ..common.auth import get_context, es_admin
from ..common.dynamo import (
    table_catalogo,
    get_servicio,
    encode_lek,
    decode_lek,
    parse_limite,
)
from ..common.respuestas import (
    ok, created,
    error_validacion, error_no_autorizado, error_prohibido,
    error_no_encontrado, error_interno,
)
from ..common.validacion import (
    parse_body, requerido, entero, booleano, get_path_id, ValidacionError,
)


def _generar_servicio_id(tenant_id: str) -> str:
    """ID único para nuevos servicios del catálogo (no choca con el seed srv-{tenant}-{n:03d})."""
    ts = int(time.time() * 1000)
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"srv-{tenant_id}-{ts}-{rand}"


# ============================================================
# GET /catalogo
# ============================================================
def listar(event, context):
    try:
        tenant_id, _, _ = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    try:
        limite = parse_limite(event)
        qs = event.get("queryStringParameters") or {}
        siguiente = decode_lek(qs.get("siguiente"))

        kwargs = {
            "KeyConditionExpression": "tenant_id = :t",
            "ExpressionAttributeValues": {":t": tenant_id},
            "Limit": limite,
        }
        if siguiente:
            kwargs["ExclusiveStartKey"] = siguiente

        resp = table_catalogo().query(**kwargs)
        items = resp.get("Items", [])
        lek = resp.get("LastEvaluatedKey")

        paginacion = {"limite": limite, "siguiente": encode_lek(lek)}
        return ok(items, paginacion)
    except Exception as e:
        return error_interno(e)


# ============================================================
# POST /catalogo  (ADMIN)
# ============================================================
def crear(event, context):
    try:
        tenant_id, usuario_id, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    if not es_admin(rol):
        return error_prohibido("Sólo ADMIN puede crear servicios del catálogo")

    try:
        body = parse_body(event)
        nombre = requerido(body, "nombre")
        categoria = requerido(body, "categoria")
        sla_horas = entero(body, "sla_horas", minimo=1)
        requiere_aprobacion = booleano(body, "requiere_aprobacion", default=False)
        es_cambio_estandar = booleano(body, "es_cambio_estandar", default=False)
        activo = booleano(body, "activo", default=True)
    except ValidacionError as e:
        return error_validacion(str(e))

    try:
        servicio_id = _generar_servicio_id(tenant_id)
        item = {
            "tenant_id": tenant_id,
            "servicio_id": servicio_id,
            "nombre": nombre,
            "categoria": categoria,
            "sla_horas": sla_horas,
            "requiere_aprobacion": requiere_aprobacion,
            "es_cambio_estandar": es_cambio_estandar,
            "activo": activo,
        }
        table_catalogo().put_item(Item=item)
        return created(item)
    except Exception as e:
        return error_interno(e)


# ============================================================
# PUT /catalogo/{id}  (ADMIN)
# ============================================================
_CAMPOS_EDITABLES = {
    "nombre", "categoria", "sla_horas",
    "requiere_aprobacion", "es_cambio_estandar", "activo",
}


def editar(event, context):
    try:
        tenant_id, _, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    if not es_admin(rol):
        return error_prohibido("Sólo ADMIN puede editar servicios del catálogo")

    try:
        servicio_id = get_path_id(event)
        body = parse_body(event)
    except ValidacionError as e:
        return error_validacion(str(e))

    actual = get_servicio(tenant_id, servicio_id)
    if not actual:
        return error_no_encontrado(f"Servicio {servicio_id} no existe")

    # Validar y normalizar tipos
    cambios = {}
    try:
        for campo in _CAMPOS_EDITABLES:
            if campo not in body:
                continue
            if campo == "sla_horas":
                cambios[campo] = entero(body, campo, minimo=1)
            elif campo in ("requiere_aprobacion", "es_cambio_estandar", "activo"):
                cambios[campo] = booleano(body, campo)
            else:
                cambios[campo] = requerido(body, campo)
    except ValidacionError as e:
        return error_validacion(str(e))

    if not cambios:
        return error_validacion("No hay campos editables en el body")

    try:
        nombres = {}
        valores = {}
        set_parts = []
        for i, (k, v) in enumerate(cambios.items()):
            nombres[f"#f{i}"] = k
            valores[f":v{i}"] = v
            set_parts.append(f"#f{i} = :v{i}")

        table_catalogo().update_item(
            Key={"tenant_id": tenant_id, "servicio_id": servicio_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames=nombres,
            ExpressionAttributeValues=valores,
        )
        return ok(get_servicio(tenant_id, servicio_id))
    except Exception as e:
        return error_interno(e)