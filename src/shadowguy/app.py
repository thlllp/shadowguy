from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.character import Character
from shadowguy.content import (
    GIG_FENCE_SOME_CHROME,
    LEGWORK_CASE_THE_BLOCK,
    MISSION_DATA_HEIST,
)
from shadowguy.corpmap import NIGHT_CITY_MAP, render_ascii_map
from shadowguy.scene import Scene, SceneKind, resolve_choice, validate_scene_registry


async def _replace_items(list_view: ListView, items: list[ListItem]) -> None:
    await list_view.clear()
    for item in items:
        list_view.append(item)
    list_view.index = 0


class CharacterSheet(Static):
    def __init__(self, character: Character) -> None:
        super().__init__()
        self.character = character

    def render(self) -> str:
        c = self.character
        return (
            f"{c.name}\n"
            f"Day {c.day}   Stamina: {c.stamina}/{c.max_stamina}\n"
            f"Health: {c.health}/{c.max_health}   "
            f"Body: {c.body}  Skill: {c.skill}  Cool: {c.cool}\n"
            f"Cash: {c.cash}eb   Rep: {c.rep}"
        )


ACTIVITIES = [LEGWORK_CASE_THE_BLOCK, GIG_FENCE_SOME_CHROME, MISSION_DATA_HEIST]
validate_scene_registry(ACTIVITIES)


class MainMenu(Screen):
    BINDINGS = [("q", "quit", "Quit"), ("m", "corp_map", "Corp Map (preview)")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="activities")
        yield Footer()

    def action_corp_map(self) -> None:
        self.app.push_screen(CorpMapScreen())

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        self.query_one(CharacterSheet).refresh()
        character = self.app.character
        items = []
        for scene in ACTIVITIES:
            label = f"{scene.kind.capitalize()} — {scene.title} ({scene.stamina_cost} stamina)"
            if scene.kind == SceneKind.MISSION:
                advantage = character.advantage_for(scene.id)
                if advantage:
                    label += f" (advantage +{advantage} banked)"
            if not character.can_afford(scene.stamina_cost):
                label += " — too tired"
            items.append(ListItem(Static(label), id=scene.id))
        items.append(ListItem(Static("End the day (rest)"), id="end_day"))
        await _replace_items(self.query_one("#activities", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "end_day":
            self.app.character.rest()
            await self._refresh()
            return
        scene = next(scene for scene in ACTIVITIES if scene.id == event.item.id)
        character = self.app.character
        if not character.can_afford(scene.stamina_cost):
            return
        character.spend_stamina(scene.stamina_cost)
        self.app.push_screen(SceneScreen(scene))


class SceneScreen(Screen):
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, scene: Scene) -> None:
        super().__init__()
        self.scene = scene
        self.stage_id = scene.start_stage
        self.awaiting_continue = False
        self._pending_next_stage: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static(self._current_stage().prompt, id="prompt"),
            ListView(id="choices"),
            id="scene_body",
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._show_stage()

    def _current_stage(self):
        return self.scene.stages[self.stage_id]

    async def _show_stage(self) -> None:
        self.awaiting_continue = False
        stage = self._current_stage()
        self.query_one("#prompt", Static).update(stage.prompt)
        items = [ListItem(Static(choice.label), id=f"choice_{i}") for i, choice in enumerate(stage.choices)]
        await _replace_items(self.query_one("#choices", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.awaiting_continue:
            await self._advance()
            return

        stage = self._current_stage()
        index = int(event.item.id.removeprefix("choice_"))
        choice = stage.choices[index]

        character = self.app.character
        result, outcome = resolve_choice(character, self.scene, choice)
        self.query_one(CharacterSheet).refresh()

        self.query_one("#prompt", Static).update(f"{result.name}: {outcome.text}")

        if not character.is_alive:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return

        self._pending_next_stage = outcome.next_stage
        await _replace_items(self.query_one("#choices", ListView), [ListItem(Static("Continue"), id="continue")])
        self.awaiting_continue = True

    async def _advance(self) -> None:
        if self._pending_next_stage is None:
            self.app.pop_screen()
            return
        self.stage_id = self._pending_next_stage
        await self._show_stage()


class CorpMapScreen(Screen):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "back", "Back"),
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    def __init__(self) -> None:
        super().__init__()
        self.corp_map = NIGHT_CITY_MAP
        self.selected_id = "city_center"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(markup=False, id="map"),
            Static(id="territory_info"),
            id="corp_map_body",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_move(self, direction: str) -> None:
        dx, dy = self.DIRECTIONS[direction]
        current = self.corp_map.territories[self.selected_id]
        for conn_id in current.connections:
            candidate = self.corp_map.territories[conn_id]
            if (candidate.x - current.x, candidate.y - current.y) == (dx, dy):
                self.selected_id = conn_id
                self._refresh()
                return

    def _refresh(self) -> None:
        self.query_one("#map", Static).update(render_ascii_map(self.corp_map, self.selected_id))
        t = self.corp_map.territories[self.selected_id]
        self.query_one("#territory_info", Static).update(
            f"{t.name} — owner: {t.owner}, value: {t.value}"
        )


class ShadowguyApp(App):
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.character = Character(name="Runner")

    def on_mount(self) -> None:
        self.push_screen(MainMenu())


def main() -> None:
    ShadowguyApp().run()


if __name__ == "__main__":
    main()
