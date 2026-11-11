#!/usr/bin/env python3
"""
Seed del microservicio Solicitudes.

Genera, por tenant:
  * Catálogo de servicios (~15 servicios, IDs srv-{tenant}-{n:03d})
  * 10,000 solicitudes (IDs req-{tenant}-{n:06d}, n = 000001..010000)

Uso:
    # Meta 13-Nov: un solo tenant
    python seed/seed_solicitudes.py --tenant acme

    # Meta 17-Nov: los 3 tenants (acme, globex, initech)
    python seed/seed_solicitudes.py --all

    # Verificar conteos sin escribir nada
    python seed/seed_solicitudes.py --all --verify

    # Otro stage / region
    python seed/seed_solicitudes.py --all --stage dev --region us-east-1
"""
import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError
from faker import Faker
from tqdm import tqdm


# ============================================================
# Configuración
# ============================================================
TENANTS = ["acme", "globex", "initech"]
SOLICITUDES_POR_TENANT = 10_000
SEED = 2026

# Flujo unificado (contrato v1.1): EN_APROBACION -> APROBADA -> COMPLETADA
#                                  EN_APROBACION -> RECHAZADA | CANCELADA
# Distribución realista (65% cerradas, 35% en progreso)
DIST_ESTADOS = [
    ("COMPLETADA",    0.55),  # cerrada
    ("CANCELADA",     0.05),  # cerrada
    ("RECHAZADA",     0.05),  # cerrada
    ("APROBADA",      0.15),  # en progreso
    ("EN_APROBACION", 0.20),  # en progreso
]

# Ventana temporal máxima de creación según el estado
ANTIGUEDAD_MAX_DIAS = {
    "EN_APROBACION":  45,
    "APROBADA":       90,
    "COMPLETADA":    365,
    "CANCELADA":     365,
    "RECHAZADA":     365,
}

# Catálogo base (15 servicios típicos de ITSM).
# Algunos marcados como cambio estándar para poder demostrar el evento
# solicitud.cambio_estandar en la demo.
CATALOGO_BASE = [
    {"nombre": "Alta de cuenta de correo",   "categoria": "ACCESOS",      "sla_horas": 24, "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Reset de contraseña",        "categoria": "ACCESOS",      "sla_horas": 2,  "requiere_aprobacion": False, "es_cambio_estandar": True},
    {"nombre": "Acceso VPN",                 "categoria": "ACCESOS",      "sla_horas": 4,  "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Instalar software",          "categoria": "SOFTWARE",     "sla_horas": 48, "requiere_aprobacion": True,  "es_cambio_estandar": True},
    {"nombre": "Actualizar licencia Office", "categoria": "SOFTWARE",     "sla_horas": 8,  "requiere_aprobacion": False, "es_cambio_estandar": True},
    {"nombre": "Configurar impresora",       "categoria": "HARDWARE",     "sla_horas": 4,  "requiere_aprobacion": False, "es_cambio_estandar": True},
    {"nombre": "Reemplazo de laptop",        "categoria": "HARDWARE",     "sla_horas": 72, "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Nueva cuenta en SharePoint", "categoria": "APLICACIONES", "sla_horas": 12, "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Acceso a GitLab",            "categoria": "APLICACIONES", "sla_horas": 8,  "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Crear repositorio Git",      "categoria": "APLICACIONES", "sla_horas": 4,  "requiere_aprobacion": False, "es_cambio_estandar": True},
    {"nombre": "Solicitud de backup",        "categoria": "DATOS",        "sla_horas": 24, "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Restaurar archivo borrado",  "categoria": "DATOS",        "sla_horas": 8,  "requiere_aprobacion": False, "es_cambio_estandar": True},
    {"nombre": "Alta en teléfono IP",        "categoria": "TELEFONIA",    "sla_horas": 24, "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Renovar certificado SSL",    "categoria": "SEGURIDAD",    "sla_horas": 48, "requiere_aprobacion": True,  "es_cambio_estandar": False},
    {"nombre": "Baja de usuario",            "categoria": "ACCESOS",      "sla_horas": 4,  "requiere_aprobacion": True,  "es_cambio_estandar": True},
]


# ============================================================
# Helpers
# ============================================================
def _seed_tenant(tenant: str) -> int:
    """Semilla determinista por tenant para que acme sea idéntico
    se corra solo o en --all."""
    return SEED + sum(ord(c) for c in tenant)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _elegir_estado() -> str:
    estados = [e for e, _ in DIST_ESTADOS]
    pesos = [p for _, p in DIST_ESTADOS]
    return random.choices(estados, weights=pesos, k=1)[0]


def _fecha_creacion(estado: str) -> datetime:
    max_dias = ANTIGUEDAD_MAX_DIAS[estado]
    delta = timedelta(
        days=random.randint(0, max_dias),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return datetime.now(timezone.utc) - delta


# ============================================================
# Generación de catálogo
# ============================================================
def _construir_catalogo(tenant: str) -> list[dict]:
    servicios = []
    for i, base in enumerate(CATALOGO_BASE, start=1):
        servicios.append({
            "tenant_id": tenant,
            "servicio_id": f"srv-{tenant}-{i:03d}",
            "nombre": base["nombre"],
            "categoria": base["categoria"],
            "sla_horas": base["sla_horas"],
            "requiere_aprobacion": base["requiere_aprobacion"],
            "es_cambio_estandar": base["es_cambio_estandar"],
            "activo": True,
        })
    return servicios


# ============================================================
# Generación de solicitudes
# ============================================================
def _construir_solicitud(tenant: str, n: int, servicios: list[dict]) -> dict:
    solicitud_id = f"req-{tenant}-{n:06d}"
    servicio = random.choice(servicios)
    solicitante_id = f"usr-{tenant}-{random.randint(1, 10000):05d}"

    estado = _elegir_estado()

    # Coherencia: un servicio sin aprobación nunca queda EN_APROBACION
    if estado == "EN_APROBACION" and not servicio["requiere_aprobacion"]:
        estado = "APROBADA"

    creado = _fecha_creacion(estado)

    # actualizado_en: entre creado y ahora
    ahora = datetime.now(timezone.utc)
    margen = max(60, int((ahora - creado).total_seconds()))
    actualizado = min(creado + timedelta(seconds=random.randint(60, margen)), ahora)

    item = {
        "tenant_id": tenant,
        "solicitud_id": solicitud_id,
        "servicio_id": servicio["servicio_id"],
        "solicitante_id": solicitante_id,
        "estado": estado,
        "datos_formulario": {
            "justificacion": fake.sentence(nb_words=12),
        },
        "cambio_id": None,
        "creado_en": _iso(creado),
        "actualizado_en": _iso(actualizado),
        # Atributos GSI (obligatorios en cada item)
        "estado_creado":      f"{estado}#{_iso(creado)}",
        "solicitante_creado": f"{solicitante_id}#{_iso(creado)}",
    }

    # Campos adicionales según el estado
    if estado in ("APROBADA", "COMPLETADA"):
        item["aprobador_id"] = f"usr-{tenant}-{random.randint(1, 100):05d}"

    if estado == "RECHAZADA":
        item["motivo_rechazo"] = random.choice([
            "Falta justificación del solicitante",
            "No cumple la política de aprobación",
            "Solicitud duplicada de un ticket existente",
            "Requiere aprobación del jefe de área",
            "El servicio no aplica para el rol del solicitante",
        ])

    # Para solicitudes COMPLETADAS de servicios con es_cambio_estandar,
    # dejamos trazabilidad del cambio que la completó.
    if estado == "COMPLETADA" and servicio["es_cambio_estandar"]:
        item["cambio_id"] = f"chg-{tenant}-{n:06d}"

    return item


# ============================================================
# E/S DynamoDB
# ============================================================
def _tabla_existe(cliente, nombre: str) -> bool:
    try:
        cliente.describe_table(TableName=nombre)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def _escribir_items(tabla, items: list[dict], descripcion: str) -> None:
    """batch_writer() ya agrupa internamente en lotes de 25."""
    with tabla.batch_writer() as batch:
        for item in tqdm(items, desc=descripcion, unit="item", ncols=90):
            batch.put_item(Item=item)


def _contar(tabla, tenant: str) -> int:
    total = 0
    kwargs = {
        "KeyConditionExpression": "tenant_id = :t",
        "ExpressionAttributeValues": {":t": tenant},
        "Select": "COUNT",
    }
    while True:
        resp = tabla.query(**kwargs)
        total += resp["Count"]
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return total


# ============================================================
# Seed de un tenant
# ============================================================
def seed_tenant(
    tenant: str,
    dynamo,
    tabla_catalogo_nombre: str,
    tabla_solicitudes_nombre: str,
    skip_catalogo: bool,
) -> None:
    print(f"\n=== Tenant: {tenant} ===")

    # Semilla reproducible independiente del orden de ejecución
    seed_val = _seed_tenant(tenant)
    random.seed(seed_val)
    Faker.seed(seed_val)

    tabla_catalogo = dynamo.Table(tabla_catalogo_nombre)
    tabla_solicitudes = dynamo.Table(tabla_solicitudes_nombre)

    # 1) Catálogo
    if not skip_catalogo:
        catalogo = _construir_catalogo(tenant)
        _escribir_items(tabla_catalogo, catalogo, f"catalogo {tenant}")
    else:
        print("[skip] catálogo no se reescribe")

    # Recuperar catálogo de DynamoDB (para que los IDs usados existan aunque
    # se haya hecho --skip-catalogo con un catálogo ya cargado)
    servicios = []
    kwargs = {"KeyConditionExpression": "tenant_id = :t",
              "ExpressionAttributeValues": {":t": tenant}}
    while True:
        resp = tabla_catalogo.query(**kwargs)
        servicios.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    if not servicios:
        raise RuntimeError(
            f"No hay catálogo para '{tenant}' en {tabla_catalogo_nombre}. "
            f"Corre sin --skip-catalogo."
        )

    # 2) Solicitudes
    solicitudes = [
        _construir_solicitud(tenant, n, servicios)
        for n in range(1, SOLICITUDES_POR_TENANT + 1)
    ]
    _escribir_items(tabla_solicitudes, solicitudes, f"solicitudes {tenant}")

    # 3) Verificación
    n_cat = _contar(tabla_catalogo, tenant)
    n_sol = _contar(tabla_solicitudes, tenant)
    print(f"[ok] {tenant}: catalogo={n_cat}  solicitudes={n_sol}")


# ============================================================
# CLI
# ============================================================
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed de Solicitudes (Integrante 4)")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--tenant", choices=TENANTS, help="Seed solo este tenant")
    grupo.add_argument("--all", action="store_true", help="Seed los 3 tenants en orden")

    parser.add_argument("--stage",  default="dev",       help="Stage (default: dev)")
    parser.add_argument("--region", default="us-east-1", help="Región AWS (default: us-east-1)")
    parser.add_argument("--skip-catalogo", action="store_true",
                        help="No reescribir el catálogo (asume que ya está cargado)")
    parser.add_argument("--verify", action="store_true",
                        help="Solo verificar conteos, no escribir")
    args = parser.parse_args(argv)

    tabla_catalogo_nombre    = f"itsm-solicitudes-{args.stage}-catalogo"
    tabla_solicitudes_nombre = f"itsm-solicitudes-{args.stage}-solicitudes"

    dynamo = boto3.resource("dynamodb", region_name=args.region)
    cliente = boto3.client("dynamodb", region_name=args.region)

    print(f"Región: {args.region}  stage: {args.stage}")
    print(f"Tabla catálogo:    {tabla_catalogo_nombre}")
    print(f"Tabla solicitudes: {tabla_solicitudes_nombre}")

    # Verificación de tablas
    for nombre in (tabla_catalogo_nombre, tabla_solicitudes_nombre):
        if not _tabla_existe(cliente, nombre):
            print(f"ERROR: la tabla '{nombre}' no existe. Despliega primero con "
                  f"'serverless deploy --stage {args.stage}'.", file=sys.stderr)
            return 1

    tenants = TENANTS if args.all else [args.tenant]

    if args.verify:
        print("\n=== Verificación de conteos ===")
        total_sol = 0
        for tenant in tenants:
            n_cat = _contar(dynamo.Table(tabla_catalogo_nombre), tenant)
            n_sol = _contar(dynamo.Table(tabla_solicitudes_nombre), tenant)
            total_sol += n_sol
            estado = "OK" if n_sol == SOLICITUDES_POR_TENANT else "PENDIENTE"
            print(f"  {tenant:8s}  catalogo={n_cat:3d}  solicitudes={n_sol:6d}  [{estado}]")
        print(f"  TOTAL solicitudes: {total_sol}")
        return 0

    for tenant in tenants:
        seed_tenant(
            tenant=tenant,
            dynamo=dynamo,
            tabla_catalogo_nombre=tabla_catalogo_nombre,
            tabla_solicitudes_nombre=tabla_solicitudes_nombre,
            skip_catalogo=args.skip_catalogo,
        )

    print("\n=== Resumen ===")
    for tenant in tenants:
        n_sol = _contar(dynamo.Table(tabla_solicitudes_nombre), tenant)
        print(f"  {tenant:8s}  {n_sol} solicitudes")

    return 0


if __name__ == "__main__":
    sys.exit(main())