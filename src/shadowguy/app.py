import random

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.character import Character
from shadowguy.content import GIG_FENCE_SOME_CHROME
from shadowguy.corpmap import generate_corp_map, owner_label, render_ascii_map
from shadowguy.factions import FACTIONS
from shadowguy.fixer import Fixer, create_fixers, expire_offers, refresh_offers
from shadowguy.jobs import generate_legwork_for_job
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


STATIC_ACTIVITIES = [GIG_FENCE_SOME_CHROME]
validate_scene_registry(STATIC_ACTIVITIES)


class MainMenu(Screen):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("m", "corp_map", "Corp Map (preview)"),
        ("f", "fixers", "Fixers"),
    ]

    CSS = """
    #sidebar {
        width: 20;
        border: solid $accent;
        padding: 1;
    }

    #main_panel {
        width: 1fr;
    }
    """

    CATEGORIES = [("gig", "Gigs"), ("job", "Jobs"), ("legwork", "Legwork"), ("fixer", "Fixers")]

    def __init__(self) -> None:
        super().__init__()
        self.selected_category = self.CATEGORIES[0][0]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Vertical(ListView(id="categories"), id="sidebar"),
            Vertical(CharacterSheet(self.app.character), ListView(id="activities"), id="main_panel"),
        )
        yield Footer()

    def action_corp_map(self) -> None:
        self.app.push_screen(CorpMapScreen())

    def action_fixers(self) -> None:
        self.app.push_screen(FixerListScreen())

    async def on_mount(self) -> None:
        await self._refresh_categories()
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh_categories(self) -> None:
        items = [ListItem(Static(label), id=f"cat_{key}") for key, label in self.CATEGORIES]
        await _replace_items(self.query_one("#categories", ListView), items)

    async def _refresh(self) -> None:
        self.query_one(CharacterSheet).refresh()
        character = self.app.character
        items = []

        if self.selected_category == "gig":
            for scene in STATIC_ACTIVITIES:
                if scene.kind != SceneKind.GIG:
                    continue
                label = f"Gig — {scene.title} ({scene.stamina_cost} stamina)"
                if not character.can_afford(scene.stamina_cost):
                    label += " — too tired"
                items.append(ListItem(Static(label), id=f"static_{scene.id}"))

        if self.selected_category == "job":
            for job in character.accepted_jobs:
                available = job.timing.is_available(character.day)
                label = f"Job — {job.scene.title} ({job.scene.stamina_cost} stamina) — {job.timing.label}"
                if not available:
                    label += " — not yet"
                elif not character.can_afford(job.scene.stamina_cost):
                    label += " — too tired"
                items.append(ListItem(Static(label), id=f"job_{job.id}"))

        if self.selected_category == "legwork":
            for job in character.accepted_jobs:
                advantage = character.advantage_for(job.scene.id)
                legwork_label = f"Legwork — Case the job: {job.scene.title}"
                if advantage:
                    legwork_label += f" (advantage +{advantage} banked)"
                items.append(ListItem(Static(legwork_label), id=f"legwork_{job.id}"))

        items.append(ListItem(Static("End the day (rest)"), id="end_day"))
        await _replace_items(self.query_one("#activities", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "categories":
            await self._select_category(event.item.id.removeprefix("cat_"))
            return

        item_id = event.item.id
        character = self.app.character

        if item_id == "end_day":
            character.rest()
            expire_offers(self.app.fixers, character.day)
            refresh_offers(self.app.fixers, character.day, self.app.rng)
            await self._refresh()
            return

        if item_id.startswith("static_"):
            scene_id = item_id.removeprefix("static_")
            scene = next(scene for scene in STATIC_ACTIVITIES if scene.id == scene_id)
            if not character.can_afford(scene.stamina_cost):
                return
            character.spend_stamina(scene.stamina_cost)
            self.app.push_screen(SceneScreen(scene))
            return

        if item_id.startswith("legwork_"):
            offer_id = item_id.removeprefix("legwork_")
            job = next(job for job in character.accepted_jobs if job.id == offer_id)
            legwork_scene = generate_legwork_for_job(job.scene.id, job.scene.title)
            if not character.can_afford(legwork_scene.stamina_cost):
                return
            character.spend_stamina(legwork_scene.stamina_cost)
            self.app.push_screen(SceneScreen(legwork_scene))
            return

        if item_id.startswith("job_"):
            offer_id = item_id.removeprefix("job_")
            job = next(job for job in character.accepted_jobs if job.id == offer_id)
            if not job.timing.is_available(character.day) or not character.can_afford(job.scene.stamina_cost):
                return
            character.spend_stamina(job.scene.stamina_cost)
            self.app.push_screen(SceneScreen(job.scene))
            return

    async def _select_category(self, key: str) -> None:
        if key == "fixer":
            self.app.push_screen(FixerListScreen())
            return
        self.selected_category = key
        await self._refresh()


class FixerListScreen(Screen):
    BINDINGS = [("q", "quit", "Quit"), ("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(id="fixers")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        items = [
            ListItem(
                Static(f"{fixer.name} — {fixer.specialty} ({len(fixer.offers)} jobs available)"),
                id=fixer.id,
            )
            for fixer in self.app.fixers
        ]
        await _replace_items(self.query_one("#fixers", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        fixer = next(fixer for fixer in self.app.fixers if fixer.id == event.item.id)
        self.app.push_screen(FixerOffersScreen(fixer))


class FixerOffersScreen(Screen):
    BINDINGS = [("q", "quit", "Quit"), ("escape", "back", "Back")]

    def __init__(self, fixer: Fixer) -> None:
        super().__init__()
        self.fixer = fixer

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"{self.fixer.name} — {self.fixer.specialty}", id="fixer_info")
        yield ListView(id="offers")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        items = [
            ListItem(
                Static(f"{offer.scene.title} ({offer.scene.stamina_cost} stamina) — {offer.timing.label}"),
                id=offer.id,
            )
            for offer in self.fixer.offers
        ]
        await _replace_items(self.query_one("#offers", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        offer = next(offer for offer in self.fixer.offers if offer.id == event.item.id)
        self.app.character.accept_job(offer)
        self.fixer.offers = [o for o in self.fixer.offers if o.id != offer.id]
        await self._refresh()


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
            if self.scene.kind == SceneKind.JOB:
                self.app.character.remove_job(self.scene.id)
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
        current = self.app.corp_map.territories[self.selected_id]
        for conn_id in current.connections:
            candidate = self.app.corp_map.territories[conn_id]
            if (candidate.x - current.x, candidate.y - current.y) == (dx, dy):
                self.selected_id = conn_id
                self._refresh()
                return

    def _refresh(self) -> None:
        corp_map = self.app.corp_map
        self.query_one("#map", Static).update(render_ascii_map(corp_map, self.selected_id))
        t = corp_map.territories[self.selected_id]
        self.query_one("#territory_info", Static).update(
            f"{t.name} — owner: {owner_label(t.owner)}, value: {t.value}"
        )


class ShadowguyApp(App):
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.character = Character(name="Runner")
        self.rng = random.Random()
        self.corp_map = generate_corp_map(FACTIONS, self.rng)
        self.fixers = create_fixers()
        refresh_offers(self.fixers, self.character.day, self.rng)

    def on_mount(self) -> None:
        self.push_screen(MainMenu())


def main() -> None:
    ShadowguyApp().run()


if __name__ == "__main__":
    main()
