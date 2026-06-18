"""
load_config("url")                              #cargar la configuración
task = cfg().task.deterministic                 #ver si una tarea es determinista
requirements = cfg().requirements.packages      #lista los requirimientos del sistema
items  = cfg().inputs.active_items()            #listar todos los inputs
path   = cfg().outputs.resolve_filename(0, 9)   #devuelve ruta de salida en el worker
"""

import tomllib
import glob as glob_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from utils.github_util import GithubUtil




#-------------------seccion-task--------------------------
@dataclass
class TaskConfig:
    deterministic: bool = True

#-------------------hash-config--------------------------
@dataclass
class HashConfig:
    exclude: list[str] = field(default_factory=list)

#-------------------seccion-requirements--------------------------
@dataclass
class RequirementsConfig:
    packages: list[str] = field(default_factory=list)


#-------------------seccion-download--------------------------
@dataclass
class DownloadFileConfig:
    url:  str
    dest: str
    post: str = ""

@dataclass
class DownloadConfig:
    files: list[DownloadFileConfig] = field(default_factory=list)

#-------------------seccion-network--------------------------
@dataclass
class NetworkConfig:
    allowed_hosts: list[str] = field(default_factory=list)


#-------------------subsecciones-inputs--------------------------

@dataclass
class DirectoryInput:
    path:       str
    recursive:  bool      = False
    extensions: list[str] = field(default_factory=list)
    sort_order: str       = "filename"


@dataclass
class FileMultiInput:
    glob:   str
    format: str = "binary"


@dataclass
class FileSingleInput:
    path:        str
    format:      str  = "text"
    delimiter:   str  = "\n"
    encoding:    str  = "utf-8"
    skip_header: bool = False
    index_file: str | None = None  
    index_len:  int | None = None 


@dataclass
class RangeContinuousInput:
    end:   int
    start: int = 0
    step:  int = 1


@dataclass
class RangeDiscreteInput:
    values: list

@dataclass
class DynamicInput:
    pass


#-------------------seccion-inputs--------------------------

InputType = Literal["directory", "file_multi", "file_single",
                    "range_continuous", "range_discrete", "dynamic"]


@dataclass
class InputsConfig:
    type:             InputType             | None = None
    directory:        DirectoryInput        | None = None
    file_multi:       FileMultiInput        | None = None
    file_single:      FileSingleInput       | None = None
    range_continuous: RangeContinuousInput  | None = None
    range_discrete:   RangeDiscreteInput    | None = None
    dynamic:          DynamicInput          | None = None
    github_url: str | None = None

    def active(self):

        if self.type == "dynamic":
            return self.dynamic
        src = {
            "directory":        self.directory,
            "file_multi":       self.file_multi,
            "file_single":      self.file_single,
            "range_continuous": self.range_continuous,
            "range_discrete":   self.range_discrete,
        }.get(self.type)

        if src is None:
            raise ValueError(
                f"inputs.type = '{self.type}' pero falta la subsección "
                f"[inputs.{self.type}] en config.toml"
            )
        return src

    def active_items(self) -> list:
        """
        Devuelve la lista completa de items a procesar.

        - directory      → lista de URLs de ficheros en GitHub
        - file_multi     → lista de URLs de ficheros glob en GitHub
        - file_single    → lista de items extraídos del archivo remoto
        - range_continuous → lista de enteros
        - range_discrete   → lista de valores concretos
        """
        src = self.active()

        match self.type:

            case "directory":
                if not self.github_url:
                    raise RuntimeError("No se proporcionó github_url en InputsConfig")
                gu = GithubUtil(self.github_url)
                return [f"{self.github_url}/blob/main/{name}" for name in gu.list_dir(src.path)]

            case "file_multi":
                if not self.github_url:
                    raise RuntimeError("No se proporcionó github_url en InputsConfig")
                gu = GithubUtil(self.github_url)
            
                return [f"{self.github_url}/blob/main/{name}" for name in gu.list_glob(src.glob)]

            case "file_single":
                if not self.github_url:
                    raise RuntimeError("No se proporcionó github_url en InputsConfig")
                gu = GithubUtil(self.github_url)

                content = gu.read_file(src.path)

                if src.format == "json":
                    import json
                    return json.loads(content)
                elif src.format in ("image", "video", "binary"):

                    return [f"{self.github_url}/blob/main/{src.path}"]
                else:

                    separador = src.delimiter.encode().decode("unicode_escape")
                    lines = content.split(separador)
                    if src.skip_header and lines:
                        lines = lines[1:]
                    return [l for l in lines if l.strip()]

            case "range_continuous":
                return range(src.start, src.end + 1, src.step) # importante no hacer una lista de esto que sino colapsa la memoria

            case "range_discrete":
                return list(src.values)

            case "dynamic":
                return []

            case None: 
                return []

            case _:
                raise ValueError(f"inputs.type desconocido: '{self.type}'")

    def total(self) -> int:
        """Número total de items sin crear listas enormes."""
        src = self.active()

        if self.type == "range_continuous":

            items = self.active_items()
            if isinstance(items, range):

                return max(0, (items.stop - items.start + (items.step - 1 if items.step > 0 else items.step + 1)) // items.step)
            else:

                return len(src)
        else:
            return len(self.active_items())




#-------------------seccion-outputs--------------------------

@dataclass
class OutputsConfig:
    dir:              str = "."
    filename_pattern: str = "*"
    mode: str = "file"

    def resolve_filename(self, start: int, end: int, worker_id: int = 0) -> Path:
        """
        Devuelve la ruta completa del fichero de output
        """
        name = self.filename_pattern.format(
            start=start,
            end=end,
            worker_id=worker_id,
        )
        return Path(self.dir) / name

    def ensure_dir(self):
        """Crea el directorio de outputs si no existe."""
        Path(self.dir).mkdir(parents=True, exist_ok=True)


#-------------------root-config--------------------------

@dataclass
class Config:
    
    task: TaskConfig
    requirements: RequirementsConfig 
    download:     DownloadConfig
    outputs: OutputsConfig
    hash: HashConfig 
    network: NetworkConfig
    inputs:  InputsConfig | None = None




#-------------------singleton--------------------------

_config: Config | None = None


def load_config(github_url: str, path: str = "config.toml") -> Config:
    """
    Lee el fichero TOML y construye el singleton Config.
    """
    global _config


    gu = GithubUtil(github_url)
    raw_str = gu.read_file(path)  

    try:
        raw = tomllib.loads(raw_str)
    except TOMLDecodeError:
        raise RuntimeError(f"Error al parsear configuración desde {path}") 

    # ---- task
    task_raw = raw.get("task", {})
    task = TaskConfig(**task_raw)

    # ---- requirements
    req_raw = raw.get("requirements", {})
    requirements = RequirementsConfig(**req_raw)

    # ---- download
    dl_raw = raw.get("download", {})
    download = DownloadConfig(
        files=[DownloadFileConfig(**f) for f in dl_raw.get("files", [])]
    )

    # ---- inputs
    if "inputs" in raw:
        

        inp_raw  = raw["inputs"]
        inp_type = inp_raw.get("type")
        if not inp_type:
            raise KeyError("Falta inputs.type en config.toml")

        valid_types = {"directory", "file_multi", "file_single",
            "range_continuous", "range_discrete", "dynamic"}
        if inp_type not in valid_types:
            raise ValueError(
                f"inputs.type = '{inp_type}' no válido. "
                f"Opciones: {', '.join(sorted(valid_types))}"
            )

        if inp_type  == "dynamic":
            inputs = InputsConfig(type="dynamic", dynamic=DynamicInput(), github_url=github_url)
        else:
            subsection = inp_raw.get(inp_type)
            if subsection is None:
                raise KeyError(
                    f"inputs.type = '{inp_type}' pero falta "
                    f"[inputs.{inp_type}] en config.toml"
                )

            type_cls = {
                "directory":        DirectoryInput,
                "file_multi":       FileMultiInput,
                "file_single":      FileSingleInput,
                "range_continuous": RangeContinuousInput,
                "range_discrete":   RangeDiscreteInput,
            }[inp_type]

            inputs = InputsConfig(
                type=inp_type,
                **{inp_type: type_cls(**subsection)},
                github_url=github_url
            )
    else:
        inputs = None


    # ---- outputs
    outputs_raw = raw.get("outputs", {})

    mode = outputs_raw.get("mode", "file")
    valid_modes = {"file", "stdout"}
    if mode not in valid_modes:
        raise ValueError(
            f"[outputs.mode] = '{mode}' no válido. Opciones: {', '.join(valid_modes)}"
        )

    outputs = OutputsConfig(
        dir=outputs_raw.get("dir", "."),
        filename_pattern=outputs_raw.get("filename_pattern", "*"),
        mode=mode
    )

    # ---- hash-config
    hash_raw = raw.get("hash", {})
    hash_cfg = HashConfig(exclude=hash_raw.get("exclude", []))

    # ---- network
    network_raw = raw.get("network", {})
    network_cfg = NetworkConfig(
        allowed_hosts=network_raw.get("allowed_hosts", [])
    )


    _config = Config(task=task, requirements=requirements, download=download, inputs=inputs, outputs=outputs, hash=hash_cfg, network=network_cfg)
    return _config


def cfg() -> Config:

    if _config is None:
        raise RuntimeError(
            "cfg() llamado antes de load_config(). "
            "Llama a load_config('url') al inicio del programa."
        )
    return _config


