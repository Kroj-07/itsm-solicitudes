import random
import string
import time

from botocore.exceptions import ClientError

from ..common.auth import get_context, es_admin, es_agente_o_admin
from ..common.dynamo import (
    table_solicitudes,
    get_solicitud,
    get_servicio,
    update_solicitud_estado,
    encode_lek,
    decode_lek,
    parse_limite,
)
from ..common.eventos import publicar_evento
from ..common.fechas import ahora_iso
from ..common.respuestas import (
    ok, created,
    error_validacion, error_no_autorizado, error_prohibido,
    error_no_encontrado, error_conflicto, error_interno,
)
from ..common.validacion import (
    parse_body, requerido, get_path_id, ValidacionError,
)


# Flujo unificado (contrato v1.1):
#   EN_APROBACION -> APROBADA -> COMPLETADA
#   EN_APROBACION -> RECHAZADA | CANCELADA
# Estados que permiten cada acción
_ESTADOS_APROBAR = ("EN_APROBACION",)
_ESTADOS_RECHAZAR = ("EN_APROBACION",)
_ESTADOS_COMPLETAR = ("APROBADA",)
_ESTADOS_CANCELAR = ("EN_APROBACION",)


def _generar_solicitud_id(tenant_id: str) -> str:
    """ID único; no choca con el seed req-{tenant}-{n:06d}."""
    ts = int(time.time() * 1000)
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"req-{tenant_id}-{ts}-{rand}"


# ============================================================
# POST /solicitudes
# ============================================================
def crear(event, context):
    try:
        tenant_id, usuario_id, _rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    try:
        body = parse_body(event)
        servicio_id = requerido(body, "servicio_id")
        datos_formulario = body.get("datos_formulario") or {}
        if not isinstance(datos_formulario, dict):
            raise ValidacionError("datos_formulario debe ser un objeto JSON")
    except ValidacionError as e:
        return error_validacion(str(e))

    servicio = get_servicio(tenant_id, servicio_id)
    if not servicio:
        return error_no_encontrado(f"Servicio {servicio_id} no existe")
    if not servicio.get("activo", True):
        return error_validacion("El servicio está inactivo")

    requiere_aprobacion = bool(servicio.get("requiere_aprobacion", False))
    estado_inicial = "EN_APROBACION" if requiere_aprobacion else "APROBADA"

    ahora = ahora_iso()
    solicitud_id = _generar_solicitud_id(tenant_id)

    item = {
        "tenant_id": tenant_id,
        "solicitud_id": solicitud_id,
        "servicio_id": servicio_id,
        "solicitante_id": usuario_id,
        "estado": estado_inicial,
        "datos_formulario": datos_formulario,
        "cambio_id": None,
        "creado_en": ahora,
        "actualizado_en": ahora,
        # Atributos de GSI
        "estado_creado": f"{estado_inicial}#{ahora}",
        "solicitante_creado": f"{usuario_id}#{ahora}",
    }

    try:
        table_solicitudes().put_item(Item=item)

        publicar_evento(
            "solicitud.creada",
            tenant_id,
            {"solicitud_id": solicitud_id, "servicio_id": servicio_id},
        )
        return created(item)
    except Exception as e:
        return error_interno(e)


# ============================================================
# GET /solicitudes?estado=...&solicitante_id=...&limite=...&siguiente=...
# ============================================================
def listar(event, context):
    try:
        tenant_id, usuario_id, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    try:
        limite = parse_limite(event)
    except ValidacionError as e:
        return error_validacion(str(e))

    qs = event.get("queryStringParameters") or {}
    estado = qs.get("estado")
    solicitante_q = qs.get("solicitante_id")
    siguiente = decode_lek(qs.get("siguiente"))

    # SOLICITANTE sólo puede ver las suyas
    if rol == "SOLICITANTE":
        solicitante_filtro = usuario_id
    else:
        solicitante_filtro = solicitante_q

    try:
        if solicitante_filtro:
            # GSI2: tenant_id + solicitante_creado (begins_with "{usr}#")
            kwargs = {
                "IndexName": "GSI2",
                "KeyConditionExpression": "tenant_id = :t AND begins_with(solicitante_creado, :s)",
                "ExpressionAttributeValues": {
                    ":t": tenant_id,
                    ":s": f"{solicitante_filtro}#",
                },
                "Limit": limite,
            }
            if estado:
                kwargs["FilterExpression"] = "estado = :e"
                kwargs["ExpressionAttributeValues"][":e"] = estado

        elif estado:
            # GSI1: tenant_id + estado_creado (begins_with "{estado}#")
            kwargs = {
                "IndexName": "GSI1",
                "KeyConditionExpression": "tenant_id = :t AND begins_with(estado_creado, :e)",
                "ExpressionAttributeValues": {
                    ":t": tenant_id,
                    ":e": f"{estado}#",
                },
                "Limit": limite,
            }

        else:
            # Sin filtros: query a la tabla principal por tenant
            kwargs = {
                "KeyConditionExpression": "tenant_id = :t",
                "ExpressionAttributeValues": {":t": tenant_id},
                "Limit": limite,
            }

        if siguiente:
            kwargs["ExclusiveStartKey"] = siguiente

        resp = table_solicitudes().query(**kwargs)
        items = resp.get("Items", [])
        lek = resp.get("LastEvaluatedKey")

        paginacion = {"limite": limite, "siguiente": encode_lek(lek)}
        return ok(items, paginacion)

    except Exception as e:
        return error_interno(e)


# ============================================================
# GET /solicitudes/{id}
# ============================================================
def obtener(event, context):
    try:
        tenant_id, usuario_id, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    try:
        solicitud_id = get_path_id(event)
    except ValidacionError as e:
        return error_validacion(str(e))

    try:
        solicitud = get_solicitud(tenant_id, solicitud_id)
        if not solicitud:
            # 404 tanto si no existe como si es de otro tenant (aislamiento)
            return error_no_encontrado(f"Solicitud {solicitud_id} no existe")

        if rol == "SOLICITANTE" and solicitud.get("solicitante_id") != usuario_id:
            return error_prohibido("No puede ver solicitudes de otro usuario")

        return ok(solicitud)
    except Exception as e:
        return error_interno(e)


# ============================================================
# POST /solicitudes/{id}/aprobar   (ADMIN)
# ============================================================
def aprobar(event, context):
    try:
        tenant_id, usuario_id, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    if not es_admin(rol):
        return error_prohibido("Sólo ADMIN puede aprobar solicitudes")

    try:
        solicitud_id = get_path_id(event)
    except ValidacionError as e:
        return error_validacion(str(e))

    try:
        solicitud = get_solicitud(tenant_id, solicitud_id)
        if not solicitud:
            return error_no_encontrado(f"Solicitud {solicitud_id} no existe")

        if solicitud["estado"] not in _ESTADOS_APROBAR:
            return error_conflicto(
                f"Estado '{solicitud['estado']}' no permite aprobar"
            )

        servicio = get_servicio(tenant_id, solicitud["servicio_id"]) or {}
        es_cambio_estandar = bool(servicio.get("es_cambio_estandar", False))

        ahora = ahora_iso()
        try:
            update_solicitud_estado(
                tenant_id, solicitud_id, "APROBADA", ahora,
                extra={"aprobador_id": usuario_id},
                estados_validos=list(_ESTADOS_APROBAR),
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return error_conflicto("La solicitud cambió de estado; reintente")
            raise

        # Si el servicio es cambio estándar, publicamos el evento
        if es_cambio_estandar:
            publicar_evento(
                "solicitud.cambio_estandar",
                tenant_id,
                {
                    "solicitud_id": solicitud_id,
                    "servicio_id": solicitud["servicio_id"],
                },
            )

        return ok(get_solicitud(tenant_id, solicitud_id))
    except Exception as e:
        return error_interno(e)


# ============================================================
# POST /solicitudes/{id}/rechazar  (ADMIN)
# ============================================================
def rechazar(event, context):
    try:
        tenant_id, _, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    if not es_admin(rol):
        return error_prohibido("Sólo ADMIN puede rechazar solicitudes")

    try:
        solicitud_id = get_path_id(event)
        body = parse_body(event)
        motivo = requerido(body, "motivo")
    except ValidacionError as e:
        return error_validacion(str(e))

    try:
        solicitud = get_solicitud(tenant_id, solicitud_id)
        if not solicitud:
            return error_no_encontrado(f"Solicitud {solicitud_id} no existe")

        if solicitud["estado"] not in _ESTADOS_RECHAZAR:
            return error_conflicto(
                f"Estado '{solicitud['estado']}' no permite rechazar"
            )

        ahora = ahora_iso()
        try:
            update_solicitud_estado(
                tenant_id, solicitud_id, "RECHAZADA", ahora,
                extra={"motivo_rechazo": motivo},
                estados_validos=list(_ESTADOS_RECHAZAR),
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return error_conflicto("La solicitud cambió de estado; reintente")
            raise

        return ok(get_solicitud(tenant_id, solicitud_id))
    except Exception as e:
        return error_interno(e)


# ============================================================
# POST /solicitudes/{id}/completar  (ADMIN, AGENTE)
# ============================================================
def completar(event, context):
    try:
        tenant_id, _, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    if not es_agente_o_admin(rol):
        return error_prohibido("Sólo ADMIN o AGENTE pueden completar solicitudes")

    try:
        solicitud_id = get_path_id(event)
    except ValidacionError as e:
        return error_validacion(str(e))

    try:
        solicitud = get_solicitud(tenant_id, solicitud_id)
        if not solicitud:
            return error_no_encontrado(f"Solicitud {solicitud_id} no existe")

        if solicitud["estado"] not in _ESTADOS_COMPLETAR:
            return error_conflicto(
                f"Estado '{solicitud['estado']}' no permite completar"
            )

        ahora = ahora_iso()
        try:
            update_solicitud_estado(
                tenant_id, solicitud_id, "COMPLETADA", ahora,
                estados_validos=list(_ESTADOS_COMPLETAR),
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return error_conflicto("La solicitud cambió de estado; reintente")
            raise

        publicar_evento(
            "solicitud.completada",
            tenant_id,
            {"solicitud_id": solicitud_id},
        )

        return ok(get_solicitud(tenant_id, solicitud_id))
    except Exception as e:
        return error_interno(e)


# ============================================================
# DELETE /solicitudes/{id}  (solicitante dueño o ADMIN)
# ============================================================
def cancelar(event, context):
    try:
        tenant_id, usuario_id, rol = get_context(event)
    except PermissionError as e:
        return error_no_autorizado(str(e))

    try:
        solicitud_id = get_path_id(event)
    except ValidacionError as e:
        return error_validacion(str(e))

    try:
        solicitud = get_solicitud(tenant_id, solicitud_id)
        if not solicitud:
            return error_no_encontrado(f"Solicitud {solicitud_id} no existe")

        # Sólo el dueño o un ADMIN
        if rol != "ADMIN" and solicitud.get("solicitante_id") != usuario_id:
            return error_prohibido("No puede cancelar solicitudes de otro usuario")

        if solicitud["estado"] not in _ESTADOS_CANCELAR:
            return error_conflicto(
                f"Estado '{solicitud['estado']}' no permite cancelar"
            )

        ahora = ahora_iso()
        try:
            update_solicitud_estado(
                tenant_id, solicitud_id, "CANCELADA", ahora,
                estados_validos=list(_ESTADOS_CANCELAR),
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return error_conflicto("La solicitud cambió de estado; reintente")
            raise

        return ok(get_solicitud(tenant_id, solicitud_id))
    except Exception as e:
        return error_interno(e)