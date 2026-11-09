import json


class ValidacionError(ValueError):
    pass


def parse_body(event) -> dict:
    """Devuelve el body como dict. Lanza ValidacionError si no es JSON válido."""
    raw = event.get("body")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValidacionError(f"Body JSON inválido: {e}")
    if not isinstance(body, dict):
        raise ValidacionError("El body debe ser un objeto JSON")
    return body


def requerido(body: dict, campo: str):
    valor = body.get(campo)
    if valor is None or (isinstance(valor, str) and valor.strip() == ""):
        raise ValidacionError(f"Campo requerido: {campo}")
    return valor


def entero(body: dict, campo: str, minimo: int | None = None) -> int:
    valor = requerido(body, campo)
    try:
        n = int(valor)
    except (ValueError, TypeError):
        raise ValidacionError(f"Campo {campo} debe ser entero")
    if minimo is not None and n < minimo:
        raise ValidacionError(f"Campo {campo} debe ser >= {minimo}")
    return n


def booleano(body: dict, campo: str, default: bool = False) -> bool:
    valor = body.get(campo, default)
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.lower() in ("true", "1", "si", "sí")
    return bool(valor)


def get_path_id(event, nombre: str = "id") -> str:
    pp = event.get("pathParameters") or {}
    valor = pp.get(nombre)
    if not valor:
        raise ValidacionError(f"Falta parámetro de ruta: {nombre}")
    return valor