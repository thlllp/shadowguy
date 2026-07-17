from textual.app import ComposeResult
from textual.containers import Grid, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

import shadowguy.archetypes as archetypes
from shadowguy.character import CORE_STATS, MAX_SKILL_RANK
from shadowguy.skills import SKILLS, skill_for

from . import (
    PANEL_NAV_BINDINGS,
    CharacterSheet,
    PanelNav,
    _compact_skill_label,
    _replace_items,
)
from .main_menu import MainMenu


class CharacterCreationScreen(PanelNav, Screen):
    # PANEL_IDS is set per-instance in __init__, not here: archetypes.ARCHETYPES is
    # lazily validated on first access (see archetypes.py), and a class body runs at
    # module import time, so building this tuple here would defeat that laziness the
    # moment this screen module is imported.
    BINDINGS = [
        ("q", "quit_menu", "Menu"),
        ("r", "reset", "Reset build"),
        ("b", "begin", "Begin run"),
        *PANEL_NAV_BINDINGS,
    ]

    CSS = """
    #pools {
        padding: 0 1;
    }

    #arch_grid {
        grid-size: 3 1;
        grid-gutter: 0 1;
        height: auto;
    }

    .arch_card {
        height: auto;
        border: round $accent;
        padding: 0 1;
    }

    .arch_card:focus {
        border: round $secondary;
    }

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
        height: auto;
    }

    .build_column ListView:focus {
        background: $boost;
    }

    #begin_row {
        height: auto;
        border: round $success;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.PANEL_IDS = (
            *(f"arch_card_{a.id}" for a in archetypes.ARCHETYPES),
            *(f"build_list_{stat}" for stat in CORE_STATS),
            "begin_row",
        )

    def _arch_card(self, archetype) -> ListView:
        card = ListView(
            ListItem(Static(archetype.description), id=f"arch_{archetype.id}"),
            id=f"arch_card_{archetype.id}",
            classes="arch_card",
        )
        card.border_title = archetype.name
        return card

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="pools")
        yield Grid(*(self._arch_card(a) for a in archetypes.ARCHETYPES), id="arch_grid")
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
        self.app.switch_screen(MainMenu())

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

        if item_id.startswith("arch_"):
            archetype = archetypes.ARCHETYPES_BY_ID[item_id.removeprefix("arch_")]
            character.reset_build()
            archetype.apply(character)
            self.notify(f"{archetype.name} build applied. Press b to begin, r to start over.")
            self.query_one(CharacterSheet).refresh()
            await self._refresh()
            return

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
