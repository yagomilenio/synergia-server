from fastapi import FastAPI, HTTPException, Query, Depends, Header, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, Response
from typing import Optional, List
import  oracledb
import json
import time
import uuid
import jwt
import hashlib
import os
from argon2 import PasswordHasher
from datetime import datetime, timedelta
from publisher import Producer
from db import *
from utils.configuration_interpreter import load_config, cfg
from utils.jwt_util import verify_token
from utils import jwt_util
import config
import zipfile
import io
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app, generate_latest, CONTENT_TYPE_LATEST
from starlette.routing import Mount
import re
import math
import httpx
import secrets
from urllib.parse import urlencode
from fastapi.middleware.cors import CORSMiddleware
from utils.smtp_util import send_verification_email
import schemas
from cachetools import TTLCache

# estado temporal en memoria para evitar CSRF, si fuera en habria que usar Redis
oauth_states = TTLCache(maxsize=1000, ttl=300)



# --- Definición de métricas ---

# Workers activos (se gestiona desde el websocket)
active_workers = Gauge(
    "p2pcn_active_workers",
    "Número de workers conectados por tarea",
    ["task_id"]
)

# Executions
executions_created = Counter(
    "p2pcn_executions_created_total",
    "Total de executions creadas",
    ["task_id"]
)
executions_completed = Counter(
    "p2pcn_executions_completed_total",
    "Total de executions completadas",
    ["task_id", "result"]   # result: success / failed
)

# Pagos
payments_amount = Counter(
    "p2pcn_payments_total_amount",
    "Créditos totales pagados a workers",
    ["type"]    # type: canonical / confirmation
)

# Tamaño de archivos subidos
upload_size_bytes = Histogram(
    "p2pcn_upload_size_bytes",
    "Tamaño de archivos subidos por workers",
    buckets=[1024, 10240, 102400, 1048576, 10485760]  # 1KB a 10MB
)

# Latencia de endpoints
request_latency = Histogram(
    "p2pcn_request_duration_seconds",
    "Latencia de endpoints REST",
    ["method", "endpoint"]
)

STATUS_MAP = {
    "ACTIVE": 1,
    "PAUSED": 2,
    "CANCELLED": 3,
    "SUCCESS": 4,
    "COMPLETED": 5
}

task_status = Gauge(
    "p2pcn_task_status",
    "Estado de cada tarea",
    ["task_id"]
)

process_status = Gauge(
    "p2pcn_process_status", 
    "Estado de cada proceso",
    ["task_id", "process_id", "status"]
)





# ----------- cargar todos los ids de los  accounts del sistema -------------
def load_system_account_ids():

    conn, cursor = get_db()
    try:

        system_ids = {}
        for key, username in config.SYSTEM_ACCOUNTS.items():
            result = get_account_by_username(cursor, username)
            if not result:
                raise Exception(f"No existe la cuenta sistema: {username}")

            system_ids[key] = result["id"]
    finally:
        close_db(conn, cursor)

    return system_ids




# ---------------- APP ----------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:8080"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_IDS = {}

@app.on_event("startup")
def startup_event():
    global SYSTEM_IDS
    SYSTEM_IDS = load_system_account_ids()

    conn, cursor = get_db()
    try:
        tasks = get_tasks_status(cursor)

    finally:
        close_db(conn, cursor)
    
    for task in tasks:
        task_status.labels(task_id=str(task["id"])).set(STATUS_MAP[task["status"]])


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    request_latency.labels(
        method=request.method,
        endpoint=request.url.path
    ).observe(duration)
    return response

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ---------------- Crear usuario ----------------
@app.post("/account")
def create_account(user_data: schemas.UserCreateDTO):
    conn, cursor = get_db()

    try:

        username = user_data.username
        email = user_data.email
        passwd = user_data.passwd

        account_id = insert_account(cursor, username, email)
        insert_auth_local_credential(cursor, account_id, PasswordHasher().hash(passwd))


        insert_transfer(cursor, SYSTEM_IDS["mint"], account_id, config.INITIAL_CREDITS)


        conn.commit()
    except oracledb.IntegrityError:
        raise HTTPException(400, detail="Usuario o email ya existe")

    finally:
        close_db(conn, cursor)


    token = jwt.encode(
        {"sub": str(account_id), "purpose": "email_verification", "exp": datetime.utcnow() + timedelta(hours=24)},
        jwt_util.SECRET_KEY,
        algorithm=jwt_util.ALGORITHM
    )
    send_verification_email(email, username, token, config.SMTP_REDIRECT)

    return {"status": "Usuario creado correctamente"}

#obtener informacion sobre  mi cuenta
@app.get("/account/{username}")
def get_account(username: str):
    conn, cursor = get_db()
    try:

        account = get_account_by_username(cursor, username)

        if not account:
            raise HTTPException(404, "Cuenta no encontrada")


        transfers = get_transfer_history(cursor, account["id"])

        account.pop("id")   #eliminamos el id  para que los clientes no obtengan ids internos
        account.pop("email")

        return {
            "account": account,
            "transfers": transfers
        }
    finally:
        close_db(conn, cursor)


# ---------------- Verificar email -------------------
@app.get("/verify-email")
def verify_email(token: str = Query(...)):
    try:
        payload = jwt.decode(token, jwt_util.SECRET_KEY, algorithms=[jwt_util.ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(400, "El enlace de verificación ha expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(400, "Token de verificación inválido")

    if payload.get("purpose") != "email_verification":
        raise HTTPException(400, "Token inválido")

    account_id = payload["sub"]

    conn, cursor = get_db()
    try:
        set_email_verified(cursor, account_id)
        conn.commit()
    finally:
        close_db(conn, cursor)

    return {"status": "Email verificado correctamente"}

# ---------------- Login / token (JWT) ----------------
@app.post("/token")
def get_token(user_data: schemas.UserDTO):
    conn, cursor = get_db()

    username = user_data.username
    email = user_data.email
    passwd = user_data.passwd

    try:
        if username:
            credential = get_credentials_by_username(cursor, username)
        elif email:
            credential = get_credentials_by_email(cursor, email)
        else:
            close_db(conn, cursor)
            raise HTTPException(400, "Debe indicar username o email")
    finally:

        close_db(conn, cursor)


    if not credential:
        raise HTTPException(401, "Login fallido")

    try:
        PasswordHasher().verify(credential["password_hash"], passwd)

    except:
        raise HTTPException(401, "Login fallido")

    verified = credential["verified"]
    if not verified:
        raise HTTPException(401, "Debes verificar el email antes de continuar.")

    if not username:
        conn, cursor = get_db()
        username = get_account_by_email(cursor, email).get("username")
        close_db(conn, cursor)

    if not email:
        conn, cursor = get_db()
        email = get_account_by_username(cursor, username).get("email")
        close_db(conn, cursor)

    # Generar JWT
    data = {"sub": str(credential["id"])}
    expire = datetime.utcnow() + timedelta(minutes=jwt_util.ACCESS_TOKEN_EXPIRE_MINUTES)
    data.update({"exp": expire})
    token = jwt.encode(data, jwt_util.SECRET_KEY, algorithm=jwt_util.ALGORITHM)
    return {"token": token, "username": username, "email": email}






# ---------------- Google OAuth ----------------

@app.get("/auth/google")
def auth_google():
    state = secrets.token_urlsafe(16)
    oauth_states[state] = True

    params = urlencode({
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI_GOOGLE,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    })
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"}


@app.get("/auth/google/callback")
async def auth_google_callback(code: str, state: str):
    if state not in oauth_states:
        raise HTTPException(400, "Estado OAuth inválido")
    del oauth_states[state]

    async with httpx.AsyncClient() as client:
        # intercambiar code por access_token
        token_resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": config.REDIRECT_URI_GOOGLE,
            "grant_type": "authorization_code",
        })
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        # obtener info del usuario
        user_resp = await client.get("https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_resp.raise_for_status()
        user_info = user_resp.json()

    email    = user_info["email"]
    username = user_info.get("name", email.split("@")[0]).replace(" ", "_")

    return _oauth_login_or_register(email, username, provider_id=2)  # 2 = GOOGLE


# ---------------- GitHub OAuth ----------------

@app.get("/auth/github")
def auth_github():
    state = secrets.token_urlsafe(16)
    oauth_states[state] = True

    params = urlencode({
        "client_id": config.GITHUB_CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI_GITHUB,
        "scope": "user:email",
        "state": state,
    })
    return {"url": f"https://github.com/login/oauth/authorize?{params}"}


@app.get("/auth/github/callback")
async def auth_github_callback(code: str, state: str):
    if state not in oauth_states:
        raise HTTPException(400, "Estado OAuth inválido")
    del oauth_states[state]

    async with httpx.AsyncClient() as client:
        # intercambiar code por access_token
        token_resp = await client.post("https://github.com/login/oauth/access_token",
            data={
                "client_id": config.GITHUB_CLIENT_ID,
                "client_secret": config.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": config.REDIRECT_URI_GITHUB,
            },
            headers={"Accept": "application/json"}
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        # obtener info del usuario
        user_resp = await client.get("https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_resp.raise_for_status()
        user_info = user_resp.json()

        # GitHub puede no devolver email publico, hay que pedirlo aparte
        email_resp = await client.get("https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        emails = email_resp.json()
        email = next((e["email"] for e in emails if e["primary"]), None)

    if not email:
        raise HTTPException(400, "No se pudo obtener el email de GitHub")

    username = user_info.get("login", email.split("@")[0])

    return _oauth_login_or_register(email, username, provider_id=3)  # 3 = GITHUB


# ---------------- Lógica común OAuth ----------------

def _oauth_login_or_register(email: str, username: str, provider_id: int):
    conn, cursor = get_db()
    try:
        account = get_account_by_email(cursor, email)

        if not account:
            # usuario nuevo: crear cuenta y darle créditos iniciales
            # si el username ya existe le añadimos un sufijo aleatorio
            base = username
            while True:
                try:
                    account_id = insert_account(cursor, username, email)
                    break
                except oracledb.IntegrityError:
                    username = f"{base}_{secrets.token_hex(3)}"

            insert_auth_provider_account(cursor, account_id, provider_id)
            insert_transfer(cursor, SYSTEM_IDS["mint"], account_id, 500000000000)
            conn.commit()
        else:
            account_id = account["id"]
            insert_auth_provider_account(cursor, account_id, provider_id)
            conn.commit()

        # generar JWT igual que en /token
        expire = datetime.utcnow() + timedelta(minutes=jwt_util.ACCESS_TOKEN_EXPIRE_MINUTES)
        token = jwt.encode(
            {"sub": str(account_id), "exp": expire},
            jwt_util.SECRET_KEY,
            algorithm=jwt_util.ALGORITHM
        )

        return {"token": token, "username": username, "email": email}

    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cursor)

# ---------------- Crear tarea ----------------
@app.post("/task")
def create_task(
    task_data: schemas.TaskCreate,
    user_id: int = Depends(verify_token)
):
    
    name = task_data.name
    description = task_data.description
    github_url = task_data.github_url
    repo_hash = task_data.repo_hash
    repo_commit = task_data.repo_commit
    resources = task_data.resources
    requirements = task_data.requirements

    try:
        load_config(github_url) 

        deterministic = cfg().task.deterministic  # se guarda en bbdd ya que si se leyera del repo todo el tiempo cambiarian cosas como los inputs etc 

        if cfg().inputs is not None:
            dynamic = cfg().inputs.type == "dynamic"
            n_inputs  = cfg().inputs.total()  
        else: 
            dynamic = False
            n_inputs = 0


        

    except Exception:
        raise HTTPException(status_code=400, detail="La configuración del repositorio es erronea o está incompleta")

    



    conn, cursor = get_db()

    try:

        row = get_balance_from_transfers(cursor, user_id)
        if not row:
            raise HTTPException(401, "Usuario no existe")

        balance = row["balance"] or 0
        if balance < config.TASK_COST:
            raise HTTPException(403, "Créditos insuficientes")

        #crear tarea en bbdd
        task_id = insert_task(cursor, name, description, github_url, user_id, deterministic, dynamic, n_inputs, repo_hash, repo_commit)

        task_status.labels(task_id=task_id).set(STATUS_MAP['ACTIVE'])

        #realizar transferencia que simboliza conste
        
        insert_transfer(cursor, user_id, SYSTEM_IDS["fees"], config.TASK_COST, task_id)


        conn.commit()



        if resources:
            resource_map = {'cpu': 1, 'gpu': 2, 'ram': 3}
            for res in resources.split(','):
                res = res.strip().lower()
                if res in resource_map:
                    insert_task_resource(cursor, task_id, resource_map[res])

        if requirements:
            for item in requirements.split(','):
                key, _, val = item.partition('=')
                insert_task_requirement(cursor, task_id, key.strip(), float(val))

        conn.commit()

    finally:
        close_db(conn, cursor)

    #crear cola correspondiente a la tarea y mandar los chunks
    producer = Producer(config.RABBITMQ_HOST, config.RABBITMQ_PORT, config.RABBITMQ_USER, config.RABBITMQ_PASSWD, f"task_{task_id}")
    producer.publish_chunks(task_id, n_inputs)

    return {"task_id": task_id}



# --------------- Añadir inputs a tareas dinamicas -------------

@app.post("/task/{task_id}/input")
def add_inputs(task_id: str, items_data: schemas.TaskInputDTO, token: str = Header(...)):

    items = items_data.items

    try:
    
        user_id = verify_token(token)

        conn, cursor = get_db()

        task = get_task_fields(cursor, task_id, ["publisher", "status", "is_dynamic"])

        if not task:
            close_db(conn, cursor)
            raise HTTPException(status_code=404, detail="Tarea no encontrada")

        if not task["is_dynamic"]:
            close_db(conn, cursor)
            raise HTTPException(status_code=400, detail="La tarea no es dinámica")

        if int(task["publisher"]) != int(user_id):
            close_db(conn, cursor)
            raise HTTPException(status_code=403, detail="No eres el publisher de esta tarea")

        if task["status"] != "ACTIVE":
            close_db(conn, cursor)
            raise HTTPException(status_code=400, detail=f"La tarea no está activa, estado actual: {task['status']}")


        producer = Producer(config.RABBITMQ_HOST, config.RABBITMQ_PORT, config.RABBITMQ_USER, config.RABBITMQ_PASSWD, f"task_{task_id}")

        items_count = len(items)

        last_items_count = get_task_fields(cursor, task_id, ["total_items"])["total_items"]

        indexed_items = [
            {"index": last_items_count + i, "value": item} 
            for i, item in enumerate(items)
        ]

        

        producer.publish_items(task_id, indexed_items)
        increment_total_items(cursor, task_id, items_count)
        conn.commit()

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        close_db(conn, cursor)

    return {"added": items_count}



# ---------------- Consultar mis tareas ----------------
@app.get("/task")
def get_my_tasks(subscribed: bool = Query(False), token: str = Header(default=None)):
    conn, cursor = get_db()
    try:
        
        if token:
            try:
                payload = jwt.decode(token, jwt_util.SECRET_KEY, algorithms=[jwt_util.ALGORITHM])
                user_id = payload.get("sub")

            except jwt.InvalidTokenError:
                    raise HTTPException(status_code=401, detail="Token inválido")

            if subscribed:
                rows = get_subscribed_tasks(cursor, user_id)

            else:
                rows = get_tasks_by_account(cursor, user_id)

        else:
            rows = get_tasks(cursor)
    finally:

        close_db(conn, cursor)
    result = [
        {"task_id": r["id"], "name": r["name"], "description": r["description"], "github_url": r["github_url"], "status": r.get("status", "—")}
        for r in rows
    ]
    return {"result": result}

# -------------- Obtener tarea concreta ---------------


@app.get("/task/{task_id}")
def get_task_by_id(task_id: str):
    conn, cursor = get_db()
    try:
        task = get_task_fields(cursor, task_id, ["id", "name", "description", "github_url", "status", "publisher", "repo_snapshot_hash"])
        if not task:
            raise HTTPException(404, "Tarea no encontrada")
        return task
    finally:
        close_db(conn, cursor)



@app.patch("/task/{task_id}")
def sync_task(
    task_id: str,
    task_data: schemas.TaskUpdateDTO,
    user_id: int = Depends(verify_token)
):
    repo_hash = task_data.repo_hash
    repo_commit = task_data.repo_commit

    conn, cursor = get_db()
    try:
        task = get_task_fields(cursor, task_id, ["publisher"])
        if not task:
            raise HTTPException(404, "Tarea no encontrada")
        
        if int(task["publisher"]) != int(user_id):
            raise HTTPException(403, "Solo el publisher puede sincronizar la tarea")
        
        update_task_hash(cursor, task_id, repo_hash, repo_commit)
        conn.commit()
    finally:
        close_db(conn, cursor)
    
    return {"task_id": task_id, "repo_hash": repo_hash, "repo_commit": repo_commit}
    
# -------------- Cambiar estado de las tareas ---------------------

@app.patch("/task/{task_id}/status")
def change_task_status(status: str, task_id: str, user_id: str = Depends(verify_token)):
    VALID_STATUS = {'ACTIVE', 'PAUSED', 'CANCELLED', 'COMPLETED'}
    if status not in VALID_STATUS:
        raise HTTPException(400, f"Status inválido, debe ser uno de: {VALID_STATUS}")

    conn, cursor = get_db()
    try:
        task = get_task_for_status_change(cursor, task_id, user_id)


        if not task:
            raise HTTPException(404, "Tarea no encontrada o no autorizado")

        if task['status'] == 'CANCELLED':
            raise HTTPException(400, "Las tareas que han sido canceladas no pueden cambiar su estado")

        if status == 'COMPLETED' and not task['is_dynamic']:
            raise HTTPException(400, "Solo las tareas dinámicas pueden cerrarse manualmente")

        if status == 'ACTIVE':
            coste_estimado = task['avg_cost_per_item'] or 0
            if task['balance'] <= 0 or task['balance'] < coste_estimado:
                raise HTTPException(400, "Saldo insuficiente para reactivar la tarea")

        

        update_task_status(cursor, task_id, status)
        task_status.labels(task_id=task_id).set(STATUS_MAP[status])
        conn.commit()

        if status in ('CANCELLED', 'COMPLETED'):
            producer = Producer(config.RABBITMQ_HOST, config.RABBITMQ_PORT, config.RABBITMQ_USER, config.RABBITMQ_PASSWD, f"task_{task_id}")
            producer.delete_rabbit_queue(task_id)

    finally:
        close_db(conn, cursor)

    return {"task_id": task_id, "status": status}


@app.delete("/task/{task_id}/subscription")
def unsubscribe_task(task_id: str, user_id: int = Depends(verify_token)):
    conn, cursor = get_db()
    try:
        deleted = delete_task_subscription(cursor, task_id, user_id)
        if not deleted:
            raise HTTPException(404, "No estás suscrito a esta tarea")
        conn.commit()
    finally:
        close_db(conn, cursor)
    return {"status": "ok"}


# ---------------- Crear proceso y execution asociado----------------
@app.post("/task/{task_id}/process")
def create_process(task_id: str, repo_hash: str, repo_commit: str, index: int = None, count: int = None, value: str = None, user_id: int = Depends(verify_token)):

    conn, cursor = get_db()

    try:

        if value is None:
            if (index is not None and index < 0) or (count is not None and count < 0):
                raise HTTPException(400, "Rango inválido")
        else:
            if index is not None and index < 0:
                raise HTTPException(400, "Rango inválido")
            if count is not None and count < 0:
                raise HTTPException(400, "Rango inválido")

        task = get_task_fields(cursor, task_id, ["id", "repo_snapshot_hash", "repo_commit"])
        if not task:
            raise HTTPException(404, "Tarea no encontrada")


        if index is not None and count is not None and check_process_overlap(cursor, task_id, index, count):
            raise HTTPException(409, "Rango solapado con otro proceso existente")


        if task["repo_snapshot_hash"] != repo_hash or task["repo_commit"] != repo_commit:
            raise HTTPException(409, "Alguno de los ficheros de la tarea han sido alterados, se le notificará al publicador")
        
        
        insert_task_subscription(cursor, user_id, task_id)


        index = insert_process(cursor, task_id, index, count, repo_hash, repo_commit, value)
        

        process_id = get_process_id_by_start_index(cursor, task_id, index)
            

        #verificar que la misma cuenta no tiene mas de una ejecucion por proceso
        execution = get_execution_id(cursor, task_id, process_id, user_id)

        if execution:
            raise HTTPException(409, "Ya existe una ejecución para este worker en este proceso")

        execution_id = insert_execution(cursor, task_id,  process_id, user_id, output=True)  #por diseño de la bbdd por defecto se pone ya a PENDING


        conn.commit()
    finally:
        close_db(conn, cursor)

    return {"process_id": process_id, "execution_id": execution_id}

# ---------------- Obtener proceso por indice dentro del intervalo ----------------------
@app.get("/task/{task_id}/process")
def get_processes(task_id: int, index: int = Query(None, description="Índice perteneciente al proceso")):
    conn, cursor = get_db()
    try:
        if index is not None:
            process = get_process_by_index(cursor, task_id, index)
            if not process:
                raise HTTPException(404, f"No se encontró un proceso que contenga el índice {index}")
            return {
                "process_id": process["id"],
                "input_start_index": process["input_start_index"],
                "input_end_index": process["input_end_index"],
                "canonical_execution_id": process["canonical_execution_id"]
            }
        else:
            rows = get_all_processes_by_task(cursor, task_id)

            return {"result": [
                {
                    "id": row["id"],
                    "input_start_index": row["input_start_index"],
                    "input_end_index": row["input_end_index"],
                    "canonical_execution_id": row["canonical_execution_id"],
                    "execution_count": row.get("execution_count", 0)
                }
                for row in (rows or [])
            ]}
    finally:
        close_db(conn, cursor)

# --------------- Obtener info execution ----------

@app.get("/task/{task_id}/process/{process_id}/executions")
def get_process_executions(task_id: str, process_id: str):
    conn, cursor = get_db()
    try:
        process = get_process(cursor, task_id, process_id)
        if not process:
            raise HTTPException(404, "Proceso no encontrado")
        executions = get_executions_by_process(cursor, task_id, process_id)
        return {
            "process": {
                "input_start_index": process["input_start_index"],
                "input_end_index": process["input_end_index"],
                "canonical_execution_id": process["canonical_execution_id"],
            },
            "executions": executions or []
        }
    finally:
        close_db(conn, cursor)


# ---------------- Crear execution --------------

@app.post("/task/{task_id}/process/{process_id}/execution")
def create_execution(task_id: str, process_id: str, user_id: int = Depends(verify_token)):

    conn, cursor = get_db()

    try:
        begin_transaction(conn)

        row_task = get_task_fields(cursor, task_id, ["is_deterministic", "status"])

        if not row_task:
            raise HTTPException(404, "Task not found")
        is_deterministic = row_task["is_deterministic"]
        status = row_task["status"]

        if status!="ACTIVE":
            print("La tarea seleccionada no está activa")
            return {"error": "La tarea no está activa en estos momentos"}


        if not is_deterministic:    #se añade bloqueo con for update para evitar que entre el tiempo de creacion de ejecucion y lectura de si ya existen se cree alguno

            execution = get_execution_id(cursor, task_id, process_id)
            if execution:
                raise HTTPException(409, "El proceso es sobre una tarea no determinista y ya ha sido procesado por otro nodo")

        #evitar que una misma cuenta confirme varias veces

        execution_id = get_execution_id(cursor, task_id, process_id, user_id)

        if execution_id:
            raise HTTPException(409, "Ya existe una ejecución para este worker en este proceso")

        

        execution_id = insert_execution(cursor, task_id, process_id, user_id, output=True) #por diseño de la bbdd por defecto se pone ya a pending

        conn.commit()

        return {"execution_id": execution_id}
    except Exception:
        conn.rollback()
        raise

    finally:
        close_db(conn, cursor)


# ---------------- Consultar procesos ----------------
@app.get("/task/{task_id}/process/{process_id}")
def get_process_request(task_id: str, process_id: str):
    conn, cursor = get_db()

    try:
    
        row = get_process(cursor, task_id, process_id)

        if not row:
            raise HTTPException(404, "Proceso no encontrado")


    finally:
        close_db(conn, cursor)

    result = [
        {"input_start_index": row["input_start_index"], "input_end_index": row["input_end_index"], "canonical_execution_id": row["canonical_execution_id"], "repo_snapshot_hash": row["repo_snapshot_hash"], "repo_commit": row["repo_commit"]}
    ]
    return {"result": result}


# ------------------ Obtener progreso de una tarea ------------

@app.get("/task/{task_id}/progress")
def get_progress(task_id: str):
    conn, cursor = get_db()
    try:
        progress = get_task_progress(cursor, task_id)
        if not progress:
            raise HTTPException(404, "Tarea no encontrada")
        return progress
    finally:
        close_db(conn, cursor)

# ---------------- Upload results ----------------
@app.post("/task/{task_id}/process/{process_id}/execution/{execution_id}/result")
async def create_result(
    task_id: int,
    process_id: str,
    execution_id: str,
    cpu_cycles: int = Form(...),
    ram_avg: float = Form(...),
    vram_avg: float = Form(0),
    tdp_w: float = Form(0),
    file: UploadFile = File(...),
    user_id: int = Depends(verify_token)
):

    conn, cursor = get_db()

    try:


        task_row = get_task_fields(cursor, task_id, ["publisher", "status", "is_deterministic", "is_dynamic"])

        if not task_row:
            raise HTTPException(404, "Task  not found")
        


        existing = get_execution_id(cursor, task_id, process_id, user_id, 'PENDING')

        if not existing:
            raise HTTPException(404, "No existe ejecución valida asociada a este worker")
        

        task_publisher = task_row['publisher']

        if task_row['status'] != 'ACTIVE' and int(user_id)!=int(task_publisher):

            return {"status": task_row['status'].lower()}

        process = get_process(cursor, task_id, process_id)

        if not process:
            raise HTTPException(404, "Process not found")
        

        file_id = await save_upload_file(cursor, file, config.UPLOAD_DIR)


        duracion = update_complete_execution(cursor, file_id, task_id, process_id, execution_id)


        task_status.labels(task_id=task_id).set(STATUS_MAP['SUCCESS'])


        executions_completed.labels(task_id=task_id, result="success").inc()

        

        cpu_cycles = cpu_cycles if cpu_cycles > 0 else 1
        ram_avg = ram_avg if ram_avg > 0 else 1
        
        vram_avg = vram_avg if vram_avg >= 0 else 0
        tdp_w = tdp_w if tdp_w >= 0 else 0

        gpu_cost = vram_avg * tdp_w * duracion
        ram_cost = ram_avg * duracion
        amount = cpu_cycles + gpu_cost + ram_cost
        

        if not task_row['is_deterministic']:

            handle_non_deterministic_payment(cursor, task_id, process_id, execution_id, user_id, task_publisher, amount)

        else:

            handle_deterministic_payment(cursor, task_id, process_id, execution_id, user_id, task_publisher, file_id, amount)


        new_balance = get_balance(cursor, task_publisher, lock=True)

        task = get_task_fields(cursor, task_id, ["avg_cost_per_item", "total_items_processed", "sum_sq_cost"])


        media_anterior = task['avg_cost_per_item'] or 0
        n_anterior = task['total_items_processed'] or 0
        sum_sq_anterior = task['sum_sq_cost'] or 0

        items_chunk = process['input_end_index'] - process['input_start_index'] + 1 #si start index es igual que end index es que se procesó uno
        cost_per_item = amount / items_chunk

        n_nuevo = n_anterior + items_chunk
        nueva_media = (media_anterior * n_anterior + cost_per_item * items_chunk) / n_nuevo
        nueva_sum_sq = sum_sq_anterior + (cost_per_item - media_anterior) * (cost_per_item - nueva_media)
        desviacion = math.sqrt(nueva_sum_sq / n_nuevo) if n_nuevo > 0 else 0


        set_task_metrics(cursor, task_id, nueva_media, n_nuevo, nueva_sum_sq)


        coste_siguiente_chunk = (nueva_media + desviacion) * items_chunk
        if new_balance <= 0 or new_balance < coste_siguiente_chunk:    #se le pausa todas las tareas que el publicador tenga
            pause_tasks_by_account(cursor, task_publisher)

            task_status.labels(task_id=task_id).set(STATUS_MAP['PAUSED'])


        
        #comprobacion de cierre de tarea completed
        if not task_row['is_dynamic']:
            progress = get_task_progress(cursor, task_id)
            if progress and progress["total_items"] > 0:
                if progress["items_procesados"] >= progress["total_items"]:
                    update_task_status(cursor, task_id, "COMPLETED")
                    task_status.labels(task_id=str(task_id)).set(STATUS_MAP["COMPLETED"])
                    producer = Producer(config.RABBITMQ_HOST, config.RABBITMQ_PORT, config.RABBITMQ_USER, config.RABBITMQ_PASSWD, f"task_{task_id}")
                    conn.commit()
                    producer.delete_rabbit_queue(task_id)

        conn.commit()




    except Exception:
        conn.rollback()
        raise
    finally:
        close_db(conn, cursor)



async def save_upload_file(cursor, file, upload_dir):


    sha256 = hashlib.sha256()
    size = 0
    #directorio staging handes de moverlo al shard final
    tmp_path = os.path.join(config.UPLOAD_DIR, "tmp")
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)

    #leer en bloques de 1MB y clacular tanto el tamaño como el hash al mismo tiempo para ficheros pesados
    with open(tmp_path, "wb") as f: 
        while chunk := await file.read(1024 * 1024):  # 1 MB
            size += len(chunk)
            sha256.update(chunk)
            f.write(chunk)


    hash_hex = sha256.hexdigest()
    shard = hash_hex[:2]       #aplicamos el sharding de 2 hexadecimales para 256 subdirectorios en este caso de un solo nivel como hace git
    shard_dir = os.path.join(upload_dir, shard)
    os.makedirs(shard_dir, exist_ok=True)

    final_path = os.path.join(shard_dir, hash_hex)


    # mover archivo si no existe
    if not os.path.exists(final_path):
        os.rename(tmp_path, final_path)
    else:
        os.remove(tmp_path)

    file_id = get_file_id(cursor, hash_hex)

    if file_id is None:

        if size > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Fichero excedió el límite")
    
        file_id = insert_file(cursor, file.filename,file.content_type,size,hash_hex)

    upload_size_bytes.observe(size)

    return file_id


def handle_non_deterministic_payment(cursor, task_id, process_id, execution_id, user_id, task_publisher, amount):
    insert_transfer(cursor, task_publisher, user_id, amount, task_id, process_id)

    payments_amount.labels(type="canonical").inc(amount) 
    
    update_canonical_process(cursor, task_id, process_id, execution_id)

def handle_deterministic_payment(cursor, task_id, process_id, execution_id, user_id, task_publisher, file_id, amount):

    prev = get_process(cursor, task_id, process_id, lock=True)
    prev_canonical_execution_id = (prev or {}).get("canonical_execution_id")


    recalculate_canonical_process(cursor, task_id, process_id)


    

    new_canonical_execution_id = get_canonical_execution_id(cursor, task_id, process_id)
    new_canonical_execution = get_execution_by_id(cursor, task_id, process_id, new_canonical_execution_id)
    new_canonical_id = new_canonical_execution['account_id']

    canonical_row = get_canonical_payment(cursor, task_publisher, task_id, process_id, new_canonical_execution_id) # no permite lock ya que es inmutable blockchain table


    if canonical_row:
        # el canonico ya habia cobrado antes, puede que haya cambiado
        canonical_account_id = canonical_row["account_id"]
        canonical_amount = canonical_row["canonical_amount"]
    else:
        # el canonico es nuevo (este mismo worker que acaba de subir)
        canonical_account_id = new_canonical_id
        canonical_amount = amount


    # contar confirmaciones del resultado canonico
    n_confirmations = get_canonical_confirmation_count(cursor, task_id, process_id, new_canonical_execution_id) # no permite lock ya que es inmutable blockchain table

    if prev_canonical_execution_id != new_canonical_execution_id:
        # el canonico ha cambiado: el anterior debe devolver el dinero y luego ya se le pagará al nuevo
        if prev_canonical_execution_id is not None:


            old_pago = get_previous_canonical_payment(cursor, task_id, process_id, task_publisher) # no permite lock ya que es inmutable blockchain table


            #1 devolucion de pago
            if old_pago:


                insert_transfer(cursor, old_pago['to_account_id'], task_publisher, old_pago['amount'], task_id, process_id)
                
                #payments_amount.labels(type="canonical_change").dec(float(old_pago['amount']))



            #2 pagar al nuevo canonico, el nuevo canonico es el que primero subio ese resultado, no el user_id

            insert_transfer(cursor, task_publisher, new_canonical_id, canonical_amount,  task_id, process_id)

            payments_amount.labels(type="canonical_change").inc(float(canonical_amount))


            #3 pagar al ultimo que confirmo el cambio del canonico

            confirmadores = recalculate_canonical_confirmations(cursor,  new_canonical_execution_id, task_id, process_id, new_canonical_id)

            for c in confirmadores:
                insert_transfer(cursor, SYSTEM_IDS["fees"], c['account_id'], canonical_amount / n_confirmations, task_id, process_id)
                payments_amount.labels(type="confirmation").inc(float(canonical_amount / n_confirmations))


            reset_confirmation_counter(cursor, user_id, task_id)


        else:
            # no habia canonico previo: publisher paga directamente al nuevo canonico
            insert_transfer(cursor, task_publisher, canonical_account_id, canonical_amount, task_id, process_id)

            payments_amount.labels(type="canonical").inc(canonical_amount)

        # actualizar reputacion de todos los afectados
        workers_afectados = get_account_ids_with_successful_execution(cursor, task_id, process_id)

        for w in workers_afectados:

            recalculate_reputation(cursor, w['account_id'], SYSTEM_IDS["fees"])

    else:
        # canonico no ha cambiado: ver si este worker confirma el resultado canonico
        canon_execution = get_execution_by_id(cursor, task_id, process_id, new_canonical_execution_id)

        canon_file_id = canon_execution["result_file_id"]

        if file_id == canon_file_id and int(user_id) != int(canonical_account_id):
            # confirma el canonico pero no es el canonico: SYSTEM_FEES le paga su parte
            reward = canonical_amount / n_confirmations

            insert_transfer(cursor, SYSTEM_IDS["fees"], user_id, reward, task_id, process_id)

            payments_amount.labels(type="confirmation").inc(float(reward))  #necesita casteo ya que viene de tipo decimal de la bbdd
            

        # resetear contador verificacion, SE RESETEA SEA O NO INCORRECTO
        reset_confirmation_counter(cursor, user_id, task_id)

        # actualizar reputacion solo del worker actual

        recalculate_reputation(cursor, user_id, SYSTEM_IDS["fees"])

    #actualizar confirmación para determinar si es necesario realizar confirmacion
    if prev_canonical_execution_id is None:  #es chunk nuevo y no verificado
        inc_confirmation_counter(cursor, user_id, task_id)

# ---------------- Obtener proceso para verificar --------


@app.get("/task/{task_id}/confirm")
def get_confirm(task_id: str, user_id: int = Depends(verify_token)):
    conn, cursor = get_db()
    try:

        process = get_process_pending_confirmation(cursor, task_id, user_id)
        if not process:
            return {"status": "nothing_to_verify"}
            
        return {
            "process_id": process["id"]
        }
    finally:
        close_db(conn, cursor)


# ---------------- Obtener salidas ----------------
@app.get("/task/{task_id}/output")
def get_outputs(task_id: str, download: bool = False, process_id: str = Query(None), canonical_only: bool = Query(False)):
    conn, cursor = get_db()


    try:
        
        rows = get_task_output_files(cursor, task_id, process_id, canonical_only)
    finally:
        close_db(conn, cursor)

    if not rows:
        raise HTTPException(404, "No hay archivos para esta tarea")


    if download:
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in rows:
                shard_dir = os.path.join(config.UPLOAD_DIR, r["hash_sha256"][:2])
                final_path = os.path.join(shard_dir, r["hash_sha256"])

                if not os.path.exists(final_path):
                    raise HTTPException(404, f"Archivo no encontrado: {r['original_name']}")

                zf.write(final_path, arcname=r["original_name"])

        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=task_{task_id}_outputs.zip"}
        )

    result = []
    MAX_CHARS = 200

    for r in rows:
        item = {
            "original_name": r["original_name"],
            "mime_type": r["mime_type"],
            "size": r["size"],
            "hash_sha256": r["hash_sha256"],
            "created_at": r["created_at"],
            "account_id": r["account_id"]
        }

        if r["original_name"].endswith((".txt", ".json", ".log")):

            shard_dir = os.path.join(config.UPLOAD_DIR, r['hash_sha256'][:2])
            final_path = os.path.join(shard_dir, r['hash_sha256'])

            if os.path.exists(final_path):
                with open(final_path, "r", encoding="utf-8") as f:
                    contenido = f.read(MAX_CHARS+1)

                if len(contenido) > MAX_CHARS:
                    item["content"] = f"{contenido[:MAX_CHARS]}..."
                else:
                    item["content"] = contenido


        result.append(item)


    return {"result": result}

"""
referencias


https://medium.com/@michael.andrews/file-name-hashing-creating-a-hashed-directory-structure-eabb03aa4091

https://docs.python.org/3/library/hashlib.html#hashlib.hash.update : calculo de hash incremental

https://fastapi.tiangolo.com/tutorial/request-files/  : subida de ficheros multipart



"""