# Seguridad y Autenticación

## Autenticación JWT
El sistema utiliza JSON Web Tokens (JWT) para la gestión de sesiones. El flujo es el siguiente:
1. **Login:** El usuario envía sus credenciales; el servidor verifica el hash (Argon2) y retorna un token firmado.
2. **Autorización:** Todas las rutas protegidas (ej: `/tasks`, `/payments`) requieren el envío del header `Authorization: Bearer <token>`.
3. **Verificación:** El módulo `utils/jwt_util.py` decodifica y valida la firma y expiración del token antes de permitir el acceso al endpoint.

## OAuth2
Se ha implementado soporte para proveedores externos, facilitando el registro mediante GitHub/Google. El estado de la transacción se gestiona mediante un caché temporal (`TTLCache`) para prevenir ataques de falsificación de peticiones entre sitios (CSRF).

## Almacenamiento de Contraseñas
Se ha sustituido el hashing tradicional por **Argon2**, el estándar actual de la industria para resistir ataques de fuerza bruta basados en GPU.