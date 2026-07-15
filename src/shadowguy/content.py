"""Hand-authored example scenes.

These predate the procedural systems (fixer jobs in jobs.py, per-location gigs in
gigs.py) and are no longer wired into app.py — kept as worked examples of the
Scene/Stage/Choice/Outcome data model. The static gigs that used to live here were
retired when gigs became procedural and per-location.
"""

from shadowguy.scene import Choice, Outcome, Scene, SceneKind, Stage

LEGWORK_CASE_THE_BLOCK = Scene(
    id="legwork_case_the_block",
    title="Case the Block",
    kind=SceneKind.LEGWORK,
    prepares_for="job_data_heist",
    start_stage="start",
    stages={
        "start": Stage(
            id="start",
            prompt="You spend a night watching the corp tower, clocking patrol rotations.",
            choices=[
                Choice(
                    label="Track the guard rotations (Pattern Seeking)",
                    skill="pattern_seeking",
                    difficulty=11,
                    success=Outcome(
                        text="You clock the pattern cold. You'll know exactly when to move.",
                        advantage_delta=3,
                    ),
                    failure=Outcome(text="The patrols never settle into a pattern. Wasted night."),
                    critical_failure=Outcome(
                        text="A guard clocks you watching. You bolt before it gets worse.",
                        health_delta=-2,
                    ),
                ),
            ],
        ),
    },
)

JOB_DATA_HEIST = Scene(
    id="job_data_heist",
    title="Data Heist",
    kind=SceneKind.JOB,
    stamina_cost=2,
    start_stage="infiltrate",
    stages={
        "infiltrate": Stage(
            id="infiltrate",
            prompt="Arasaka's tower looms above. You need a way past the perimeter.",
            choices=[
                Choice(
                    label="Slip past the guard drones (Stealth)",
                    skill="stealth",
                    difficulty=12,
                    success=Outcome(text="You thread the drone patrol without a flicker.", next_stage="vault"),
                    failure=Outcome(
                        text="A drone clips your trail. You bail before it locks on.",
                        health_delta=-2,
                    ),
                    critical_failure=Outcome(
                        text="Spotted cold. Alarms blare as you scramble out.",
                        health_delta=-6,
                    ),
                ),
                Choice(
                    label="Hack the service door (Hack)",
                    skill="hack",
                    difficulty=13,
                    success=Outcome(text="The lock clicks open silently.", next_stage="vault"),
                    failure=Outcome(
                        text="The lock jams and half-triggers an alert. You pull back.",
                        health_delta=-2,
                    ),
                    critical_failure=Outcome(
                        text="The panel arcs and fries your deck's uplink. Aborted.",
                        health_delta=-4,
                    ),
                ),
            ],
        ),
        "vault": Stage(
            id="vault",
            prompt="You're in. The data vault's encryption is live and watching.",
            choices=[
                Choice(
                    label="Crack the encryption (Hack)",
                    skill="hack",
                    difficulty=14,
                    success=Outcome(text="The vault peels open. Data's yours.", next_stage="extract"),
                    failure=Outcome(
                        text="A defense turret wakes up and clips you on the way past.",
                        health_delta=-4,
                        next_stage="extract",
                    ),
                    critical_success=Outcome(
                        text="You crack it clean and skim extra corp files on the way out.",
                        cash_delta=200,
                        next_stage="extract",
                    ),
                    critical_failure=Outcome(
                        text="The vault fries back. Feedback slams through your deck.",
                        health_delta=-8,
                        next_stage="extract",
                    ),
                ),
            ],
        ),
        "extract": Stage(
            id="extract",
            prompt="Lockdown is minutes out. You need to get clear of the tower.",
            choices=[
                Choice(
                    label="Fight through security (Toughness)",
                    skill="toughness",
                    difficulty=13,
                    success=Outcome(text="You put down the response team and clear the exit.", cash_delta=500, rep_delta=2),
                    failure=Outcome(text="You get out, bloodied and empty-handed.", health_delta=-5),
                    critical_failure=Outcome(
                        text="The response team doesn't miss twice.", health_delta=-10
                    ),
                ),
                Choice(
                    label="Bluff your way past the checkpoint (Deception)",
                    skill="deception",
                    difficulty=15,
                    success=Outcome(text="They wave you through without a second look.", cash_delta=500, rep_delta=1),
                    failure=Outcome(text="They see through it. You have to run for it.", health_delta=-5),
                    critical_failure=Outcome(
                        text="They call it in immediately. You're cut down fleeing.",
                        health_delta=-10,
                    ),
                ),
            ],
        ),
    },
)
