"""Travel time, independent of any UI.

The TUI's map screen and the graphical front-end both need the same answer to
"how long does one hop cost?", so the formula lives here rather than in a screen
module. `TRAVEL_HOURS_COST` is the base; a vehicle and an owned RideShare+ app
cut it additively.
"""

from typing import TYPE_CHECKING

from shadowguy.inventory import equipped_travel_reduction
from shadowguy.shops import owned_app_bonus

if TYPE_CHECKING:
    from shadowguy.character import Character

TRAVEL_HOURS_COST = 2.0


def travel_hours(character: "Character") -> float:
    """Hours per district hop for this runner."""
    reduction = equipped_travel_reduction(character.inventory) + owned_app_bonus(
        character, "travel_reduction"
    )
    return TRAVEL_HOURS_COST * (1 - reduction)
