# Ciclo de Vida de una Tarea

## 1. Definición y Publicación
Cuando un usuario registra una tarea, el sistema realiza los siguientes pasos:
* **Validación:** Se verifica la URL de GitHub y la integridad del hash del repositorio.
* **Fragmentación:** El módulo `publisher.py` calcula el tamaño óptimo de los *chunks* basándose en la memoria disponible del nodo y la cantidad de inputs.
* **Encolado:** Se publican los mensajes en RabbitMQ utilizando el protocolo AMQP con persistencia (`delivery_mode=2`) para asegurar que no se pierdan tareas si el servidor se reinicia.

## 2. Ejecución Distribuida
Los trabajadores obtienen los *chunks* a través de `ws_api.py`, que mantiene una conexión persistente y monitorea la salud del worker mediante contadores de Prometheus.