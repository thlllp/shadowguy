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

GIG_FENCE_SOME_CHROME = Scene(
    id="gig_fence_some_chrome",
    title="Fence Some Chrome",
    kind=SceneKind.GIG,
    start_stage="start",
    stages={
        "start": Stage(
            id="start",
            prompt="A fence on Autopia Row eyes the cyberware you're carrying.",
            choices=[
                Choice(
                    label="Haggle for a fair price (Negotiations)",
                    skill="negotiations",
                    difficulty=10,
                    success=Outcome(text="You talk him up to a decent price.", cash_delta=150),
                    failure=Outcome(text="He lowballs you and won't budge.", cash_delta=50),
                    critical_success=Outcome(
                        text="He's impressed, pays top eddies for the lot.", cash_delta=300
                    ),
                    critical_failure=Outcome(
                        text="He laughs you out of the shop and talks you down around town.",
                        rep_delta=-1,
                    ),
                ),
                Choice(
                    label="Lean on him for a better cut (Intimidation)",
                    skill="intimidation",
                    difficulty=12,
                    success=Outcome(text="He pays up, and doesn't meet your eye.", cash_delta=200),
                    failure=Outcome(
                        text="He's been leaned on by worse. You take what he offers.", cash_delta=50
                    ),
                    critical_success=Outcome(
                        text="He empties the till and thanks you for your business.", cash_delta=350
                    ),
                    critical_failure=Outcome(
                        text="His muscle walks you out. Word gets around that you're desperate.",
                        health_delta=-3,
                        rep_delta=-1,
                    ),
                ),
            ],
        ),
    },
)

GIG_CHEM_TRIAL = Scene(
    id="gig_chem_trial",
    title="Chem Trial",
    kind=SceneKind.GIG,
    start_stage="start",
    stages={
        "start": Stage(
            id="start",
            prompt=(
                "A ripperdoc three floors below street level needs a body to test a batch on. "
                "It pays the same whether the batch is any good or not."
            ),
            choices=[
                Choice(
                    label="Take the full dose and ride it out (Resist Poison)",
                    skill="resist_poison",
                    difficulty=13,
                    success=Outcome(
                        text="Your liver files a complaint. The doc pays in full.", cash_delta=250
                    ),
                    failure=Outcome(
                        text="You come out of it shaking, and he docks you for the mess.",
                        health_delta=-5,
                        cash_delta=100,
                    ),
                    critical_success=Outcome(
                        text="You barely feel it. He wants you back next week.",
                        cash_delta=350,
                        rep_delta=1,
                    ),
                    critical_failure=Outcome(
                        text="The batch was bad. You wake up on the floor hours later.",
                        health_delta=-10,
                    ),
                ),
                Choice(
                    label="Let him culture something in you instead (Resist Disease)",
                    skill="resist_disease",
                    difficulty=12,
                    success=Outcome(
                        text="Whatever it was, your blood ate it. He pays and says nothing.",
                        cash_delta=220,
                    ),
                    failure=Outcome(
                        text="You run a fever for two days and get half the fee.",
                        health_delta=-4,
                        cash_delta=110,
                    ),
                    critical_success=Outcome(
                        text="It never takes. He pays extra for the blood work alone.",
                        cash_delta=320,
                    ),
                    critical_failure=Outcome(
                        text="It takes hold. You are sick in ways the doc will not name.",
                        health_delta=-9,
                    ),
                ),
            ],
        ),
    },
)

GIG_RING_FIGHT = Scene(
    id="gig_ring_fight",
    title="Ring Fight",
    kind=SceneKind.GIG,
    start_stage="start",
    stages={
        "start": Stage(
            id="start",
            prompt=(
                "A basement ring under a noodle bar. The crowd has money down, "
                "and the fighter across from you has done this before."
            ),
            choices=[
                Choice(
                    label="Fight it clean with the blade (Long Blade)",
                    skill="long_blade",
                    difficulty=13,
                    success=Outcome(
                        text="Three exchanges and it's over. The crowd pays out.",
                        cash_delta=250,
                        rep_delta=1,
                    ),
                    failure=Outcome(
                        text="You lose on points, bleeding from a shoulder you didn't guard.",
                        health_delta=-6,
                    ),
                    critical_success=Outcome(
                        text="One movement, and they're down. The room goes quiet, then loud.",
                        cash_delta=400,
                        rep_delta=2,
                    ),
                    critical_failure=Outcome(
                        text="You overcommit and they open you up. Someone drags you out.",
                        health_delta=-12,
                    ),
                ),
                Choice(
                    label="Let them swing until they tire (Center of Gravity)",
                    skill="center_of_gravity",
                    difficulty=12,
                    success=Outcome(
                        text="You stay on your feet through all of it. They don't.",
                        cash_delta=200,
                        rep_delta=1,
                    ),
                    failure=Outcome(
                        text="You go down in the fourth. The crowd got its money's worth.",
                        health_delta=-5,
                    ),
                    critical_success=Outcome(
                        text="They punch themselves out on you, and fall over unaided.",
                        cash_delta=330,
                        rep_delta=2,
                    ),
                    critical_failure=Outcome(
                        text="You go down in the first, and they keep going.", health_delta=-11
                    ),
                ),
                Choice(
                    label="Stare them down before the bell (Intimidation)",
                    skill="intimidation",
                    difficulty=15,
                    success=Outcome(
                        text="They fight like someone looking for the exit. It's short.",
                        cash_delta=180,
                        rep_delta=1,
                    ),
                    failure=Outcome(
                        text="They aren't impressed, and they make the point with their fists.",
                        health_delta=-6,
                    ),
                    critical_success=Outcome(
                        text="They don't come out for the bell. You are paid for a fight "
                        "that never happened.",
                        cash_delta=300,
                        rep_delta=2,
                    ),
                    critical_failure=Outcome(
                        text="The crowd laughs at you. So does the fighter, the whole way through.",
                        health_delta=-8,
                        rep_delta=-1,
                    ),
                ),
            ],
        ),
    },
)

GIG_CARD_TABLE = Scene(
    id="gig_card_table",
    title="The Back Table",
    kind=SceneKind.GIG,
    start_stage="start",
    stages={
        "start": Stage(
            id="start",
            prompt=(
                "A long-running game in the back of a laundromat. "
                "The buy-in is steep and nobody at the table is honest."
            ),
            choices=[
                Choice(
                    label="Play the players, not the cards (Read Face)",
                    skill="read_face",
                    difficulty=13,
                    success=Outcome(text="You know their hands before they do.", cash_delta=280),
                    failure=Outcome(text="You misread the quiet one and pay for it.", cash_delta=-120),
                    critical_success=Outcome(
                        text="You clean the table without ever showing a card.",
                        cash_delta=500,
                        rep_delta=1,
                    ),
                    critical_failure=Outcome(
                        text="The whole table was reading you. You leave light.", cash_delta=-250
                    ),
                ),
                Choice(
                    label="Bet like you have it whether you do or not (Deception)",
                    skill="deception",
                    difficulty=12,
                    success=Outcome(
                        text="You take three pots without a hand worth playing.", cash_delta=220
                    ),
                    failure=Outcome(text="They call you on the big one.", cash_delta=-100),
                    critical_success=Outcome(
                        text="You bluff a full table off a pot you had no business winning.",
                        cash_delta=420,
                        rep_delta=1,
                    ),
                    critical_failure=Outcome(
                        text="They've had your tell since the first hour, and they let you bleed.",
                        cash_delta=-220,
                    ),
                ),
                Choice(
                    label="Improve your hand on the way past (Sleight of Hand)",
                    skill="sleight_of_hand",
                    difficulty=15,
                    success=Outcome(text="Nobody sees the swap. Nobody ever does.", cash_delta=400),
                    failure=Outcome(text="You lose your nerve and play it straight.", cash_delta=-80),
                    critical_success=Outcome(
                        text="You deal yourself the whole night, and they thank you for the game.",
                        cash_delta=650,
                    ),
                    critical_failure=Outcome(
                        text="A hand closes on your wrist. They take the pot and a finger's worth of skin.",
                        health_delta=-6,
                        cash_delta=-200,
                        rep_delta=-1,
                    ),
                ),
            ],
        ),
    },
)

GIG_STREET_WHISPERS = Scene(
    id="gig_street_whispers",
    title="Street Whispers",
    kind=SceneKind.GIG,
    start_stage="start",
    stages={
        "start": Stage(
            id="start",
            prompt=(
                "An info broker will pay for anything worth knowing about corp movements "
                "this week. She doesn't care how you come by it."
            ),
            choices=[
                Choice(
                    label="Sit in the right bars and say nothing (Listening)",
                    skill="listening",
                    difficulty=11,
                    success=Outcome(text="Two loose conversations, one worth selling.", cash_delta=180),
                    failure=Outcome(text="A week of bad coffee and nothing to show for it."),
                    critical_success=Outcome(
                        text="You overhear a name that shouldn't have been said aloud.",
                        cash_delta=320,
                        rep_delta=1,
                    ),
                    critical_failure=Outcome(
                        text="You're made as a listener. The bar goes quiet when you walk in now.",
                        rep_delta=-1,
                    ),
                ),
                Choice(
                    label="Case their movements yourself (Recon)",
                    skill="recon",
                    difficulty=13,
                    success=Outcome(text="You bring her a schedule she can actually use.", cash_delta=250),
                    failure=Outcome(text="You watch the wrong building for three days."),
                    critical_success=Outcome(
                        text="You bring her the rotation, the vehicles, and a face she recognises.",
                        cash_delta=400,
                        rep_delta=1,
                    ),
                    critical_failure=Outcome(
                        text="Corp security clocks you watching and moves you along, hard.",
                        health_delta=-4,
                    ),
                ),
                Choice(
                    label="Make up something good and sell it as real (Forgery)",
                    skill="forgery",
                    difficulty=14,
                    success=Outcome(
                        text="An internal memo that never existed. She pays without blinking.",
                        cash_delta=300,
                    ),
                    failure=Outcome(text="She reads two lines, hands it back, and says nothing."),
                    critical_success=Outcome(
                        text="Your memo is good enough that it turns out to be true. "
                        "She pays twice and asks no questions.",
                        cash_delta=500,
                    ),
                    critical_failure=Outcome(
                        text="She acts on it, loses money, and makes sure the street knows whose paper it was.",
                        rep_delta=-2,
                    ),
                ),
            ],
        ),
    },
)

GIG_WORK_A_MARK = Scene(
    id="gig_work_a_mark",
    title="Work a Mark",
    kind=SceneKind.GIG,
    start_stage="start",
    stages={
        "start": Stage(
            id="start",
            prompt=(
                "A mid-level corp suit drinks alone in a rooftop bar, badge still clipped on. "
                "There's a way to leave here with more than you came with."
            ),
            choices=[
                Choice(
                    label="Give them the evening they wanted (Seduction)",
                    skill="seduction",
                    difficulty=13,
                    success=Outcome(
                        text="They pay for everything, and hand you their card at the door.",
                        cash_delta=260,
                    ),
                    failure=Outcome(text="They're married, bored, and not interested."),
                    critical_success=Outcome(
                        text="They talk all night. Half of it was worth money.",
                        cash_delta=420,
                        rep_delta=1,
                    ),
                    critical_failure=Outcome(
                        text="They call you what you are, loudly, in front of the room.",
                        rep_delta=-1,
                    ),
                ),
                Choice(
                    label="Find the sore spot and lean on it (Read the Room)",
                    skill="read_the_room",
                    difficulty=12,
                    success=Outcome(
                        text="They hate their boss. You let them talk, and take notes.",
                        cash_delta=200,
                    ),
                    failure=Outcome(text="You misjudge the mood and they close up."),
                    critical_success=Outcome(
                        text="They hate their boss enough to prove it, and hand you the proof.",
                        cash_delta=340,
                        rep_delta=1,
                    ),
                    critical_failure=Outcome(
                        text="You push a bruise that isn't there. They call security.",
                        health_delta=-3,
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
