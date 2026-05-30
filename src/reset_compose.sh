#!/bin/bash
docker compose down
docker volume rm api_mariadb_data api_rabbitmq_data
docker compose up
