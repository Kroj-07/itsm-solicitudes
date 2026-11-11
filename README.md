# itsm-solicitudes

Microservicio **Solicitudes de Servicio + Catálogo** del proyecto final CS2032 (SaaS ITSM multi-tenant, serverless, basado en eventos).

- **Dueño**: Integrante 4
- **Runtime**: Python 3.12 · **Región**: `us-east-1` · **Stage**: `dev`
- **Stack (fase actual, avance 30%)**: API Gateway REST (**lambda-proxy**) + Lambda + DynamoDB (2 tablas) + Lambda Authorizer de `itsm-auth`
- **Eventos SNS/SQS**: **pospuestos por revisión del profesor** (no eliminados). Ver sección 3.
- **Repos relacionados**: `itsm-auth` (authorizer), `itsm-infra` (SNS/SQS, **no desplegar** en esta fase), `itsm-cambios`, `itsm-frontend`

---

## 1. Endpoints

Todas las rutas usan integración **lambda-proxy** (default de Serverless), están protegidas por el **Lambda Authorizer** de `itsm-auth` (`resultTtlInSeconds: 0`) y son **CORS-enabled**.
El `tenant_id`, `usuario_id` y `rol` salen del contexto del authorizer (JWT), **nunca** del body ni de la URL.

| Método | Ruta | Descripción | Roles |
|---|---|---|---|
| `GET` | `/catalogo` | Lista servicios del catálogo (paginado) | Todos |
| `POST` | `/catalogo` | Crea servicio | `ADMIN` |
| `PUT` | `/catalogo/{id}` | Edita servicio | `ADMIN` |
| `POST` | `/solicitudes` | Crea solicitud (estado `EN_APROBACION`, o `APROBADA` si el servicio no requiere aprobación) | Todos |
| `GET` | `/solicitudes` | Lista paginada, filtros `estado` y `solicitante_id` | Todos (SOLICITANTE sólo las suyas) |
| `GET` | `/solicitudes/{id}` | Detalle | Todos (SOLICITANTE sólo la suya) |
| `POST` | `/solicitudes/{id}/aprobar` | `EN_APROBACION` → `APROBADA` | `ADMIN` |
| `POST` | `/solicitudes/{id}/rechazar` | `EN_APROBACION` → `RECHAZADA` con `motivo` | `ADMIN` |
| `POST` | `/solicitudes/{id}/completar` | `APROBADA` → `COMPLETADA` | `ADMIN`, `AGENTE` |
| `DELETE` | `/solicitudes/{id}` | Cancela (sólo desde `EN_APROBACION`) | Solicitante dueño o `ADMIN` |

### Formato de respuesta

**Éxito**
```json
{
  "data": { "...": "..." },
  "paginacion": { "limite": 20, "siguiente": "<base64-LEK-o-null>" }
}
```

**Error**
```json
{ "error": { "codigo": "NO_ENCONTRADO", "mensaje": "Solicitud req-acme-000123 no existe" } }
```

**Códigos**: `200`, `201`, `400`, `401`, `403`, `404`, `409`, `500`. Fechas **ISO 8601 UTC** (`2026-11-12T14:30:00Z`).

### Paginación
Query params `limite` (default 20, máximo 100) y `siguiente` (token opaco en base64 del `LastEvaluatedKey`). Nunca se usa `Scan`.

---

## 2. Modelo de datos

Dos tablas DynamoDB `PAY_PER_REQUEST`, con `tenant_id` siempre como PK.

### `itsm-solicitudes-dev-catalogo`

| Atributo | Tipo | Notas |
|---|---|---|
| `tenant_id` | S | **PK** |
| `servicio_id` | S | **SK** — `srv-{tenant}-{n:03d}` |
| `nombre`, `categoria` | S | |
| `sla_horas` | N | |
| `requiere_aprobacion` | BOOL | |
| `es_cambio_estandar` | BOOL | si es `true`, aprobar publicará `solicitud.cambio_estandar` (cuando los eventos estén activos) |
| `activo` | BOOL | |

### `itsm-solicitudes-dev-solicitudes`

| Atributo | Tipo | Notas |
|---|---|---|
| `tenant_id` | S | **PK** |
| `solicitud_id` | S | **SK** — `req-{tenant}-{n:06d}` |
| `servicio_id`, `solicitante_id` | S | |
| `estado` | S | ver flujo abajo |
| `datos_formulario` | M | `{ "justificacion": "..." }` |
| `cambio_id` | S \| null | se llenará al completar vía cambio estándar (fase de eventos) |
| `creado_en`, `actualizado_en` | S | ISO 8601 UTC |
| **`estado_creado`** | S | GSI1: `"{estado}#{creado_en}"` |
| **`solicitante_creado`** | S | GSI2: `"{solicitante_id}#{creado_en}"` |

**GSIs**:
- `GSI1` — PK `tenant_id` + SK `estado_creado` (listar por estado)
- `GSI2` — PK `tenant_id` + SK `solicitante_creado` (mis solicitudes)

La estructura completa (para el informe) está en [`docs/modelo.json`](docs/modelo.json).

### Flujo de estados (unificado, contrato v1.1)

```
EN_APROBACION ──► APROBADA ──► COMPLETADA
      │
      ├──► RECHAZADA
      └──► CANCELADA
```

- Estados válidos: `EN_APROBACION`, `APROBADA`, `COMPLETADA`, `RECHAZADA`, `CANCELADA`.
- Si el servicio tiene `requiere_aprobacion: false`, la solicitud nace en `APROBADA`.
- `aprobar` y `rechazar` (ADMIN) y `cancelar` (dueño o ADMIN) sólo desde `EN_APROBACION`.
- `completar` (ADMIN/AGENTE) sólo desde `APROBADA`. La completación automática por `cambio.implementado` queda para la fase de eventos.
- `ENVIADA` y `EN_EJECUCION` **ya no existen** en esta versión (no hay endpoints que los gestionen).

---

## 3. Eventos (APLAZADOS, no eliminados)

> **Estado**: SNS/SQS están pospuestos en esta fase por revisión del profesor. El plan original sigue contemplando la arquitectura basada en eventos para la entrega completa.

Qué significa en este repo:

- `serverless.yml` **no** resuelve `/itsm/dev/sns-eventos-arn` ni `/itsm/dev/q-solicitudes-arn` y **no** declara triggers SQS (`consumirCambios` fuera de `functions`).
- `provider.environment.EVENTS_ENABLED: 'false'`. `publicar_evento()` (`src/common/eventos.py`) retorna sin hacer nada; ninguna operación CRUD falla por eventos.
- `src/consumers/cambios.py` se conserva como código futuro y está excluido del paquete (`package.patterns`).
- No se crean placeholders de ARN falsos.

Diseño previsto para la fase EDA (sin cambios):

| Publica (topic `itsm-eventos-dev`) | Cuándo | `data` |
|---|---|---|
| `solicitud.creada` | `POST /solicitudes` | `{ solicitud_id, servicio_id }` |
| `solicitud.cambio_estandar` | `POST .../aprobar` sobre servicio con `es_cambio_estandar: true` | `{ solicitud_id, servicio_id }` |
| `solicitud.completada` | `POST .../completar` o al consumir `cambio.implementado` | `{ solicitud_id }` |

| Consume (`q-solicitudes`) | Acción |
|---|---|
| `cambio.implementado` | `APROBADA` → `COMPLETADA`, guarda `cambio_id`, publica `solicitud.completada`. Idempotente. |

Sobre del mensaje y `MessageAttributes` (`evento`, `tenant_id`) sin cambios respecto al plan original.

**Para activar la fase EDA** (cuando el profesor lo autorice): desplegar `itsm-infra` primero, publicar los ARN reales en SSM, volver a declarar `SNS_EVENTOS_ARN` (SSM) y el trigger `consumirCambios` en `serverless.yml`, quitar la exclusión de `src/consumers/**` y poner `EVENTS_ENABLED: 'true'`.

---

## 4. Requisitos previos

| Herramienta | Versión |
|---|---|
| AWS CLI v2 | última |
| Node.js | 20 LTS |
| Serverless Framework | **v3** (`npm i -g serverless@3`) |
| Python | 3.12 |
| Git | última |
| Postman o Bruno | para pruebas |

**Credenciales AWS Academy**: se renuevan cada sesión → ejecutar `aws sts get-caller-identity` antes de cada deploy.

**Parámetro SSM requerido** (si no existe en la misma cuenta y región, `serverless deploy` falla):

| Parámetro | Dueño |
|---|---|
| `/itsm/dev/authorizer-arn` | Integrante 5 (`itsm-auth`) |

Verificar:
```bash
aws ssm get-parameter --name /itsm/dev/authorizer-arn
```

En esta fase **no** se necesitan `/itsm/dev/sns-eventos-arn` ni `/itsm/dev/q-solicitudes-arn`.

---

## 5. Clonar y desplegar

> ⚠️ **`git clone` NO despliega a AWS.** Trae el código. El deploy es `serverless deploy`.

```bash
# 1. Traer el código
git clone https://github.com/<org>/itsm-solicitudes.git
cd itsm-solicitudes

# 2. Verificar credenciales (Learner Lab rota cada sesión)
aws sts get-caller-identity

# 3. Desplegar (crea API Gateway lambda-proxy + Lambdas + DynamoDB; sin SNS/SQS)
npm install -g serverless@3
serverless deploy --stage dev

# 4. Guardar la salida (evidencia del informe)
serverless deploy --stage dev | tee docs/deploy-output.txt
```

La salida incluye `endpoints:` con las URLs. Compartirlas en el canal del grupo (las necesita el Integrante 2 para `VITE_API_SOLICITUDES`).

> ⚠️ **No ejecutar `serverless remove`**: puede eliminar las tablas DynamoDB y sus datos (contrato v1.1, sección 12). No cambiar PK/SK ni reemplazar tablas con datos sin plan de migración y backup.

---

## 6. Seed de datos (30,000 solicitudes)

Corre **en local**, no en Lambda.

```bash
# Instalar dependencias del seed en un venv
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r seed/requirements-seed.txt

# Meta 13-Nov: primer tenant
python seed/seed_solicitudes.py --tenant acme

# Meta 17-Nov: los 3 tenants
python seed/seed_solicitudes.py --all

# Verificar conteos (sin reescribir)
python seed/seed_solicitudes.py --all --verify
```

**Salida esperada de `--verify`:**
```
  acme      catalogo= 15  solicitudes= 10000  [OK]
  globex    catalogo= 15  solicitudes= 10000  [OK]
  initech   catalogo= 15  solicitudes= 10000  [OK]
  TOTAL solicitudes: 30000
```

**Detalles del seed**:
- Semilla determinista por tenant (`SEED = 2026 + sum(ord(c) for c in tenant)`) → correr `--tenant acme` o `--all` produce los mismos datos para `acme`.
- Catálogo: 15 servicios por tenant (`srv-{tenant}-001` … `-015`), 6 con `es_cambio_estandar: true`.
- Estados (flujo unificado): **65% cerradas** (`COMPLETADA` 55%, `CANCELADA` 5%, `RECHAZADA` 5%), **35% en progreso** (`APROBADA` 15%, `EN_APROBACION` 20%; un servicio sin aprobación nunca queda `EN_APROBACION`).
- Fechas en los últimos 12 meses, con ventana menor para estados nuevos.
- `batch_writer()` de boto3 (lotes de 25) + `tqdm`.
- `put_item` es idempotente: los IDs son deterministas, así que volver a correr el seed sobrescribe los mismos registros (útil si se había sembrado con los estados antiguos `ENVIADA`/`EN_EJECUCION`).

**Evidencia para el informe** (conteo por tenant):
```bash
python seed/seed_solicitudes.py --all --verify
```
o con AWS CLI (ojo: la versión de una línea devuelve sólo la primera página):
```bash
aws dynamodb query \
  --table-name itsm-solicitudes-dev-solicitudes \
  --key-condition-expression "tenant_id = :t" \
  --expression-attribute-values '{"t":{"S":"acme"}}' \
  --select COUNT
```
Usar el script `--verify` (pagina correctamente) como evidencia oficial.

---

## 7. Pruebas

### 7.1 Autorización

```bash
# Sin token -> 401
curl -i "$API_URL/solicitudes"

# Con token -> 200
curl -i -H "Authorization: Bearer $JWT" "$API_URL/solicitudes"
```

### 7.2 Flujo E2E (acme)

```bash
# 1) Crear solicitud de un servicio que requiere aprobación
curl -X POST "$API_URL/solicitudes" \
  -H "Authorization: Bearer $JWT_SOLICITANTE" \
  -H "Content-Type: application/json" \
  -d '{"servicio_id":"srv-acme-001","datos_formulario":{"justificacion":"Necesito correo"}}'

# 2) Aprobar como ADMIN  (EN_APROBACION -> APROBADA)
curl -X POST "$API_URL/solicitudes/req-acme-XXXXXX/aprobar" \
  -H "Authorization: Bearer $JWT_ADMIN"

# 3) Completar como AGENTE  (APROBADA -> COMPLETADA)
curl -X POST "$API_URL/solicitudes/req-acme-XXXXXX/completar" \
  -H "Authorization: Bearer $JWT_AGENTE"
```

### 7.3 Simular `cambio.implementado` (POSPUESTO)

Requiere SNS/SQS desplegados (fase EDA). En esta fase la completación se prueba con `POST /solicitudes/{id}/completar`.

### 7.4 Aislamiento multi-tenant (evidencia del informe)

```bash
# Con token de globex, pedir un ID de acme -> 404
curl -i "$API_URL/solicitudes/req-acme-000001" \
  -H "Authorization: Bearer $JWT_GLOBEX"
```

---

## 8. Estructura del repo

```
itsm-solicitudes/
├── serverless.yml
├── requirements.txt              (vacío: boto3 ya viene en el runtime)
├── src/
│   ├── common/                   respuestas, auth, dynamo, eventos (no-op si EVENTS_ENABLED=false), validación
│   ├── handlers/
│   │   ├── catalogo.py           GET/POST/PUT catálogo
│   │   └── solicitudes.py        CRUD + aprobar/rechazar/completar/cancelar
│   └── consumers/
│       └── cambios.py            FUTURO (fase EDA): consumidor de q-solicitudes, fuera del despliegue
├── seed/
│   ├── seed_solicitudes.py       seed de 30,000 solicitudes + catálogo
│   └── requirements-seed.txt
├── docs/
│   ├── modelo.json               estructura JSON de las tablas (informe)
│   └── deploy-output.txt         salida de serverless deploy (evidencia)
└── README.md
```

---

## 9. Notas para el grupo

- **Front**: la URL base del API va en `VITE_API_SOLICITUDES` (variables de entorno del repo `itsm-frontend`, Amplify).
- **Cambios** (fase EDA): el evento `solicitud.cambio_estandar` trae `solicitud_id`; al implementar ese cambio, Cambios debe devolver `solicitud_id` en `cambio.implementado` para que el consumidor sepa qué completar. Verificar el contrato antes de habilitar consumidores.
- **Notificaciones** (fase EDA): `solicitud.creada` y `solicitud.completada` irán a `q-notificaciones`.
- **Cambios al contrato** (nombres de tabla, estados, sobre del evento): avisar al grupo antes de aplicarlos.

---

## 10. Troubleshooting

| Síntoma | Causa / fix |
|---|---|
| `serverless deploy` falla en `Cannot resolve variable at ... /itsm/dev/authorizer-arn` | Falta el parámetro SSM o estás en otra cuenta/región. Verificar con `aws sts get-caller-identity` y `aws ssm get-parameter`. No crear ARNs placeholder falsos. |
| `500`/`Internal server error` o `event` sin `requestContext` | La ruta debe ser lambda-proxy (no usar `integration: lambda`). |
| `401` incluso con token válido | Falta `Authorization: Bearer <jwt>` en el header. |
| `403` al crear catálogo | El token no tiene `rol: ADMIN`. |
| `404` al pedir un ID que existe en otro tenant | **Correcto** (aislamiento). |
| Estados `ENVIADA`/`EN_EJECUCION` en datos viejos | Re-correr el seed (IDs deterministas, sobrescribe). |
| Credenciales expiran a mitad del deploy | Renovar la sesión del Learner Lab (credenciales rotan) y reintentar. |