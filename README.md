# Operation Hang Up — starter kit (v0.2)

A small, strictly defensive kit for logging unwanted robocalls and sales calls. This is a "maybe someday" starter, not a war on every spam ring. It does not block calls, place calls, file complaints, or sue anyone. It keeps a local record so a person can register, block, report, or (if a real company is actually findable) decide whether a small-claims case is worth the time.

Defense only. Never hack back. Never call them back. Never harass a number, a carrier, or a person.

**Not legal advice.** The Do Not Call notes below are a map of published federal rules and official pages, not counsel, and not a determination that any call was illegal or that a lawsuit would succeed.

## The plan

Do these in order. The log in this folder supports steps 3 and 4; it does not replace them.

1. **Register the number** on the National Do Not Call Registry at [donotcall.gov](https://www.donotcall.gov) (registration page: [donotcall.gov/register.html](https://www.donotcall.gov/register.html)), or call 1-888-382-1222 from the phone you want listed. It is free. Registrations do not expire. The registry tells *legitimate* telemarketers which numbers not to call. It does not block calls, and it does not stop scammers. Political calls, charities, surveys, debt collectors, and firms with an existing business relationship or written permission may still get through. Record the registration date with this kit (`registry --date YYYY-MM-DD`) so later case files can apply the 31-day and 12-month checks below.

2. **Turn on the carrier's free spam blocking** (and the phone's own filter). Most carriers offer a free spam-label or block tool. Use that. Do not pay a random "call blocker" site that wants a subscription or your contacts. If a robocall asks you to press a button to opt out or "speak to a representative," don't. That can confirm the number is live.

3. **Log the calls** with this kit. Date, time, the number on caller ID, the name it showed, the company if known, what they pitched, whether it was a live person or a recording, and any screenshot or voicemail. Repeat *companies* matter as much as repeat numbers, because spoofers rotate numbers. You do not have to type the formal `add` command every time: tell your assistant about the call in chat ("spam call, 555-010-whatever, car warranty"), or send a screenshot of recent calls, and the call gets logged here. The `quick` command is what that uses.

4. **Report them.**
   - Unwanted telemarketing or Do Not Call problems, and no money lost: [donotcall.gov/file-a-complaint](https://www.donotcall.gov/file-a-complaint).
   - A scam, a bad business practice, money lost, or personal information shared: [reportfraud.ftc.gov](https://reportfraud.ftc.gov/).
   The FTC uses reports to spot patterns. It does not resolve your individual case or get your money back.

5. **A lawsuit is a maybe, and only the person who received the calls can file it.** Under the Telephone Consumer Protection Act (47 U.S.C. § 227), a person who gets an illegal call or text may sue for $500 per violation, or actual loss if that is larger. A court may raise that to $1,500 if the violation was willful or knowing. That suit is theirs to file, usually in small claims court, against a *real, findable* company. The FTC will not file it for them. Offshore, spoofed, or fly-by-night callers are often unfindable, which makes a suit a dead end even when the calls were illegal. This kit will never file a case, send a demand, or contact a caller.

## Do Not Call dates (verified 2026-11-18)

These are the two timing rules this kit applies, checked against official sources. Case files show the registration date, which calls fall 31 or more days after registration, and whether the 12-month, 2-or-more-calls test is met for each company. Exceptions (existing business relationship, written permission, charities, political calls, and others) are not evaluated here.

- **31-day honor period.** Telemarketers must honor a National Do Not Call registration starting no later than 31 days after the number is placed on the Registry. This kit treats a call as past the honor mark when it falls on or after registration date + 31 days. Sources: FCC enforcement order, [DOC-307630A1](https://www.fcc.gov/eb/Orders/2009/DOC-307630A1.html) ("must honor do-not-call registrations starting no later than 31 days after a number is placed on the National Do-Not-Call Registry"); the seller safe harbor in [47 C.F.R. § 64.1200(c)(2)(i)(D)](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-B/part-64/subpart-L/section-64.1200) (use a registry version obtained no more than 31 days before the call); the FTC consumer article ([archived copy](https://web.archive.org/web/20210510152337/https://www.consumer.ftc.gov/articles/0108-national-do-not-call-registry)) ("it can take up to 31 days for sales calls to stop"); and the [FTC Do Not Call Databook 2025](https://search.ftc.gov/system/files/ftc_gov/pdf/DNC-Data-Book-2025.pdf) ("must remove numbers added to the Registry from their call lists at least every 31 days").

- **More than one call in any 12-month period, by or on behalf of the same entity.** [47 U.S.C. § 227(c)(5)](https://www.law.cornell.edu/uscode/text/47/227) gives a private right of action to a person who has received more than one telephone call within any 12-month period by or on behalf of the same entity in violation of the regulations prescribed under that subsection. This kit treats that test as met when two or more calls that are already 31+ days after registration, and attributed to the same company, fall within any 365-day window. One call is not enough.

## What this tool will not do

- It will not dial, text, or "verify" a number.
- It will not scrape, look up, or hack a caller.
- It will not auto-file an FTC or FCC complaint, or send mail in anyone's name.
- It will not give legal advice. The notes above are a map of public government pages and the statute, not counsel.

## How to use the log

All commands run from this folder. Phone data lives in `data/` (the call log, case files, and evidence copies) and stays local in plaintext. It is not for a public copy or a shared backup in readable form. An optional encrypted archive can be written to `backup/calllog.enc`.

You can also just tell your assistant about a call in chat, or send a screenshot of recent calls, and it will be logged for you. No need to type a command.

```
python3 calllog.py add --number 555-010-0100 --when "2026-11-18 14:30" \
    --name "ACME WARRANTY" --pitch "extended car warranty" --kind recording \
    --company "ACME WARRANTY" --evidence voicemail.png

python3 calllog.py quick "spam call 555-010-0100 car warranty recording"
python3 calllog.py quick "2026-11-18 2:30pm 555-010-0100 ACME WARRANTY live"

python3 calllog.py list
python3 calllog.py list --number 5550100100
python3 calllog.py list --company "ACME WARRANTY"
python3 calllog.py repeats
python3 calllog.py case 555-010-0100
python3 calllog.py case --company "ACME WARRANTY"
python3 calllog.py case --all

python3 calllog.py registry --date 2026-01-15
python3 calllog.py registry

python3 calllog.py backup
python3 calllog.py restore
```

`--kind` is `live` or `recording`. `--when` is local America/Chicago time (`YYYY-MM-DD HH:MM` or a full ISO stamp). `quick` takes one free-text line, stores the best guess (number, time defaulting to now in America/Chicago, name, pitch, kind, and company if it can find one), and keeps the raw text. It does not ask follow-up questions. `--company` is optional on `add` and `quick`. `--evidence` is repeatable and copies a screenshot or voicemail into `data/evidence/` under a SHA-256 filename; case files list those hashes. A case file is only written for a number or company that has been logged **3 or more times**. That threshold is a practical bar for "this keeps happening," not a legal rule. `repeats` and `case` group by company as well as by number, because spoofers rotate numbers. `backup` runs automatically after `add`, `quick`, and `registry`.

Tests: `python3 -m unittest discover -s tests -v`

## Privacy and the encrypted backup

`data/` holds phone records, case files, and evidence copies. Keep that folder off anything public, including a shared repo, and off cloud storage in plaintext.

The archive is encrypted with Fernet (AES-128-CBC plus HMAC-SHA256, from the Python `cryptography` package). The key file lives **outside this folder and outside all backups**. By default it is `~/.config/hang-up/key` (directory mode 700, file mode 600). You can point it somewhere else with the `HANGUP_KEY_PATH` or `HANGUP_KEY_DIR` environment variables. The key is never printed and never placed in this repo.

**If the key file is lost, the backup is useless.** The key will not survive a computer reset unless an off-box copy is kept somewhere only you control. Restore the key to `~/.config/hang-up/key` (or the path you configured) with mode 600, then run `python3 calllog.py restore`. Never paste the key in chat.

## License

MIT. See `LICENSE`. Copyright 2026 Steward Commons contributors.

Operation Hang Up v0.2, November 2026.
