# Synergia — Servidor

Backend de la plataforma de computación distribuida voluntaria **Synergia**. Cualquier persona puede publicar una tarea de cómputo apuntando a un repositorio GitHub con un `config.toml`, y los workers de la red se encargan de ejecutarla a cambio de créditos del sistema.

Este repositorio contiene la API REST, la API WebSocket, la infraestructura Docker y el sistema de monitorización.

---

## Cómo funciona

El flujo completo tiene tres actores:

**Publisher** → publica una tarea indicando la URL del repositorio y el hash del commit. El servidor lee el `config.toml`, divide los inputs en chunks y los mete en una cola RabbitMQ.

**Worker** → se conecta por WebSocket a la cola de una tarea, consume chunks, ejecuta el código localmente y sube los resultados.

**Servidor** → gestiona la base de datos, distribuye trabajo, verifica resultados, calcula pagos y expone métricas.

El mecanismo de pago distingue entre tareas **deterministas** (varios workers ejecutan el mismo chunk para verificación cruzada, se paga al canónico y a los confirmadores) y **no deterministas** (un solo resultado por chunk, pago directo).

---

## Estructura del repositorio

```
synergia-server/
├── src/
│   ├── api.py                        # API REST (FastAPI)
│   ├── ws_api.py                     # API WebSocket + cola RabbitMQ
│   ├── publisher.py                  # Productor RabbitMQ (chunking)
│   ├── schemas.py                    # Modelos Pydantic
│   ├── config.py                     # Carga de variables de entorno y config.json
│   ├── db/
│   │   ├── db.py                     # Capa de acceso a datos (Oracle)
│   │   └── db_oracle.py              # Queries Oracle
│   ├── utils/
│   │   ├── configuration_interpreter.py   # Parser de config.toml del repositorio de tarea
│   │   ├── github_util.py            # Utilidades de lectura de repositorios GitHub
│   │   ├── jwt_util.py               # Generación y verificación de JWT
│   │   └── smtp_util.py              # Envío de emails de verificación
│   └── tests/
│       ├── test_rest_api.py
│       ├── test_websocket_api.py
│       └── test_db.py
├── infra/
│   ├── docker/
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   └── .env.example
│   ├── database/
│   │   └── oracle-init/              # Scripts de inicialización Oracle
│   ├── monitoring/
│   │   ├── prometheus/prometheus.yml
│   │   └── dashboards/synergia_dashboard.json   # Dashboard Grafana
│   └── ngrok/ngrok.yml
└── docs/
    ├── CONFIG_REFERENCE.md           # Referencia completa de config.toml
    ├── entidad-relacion-synergia.png
    └── modelado-datos-synergia.png
```

---

## Stack

| Componente | Tecnología |
|---|---|
| API REST | FastAPI + Uvicorn |
| API WebSocket | FastAPI WebSockets + aio-pika |
| Cola de mensajes | RabbitMQ |
| Base de datos | Oracle Database Free |
| Autenticación | JWT (PyJWT) + Argon2 |
| OAuth | Google / GitHub |
| Métricas | Prometheus + Grafana |
| Túnel desarrollo | ngrok |
| Tests | pytest |

---

## Puesta en marcha

### Requisitos previos

- Docker y Docker Compose
- Python 3.11+ (solo si se quiere lanzar fuera de Docker)

### 1. Variables de entorno

```bash
cp infra/docker/.env.example infra/docker/.env
```

Rellenar `.env`:

```env
ORACLE_PASSWORD=
ORACLE_APP_PASSWORD=
RABBITMQ_PASSWORD=
JWT_SECRET_KEY=
GOOGLE_CLIENT_SECRET=
GITHUB_CLIENT_SECRET=
SMTP_PASSWD=
GRAFANA_ADMIN_PASSWORD=
NGROK_AUTHTOKEN=
```

### 2. Levantar la infraestructura

```bash
docker compose -f infra/docker/docker-compose.yml up -d
```

Esto levanta Oracle, RabbitMQ, la API REST (`:8000`), la API WebSocket (`:8001`), Prometheus (`:9090`) y Grafana (`:3000`). Además, si se ha configurado Ngrok, en el puerto `4040` se puede ver los endpoints de cada una de las APIs expuestas (API REST y API WebSocket.   

Oracle tarda un par de minutos en estar listo la primera vez. El healthcheck lo gestiona automáticamente.

### 3. Verificar que todo está en pie

```bash
curl http://localhost:8000/metrics
curl http://localhost:8001/metrics
```

### Ejecutar los tests

```bash
docker compose -f infra/docker/docker-compose.yml --profile test run test
```

---

## API REST — Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/account` | Registrar cuenta |
| `POST` | `/token` | Login con usuario y contraseña |
| `GET` | `/auth/google` | Iniciar OAuth Google |
| `GET` | `/auth/github` | Iniciar OAuth GitHub |
| `POST` | `/task` | Publicar una tarea nueva |
| `GET` | `/task` | Listar tareas (propias o suscritas) |
| `GET` | `/task/{id}` | Detalle de una tarea |
| `PATCH` | `/task/{id}/status` | Cambiar estado (`ACTIVE`, `PAUSED`, `CANCELLED`, `COMPLETED`) |
| `POST` | `/task/{id}/input` | Añadir inputs a tareas dinámicas |
| `GET` | `/task/{id}/progress` | Progreso de procesamiento |
| `GET` | `/task/{id}/output` | Consultar o descargar resultados |
| `POST` | `/task/{id}/process` | Registrar proceso (worker) |
| `POST` | `/task/{id}/process/{pid}/execution/{eid}/result` | Subir resultado de una ejecución |
| `GET` | `/task/{id}/confirm` | Obtener proceso pendiente de verificación |
| `GET` | `/metrics` | Métricas Prometheus |

La autenticación se hace enviando el JWT en el header `token`.

## API WebSocket

```
ws://host:8001/ws/task/{task_id}?token={jwt}&n_consumes={n}
```

El worker envía mensajes JSON para pedir trabajo:

```json
{ "action": "next", "n": 4 }
```

El servidor responde con los chunks asignados o con mensajes de estado:

```json
{ "status": "empty" }         // cola vacía, no hay más trabajo
{ "status": "paused" }        // tarea pausada
{ "status": "completed" }     // tarea terminada
{ "status": "verification_required" }   // el worker debe verificar antes de continuar
```

---

## Configuración de tareas (`config.toml`)

Cada tarea apunta a un repositorio GitHub que debe incluir un `config.toml`. El servidor lo lee al publicar la tarea para determinar el número de inputs, el tipo de chunking y si la tarea es determinista o no.

Ejemplo mínimo:

```toml
[task]
deterministic = true

[inputs]
type = "range_continuous"

[inputs.range_continuous]
start = 0
end   = 999999

[runner]
command   = "python main.py"
arg_start = "--start"
arg_end   = "--end"

[outputs]
dir              = "outputs"
filename_pattern = "result_{start}_{end}.txt"
```

Tipos de input disponibles: `directory`, `file_multi`, `file_single`, `range_continuous`, `range_discrete`, `dynamic`.

Referencia completa en [`docs/CONFIG_REFERENCE.md`](docs/CONFIG_REFERENCE.md).

---

## Tareas de ejemplo

Repositorios que implementan la interfaz de Synergia y sirven de referencia para crear nuevas tareas:

| Tarea | Descripción |
|---|---|
| [testRepositoryForParallel](https://github.com/yagomilenio/testRepositoryForParallel) | Repositorio de prueba básico para validar la integración con la plataforma |
| [foldingathomesynergia](https://github.com/yagomilenio/foldingathomesynergia) | Contribución a Folding@home usando los workers de la red como nodos de cómputo |
| [qwen2-vl-7b-parallel-test](https://github.com/yagomilenio/qwen2-vl-7b-parallel-test) | Inferencia distribuida del modelo de visión Qwen2-VL 7B sobre un conjunto de imágenes |
| [ollama-llm-task](https://github.com/yagomilenio/ollama-llm-task) | Ejecución paralela de prompts contra un LLM local vía Ollama |
| [blender-render-task](https://github.com/yagomilenio/blender-render-task) | Renderizado de escenas Blender distribuido por frames entre los workers |
| [yescrypt_task_cracker](https://github.com/yagomilenio/yescrypt_task_cracker) | Ataque de diccionario distribuido sobre hashes yescrypt |

---

## Monitorización

Grafana está disponible en `http://localhost:3000`. El dashboard incluido en `infra/monitoring/dashboards/synergia_dashboard.json` muestra workers activos por tarea, executions completadas y fallidas, créditos pagados, latencia de endpoints y estado de las tareas en tiempo real.

Las métricas que expone el servidor son:

- `p2pcn_active_workers` — workers conectados por tarea
- `p2pcn_executions_created_total` — executions creadas
- `p2pcn_executions_completed_total` — executions completadas (éxito/fallo)
- `p2pcn_payments_total_amount` — créditos pagados (canónico / confirmación)
- `p2pcn_upload_size_bytes` — tamaño de ficheros subidos
- `p2pcn_request_duration_seconds` — latencia por endpoint
- `p2pcn_task_status` — estado numérico de cada tarea

---

## Modelo de datos

El esquema entidad-relación está en `docs/entidad-relacion-synergia.png`. Los scripts de inicialización de Oracle están en `infra/database/oracle-init/`.

---

## Licencia

[GPL](LICENSE)
