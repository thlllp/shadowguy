"""Talking to a town-square NPC.

Standard locals give flavour and a district hint. Named contacts offer a one-off
favour gated on `Character.local_standing` (already part of the save), so there
is no extra save state to migrate.
"""

from textual.app import ComposeResult
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.checks import resolve_check
from shadowguy.npcs import Npc
from shadowguy.skills import skill_value

from . import MENU_BACK_BINDINGS, BackScreen, CharacterSheet, _replace_items


class NpcDialogueScreen(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

    def __init__(self, npc: Npc) -> None:
        super().__init__()
        self.npc = npc
        self._prompt = ""
        self._talks = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="npc_prompt")
        yield ListView(id="npc_actions")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    def _standing(self) -> int:
        return self.app.character.local_standing_with(self.npc.id)

    async def _refresh(self) -> None:
        npc = self.npc
        prompt = self._prompt or f"{npc.name} — {npc.label}\n\n{npc.blurb}".rstrip()
        self.query_one("#npc_prompt", Static).update(prompt)

        rows = [ListItem(Static("Talk"), id="talk")]
        if npc.special:
            if self._standing() < 1 and npc.quest_skill is not None:
                rows.append(ListItem(Static("Ask what they need"), id="quest"))
            else:
                rows.append(ListItem(Static("Catch up"), id="thanks"))
        else:
            rows.append(ListItem(Static("Ask about the district"), id="hint"))
        rows.append(ListItem(Static("Leave"), id="leave"))
        await _replace_items(self.query_one("#npc_actions", ListView), rows)
        self.query_one("#npc_actions", ListView).focus()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "talk":
            await self._talk()
        elif event.item.id == "hint":
            await self._hint()
        elif event.item.id == "quest":
            await self._quest()
        elif event.item.id == "thanks":
            self._prompt = f"{self.npc.name}: You've done enough for me. We're square."
            await self._refresh()
        elif event.item.id == "leave":
            self.app.pop_screen()

    async def _talk(self) -> None:
        lines = self.npc.lines
        if not lines:
            self._prompt = f"{self.npc.name} has nothing to say."
        else:
            line = lines[(self.npc.line_index + self._talks) % len(lines)]
            self._talks += 1
            self._prompt = f"{self.npc.name}: {line}"
        await self._refresh()

    async def _hint(self) -> None:
        app = self.app
        territory_id = self.npc.territory_id
        fixer = next(
            (fixer for fixer in app.fixers if fixer.location_id == territory_id),
            None,
        )
        if fixer is not None:
            self._prompt = (
                f"{self.npc.name}: {fixer.name} is working out of here. "
                f"{fixer.specialty}. Worth a word."
            )
        else:
            locations = {
                location.id: location
                for location in app.corp_map.territories[territory_id].locations
            }
            gigs = [
                locations[location_id]
                for location_id in app.location_gigs
                if location_id in locations
            ]
            if gigs:
                self._prompt = (
                    f"{self.npc.name}: Someone at {gigs[0].name} is looking for a hand."
                )
            else:
                self._prompt = (
                    f"{self.npc.name}: Nothing worth chasing here today. "
                    "Try the bars; people talk when they're drinking."
                )
        await self._refresh()

    async def _quest(self) -> None:
        npc = self.npc
        character = self.app.character
        if npc.quest_skill is None:
            return
        result = resolve_check(
            stat_value=skill_value(character, npc.quest_skill),
            difficulty=npc.quest_difficulty,
            rng=self.app.rng,
        )
        if result.result.passed:
            character.cash += npc.reward_cash
            character.adjust_local_standing(npc.id, npc.reward_standing)
            self._prompt = (
                f"{npc.name}: Not bad. {npc.reward_cash}eb, and I owe you one."
            )
        else:
            self._prompt = (
                f"{npc.name}: Not this time. Come back when you've got steadier hands."
            )
        await self._refresh()
