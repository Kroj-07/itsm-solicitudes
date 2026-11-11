import json
import os

from .fechas import ahora_iso


_TRUE = ("true", "1", "yes", "si", "sí")
_sns = None


def eventos_habilitados() -> bool:
    """
    Los eventos SNS/SQS estan POSPUESTOS en la fase actual (contrato v1.1).
    Solo se activan con EVENTS_ENABLED=true (fase EDA, cuando Infra publique los ARN reales).
    """
    return os.environ.get("EVENTS_ENABLED", "false").strip().lower() in _TRUE


def _cliente_sns():
    # Import y cliente perezosos: con eventos desactivados no se toca SNS.
    global _sns
    if _sns is None:
        import boto3
        _sns = boto3.client("sns")
    return _sns


def publicar_evento(evento: str, tenant_id: str, data: dict, origen: str = "solicitudes"):
    """
    Publica un evento en el topic SNS con el sobre del contrato
    y los MessageAttributes requeridos por las filter policies.

    - EVENTS_ENABLED=false (fase actual): no-op, retorna None.
    - Nunca propaga errores: se llama despues de persistir la solicitud y un fallo de
      mensajeria no debe convertir una operacion exitosa en un 500.
    """
    if not eventos_habilitados():
        return None

    topic_arn = os.environ.get("SNS_EVENTOS_ARN")
    if not topic_arn:
        print(f"[SNS] {evento} omitido: EVENTS_ENABLED=true pero falta SNS_EVENTOS_ARN")
        return None

    mensaje = {
        "evento": evento,
        "version": "1.0",
        "tenant_id": tenant_id,
        "origen": origen,
        "timestamp": ahora_iso(),
        "data": data,
    }

    try:
        resp = _cliente_sns().publish(
            TopicArn=topic_arn,
            Message=json.dumps(mensaje),
            MessageAttributes={
                "evento": {"DataType": "String", "StringValue": evento},
                "tenant_id": {"DataType": "String", "StringValue": tenant_id},
            },
        )
    except Exception as e:
        print(f"[SNS] ERROR publicando {evento} tenant={tenant_id}: {type(e).__name__}: {e}")
        return None

    print(f"[SNS] {evento} -> tenant={tenant_id} msgId={resp.get('MessageId')}")
    return resp
