# Modelo de Datos

## Persistencia
El sistema utiliza **Oracle Database** para garantizar la integridad transaccional necesaria en los pagos entre nodos.

## Estructura de Schemas
La validación de datos se centraliza en `src/schemas.py` utilizando **Pydantic**. Esto permite:
* **Tipado estricto:** Asegura que los campos recibidos cumplen con los requisitos de longitud y formato (ej: `EmailStr`).
* **Validación de negocio:** Se han implementado validadores personalizados para restringir dominios de correo permitidos y formatos de nombres de usuario.