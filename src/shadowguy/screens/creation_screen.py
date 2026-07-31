from textual.app import ComposeResult
from textual.containers import Grid, ScrollableContainer, Vertical
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.character import CORE_STATS, MAX_SKILL_RANK
from shadowguy.skills import SKILLS, skill_for

from . import (
    MENU_BACK_BINDINGS,
    PANEL_NAV_BINDINGS,
    BackScreen,
    CharacterSheet,
    PanelNav,
    _compact_skill_label,
    _replace_items,
)


class CharacterCreationScreen(PanelNav, BackScreen):
    """The hand-spend build screen. Reached either straight from BuildSelectScreen
    with both pools full, or from ArchetypeSelectScreen with a preset already
    applied and nothing left to spend (r resets it back to a blank build).

    A BackScreen since the presets moved off it: escape returns to whichever of
    those pushed it, so picking the wrong one isn't a dead end. Every route in
    (both of those, plus app.restart_run/load_state) puts BuildSelectScreen
    underneath, so there is always somewhere to pop back to. The build is left
    alone on the way out -- r is what blanks it."""

    PANEL_IDS = (*(f"build_list_{stat}" for stat in CORE_STATS), "begin_row")

    BINDINGS = [
        *MENU_BACK_BINDINGS,
        ("r", "reset", "Reset build"),
        ("b", "begin", "Begin run"),
        *PANEL_NAV_BINDINGS,
    ]

    CSS = """
    #pools {
        padding: 0 1;
    }

    /* The six build columns scroll as one region between the pools line and the begin
       row. Sized 1fr so it takes whatever the fixed chrome leaves, which is what keeps
       #begin on screen at 80x24 -- the run was unstartable back when the preset cards
       shared this scroller and pushed it past the bottom edge. */
    #build_scroll {
        height: 1fr;
    }

    #build_grid {
        grid-size: 3 2;
        grid-gutter: 1 2;
        height: auto;
    }

    .build_column {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }

    .build_column ListView {
        /* Bounded for the same reason SkillsScreen's columns are: an auto-height
           ListView is as tall as its content, and scrolling a row into view only makes
           it visible inside its own container -- so #build_scroll alone could not reach
           the bottom of an 11-skill column. Capped, the list scrolls itself and every
           rank is buyable at any terminal size. Tighter than SkillsScreen's equivalent
           because this screen also carries the begin row: 8 is what still leaves the
           last logic row fully on an 80x24 terminal. */
        height: auto;
        max-height: 8;
    }

    .build_column ListView:focus {
        background: $boost;
    }

    #begin_row {
        height: auto;
        border: round $success;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="pools")
        yield ScrollableContainer(
            Grid(
                *(
                    Vertical(
                        Static(id=f"build_head_{stat}"),
                        ListView(id=f"build_list_{stat}"),
                        classes="build_column",
                    )
                    for stat in CORE_STATS
                ),
                id="build_grid",
            ),
            id="build_scroll",
        )
        yield ListView(id="begin_row")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    def _unspent(self) -> int:
        character = self.app.character
        return character.stat_points + character.skill_points

    async def action_reset(self) -> None:
        self.app.character.reset_build()
        self.query_one(CharacterSheet).refresh()
        await self._refresh()

    def action_begin(self) -> None:
        if self._unspent():
            self.notify("Spend every point before the run starts.", severity="warning")
            return
        self.app.begin_run()

    def _update_pools(self) -> None:
        character = self.app.character
        self.query_one("#pools", Static).update(
            f"Stat points: {character.stat_points}   Skill points: {character.skill_points}"
            "   —   enter spends · left/right change panel · r resets · b begins"
        )

    async def _refresh_column(self, stat: str, index: int = 0) -> None:
        character = self.app.character
        self.query_one(f"#build_head_{stat}", Static).update(f"{stat.capitalize()} — {character.stat(stat)}")
        items = [ListItem(Static(f"Raise {stat.capitalize()}\n  1 stat point"), id=f"stat_{stat}")]
        items += [
            ListItem(Static(_compact_skill_label(character, skill, show_cost=True)), id=f"skill_{skill.id}")
            for skill in SKILLS
            if skill.stat == stat
        ]
        await _replace_items(self.query_one(f"#build_list_{stat}", ListView), items, index)

    async def _refresh_begin(self) -> None:
        unspent = self._unspent()
        label = "Begin run" if not unspent else f"Begin run — {unspent} points unspent"
        await _replace_items(self.query_one("#begin_row", ListView), [ListItem(Static(label), id="begin")])

    async def _refresh(self) -> None:
        self._update_pools()
        for stat in CORE_STATS:
            await self._refresh_column(stat)
        await self._refresh_begin()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "begin":
            self.action_begin()
            return

        character = self.app.character
        index = event.list_view.index or 0
        if item_id.startswith("stat_"):
            stat = item_id.removeprefix("stat_")
            if not character.spend_stat_point(stat):
                self.notify("No stat points left.", severity="warning")
        else:
            skill_id = item_id.removeprefix("skill_")
            stat = skill_for(skill_id).stat
            name = skill_for(skill_id).name
            cost = character.next_rank_cost(skill_id)
            if cost is None:
                self.notify(f"{name} is already at rank {MAX_SKILL_RANK}.", severity="warning")
            elif not character.spend_skill_point(skill_id):
                self.notify(
                    f"{name} rank {character.skill_rank(skill_id) + 1} costs {cost} points; "
                    f"you have {character.skill_points}.",
                    severity="warning",
                )

        self.query_one(CharacterSheet).refresh()
        self._update_pools()
        await self._refresh_column(stat, index)
        await self._refresh_begin()
