from datetime import datetime
from pydantic import BaseModel


class Provenance(BaseModel):
    author: str
    date: datetime
    version: str

    def __str__(self):
        return f"{self.author} ({self.date.isoformat()}) v{self.version}"


class Document(BaseModel):
    issuer_id: int
    content: str
    metadata: Provenance
