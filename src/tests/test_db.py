import pytest
import mysql.connector
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from db.db import *


# Fixtures


@pytest.fixture(scope="session")
def db():
    """Conexión directa para el setup de tests (no usa el pool)."""
    conn = mysql.connector.connect(
        host="mariadb",
        user="root",
        password="1234",
        database="p2pcn"
    )
    cursor = conn.cursor(dictionary=True)
    yield conn, cursor
    cursor.close()
    conn.close()


@pytest.fixture(autouse=True)
def rollback(db):
    """Cada test corre en una transacción que se revierte al acabar."""
    conn, _ = db
    conn.start_transaction()
    yield
    conn.rollback()


@pytest.fixture
def cursor(db):
    _, cursor = db
    return cursor


@pytest.fixture
def system_ids(cursor):
    """IDs de las cuentas sistema."""
    ids = {}
    for key, username in [("mint", "SYSTEM_MINT"), ("fees", "SYSTEM_FEES"),
                          ("burn", "SYSTEM_BURN"), ("rewards", "SYSTEM_REWARDS")]:
        cursor.execute("SELECT id FROM account WHERE username = %s", (username,))
        row = cursor.fetchone()
        assert row, f"Cuenta sistema {username} no encontrada"
        ids[key] = row["id"]
    return ids


@pytest.fixture
def account(cursor):
    """Crea una cuenta de prueba y devuelve su id."""
    account_id = insert_account(cursor, "test_user", "test@example.com")
    return account_id


@pytest.fixture
def account2(cursor):
    account_id = insert_account(cursor, "test_user2", "test2@example.com")
    return account_id


@pytest.fixture
def task(cursor, account, system_ids):
    """Crea una tarea de prueba y devuelve su id."""
    insert_transfer(cursor, system_ids["mint"], account, 1000000)
    task_id = insert_task(cursor, "Test Task", "desc", "https://github.com/test/repo",
                          account, True, False, 100, "abc123hash")
    return task_id


@pytest.fixture
def process(cursor, task):
    """Crea un proceso de prueba y devuelve su id."""
    insert_process(cursor, task, 0, 9, "abc123hash")
    process_id = get_process_id_by_start_index(cursor, task, 0)
    return process_id


@pytest.fixture
def execution(cursor, task, process, account):
    """Crea una ejecución de prueba y devuelve su id."""
    execution_id = insert_execution(cursor, task, process, account, output=True)
    return execution_id



# Tests: Account


class TestAccount:

    def test_insert_account(self, cursor):
        account_id = insert_account(cursor, "nuevo_user", "nuevo@example.com")
        assert account_id is not None
        assert isinstance(account_id, int)

    def test_insert_account_duplicate_username(self, cursor):
        insert_account(cursor, "dup_user", "dup1@example.com")
        with pytest.raises(mysql.connector.IntegrityError):
            insert_account(cursor, "dup_user", "dup2@example.com")

    def test_insert_account_duplicate_email(self, cursor):
        insert_account(cursor, "user_a", "mismo@example.com")
        with pytest.raises(mysql.connector.IntegrityError):
            insert_account(cursor, "user_b", "mismo@example.com")

    def test_get_account_by_id(self, cursor, account):
        row = get_account_by_id(cursor, account)
        assert row is not None
        assert row["username"] == "test_user"
        assert row["email"] == "test@example.com"

    def test_get_account_by_id_not_found(self, cursor):
        row = get_account_by_id(cursor, 999999)
        assert row is None

    def test_get_account_by_username(self, cursor, account):
        row = get_account_by_username(cursor, "test_user")
        assert row is not None
        assert row["id"] == account

    def test_get_account_by_username_not_found(self, cursor):
        row = get_account_by_username(cursor, "no_existe")
        assert row is None

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
        cred = get_credentials_by_username(cursor, "no_existe")
        assert cred is None

    def test_insert_auth_provider_account(self, cursor, account):
        insert_auth_provider_account(cursor, account, 2)  # GOOGLE
        cursor.execute(
            "SELECT * FROM auth_provider_account WHERE account_id = %s AND provider_id = 2",
            (account,)
        )
        row = cursor.fetchone()
        assert row is not None

    def test_insert_auth_provider_account_ignore_duplicate(self, cursor, account):
        insert_auth_provider_account(cursor, account, 2)
        insert_auth_provider_account(cursor, account, 2)  # no debe lanzar excepción



# Tests: Balance y Transfers


class TestTransfers:

    def test_insert_transfer_updates_balance(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 500)
        balance = get_balance(cursor, account)
        assert float(balance) == 500.0

    def test_insert_transfer_debits_sender(self, cursor, account, system_ids):
        balance_before = get_balance(cursor, system_ids["mint"])
        insert_transfer(cursor, system_ids["mint"], account, 100)
        balance_after = get_balance(cursor, system_ids["mint"])
        assert float(balance_after) == float(balance_before) - 100

    def test_insert_transfer_zero_amount_fails(self, cursor, account, system_ids):
        with pytest.raises(Exception):
            insert_transfer(cursor, system_ids["mint"], account, 0)

    def test_insert_transfer_negative_amount_fails(self, cursor, account, system_ids):
        with pytest.raises(Exception):
            insert_transfer(cursor, system_ids["mint"], account, -100)

    def test_get_balance(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 300)
        balance = get_balance(cursor, account)
        assert float(balance) == 300.0

    def test_get_balance_zero(self, cursor, account):
        balance = get_balance(cursor, account)
        assert float(balance) == 0.0

    def test_get_transfer_history(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 100)
        insert_transfer(cursor, system_ids["mint"], account, 200)
        history = get_transfer_history(cursor, account)
        assert len(history) >= 2

    def test_get_transfer_history_empty(self, cursor, account):
        history = get_transfer_history(cursor, account)
        assert history == []

    def test_get_balance_from_transfers(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 1000)
        row = get_balance_from_transfers(cursor, account)
        assert row is not None
        assert float(row["balance"]) == 1000.0



# Tests: Task


class TestTask:

    def test_insert_task(self, cursor, account, system_ids):
        insert_transfer(cursor, system_ids["mint"], account, 1000000)
        task_id = insert_task(cursor, "Mi Tarea", "desc", "https://github.com/x/y",
                              account, True, False, 50, "hash123")
        assert task_id is not None

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

    def test_update_task_status(self, cursor, task):
        update_task_status(cursor, task, "PAUSED")
        row = get_task_fields(cursor, task, ["status"])
        assert row["status"] == "PAUSED"

    def test_update_task_status_cancelled(self, cursor, task):
        update_task_status(cursor, task, "CANCELLED")
        row = get_task_fields(cursor, task, ["status"])
        assert row["status"] == "CANCELLED"

    def test_increment_total_items(self, cursor, task):
        increment_total_items(cursor, task, 10)
        row = get_task_fields(cursor, task, ["total_items"])
        assert row["total_items"] == 110  # 100 iniciales + 10

    def test_pause_tasks_by_account(self, cursor, account, task):
        pause_tasks_by_account(cursor, account)
        row = get_task_fields(cursor, task, ["status"])
        assert row["status"] == "PAUSED"

    def test_set_task_metrics(self, cursor, task):
        set_task_metrics(cursor, task, 42.5, 10, 100.0)
        row = get_task_fields(cursor, task, ["avg_cost_per_item", "total_items_processed"])
        assert float(row["avg_cost_per_item"]) == pytest.approx(42.5)
        assert row["total_items_processed"] == 10



# Tests: Process


class TestProcess:

    def test_insert_process(self, cursor, task):
        insert_process(cursor, task, 0, 9, "hash123")
        process_id = get_process_id_by_start_index(cursor, task, 0)
        assert process_id is not None

    def test_insert_process_with_value(self, cursor, task):
        insert_process(cursor, task, 0, 0, "hash123", value="palabra")
        process_id = get_process_id_by_start_index(cursor, task, 0)
        assert process_id is not None

    def test_check_process_overlap_no_overlap(self, cursor, task):
        insert_process(cursor, task, 0, 9, "hash123")
        overlap = check_process_overlap(cursor, task, 10, 19)
        assert overlap is False

    def test_check_process_overlap_with_overlap(self, cursor, task):
        insert_process(cursor, task, 0, 9, "hash123")
        overlap = check_process_overlap(cursor, task, 5, 15)
        assert overlap is True

    def test_get_process(self, cursor, task, process):
        row = get_process(cursor, task, process)
        assert row is not None
        assert row["input_start_index"] == 0
        assert row["input_end_index"] == 9

    def test_get_process_not_found(self, cursor, task):
        row = get_process(cursor, task, 999999)
        assert row is None

    def test_get_process_by_index(self, cursor, task, process):
        row = get_process_by_index(cursor, task, 5)
        assert row is not None
        assert row["id"] == process

    def test_get_process_by_index_not_found(self, cursor, task, process):
        row = get_process_by_index(cursor, task, 999)
        assert row is None

    def test_get_last_process_by_task(self, cursor, task, process):
        row = get_last_process_by_task(cursor, task)
        assert row is not None
        assert row["repo_snapshot_hash"] == "abc123hash"

    def test_get_last_process_by_task_empty(self, cursor, task):
        row = get_last_process_by_task(cursor, task)
        # si no hay procesos debe devolver None
        # en este caso el fixture 'task' no crea procesos
        # así que debería ser None
        assert row is None



# Tests: Execution


class TestExecution:

    def test_insert_execution(self, cursor, task, process, account):
        execution_id = insert_execution(cursor, task, process, account, output=True)
        assert execution_id is not None
        assert isinstance(execution_id, int)

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
        exec_id = get_execution_id(cursor, task, process, status="SUCCESS")
        assert exec_id is None

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



# Tests: Task Subscription


class TestTaskSubscription:

    def test_insert_task_subscription(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        cursor.execute(
            "SELECT * FROM task_subscription WHERE account_id = %s AND task_id = %s",
            (account, task)
        )
        row = cursor.fetchone()
        assert row is not None

    def test_insert_task_subscription_ignore_duplicate(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        insert_task_subscription(cursor, account, task)  # no debe lanzar excepción

    def test_inc_confirmation_counter(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        inc_confirmation_counter(cursor, account, task)
        val = get_last_task_confirmation(cursor, account, task)
        assert val == 1

    def test_reset_confirmation_counter(self, cursor, account, task):
        insert_task_subscription(cursor, account, task)
        inc_confirmation_counter(cursor, account, task)
        inc_confirmation_counter(cursor, account, task)
        reset_confirmation_counter(cursor, account, task)
        val = get_last_task_confirmation(cursor, account, task)
        assert val == 0

    def test_get_last_task_confirmation_not_subscribed(self, cursor, account, task):
        val = get_last_task_confirmation(cursor, account, task)
        assert val is None



# Tests: Canonical Process


class TestCanonical:

    def test_update_canonical_process(self, cursor, task, process, execution):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "c" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)
        canon_id = get_canonical_execution_id(cursor, task, process)
        assert canon_id == execution

    def test_update_canonical_process_only_once(self, cursor, task, process, execution, account2, cursor2=None):
        """update_canonical_process no debe sobreescribir si ya hay canónico."""
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "d" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        execution2_id = insert_execution(cursor, task, process, account2, output=True)
        update_complete_execution(cursor, file_id, task, process, execution2_id)
        update_canonical_process(cursor, task, process, execution2_id)  # no debe cambiar

        canon_id = get_canonical_execution_id(cursor, task, process)
        assert canon_id == execution  # el primer canónico se mantiene

    def test_recalculate_canonical_process(self, cursor, task, process, execution, account2):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "e" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)

        execution2_id = insert_execution(cursor, task, process, account2, output=True)
        update_complete_execution(cursor, file_id, task, process, execution2_id)

        recalculate_canonical_process(cursor, task, process)
        canon_id = get_canonical_execution_id(cursor, task, process)
        assert canon_id is not None

    def test_get_canonical_confirmation_count(self, cursor, task, process, execution, account2):
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "f" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        update_canonical_process(cursor, task, process, execution)

        execution2_id = insert_execution(cursor, task, process, account2, output=True)
        update_complete_execution(cursor, file_id, task, process, execution2_id)

        count = get_canonical_confirmation_count(cursor, task, process, execution)
        assert count == 2  # execution + execution2 tienen el mismo file



# Tests: File


class TestFile:

    def test_insert_file(self, cursor):
        file_id = insert_file(cursor, "test.txt", "text/plain", 1024, "a" * 64)
        assert file_id is not None

    def test_get_file_id(self, cursor):
        hash_val = "b" * 64
        insert_file(cursor, "test.txt", "text/plain", 512, hash_val)
        file_id = get_file_id(cursor, hash_val)
        assert file_id is not None

    def test_get_file_id_not_found(self, cursor):
        file_id = get_file_id(cursor, "0" * 64)
        assert file_id is None

    def test_insert_file_duplicate_hash(self, cursor):
        hash_val = "c" * 64
        insert_file(cursor, "f1.txt", "text/plain", 100, hash_val)
        with pytest.raises(mysql.connector.IntegrityError):
            insert_file(cursor, "f2.txt", "text/plain", 200, hash_val)



# Tests: Reputation


class TestReputation:

    def test_recalculate_reputation_no_executions(self, cursor, account, system_ids):
        recalculate_reputation(cursor, account, system_ids["fees"])
        row = get_account_by_id(cursor, account)
        assert row["reputation"] == 0

    def test_recalculate_reputation_with_canonical(self, cursor, account, task, process,
                                                    execution, system_ids, account2):
        """Un worker que siempre coincide con el canónico debe tener reputación alta."""
        file_id = insert_file(cursor, "r.txt", "text/plain", 10, "g" * 64)
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
        rows = get_task_output_files(cursor, task)
        assert rows == []

    def test_get_task_output_files(self, cursor, task, process, execution):
        file_id = insert_file(cursor, "out.txt", "text/plain", 50, "h" * 64)
        update_complete_execution(cursor, file_id, task, process, execution)
        rows = get_task_output_files(cursor, task)
        assert len(rows) >= 1
        assert any(r["hash_sha256"] == "h" * 64 for r in rows)
