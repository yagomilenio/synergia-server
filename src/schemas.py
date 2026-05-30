from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List


ALLOWED_DOMAINS = {"gmail.com", "outlook.com", "hotmail.com", "udc.es"}

def get_domain(email: str):
    return email.split("@")[-1].lower()


class UserCreateDTO(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_.-]{3,32}$")
    passwd: str = Field(..., min_length=8)
    email: EmailStr

    @field_validator('email')
    @classmethod
    def validate_email_domain(cls, v: str) -> str:
        domain = v.split("@")[-1].lower()
        
        if domain not in ALLOWED_DOMAINS:
            dominios_str = ", ".join(ALLOWED_DOMAINS)
            raise ValueError(f"Dominio no permitido. Solo se aceptan: {dominios_str}")
        
        return v


class UserDTO(BaseModel):
    username: Optional[str] = None
    passwd: str = Field(..., min_length=8)
    email: Optional[EmailStr] = None


class TaskCreate(BaseModel):
    name: str
    description: str = ""
    github_url: str
    repo_hash: str
    repo_commit: str
    resources: Optional[str] = None
    requirements: Optional[str] = None


class TaskInputDTO(BaseModel):
    items: List[str] = Field(..., min_items=1, description="Lista de inputs para la tarea")
    


class TaskUpdateDTO(BaseModel):
    repo_hash: str = Field(..., description="Nuevo hash del repositorio")
    repo_commit: str = Field(..., description="Nuevo commit de referencia")