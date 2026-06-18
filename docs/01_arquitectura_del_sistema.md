# Arquitectura del Sistema

## Descripción General
Synergia Server opera bajo una arquitectura basada en eventos y colas de mensajes. Los componentes están desacoplados para permitir la escalabilidad horizontal de los nodos de cómputo.



## Flujos Principales
1. **Ingesta:** La API REST (`api.py`) recibe el repositorio y valida los parámetros mediante `schemas.py`.
2. **Orquestación:** El `publisher.py` fragmenta la tarea en *chunks* y los distribuye en RabbitMQ.
3. **Ejecución:** Los nodos trabajadores se conectan vía WebSocket (`ws_api.py`) para consumir las tareas en tiempo real.
4. **Persistencia:** Todos los resultados y estados se consolidan en la base de datos Oracle (`db/`).