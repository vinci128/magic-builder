# Precon swaps — September 2026

Swaps for the three precons in `ManaBox_Collection_sept2026.csv`, using only
the loose cards in the `carte` binder (255 cards, mostly EOE / Hobbit /
Strixhaven / Spider-Man / Final Fantasy commons and uncommons). All three
decks were verified stock against their binders before this was written.

The post-swap lists, ready to import into ManaBox or Moxfield:

- `limit-break-upgraded.txt`
- `turtle-power-upgraded.txt`
- `revival-trance-upgraded.txt`

None of these swaps moves a deck out of Bracket 2 — nothing in the loose pile
is a Game Changer or a combo piece — but each tightens what the deck already
does. Cuts follow EDHREC's most-cut list for each precon.

The same question can be asked of any collection with
`python main.py <collection.csv> --precons`, or by uploading it to the web UI,
where the "Precons you own" panel appears when a precon is detected. That
scorer is heuristic (EDHREC upgrade data, the commander's page, the deck's own
rules-text themes, roles the precon is thin on); the picks below are a hand
review of the same pool and differ from it in places, noted where they do.

## Limit Break — Cloud, Ex-SOLDIER (Naya, Equipment, reach 7 power)

| Out | In | Why |
|---|---|---|
| Explorer's Scope | **Kíli the Resourceful** | First equip each turn is free (three artifacts/legendaries is always true here) and it draws a card whenever an Equipment enters. The best card in the pile for any of the three decks. |
| Elena, Turk Recruit | **Iron Hills Blacksmith** | Two-mana double striker that brings its own Equipment token — triggers Puresteel Paladin and Kíli. |
| Armory Automaton | **Stone by Sunlight** | Two-mana instant removal; the precon runs three removal spells averaging five mana. |
| Furious Rise | **Squire's Lightblade** | {W} flash Equipment that attaches itself: another equipped attacker is another card off Cloud's trigger, at instant speed. |
| Heidegger, Shinra Executive | **Thorin Oakenshield** | Two-mana 3/2 trample that gives every artifact and creature you control ward 1. |
| Secret Rendezvous | **Bulk Up** | Double Cloud's power in response to his attack trigger to hit the 7-power Treasure check, or before Tifa's extra-combat trigger. Flashback. |

Optional: a second Squire's Lightblade, Well-Worn Spatula ({1}, equip 1), Meltstrider's Gear.
Skip Vow to Erebor and Dwarven Mattock — they only work on Dwarves.
**Not legal:** Glamdring, Foe-hammer looks perfect but its Adventure half is blue,
so its colour identity is outside Naya. (The scorer caught this; the first
draft of this list had it in.)

## Turtle Power! — Leonardo, the Balance // Michelangelo, the Heart (5c, tokens → counters)

Helmed as you play it: Michelangelo and Leonardo (EDHREC's most popular
pairing, 4,164 decks). The engine is: attack → Michelangelo's Raid in the
second main phase makes a Food and puts a counter on something → the Food is a
token entering, so Leonardo puts a counter on every creature you control. That
fires once per turn and *after* combat, which is why tokens made before combat
are worth more than they look: they let Leonardo's team counters land before
attacks instead of after. Donatello and Splinter are in the 99 and still do
their thing when they are out, but nothing below depends on them.

Cuts are the precon cards the fewest Leo // Mikey decks on EDHREC keep — the
generic precon cut list is measured across every Turtle Power build and gets
this pairing wrong in places (see Tempestra, below).

| Out (kept by … of Leo // Mikey decks) | In | Why |
|---|---|---|
| Electric Seaweed (not listed) | **Silk, Web Weaver** | A token on every creature spell you cast — precombat, so Leonardo's counters go on before you attack instead of waiting for Michelangelo's Food. Web-slinging casts it for three by returning a tapped creature (a Mutagen-sacked one, say). Anthem activation for the alpha strike. |
| Roadkill Rodney (21%) | **Weftstalker Ardent** | Pings every opponent whenever a creature *or artifact* enters — the Food every turn, every Mutagen, every Treasure. |
| Krang, the All-Powerful (not listed) | **Rayblade Trooper** | ETB +1/+1 counter; whenever a countered creature dies, a Soldier token — with Leonardo, every creature is a countered creature. Warp for two. |
| Rat King, Pale Piper (11%) | **Feed the Swarm** | Clean two-mana removal that also answers enchantments. |
| Mole Module (7%) | **Blitzball Stadium** | Support X puts counters on the team; later makes a creature unblockable and draws a card per counter type — Michelangelo's Food and Leonardo's counters mean two kinds are easy. |

**Kept on purpose — Tempestra, Dame of Games.** She is on EDHREC's generic
cut list for the precon, and the first draft of this list cut her. Wrong for
this pairing: sacrifice a Mutagen (or Food) and the token copy of Leonardo
*entering* fires both Leonardos' once-per-turn triggers — two team-wide
counters, precombat, on a hasty 4/4 with its own WUBRG activation — and a copy
of Michelangelo is a second Raid. Leo // Mikey decks keep her at 27% against
18% for Heroes in a Half Shell decks, which is the same conclusion in the data.
Exploding Barrel also stays (29%; the deck needs its rocks for the WUBRG
activation). Next cuts if you keep buying: Acidic Slime (11%), Biogenic Ooze
(13%), Foot Chopper (17%), Harmonize (20%).

Optional: Biosynthic Burst (counter + indestructible + untap, protects a
commander for two), Drix Fatemaker, Selfcraft Mechan (sacrifice a Mutagen:
counter + card), Guy in the Chair (any-colour dork for Leonardo's WUBRG
activation), Thrumming Hivepool (two tokens every upkeep, a guaranteed
precombat Leonardo trigger, but six mana), Summon: Knights of Round as an
eight-mana finisher.

The scorer, run with `--precon-commanders "Leonardo, the Balance //
Michelangelo, the Heart"`, agrees on Rayblade Trooper and Blitzball Stadium
and ranks Silk lower than this list does (it can't see the combat-timing
argument); it also likes Astrologian's Planisphere and Fractal Anomaly, both
fine but small.

Your copy differs from EDHREC's list by five cards (yours has Voracious Hydra,
Vigor, Corpsejack Menace, Acidic Slime and Steelbane Hydra where EDHREC lists
Aggro Amalgam, Heralds of the Shredder, Humongous Fungus, Marauding Mutagen and
Slash Clone); the list here is built from your binder.

## Revival Trance — Terra, Herald of Hope (Mardu, reanimate power ≤ 3)

| Out | In | Why |
|---|---|---|
| Angel of the Ruins | **Zombify** | Unconditional four-mana reanimation; the deck has only Reanimate, Stitch Together and Rejoin the Fight. Angel is the other seven-drop and is on EDHREC's most-cut list; Meteor Golem stays as the deck's one catch-all answer to an enchantment or planeswalker, and Rejoin the Fight can cheat it back. |
| Interceptor, Shadow's Hound | **Umbral Collar Zealot** | 3/2 for two with a free sacrifice outlet that surveils — fills the graveyard, and at power 3 Terra brings it back. |
| Ruin Grinder | **Ahriman** | Flying deathtouch, sacrifice a creature or artifact to draw; power 2, so it loops with Terra. |
| Umaro, Raging Yeti | **Bitter Triumph** | Two-mana instant removal whose discard cost puts a creature in the graveyard for Terra. |

Optional: Charging Strifeknight (hasty 3/3 looter), Scorpion, Seething Striker
(connive whenever a creature died), Key to the Side-Door (makes Terra
unblockable so her damage trigger connects). The scorer also rates Summon:
Knights of Round highly here on EDHREC's data (38% of Terra decks run it); it
is eight mana, so it is left out of the list but is a fair finisher if the
Turtles don't take it.

## What was checked

- Every IN card is in the `carte` binder, commander-legal, and inside the
  deck's colour identity (script-verified, not eyeballed).
- Each post-swap list is exactly 100 cards.
- Cards were not double-booked across decks: Bitter Triumph → Terra, Feed the
  Swarm → Turtles, Stone by Sunlight → Cloud.
