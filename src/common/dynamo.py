import base64
import json
import os

import boto3


_dynamodb = boto3.resource("dynamodb")


def table_catalogo():
    return _dynamodb.Table(os.environ["TABLE_CATALOGO"])


def table_solicitudes():
    return _dynamodb.Table(os.environ["TABLE_SOLICITUDES"])


# ---------- Paginación ----------

def encode_lek(lek):
    if not lek:
        return None
    return base64.b64encode(json.dumps(lek).encode("utf-8")).decode("utf-8")


def decode_lek(token):
    if not token:
        return None
    return json.loads(base64.b64decode(token.encode("utf-8")).decode("utf-8"))


def parse_limite(event, default: int = 20, maximo: int = 100) -> int:
    qs = event.get("queryStringParameters") or {}
    raw = qs.get("limite")
    if not raw:
        return default
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return default
    return max(1, min(n, maximo))


# ---------- Helpers de solicitud ----------

def get_solicitud(tenant_id: str, solicitud_id: str):
    resp = table_solicitudes().get_item(
        Key={"tenant_id": tenant_id, "solicitud_id": solicitud_id}
    )
    return resp.get("Item")


def get_servicio(tenant_id: str, servicio_id: str):
    resp = table_catalogo().get_item(
        Key={"tenant_id": tenant_id, "servicio_id": servicio_id}
    )
    return resp.get("Item")


def update_solicitud_estado(
    tenant_id: str,
    solicitud_id: str,
    nuevo_estado: str,
    ahora: str,
    extra: dict | None = None,
    estados_validos: list[str] | None = None,
):
    """
    Actualiza estado + estado_creado + actualizado_en de una solicitud.

    - `extra`: campos adicionales a SET (ej. cambio_id, aprobador_id).
    - `estados_validos`: si se pasa, sólo actualiza si el estado actual está en la lista
      (ConditionExpression). Lanza ConditionalCheckFailedException si no cumple.
    """
    update_expr = "SET estado = :e, actualizado_en = :a, estado_creado = :ec"
    expr_vals = {
        ":e": nuevo_estado,
        ":a": ahora,
        ":ec": f"{nuevo_estado}#{ahora}",
    }

    if extra:
        for k, v in extra.items():
            update_expr += f", #{k} = :{k}"
            expr_vals[f":{k}"] = v

    kwargs = {
        "Key": {"tenant_id": tenant_id, "solicitud_id": solicitud_id},
        "UpdateExpression": update_expr,
        "ExpressionAttributeValues": expr_vals,
    }
    if extra:
        kwargs["ExpressionAttributeNames"] = {f"#{k}": k for k in extra}

    if estados_validos:
        placeholders = ",".join(f":s{i}" for i in range(len(estados_validos)))
        kwargs["ConditionExpression"] = f"estado IN ({placeholders})"
        for i, e in enumerate(estados_validos):
            expr_vals[f":s{i}"] = e

    table_solicitudes().update_item(**kwargs)