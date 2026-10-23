# CODEBASE_MAP

Una línea por archivo/módulo relevante. Convenciones y trampas: ver `CLAUDE.md`. Rutas relativas a la raíz del repo; todo el código Python está en `src/` (cwd de ejecución).

## src/ — aplicaciones

- `api.py` — App FastAPI REST (:8000). Define métricas Prometheus (`p2pcn_*`, `STATUS_MAP`), `oauth_states` (TTLCache 300 s anti-CSRF), `SYSTEM_IDS` (ids de `SYSTEM_MINT`/`SYSTEM_FEES`, cargados en `startup_event` vía `load_system_account_ids`) y `metrics_middleware` (latencia por endpoint).
- `ws_api.py` — App FastAPI WebSocket (:8001). `task_websocket` (`/ws/task/{id}?n_consumes=&token=`): bucle `next`/`confirmation_done`, ack/nack diferido, bloqueo por verificación y limpieza al desconectar. `consume_n_messages` (consume hasta n mensajes o 1 s), `safe_send_json`. Monta `/metrics`.
- `publisher.py` — `Producer(address, port, user, passwd, queue)`: `publish_chunks(task_id, n_inputs)`, `publish_items(task_id, items)` (tareas dinámicas, ≤ 65535 B por item), `generate_chunks` (tamaño de chunk para que la cola quede ≤ 512 KB), `delete_rabbit_queue`. Helpers `memoria_total`, `suma_digitos_secuencia`. Conexión bloqueante pika.
- `schemas.py` — Pydantic: `UserCreateDTO` (valida username, passwd ≥ 8 y dominio en `ALLOWED_DOMAINS`), `UserDTO` (login por username o email), `TaskCreate`, `TaskInputDTO`, `TaskUpdateDTO`.
- `config.py` — Carga `config.json` (cwd) + `.env`; expone constantes: `TASK_COST`, `INITIAL_CREDITS`, `PROCESS_UNTIL_CONFIRMATION`, `SYSTEM_ACCOUNTS`, `RABBITMQ_*`, `ORACLE_*`, `UPLOAD_DIR`, `MAX_UPLOAD_BYTES`, `OAUTH`/`SMTP_*`, `JWT_SECRET_KEY`, `DB_BACKEND`. Lanza `RuntimeError` si falta `config.json`.

## src/api.py — endpoints (handler → auth)

- `POST /account` `create_account` (público): alta local, mint de créditos, email de verificación. `GET /account/{username}` `get_account` (público): saldo/transfers sin id ni email.
- `GET /verify-email` `verify_email`. `POST /token` `get_token` (login username/email + Argon2; exige email verificado) → `{token, username, email}`.
- `GET /auth/google|github` y `/auth/{provider}/callback`: OAuth; `_oauth_login_or_register(email, username, provider_id)` (2 = Google, 3 = GitHub).
- `POST /task` `create_task` (token): lee `config.toml` del repo, cobra `TASK_COST`, inserta tarea/recursos/requisitos y publica chunks.
- `POST /task/{id}/input` `add_inputs` (token, header manual): añade items a tarea dinámica (solo publisher, tarea ACTIVE).
- `GET /task` `get_my_tasks` (token opcional; `?subscribed=`): sin token lista todas. `GET /task/{id}` `get_task_by_id`.
- `PATCH /task/{id}` `sync_task` (token): actualiza hash/commit del repo (solo publisher). `PATCH /task/{id}/status?status=` `change_task_status` (token): ACTIVE/PAUSED/CANCELLED/COMPLETED; borra la cola al cancelar/completar.
- `DELETE /task/{id}/subscription` `unsubscribe_task` (token).
- `POST /task/{id}/process` `create_process` (token; query `repo_hash`, `repo_commit`, `index`, `count`, `value`): crea proceso + execution, comprueba solape y hash del repo.
- `GET /task/{id}/process` `get_processes` (`?index=`), `GET /task/{id}/process/{pid}` `get_process_request`, `GET .../executions` `get_process_executions`: consulta pública.
- `POST /task/{id}/process/{pid}/execution` `create_execution` (token): execution extra (confirmación). Devuelve 200 con `{"error"}` si la tarea no está activa.
- `POST .../execution/{eid}/result` `create_result` (token, multipart `file`, `cpu_cycles`, `ram_avg`, `vram_avg`, `tdp_w`): guarda fichero, calcula coste, paga, actualiza media/desviación de coste, pausa por saldo y completa la tarea.
- `GET /task/{id}/progress` `get_progress`; `GET /task/{id}/confirm` `get_confirm` (token): proceso a verificar; `GET /task/{id}/output` `get_outputs` (`?download=`, `process_id`, `canonical_only`; zip o listado con preview ≤ 200 chars de .txt/.json/.log).
- Internas: `save_upload_file` (sha256 incremental, sharding por 2 hex), `handle_non_deterministic_payment`, `handle_deterministic_payment` (canónico, confirmaciones, devoluciones, reputación).

## src/db/

- `db.py` — Selector de backend por `config.DB_BACKEND` (`from db.db_oracle import *`; MariaDB comentado).
- `__init__.py` — Re-exporta (`from .db import …` + `__all__`) todas las funciones de `db_oracle`. Añadir aquí cada función nueva.
- `db_oracle.py` — Pool oracledb y SQL. Familias:
  - Conexión: `get_db`, `close_db`, `begin_transaction`, `_exec`, `_rowfactory`.
  - Cuentas/auth: `insert_account`, `insert_auth_local_credential`, `insert_auth_provider_account`, `get_account_by_id|username|email`, `get_credentials_by_username|email`, `set_email_verified`.
  - Créditos: `insert_transfer` (inserta transfer + actualiza ambos balances), `update_balance`, `get_balance`, `get_balance_from_transfers`, `get_transfer_history`.
  - Tareas: `insert_task`, `get_tasks`, `get_tasks_by_account`, `get_subscribed_tasks`, `get_tasks_status`, `get_task_fields(cursor, id, [campos])`, `get_task_for_status_change`, `update_task_status`, `pause_tasks_by_account`, `update_task_hash`, `increment_total_items`, `set_task_metrics`, `get_task_progress`, `insert_task_resource`, `insert_task_requirement`.
  - Suscripciones/verificación: `insert_task_subscription`, `delete_task_subscription`, `get_last_task_confirmation`, `inc_confirmation_counter`, `reset_confirmation_counter`, `get_process_to_confirmate`, `get_process_pending_confirmation` (ranking por reputación del canónico, coste anómalo y pocos confirmadores).
  - Procesos: `insert_process`, `get_process`, `get_process_by_index`, `get_process_id_by_start_index`, `get_all_processes_by_task`, `get_last_process_by_task`, `check_process_overlap`, `delete_process_by_task`, `get_processes_and_execution_without_completed_executions`.
  - Executions: `insert_execution`, `get_execution_id`, `get_execution_by_id`, `get_executions_by_process`, `update_complete_execution` (devuelve duración en s), `cancel_incomplete_executions`, `is_process_successfully_terminated`.
  - Canónico/pagos/reputación: `update_canonical_process`, `recalculate_canonical_process`, `get_canonical_execution_id`, `get_canonical_id_by_result_id`, `get_canonical_payment`, `get_canonical_confirmation_count`, `get_previous_canonical_payment`, `recalculate_canonical_confirmations`, `get_account_ids_with_successful_execution`, `recalculate_reputation`.
  - Ficheros: `get_file_id`, `insert_file`, `get_task_output_files`.

## src/utils/

- `configuration_interpreter.py` — Parser del `config.toml` del repo de la tarea. `load_config(github_url, path)` rellena el singleton y `cfg()` lo devuelve (`Config`: `task`, `requirements`, `download`, `inputs`, `outputs`, `hash`, `network`). `InputsConfig.active_items()/total()` para `directory`, `file_multi`, `file_single`, `range_continuous` (devuelve `range`, no lista), `range_discrete`, `dynamic`. `OutputsConfig.resolve_filename`.
- `github_util.py` — `GithubUtil(url)`: `list_dir`, `read_file`, `get_commit`, `get_repo_link`, `get_branch`; API de contenidos de GitHub sin autenticar; acepta `…/tree/<rama>`.
- `jwt_util.py` — `verify_token(token: Header)` → `sub`; constantes `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`.
- `smtp_util.py` — `send_verification_email(to, username, token, base_url)` por SMTP + STARTTLS (excluido de cobertura).

## src/tests/

- `conftest.py` — fixture autouse que vacía el registro de Prometheus.
- `test_rest_api.py` (clases por endpoint; mockea `oracledb`/`config` en `sys.modules`), `test_websocket_api.py` (WS con TestClient y mocks de aio-pika), `test_configuration.py` (parser con `GithubUtil` stub), `test_github.py`, `test_publisher.py` (pika mockeado).
- `test_db.py` — Integración con Oracle real (env `ORACLE_*_TEST`); rollback por test; crea cuentas/tareas/procesos con fixtures.

## infra/

- `docker/docker-compose.yml` — Servicios: `oracle`, `rabbitmq`, `rest_api`, `ws_api` (uvicorn `--reload`, montan `src/`), `prometheus`, `grafana`, `ngrok`, `test` (profile `test`). `docker/Dockerfile` — `python:3.13-slim` + `src/requirements.txt`.
- `database/oracle-init/synergia_oracle.sql` — Esquema completo: `account`, `auth_*`, `task`, `task_subscription` (trigger de auto-suscripción a task 1), `resource_*`, `task_requirement`, `result_file`, `process`, `execution`, `transfer` (blockchain table); seeds `SYSTEM_MINT`, `SYSTEM_FEES`, proveedores (1 LOCAL, 2 GOOGLE, 3 GITHUB), recursos (1 CPU, 2 GPU, 3 RAM) y tarea 1. `01_init.sh` crea el usuario `synergia` y lo ejecuta solo si no hay tablas.
- `monitoring/prometheus/prometheus.yml` — Scrape de `rest_api:8000`, `ws_api:8001` y RabbitMQ (`:15692`). `monitoring/dashboards/synergia_dashboard.json` — Dashboard Grafana.
- `ngrok/ngrok.yml` — Túneles `rest_api` y `ws_api` (contiene token; ver `CLAUDE.md`).

## docs/

- `CONFIG_REFERENCE.md` — Referencia vigente de `config.toml` (inputs, runner, outputs). `01`–`05_*.md` — resúmenes breves, parcialmente desactualizados. `entidad-relacion-synergia.*` — diagrama ER.
