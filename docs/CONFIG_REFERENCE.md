# ⚙️ Referencia de Configuración — `config.toml`

El fichero de configuración le dice a la **plataforma de paralelización** tres cosas y solo tres:

- **De dónde vienen los datos** → sección `[inputs]`
- **Cómo ejecutar el script** → sección `[runner]`
- **Dónde van los resultados** → sección `[outputs]`

Todo lo demás (modelo, prompts, parámetros internos del script) es responsabilidad del propio script, no de este fichero.

---

## Índice

- [⚙️ Referencia de Configuración — `config.toml`](#️-referencia-de-configuración--configtoml)
  - [Índice](#índice)
  - [`[inputs]` — Fuente de datos](#inputs--fuente-de-datos)
    - [Tipo `directory`](#tipo-directory)
    - [Tipo `file_multi`](#tipo-file_multi)
    - [Tipo `file_single`](#tipo-file_single)
    - [Tipo `range_continuous`](#tipo-range_continuous)
    - [Tipo `range_discrete`](#tipo-range_discrete)
  - [`[runner]` — Cómo ejecutar](#runner--cómo-ejecutar)
  - [`[outputs]` — Resultados](#outputs--resultados)
  - [Ejemplo completo](#ejemplo-completo)
  - [Referencia rápida de tipos de input](#referencia-rápida-de-tipos-de-input)
  - [Separadores disponibles](#separadores-disponibles)

---

## `[inputs]` — Fuente de datos

Define **qué datos** recibe el script y cómo están organizados. El campo `type` es el discriminador: solo se lee la subsección que coincide con su valor.

```toml
[inputs]
type = "directory"   # → activa [inputs.directory]
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `type` | `string` | ✅ | Discriminador. Valores: `directory` · `file_multi` · `file_single` · `range_continuous` · `range_discrete` |

---

### Tipo `directory`

Un directorio entero. La plataforma escanea los ficheros, les asigna índices `0..N-1` y los reparte entre workers.

```toml
[inputs]
type = "directory"

[inputs.directory]
path       = "inputs/images"
recursive  = false
extensions = [".jpg", ".jpeg", ".png", ".webp"]
sort_order = "filename"
```

| Campo | Tipo | Defecto | Descripción |
|---|---|---|---|
| `path` | `string` | — | Ruta al directorio |
| `recursive` | `bool` | `false` | Si `true`, incluye subdirectorios |
| `extensions` | `array[string]` | `[]` (todos) | Filtro de extensiones. Vacío = sin filtro |
| `sort_order` | `string` | `"filename"` | Orden de los índices: `filename` (alfabético) · `modified` (fecha) |

---

### Tipo `file_multi`

Varios ficheros seleccionados por patrón glob, potencialmente en rutas distintas.

```toml
[inputs]
type = "file_multi"

[inputs.file_multi]
glob   = "inputs/images/*.png"
format = "image"
```

| Campo | Tipo | Defecto | Descripción |
|---|---|---|---|
| `glob` | `string` | — | Patrón glob. Admite `*`, `**`, `?` |
| `format` | `string` | `"binary"` | Tipo de los ficheros: `image` · `video` · `binary` · `text` |

**Ejemplos de patrones:**

```toml
glob = "data/**/*.jpg"          # todos los .jpg de forma recursiva
glob = "batch_?.png"            # batch_1.png, batch_2.png...
glob = "inputs/{a,b,c}/*.webp"  # subcarpetas específicas
```

---

### Tipo `file_single`

Un único fichero que contiene múltiples items. La plataforma lo divide en items según el delimitador y los reparte entre workers.

```toml
[inputs]
type = "file_single"

[inputs.file_single]
path        = "inputs/lista.txt"
format      = "text"
delimiter   = "\n"
encoding    = "utf-8"
skip_header = false
```

| Campo | Tipo | Defecto | Descripción |
|---|---|---|---|
| `path` | `string` | — | Ruta al fichero |
| `format` | `string` | `"text"` | Tipo del fichero: `text` · `csv` · `json` · `image` · `video` |
| `delimiter` | `string` | `"\n"` | Separador entre items. Solo aplica si `format = "text"` |
| `encoding` | `string` | `"utf-8"` | Codificación. Solo aplica para formatos de texto |
| `skip_header` | `bool` | `false` | Si `true`, ignora la primera línea. Útil para CSV con cabecera |

> Para `format = "csv"` o `"json"` se ignora `delimiter` y se usa el parser nativo.

---

### Tipo `range_continuous`

Un rango numérico continuo. La plataforma divide el rango en chunks y asigna uno a cada worker.

```toml
[inputs]
type = "range_continuous"

[inputs.range_continuous]
start = 0
end   = 999999
step  = 1
```

| Campo | Tipo | Defecto | Descripción |
|---|---|---|---|
| `start` | `int` | `0` | Valor inicial (inclusivo) |
| `end` | `int` | — | Valor final (inclusivo) |
| `step` | `int` | `1` | Incremento entre valores |

**Cuándo usarlo:** cálculos matemáticos, IDs de base de datos, rangos de números primos.

---

### Tipo `range_discrete`

Una lista de valores concretos no contiguos.

```toml
[inputs]
type = "range_discrete"

[inputs.range_discrete]
values = [1, 5, 23, 47, 200, 9999]
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `values` | `array[int\|string]` | ✅ | Lista de valores a procesar |

**Cuándo usarlo:** IDs específicos, subset de un dataset, reintentar solo los items fallidos.

---

## `[runner]` — Cómo ejecutar

Le dice a la plataforma **cómo lanzar el script** para cada worker. La plataforma construye el comando final concatenando `command`, los argumentos de rango y `extra_args`.

```toml
[runner]
command    = "python run_vision.py"
arg_start  = "--start"
arg_end    = "--end"
workdir    = "."
extra_args = ""
```

| Campo | Tipo | Defecto | Descripción |
|---|---|---|---|
| `command` | `string` | — | Comando completo para lanzar el script |
| `arg_start` | `string` | `"--start"` | Nombre del argumento CLI para el índice inicial |
| `arg_end` | `string` | `"--end"` | Nombre del argumento CLI para el índice final |
| `workdir` | `string` | `"."` | Directorio de trabajo desde el que se ejecuta el comando |
| `extra_args` | `string` | `""` | Argumentos adicionales fijos que se añaden a cada llamada |

**Cómo construye la plataforma el comando:**

Con la configuración de arriba, la plataforma genera por cada worker:

```bash
python run_vision.py --start 0  --end 9
python run_vision.py --start 10 --end 19
python run_vision.py --start 20 --end 29
```

**Ejemplo con argumentos extra:**

```toml
[runner]
command    = "python run_vision.py"
arg_start  = "--start"
arg_end    = "--end"
extra_args = "--pack ocr --config prod.toml"
workdir    = "/srv/jobs/vision"
```

Genera:

```bash
python run_vision.py --start 0 --end 9 --pack ocr --config prod.toml
```

---

## `[outputs]` — Resultados

Define dónde escribe cada worker sus resultados.

```toml
[outputs]
dir              = "outputs"
filename_pattern = "results_{start}_{end}.json"
```

| Campo | Tipo | Defecto | Descripción |
|---|---|---|---|
| `dir` | `string` | `"outputs"` | Directorio base donde se escriben los resultados |
| `filename_pattern` | `string` | `"results_{start}_{end}.json"` | Patrón del nombre de fichero por worker |

**Variables disponibles en `filename_pattern`:**

| Variable | Valor |
|---|---|
| `{start}` | Índice inicial del chunk de este worker |
| `{end}` | Índice final del chunk de este worker |
| `{worker_id}` | ID numérico del worker (0, 1, 2…) |

**Ejemplos:**

```toml
filename_pattern = "results_{start}_{end}.json"   # defecto
filename_pattern = "worker_{worker_id}.json"       # por ID de worker
filename_pattern = "batch_{start}-{end}.json"      # con guión
```

Cada worker escribe en su propio fichero → **sin conflictos de escritura**.

---

## Ejemplo completo

```toml

#  config.toml — Plataforma de paralelización


[inputs]
type = "directory"

[inputs.directory]
path       = "inputs/images"
recursive  = false
extensions = [".jpg", ".jpeg", ".png", ".webp"]
sort_order = "filename"

[runner]
command    = "python run_vision.py"
arg_start  = "--start"
arg_end    = "--end"
extra_args = "--pack general"
workdir    = "."

[outputs]
dir              = "outputs"
filename_pattern = "results_{start}_{end}.json"
```

---

## Referencia rápida de tipos de input

| Situación | `type` |
|---|---|
| Carpeta de imágenes o vídeos | `directory` |
| Ficheros dispersos / selección por patrón | `file_multi` |
| Lista de items en un `.txt` o `.csv` | `file_single` |
| Rango numérico continuo (0 a 1.000.000) | `range_continuous` |
| Lista de valores concretos no contiguos | `range_discrete` |

---

## Separadores disponibles

Aplica solo a `[inputs.file_single]` con `format = "text"`.

| Valor | Carácter | Cuándo usarlo |
|---|---|---|
| `"\n"` | Salto de línea | Una línea = un item (defecto) |
| `","` | Coma | CSV simple |
| `";"` | Punto y coma | CSV europeo |
| `"\t"` | Tabulador | TSV / exportaciones de Excel |
| `"\|"` | Pipe | Items que contienen comas o puntos y coma |
| `"\0"` | Null byte | Strings con cualquier carácter especial posible |

---

*Referencia de configuración · Plataforma de paralelización · Python 3.11+*
