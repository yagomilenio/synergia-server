import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Evitar que create_pool se ejecute al importar db_oracle
import oracledb as _real_oracledb
from unittest.mock import MagicMock

mock_oracledb = MagicMock()
mock_oracledb.NUMBER = _real_oracledb.NUMBER
mock_oracledb.IntegrityError = _real_oracledb.IntegrityError
sys.modules["oracledb"] = mock_oracledb

from db.db_oracle import *

# Restaurar el oracledb real para las conexiones del fixture
sys.modules["oracledb"] = _real_oracledb
import oracledb

import pytest


#variables comunes


NEW_VALID_HASH = "2f82328547c7c017a428dce74bef0b4e22b8db350c512a84902a6ca4106823c2"
NEW_VALID_COMMIT = "acb9a43974e6cc97789d678479877c03c995982b"



# Fixtures


@pytest.fixture(scope="session")
def db():
    """Conexión directa para el setup de tests (no usa el pool)."""
    conn = oracledb.connect(
        user=os.environ.get("ORACLE_USER_TEST"),
        password=os.environ.get("ORACLE_PASSWD_TEST"),
        dsn=os.environ.get("ORACLE_DSN_TEST"),
    )
    cursor = conn.cursor()
    yield conn, cursor
    cursor.close()
    conn.close()


@pytest.fixture(autouse=True)
def rollback(db):
    """Cada test corre en una transacción que se revierte al acabar."""
    conn, _ = db
    yield
    conn.rollback()


@pytest.fixture
def cursor(db):
    _, cursor = db
    return cursor


@pytest.fixture
def system_ids(cursor):
    """IDs de las cuentas sistema (deben existir en la BD antes de los tests)."""
    ids = {}
    for key, username in [("mint",    "SYSTEM_MINT"),
                           ("fees",    "SYSTEM_FEES")]:
        cursor.execute(
            "SELECT id FROM account WHERE username = :1", (username,)
        )
        cursor.rowfactory = lambda *args: dict(zip([d[0].lower() for d in cursor.description], args))
        row = cursor.fetchone()
        assert row, f"Cuenta sistema {username} no encontrada"
        ids[key] = row["id"]
    return ids


@pytest.fixture
def account(cursor):
    """Crea una cuenta de prueba y devuelve su id."""
    return insert_account(cursor, "test_user", "test@example.com")


@pytest.fixture
def account2(cursor):
    return insert_account(cursor, "test_user2", "test2@example.com")


@pytest.fixture
def task(cursor, account, system_ids):
    """Crea una tarea de prueba y devuelve su id."""
    insert_transfer(cursor, system_ids["mint"], account, 1_000_000)
    return insert_task(
        cursor,
        "Test Task", "desc", "https://github.com/test/repo",
        account, True, False, 100, NEW_VALID_HASH, NEW_VALID_COMMIT
    )


@pytest.fixture
def process(cursor, task):
    """Crea un proceso de prueba y devuelve su id."""
    insert_process(cursor, task, 0, 10, NEW_VALID_HASH, NEW_VALID_COMMIT)
    return get_process_id_by_start_index(cursor, task, 0)


@pytest.fixture
def execution(cursor, task, process, account):
    """Crea una ejecución de prueba y devuelve su id."""
    return insert_execution(cursor, task, process, account, output=True)








# Tests: Account


class TestAccount:

    def test_insert_account(self, cursor):
        account_id = insert_account(cursor, "nuevo_user", "nuevo@example.com")
        assert account_id is not None
        assert isinstance(account_id, int)

    def test_insert_account_duplicate_username(self, cursor):
        insert_account(cursor, "dup_user", "dup1@example.com")
        with pytest.raises(oracledb.IntegrityError):
            insert_account(cursor, "dup_user", "dup2@example.com")

    def test_insert_account_duplicate_email(self, cursor):
        insert_account(cursor, "user_a", "mismo@example.com")
        with pytest.raises(oracledb.IntegrityError):
            insert_account(cursor, "user_b", "mismo@example.com")

    def test_get_account_by_id(self, cursor, account):
        row = get_account_by_id(cursor, account)
        assert row is not None
        assert row["username"] == "test_user"
        assert row["email"] == "test@example.com"

    def test_get_account_by_id_not_found(self, cursor):
        row = get_account_by_id(cursor, 999999999)
        assert row is None

    def test_get_account_by_username(self, cursor, account):
        row = get_account_by_username(cursor, "test_user")
        assert row is not None
        assert row["id"] == account

    def test_get_account_by_username_not_found(self, cursor):
        row = get_account_by_username(cursor, "no_existe")
        assert row is None

    def test_get_account_by_email(self, cursor, account):
        row = get_account_by_email(cursor, "test@example.com")
        assert row is not None
        assert row["id"] == account

    def test_insert_auth_local_credential(self, cursor, account):
        insert_auth_local_credential(cursor, account, "hashed_password_123")
        cred = get_credentials_by_username(cursor, "test_user")
        assert cred is not None
        assert cred["password_hash"] == "hashed_password_123"

    def test_get_credentials_by_email(self, cursor, account):
        insert_auth_local_credential(cursor, account, "hashed_pw")
        cred = get_credentials_by_email(cursor, "test@example.com")
        assert cred is not None
        assert cred["password_hash"] == "hashed_pw"

    def test_get_credentials_not_found(self, cursor):
        assert get_credentials_by_username(cursor, "no_existe") is None

    def test_set_email_verified(self, cursor, account):
        insert_auth_local_credential(cursor, account, "hash")
        set_email_verified(cursor, account)
        cred = get_credentials_by_username(cursor, "test_user")
        assert cred["verified"] == 1

    def test_insert_auth_provider_account(self, cursor, account):
        insert_auth_provider_account(cursor, account, 2)  # 2 = GOOGLE
        cursor.execute(
            "SELECT 1 FROM auth_provider_account "
            "WHERE account_id = :1 AND provider_id = 2",
            (account,)
        )
        assert cursor.fetchone() is not None

    def test_insert_auth_provider_account_ignore_duplicate(self, cursor, account):
        insert_auth_provider_account(cursor, account, 2)
        insert_auth_provider_account(cursor, account, 2)  # INSERT condicional, no debe lanzar



# Tests: Transfers y balance


class TestTransfers:

    def test_insert_transfer_updates_balance(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 500)
        assert float(get_balance(cursor, account)) == 500.0

    def test_insert_transfer_debits_sender(self, cursor, account, system_ids):
        before = float(get_balance(cursor, system_ids["mint"]))
        insert_transfer(cursor, system_ids["mint"], account, 100)
        after = float(get_balance(cursor, system_ids["mint"]))
        assert after == before - 100

    def test_get_balance_zero(self, cursor, account):
        assert float(get_balance(cursor, account)) == 0.0

    def test_get_transfer_history(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 100)
        insert_transfer(cursor, system_ids["mint"], account, 200)
        history = get_transfer_history(cursor, account)
        assert len(history) >= 2

    def test_get_transfer_history_empty(self, cursor, account):
        assert get_transfer_history(cursor, account) == []

    def test_get_balance_from_transfers(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 1000)
        row = get_balance_from_transfers(cursor, account)
        assert row is not None
        assert float(row["balance"]) == 1000.0



# Tests: Task


class TestTask:

    def test_insert_task(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 1_000_000)
        task_id = insert_task(
            cursor, "Mi Tarea", "desc", "https://github.com/x/y",
            account, True, False, 50, "hash123", "commit123"
        )
        assert task_id is not None
        assert isinstance(task_id, int)

    def test_get_task_fields(self, cursor, task):
        row = get_task_fields(cursor, task, ["id", "name", "status"])
        assert row is not None
        assert row["name"] == "Test Task"
        assert row["status"] == "ACTIVE"

    def test_get_task_fields_invalid_field(self, cursor, task):
        with pytest.raises(ValueError):
            get_task_fields(cursor, task, ["DROP TABLE account"])

    def test_get_tasks_by_account(self, cursor, account, task):
        rows = get_tasks_by_account(cursor, account)
        assert any(r["id"] == task for r in rows)

    def test_get_tasks(self, cursor, task):
        rows = get_tasks(cursor)
        assert any(r["id"] == task for r in rows)

    def test_get_tasks_status(self, cursor, task):
        rows = get_tasks_status(cursor)
        assert any(r["id"] == task for r in rows)

    def test_update_task_status_paused(self, cursor, task):
        update_task_status(cursor, task, "PAUSED")
        assert get_task_fields(cursor, task, ["status"])["status"] == "PAUSED"

    def test_update_task_status_cancelled(self, cursor, task):
        update_task_status(cursor, task, "CANCELLED")
        assert get_task_fields(cursor, task, ["status"])["status"] == "CANCELLED"

    def test_increment_total_items(self, cursor, task):
        increment_total_items(cursor, task, 10)
        assert get_task_fields(cursor, task, ["total_items"])["total_items"] == 110

    def test_pause_tasks_by_account(self, cursor, account, task):
        pause_tasks_by_account(cursor, account)
        assert get_task_fields(cursor, task, ["status"])["status"] == "PAUSED"

    def test_set_task_metrics(self, cursor, task):
        set_task_metrics(cursor, task, 42.5, 10, 100.0)
        row = get_task_fields(cursor, task, ["avg_cost_per_item", "total_items_processed"])
        assert float(row["avg_cost_per_item"]) == pytest.approx(42.5)
        assert row["total_items_processed"] == 10

    def test_update_task_hash(self, cursor, task):
        
        update_task_hash(cursor, task, NEW_VALID_HASH, NEW_VALID_COMMIT)
        row = get_task_fields(cursor, task, ["repo_snapshot_hash", "repo_commit"])
        assert row["repo_snapshot_hash"] == NEW_VALID_HASH
        assert row["repo_commit"] == NEW_VALID_COMMIT

    def test_get_task_progress(self, cursor, task):
        row = get_task_progress(cursor, task)
        assert row is not None
        assert "total_items" in row

    def test_get_task_for_status_change(self, cursor, task, account):
        row = get_task_for_status_change(cursor, task, account)
        assert row is not None
        assert row["id"] == task



# Tests: Process


class TestProcess:

    def test_insert_process(self, cursor, task):
        insert_process(cursor, task, 0, 10, NEW_VALID_HASH, NEW_VALID_COMMIT)
        pid = get_process_id_by_start_index(cursor, task, 0)
        assert pid is not None

    def test_insert_process_with_value(self, cursor, task):
        insert_process(cursor, task, 0, 1, NEW_VALID_HASH, NEW_VALID_COMMIT, value="palabra")
        pid = get_process_id_by_start_index(cursor, task, 0)
        assert pid is not None

    def test_insert_process_auto_index(self, cursor, task):
        """Con index=None el sistema calcula el siguiente índice libre."""
        insert_process(cursor, task, 0, 5, NEW_VALID_HASH, NEW_VALID_COMMIT)
        insert_process(cursor, task, None, 5, NEW_VALID_HASH, NEW_VALID_COMMIT)
        pid = get_process_id_by_start_index(cursor, task, 5)
        assert pid is not None

    def test_check_process_overlap_no_overlap(self, cursor, task):
        insert_process(cursor, task, 0, 10, NEW_VALID_HASH, NEW_VALID_COMMIT)
        assert check_process_overlap(cursor, task, 10, 10) is False

    def test_check_process_overlap_with_overlap(self, cursor, task):
        insert_process(cursor, task, 0, 10,NEW_VALID_HASH, NEW_VALID_COMMIT)
        assert check_process_overlap(cursor, task, 5, 10) is True

    def test_get_process(self, cursor, task, process):
        row = get_process(cursor, task, process)
        assert row is not None
        assert row["input_start_index"] == 0
        assert row["input_end_index"] == 9

    def test_get_process_not_found(self, cursor, task):
        assert get_process(cursor, task, 999999999) is None

    def test_get_process_by_index(self, cursor, task, process):
        row = get_process_by_index(cursor, task, 5)
        assert row is not None
        assert row["id"] == process

    def test_get_process_by_index_not_found(self, cursor, task):
        assert get_process_by_index(cursor, task, 999) is None

    def test_get_last_process_by_task(self, cursor, task, process):
        row = get_last_process_by_task(cursor, task)
        assert row is not None
        assert row["repo_snapshot_hash"] == NEW_VALID_HASH

    def test_get_last_process_by_task_no_processes(self, cursor, task):
        """Con una tarea sin procesos debe devolver None."""
        new_task_id = insert_task(
            cursor, "Sin procesos", "d", "https://github.com/x/y",
            # reutilizamos publisher de task fixture (account ya existe en la sesión)
            # pero necesitamos el account_id; lo sacamos de la tarea existente
            get_task_fields(cursor, task, ["publisher"])["publisher"],
            True, False, 0, "nohash", "nocommit"
        )
        assert get_last_process_by_task(cursor, new_task_id) is None

    def test_get_all_processes_by_task(self, cursor, task, process):
        rows = get_all_processes_by_task(cursor, task)
        assert any(r["id"] == process for r in rows)

    def test_delete_process_by_task(self, cursor, task, process):
        delete_process_by_task(cursor, task, [process])
        assert get_process(cursor, task, process) is None

    def test_delete_process_empty_list(self, cursor, task):
        """Lista vacía no debe lanzar excepción."""
        delete_process_by_task(cursor, task, [])



# Tests: Execution


class TestExecution:

    def test_insert_execution(self, cursor, task, process, account):
        exec_id = insert_execution(cursor, task, process, account, output=True)
        assert exec_id is not None
        assert isinstance(exec_id, int)

    def test_insert_execution_without_output(self, cursor, task, process, account):
        result = insert_execution(cursor, task, process, account, output=False)
        assert result is None

    def test_get_execution_by_id(self, cursor, task, process, execution, account):
        row = get_execution_by_id(cursor, task, process, execution)
        assert row is not None
        assert row["account_id"] == account
        assert row["status"] == "PENDING"

    def test_get_execution_id_pending(self, cursor, task, process, execution, account):
        exec_id = get_execution_id(cursor, task, process, account, status="PENDING")
        assert exec_id == execution

    def test_get_execution_id_not_found(self, cursor, task, process):
        assert get_execution_id(cursor, task, process, status="SUCCESS") is None

    def test_update_complete_execution(self, cursor, task, process, execution):
        file_id = insert_file(cursor, "result.txt", "text/plain", 100, "a" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        row = get_execution_by_id(cursor, task, process, execution)
        assert row["status"] == "SUCCESS"
        assert row["result_file_id"] == file_id

    def test_get_account_ids_with_successful_execution(self, cursor, task, process, execution, account):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "b" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        rows = get_account_ids_with_successful_execution(cursor, task, process)
        assert any(r["account_id"] == account for r in rows)

    def test_cancel_incomplete_executions(self, cursor, task, process, execution, account):
        cancel_incomplete_executions(cursor, task, account)
        row = get_execution_by_id(cursor, task, process, execution)
        assert row["status"] == "CANCELLED"

    def test_get_executions_by_process(self, cursor, task, process, execution):
        rows = get_executions_by_process(cursor, task, process)
        assert any(r["id"] == execution for r in rows)

    def test_is_process_successfully_terminated_false(self, cursor, task, process, account):
        result = is_process_successfully_terminated(cursor, task, 0, 9, account)
        assert result is False

    def test_is_process_successfully_terminated_true(self, cursor, task, process, execution, account):
        file_id = insert_file(cursor, "ok.txt", "text/plain", 10, "c" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        result = is_process_successfully_terminated(cursor, task, 0, 9, account)
        assert result is True

    def test_get_processes_and_execution_without_completed(self, cursor, task, process, execution, account):
        cancel_incomplete_executions(cursor, task, account)
        rows = get_processes_and_execution_without_completed_executions(cursor, task, account)
        assert any(r["id"] == process for r in rows)



# Tests: Task Subscription


class TestTaskSubscription:

    def test_insert_task_subscription(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        cursor.execute(
            "SELECT 1 FROM task_subscription "
            "WHERE account_id = :1 AND task_id = :2",
            (account, task)
        )
        assert cursor.fetchone() is not None

    def test_insert_task_subscription_ignore_duplicate(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        insert_task_subscription(cursor, account, task)  # INSERT condicional, sin excepción

    def test_inc_confirmation_counter(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        inc_confirmation_counter(cursor, account, task)
        assert get_last_task_confirmation(cursor, account, task) == 1

    def test_reset_confirmation_counter(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        inc_confirmation_counter(cursor, account, task)
        inc_confirmation_counter(cursor, account, task)
        reset_confirmation_counter(cursor, account, task)
        assert get_last_task_confirmation(cursor, account, task) == 0

    def test_get_last_task_confirmation_not_subscribed(self, cursor, account, task):
        assert get_last_task_confirmation(cursor, account, task) is None

    def test_get_subscribed_tasks(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        rows = get_subscribed_tasks(cursor, account)
        assert any(r["id"] == task for r in rows)

    def test_delete_task_subscription(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        result = delete_task_subscription(cursor, task, account)
        assert result is True
        assert get_last_task_confirmation(cursor, account, task) is None



# Tests: Canonical Process


class TestCanonical:

    def test_update_canonical_process(self, cursor, task, process, execution):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "c" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)
        assert get_canonical_execution_id(cursor, task, process) == execution

    def test_update_canonical_process_only_once(self, cursor, task, process, execution, account2):
        """Una vez fijado el canónico, update_canonical_process no lo sobreescribe."""
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "d" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        exec2 = insert_execution(cursor, task, process, account2, output=True)
        update_complete_execution(cursor, file_id, task, process, exec2)
        update_canonical_process(cursor, task, process, exec2)  # no debe cambiar

        assert get_canonical_execution_id(cursor, task, process) == execution

    def test_recalculate_canonical_process(self, cursor, task, process, execution, account2):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "e" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)

        exec2 = insert_execution(cursor, task, process, account2, output=True)
        update_complete_execution(cursor, file_id, task, process, exec2)

        recalculate_canonical_process(cursor, task, process)
        assert get_canonical_execution_id(cursor, task, process) is not None

    def test_get_canonical_confirmation_count(self, cursor, task, process, execution, account2):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "f" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        exec2 = insert_execution(cursor, task, process, account2, output=True)
        update_complete_execution(cursor, file_id, task, process, exec2)

        count = get_canonical_confirmation_count(cursor, task, process, execution)
        assert count == 2

    def test_get_canonical_id_by_result_id(self, cursor, task, process, execution, account):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "g" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        result = get_canonical_id_by_result_id(cursor, task, process, file_id)
        assert result == account

    def test_recalculate_canonical_confirmations(self, cursor, task, process, execution, account, account2):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "h" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        exec2 = insert_execution(cursor, task, process, account2, output=True)
        update_complete_execution(cursor, file_id, task, process, exec2)

        rows = recalculate_canonical_confirmations(cursor, execution, task, process, account)
        assert any(r["account_id"] == account2 for r in rows)

    def test_get_process_pending_confirmation(self, cursor, task, process, execution, account2):
        """Un worker distinto del canónico debe ver el proceso como pendiente de verificar."""
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "i" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)
        set_task_metrics(cursor, task, 10.0, 1, 0.0)

        row = get_process_pending_confirmation(cursor, task, account2)
        assert row is not None
        assert row["id"] == process

    def test_get_process_to_confirmate(self, cursor, task, process, execution, account2):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "j" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        row = get_process_to_confirmate(cursor, task, account2)
        assert row is not None



# Tests: File


class TestFile:

    def test_insert_file(self, cursor):
        file_id = insert_file(cursor, "test.txt", "text/plain", 1024, "a" * 64)
        assert file_id is not None
        assert isinstance(file_id, int)

    def test_get_file_id(self, cursor):
        hash_val = "b" * 64
        insert_file(cursor, "test.txt", "text/plain", 512, hash_val)
        assert get_file_id(cursor, hash_val) is not None

    def test_get_file_id_not_found(self, cursor):
        assert get_file_id(cursor, "0" * 64) is None

    def test_insert_file_duplicate_hash(self, cursor):
        hash_val = "c" * 64
        insert_file(cursor, "f1.txt", "text/plain", 100, hash_val)
        with pytest.raises(oracledb.IntegrityError):
            insert_file(cursor, "f2.txt", "text/plain", 200, hash_val)



# Tests: Reputation


class TestReputation:

    def test_recalculate_reputation_no_executions(self, cursor, account, system_ids):
        recalculate_reputation(cursor, account, system_ids["fees"])
        row = get_account_by_id(cursor, account)
        assert row["reputation"] == 0

    def test_recalculate_reputation_with_canonical(self, cursor, account, account2,
                                                    task, process, execution, system_ids):
        """Worker que coincide con el canónico debe mantener reputación >= 0."""
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "k" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        insert_transfer(cursor, system_ids["mint"], account2, 1000)
        insert_transfer(cursor, account2, account, 100, task, process)

        recalculate_reputation(cursor, account, system_ids["fees"])
        row = get_account_by_id(cursor, account)
        assert row["reputation"] >= 0



# Tests: Output Files


class TestOutputFiles:

    def test_get_task_output_files_empty(self, cursor, task):
        assert get_task_output_files(cursor, task) == []

    def test_get_task_output_files(self, cursor, task, process, execution):
        file_id = insert_file(cursor, "out.txt", "text/plain", 50, "l" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        rows = get_task_output_files(cursor, task)
        assert len(rows) >= 1
        assert any(r["hash_sha256"] == "l" * 64 for r in rows)

    def test_get_task_output_files_canonical_only(self, cursor, task, process, execution, account2):
        """Con canonical_only=True solo devuelve ficheros de la ejecución canónica."""
        file_id = insert_file(cursor, "out.txt", "text/plain", 50, "m" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        exec2 = insert_execution(cursor, task, process, account2, output=True)
        file_id2 = insert_file(cursor, "out2.txt", "text/plain", 50, "n" * 64)
        update_complete_execution(cursor, file_id2, task, process, exec2)

        rows = get_task_output_files(cursor, task, canonical_only=True)
        hashes = [r["hash_sha256"] for r in rows]
        assert "m" * 64 in hashes
        assert "n" * 64 not in hashes

    def test_get_task_output_files_by_process(self, cursor, task, process, execution):
        file_id = insert_file(cursor, "out.txt", "text/plain", 50, "o" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        rows = get_task_output_files(cursor, task, process_id=process)
        assert len(rows) >= 1



# Tests: Canonical payment helpers


class TestCanonicalPayment:

    def test_get_canonical_payment(self, cursor, task, process, execution, account, account2, system_ids):
        """Verifica que se recupera el pago al canónico correctamente."""
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "p" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        insert_transfer(cursor, system_ids["mint"], account2, 500)
        publisher_id = get_task_fields(cursor, task, ["publisher"])["publisher"]
        insert_transfer(cursor, publisher_id, account, 50, task, process)

        row = get_canonical_payment(cursor, publisher_id, task, process, execution)
        assert row is not None
        assert row["account_id"] == account

    def test_get_previous_canonical_payment(self, cursor, task, process, execution, account, account2, system_ids):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "q" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        publisher_id = get_task_fields(cursor, task, ["publisher"])["publisher"]
        insert_transfer(cursor, publisher_id, account2, 50, task, process)

        row = get_previous_canonical_payment(cursor, task, process, publisher_id)
        assert row is not None
        assert row["to_account_id"] == account2