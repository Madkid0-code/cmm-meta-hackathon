"""The onboarding form — the top of the CMM chain.

In production CMM the chain starts at `candidates`: a person's approved form
becomes their sweep config (the 'shopper' step) and their attested profile.
Same here. One JSON form in, two artifacts out:

1. an attested-profile markdown file (the ONLY skills the system may ever
   claim — evidence attached to every term), and
2. a sweep_configs row (what the sweeper shops for).

The seeker attests; the system never invents. That boundary is the product.
"""
import json


def load_form(path):
    with open(path) as f:
        form = json.load(f)
    for key in ("name", "search", "attested_skills"):
        if key not in form:
            raise ValueError(f"form is missing '{key}'")
    # A brand-new account starts empty: no keywords and no attested skills
    # yet. The UI renders the empty editors so the seeker can create their
    # profile; the chain only runs once they save and scan.
    form["search"].setdefault("keywords", [])
    form.setdefault("attested_skills", [])
    return form


def render_profile_md(form):
    """Attested profile in the exact format the verifier parses."""
    lines = [
        f"# Attested profile — {form['name']}",
        "",
        "Every claim below was attested by the seeker on their onboarding form,",
        "with evidence. The system may claim ONLY what is listed here.",
        "",
        "## Attested skills",
    ]
    for skill in form["attested_skills"]:
        lines.append(f"- {skill['term']} :: {skill['evidence']}")
    boundaries = form.get("hard_boundaries") or []
    if boundaries:
        lines += ["", "## Hard boundaries — never claimed"]
        for b in boundaries:
            lines.append(f"- {b}")
    return "\n".join(lines) + "\n"
