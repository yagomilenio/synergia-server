# Guía de Despliegue

## Requisitos Previos
* Docker y Docker Compose instalados.
* Acceso a un servidor Oracle Database (o uso del contenedor proporcionado).

## Pasos para iniciar el entorno
1. **Configuración:** Editar el archivo `src/config.json` con las credenciales de RabbitMQ y la base de datos.
2. **Levantar infraestructura:**
   ```bash
   docker-compose -f infra/docker/docker-compose.yml up -d