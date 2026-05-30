import oracledb
import config



pool = oracledb.create_pool(
    user=config.ORACLE_USER,
    password=config.ORACLE_PASSWD,
    dsn=config.ORACLE_DSN,
    min=2,
    max=10,
    increment=1
)

def _rowfactory(cursor):
    cols = [d[0].lower() for d in cursor.description]
    return lambda *args: dict(zip(cols, args))

def get_db():
    conn = pool.acquire()
    cursor = conn.cursor()
    return conn, cursor

def close_db(conn, cursor):
    try:
        if cursor:
            cursor.close()
    except Exception:
        pass
    try:
        if conn:
            pool.release(conn)
    except Exception:
        pass

def begin_transaction(conn):
    conn.begin()

def _exec(cursor, sql, params=None):
    if params is not None:
        cursor.execute(sql, params)
    else:
        cursor.execute(sql)
    if cursor.description:
        cursor.rowfactory = _rowfactory(cursor)


def check_process_overlap(cursor, task_id, index, count):
    end_index = index + count - 1
    sql = """
        SELECT 1
        FROM process
        WHERE task_id = :1
        AND NOT (input_end_index < :2 OR input_start_index > :3)
        FETCH FIRST 1 ROWS ONLY
    """
    _exec(cursor, sql, (task_id, index, end_index))
    return cursor.fetchone() is not None

def insert_account(cursor, username, email):
    var_id = cursor.var(oracledb.NUMBER)
    cursor.execute(
        "INSERT INTO account (username, email) VALUES (:1, :2) RETURNING id INTO :3",
        (username, email, var_id)
    )
    return int(var_id.getvalue()[0])

def insert_auth_local_credential(cursor, account_id, hashed_passwd):
    cursor.execute(
        "INSERT INTO auth_local_credential (account_id, password_hash) VALUES (:1, :2)",
        (account_id, hashed_passwd)
    )

def insert_transfer(cursor, from_account_id, to_account_id, amount, task_id=None, process_id=None):
    cursor.execute("""
        INSERT INTO transfer (from_account_id, to_account_id, amount, task_id, process_id)
        VALUES (:1, :2, :3, :4, :5)
    """, (from_account_id, to_account_id, amount, task_id, process_id))

    update_balance(cursor, -amount, from_account_id)
    update_balance(cursor, amount, to_account_id)

def update_balance(cursor, balance, account_id):
    cursor.execute("""
        UPDATE account
        SET balance = balance + :1
        WHERE id = :2
    """, (balance, account_id))

def get_account_by_id(cursor, account_id, lock=False):
    sql = """
        SELECT id, username, email, created_at, reputation, balance
        FROM account
        WHERE id = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (account_id,))
    return cursor.fetchone()

def get_account_by_username(cursor, username, lock=False):
    sql = """
        SELECT id, username, email, created_at, reputation, balance
        FROM account
        WHERE username = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (username,))
    return cursor.fetchone()

def get_transfer_history(cursor, account_id, lock=False):
    sql = """
        SELECT t.id, t.from_account_id, fa.username AS from_user,
               t.to_account_id, ta.username AS to_user,
               t.task_id, t.process_id, t.amount, t.created_at
        FROM transfer t
        LEFT JOIN account fa ON t.from_account_id = fa.id
        LEFT JOIN account ta ON t.to_account_id = ta.id
        WHERE t.from_account_id = :1 OR t.to_account_id = :2
        ORDER BY t.created_at DESC
    """

    if lock:
        sql += " FOR UPDATE OF t"

    _exec(cursor, sql, (account_id, account_id))
    return cursor.fetchall()

def get_credentials_by_username(cursor, username, lock=False):
    sql = """
        SELECT password_hash, a.id, alc.verified
        FROM account a
        JOIN auth_local_credential alc ON alc.account_id = a.id
        WHERE username = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (username,))
    return cursor.fetchone()

def get_credentials_by_email(cursor, email, lock=False):
    sql = """
        SELECT password_hash, a.id, alc.verified FROM account a
        JOIN auth_local_credential alc ON alc.account_id = a.id
        WHERE email = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (email,))
    return cursor.fetchone()

def get_balance(cursor, account_id, lock=False):
    sql = """
        SELECT balance
        FROM account
        WHERE id = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (account_id,))
    row = cursor.fetchone()
    return row['balance'] if row else None

def get_balance_from_transfers(cursor, account_id, lock=False):
    sql = """
        SELECT COALESCE(SUM(t_in.amount),0) - COALESCE(SUM(t_out.amount),0) AS balance
        FROM account a
        LEFT JOIN transfer t_in  ON t_in.to_account_id = a.id
        LEFT JOIN transfer t_out ON t_out.from_account_id = a.id
        WHERE a.id = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (account_id,))
    return cursor.fetchone()

def insert_task(cursor, name, description, github_url, account_id, deterministic, dynamic, total_items, repo_hash, repo_commit):
    var_id = cursor.var(oracledb.NUMBER)
    cursor.execute("""
        INSERT INTO task (name, description, github_url, publisher, is_deterministic, is_dynamic, total_items, repo_snapshot_hash, repo_commit)
        VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9) RETURNING id INTO :10
    """, (name, description, github_url, account_id, deterministic, dynamic, total_items, repo_hash, repo_commit, var_id))
    return int(var_id.getvalue()[0])

def get_tasks_by_account(cursor, account_id, lock=False):
    sql = """
        SELECT id, name, description, github_url, status
        FROM task
        WHERE publisher = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (account_id,))
    return cursor.fetchall()

def get_tasks(cursor, lock=False):
    sql = """
        SELECT id, name, description, github_url, status
        FROM task
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql)
    return cursor.fetchall()

def get_tasks_status(cursor, lock=False):
    sql="""
        SELECT id, status 
        FROM task 
    """

    if lock:
        sql += " FOR UPDATE"

    _exec(cursor, sql)
    return cursor.fetchall()

def get_task_fields(cursor, task_id, fields, lock=False):
    allowed = {"id","status","publisher","name","description","github_url",
        "avg_cost_per_item","total_items_processed","sum_sq_cost","is_deterministic",
        "is_dynamic","total_items","repo_snapshot_hash","repo_commit"}

    safe_fields = [f for f in fields if f in allowed]
    if not safe_fields:
        raise ValueError("Invalid fields")

    sql = f"SELECT {', '.join(safe_fields)} FROM task WHERE id = :1"
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id,))
    return cursor.fetchone()

def increment_total_items(cursor, task_id, count=1):
    cursor.execute("""
        UPDATE task
        SET total_items = total_items + :1
        WHERE id = :2
    """, (count, task_id))

def get_task_for_status_change(cursor, task_id, account_id, lock=False):
    sql = """
        SELECT t.id, t.avg_cost_per_item, a.balance, t.is_dynamic,  t.status
        FROM task t
        JOIN account a ON a.id = t.publisher
        WHERE t.id = :1 AND t.publisher = :2
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, account_id))
    return cursor.fetchone()

def update_task_status(cursor, task_id, status):
    cursor.execute(
        "UPDATE task SET status = :1 WHERE id = :2",
        (status, task_id)
    )

def pause_tasks_by_account(cursor, account_id):
    cursor.execute("""
        UPDATE task
        SET status = 'PAUSED'
        WHERE publisher = :1 AND status not in ('COMPLETED', 'CANCELLED')
    """, (account_id,))

def get_process_id_by_start_index(cursor, task_id, start_index, lock=False):
    sql = """
        SELECT id FROM process
        WHERE task_id = :1
        AND input_start_index = :2
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, start_index))
    row = cursor.fetchone()
    return row['id'] if row else None

def get_process(cursor, task_id, process_id, lock=False):
    sql = """
        SELECT input_start_index, input_end_index, canonical_execution_id, repo_snapshot_hash, repo_commit
        FROM process
        WHERE task_id = :1 AND id = :2
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, process_id))
    return cursor.fetchone()

def insert_task_subscription(cursor, account_id, task_id):
    cursor.execute("""
        INSERT INTO task_subscription (account_id, task_id)
        SELECT :account_id, :task_id FROM dual
        WHERE NOT EXISTS (
            SELECT 1 FROM task_subscription
            WHERE account_id = :account_id AND task_id = :task_id
        )
    """, {
        "account_id": account_id,
        "task_id": task_id
    })

def get_last_task_confirmation(cursor, account_id, task_id, lock=False):
    sql = """
        SELECT chunks_since_last_verification
        FROM task_subscription
        WHERE account_id = :1 AND task_id = :2
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (account_id, task_id))
    row = cursor.fetchone()
    return row['chunks_since_last_verification'] if row else None

def get_process_to_confirmate(cursor, task_id, account_id, lock=False):
    sql = """
        SELECT p.id
        FROM process p
        JOIN execution e ON e.task_id = p.task_id
            AND e.process_id = p.id
            AND e.id = p.canonical_execution_id
        WHERE p.task_id = :task_id
        AND p.canonical_execution_id IS NOT NULL
        AND e.account_id != :account_id
        AND NOT EXISTS (
            SELECT 1 FROM execution
            WHERE task_id = p.task_id AND process_id = p.id
            AND account_id = :account_id AND status = 'SUCCESS'
        )
        FETCH FIRST 1 ROWS ONLY
    """
    if lock:
        sql = sql.replace("FETCH FIRST 1 ROWS ONLY", "FOR UPDATE")

    _exec(cursor, sql, {
        "task_id": task_id,
        "account_id": account_id
    })
    return cursor.fetchone()

def is_process_successfully_terminated(cursor, task_id, start, end, user_id, lock=False):
    sql = """
        SELECT e.status FROM execution e
        JOIN process p ON p.task_id = e.task_id AND p.id = e.process_id
        WHERE p.task_id = :1
        AND p.input_start_index <= :2
        AND p.input_end_index >= :3
        AND e.account_id = :4
        AND e.status = 'SUCCESS'
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, start, end, user_id))
    return cursor.fetchone() is not None

def cancel_incomplete_executions(cursor, task_id, account_id):
    cursor.execute("""
        UPDATE execution
        SET status = 'CANCELLED'
        WHERE account_id = :1
        AND task_id = :2
        AND status IN ('PENDING', 'RUNNING')
    """, (account_id, task_id))

def get_processes_and_execution_without_completed_executions(cursor, task_id, account_id, lock=False):
    sql = """
        SELECT p.id
        FROM process p
        WHERE p.task_id = :1
        AND (
            SELECT COUNT(*) FROM execution e
            WHERE e.task_id = p.task_id
            AND e.process_id = p.id
        ) = 1
        AND EXISTS (
            SELECT 1 FROM execution e
            WHERE e.task_id = p.task_id
            AND e.process_id = p.id
            AND e.account_id = :2
            AND e.status = 'CANCELLED'
        )
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, account_id))
    return cursor.fetchall()


def delete_process_by_task(cursor, task_id, ids_array):
    if not ids_array:
        return  

    format_strings = ','.join(f':{i+2}' for i in range(len(ids_array)))

    sql = f"""
        DELETE FROM process
        WHERE task_id = :1
        AND id IN ({format_strings})
    """

    cursor.execute(sql, (task_id, *ids_array))

def insert_process(cursor, task_id, index, count, repo_hash, repo_commit, value=None):
    if count is None:
        count = 1

    if index is None:
        _exec(cursor, """
            SELECT MAX(input_end_index) AS max_end FROM process WHERE task_id = :1
        """, (task_id,))
        row = cursor.fetchone()
        last_end = row['max_end'] if row['max_end'] is not None else -1
        index = last_end + 1

    end_index = index + count - 1

    cursor.execute("""
        INSERT INTO process (task_id, input_start_index, input_end_index, repo_snapshot_hash, repo_commit, input_value)
        VALUES (:1, :2, :3, :4, :5, :6)
    """, (task_id, index, end_index, repo_hash, repo_commit, value))

    return index

def get_last_process_by_task(cursor, task_id, lock=False):
    sql = """
        SELECT repo_snapshot_hash
        FROM process
        WHERE task_id = :1
        ORDER BY id DESC
        FETCH FIRST 1 ROWS ONLY
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id,))
    return cursor.fetchone()

def get_execution_id(cursor, task_id, process_id, account_id=None, status='SUCCESS', lock=False):
    if account_id is not None:
        sql = """
            SELECT id FROM execution
            WHERE task_id = :1 AND process_id = :2
            AND account_id = :3 AND status = :4
            FETCH FIRST 1 ROWS ONLY
        """
        params = (task_id, process_id, account_id, status)
    else:
        sql = """
            SELECT id FROM execution
            WHERE task_id = :1 AND process_id = :2
            AND status = :3
            FETCH FIRST 1 ROWS ONLY
        """
        params = (task_id, process_id, status)

    if lock:
        sql = sql.replace("FETCH FIRST 1 ROWS ONLY", "FOR UPDATE")
    _exec(cursor, sql, params)
    row = cursor.fetchone()
    return row['id'] if row else None

def get_execution_by_id(cursor, task_id, process_id, execution_id, lock=False):
    sql = """
        SELECT account_id, status, result_file_id, start_date, end_date
        FROM execution
        WHERE task_id = :1 AND process_id = :2 AND id = :3
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, process_id, execution_id))
    return cursor.fetchone()

def insert_execution(cursor, task_id, process_id, account_id, output=False):
    cursor.execute("""
        INSERT INTO execution (task_id, process_id, account_id)
        VALUES (:1, :2, :3)
    """, (task_id, process_id, account_id))

    if output:
        _exec(cursor, """
            SELECT MAX(id) AS id FROM execution
            WHERE task_id = :1 AND process_id = :2
        """, (task_id, process_id))
        execution_id = cursor.fetchone()['id']
        return execution_id


def get_file_id(cursor, hash):
    _exec(cursor, """
        SELECT id FROM result_file WHERE hash_sha256 = :1
    """, (hash,))
    row = cursor.fetchone()
    return row['id'] if row else None

def insert_file(cursor, name, content_type, size, hash):
    var_id = cursor.var(oracledb.NUMBER)
    cursor.execute("""
        INSERT INTO result_file (original_name, mime_type, file_size, hash_sha256)
        VALUES (:1, :2, :3, :4) RETURNING id INTO :5
    """, (name, content_type, size, hash, var_id))
    return int(var_id.getvalue()[0])

from datetime import datetime

def update_complete_execution(cursor, file_id, task_id, process_id, execution_id):
    cursor.execute("""
        UPDATE execution
        SET result_file_id = :1,
            status = 'SUCCESS',
            end_date = CURRENT_TIMESTAMP
        WHERE task_id = :2 AND process_id = :3 AND id = :4
    """, (file_id, task_id, process_id, execution_id))

    _exec(cursor, """
        SELECT start_date, end_date
        FROM execution
        WHERE task_id = :1 AND process_id = :2 AND id = :3
    """, (task_id, process_id, execution_id))

    row = cursor.fetchone()
    if not row or not row['start_date'] or not row['end_date']:
        return None

    duration = (row['end_date'] - row['start_date']).total_seconds()
    return duration

def update_canonical_process(cursor, task_id, process_id, execution_id):
    cursor.execute("""
        UPDATE process SET canonical_execution_id = :1
        WHERE task_id = :2 AND id = :3 AND canonical_execution_id IS NULL
    """, (execution_id, task_id, process_id))

def recalculate_canonical_process(cursor, task_id, process_id):
    cursor.execute("""
        UPDATE process p
        SET canonical_execution_id = (
            SELECT id FROM (
                SELECT e2.id,
                       ROW_NUMBER() OVER (ORDER BY e2.end_date ASC) AS rn
                FROM execution e2
                WHERE e2.task_id = p.task_id AND e2.process_id = p.id
                AND e2.result_file_id = (
                    SELECT result_file_id FROM (
                        SELECT e.result_file_id,
                               ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC, MIN(e.id) ASC) AS rn2
                        FROM execution e
                        WHERE e.task_id = p.task_id AND e.process_id = p.id AND e.status = 'SUCCESS'
                        GROUP BY e.result_file_id
                    ) WHERE rn2 = 1
                )
            ) WHERE rn = 1
        )
        WHERE p.task_id = :1 AND p.id = :2
    """, (task_id, process_id))

def get_canonical_payment(cursor, task_publisher, task_id, process_id, canonical_execution_id, lock=False):
    sql = """
        SELECT *
        FROM (
            SELECT e.account_id, t.amount AS canonical_amount
            FROM execution e
            JOIN transfer t
            ON t.task_id = e.task_id
            AND t.process_id = e.process_id
            AND t.to_account_id = e.account_id
            AND t.from_account_id = :1
            WHERE e.task_id = :2
            AND e.process_id = :3
            AND e.id = :4
            ORDER BY e.id
        )
        WHERE ROWNUM = 1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_publisher, task_id, process_id, canonical_execution_id))
    return cursor.fetchone()

def get_canonical_confirmation_count(cursor, task_id, process_id, canonical_execution_id, lock=False):
    sql = """
        SELECT COUNT(*) AS n
        FROM execution e
        JOIN execution canon ON canon.task_id = e.task_id
            AND canon.process_id = e.process_id
            AND canon.id = :1
        WHERE e.task_id = :2 AND e.process_id = :3
        AND e.status = 'SUCCESS'
        AND e.result_file_id = canon.result_file_id
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (canonical_execution_id, task_id, process_id))
    row = cursor.fetchone()
    return row["n"] if row else 1

def get_previous_canonical_payment(cursor, task_id, process_id, task_publisher, lock=False):
    sql = """
        SELECT to_account_id, amount FROM transfer
        WHERE task_id = :task_id AND process_id = :process_id
        AND to_account_id != :task_publisher
        AND from_account_id = :task_publisher
        FETCH FIRST 1 ROWS ONLY
    """
    if lock:
        sql = sql.replace("FETCH FIRST 1 ROWS ONLY", "FOR UPDATE")

    _exec(cursor, sql, {
        "task_id": task_id,
        "process_id": process_id,
        "task_publisher": task_publisher
    })
    return cursor.fetchone()

def reset_confirmation_counter(cursor, account_id, task_id):
    cursor.execute("""
        UPDATE task_subscription
        SET chunks_since_last_verification = 0
        WHERE account_id = :1 AND task_id = :2
    """, (account_id, task_id))

def get_subscribed_tasks(cursor, account_id, lock=False):
    sql = """
        SELECT t.id AS id, name, description, github_url, status
        FROM task_subscription ts
        JOIN task t ON ts.task_id = t.id
        WHERE ts.account_id = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (account_id,))
    return cursor.fetchall()

def inc_confirmation_counter(cursor, account_id, task_id):
    cursor.execute("""
        UPDATE task_subscription
        SET chunks_since_last_verification = chunks_since_last_verification + 1
        WHERE account_id = :1 AND task_id = :2
    """, (account_id, task_id))

def get_account_ids_with_successful_execution(cursor, task_id, process_id):
    _exec(cursor, """
        SELECT DISTINCT account_id FROM execution
        WHERE task_id = :1 AND process_id = :2 AND status = 'SUCCESS'
    """, (task_id, process_id))
    return cursor.fetchall()

def recalculate_reputation(cursor, account_id, fees_id):

    cursor.execute("""
        UPDATE account
        SET reputation = COALESCE((
            SELECT rep FROM (
                SELECT
                    COALESCE(SUM(
                        CASE
                            WHEN e.result_file_id = canon.result_file_id
                            THEN t_canon.amount
                            ELSE 0
                        END
                    ), 0)
                    * 100 /
                    NULLIF(COALESCE(SUM(t_canon.amount), 0), 0) AS rep

                FROM execution e
                JOIN process p
                    ON p.task_id = e.task_id
                AND p.id = e.process_id
                JOIN execution canon
                    ON canon.id = p.canonical_execution_id
                AND canon.task_id = p.task_id
                AND canon.process_id = p.id
                JOIN transfer t_canon
                    ON t_canon.task_id = e.task_id
                AND t_canon.process_id = e.process_id
                AND t_canon.to_account_id = canon.account_id
                AND t_canon.from_account_id != :fees_id

                WHERE e.account_id = :account_id
                AND e.status = 'SUCCESS'
                AND canon.account_id != :account_id
            ) subquery
        ), 0)
        WHERE id = :account_id
    """, {
        "fees_id": fees_id,
        "account_id": account_id
    })

def set_task_metrics(cursor, task_id, avg, n, sq):
    cursor.execute("""
        UPDATE task
        SET avg_cost_per_item = :1,
            total_items_processed = :2,
            sum_sq_cost = :3
        WHERE id = :4
    """, (avg, n, sq, task_id))

def get_process_pending_confirmation(cursor, task_id, account_id):
    _exec(cursor, """
        SELECT p.id
        FROM process p
        JOIN execution e_canon ON e_canon.task_id = p.task_id
            AND e_canon.process_id = p.id
            AND e_canon.id = p.canonical_execution_id
        JOIN account a_canon ON a_canon.id = e_canon.account_id
        JOIN task t ON t.id = p.task_id
        WHERE p.task_id = :task_id
        AND p.canonical_execution_id IS NOT NULL
        AND e_canon.account_id != :user_id
        AND NOT EXISTS (
            SELECT 1 FROM execution
            WHERE task_id = p.task_id AND process_id = p.id
            AND account_id = :user_id AND status = 'SUCCESS'
        )
        ORDER BY (
            -- 1. REPUTACION BAJA DEL CANONICO
            (1.0 / NULLIF(a_canon.reputation + 1, 0))

            -- 2. COSTE ANOMALO usando avg y desviacion estandar ya calculados en task
            + ABS(
                (
                    SELECT NVL(SUM(tr.amount), 0)
                    FROM transfer tr
                    WHERE tr.task_id = p.task_id
                    AND tr.process_id = p.id
                    AND tr.to_account_id = e_canon.account_id
                )
                - t.avg_cost_per_item * (p.input_end_index - p.input_start_index + 1)
            ) / NULLIF(
                    SQRT(t.sum_sq_cost / NULLIF(t.total_items_processed, 0))  -- desviacion estandar
                    * (p.input_end_index - p.input_start_index + 1),
                0)

            -- 3. POCOS CONFIRMADORES
            + (1.0 / NULLIF(
                (SELECT COUNT(*) FROM execution e3
                WHERE e3.task_id = p.task_id AND e3.process_id = p.id
                AND e3.status = 'SUCCESS'),
                0
            ))

        ) DESC
        FETCH FIRST 1 ROWS ONLY
    """, {
        "task_id": task_id,
        "user_id": account_id
    })
    return cursor.fetchone()


def get_task_output_files(cursor, task_id, process_id=None, canonical_only=False):
    conditions = ["e.task_id = :1"]
    params = [task_id]


    if process_id is not None:
        conditions.append("e.process_id = :2")
        params.append(process_id)

    if canonical_only:
        conditions.append("p.CANONICAL_EXECUTION_ID = e.id")

    where = " AND ".join(conditions)
    _exec(cursor, f"""
        SELECT f.original_name, f.mime_type, f.file_size as "size", f.hash_sha256, f.created_at, e.account_id
        FROM result_file f
        JOIN execution e ON f.id = e.result_file_id
        JOIN process p ON p.task_id = e.task_id AND p.id = e.process_id
        WHERE {where}
    """, tuple(params))

    return cursor.fetchall()
def get_canonical_id_by_result_id(cursor, task_id, process_id, file_id, lock=False):
    sql = """
        SELECT e.account_id AS id
        FROM execution e
        WHERE task_id = :1 AND process_id = :2 AND result_file_id = :3
        ORDER BY e.end_date ASC
        FETCH FIRST 1 ROWS ONLY
    """
    if lock:
        sql = sql.replace("FETCH FIRST 1 ROWS ONLY", "FOR UPDATE")
    _exec(cursor, sql, (task_id, process_id, file_id))
    row = cursor.fetchone()
    return row['id'] if row else None

def get_canonical_execution_id(cursor, task_id, process_id, lock=False):
    sql = """
        SELECT canonical_execution_id
        FROM process
        WHERE task_id = :1 AND id = :2
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, process_id))
    row = cursor.fetchone()
    return row['canonical_execution_id'] if row else None

def recalculate_canonical_confirmations(cursor, new_canonical_execution_id, task_id, process_id, new_canonical_id):
    _exec(cursor, """
        SELECT DISTINCT e.account_id
        FROM execution e
        JOIN execution canon ON canon.task_id = e.task_id
            AND canon.process_id = e.process_id
            AND canon.id = :1
        WHERE e.task_id = :2 AND e.process_id = :3
        AND e.status = 'SUCCESS'
        AND e.result_file_id = canon.result_file_id
        AND e.account_id != :4
    """, (new_canonical_execution_id, task_id, process_id, new_canonical_id))
    return cursor.fetchall()

def get_process_by_index(cursor, task_id, index, lock=False):
    sql = """
        SELECT id, input_start_index, input_end_index, canonical_execution_id
        FROM process
        WHERE task_id = :1
        AND input_start_index <= :2
        AND input_end_index > :2
        FETCH FIRST 1 ROWS ONLY
    """
    if lock:
        sql = sql.replace("FETCH FIRST 1 ROWS ONLY", "FOR UPDATE")
    _exec(cursor, sql, (task_id, index))
    return cursor.fetchone()

def get_account_by_email(cursor, email, lock=False):
    sql = """
        SELECT id, username, email FROM account WHERE email = :1
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (email,))
    return cursor.fetchone()

def insert_auth_provider_account(cursor, account_id, provider_id):
    cursor.execute("""
        INSERT INTO auth_provider_account (account_id, provider_id)
        SELECT :account_id, :provider_id FROM dual
        WHERE NOT EXISTS (
            SELECT 1 
            FROM auth_provider_account
            WHERE account_id = :account_id
            AND provider_id = :provider_id
        )
    """, {
        "account_id": account_id,
        "provider_id": provider_id
    })

def update_task_hash(cursor, task_id, repo_hash, repo_commit):
    cursor.execute(
        "UPDATE task SET repo_snapshot_hash = :1, repo_commit = :2 WHERE id = :3",
        (repo_hash, repo_commit, task_id)
    )

def get_all_processes_by_task(cursor, task_id, lock=False):
    sql = """
    SELECT
        p.id,
        p.input_start_index,
        p.input_end_index,
        p.canonical_execution_id,
        COUNT(
        DISTINCT CASE 
            WHEN e.status = 'SUCCESS'
            AND e.result_file_id = canon.result_file_id
            THEN e.id
        END
    ) AS execution_count
    FROM process p
    LEFT JOIN execution e ON e.process_id = p.id AND e.task_id = p.task_id
    LEFT JOIN execution canon ON canon.id = p.canonical_execution_id
    WHERE p.task_id = :1
    GROUP BY p.id, p.input_start_index, p.input_end_index, p.canonical_execution_id
    ORDER BY p.input_start_index ASC
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id,))
    return cursor.fetchall()

def get_task_progress(cursor, task_id, lock=False):
    sql = """
        SELECT
            t.total_items,
            t.is_dynamic,
            COALESCE(SUM(p.input_end_index - p.input_start_index + 1), 0) AS items_procesados
        FROM task t
        LEFT JOIN process p ON p.task_id = t.id
            AND p.canonical_execution_id IS NOT NULL
        WHERE t.id = :1
        GROUP BY t.id, t.total_items, t.is_dynamic
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id,))
    return cursor.fetchone()


def get_executions_by_process(cursor, task_id, process_id, lock=False):
    sql = """
        SELECT
            e.id,
            e.status,
            a.username,
            f.hash_sha256,
            e.result_file_id,
            p.canonical_execution_id,
            CASE
                WHEN e.status = 'SUCCESS'
                AND e.result_file_id IS NOT NULL
                AND e.result_file_id = canon.result_file_id
                THEN 1 ELSE 0
            END AS coincide
        FROM execution e
        JOIN account a ON a.id = e.account_id
        JOIN process p ON p.task_id = e.task_id AND p.id = e.process_id
        LEFT JOIN result_file f ON f.id = e.result_file_id
        LEFT JOIN execution canon ON canon.id = p.canonical_execution_id
                                 AND canon.task_id = e.task_id
                                 AND canon.process_id = e.process_id
        WHERE e.task_id = :1 AND e.process_id = :2
        ORDER BY e.id ASC
    """
    if lock:
        sql += " FOR UPDATE"
    _exec(cursor, sql, (task_id, process_id))
    return cursor.fetchall()


def insert_task_resource(cursor, task_id, resource_id):
    cursor.execute("""
        MERGE INTO resource_task rt
        USING (
            SELECT :1 AS resource_id, :2 AS task_id FROM dual
        ) src
        ON (rt.resource_id = src.resource_id AND rt.task_id = src.task_id)
        WHEN NOT MATCHED THEN
            INSERT (resource_id, task_id)
            VALUES (src.resource_id, src.task_id)
    """, (resource_id, task_id))

def insert_task_requirement(cursor, task_id, metric_name, min_value):
    cursor.execute("""
        INSERT INTO task_requirement (task_id, metric_id, min_value)
        SELECT :1, id, :2
        FROM resource_metric
        WHERE name = :3
    """, (task_id, min_value, metric_name))

def set_email_verified(cursor, account_id):
    cursor.execute(
        "UPDATE auth_local_credential SET verified = 1 WHERE account_id = :1",
        (account_id,)
    )

def delete_task_subscription(cursor, task_id, user_id):
    cursor.execute(
        "DELETE FROM task_subscription WHERE task_id = :task_id AND account_id = :user_id",
        {"task_id": task_id, "user_id": user_id}
    )
    return cursor.rowcount > 0