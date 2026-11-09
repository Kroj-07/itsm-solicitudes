import decimal
import json


_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
}


def _json_default(obj):
    """Convierte Decimal (DynamoDB) a int/float para que json.dumps funcione."""
    if isinstance(obj, decimal.Decimal):
        if obj == obj.to_integral_value():
            return int(obj)
        return float(obj)
    raise TypeError(f"Tipo no serializable: {type(obj)}")


def _respuesta(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": _CORS,
        "body": json.dumps(payload, default=_json_default),
    }


def ok(data, paginacion=None, status: int = 200) -> dict:
    payload = {"data": data}
    if paginacion is not None:
        payload["paginacion"] = paginacion
    return _respuesta(status, payload)


def created(data) -> dict:
    return _respuesta(201, {"data": data})


def error(codigo: str, mensaje: str, status: int) -> dict:
    return _respuesta(status, {"error": {"codigo": codigo, "mensaje": mensaje}})


# Atajos
def error_validacion(mensaje: str) -> dict:
    return error("VALIDACION", mensaje, 400)


def error_no_autorizado(mensaje: str = "No autorizado") -> dict:
    return error("NO_AUTORIZADO", mensaje, 401)


def error_prohibido(mensaje: str = "Prohibido") -> dict:
    return error("PROHIBIDO", mensaje, 403)


def error_no_encontrado(mensaje: str) -> dict:
    return error("NO_ENCONTRADO", mensaje, 404)


def error_conflicto(mensaje: str) -> dict:
    return error("ESTADO_INVALIDO", mensaje, 409)


def error_interno(exc: Exception) -> dict:
    print(f"[500] {type(exc).__name__}: {exc}")
    return error("INTERNO", "Error interno del servidor", 500)