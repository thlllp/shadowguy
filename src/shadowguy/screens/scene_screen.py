from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.checks import CheckResult
from shadowguy.abstract_combat import CombatOutcome
from shadowguy.combat import crew_stats, drop_for_result
from shadowguy.gigs import GIG_FAIL_REP_HIT, GIG_FAIL_STANDING_HIT
from shadowguy.jobs import JOB_FAILURE_REP_HIT, JOB_FAILURE_TRUST_HIT
from shadowguy.matrix import MatrixOutcome
from shadowguy.runners import complete_job, recruit_cut
from shadowguy.skills import skill_value
from shadowguy.scene import Scene, SceneKind, apply_outcome, resolve_choice, resolve_entrance
from shadowguy.support import support_for
from shadowguy.tactical import TacticalOutcome

from . import MENU_QUIT_BINDINGS, CharacterSheet, _replace_items
from .burglary_screens import EntrancePickScreen
from .combat_screen import CombatScreen
from .matrix_screen import MatrixScreen
from .tactical_screen import TacticalScreen


class SceneScreen(Screen):
    BINDINGS = MENU_QUIT_BINDINGS

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
        stage = self._current_stage()
        if stage.burglary is not None:
            await self._show_burglary(stage)
            return
        if stage.narration is not None:
            await self._show_narration(stage)
            return
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

        result, outcome = resolve_choice(
            self.app.character, self.scene, choice, self.app.corp_map
        )
        if not self._show_resolved(result, outcome):
            return
        await self._await_continue(outcome.next_stage, result)

    def _show_resolved(self, result: CheckResult, outcome) -> bool:
        """Report an already-applied check: bank the crew's cut, redraw, and flash the
        prompt on a critical. Returns whether the runner is still alive to go on —
        False means the run is over and the caller must stop."""
        character = self.app.character
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
            return False
        return True

    async def _await_continue(self, next_stage: str | None, result: CheckResult | None) -> None:
        self._pending_next_stage = next_stage
        self._pending_result = result
        await _replace_items(self.query_one("#choices", ListView), [ListItem(Static("Continue"), id="continue")])
        self.awaiting_continue = True

    async def _show_combat(self, stage) -> None:
        self.stage_id = stage.id
        self.app.push_screen(
            CombatScreen(stage.combat, drop_for_result(self._pending_result)),
            self._on_combat_end,
        )

    async def _show_tactical(self, stage) -> None:
        self.stage_id = stage.id
        self.app.push_screen(TacticalScreen(stage.tactical, allies=self._crew_units()), self._on_tactical_end)

    def _support(self):
        """The remote hacker backing this job, if one was hired as support. Same read-it-
        when-the-fight-opens rule as _crew_units, and None for anything that isn't a job."""
        if self.scene.kind is not SceneKind.JOB:
            return None
        return support_for(self.app.character.crew_support(self.scene.id), self.app.runners)

    def _crew_units(self) -> list:
        """The hired runners who fight this stage beside the player, as stat blocks. Read
        off the Character at the moment the fight opens rather than baked into the stage
        at generation, so hiring someone between accepting a job and walking it counts.
        Only jobs have a crew (a gig is solo work), and only the grid fields them —
        combat.py's abstract fight has no positions to put a second body in.

        `crew_on_site`, not `crew_working`: a hire taken on as remote support isn't at
        the location and doesn't get a body on the map."""
        if self.scene.kind is not SceneKind.JOB:
            return []
        character = self.app.character
        return [crew_stats(self.app.runner(hire.runner_id)) for hire in character.crew_on_site(self.scene.id)]

    async def _on_tactical_end(self, result: TacticalOutcome) -> None:
        character = self.app.character
        if result is TacticalOutcome.DEAD:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return
        stage = self._current_stage()
        outcome = stage.tactical.victory if result is TacticalOutcome.VICTORY else stage.tactical.escape
        await self._finish_stage_outcome(outcome)

    async def _show_matrix(self, stage) -> None:
        self.stage_id = stage.id
        self.app.push_screen(
            MatrixScreen(stage.matrix, drop_for_result(self._pending_result)),
            self._on_matrix_end,
        )

    async def _on_matrix_end(self, result: MatrixOutcome) -> None:
        # No death branch: a remote hack ejects, it doesn't kill (see matrix.py). SEIZED
        # advances/pays like any victory; EJECTED (integrity gone or a jack-out) is the
        # contract blown, the same escape Outcome a lost meat fight uses.
        stage = self._current_stage()
        outcome = stage.matrix.victory if result is MatrixOutcome.SEIZED else stage.matrix.escape
        await self._finish_stage_outcome(outcome)

    async def _show_burglary(self, stage) -> None:
        self.stage_id = stage.id
        self.app.push_screen(EntrancePickScreen(stage.burglary), self._on_entrance_picked)

    async def _show_narration(self, stage) -> None:
        """No roll, nothing to click -- the beat's Outcome applies immediately and the
        player just reads it. _finish_stage_outcome already does everything a plain
        beat with a resolved Outcome needs (combat/tactical/matrix reuse it too)."""
        self.stage_id = stage.id
        await self._finish_stage_outcome(stage.narration)

    async def _on_entrance_picked(self, chosen_index: int) -> None:
        stage = self._current_stage()
        entrance = stage.burglary.entrances[chosen_index]
        result, outcome = resolve_entrance(
            self.app.character, self.scene, entrance, self.app.corp_map
        )
        if not self._show_resolved(result, outcome):
            return

        # An entrance's check resolves (and applies, via resolve_entrance above)
        # immediately, same as any Choice -- but a critical failure or the
        # always-fights ambush routes straight to the fight, skipping the walk
        # entirely, the same door every other stage's critical failure uses.
        # outcome is already applied, so this is a plain advance (_await_continue),
        # not a re-apply (_finish_stage_outcome would double-apply it).
        target = self.scene.stages[outcome.next_stage]
        if target.combat is not None or target.tactical is not None or target.matrix is not None:
            await self._await_continue(outcome.next_stage, result)
            return

        # Stash what to resume with once the walk ends -- the same _pending_*
        # fields _await_continue always uses, not a burglary-only duplicate.
        self._pending_next_stage = outcome.next_stage
        self._pending_result = result
        self.app.push_screen(
            TacticalScreen(
                stage.burglary,
                allies=self._crew_units(),
                spawn=entrance.spawn,
                support=self._support(),
            ),
            self._on_burglary_end,
        )

    async def _on_burglary_end(self, result: TacticalOutcome) -> None:
        """How an infiltration ended. Reaching the score is the only success; walking
        back out an entrance is bailing, and dying is dying. Clearing every guard doesn't
        end it at all (see tactical._settle), so there's no VICTORY branch to write."""
        character = self.app.character
        if result is TacticalOutcome.DEAD:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return
        if result is not TacticalOutcome.SECURED:
            # A fresh Outcome, never applied yet -- same shape as a fight ending. Walking
            # out empty-handed is what a critical failure represents everywhere else, so
            # it hands the enemy the same drop (combat.drop_for_result) on the way into
            # the fight it routes to.
            stage = self._current_stage()
            await self._finish_stage_outcome(stage.burglary.bailed, result=CheckResult.CRITICAL_FAILURE)
            return
        # Got it -- the entrance check's Outcome (health/cash/rep/etc) already applied at
        # pick time; only stage advancement waited on the infiltration, so this is a
        # plain advance, not a re-apply.
        await self._await_continue(self._pending_next_stage, self._pending_result)

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
        await self._finish_stage_outcome(outcome)

    async def _finish_stage_outcome(self, outcome, result: CheckResult | None = None) -> None:
        character = self.app.character
        apply_outcome(character, outcome, self.scene, self.app.corp_map)
        if not character.is_alive:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return
        self._take_crew_cut(outcome)
        self.query_one(CharacterSheet).refresh()
        self.query_one("#prompt", Static).update(outcome.text)
        await self._await_continue(outcome.next_stage, result)

    def _take_crew_cut(self, outcome) -> None:
        if self.scene.kind is not SceneKind.JOB or outcome.next_stage is not None or outcome.cash_delta <= 0:
            return
        character = self.app.character
        leadership = skill_value(character, "leadership")
        for hire in character.crew_for_job(self.scene.id):
            runner = self.app.runner(hire.runner_id)
            cut = min(int(recruit_cut(runner, leadership) * outcome.cash_delta), character.cash)
            character.cash -= cut
            self.notify(f"{runner.name} takes {cut}eb — their cut of the job.")
            # Not split with the player's own XP (character.gain_experience already
            # credited the full amount via apply_outcome) — a crew member earns the
            # same job the same way the player did, in parallel, not out of a shared pot.
            character.grant_crew_experience(hire.runner_id, outcome.experience_delta)
            # And the same again on the runner's own sheet, which is where it does
            # something: crew_experience is the player's ledger of what a hire earned
            # working for them, complete_job is the hire getting better for it. Their cut
            # is the pay, so the money they spend on gear is money you handed them.
            bought = complete_job(runner, cut, outcome.experience_delta)
            if bought is not None:
                self.notify(f"{runner.name} spends their cut on {bought.name}.")

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
        if stage.matrix is not None:
            await self._show_matrix(stage)
            return
        if stage.burglary is not None:
            await self._show_burglary(stage)
            return
        if stage.narration is not None:
            await self._show_narration(stage)
            return

        self.stage_id = self._pending_next_stage
        await self._show_stage()
