import json

from botocore.exceptions import ClientError

from ..common.dynamo import update_solicitud_estado
from ..common.eventos import publicar_evento
from ..common.fechas import ahora_iso


def _parse_sqs_record(record) -> dict:
    """
    SQS trae el sobre de SNS. Si raw delivery está activo, el body es el evento directo.
    """
    body = json.loads(record["body"])
    if isinstance(body, dict) and "Message" in body:
        return json.loads(body["Message"])  # SNS envelope
    return body  # raw delivery / send-message directo


def _procesar(record) -> None:
    evento = _parse_sqs_record(record)

    tipo = evento.get("evento")
    if tipo != "cambio.implementado":
        print(f"[consumer] Ignorando evento: {tipo}")
        return

    tenant_id = evento.get("tenant_id")
    data = evento.get("data") or {}
    solicitud_id = data.get("solicitud_id")
    cambio_id = data.get("cambio_id") or data.get("id")

    if not tenant_id or not solicitud_id:
        # Regla del contrato: si no trae solicitud_id, ignorar SIN fallar
        print(f"[consumer] Evento sin tenant_id o solicitud_id, ignorando: {evento}")
        return

    ahora = ahora_iso()
    extra = {"cambio_id": cambio_id} if cambio_id else None

    try:
        update_solicitud_estado(
            tenant_id, solicitud_id, "COMPLETADA", ahora,
            extra=extra,
            estados_validos=["APROBADA", "EN_EJECUCION"],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Ya estaba COMPLETADA (idempotencia) o estado inválido -> ignorar
            print(f"[consumer] {solicitud_id} ya completada o estado no válido, ignorando")
            return
        raise

    # Publicar solicitud.completada para q-notificaciones
    publicar_evento(
        "solicitud.completada",
        tenant_id,
        {"solicitud_id": solicitud_id},
    )
    print(f"[consumer] Solicitud {solicitud_id} ({tenant_id}) -> COMPLETADA")


def handler(event, context):
    """
    Lambda consumidora de q-solicitudes.
    Devuelve batchItemFailures para que SQS reintente sólo los fallidos.
    """
    fallidos = []
    for record in event.get("Records", []):
        try:
            _procesar(record)
        except Exception as e:
            print(f"[consumer] ERROR {record['messageId']}: {type(e).__name__}: {e}")
            fallidos.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": fallidos}