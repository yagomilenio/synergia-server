import asyncio
import json
import jwt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
import aio_pika
import config
from db import *
from utils.jwt_util import verify_token
from prometheus_client import Gauge, make_asgi_app

app = FastAPI()


active_workers = Gauge("p2pcn_active_workers", "Workers activos", ["task_id"])
app.mount("/metrics", make_asgi_app())


@app.websocket("/ws/task/{task_id}")
async def task_websocket(websocket: WebSocket, task_id: str, n_consumes: int, token: str = Query(...)):
    try:
        user_id = verify_token(token)
    except HTTPException:
        await websocket.close(code=1008)  # Policy Violation
        return

    await websocket.accept()

    active_workers.labels(task_id=task_id).inc()

    

    
    try:
        conn, cursor = get_db()
        task = get_task_fields(cursor, task_id, ["status"])
        close_db(conn, cursor)


        if not task:
            await websocket.close(code=1008)
            return



        connection = await aio_pika.connect_robust(
            host=config.RABBITMQ_HOST,
            port=config.RABBITMQ_PORT,
            login=config.RABBITMQ_USER,
            password=config.RABBITMQ_PASSWD,
        )
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=n_consumes)
        queue = await channel.declare_queue(f"task_{task_id}", durable=True)

        current_messages: list[aio_pika.IncomingMessage] = []

        while True:

            

            try:
                signal = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            
            conn, cursor = get_db()
            try: 
                conn.commit()
                task=get_task_fields(cursor, task_id, ["status", "publisher"])

                print(f"datos tarea: {task}")

                if task['status'] == "COMPLETED":
                    await websocket.send_json({"status": "completed"})
                    break

                elif task['status'] != 'ACTIVE' and int(user_id) != int(task['publisher']):
                    await websocket.send_json({"status": "paused"})
                    break  

                try:
                    payload = json.loads(signal)
                    if isinstance(payload, dict) and payload.get("action") == "next":
                        MAX_PREFETCH = 65535    #el protocolo amqp usado solo permite por mensaje enviar 16 bits
                        n = min(int(payload.get("n", n_consumes)), MAX_PREFETCH)  # n dinámico
                    elif isinstance(payload, dict) and payload.get("action") == "confirmation_done":
                    
                        last_confirmation = get_last_task_confirmation(cursor, user_id, task_id)
                        if last_confirmation is None or last_confirmation < config.PROCESS_UNTIL_CONFIRMATION:
                            conn.commit()

                        n = None

                    else:
                        n = n_consumes
                except json.JSONDecodeError:
                    n = n_consumes

                if n is None:
                    continue

                if n != n_consumes:
                    await channel.set_qos(prefetch_count=n)
                    n_consumes = n





                # comprobar si necesita verificacion
                last_confirmation=get_last_task_confirmation(cursor, user_id, task_id)
                
                if last_confirmation and last_confirmation >= config.PROCESS_UNTIL_CONFIRMATION:


                    # comprobar si hay algo que verificar antes de bloquear al worker
                    hay_algo=get_process_to_confirmate(cursor, task_id, user_id)

                    if hay_algo:
                        for msg in current_messages:
                            if not msg.processed:
                                body = json.loads(msg.body)
                                start = body.get('index')
                                end = body.get('end_index') if body.get('end_index') is not None else (body.get('index', 0) + body.get('count', 1) - 1)
                                is_process_succ = is_process_successfully_terminated(cursor, task_id, start, end, user_id)
                                if is_process_succ:
                                    await msg.ack()
                                else:
                                    await msg.nack(requeue=True)
                        current_messages = []
                        await websocket.send_json({"status": "verification_required"})
                        continue



                

                #hacer ack de mensajes previos
                for msg in current_messages:
                    if not msg.processed:
                        body = json.loads(msg.body)
                        start = body.get('index')
                        end = body.get('end_index') if body.get('end_index') is not None else (body.get('index', 0) + body.get('count', 1) - 1)
                        
                        is_process_succ = is_process_successfully_terminated(cursor, task_id, start, end, user_id)

                        if is_process_succ:
                            await msg.ack()
                        else:
                            await msg.nack(requeue=True)

            finally:
                close_db(conn, cursor)

            current_messages = []
            messages = []

            #consumir n mensajes
            current_messages, messages = await consume_n_messages(queue, n)

            if not messages:
                await websocket.send_json({"status": "empty"})
            else:
                await websocket.send_json(messages)


    finally:

        acks = []
        nacks = []

        conn, cursor = get_db()

        for msg in current_messages:
            if not msg.processed:   #en el caso de que se haga muera el cliente entre el proceso de subida de resultados y este se ponga el execution a sucess y se ejecuta el next entonces no se hace ack en rabbit que se compruebe esto  para saber si hacer ack o volver a cola
                body = json.loads(msg.body)
                start = body.get('index')
                end = body.get('end_index') if body.get('end_index') is not None else (body.get('index', 0) + body.get('count', 1) - 1)

                is_process_succ = is_process_successfully_terminated(cursor, task_id, start, end, user_id)
                
                if is_process_succ:
                    acks.append(msg)   # ya está en SUCCESS, confirmar
                else:
                    nacks.append(msg)  # no está en SUCCESS, devolver a la cola

        try:
            conn.rollback()
            cancel_incomplete_executions(cursor, task_id, user_id)
            conn.commit()


            # Si el process tiene una sola execution y es de este usuario, limpiar
            orphan_processes = get_processes_and_execution_without_completed_executions(cursor, task_id, user_id)
            

            if orphan_processes:
                orphan_ids = [row['id'] for row in orphan_processes]
                

                delete_process_by_task(cursor, task_id, orphan_ids)

                conn.commit()
        finally:
            close_db(conn, cursor)


        for msg in acks:
            await msg.ack()
        for msg in nacks:
            await msg.nack(requeue=True)


        active_workers.labels(task_id=task_id).dec()
        await connection.close()


async def safe_send_json(ws: WebSocket, data: dict):
    try:
        await ws.send_json(data)
    except WebSocketDisconnect:
        print("Cliente desconectado al intentar enviar datos")
        return False
    return True


async def consume_n_messages(queue, n):
    messages = []
    msgs_raw = []
    done = asyncio.Event()

    async def on_message(msg: aio_pika.IncomingMessage):
        msgs_raw.append(msg)
        messages.append(json.loads(msg.body))

        if len(messages) >= n:
            done.set()  # señalamos que ya tenemos suficientes

    # crear consumer
    consumer_tag = await queue.consume(on_message)

    try:
        # esperar hasta recibir n mensajes o timeout
        try:
            await asyncio.wait_for(done.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass
    finally:
        # cancelar consumer SIEMPRE
        await queue.cancel(consumer_tag)

    return msgs_raw, messages




#se  considera conexion directa con bbdd para reducir latencias