from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.combat import Action, available_actions, start_combat, take_turn
from shadowguy.scene import Encounter, Drop

from . import CharacterSheet, _replace_items


COMBAT_LOG_LINES = 8


class CombatScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu")]

    def __init__(self, encounter: Encounter, drop: Drop) -> None:
        super().__init__()
        self.encounter = encounter
        self.drop = drop
        self.state = None
        self.actions: list[Action] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static(self.encounter.prompt, id="prompt"),
            Static(id="enemies"),
            Static(id="combat_log"),
            ListView(id="actions"),
            id="combat_body",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.state = start_combat(self.app.character, self.encounter.enemies, self.drop, self.app.rng)
        await self._refresh()

    def _enemy_text(self) -> Text:
        lines = []
        for fighter in self.state.fighters:
            if not fighter.is_standing:
                lines.append(f"  {fighter.enemy.name}: down")
            else:
                stunned = " (reeling)" if fighter.stunned_rounds else ""
                lines.append(f"  {fighter.enemy.name}: {fighter.health}/{fighter.enemy.health}{stunned}")
        return Text("\n".join(lines))

    async def _refresh(self) -> None:
        state = self.state
        self.query_one(CharacterSheet).refresh()
        self.query_one("#enemies", Static).update(self._enemy_text())
        self.query_one("#combat_log", Static).update(Text("\n".join(state.log[-COMBAT_LOG_LINES:])))

        if state.is_over:
            self.actions = []
            await _replace_items(
                self.query_one("#actions", ListView), [ListItem(Static("Continue"), id="done")]
            )
            return

        self.actions = available_actions(self.app.character, self.state.weapon_cooldowns)
        await _replace_items(
            self.query_one("#actions", ListView),
            [ListItem(Static(action.label), id=f"action_{i}") for i, action in enumerate(self.actions)],
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.state.is_over:
            self.dismiss(self.state.outcome)
            return

        action = self.actions[int(event.item.id.removeprefix("action_"))]
        take_turn(self.state, action, self.app.rng)
        await self._refresh()
