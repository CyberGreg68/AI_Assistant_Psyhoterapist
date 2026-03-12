Load trigger files from locales/hu/triggers/*.hu.json after loading the Hungarian manifest and phrase bundle.
At runtime, sort candidate triggers by prio ascending, then prefer the strongest tag overlap with detected patient context.
For the selected trigger, iterate cand in order and use the first phrase id that exists in the active phrase bundle.
If no candidate phrase is usable, apply fb in this order: use_variant, ask_clarifying, call_llm, escalate.
