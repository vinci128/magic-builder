import os

import anthropic

from collection import OwnedCard


def get_deck_review(commander: OwnedCard, deck: list) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "[Claude review skipped — set ANTHROPIC_API_KEY to enable]"

    client = anthropic.Anthropic(api_key=api_key)

    ci_str = "".join(commander.color_identity) if commander.color_identity else "Colorless"
    card_list = "\n".join(
        f"  {c.name} [{c.type_line}] (CMC {int(c.cmc)})"
        for c in deck
    )

    prompt = f"""You are a Magic: The Gathering Commander deck expert.

Commander: {commander.name}
Color identity: {ci_str}
Commander text: {commander.oracle_text}

Proposed 99-card list:
{card_list}

Please evaluate this deck and provide a concise analysis covering:
1. Overall strategy and win conditions
2. Power level (1–10, where 10 is cEDH)
3. Key strengths of this specific build
4. Glaring weaknesses or missing pieces
5. Top 3 swap suggestions (prefer cards already in the list above; if suggesting adds, note they may not be owned)

Be specific with card names and explain the synergies you see."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system="You are a knowledgeable Magic: The Gathering Commander format expert. Give practical, specific deck-building advice.",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
