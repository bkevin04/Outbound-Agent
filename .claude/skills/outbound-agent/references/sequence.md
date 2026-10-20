# The sequence: Kevin's three touches, built for lemlist

Kevin's outbound is three emails to one champion. The agent writes the same three touches for all ten champions: the structure and the company-level sentences go into the lemlist steps once, and what changes per account goes into lemlist custom variables on each lead. lemlist fills them in, so every email is personal and nothing is pasted per lead.

## The touches

**Touch 1, Day 1: open with the trigger**
- Subject `{{t1Subject}}`: the trigger in a few words, then "Then What?" ("$50M Series B, Then What?", "New Head of Support, Then What?").
- `Hi {{firstName}},`
- `{{t1Opener}}`: the trigger and what it usually means for them, ending on the pain. 2 or 3 short sentences. "Congrats on the Series D. More funding usually means more developers on Hooli, and more developers means more questions in the docs and the Slack community that your team has to answer."
- Template: "That is what {Company} does. {two plain sentences on how it works}." then `{{t1Proof}}`: one customer closest to this account, who they are, the result. "Northwind, a location API for developers, cut their monthly support tickets by 30% with Acme."
- Template: "We're in {city}. Would you be open for a quick 15-minute conversation to see how it fits for your team?"
- Note (on the page and in emails.md, not sent): "[I would attach a 30 second video of {something concrete}, visual hooks are more aesthetic and give a strong impression of what {Company} really is about]"

**Touch 2, Day 3: new angle for higher response**
- Subject `{{t2Subject}}`: "What Would Your {Engineers|Support Team|Developers|Board} Say?"
- `{{t2Hook}}`: one line that turns the trigger around. "Every question your engineers answer in Slack is time they are not building Hooli."
- `{{t2Proof}}`: the proof story of one customer with a number (a different customer from touch 1 when there is one).
- Template: "The reason is that {how the product keeps the team in control}."
- Template: "We're in {city}. Would 15 minutes be useful to see how that would look for {{companyName}}?"
- Note: "[I would attach a short case study about {customer}]"

**Touch 3, Day 5: close the loop**
- Subject (template): "Before Year-End {Planning|Reporting}".
- Template: "I'll keep this short, and it's my last note for now."
- Template: "{Year-end moment for this buyer}. If {the outcome} is worth solving before then, I'd be glad to set up 15 minutes with {CEO first name}, our CEO. If not, no problem at all. I'll check back in the new year."
- Template: "And if someone on the {support or docs} side is closer to this than you, a name would really help."

All three are signed "Kevin" (sequence.json `signature`).

## sequence.json

```jsonc
{
  "company": "Acme",
  "signature": "Kevin",
  "touches": [
    {"label": "TOUCH 1: DAY 1", "day": 1, "angle": "Open with the trigger", "subject": "{{t1Subject}}",
     "paragraphs": ["Hi {{firstName}},", "{{t1Opener}}",
                    "That is what Acme does. It reads your docs, GitHub and community and answers developer questions right where they ask them, with a link to the source. {{t1Proof}}",
                    "We're in New York. Would you be open for a quick 15-minute conversation to see how it fits for your team?",
                    "[I would attach a 30 second video of Acme answering a real question from your docs, visual hooks are more aesthetic and give a strong impression of what Acme really is about]"]},
    {"label": "TOUCH 2: DAY 3", "day": 3, "angle": "New angle for higher response", "subject": "{{t2Subject}}",
     "paragraphs": ["Hi {{firstName}},", "{{t2Hook}}", "{{t2Proof}}",
                    "The reason is that Acme only answers from your own content and links every answer to the source, so your team stays in control of what developers see.",
                    "We're in New York. Would 15 minutes be useful to see how that would look for {{companyName}}?",
                    "[I would attach a short case study about the customer above]"]},
    {"label": "TOUCH 3: DAY 5", "day": 5, "angle": "Close the loop", "subject": "Before Year-End Planning",
     "paragraphs": ["Hi {{firstName}},", "I'll keep this short, and it's my last note for now.", "...", "..."]}
  ],
  "leads": {
    "initech.io": {"t1Subject": "...", "t1Opener": "...", "t1Proof": "...", "t2Subject": "...", "t2Hook": "...", "t2Proof": "..."}
  }
}
```

Rules the scripts enforce: exactly 3 touches; every `{{variable}}` has a value for every lead (lemlist would send it empty); `lint` passes. Paragraphs in `[brackets]` stay out of the lemlist steps. The standard variables `firstName`, `lastName`, `companyName`, `jobTitle` come from the champion and the account. Each lead also carries `fitScore`, `triggerLine`, `whyTheyFit` and `agentWeek` as custom variables, so the campaign's lead list shows why each one is there.

lemlist: step 1 is created with the campaign (`create_campaign_with_sequence`), steps 2 and 3 with `add_sequence_step` (delays 2 and 2 days, so Day 1, Day 3, Day 5: email 1 goes out as soon as the campaign is launched, email 2 two days later, email 3 two days after that). Each step keeps its own subject.
