
--  account 
CREATE SEQUENCE seq_account START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;

CREATE TABLE account (
    id         NUMBER(19)    NOT NULL,
    username   VARCHAR2(255) NOT NULL,
    email      VARCHAR2(255) NOT NULL,
    created_at TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL,
    reputation NUMBER(10)    DEFAULT 0 NOT NULL,
    balance    NUMBER(20,4)  DEFAULT 0 NOT NULL
);

ALTER TABLE account ADD CONSTRAINT pk_account  PRIMARY KEY (id);
ALTER TABLE account ADD CONSTRAINT u_email     UNIQUE (email);
ALTER TABLE account ADD CONSTRAINT u_username  UNIQUE (username);

CREATE OR REPLACE TRIGGER trg_account_bi
BEFORE INSERT ON account
FOR EACH ROW
BEGIN
    IF :NEW.id IS NULL THEN
        :NEW.id := seq_account.NEXTVAL;
    END IF;
END;
/

INSERT INTO account (username, email) VALUES ('SYSTEM_MINT',    'system_mint@internal');
INSERT INTO account (username, email) VALUES ('SYSTEM_FEES',    'system_fees@internal');


--  auth_provider 
CREATE TABLE auth_provider (
    id   NUMBER(3)    NOT NULL,
    name VARCHAR2(20) NOT NULL
);

ALTER TABLE auth_provider ADD CONSTRAINT pk_auth_provider     PRIMARY KEY (id);
ALTER TABLE auth_provider ADD CONSTRAINT u_auth_provider_name UNIQUE (name);

INSERT INTO auth_provider (id, name) VALUES (1, 'LOCAL');
INSERT INTO auth_provider (id, name) VALUES (2, 'GOOGLE');
INSERT INTO auth_provider (id, name) VALUES (3, 'GITHUB');


--  auth_provider_account 
CREATE TABLE auth_provider_account (
    account_id  NUMBER(19) NOT NULL,
    provider_id NUMBER(3)  NOT NULL
);

ALTER TABLE auth_provider_account ADD CONSTRAINT pk_auth_provider_account PRIMARY KEY (account_id, provider_id);
ALTER TABLE auth_provider_account ADD CONSTRAINT fk_apa_account  FOREIGN KEY (account_id)  REFERENCES account(id) ON DELETE CASCADE;
ALTER TABLE auth_provider_account ADD CONSTRAINT fk_apa_provider FOREIGN KEY (provider_id) REFERENCES auth_provider(id);


--  auth_local_credential 
CREATE TABLE auth_local_credential (
    account_id          NUMBER(19)    NOT NULL,
    password_hash       VARCHAR2(255) NOT NULL,
    password_updated_at TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL,
    verified            NUMBER(1)     DEFAULT 0 NOT NULL
);

ALTER TABLE auth_local_credential ADD CONSTRAINT pk_auth_local_credential PRIMARY KEY (account_id);
ALTER TABLE auth_local_credential ADD CONSTRAINT fk_alc_account  FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE;
ALTER TABLE auth_local_credential ADD CONSTRAINT chk_alc_verified CHECK (verified IN (0, 1));


--  resource_type 
CREATE SEQUENCE seq_resource_type START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;

CREATE TABLE resource_type (
    id   NUMBER(10)    NOT NULL,
    name VARCHAR2(255) NOT NULL
);

ALTER TABLE resource_type ADD CONSTRAINT pk_resource_type PRIMARY KEY (id);

CREATE OR REPLACE TRIGGER trg_resource_type_bi
BEFORE INSERT ON resource_type
FOR EACH ROW
BEGIN
    IF :NEW.id IS NULL THEN
        :NEW.id := seq_resource_type.NEXTVAL;
    END IF;
END;
/

INSERT INTO resource_type (id, name) VALUES (1, 'CPU');
INSERT INTO resource_type (id, name) VALUES (2, 'GPU');
INSERT INTO resource_type (id, name) VALUES (3, 'RAM');


--  task 
CREATE SEQUENCE seq_task START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;

CREATE TABLE task (
    id                    NUMBER(19)    NOT NULL,
    name                  VARCHAR2(255) NOT NULL,
    description           VARCHAR2(255),
    github_url            VARCHAR2(255) NOT NULL,
    created_at            TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL,
    publisher             NUMBER(19)    NOT NULL,
    status                VARCHAR2(10)  DEFAULT 'ACTIVE' NOT NULL,
    total_items_processed NUMBER(19)    DEFAULT 0,
    total_items           NUMBER(19)    DEFAULT 0,
    avg_cost_per_item     BINARY_FLOAT  DEFAULT 0,
    sum_sq_cost           BINARY_FLOAT  DEFAULT 0,
    is_deterministic      NUMBER(1)     DEFAULT 0 NOT NULL,
    is_dynamic            NUMBER(1)     DEFAULT 0 NOT NULL,
    repo_snapshot_hash    CHAR(64)      NOT NULL,
    repo_commit           CHAR(40)      NOT NULL
);

ALTER TABLE task ADD CONSTRAINT pk_task         PRIMARY KEY (id);
ALTER TABLE task ADD CONSTRAINT fk_publisher    FOREIGN KEY (publisher) REFERENCES account(id);
ALTER TABLE task ADD CONSTRAINT chk_task_status CHECK (status IN ('ACTIVE','PAUSED','COMPLETED','CANCELLED'));
ALTER TABLE task ADD CONSTRAINT chk_is_det      CHECK (is_deterministic IN (0, 1));
ALTER TABLE task ADD CONSTRAINT chk_is_dyn      CHECK (is_dynamic IN (0, 1));

CREATE OR REPLACE TRIGGER trg_task_bi
BEFORE INSERT ON task
FOR EACH ROW
BEGIN
    IF :NEW.id IS NULL THEN
        :NEW.id := seq_task.NEXTVAL;
    END IF;
END;
/

INSERT INTO task (name, description, github_url, publisher, repo_snapshot_hash, repo_commit)
VALUES (
    'Folding@home, Investigacion de enfermedades',
    'Ejecuta unidades de trabajo de Folding@home contribuyendo a la investigacion de enfermedades como el cancer y el Alzheimer.',
    'https://github.com/yagomilenio/foldingathomesynergia',
    1,
    'ed6fb7cdded6c0e4c189a58e1439e99e0a5f4887063d70438b270f36db0c6db5',
    '613d9abc9a8ec5af3e4a43ba4eabfb6c622e520c'
);


--  resource_task 
CREATE TABLE resource_task (
    resource_id NUMBER(10) NOT NULL,
    task_id     NUMBER(19) NOT NULL
);

ALTER TABLE resource_task ADD CONSTRAINT pk_resource_task PRIMARY KEY (resource_id, task_id);
ALTER TABLE resource_task ADD CONSTRAINT fk_rt_resource   FOREIGN KEY (resource_id) REFERENCES resource_type(id);
ALTER TABLE resource_task ADD CONSTRAINT fk_rt_task       FOREIGN KEY (task_id)     REFERENCES task(id) ON DELETE CASCADE;


--  resource_metric 
CREATE SEQUENCE seq_resource_metric START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;

CREATE TABLE resource_metric (
    id          NUMBER(10)    NOT NULL,
    resource_id NUMBER(10)    NOT NULL,
    name        VARCHAR2(64)  NOT NULL,
    label       VARCHAR2(128) NOT NULL,
    unit        VARCHAR2(32)  NOT NULL
);

ALTER TABLE resource_metric ADD CONSTRAINT pk_resource_metric      PRIMARY KEY (id);
ALTER TABLE resource_metric ADD CONSTRAINT uq_resource_metric_name UNIQUE (resource_id, name);
ALTER TABLE resource_metric ADD CONSTRAINT fk_metric_resource      FOREIGN KEY (resource_id) REFERENCES resource_type(id);

CREATE OR REPLACE TRIGGER trg_resource_metric_bi
BEFORE INSERT ON resource_metric
FOR EACH ROW
BEGIN
    IF :NEW.id IS NULL THEN
        :NEW.id := seq_resource_metric.NEXTVAL;
    END IF;
END;
/

INSERT INTO resource_metric (resource_id, name, label, unit) VALUES (1, 'min_threads',  'Minimo de hilos',         'hilos');
INSERT INTO resource_metric (resource_id, name, label, unit) VALUES (1, 'min_freq_mhz', 'Frecuencia minima',       'MHz');
INSERT INTO resource_metric (resource_id, name, label, unit) VALUES (2, 'min_vram_mb',  'VRAM minima',             'MB');
INSERT INTO resource_metric (resource_id, name, label, unit) VALUES (2, 'min_cuda_cc',  'Compute Capability min.', 'x.x');
INSERT INTO resource_metric (resource_id, name, label, unit) VALUES (3, 'min_ram_mb',   'RAM minima',              'MB');


--  task_requirement 
CREATE TABLE task_requirement (
    task_id   NUMBER(19)   NOT NULL,
    metric_id NUMBER(10)   NOT NULL,
    min_value BINARY_FLOAT NOT NULL
);

ALTER TABLE task_requirement ADD CONSTRAINT pk_task_requirement PRIMARY KEY (task_id, metric_id);
ALTER TABLE task_requirement ADD CONSTRAINT fk_req_task         FOREIGN KEY (task_id)   REFERENCES task(id) ON DELETE CASCADE;
ALTER TABLE task_requirement ADD CONSTRAINT fk_req_metric       FOREIGN KEY (metric_id) REFERENCES resource_metric(id);
ALTER TABLE task_requirement ADD CONSTRAINT chk_min_value       CHECK (min_value > 0);


--  task_subscription 
CREATE TABLE task_subscription (
    account_id                     NUMBER(19) NOT NULL,
    task_id                        NUMBER(19) NOT NULL,
    chunks_since_last_verification NUMBER(10) DEFAULT 0
);

ALTER TABLE task_subscription ADD CONSTRAINT pk_task_subscription PRIMARY KEY (account_id, task_id);
ALTER TABLE task_subscription ADD CONSTRAINT fk_sub_account       FOREIGN KEY (account_id) REFERENCES account(id);
ALTER TABLE task_subscription ADD CONSTRAINT fk_sub_task          FOREIGN KEY (task_id)    REFERENCES task(id) ON DELETE CASCADE;

CREATE OR REPLACE TRIGGER trg_auto_subscribe
AFTER INSERT ON account
FOR EACH ROW
BEGIN
    IF :NEW.username NOT IN ('SYSTEM_MINT','SYSTEM_FEES') THEN
        INSERT INTO task_subscription (account_id, task_id)
        SELECT :NEW.id, 1 FROM dual
        WHERE NOT EXISTS (
            SELECT 1 FROM task_subscription
            WHERE account_id = :NEW.id AND task_id = 1
        );
    END IF;
END;
/


--  result_file 
CREATE SEQUENCE seq_result_file START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;

CREATE TABLE result_file (
    id            NUMBER(19)    NOT NULL,
    original_name VARCHAR2(255) NOT NULL,
    mime_type     VARCHAR2(100),
    file_size     NUMBER(19)    NOT NULL,   -- ← renombrado
    hash_sha256   CHAR(64)      NOT NULL,
    created_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP NOT NULL
);

ALTER TABLE result_file ADD CONSTRAINT pk_result_file PRIMARY KEY (id);
ALTER TABLE result_file ADD CONSTRAINT uq_file_hash   UNIQUE (hash_sha256);

CREATE OR REPLACE TRIGGER trg_result_file_bi
BEFORE INSERT ON result_file
FOR EACH ROW
BEGIN
    IF :NEW.id IS NULL THEN
        :NEW.id := seq_result_file.NEXTVAL;
    END IF;
END;
/


--  process 
CREATE TABLE process (
    id                     NUMBER(19) NOT NULL,
    task_id                NUMBER(19) NOT NULL,
    input_start_index      NUMBER(19) NOT NULL,
    input_end_index        NUMBER(19) NOT NULL,
    input_value            CLOB,
    canonical_execution_id NUMBER(19),
    repo_snapshot_hash     CHAR(64),
    repo_commit            CHAR(40)
);

ALTER TABLE process ADD CONSTRAINT pk_process      PRIMARY KEY (task_id, id);
ALTER TABLE process ADD CONSTRAINT fk_process_task FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE;
ALTER TABLE process ADD CONSTRAINT uq_task_start   UNIQUE (task_id, input_start_index);
ALTER TABLE process ADD CONSTRAINT uq_task_end     UNIQUE (task_id, input_end_index);

CREATE OR REPLACE TRIGGER trg_process_bi
BEFORE INSERT ON process
FOR EACH ROW
BEGIN
    IF :NEW.id IS NULL THEN
        SELECT NVL(MAX(id), 0) + 1
        INTO :NEW.id
        FROM process
        WHERE task_id = :NEW.task_id;
    END IF;
END;
/


--  execution 
CREATE TABLE execution (
    id             NUMBER(19)   NOT NULL,
    task_id        NUMBER(19)   NOT NULL,
    process_id     NUMBER(19)   NOT NULL,
    account_id     NUMBER(19)   NOT NULL,
    status         VARCHAR2(10) DEFAULT 'PENDING' NOT NULL,
    result_file_id NUMBER(19),
    start_date     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP NOT NULL,
    end_date       TIMESTAMP
);

ALTER TABLE execution ADD CONSTRAINT pk_execution    PRIMARY KEY (task_id, process_id, id);
ALTER TABLE execution ADD CONSTRAINT fk_exec_process FOREIGN KEY (task_id, process_id) REFERENCES process(task_id, id) ON DELETE CASCADE;
ALTER TABLE execution ADD CONSTRAINT fk_exec_account FOREIGN KEY (account_id)     REFERENCES account(id);
ALTER TABLE execution ADD CONSTRAINT fk_exec_file    FOREIGN KEY (result_file_id) REFERENCES result_file(id);
ALTER TABLE execution ADD CONSTRAINT chk_exec_status CHECK (status IN ('PENDING','SUCCESS','FAILED','CANCELLED','PAUSED'));

CREATE OR REPLACE TRIGGER trg_execution_bi
BEFORE INSERT ON execution
FOR EACH ROW
BEGIN
    SELECT NVL(MAX(id), 0) + 1
    INTO :NEW.id
    FROM execution
    WHERE task_id    = :NEW.task_id
      AND process_id = :NEW.process_id;
END;
/

CREATE OR REPLACE TRIGGER trg_execution_bu
BEFORE UPDATE ON execution
FOR EACH ROW
BEGIN
    IF :NEW.status IN ('SUCCESS','FAILED','CANCELLED')
       AND :OLD.status NOT IN ('SUCCESS','FAILED','CANCELLED') THEN
        :NEW.end_date := CURRENT_TIMESTAMP;
    END IF;
END;
/

-- FK circular process <-> execution
ALTER TABLE process ADD CONSTRAINT fk_canonical_execution
    FOREIGN KEY (task_id, id, canonical_execution_id)
    REFERENCES execution(task_id, process_id, id);


--  transfer 
CREATE SEQUENCE seq_transfer START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;

CREATE BLOCKCHAIN TABLE transfer (
    id              NUMBER(19)   NOT NULL,
    from_account_id NUMBER(19)   NOT NULL,
    to_account_id   NUMBER(19)   NOT NULL,
    task_id         NUMBER(19),
    process_id      NUMBER(19),
    amount          NUMBER(20,4) NOT NULL,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP NOT NULL
)
NO DROP UNTIL 31 DAYS IDLE    -- no se puede dropear hasta 31 días sin inserts
NO DELETE LOCKED              -- NUNCA se puede borrar ninguna fila
HASHING USING "SHA2_512" VERSION "v1";

ALTER TABLE transfer ADD CONSTRAINT pk_transfers       PRIMARY KEY (id);
ALTER TABLE transfer ADD CONSTRAINT fk_from_account_id FOREIGN KEY (from_account_id) REFERENCES account(id);
ALTER TABLE transfer ADD CONSTRAINT fk_to_account_id   FOREIGN KEY (to_account_id)   REFERENCES account(id);
ALTER TABLE transfer ADD CONSTRAINT fk_process_id      FOREIGN KEY (task_id, process_id) REFERENCES process(task_id, id);
ALTER TABLE transfer ADD CONSTRAINT valid_amount        CHECK (amount > 0);

CREATE OR REPLACE TRIGGER trg_transfer_bi
BEFORE INSERT ON transfer
FOR EACH ROW
BEGIN
    IF :NEW.id IS NULL THEN
        :NEW.id := seq_transfer.NEXTVAL;
    END IF;
END;
/

COMMIT;