from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    available: bool = True
    connection_required: bool = False

    def to_dict(self):
        return asdict(self)
