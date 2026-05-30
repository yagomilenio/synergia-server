import io
import json
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, call
import pytest
from fastapi.testclient import TestClient



FAKE_CONFIG = {
    "task_cost": 1000,
    "system_accounts": {
        "fees":    "SYSTEM_FEES",
        "rewards": "SYSTEM_REWARDS",
        "burn":    "SYSTEM_BURN",
        "mint":    "SYSTEM_MINT",
    },
    "rabbitmq": {"host": "localhost", "port": 5672, "user": "guest",
                 "password": "guest", "queue": "main"},
    "mariadb":  {"user": "root", "password": "1234",
                 "database": "p2pcn", "address": "mariadb"},
    "upload_dir": "/tmp/uploads",
    "oauth": {
        "google_client_id":     "gid",
        "google_client_secret": "gsecret",
        "github_client_id":     "ghid",
        "github_client_secret": "ghsecret",
    },
}

# JWT válido para user_id=1 (pre-generado con la SECRET_KEY del código)
import jwt as _jwt
_SECRET  = "VrQKDysvmgZv7kS/NjPi6k05lXRT4LP8mwABREImmHA="
_ALGO    = "HS256"

def _make_token(user_id: int = 1, expired: bool = False) -> str:
    delta = timedelta(minutes=-1) if expired else timedelta(hours=24)
    exp   = datetime.utcnow() + delta
    return _jwt.encode({"sub": str(user_id), "exp": exp}, _SECRET, algorithm=_ALGO)

VALID_TOKEN   = _make_token(1)
EXPIRED_TOKEN = _make_token(1, expired=True)


@pytest.fixture(scope="module")
def client():
    """
    Crea el TestClient mockeando config, DB y otras dependencias
    para que api.py arranque sin infraestructura real.
    """
    with patch("builtins.open", MagicMock()), \
         patch("json.load", return_value=FAKE_CONFIG), \
         patch("os.path.exists", return_value=True):

        # Importamos config mockeado antes que api
        import importlib, sys

        # Limpiamos posibles imports previos para forzar recarga limpia
        for mod in ["config", "api", "db", "db.db", "utils.jwt_util",
                    "utils.configuration_interpreter", "publisher"]:
            sys.modules.pop(mod, None)

        with patch("mysql.connector.pooling.MySQLConnectionPool") as _pool, \
             patch("publisher.Producer") as _prod, \
             patch("api.load_system_account_ids", return_value={
                 "mint": 1, "fees": 2, "burn": 3, "rewards": 4
             }), \
             patch("api.startup_event"):

            from api import app
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c


# Helpers reutilizables
def auth_headers(token: str = None) -> dict:
    return {"token": token or VALID_TOKEN}



# 1.  POST /account  — Crear usuario


class TestCreateAccount:

    def test_create_account_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.insert_account", return_value=99), \
             patch("api.insert_auth_local_credential"), \
             patch("api.insert_transfer"), \
             patch("api.close_db"):

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_db.return_value = (mock_conn, mock_cursor)

            resp = client.post("/account", params={
                "username": "testuser",
                "passwd":   "securepass123",
                "email":    "test@example.com",
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "Usuario creado correctamente"

    def test_create_account_invalid_email(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/account", params={
                "username": "testuser",
                "passwd":   "securepass123",
                "email":    "not-an-email",
            })
            assert resp.status_code == 400
            assert "Email" in resp.json()["detail"]

    def test_create_account_short_username(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/account", params={
                "username": "ab",
                "passwd":   "securepass123",
                "email":    "test@example.com",
            })
            assert resp.status_code == 400
            assert "Username" in resp.json()["detail"]

    def test_create_account_long_username(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/account", params={
                "username": "a" * 33,
                "passwd":   "securepass123",
                "email":    "test@example.com",
            })
            assert resp.status_code == 400

    def test_create_account_invalid_username_chars(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/account", params={
                "username": "user name!",
                "passwd":   "securepass123",
                "email":    "test@example.com",
            })
            assert resp.status_code == 400

    def test_create_account_short_password(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/account", params={
                "username": "validuser",
                "passwd":   "short",
                "email":    "test@example.com",
            })
            assert resp.status_code == 400
            assert "contraseña" in resp.json()["detail"]

    def test_create_account_duplicate_user(self, client):
        import mysql.connector
        with patch("api.get_db") as mock_db, \
             patch("api.insert_account",
                   side_effect=mysql.connector.IntegrityError("dup")), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/account", params={
                "username": "existing",
                "passwd":   "securepass123",
                "email":    "existing@example.com",
            })
            assert resp.status_code == 400
            assert "ya existe" in resp.json()["detail"]

    def test_create_account_missing_email(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/account", params={
                "username": "testuser",
                "passwd":   "securepass123",
            })
            # email=None → no coincide con EMAIL_REGEX → 400
            assert resp.status_code == 400

    def test_create_account_missing_username(self, client):
        resp = client.post("/account", params={
            "passwd": "securepass123",
            "email":  "test@example.com",
        })
        assert resp.status_code == 422  # FastAPI validation

    def test_create_account_missing_password(self, client):
        resp = client.post("/account", params={
            "username": "testuser",
            "email":    "test@example.com",
        })
        assert resp.status_code == 422



# 2.  GET /account/{username}  — Obtener cuenta


class TestGetAccount:

    def test_get_account_ok(self, client):
        fake_account = {
            "id": 10, "username": "testuser", "email": "t@t.com",
            "balance": 500, "reputation": 0, "created_at": "2024-01-01",
        }
        fake_transfers = [{"amount": 500, "from": 1, "to": 10}]

        with patch("api.get_db") as mock_db, \
             patch("api.get_account_by_username", return_value=fake_account.copy()), \
             patch("api.get_transfer_history",    return_value=fake_transfers), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/account/testuser")

            assert resp.status_code == 200
            data = resp.json()
            assert "account" in data
            assert "transfers" in data
            # El id interno NO debe exponerse
            assert "id" not in data["account"]

    def test_get_account_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_account_by_username", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/account/ghost")
            assert resp.status_code == 404

    def test_get_account_no_id_in_response(self, client):
        """El endpoint debe eliminar el campo 'id' de la respuesta."""
        fake = {"id": 42, "username": "u", "email": "u@u.com", "balance": 0}
        with patch("api.get_db") as mock_db, \
             patch("api.get_account_by_username", return_value=fake), \
             patch("api.get_transfer_history",    return_value=[]), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/account/u")
            assert "id" not in resp.json()["account"]



# 3.  GET /token  — Login JWT


class TestGetToken:

    def _fake_cred(self):
        from argon2 import PasswordHasher
        ph = PasswordHasher()
        return {"id": 5, "password_hash": ph.hash("mypassword")}

    def test_login_by_username_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_credentials_by_username", return_value=self._fake_cred()), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/token", params={
                "username": "testuser",
                "passwd":   "mypassword",
            })
            assert resp.status_code == 200
            assert "token" in resp.json()

    def test_login_by_email_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_credentials_by_email", return_value=self._fake_cred()), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/token", params={
                "email":  "test@example.com",
                "passwd": "mypassword",
            })
            assert resp.status_code == 200

    def test_login_wrong_password(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_credentials_by_username", return_value=self._fake_cred()), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/token", params={
                "username": "testuser",
                "passwd":   "wrongpassword",
            })
            assert resp.status_code == 401

    def test_login_user_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_credentials_by_username", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/token", params={
                "username": "nobody",
                "passwd":   "pass",
            })
            assert resp.status_code == 401

    def test_login_no_username_no_email(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/token", params={"passwd": "pass"})
            assert resp.status_code == 400

    def test_token_is_valid_jwt(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_credentials_by_username", return_value=self._fake_cred()), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/token", params={
                "username": "testuser",
                "passwd":   "mypassword",
            })
            token = resp.json()["token"]
            decoded = _jwt.decode(token, _SECRET, algorithms=[_ALGO])
            assert decoded["sub"] == "5"



# 4.  GET /auth/google  y  GET /auth/github  — OAuth init


class TestOAuthInit:

    def test_auth_google_returns_url(self, client):
        resp = client.get("/auth/google")
        assert resp.status_code == 200
        url = resp.json()["url"]
        assert "accounts.google.com" in url
        assert "state=" in url

    def test_auth_github_returns_url(self, client):
        resp = client.get("/auth/github")
        assert resp.status_code == 200
        url = resp.json()["url"]
        assert "github.com/login/oauth/authorize" in url
        assert "state=" in url

    def test_auth_google_state_unique(self, client):
        r1 = client.get("/auth/google")
        r2 = client.get("/auth/google")
        state1 = r1.json()["url"].split("state=")[1].split("&")[0]
        state2 = r2.json()["url"].split("state=")[1].split("&")[0]
        assert state1 != state2

    def test_auth_github_callback_invalid_state(self, client):
        resp = client.get("/auth/github/callback", params={
            "code":  "somecode",
            "state": "invalid_state_xyz",
        })
        assert resp.status_code == 400
        assert "OAuth" in resp.json()["detail"]

    def test_auth_google_callback_invalid_state(self, client):
        resp = client.get("/auth/google/callback", params={
            "code":  "somecode",
            "state": "invalid_state_xyz",
        })
        assert resp.status_code == 400



# 5.  POST /task  — Crear tarea


class TestCreateTask:

    def _mock_cfg(self):
        mock = MagicMock()
        mock.task.deterministic = True
        mock.inputs.type = "static"
        mock.inputs.total.return_value = 10
        return mock

    def test_create_task_ok(self, client):
        with patch("api.load_config"), \
             patch("api.cfg", return_value=self._mock_cfg()), \
             patch("api.get_db") as mock_db, \
             patch("api.get_balance_from_transfers", return_value={"balance": 9999999}), \
             patch("api.insert_task", return_value=42), \
             patch("api.insert_transfer"), \
             patch("api.close_db"), \
             patch("api.Producer") as mock_prod:

            mock_prod_inst = MagicMock()
            mock_prod.return_value = mock_prod_inst
            mock_db.return_value = (MagicMock(), MagicMock())

            resp = client.post("/task", params={
                "name":        "Mi Tarea",
                "description": "Desc",
                "github_url":  "https://github.com/user/repo",
                "repo_hash":   "abc123",
            }, headers=auth_headers())

            assert resp.status_code == 200
            assert resp.json()["task_id"] == 42

    def test_create_task_insufficient_credits(self, client):
        with patch("api.load_config"), \
             patch("api.cfg", return_value=self._mock_cfg()), \
             patch("api.get_db") as mock_db, \
             patch("api.get_balance_from_transfers", return_value={"balance": 0}), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task", params={
                "name":       "Tarea",
                "github_url": "https://github.com/user/repo",
                "repo_hash":  "abc123",
            }, headers=auth_headers())
            assert resp.status_code == 403

    def test_create_task_invalid_config(self, client):
        with patch("api.load_config", side_effect=Exception("bad config")), \
             patch("api.get_db") as mock_db, \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task", params={
                "name":       "Tarea",
                "github_url": "https://github.com/user/repo",
                "repo_hash":  "abc123",
            }, headers=auth_headers())
            assert resp.status_code == 400

    def test_create_task_no_token(self, client):
        resp = client.post("/task", params={
            "name":       "Tarea",
            "github_url": "https://github.com/user/repo",
            "repo_hash":  "abc123",
        })
        assert resp.status_code == 422  # token requerido

    def test_create_task_expired_token(self, client):
        resp = client.post("/task", params={
            "name":       "Tarea",
            "github_url": "https://github.com/user/repo",
            "repo_hash":  "abc123",
        }, headers=auth_headers(EXPIRED_TOKEN))
        assert resp.status_code == 401

    def test_create_task_rabbitmq_called(self, client):
        with patch("api.load_config"), \
             patch("api.cfg", return_value=self._mock_cfg()), \
             patch("api.get_db") as mock_db, \
             patch("api.get_balance_from_transfers", return_value={"balance": 9999999}), \
             patch("api.insert_task", return_value=77), \
             patch("api.insert_transfer"), \
             patch("api.close_db"), \
             patch("api.Producer") as mock_prod:

            mock_prod_inst = MagicMock()
            mock_prod.return_value = mock_prod_inst
            mock_db.return_value = (MagicMock(), MagicMock())

            client.post("/task", params={
                "name":       "Tarea",
                "github_url": "https://github.com/user/repo",
                "repo_hash":  "abc123",
            }, headers=auth_headers())

            mock_prod_inst.publish_chunks.assert_called_once()

    def test_create_task_user_not_exist(self, client):
        with patch("api.load_config"), \
             patch("api.cfg", return_value=self._mock_cfg()), \
             patch("api.get_db") as mock_db, \
             patch("api.get_balance_from_transfers", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task", params={
                "name":       "Tarea",
                "github_url": "https://github.com/user/repo",
                "repo_hash":  "abc123",
            }, headers=auth_headers())
            assert resp.status_code == 401



# 6.  GET /task  — Listar tareas


class TestListTasks:

    def _fake_rows(self):
        return [
            {"id": 1, "name": "T1", "description": "D1",
             "github_url": "https://github.com/u/r1"},
            {"id": 2, "name": "T2", "description": "D2",
             "github_url": "https://github.com/u/r2"},
        ]

    def test_list_tasks_public_no_token(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_tasks", return_value=self._fake_rows()), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task")
            assert resp.status_code == 200
            assert len(resp.json()["result"]) == 2

    def test_list_tasks_with_token(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_tasks_by_account", return_value=[self._fake_rows()[0]]), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task", headers=auth_headers())
            assert resp.status_code == 200
            data = resp.json()["result"]
            assert len(data) == 1

    def test_list_tasks_invalid_token(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task", headers={"token": "not.a.jwt.token"})
            assert resp.status_code == 401

    def test_list_tasks_response_shape(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_tasks", return_value=self._fake_rows()), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task")
            item = resp.json()["result"][0]
            assert "task_id" in item
            assert "name" in item
            assert "description" in item
            assert "github_url" in item



# 7.  GET /task/{task_id}  — Obtener tarea concreta


class TestGetTask:

    def test_get_task_ok(self, client):
        fake = {
            "id": 1, "name": "T", "description": "D",
            "github_url": "https://github.com/u/r",
            "status": "ACTIVE", "publisher": 1,
            "repo_snapshot_hash": "abc",
        }
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=fake), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1")
            assert resp.status_code == 200
            assert resp.json()["id"] == 1

    def test_get_task_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/9999")
            assert resp.status_code == 404



# 8.  PATCH /task/{task_id}/status  — Cambiar estado


class TestChangeTaskStatus:

    def _fake_task(self, is_dynamic=False, balance=999999):
        return {
            "id": 1, "publisher": 1, "is_dynamic": is_dynamic,
            "balance": balance, "avg_cost_per_item": 0,
        }

    def test_pause_task_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_for_status_change", return_value=self._fake_task()), \
             patch("api.update_task_status"), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.patch("/task/1/status", params={"status": "PAUSED"},
                                headers=auth_headers())
            assert resp.status_code == 200
            assert resp.json()["status"] == "PAUSED"

    def test_cancel_task_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_for_status_change", return_value=self._fake_task()), \
             patch("api.update_task_status"), \
             patch("api.close_db"), \
             patch("api.Producer") as mock_prod:

            mock_prod.return_value = MagicMock()
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.patch("/task/1/status", params={"status": "CANCELLED"},
                                headers=auth_headers())
            assert resp.status_code == 200

    def test_complete_non_dynamic_task_fails(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_for_status_change",
                   return_value=self._fake_task(is_dynamic=False)), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.patch("/task/1/status", params={"status": "COMPLETE"},
                                headers=auth_headers())
            assert resp.status_code == 400

    def test_complete_dynamic_task_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_for_status_change",
                   return_value=self._fake_task(is_dynamic=True)), \
             patch("api.update_task_status"), \
             patch("api.close_db"), \
             patch("api.Producer") as mock_prod:

            mock_prod.return_value = MagicMock()
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.patch("/task/1/status", params={"status": "COMPLETE"},
                                headers=auth_headers())
            assert resp.status_code == 200

    def test_invalid_status_value(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.patch("/task/1/status", params={"status": "DELETED"},
                                headers=auth_headers())
            assert resp.status_code == 400

    def test_task_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_for_status_change", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.patch("/task/9999/status", params={"status": "PAUSED"},
                                headers=auth_headers())
            assert resp.status_code == 404

    def test_reactivate_with_no_balance_fails(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_for_status_change",
                   return_value=self._fake_task(balance=0)), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.patch("/task/1/status", params={"status": "ACTIVE"},
                                headers=auth_headers())
            assert resp.status_code == 400

    def test_no_token_returns_422(self, client):
        resp = client.patch("/task/1/status", params={"status": "PAUSED"})
        assert resp.status_code == 422



# 9.  POST /task/{task_id}/input  — Añadir inputs dinámicos


class TestAddInputs:

    def test_add_inputs_ok(self, client):
        fake_task = {"publisher": 1, "status": "ACTIVE", "is_dynamic": True}
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", side_effect=[
                 fake_task,
                 {"total_items": 5},
             ]), \
             patch("api.increment_total_items"), \
             patch("api.close_db"), \
             patch("api.Producer") as mock_prod:

            mock_prod.return_value = MagicMock()
            mock_db.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/task/1/input",
                json=["item_a", "item_b", "item_c"],
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            assert resp.json()["added"] == 3

    def test_add_inputs_not_dynamic(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",
                   return_value={"publisher": 1, "status": "ACTIVE",
                                 "is_dynamic": False}), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/input", json=["x"],
                               headers=auth_headers())
            assert resp.status_code == 400

    def test_add_inputs_task_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/9999/input", json=["x"],
                               headers=auth_headers())
            assert resp.status_code == 404

    def test_add_inputs_not_publisher(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",
                   return_value={"publisher": 99, "status": "ACTIVE",
                                 "is_dynamic": True}), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/input", json=["x"],
                               headers=auth_headers())
            assert resp.status_code == 403

    def test_add_inputs_task_not_active(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",
                   return_value={"publisher": 1, "status": "PAUSED",
                                 "is_dynamic": True}), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/input", json=["x"],
                               headers=auth_headers())
            assert resp.status_code == 400



# 10.  POST /task/{task_id}/process  — Crear proceso


class TestCreateProcess:

    def test_create_process_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",
                   return_value={"id": 1, "repo_snapshot_hash": "abc123"}), \
             patch("api.check_process_overlap", return_value=False), \
             patch("api.insert_task_subscription"), \
             patch("api.insert_process"), \
             patch("api.get_process_id_by_start_index", return_value=10), \
             patch("api.get_execution_id", return_value=None), \
             patch("api.insert_execution", return_value=55), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/process", params={
                "index":     0,
                "count":     10,
                "repo_hash": "abc123",
            }, headers=auth_headers())

            assert resp.status_code == 200
            assert resp.json()["process_id"] == 10
            assert resp.json()["execution_id"] == 55

    def test_create_process_overlap(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",
                   return_value={"id": 1, "repo_snapshot_hash": "abc123"}), \
             patch("api.check_process_overlap", return_value=True), \
             patch("api.insert_task_subscription"), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/process", params={
                "index":     0,
                "count":     10,
                "repo_hash": "abc123",
            }, headers=auth_headers())
            assert resp.status_code == 409

    def test_create_process_hash_mismatch(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",
                   return_value={"id": 1, "repo_snapshot_hash": "real_hash"}), \
             patch("api.check_process_overlap", return_value=False), \
             patch("api.insert_task_subscription"), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/process", params={
                "index":     0,
                "count":     10,
                "repo_hash": "wrong_hash",
            }, headers=auth_headers())
            assert resp.status_code == 409

    def test_create_process_negative_index(self, client):
        with patch("api.get_db") as mock_db, patch("api.close_db"):
            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/process", params={
                "index":     -1,
                "count":     10,
                "repo_hash": "abc123",
            }, headers=auth_headers())
            assert resp.status_code == 400

    def test_create_process_task_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=None), \
             patch("api.check_process_overlap", return_value=False), \
             patch("api.insert_task_subscription"), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/9999/process", params={
                "index":     0,
                "count":     5,
                "repo_hash": "abc",
            }, headers=auth_headers())
            assert resp.status_code == 404

    def test_create_process_execution_already_exists(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",
                   return_value={"id": 1, "repo_snapshot_hash": "abc123"}), \
             patch("api.check_process_overlap", return_value=False), \
             patch("api.insert_task_subscription"), \
             patch("api.insert_process"), \
             patch("api.get_process_id_by_start_index", return_value=10), \
             patch("api.get_execution_id", return_value=88), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post("/task/1/process", params={
                "index":     0,
                "count":     10,
                "repo_hash": "abc123",
            }, headers=auth_headers())
            assert resp.status_code == 409



# 11.  GET /task/{task_id}/process  — Proceso por índice


class TestGetProcessByIndex:

    def test_get_process_ok(self, client):
        fake = {
            "id": 5,
            "input_start_index":     0,
            "input_end_index":       9,
            "canonical_execution_id": None,
        }
        with patch("api.get_db") as mock_db, \
             patch("api.get_process_by_index", return_value=fake), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1/process", params={"index": 3})
            assert resp.status_code == 200
            assert resp.json()["process_id"] == 5

    def test_get_process_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_process_by_index", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1/process", params={"index": 999})
            assert resp.status_code == 404

    def test_get_process_missing_index_param(self, client):
        resp = client.get("/task/1/process")
        assert resp.status_code == 422



# 12.  GET /task/{task_id}/process/{process_id}  — Proceso concreto


class TestGetProcess:

    def test_get_process_by_id_ok(self, client):
        fake = {
            "input_start_index":     0,
            "input_end_index":       9,
            "canonical_execution_id": 3,
        }
        with patch("api.get_db") as mock_db, \
             patch("api.get_process", return_value=fake), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1/process/5")
            assert resp.status_code == 200
            data = resp.json()["result"][0]
            assert data["input_start_index"] == 0



# 13.  POST /task/{task_id}/process/{process_id}/execution  — Crear ejecución


class TestCreateExecution:

    def test_create_execution_deterministic_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.begin_transaction"), \
             patch("api.get_task_fields",
                   return_value={"is_deterministic": True, "status": "ACTIVE"}), \
             patch("api.get_execution_id", return_value=None), \
             patch("api.insert_execution", return_value=77), \
             patch("api.close_db"):

            mock_conn = MagicMock()
            mock_db.return_value = (mock_conn, MagicMock())
            resp = client.post("/task/1/process/5/execution",
                               headers=auth_headers())
            assert resp.status_code == 200
            assert resp.json()["execution_id"] == 77

    def test_create_execution_task_not_active(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.begin_transaction"), \
             patch("api.get_task_fields",
                   return_value={"is_deterministic": True, "status": "PAUSED"}), \
             patch("api.close_db"):

            mock_conn = MagicMock()
            mock_db.return_value = (mock_conn, MagicMock())
            resp = client.post("/task/1/process/5/execution",
                               headers=auth_headers())
            assert resp.status_code == 200  # devuelve error en el body (diseño original)
            assert "error" in resp.json()

    def test_create_execution_not_deterministic_already_processed(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.begin_transaction"), \
             patch("api.get_task_fields",
                   return_value={"is_deterministic": False, "status": "ACTIVE"}), \
             patch("api.get_execution_id", side_effect=[88, None]), \
             patch("api.close_db"):

            mock_conn = MagicMock()
            mock_db.return_value = (mock_conn, MagicMock())
            resp = client.post("/task/1/process/5/execution",
                               headers=auth_headers())
            assert resp.status_code == 409

    def test_create_execution_duplicate_for_worker(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.begin_transaction"), \
             patch("api.get_task_fields",
                   return_value={"is_deterministic": True, "status": "ACTIVE"}), \
             patch("api.get_execution_id", return_value=55), \
             patch("api.close_db"):

            mock_conn = MagicMock()
            mock_db.return_value = (mock_conn, MagicMock())
            resp = client.post("/task/1/process/5/execution",
                               headers=auth_headers())
            assert resp.status_code == 409

    def test_create_execution_task_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.begin_transaction"), \
             patch("api.get_task_fields", return_value=None), \
             patch("api.close_db"):

            mock_conn = MagicMock()
            mock_db.return_value = (mock_conn, MagicMock())
            resp = client.post("/task/1/process/5/execution",
                               headers=auth_headers())
            assert resp.status_code == 404



# 14.  POST …/execution/{execution_id}/result  — Subir resultados


class TestUploadResult:

    def _fake_task_row(self):
        return {
            "publisher":       1,
            "status":          "ACTIVE",
            "is_deterministic": True,
        }

    def _fake_process(self):
        return {
            "id":                1,
            "input_start_index": 0,
            "input_end_index":   9,
            "canonical_execution_id": None,
        }

    def test_upload_result_ok(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=self._fake_task_row()), \
             patch("api.get_execution_id", return_value={"id": 1}), \
             patch("api.get_process",      return_value=self._fake_process()), \
             patch("api.save_upload_file", new_callable=AsyncMock,
                   return_value=99), \
             patch("api.update_complete_execution", return_value=10.0), \
             patch("api.handle_deterministic_payment"), \
             patch("api.get_balance",      return_value=999999), \
             patch("api.get_task_fields"),  \
             patch("api.set_task_metrics"), \
             patch("api.close_db"):

            # Necesitamos reparchar get_task_fields con side_effect
            pass  # test simplificado, ver abajo

        # Test con side_effect correcto
        task_calls = [self._fake_task_row(),
                      {"avg_cost_per_item": 0, "total_items_processed": 0, "sum_sq_cost": 0}]
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields",         side_effect=task_calls), \
             patch("api.get_execution_id",         return_value={"id": 1}), \
             patch("api.get_process",              return_value=self._fake_process()), \
             patch("api.save_upload_file",         new_callable=AsyncMock, return_value=99), \
             patch("api.update_complete_execution", return_value=10.0), \
             patch("api.handle_deterministic_payment"), \
             patch("api.get_balance",              return_value=999999), \
             patch("api.set_task_metrics"), \
             patch("api.pause_tasks_by_account"), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            file_content = b"resultado del proceso"
            resp = client.post(
                "/task/1/process/1/execution/1/result",
                data={
                    "cpu_cycles": 1000000,
                    "ram_avg":    512.0,
                    "vram_avg":   0.0,
                    "tdp_w":      0.0,
                },
                files={"file": ("output.txt", io.BytesIO(file_content),
                                "text/plain")},
                headers=auth_headers(),
            )
            assert resp.status_code == 200

    def test_upload_result_no_execution_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=self._fake_task_row()), \
             patch("api.get_execution_id", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post(
                "/task/1/process/1/execution/1/result",
                data={"cpu_cycles": 1000, "ram_avg": 512.0},
                files={"file": ("out.txt", io.BytesIO(b"data"), "text/plain")},
                headers=auth_headers(),
            )
            assert resp.status_code == 404

    def test_upload_result_task_not_found(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post(
                "/task/9999/process/1/execution/1/result",
                data={"cpu_cycles": 1000, "ram_avg": 512.0},
                files={"file": ("out.txt", io.BytesIO(b"data"), "text/plain")},
                headers=auth_headers(),
            )
            assert resp.status_code == 404

    def test_upload_result_task_paused_non_publisher(self, client):
        """Si la tarea está pausada y el usuario NO es el publisher, retorna status."""
        paused_task = {"publisher": 99, "status": "PAUSED", "is_deterministic": True}
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_fields", return_value=paused_task), \
             patch("api.get_execution_id", return_value={"id": 1}), \
             patch("api.get_process",      return_value=self._fake_process()), \
             patch("api.save_upload_file", new_callable=AsyncMock, return_value=99), \
             patch("api.update_complete_execution", return_value=10.0), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.post(
                "/task/1/process/1/execution/1/result",
                data={"cpu_cycles": 1000, "ram_avg": 512.0},
                files={"file": ("out.txt", io.BytesIO(b"data"), "text/plain")},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "paused"



# 15.  GET /task/{task_id}/confirm  — Obtener proceso a verificar


class TestGetConfirm:

    def test_confirm_pending_process(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_process_pending_confirmation",
                   return_value={"id": 7}), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1/confirm", headers=auth_headers())
            assert resp.status_code == 200
            assert resp.json()["process_id"] == 7

    def test_confirm_nothing_to_verify(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_process_pending_confirmation", return_value=None), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1/confirm", headers=auth_headers())
            assert resp.status_code == 200
            assert resp.json()["status"] == "nothing_to_verify"

    def test_confirm_requires_auth(self, client):
        resp = client.get("/task/1/confirm")
        assert resp.status_code == 422



# 16.  GET /task/{task_id}/output  — Obtener resultados


class TestGetOutputs:

    def _fake_rows(self):
        return [
            {
                "original_name": "result.txt",
                "mime_type":     "text/plain",
                "size":          100,
                "hash_sha256":   "aabbccdd" + "0" * 56,
                "created_at":    "2024-01-01",
                "account_id":    1,
            }
        ]

    def test_get_outputs_metadata(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_output_files", return_value=self._fake_rows()), \
             patch("api.close_db"), \
             patch("os.path.exists", return_value=False):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1/output")
            assert resp.status_code == 200
            result = resp.json()["result"]
            assert len(result) == 1
            assert result[0]["original_name"] == "result.txt"

    def test_get_outputs_no_files(self, client):
        with patch("api.get_db") as mock_db, \
             patch("api.get_task_output_files", return_value=[]), \
             patch("api.close_db"):

            mock_db.return_value = (MagicMock(), MagicMock())
            resp = client.get("/task/1/output")
            assert resp.status_code == 404

    def test_get_outputs_download_zip(self, client):
        hash_val = "aa" + "0" * 62
        rows = [{
            "original_name": "result.txt",
            "mime_type":     "text/plain",
            "size":          50,
            "hash_sha256":   hash_val,
            "created_at":    "2024-01-01",
            "account_id":    1,
        }]

        with patch("api.get_db") as mock_db, \
            patch("api.get_task_output_files", return_value=rows), \
            patch("api.close_db"), \
            patch("os.path.exists", return_value=True), \
            patch("zipfile.ZipFile") as mock_zf:

            mock_zip_instance = MagicMock()
            mock_zf.return_value.__enter__ = MagicMock(return_value=mock_zip_instance)
            mock_zf.return_value.__exit__ = MagicMock(return_value=False)
            mock_db.return_value = (MagicMock(), MagicMock())

            resp = client.get("/task/1/output", params={"download": "true"})
            assert resp.status_code == 200



# 17.  GET /metrics  — Prometheus


class TestMetrics:

    def test_metrics_returns_text(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "p2pcn" in resp.text or "python" in resp.text

    def test_metrics_content_type(self, client):
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers.get("content-type", "")



# 18.  Seguridad general — JWT


class TestJWTSecurity:

    def test_expired_token_rejected(self, client):
        resp = client.get("/task/1/confirm", headers={"token": EXPIRED_TOKEN})
        assert resp.status_code == 401

    def test_malformed_token_rejected(self, client):
        resp = client.get("/task/1/confirm",
                          headers={"token": "not.a.valid.token"})
        assert resp.status_code == 401

    def test_missing_token_returns_422(self, client):
        resp = client.get("/task/1/confirm")
        assert resp.status_code == 422

    def test_token_with_wrong_secret_rejected(self, client):
        bad_token = _jwt.encode(
            {"sub": "1", "exp": datetime.utcnow() + timedelta(hours=1)},
            "wrongsecret",
            algorithm="HS256",
        )
        resp = client.get("/task/1/confirm", headers={"token": bad_token})
        assert resp.status_code == 401
