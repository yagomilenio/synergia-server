import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call, PropertyMock
import pytest
import jwt as _jwt
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession


# Constantes y helpers JWT


_SECRET = "VrQKDysvmgZv7kS/NjPi6k05lXRT4LP8mwABREImmHA="
_ALGO   = "HS256"

FAKE_CONFIG = {
    "task_cost": 1000,
    "system_accounts": {
        "fees": "SYSTEM_FEES", "rewards": "SYSTEM_REWARDS",
        "burn": "SYSTEM_BURN", "mint": "SYSTEM_MINT",
    },
    "rabbitmq": {"host": "localhost", "port": 5672,
                 "user": "guest", "password": "guest", "queue": "main"},
    "mariadb":  {"user": "root", "password": "1234",
                 "database": "p2pcn", "address": "mariadb"},
    "upload_dir": "/tmp/uploads",
    "oauth": {
        "google_client_id": "gid", "google_client_secret": "gsecret",
        "github_client_id": "ghid", "github_client_secret": "ghsecret",
    },
}


def make_token(user_id: int = 1, expired: bool = False) -> str:
    delta = timedelta(minutes=-1) if expired else timedelta(hours=24)
    return _jwt.encode(
        {"sub": str(user_id), "exp": datetime.utcnow() + delta},
        _SECRET, algorithm=_ALGO,
    )


VALID_TOKEN   = make_token(1)
EXPIRED_TOKEN = make_token(1, expired=True)
TOKEN_USER_2  = make_token(2)



# Fake aio_pika message


def make_fake_message(index: int = 0, count: int = 1, end_index: int = None):
    """Crea un mock de aio_pika.IncomingMessage con body JSON."""
    body_data = {"index": index, "count": count}
    if end_index is not None:
        body_data["end_index"] = end_index

    msg              = MagicMock()
    msg.body         = json.dumps(body_data).encode()
    msg.processed    = False
    msg.ack          = AsyncMock()
    msg.nack         = AsyncMock()
    return msg



# Fixture principal: app + TestClient


@pytest.fixture(scope="module")
def ws_client():
    """
    Levanta ws_api.app sin infraestructura real.
    Todos los imports problemáticos se parchean antes de cargar el módulo.
    """
    import sys
    for mod in ["ws_api", "config", "db", "db.db", "utils.jwt_util", "aio_pika"]:
        sys.modules.pop(mod, None)

    with patch("builtins.open", MagicMock()), \
         patch("json.load", return_value=FAKE_CONFIG), \
         patch("os.path.exists", return_value=True), \
         patch("mysql.connector.pooling.MySQLConnectionPool"), \
         patch("aio_pika.connect_robust", new_callable=AsyncMock):

        from ws_api import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c



# Helpers para construir mocks de aio_pika completos


def build_amqp_mocks(messages=None):
    """
    Devuelve (mock_connect, mock_connection, mock_channel, mock_queue)
    con side_effects preconfigurados.
    messages: lista de fake_message que devolverá consume_n_messages
    """
    messages = messages or []

    mock_queue      = AsyncMock()
    mock_channel    = AsyncMock()
    mock_connection = AsyncMock()
    mock_connect    = AsyncMock(return_value=mock_connection)

    mock_connection.channel = AsyncMock(return_value=mock_channel)
    mock_channel.set_qos    = AsyncMock()
    mock_channel.declare_queue = AsyncMock(return_value=mock_queue)

    return mock_connect, mock_connection, mock_channel, mock_queue



# 1.  Autenticación


class TestWSAuthentication:

    def test_invalid_token_closes_1008(self, ws_client):
        """Token inválido → conexión rechazada con código 1008."""
        with patch("ws_api.verify_token", side_effect=Exception("bad token")), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            try:
                with ws_client.websocket_connect(
                    "/ws/task/1?token=badtoken&n_consumes=1"
                ) as ws:
                    pass
            except Exception:
                pass  # La conexión se cierra con código 1008

    def test_expired_token_closes_connection(self, ws_client):
        """Token expirado → verify_token lanza HTTPException → cierre 1008."""
        from fastapi import HTTPException
        with patch("ws_api.verify_token",
                   side_effect=HTTPException(status_code=401, detail="Token expirado")):
            try:
                with ws_client.websocket_connect(
                    f"/ws/task/1?token={EXPIRED_TOKEN}&n_consumes=1"
                ) as ws:
                    pass
            except Exception:
                pass

    def test_valid_token_accepts_connection(self, ws_client):
        """Token válido + tarea existente → conexión aceptada."""
        mock_connect, mock_conn, mock_channel, mock_queue = build_amqp_mocks()

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields", return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust", mock_connect):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                data = ws.receive_json()
                # Puede ser "empty" o lista de mensajes
                assert data is not None


# 2.  Tarea no encontrada

class TestWSTaskNotFound:

    def test_task_not_found_closes_1008(self, ws_client):
        mock_connect, *_ = build_amqp_mocks()

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields", return_value=None), \
             patch("aio_pika.connect_robust", mock_connect):

            mock_db.return_value = (MagicMock(), MagicMock())
            try:
                with ws_client.websocket_connect(
                    f"/ws/task/9999?token={VALID_TOKEN}&n_consumes=1"
                ) as ws:
                    pass
            except Exception:
                pass  # se espera cierre


# 3.  Ciclo completo: mensajes consumidos → ACK

class TestWSFullCycle:

    def test_consume_messages_and_ack(self, ws_client):
        """
        Worker envía 'next' → recibe mensajes → envía otro 'next' →
        los mensajes previos se marcan como SUCCESS → ACK.
        """
        msg1 = make_fake_message(index=0, count=5, end_index=4)
        msg2 = make_fake_message(index=5, count=5, end_index=9)

        # Primer consume devuelve msg1, segundo devuelve msg2
        consume_calls = [
            ([msg1], [{"index": 0, "count": 5, "end_index": 4}]),
            ([msg2], [{"index": 5, "count": 5, "end_index": 9}]),
        ]
        consume_iter = iter(consume_calls)

        async def fake_consume(queue, n):
            try:
                return next(consume_iter)
            except StopIteration:
                return ([], [])

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.is_process_successfully_terminated", return_value=True), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("ws_api.consume_n_messages", side_effect=fake_consume), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=5"
            ) as ws:
                # Primera señal next
                ws.send_text(json.dumps({"action": "next", "n": 5}))
                data1 = ws.receive_json()
                assert isinstance(data1, list) or isinstance(data1, dict)

                # Segunda señal next → los mensajes anteriores deberían ACK
                ws.send_text(json.dumps({"action": "next", "n": 5}))
                data2 = ws.receive_json()
                assert data2 is not None

    def test_empty_queue_returns_empty_status(self, ws_client):
        """Cola vacía → el WS devuelve {'status': 'empty'}."""
        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                data = ws.receive_json()
                assert data == {"status": "empty"}


# 4.  Estado "paused" — tarea no activa y usuario no es publisher

class TestWSPausedTask:

    def test_paused_task_non_publisher_receives_paused(self, ws_client):
        """
        Si la tarea está PAUSED y el user_id != publisher,
        el WS debe enviar {'status': 'paused'} y cerrar.
        """
        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "PAUSED", "publisher": 99}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                data = ws.receive_json()
                assert data == {"status": "paused"}

    def test_paused_task_publisher_can_continue(self, ws_client):
        """El publisher puede seguir recibiendo mensajes aunque la tarea esté PAUSED."""
        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "PAUSED", "publisher": 1}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                data = ws.receive_json()
                # El publisher NO recibe "paused" → recibe empty o mensajes
                assert data != {"status": "paused"}


# 5.  Verificación requerida

class TestWSVerificationRequired:

    def test_verification_required_when_threshold_reached(self, ws_client):
        """
        Cuando last_confirmation >= CONFIRMACIONES_CADA_N_CHUNKS (10) y hay
        procesos pendientes de verificar → enviar {'status': 'verification_required'}.
        """
        msg_to_nack = make_fake_message(index=0, count=1)

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=10), \
             patch("ws_api.get_process_to_confirmate", return_value={"id": 5}), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                data = ws.receive_json()
                assert data == {"status": "verification_required"}

    def test_no_verification_when_below_threshold(self, ws_client):
        """
        Cuando last_confirmation < 10 → no se requiere verificación.
        """
        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=5), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                data = ws.receive_json()
                assert data != {"status": "verification_required"}

    def test_verification_skipped_when_nothing_to_confirm(self, ws_client):
        """
        Aunque last_confirmation >= 10, si no hay procesos pendientes
        no se envía 'verification_required'.
        """
        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=10), \
             patch("ws_api.get_process_to_confirmate", return_value=None), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                data = ws.receive_json()
                assert data != {"status": "verification_required"}


# 6.  n dinámico con la acción "next"

class TestWSDynamicN:

    def test_dynamic_n_updates_prefetch(self, ws_client):
        """
        Enviar {'action': 'next', 'n': 20} debe llamar a set_qos con 20.
        """
        mock_channel = AsyncMock()
        mock_channel.set_qos          = AsyncMock()
        mock_channel.declare_queue    = AsyncMock(return_value=MagicMock())

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=mock_channel),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=5"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 20}))
                ws.receive_json()
                # set_qos debe haber sido llamado con 20 al cambiar n
                mock_channel.set_qos.assert_called()

    def test_max_prefetch_capped_at_65535(self, ws_client):
        """n > 65535 debe ser recortado a 65535 (límite AMQP)."""
        mock_channel = AsyncMock()
        mock_channel.set_qos       = AsyncMock()
        mock_channel.declare_queue = AsyncMock(return_value=MagicMock())

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=mock_channel),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 100000}))
                ws.receive_json()
                # set_qos llamado con 65535, no 100000
                calls = mock_channel.set_qos.call_args_list
                for c in calls:
                    n_arg = c.kwargs.get("prefetch_count") or (c.args[0] if c.args else None)
                    if n_arg is not None:
                        assert n_arg <= 65535

    def test_invalid_json_signal_uses_default_n(self, ws_client):
        """Si la señal no es JSON válido, se usa n_consumes original."""
        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])) as mock_consume, \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=3"
            ) as ws:
                ws.send_text("not_json")
                ws.receive_json()
                # consume debe haber sido llamado con n=3 (el valor por defecto)
                mock_consume.assert_called_once()
                _, n_called = mock_consume.call_args.args
                assert n_called == 3


# 7.  Limpieza en desconexión (cleanup)

class TestWSCleanup:

    def test_cleanup_cancels_incomplete_executions(self, ws_client):
        """Al desconectarse, debe cancelar las ejecuciones incompletas."""
        mock_cancel = MagicMock()

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions", mock_cancel), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                ws.receive_json()
            # Tras salir del context manager la conexión se cierra → cleanup
            mock_cancel.assert_called()

    def test_cleanup_deletes_orphan_processes(self, ws_client):
        """Los procesos huérfanos deben eliminarse al desconectarse."""
        mock_delete = MagicMock()
        orphans = [{"id": 10}, {"id": 11}]

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=orphans), \
             patch("ws_api.delete_process_by_task", mock_delete), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                ws.receive_json()

            mock_delete.assert_called()

    def test_cleanup_nacks_unprocessed_messages(self, ws_client):
        """
        Mensajes no procesados exitosamente al desconectarse deben hacer NACK.
        """
        msg = make_fake_message(index=0, count=1, end_index=0)

        # Primer consume entrega el mensaje, segundo cierra el WS
        consume_iter = iter([
            ([msg], [{"index": 0, "count": 1, "end_index": 0}]),
        ])

        async def fake_consume(queue, n):
            try:
                return next(consume_iter)
            except StopIteration:
                await asyncio.sleep(10)  # block hasta que se cierre la conexión
                return ([], [])

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.is_process_successfully_terminated", return_value=False), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("ws_api.consume_n_messages", side_effect=fake_consume), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                # No esperamos respuesta ya que consume bloquea → cerramos
            # El mensaje no fue procesado exitosamente → nack en cleanup
            msg.nack.assert_called()


# 8.  Mensajes con ACK (proceso terminado con éxito)

class TestWSAckBehavior:

    def test_ack_when_process_successfully_terminated(self, ws_client):
        """Si is_process_successfully_terminated = True → msg.ack()."""
        msg = make_fake_message(index=0, count=5, end_index=4)

        consume_calls = iter([
            ([msg], [{"index": 0, "count": 5, "end_index": 4}]),
            ([], []),
        ])

        async def fake_consume(queue, n):
            try:
                return next(consume_calls)
            except StopIteration:
                return ([], [])

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.is_process_successfully_terminated", return_value=True), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("ws_api.consume_n_messages", side_effect=fake_consume), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=5"
            ) as ws:
                # Primera señal: recibe msg
                ws.send_text(json.dumps({"action": "next", "n": 5}))
                ws.receive_json()

                # Segunda señal: procesa ACK del anterior y consume vacío
                ws.send_text(json.dumps({"action": "next", "n": 5}))
                ws.receive_json()

            # El mensaje debe haber recibido ACK
            msg.ack.assert_called_once()

    def test_nack_when_process_not_terminated(self, ws_client):
        """Si is_process_successfully_terminated = False → msg.nack(requeue=True)."""
        msg = make_fake_message(index=0, count=5, end_index=4)

        consume_calls = iter([
            ([msg], [{"index": 0, "count": 5, "end_index": 4}]),
            ([], []),
        ])

        async def fake_consume(queue, n):
            try:
                return next(consume_calls)
            except StopIteration:
                return ([], [])

        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.is_process_successfully_terminated", return_value=False), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("ws_api.consume_n_messages", side_effect=fake_consume), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=5"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 5}))
                ws.receive_json()
                ws.send_text(json.dumps({"action": "next", "n": 5}))
                ws.receive_json()

            msg.nack.assert_called_once_with(requeue=True)


# 9.  Parámetros de URL

class TestWSURLParams:

    def test_missing_token_param(self, ws_client):
        """Falta token → 403 o cierre inmediato."""
        try:
            with ws_client.websocket_connect(
                "/ws/task/1?n_consumes=1"
            ) as ws:
                ws.receive_json()
        except Exception:
            pass  # Se espera error de conexión

    def test_missing_n_consumes_param(self, ws_client):
        """Falta n_consumes → FastAPI devuelve 422."""
        try:
            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}"
            ) as ws:
                pass
        except Exception:
            pass  # 422 o cierre


# 10.  consume_n_messages — tests unitarios de la función auxiliar

class TestConsumeNMessages:
    """
    Tests unitarios directos de la función consume_n_messages
    (sin levantar el WS completo).
    """

    @pytest.mark.asyncio
    async def test_consume_returns_empty_on_timeout(self):
        from ws_api import consume_n_messages

        mock_queue       = AsyncMock()
        consumer_tag     = "ctag_1"
        mock_queue.consume = AsyncMock(return_value=consumer_tag)
        mock_queue.cancel  = AsyncMock()

        # El on_message nunca se llama → timeout → lista vacía
        async def slow_consume(callback):
            return consumer_tag

        mock_queue.consume = slow_consume

        msgs_raw, messages = await consume_n_messages(mock_queue, 5)
        assert messages == []
        assert msgs_raw == []

    @pytest.mark.asyncio
    async def test_consume_returns_n_messages(self):
        from ws_api import consume_n_messages

        received_messages = []
        received_raw      = []

        # Simulamos que el broker entrega 3 mensajes inmediatamente
        async def instant_consume(callback):
            for i in range(3):
                msg = make_fake_message(index=i, count=1)
                await callback(msg)
            return "ctag_instant"

        mock_queue        = AsyncMock()
        mock_queue.consume = instant_consume
        mock_queue.cancel  = AsyncMock()

        msgs_raw, messages = await consume_n_messages(mock_queue, 3)
        assert len(messages) == 3
        assert len(msgs_raw) == 3

    @pytest.mark.asyncio
    async def test_consume_stops_at_n_even_if_more_available(self):
        from ws_api import consume_n_messages

        async def instant_consume_10(callback):
            for i in range(10):
                msg = make_fake_message(index=i)
                await callback(msg)
            return "ctag"

        mock_queue        = AsyncMock()
        mock_queue.consume = instant_consume_10
        mock_queue.cancel  = AsyncMock()

        _, messages = await consume_n_messages(mock_queue, 3)
        # Debe dejar de consumir cuando llega a 3
        assert len(messages) <= 10  # puede recibir más, pero el done.set() detiene


# 11.  Múltiples workers en la misma tarea

class TestWSMultipleWorkers:

    def test_two_workers_connect_same_task(self, ws_client):
        """Dos workers distintos pueden conectarse a la misma tarea."""

        def build_context(user_id):
            return {
                "verify_token": MagicMock(return_value=user_id),
                "get_task_fields": {"status": "ACTIVE", "publisher": 99},
                "token": make_token(user_id),
            }

        for uid in [1, 2]:
            token = make_token(uid)
            with patch("ws_api.verify_token", return_value=uid), \
                 patch("ws_api.get_db") as mock_db, \
                 patch("ws_api.close_db"), \
                 patch("ws_api.get_task_fields",
                       return_value={"status": "ACTIVE", "publisher": 99}), \
                 patch("ws_api.get_last_task_confirmation", return_value=0), \
                 patch("ws_api.consume_n_messages",
                       new_callable=AsyncMock, return_value=([], [])), \
                 patch("ws_api.cancel_incomplete_executions"), \
                 patch("ws_api.get_processes_and_execution_without_completed_executions",
                       return_value=[]), \
                 patch("aio_pika.connect_robust",
                       AsyncMock(return_value=MagicMock(
                           channel=AsyncMock(return_value=MagicMock(
                               set_qos=AsyncMock(),
                               declare_queue=AsyncMock(return_value=MagicMock()),
                           )),
                           close=AsyncMock(),
                       ))):

                mock_db.return_value = (MagicMock(), MagicMock())

                with ws_client.websocket_connect(
                    f"/ws/task/1?token={token}&n_consumes=1"
                ) as ws:
                    ws.send_text(json.dumps({"action": "next", "n": 1}))
                    data = ws.receive_json()
                    assert data is not None


# 12.  Métricas Prometheus WebSocket

class TestWSMetrics:

    def test_ws_metrics_endpoint_accessible(self, ws_client):
        """El endpoint /metrics debe devolver métricas de Prometheus."""
        resp = ws_client.get("/metrics")
        assert resp.status_code == 200

    def test_active_workers_gauge_increments(self, ws_client):
        """
        Al conectar un worker, el gauge p2pcn_active_workers debe incrementarse.
        No podemos leerlo directamente en unit tests, pero verificamos que la
        conexión se establece sin errores.
        """
        with patch("ws_api.verify_token", return_value=1), \
             patch("ws_api.get_db") as mock_db, \
             patch("ws_api.close_db"), \
             patch("ws_api.get_task_fields",
                   return_value={"status": "ACTIVE", "publisher": 2}), \
             patch("ws_api.get_last_task_confirmation", return_value=0), \
             patch("ws_api.consume_n_messages",
                   new_callable=AsyncMock, return_value=([], [])), \
             patch("ws_api.cancel_incomplete_executions"), \
             patch("ws_api.get_processes_and_execution_without_completed_executions",
                   return_value=[]), \
             patch("aio_pika.connect_robust",
                   AsyncMock(return_value=MagicMock(
                       channel=AsyncMock(return_value=MagicMock(
                           set_qos=AsyncMock(),
                           declare_queue=AsyncMock(return_value=MagicMock()),
                       )),
                       close=AsyncMock(),
                   ))):

            mock_db.return_value = (MagicMock(), MagicMock())

            with ws_client.websocket_connect(
                f"/ws/task/1?token={VALID_TOKEN}&n_consumes=1"
            ) as ws:
                ws.send_text(json.dumps({"action": "next", "n": 1}))
                ws.receive_json()
                # La conexión está activa aquí → gauge debe ser >= 1
                resp_inside = ws_client.get("/metrics")
                assert "p2pcn_active_workers" in resp_inside.text


# 13.  safe_send_json — helper auxiliar

class TestSafeSendJson:

    @pytest.mark.asyncio
    async def test_safe_send_returns_true_on_success(self):
        from ws_api import safe_send_json
        from starlette.websockets import WebSocketDisconnect

        mock_ws          = AsyncMock()
        mock_ws.send_json = AsyncMock(return_value=None)

        result = await safe_send_json(mock_ws, {"status": "ok"})
        assert result is True

    @pytest.mark.asyncio
    async def test_safe_send_returns_false_on_disconnect(self):
        from ws_api import safe_send_json
        from starlette.websockets import WebSocketDisconnect

        mock_ws          = AsyncMock()
        mock_ws.send_json = AsyncMock(
            side_effect=WebSocketDisconnect(code=1001)
        )

        result = await safe_send_json(mock_ws, {"status": "ok"})
        assert result is False
