#!/bin/bash

echo "Comprobando si la BBDD ya está inicializada..."


EXISTE=$(sqlplus -s / as sysdba <<EOF
SET HEADING OFF
SET FEEDBACK OFF
SET PAGESIZE 0
SET VERIFY OFF
SET ECHO OFF

ALTER SESSION SET CONTAINER = FREEPDB1;

SELECT COUNT(*) 
FROM dba_tables 
WHERE owner = 'SYNERGIA';

EXIT;
EOF
)

EXISTE=$(echo "$EXISTE" | tr -d '[:space:]')

if [ "$EXISTE" != "0" ]; then
    echo "La BBDD ya existe. No se ejecuta init."
    exit 0
fi

echo "Creando usuario synergia..."

sqlplus -s / as sysdba  <<EOF
ALTER SESSION SET CONTAINER = FREEPDB1;
CREATE USER $APP_USER IDENTIFIED BY "$APP_USER_PASSWORD";
GRANT CONNECT, RESOURCE TO synergia;
ALTER USER synergia QUOTA UNLIMITED ON USERS;
EXIT;
EOF


echo "Ejecutando script SQL inicial..."

sqlplus -s / as sysdba <<EOF
ALTER SESSION SET CONTAINER = FREEPDB1;
ALTER SESSION SET CURRENT_SCHEMA = $APP_USER;
@/opt/oracle/scripts/setup/synergia_oracle.sql
EXIT;
EOF

echo "Inicialización completada."