import json
import os

import boto3

from .fechas import ahora_iso


_sns = boto3.client("sns")


def publicar_evento(evento: str, tenant_id: str, data: dict, origen: str = "solicitudes"):
    """
    Publica un evento en el topic SNS con el sobre del contrato
    y los MessageAttributes requeridos por las filter policies.
    """
    mensaje = {
        "evento": evento,
        "version": "1.0",
        "tenant_id": tenant_id,
        "origen": origen,
        "timestamp": ahora_iso(),
        "data": data,
    }

    resp = _sns.publish(
        TopicArn=os.environ["SNS_EVENTOS_ARN"],
        Message=json.dumps(mensaje),
        MessageAttributes={
            "evento": {"DataType": "String", "StringValue": evento},
            "tenant_id": {"DataType": "String", "StringValue": tenant_id},
        },
    )
    print(f"[SNS] {evento} -> tenant={tenant_id} msgId={resp.get('MessageId')}")
    return resp