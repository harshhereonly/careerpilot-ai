# Chrome extension (WXT + React + TypeScript, Manifest V3)

**Status: not started.** The extension is built in its own phase; this folder only reserves its place.

## Non-negotiable rules

The extension is a companion for contacts the user enters themselves. It must never:

- scrape or read LinkedIn profile pages, or extract profile data automatically;
- inject buttons, badges or content scripts into LinkedIn (or request LinkedIn host permissions);
- automate LinkedIn connections or messages, visit profiles in the background, or bulk-collect profiles.

It supports manual entry, contact preview and lookup, duplicate and already-contacted checks,
adding contacts to the outreach queue, and opening the contact on the website. Contact capture goes
through a replaceable provider interface with a manual provider, a CSV provider and an interface for
a future *authorized* API provider. There is deliberately no DOM-extraction provider, not even
behind a flag. A test over the built manifest will enforce the "no LinkedIn permissions" rule.
