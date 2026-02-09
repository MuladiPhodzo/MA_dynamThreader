from dataclasses import dataclass
from datetime import timedelta


@dataclass
class ProcessRequirement:
    resource: str
    max_age: timedelta | None = None
    required: bool = True
