# Outbound Agent

Kevin's weekly outbound agent: company name in, 5 accounts with champion emails and a lemlist sequence out. The playbook is `.claude/skills/outbound-agent/SKILL.md`; follow it stage by stage.

- This repo is **public**. People's emails, phones and LinkedIn URLs only ever go in `runs/` (git-ignored). Never commit `runs/`, never paste a champion's email into a tracked file.
- Apollo: at most 6 credits a run (1 company search + ≤5 reveals, one per lead). No phone reveals, no waterfall, no organization enrich.
- lemlist: build and load the campaign with Kevin as its sender, never launch it, never turn on lemlist enrichment. Emails go out Day 1, Day 3, Day 5 (delays 0, 2, 2) once someone launches it, which the agent never does.
- Copy people read is in Kevin's voice (`references/voice.md`, or the `kevin-voice` skill when available).
- Python is standard library only, except `agent/llm.py` in API mode (the `anthropic` SDK) and `certifi` for TLS (requirements.txt). The standalone run reaches Claude through Claude Code (`claude -p`) on Kevin's plan by default; `OUTBOUND_CLAUDE=api` switches to the API. Run `python3 -m unittest discover -s tests` after changing `agent/` (the tests fake Claude, Apollo and lemlist and need no package).
