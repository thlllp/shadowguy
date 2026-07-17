from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.checks import CheckResult
from shadowguy.combat import CombatOutcome, drop_for_result
from shadowguy.gigs import GIG_FAIL_REP_HIT, GIG_FAIL_STANDING_HIT
from shadowguy.jobs import JOB_FAILURE_REP_HIT, JOB_FAILURE_TRUST_HIT
from shadowguy.runners import RUNNERS_BY_ID
from shadowguy.scene import Scene, SceneKind, apply_outcome, resolve_choice
from shadowguy.tactical import TacticalOutcome

from . import CharacterSheet, _replace_items


class SceneScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu")]

    def __init__(self, scene: Scene) -> None:
        super().__init__()
        self.scene = scene
        self.stage_id = scene.start_stage
        self.awaiting_continue = False
        self._pending_next_stage: str | None = None
        self._pending_result: CheckResult | None = None

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
        self._take_crew_cut(outcome)
        self.query_one(CharacterSheet).refresh()

        prompt = self.query_one("#prompt", Static)
        prompt.update(f"{result.name}: {outcome.text}")

        if result in (CheckResult.CRITICAL_SUCCESS, CheckResult.CRITICAL_FAILURE):
            flash = "green" if result is CheckResult.CRITICAL_SUCCESS else "red"
            prompt.styles.background = Color.parse(flash)
            prompt.styles.animate("background", value=Color(0, 0, 0, 0), duration=0.6)

        if not character.is_alive:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return

        await self._await_continue(outcome.next_stage, result)

    async def _await_continue(self, next_stage: str | None, result: CheckResult | None) -> None:
        self._pending_next_stage = next_stage
        self._pending_result = result
        await _replace_items(self.query_one("#choices", ListView), [ListItem(Static("Continue"), id="continue")])
        self.awaiting_continue = True

    async def _show_combat(self, stage) -> None:
        from .combat_screen import CombatScreen

        self.stage_id = stage.id
        self.app.push_screen(
            CombatScreen(stage.combat, drop_for_result(self._pending_result)),
            self._on_combat_end,
        )

    async def _show_tactical(self, stage) -> None:
        from .tactical_screen import TacticalScreen

        self.stage_id = stage.id
        self.app.push_screen(TacticalScreen(stage.tactical), self._on_tactical_end)

    async def _on_tactical_end(self, result: TacticalOutcome) -> None:
        character = self.app.character
        if result is TacticalOutcome.DEAD:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return
        stage = self._current_stage()
        outcome = stage.tactical.victory if result is TacticalOutcome.VICTORY else stage.tactical.escape
        await self._finish_fight(outcome)

    async def _on_combat_end(self, result: CombatOutcome) -> None:
        character = self.app.character
        if result is CombatOutcome.DEAD:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return

        if result is CombatOutcome.KNOCKED_OUT:
            roll = self.app.rng.randint(1, 6)
            if roll <= 2:
                self.app.exit(message=f"{character.name} didn't wake up. Game over.")
                return
            character.cash //= 2
            character.health = 1
            msg = "Most of your creds are gone." if roll <= 4 else "At least you're alive."
            self.notify("You came to in an alley. " + msg)
            if self.scene.kind == SceneKind.JOB:
                character.adjust_fixer_trust(self.scene.target_fixer_id, JOB_FAILURE_TRUST_HIT)
                character.adjust_rep(JOB_FAILURE_REP_HIT)
                character.remove_job(self.scene.id)
            elif self.scene.kind == SceneKind.GIG:
                character.adjust_local_standing(self.scene.target_character_id, GIG_FAIL_STANDING_HIT)
                character.adjust_rep(GIG_FAIL_REP_HIT)
                self.app.location_gigs.pop(self.scene.target_location_id, None)
            self.app.pop_screen()
            return

        stage = self._current_stage()
        outcome = stage.combat.victory if result is CombatOutcome.VICTORY else stage.combat.escape
        await self._finish_fight(outcome)

    async def _finish_fight(self, outcome) -> None:
        apply_outcome(self.app.character, outcome, self.scene)
        self._take_crew_cut(outcome)
        self.query_one(CharacterSheet).refresh()
        self.query_one("#prompt", Static).update(outcome.text)
        await self._await_continue(outcome.next_stage, None)

    def _take_crew_cut(self, outcome) -> None:
        if self.scene.kind is not SceneKind.JOB or outcome.next_stage is not None or outcome.cash_delta <= 0:
            return
        character = self.app.character
        for hire in character.crew_for_job(self.scene.id):
            runner = RUNNERS_BY_ID[hire.runner_id]
            cut = min(int(runner.job_cut * outcome.cash_delta), character.cash)
            character.cash -= cut
            self.notify(f"{runner.name} takes {cut}eb — their cut of the job.")

    async def _advance(self) -> None:
        if self._pending_next_stage is None:
            if self.scene.kind == SceneKind.JOB:
                self.app.character.remove_job(self.scene.id)
            elif self.scene.kind == SceneKind.GIG:
                self.app.location_gigs.pop(self.scene.target_location_id, None)
            self.app.pop_screen()
            return

        stage = self.scene.stages[self._pending_next_stage]
        if stage.combat is not None:
            await self._show_combat(stage)
            return
        if stage.tactical is not None:
            await self._show_tactical(stage)
            return

        self.stage_id = self._pending_next_stage
        await self._show_stage()
