# Synergia Server

Backend de computación distribuida voluntaria: un publisher registra una tarea (repo GitHub con `config.toml`), los workers consumen chunks por WebSocket, ejecutan y suben resultados, y se pagan créditos entre cuentas.
Stack: Python 3.13, FastAPI/Uvicorn (2 apps), RabbitMQ (pika + aio-pika), Oracle Free (oracledb, SQL a mano), JWT + Argon2, Prometheus/Grafana.
Todo el código está en `src/` y se ejecuta con **cwd = `src/`** (`config.json` se lee relativo al cwd).
Mapa detallado por archivo: `CODEBASE_MAP.md`. Si `docs/` contradice al código, manda el código.

## Estructura
- `src/api.py` — API REST (:8000): cuentas, auth, tareas, procesos, resultados, pagos, `/metrics`.
- `src/ws_api.py` — API WebSocket (:8001): reparte chunks desde RabbitMQ a workers.
- `src/publisher.py` — `Producer`: calcula chunks y publica en colas `task_<id>`.
- `src/schemas.py` — DTOs Pydantic. `src/config.py` — carga `config.json` + secretos de env.
- `src/db/` — `db.py` elige backend; `db_oracle.py` todas las queries; `__init__.py` re-exporta.
- `src/utils/` — `configuration_interpreter.py` (parser de `config.toml` de la tarea), `github_util.py`, `jwt_util.py`, `smtp_util.py`.
- `src/tests/` — pytest (mocks salvo `test_db.py`).
- `infra/` — `docker/` (compose, Dockerfile), `database/oracle-init/` (esquema SQL), `monitoring/` (Prometheus, Grafana), `ngrok/`.
- `docs/` — docs parciales/desactualizadas + `CONFIG_REFERENCE.md` (formato de `config.toml`, útil y vigente).

## Comandos (no hay Makefile, lint, build ni CI)
```
cp src/config.json.example src/config.json     # ya existe uno versionado
cp infra/docker/.env.example infra/docker/.env # variables para compose
cp src/.env.example src/.env                   # lo lee python-dotenv desde src/ (TODO: confirmar, ver Delicados)
docker compose -f infra/docker/docker-compose.yml up -d
docker compose -f infra/docker/docker-compose.yml --profile test run test   # pytest tests/ -v --tb=short
cd src && uvicorn api:app --port 8000 ; uvicorn ws_api:app --port 8001      # sin Docker (requiere Oracle y RabbitMQ)
```
- Puertos: REST 8000, WS 8001, Oracle 1521, RabbitMQ 5672/15672/15692, Prometheus 9090, Grafana 3000, ngrok 4040.
- Oracle tarda ~2 min la primera vez. Dependencias: `src/requirements.txt` (runtime) y `src/tests/requirements` (tests, con coverage/pytest-asyncio).
- Tests: `test_db.py` es de integración y necesita Oracle real con el esquema cargado y env `ORACLE_USER_TEST`, `ORACLE_PASSWD_TEST`, `ORACLE_DSN_TEST` (cada test hace rollback). El resto usa mocks y corre sin infraestructura, desde `src/`: `pytest tests/test_rest_api.py -v`.
- Despliegue: solo `docker compose` (ver `docs/05_guia_de_despliegue.md`).

## Convenciones
- **Capas**: endpoints (`api.py`) → funciones de `db` con cursor explícito. Sin ORM. Para añadir una query: implementarla en `db_oracle.py` **y** añadirla a los imports y a `__all__` de `db/__init__.py` (`api.py`/`ws_api.py` hacen `from db import *`).
- **Conexión**: `conn, cursor = get_db()` … `try/finally close_db(conn, cursor)`. Commit explícito; `rollback()` en `except`. Pool oracledb (2–10) creado **al importar** `db_oracle`.
- **Queries**: `_exec()` para SELECT (devuelve dicts con claves en minúsculas); `cursor.execute` para DML. `lock=True` añade `FOR UPDATE`. Parámetros posicionales `:1` o con nombre.
- **Auth**: JWT HS256, 24 h, en header **`token`** (no `Authorization: Bearer`). `verify_token` devuelve el `sub` (id de cuenta). Preferir `Depends(verify_token)`; `add_inputs` y `get_my_tasks` leen el header a mano. WS: `?token=`, cierra con 1008. Escritura y `/confirm` exigen token; casi todos los GET son públicos.
- **Config**: `config.py` lee `config.json` y los secretos solo de env (`RABBITMQ_PASSWORD`, `ORACLE_APP_PASSWORD`, `JWT_SECRET_KEY`, `GOOGLE/GITHUB_CLIENT_SECRET`, `SMTP_PASSWD`). Las claves `password` de `config.json` se ignoran. `DB_BACKEND` solo admite `oracle` (MariaDB está sin implementar).
- **Errores**: `HTTPException(status, "mensaje en español")`; 409 para conflictos de proceso/ejecución. Algunos casos devuelven 200 con `{"error"|"status": ...}` (p. ej. tarea no activa).
- **Colas**: una por tarea, `task_<id>`, durables. Mensaje = `{"index","count"}` (inputs por rango) o `{"index","value"}` (tarea dinámica, ≤ 65535 B).
- **Métricas**: prefijo `p2pcn_`. Logging con `print()`; no hay logger.
- **Estilo**: comentarios y mensajes en español. Commits `[ADD] - …` / `[MOD] - …`. Ramas: `develop` + `feature/*`.
- **Emails permitidos** al registrar: `schemas.ALLOWED_DOMAINS`.

## Modelo de dominio (no deducible leyendo un solo archivo)
- Task → Process (rango inclusivo `[start,end]` de inputs) → Execution (una por worker y proceso). `process.id` y `execution.id` son por padre (triggers `MAX+1`, PK compuestas).
- **Determinista**: varios workers repiten el chunk; canónico = hash de resultado más repetido (desempate: menor `execution.id`). El publisher paga al canónico; cada confirmador cobra de `SYSTEM_FEES` `canonical_amount / n_confirmaciones`. Si cambia el canónico, se devuelve el pago al anterior (transfer inversa) y se paga al nuevo. **No determinista**: un resultado por chunk, pago directo.
- **Verificación**: `task_subscription.chunks_since_last_verification` sube con cada chunk nuevo; al llegar a `process_until_confirmation` el WS responde `verification_required` hasta que el worker confirma (`GET /task/{id}/confirm`, acción WS `confirmation_done`).
- **Créditos**: tabla `transfer` + `account.balance` desnormalizado (lo actualiza `insert_transfer`). Alta de cuenta: `SYSTEM_MINT` → cuenta. Crear tarea: `task_cost` → `SYSTEM_FEES`. Coste de una ejecución: constantes hardcodeadas en `create_result`.
- Si el saldo del publisher < coste estimado del siguiente chunk (media + desviación), se **pausan todas sus tareas**. Una tarea no dinámica pasa a `COMPLETED` sola; la dinámica se cierra a mano (`PATCH /task/{id}/status`).
- **Integridad del repo**: el worker envía `repo_hash`/`repo_commit`; si no coinciden con el snapshot de la tarea → 409. El publisher lo actualiza con `PATCH /task/{id}`.
- **Ficheros**: `UPLOAD_DIR/<sha256[:2]>/<sha256>`, deduplicados por hash.
- **WS y RabbitMQ**: el ack es diferido. Un mensaje solo se ack si la execution del worker está en `SUCCESS` (al pedir `next` o al desconectar); si no, `nack requeue`. Al desconectar: executions pendientes → `CANCELLED` y procesos huérfanos se borran.

## Dónde NO tocar
- `infra/database/oracle-init/synergia_oracle.sql` solo corre en la primera inicialización; no hay migraciones, un cambio de esquema no llega a una BD existente.
- Nunca `UPDATE`/`DELETE` sobre `transfer`. No editar `docs/*.png|svg|drawio|html` ni `LICENSE`.
- No mostrar ni copiar `infra/ngrok/ngrok.yml` (token real) ni `.env`.

## Puntos delicados
- **`transfer` es BLOCKCHAIN TABLE**: inmutable (`NO DELETE LOCKED`), `amount > 0`, FK a `process` sin cascade. No se pueden borrar procesos con pagos ni limpiar datos de test; no admite `FOR UPDATE`. Corregir un pago = transfer inversa.
- **Saldo**: `get_balance_from_transfers` hace dos LEFT JOIN (in × out) y puede multiplicar sumas; valida el saldo en `POST /task`. TODO: confirmar.
- **Singleton de config de tarea**: `configuration_interpreter._config` es global; `create_task` es síncrono (threadpool) → carrera entre peticiones concurrentes.
- **Parser de `config.toml`**: `file_multi` llama a `GithubUtil.list_glob`, que no existe; `TOMLDecodeError` sin importar; `directory`/`file_multi` fijan `blob/main`. GitHub se consulta sin token (límite de la API).
- **Token de verificación de email** (`purpose=email_verification`) comparte secreto y `sub` con el de sesión; `verify_token` no comprueba `purpose`. TODO: confirmar intención.
- **OAuth**: créditos iniciales hardcodeados (`500000000000`) en vez de `config.INITIAL_CREDITS`.
- **Subidas**: `save_upload_file` escribe siempre en `UPLOAD_DIR/tmp` (subidas concurrentes se pisan) y valida `max_upload_bytes` después de escribir.
- **IDs por `MAX+1`**: `insert_execution(output=True)` relee `MAX(id)`; carrera con workers simultáneos. `get_execution_id` filtra por `status='SUCCESS'` por defecto (pasar `'PENDING'` explícito).
- **Off-by-one**: `get_process_by_index` usa `input_end_index > :indice` (fin exclusivo) mientras todo lo demás es inclusivo. TODO: confirmar.
- **Métrica `p2pcn_task_status`**: `create_result` la pone a `SUCCESS` (4) tras cada resultado aunque la tarea siga `ACTIVE`; ese estado no existe en el CHECK de `task`. TODO: confirmar.
- **Seed**: tarea 1 (Folding@home, publisher = `SYSTEM_MINT`); el trigger `trg_auto_subscribe` suscribe cada cuenta nueva a `task_id = 1`. Si faltan `SYSTEM_MINT`/`SYSTEM_FEES`, `api.py` falla al arrancar.
- **Docker**: `rest_api`/`ws_api` no tienen `env_file`; sus secretos llegan por `src/.env` (README solo menciona `infra/docker/.env`). El servicio `test` usa `build: .` con contexto `infra/docker` pero el Dockerfile copia `src/requirements.txt`. `src/reset_compose.sh` usa volúmenes de MariaDB obsoletos. TODO: confirmar los tres.
- **Tests**: `test_rest_api.py`/`test_websocket_api.py` reemplazan `sys.modules` (oracledb, config…) y re-importan `api`/`ws_api`; si cambias imports ahí, revisa el fixture `client`. `conftest.py` vacía el registro Prometheus (métricas duplicadas al reimportar). `p2pcn_active_workers` se define en `api.py` y en `ws_api.py`.
- **Secretos versionados**: `infra/ngrok/ngrok.yml` contiene un authtoken real (rotarlo); `src/config.json` está en git (sin contraseñas, con client IDs y usuario SMTP).
- **Docs desactualizados**: `docs/03` cita `Authorization: Bearer` y rutas `/tasks`, `/payments` que no existen; `docs/02` habla de memoria del nodo, pero el chunking se calcula por tamaño del mensaje AMQP (≤ 512 KB).
