from datetime import datetime, timezone


def ahora_iso() -> str:
    """Fecha actual en ISO 8601 UTC con sufijo Z (ej. 2026-11-12T14:30:00Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")