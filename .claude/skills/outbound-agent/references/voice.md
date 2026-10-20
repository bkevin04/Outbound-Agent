# Kevin's voice (for the ICP, why / trigger lines and the emails)

When the `kevin-voice` skill is available, load it: it is the full reference and wins over this page. This is the short version the agent needs when it is not.

## How he writes

His own sentences are the reference:
- "On the home page, a demo video is missing so as a user it is hard to imagine how the product works."
- "Your testimonials are specific and come from founders. For example, it's clear Globex saves £10,000 a year."
- "I think what would make sense here is a tutorial."
- "Congrats on the Series D. More funding usually means more developers on Hooli, and more developers means more questions in the docs and the Slack community that your team has to answer."

What his sentences do:
- Plain, direct, short. Mostly under 25 words.
- Join thoughts with "and", "so", "but" and commas. Not "which", "while", "since", "even though", "whereas", and no semicolons.
- One example, one customer and one number: "For example, ...". Never three names and three numbers in a row.
- Say the point once and stop. No tail that explains why it matters.
- Numbers plain: "$5B", never "~$5B". No em dashes inside a sentence: commas and colons.
- "Why they fit" names a customer: "..., just like Northwind (Acme's customer)."

## Never write

leverage, seamless, robust, unlock, delve, landscape, journey, north star, move the needle, stands out, speaks to, surprised me, notably, compelling, crucial, genuinely, the word "pattern", passionate about, proven track record, fast-paced, circle back, touch base, game-changer, cutting-edge, "I hope this email finds you well", insider words (signals, motion, wedge, double down, re-ups). No clever idioms ("numbers that sell themselves", "the message lands"), no "not X, it is Y" turns, no "exactly" more than once, no claims about how buyers think in general.

`agent/voice.py` checks the mechanical part; the read-aloud pass is still the agent's job.

## Before and after

- AI: "Your case studies have numbers that sell themselves: Globex cut costs by 50%, Initech pays a third of what its old vendor cost, and Umbrella cut debugging time by 80%."
  Kevin: "Your case studies show real results, for example Globex cut their costs by 50% after switching."
- AI: "A Series C fintech that names NetSuite in the post, the exact Initech pattern."
  Kevin: "A Series C fintech that names NetSuite in the post, just like Initech (Acme's customer)."
