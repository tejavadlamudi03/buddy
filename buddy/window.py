"""What Buddy knows about an open window, on any system."""
from dataclasses import dataclass

BUDDY_TITLE = "Buddy Voice Control"
HIDDEN_CLASS = "BuddyWhatsApp"


@dataclass
class Window:
    id: int
    title: str
    cls: str
