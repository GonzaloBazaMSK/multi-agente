from pydantic import BaseModel, Field


class Course(BaseModel):
    id: str
    nombre: str
    descripcion: str
    categoria: str
    duracion_horas: int | None = None
    modalidad: str | None = None  # online, presencial, mixto
    precio: float
    moneda: str = "ARS"
    pais: str = "AR"
    tiene_certificado: bool = True
    tipo_certificado: str | None = None  # universitario, aval_sociedades, etc.
    docentes: list[str] = Field(default_factory=list)
    fecha_inicio: str | None = None
    cuotas_disponibles: int | None = None
    precio_cuota: float | None = None
    url_inscripcion: str | None = None
    rebill_plan_id: str | None = None
    mp_product_id: str | None = None
    lms_course_id: str | None = None
    lms_platform: str | None = None  # moodle, blackboard, tropos
    tags: list[str] = Field(default_factory=list)

    @property
    def precio_formateado(self) -> str:
        return f"{self.moneda} {self.precio:,.0f}"
