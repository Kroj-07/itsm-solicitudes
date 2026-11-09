# itsm-solicitudes

Microservicio **Solicitudes de Servicio + Catálogo** del proyecto final CS2032 (SaaS ITSM multi-tenant, serverless, basado en eventos).

- **Dueño**: Integrante 4
- **Runtime**: Python 3.12 · **Región**: `us-east-1` · **Stage**: `dev`
- **Stack**: API Gateway (REST) + Lambda + DynamoDB (2 tablas) + SNS + SQS
- **Repos relacionados**: `itsm-auth` (authorizer), `itsm-infra` (SNS/SQS), `itsm-cambios` (publica `cambio.implementado`), `itsm-frontend`

---

## 1. Endpoints

Todas las rutas están protegidas por el **Lambda Authorizer** del Integrante 5 y son **CORS-enabled**.
El `tenant_id`, `usuario_id` y `rol` salen del contexto del authorizer (JWT), **nunca** del body ni de la URL.

| Método | Ruta | Descripción | Roles |
|---|---|---|---|
| `GET` | `/catalogo` | Lista servicios del catálogo (paginado) | Todos |
| `POST` | `/catalogo` | Crea servicio | `ADMIN` |
| `PUT` | `/catalogo/{id}` | Edita servicio | `ADMIN` |
| `POST` | `/solicitudes` | Crea solicitud (estado `ENVIADA` o `EN_APROBACION`) | Todos |
| `GET` | `/solicitudes` | Lista paginada, filtros `estado` y `solicitante_id` | Todos (SOLICITANTE sólo las suyas) |
| `GET` | `/solicitudes/{id}` | Detalle | Todos (SOLICITANTE sólo la suya) |
| `POST` | `/solicitudes/{id}/aprobar` | Aprueba (publica `solicitud.cambio_estandar` si aplica) | `ADMIN` |
| `POST` | `/solicitudes/{id}/rechazar` | Rechaza con `motivo` | `ADMIN` |
| `POST` | `/solicitudes/{id}/completar` | Marca `COMPLETADA` (publica `solicitud.completada`) | `ADMIN`, `AGENTE` |
| `DELETE` | `/solicitudes/{id}` | Cancela (sólo desde `ENVIADA`/`EN_APROBACION`) | Solicitante dueño o `ADMIN` |

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
| `es_cambio_estandar` | BOOL | si es `true`, aprobar publica `solicitud.cambio_estandar` |
| `activo` | BOOL | |

### `itsm-solicitudes-dev-solicitudes`

| Atributo | Tipo | Notas |
|---|---|---|
| `tenant_id` | S | **PK** |
| `solicitud_id` | S | **SK** — `req-{tenant}-{n:06d}` |
| `servicio_id`, `solicitante_id` | S | |
| `estado` | S | ver flujo abajo |
| `datos_formulario` | M | `{ "justificacion": "..." }` |
| `cambio_id` | S \| null | se llena al completar vía cambio estándar |
| `creado_en`, `actualizado_en` | S | ISO 8601 UTC |
| **`estado_creado`** | S | GSI1: `"{estado}#{creado_en}"` |
| **`solicitante_creado`** | S | GSI2: `"{solicitante_id}#{creado_en}"` |

**GSIs**:
- `GSI1` — PK `tenant_id` + SK `estado_creado` (listar por estado)
- `GSI2` — PK `tenant_id` + SK `solicitante_creado` (mis solicitudes)

La estructura completa (para el informe) está en [`docs/modelo.json`](docs/modelo.json).

### Flujo de estados

```
ENVIADA ─────────► EN_APROBACION ──► APROBADA ──► EN_EJECUCION ──► COMPLETADA
   │                    │
   └──► CANCELADA       └──► RECHAZADA
```

- Si el servicio tiene `requiere_aprobacion: false`, la solicitud nace en `APROBADA`.
- `completar` es manual (`ADMIN`/`AGENTE`) **o** automático cuando llega `cambio.implementado` por `q-solicitudes`.
- `cancelar` se permite desde `ENVIADA` o `EN_APROBACION` (dueño o `ADMIN`).

---

## 3. Eventos

### Publica (topic `itsm-eventos-dev`)

| Evento | Cuándo | `data` |
|---|---|---|
| `solicitud.creada` | `POST /solicitudes` | `{ solicitud_id, servicio_id }` |
| `solicitud.cambio_estandar` | `POST .../aprobar` sobre servicio con `es_cambio_estandar: true` | `{ solicitud_id, servicio_id }` |
| `solicitud.completada` | `POST .../completar` o al consumir `cambio.implementado` | `{ solicitud_id }` |

### Consume (cola `q-solicitudes`)

| Evento | Acción |
|---|---|
| `cambio.implementado` | Pasa la solicitud a `COMPLETADA`, guarda `cambio_id` y publica `solicitud.completada`. Idempotente. |

**Sobre del mensaje** (body JSON en SNS):
```json
{
  "evento": "solicitud.cambio_estandar",
  "version": "1.0",
  "tenant_id": "acme",
  "origen": "solicitudes",
  "timestamp": "2026-11-12T14:30:00Z",
  "data": { "solicitud_id": "req-acme-000123", "servicio_id": "srv-acme-004" }
}
```
**MessageAttributes** (obligatorios, sin ellos las filter policies no enrutan):
```
evento    = String "solicitud.cambio_estandar"
tenant_id = String "acme"
```

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

**Parámetros SSM requeridos** (los publican otros integrantes; si no existen, `serverless deploy` falla):

| Parámetro | Dueño |
|---|---|
| `/itsm/dev/authorizer-arn` | Integrante 5 |
| `/itsm/dev/sns-eventos-arn` | Integrante 1 |
| `/itsm/dev/q-solicitudes-arn` | Integrante 1 |

Verificar:
```bash
aws ssm get-parameter --name /itsm/dev/authorizer-arn
aws ssm get-parameter --name /itsm/dev/sns-eventos-arn
aws ssm get-parameter --name /itsm/dev/q-solicitudes-arn
```

---

## 5. Clonar y desplegar

> ⚠️ **`git clone` NO despliega a AWS.** Trae el código. El deploy es `serverless deploy`.

```bash
# 1. Traer el código
git clone https://github.com/<org>/itsm-solicitudes.git
cd itsm-solicitudes

# 2. Verificar credenciales (Learner Lab rota cada sesión)
aws sts get-caller-identity

# 3. Desplegar (crea Lambdas + DynamoDB)
npm install -g serverless@3
serverless deploy --stage dev

# 4. Guardar la salida (evidencia del informe)
serverless deploy --stage dev | tee docs/deploy-output.txt
```

La salida incluye `endpoints:` con las URLs. Compartirlas en el canal del grupo (las necesita el Integrante 2 para `VITE_API_SOLICITUDES`).

**Borra todo** (si hay que recrear desde cero):
```bash
serverless remove --stage dev
```

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
- Estados: **60% cerradas** (`COMPLETADA`/`CANCELADA`/`RECHAZADA`), **25% en progreso**, **15% nuevas**.
- Fechas en los últimos 12 meses, con ventana menor para estados nuevos.
- `batch_writer()` de boto3 (lotes de 25) + `tqdm`.
- `put_item` es idempotente: correr el seed dos veces es seguro.

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

# 2) Aprobar como ADMIN  (dispara solicitud.cambio_estandar si aplica)
curl -X POST "$API_URL/solicitudes/req-acme-XXXXXX/aprobar" \
  -H "Authorization: Bearer $JWT_ADMIN"

# 3) Completar como AGENTE
curl -X POST "$API_URL/solicitudes/req-acme-XXXXXX/completar" \
  -H "Authorization: Bearer $JWT_AGENTE"
```

### 7.3 Simular `cambio.implementado` sin esperar a Cambios

```bash
aws sns publish --topic-arn "$SNS_EVENTOS_ARN" \
  --message '{"evento":"cambio.implementado","version":"1.0","tenant_id":"acme","origen":"cambios","timestamp":"2026-11-12T14:30:00Z","data":{"solicitud_id":"req-acme-000123","cambio_id":"chg-acme-000123"}}' \
  --message-attributes '{"evento":{"DataType":"String","StringValue":"cambio.implementado"},"tenant_id":{"DataType":"String","StringValue":"acme"}}'
```

Verificar en **CloudWatch Logs** del Lambda `itsm-solicitudes-dev-consumirCambios` y confirmar en DynamoDB que la solicitud pasó a `COMPLETADA` con `cambio_id` seteado.

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
│   ├── common/                   respuestas, auth, dynamo, eventos, validación
│   ├── handlers/
│   │   ├── catalogo.py           GET/POST/PUT catálogo
│   │   └── solicitudes.py        CRUD + aprobar/rechazar/completar/cancelar
│   └── consumers/
│       └── cambios.py            consumidor de q-solicitudes (cambio.implementado)
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
- **Cambios**: el evento `solicitud.cambio_estandar` trae `solicitud_id`; al implementar ese cambio, Cambios debe devolver `solicitud_id` en `cambio.implementado` para que nuestro consumidor sepa qué completar.
- **Notificaciones**: `solicitud.creada` y `solicitud.completada` van a `q-notificaciones` (filtradas por `evento`).
- **Cambios al contrato** (nombres de tabla, estados, sobre del evento): avisar al grupo antes de aplicarlos.

---

## 10. Troubleshooting

| Síntoma | Causa / fix |
|---|---|
| `serverless deploy` falla en `Cannot resolve variable at ... /itsm/dev/...` | Falta el parámetro SSM. Crear placeholder temporal o esperar al dueño. |
| `401` incluso con token válido | Falta `Authorization: Bearer <jwt>` en el header. |
| `403` al crear catálogo | El token no tiene `rol: ADMIN`. |
| `404` al pedir un ID que existe en otro tenant | **Correcto** (aislamiento). |
| Consumidor no procesa nada | La filter policy de `q-solicitudes` no enruta `cambio.implementado`, o el evento no trae `solicitud_id`. |
| Credenciales expiran a mitad del deploy | Renovar la sesión del Learner Lab (credenciales rotan) y reintentar. |