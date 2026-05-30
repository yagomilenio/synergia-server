import config

if config.DB_BACKEND == "oracle":
    from db.db_oracle import *
#elif config.DB_BACKEND == "mariadb":
#    from db.db_mariadb import *
else:
    raise RuntimeError(f"DB_BACKEND desconocido: '{config.DB_BACKEND}'. Usa 'oracle' o 'mariadb'.")