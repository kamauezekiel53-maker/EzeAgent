from .computer_control import SKILL as computer_control
from .connected_services import SKILL as connected_services
from .memory import SKILL as memory
from .reminders import SKILL as reminders
from .security_assessment import SKILL as security_assessment
from .voice import SKILL as voice
from .workspace import SKILL as workspace

BUILTIN_SKILLS = (voice, computer_control, workspace, memory, reminders, security_assessment, connected_services)

def list_skills():
    return [skill.to_dict() for skill in BUILTIN_SKILLS]
