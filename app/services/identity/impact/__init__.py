"""What an extension changes in igni, and where two extensions collide.

An extension can take over a great deal of igni — the whole of the
agent's prompt, if that is what a deployment wants. That is allowed on
purpose, and it makes one question unavoidable: what happens when two
extensions want the same thing? A super admin answers it once, for every
case, by putting the extensions in one **priority** order
(`app.services.identity.priority.ExtensionPriority`): the higher one wins, automatically, here on the
Services page and on every machine. Nothing is ever held back from
distribution for a collision.

This is igni server's half of a rule igni states too
(`src/igni_core/extensions/impact.py` in igni-core). The two are held to one
answer by `tests/fixtures/extension_impact_cases.json`, a file both
repositories carry and both run.

## Prompts: overrides and edits

Per prompt — `main`, `plan_mode`, `agent:<name>` — an extension overrides
it whole, or edits it: appends a section, inserts one before or after a
named section, replaces a named section. Edits from several extensions
stack, in priority order, and never collide unless they replace the same
section. An override is the new starting point: the others' edits land
on it where it has the section they name, and are *dropped* where it does
not — listed, not a collision.

## What collides, and who wins

A **claim** is something only one extension can have, spelled
`<kind>:<name>`. In every collision the extension highest in the priority
order keeps it, and each other loses that claim only:

* `prompt:<target>` — overriding a whole prompt. Of two overrides the
  higher wins. An `exclusive` override also takes on everyone who edits
  that prompt, and holds only above all of them: then their edits of it
  are off; if any is higher, the exclusive override is off and the edits
  stand (the next override down, if any, has the prompt);
* `prompt:<target>#<section>` — replacing one section;
* `command:/<name>` — a slash command;
* `anchor:<NAME>` — the text at a `{{NAME}}` in a prompt.

An insert or an append is spelled `prompt:<target>+<name>` where it has
to be named, and is never a claim. Pages, settings, tools and a plugin's
own agents are named in the extension's namespace and cannot collide;
grants only add. Hooks (`permissions.hooks`) never collide: igni asks
the extensions hooking an event in priority order, rejecting or changing. A `when`
condition does not avoid a collision. Every loss says why, beginning
`lower priority than <extension>`.

The package keeps one concern per module: the claim vocabulary
(`claims`), what a manifest may be granted (`grants`), reading a manifest
(`manifest`), what one extension changes (`contributions`,
`extension_impact`), igni's own catalogues (`igni_catalogues`), the
super admin's order (`priority_order`), and settling collisions
(`resolution`, `resolver`).
"""
