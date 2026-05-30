import os
import json
from dotenv import load_dotenv

CONFIG_FILE = os.path.expanduser("config.json")



load_dotenv()

def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise RuntimeError(f"No se encontró el archivo de configuración: {CONFIG_FILE}")
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

config = load_config()

INITIAL_CREDITS  = config["initial_credits"]
PROCESS_UNTIL_CONFIRMATION = config["process_until_confirmation"]
TASK_COST = config["task_cost"]
SYSTEM_ACCOUNTS = config["system_accounts"]
FEES_ACCOUNT_NAME = SYSTEM_ACCOUNTS["fees"]
SYSTEM_MINT_NAME = SYSTEM_ACCOUNTS["mint"]

RABBITMQ_HOST = config["rabbitmq"]["host"]
RABBITMQ_PORT = config["rabbitmq"]["port"]
RABBITMQ_USER = config["rabbitmq"]["user"]
RABBITMQ_PASSWD = os.getenv('RABBITMQ_PASSWORD')
RABBITMQ_QUEUE = config["rabbitmq"]["queue"]

MARIADB_USER = config["mariadb"]["user"]
MARIADB_PASSWD = os.getenv('MARIADB_PASSWORD')
MARIADB_DATABASE = config["mariadb"]["database"]
MARIADB_ADDRESS = config["mariadb"]["address"]

ORACLE_USER = config["oracle"]["user"]
ORACLE_PASSWD = os.getenv('ORACLE_APP_PASSWORD')
ORACLE_DSN = config["oracle"]["dsn"]

UPLOAD_DIR = config['upload_dir']

GOOGLE_CLIENT_ID     = config["oauth"]["google_client_id"]
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
REDIRECT_URI_GOOGLE = config["oauth"]["redirect_uri_google"]

GITHUB_CLIENT_ID     = config["oauth"]["github_client_id"]
GITHUB_CLIENT_SECRET = os.getenv('GITHUB_CLIENT_SECRET')
REDIRECT_URI_GITHUB = config["oauth"]["redirect_uri_github"]

SMTP_HOST     = config["smtp"]["host"]
SMTP_PORT     = config["smtp"]["port"]
SMTP_USER     = config["smtp"]["user"]
SMTP_FROM     = config["smtp"]["from"]
SMTP_PASSWD = os.getenv('SMTP_PASSWD')
SMTP_REDIRECT = config["smtp"]["redirect"]

MAX_UPLOAD_BYTES = config["max_upload_bytes"]

JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')


DB_BACKEND = config.get('db_backend')

