# PSX Data Downloader

**Download years of historical Pakistan Stock Exchange data to your own computer — with a point-and-click app. No coding required.**

The [PSX Data Portal](https://dps.psx.com.pk/downloads) publishes a new set of files
every trading day: closing rates, off-market (NDM) deals, futures open interest, VAR
margins, daily quotations, and more. But the website only lets you download **one day
at a time**. Building a few years of history by hand means thousands of clicks.

This tool does it for you. Tick the datasets you want, pick a date range, press
**Start**, and it saves every file into a tidy folder on your PC.

```
data/raw/nd_accepted/2025/2025-03-14.pdf
data/raw/nd_accepted/2025/2025-03-17.pdf
data/raw/omts/2025/2025-03-14.csv
data/raw/omts/2025/2025-03-17.csv
...
```

Files are saved exactly as PSX publishes them — nothing is altered, reformatted, or
interpreted. What you get is a clean, dated archive you can open in Excel, feed into a
script, or keep as a personal research library.

---

## Table of contents

- [What you can download](#what-you-can-download)
- [Setup (one time, about 10 minutes)](#setup-one-time-about-10-minutes)
- [Using the app](#using-the-app)
- [Where your files go](#where-your-files-go)
- [Good things to know](#good-things-to-know)
- [Questions and problems](#questions-and-problems)
- [For command-line users](#for-command-line-users)
- [How it works under the hood](#how-it-works-under-the-hood)
- [Please read: legal and fair use](#please-read-legal-and-fair-use)

---

## What you can download

24 daily datasets, grouped the same way the PSX website groups them.

| Dataset | What it is | Format |
|---|---|---|
| **Market Summary** | | |
| Market Summary (Closing) | The full daily closing summary | `.Z` |
| Closing Rate Summary | Official closing rates | PDF |
| Symbol Price (Upper/Lower) | Upper and lower price caps per symbol | ZIP |
| Symbols Short Long Name | Symbol code → company name list | ZIP |
| GIS Revaluation Rates | Government Ijara Sukuk revaluation rates | CSV + PDF |
| Bai Muajjal Volume | Bai Muajjal (DVF) trade volumes | CSV |
| **Off Market / Negotiated Deals (NDM)** | | |
| ND Accepted | Negotiated deals that were accepted | PDF |
| ND Rejected | Negotiated deals that were rejected | PDF |
| ND Threshold | Negotiated-deal thresholds | PDF |
| Off Market Transaction Summary | Off-market transaction summary | CSV |
| Off Market Transaction (PDF) | The same, as a PDF report | PDF |
| **Futures** | | |
| Symbol Wise Open Interest (DFC) | Deliverable futures open interest | PDF + XLS |
| Symbol Wise Open Interest (CSF) | Cash-settled futures open interest | PDF + XLS |
| Position Limits | Position limits for futures | XLS |
| **SIF Market** | | |
| Open Interest Report | SIF open interest | CSV |
| Fair Value Report | SIF fair values | PDF |
| **DFC Market** | | |
| Net Blank Sale Position in DFC | Net blank sale positions | CSV |
| **Other daily reports** | | |
| VAR Margins | Value-at-risk margins | ZIP |
| Ready Market Short Sell Vol | Short-sell volumes, ready market | PDF |
| Daily Announcements | Company announcements for the day | PDF |
| Daily Quotations | The full daily quotations report | PDF |
| Post Close Report | Post-close session report | `.Z` |
| Internet Trading Subscribers List | Broker internet-trading subscribers | PDF |
| Constituent Data (PSX Indices) | Index constituents | XLS |

**How far back can you go?** It depends on the dataset. The PSX website's own date
picker offers about 10 years, but each dataset started on a different day — Market
Summary goes back to at least 2015, while ND Accepted only begins around 2019. The tool
doesn't guess: it simply asks for each date, and any date PSX has no file for is
recorded as *missing* and never asked about again. So you can safely set a start date of
2015 and let it find out for you.

> **A note on `.Z` files:** a couple of datasets come as `.Z`, an old Unix compression
> format that Windows can't open by default. They're saved as-is. [7-Zip](https://www.7-zip.org/)
> (free) opens them.

---

## Setup (one time, about 10 minutes)

### Step 1 — Install Python

Download Python **3.11 or newer** from [python.org/downloads](https://www.python.org/downloads/).

> ⚠️ **On the first install screen, tick "Add python.exe to PATH"** before clicking
> Install. If you miss it, the commands below won't be found and you'll have to
> reinstall. It's the single most common setup mistake.

### Step 2 — Get this project onto your PC

**The easy way:** click the green **Code** button at the top of this GitHub page →
**Download ZIP** → right-click the downloaded file → **Extract All**. Put the folder
somewhere you'll remember, e.g. `C:\PSX Data Downloader`.

**If you have Git:**

```powershell
git clone https://github.com/hhk2763/psx-data-downloader.git
cd psx-data-downloader
```

### Step 3 — Install the parts it needs

Open the project folder in File Explorer, click in the address bar, type `powershell`
and press Enter. A blue window opens, already pointing at the right folder. Paste this
and press Enter:

```powershell
pip install -r requirements.txt
```

It downloads a handful of standard Python packages. Give it a minute or two.

### Step 4 — Start the app

```powershell
python -m streamlit run app.py
```

Your browser opens at `http://localhost:8501` with the app running. **That's it.**

Everything runs on your own machine — nothing is uploaded anywhere, and there are no
accounts, keys, or sign-ups.

> **Next time**, you only need Step 3's PowerShell trick and Step 4's one line. To make
> it a double-click, save those two lines in a file called `start.bat` in the project
> folder:
>
> ```bat
> python -m streamlit run app.py
> pause
> ```

---

## Using the app

The app has four tabs.

### 1. Download

This is where you'll spend your time.

1. **Pick your datasets.** They're in collapsible groups matching the PSX website
   headings. Tick individual datasets, or use **Select all of &lt;section&gt;** to take a
   whole group at once.
2. **Pick your dates.** Either use the **Quick range** dropdown (Last 5 days, Last 30
   days, This month, Year-to-date, or Max = 10 years) or set **From** and **To**
   yourself.
3. **Check the estimate.** Before anything is downloaded, three boxes tell you:
   - **Files planned** — how many files that selection covers
   - **To fetch** — how many you don't already have (this is the number that matters)
   - **Estimated time** — roughly how long it will take
4. **Press Start.** A progress bar fills up, with live running totals and a log of the
   most recent files.
5. **Press Stop** whenever you like. Nothing is lost — press Start again later and it
   picks up exactly where it left off.

Two extra controls on the right:

- **Extensions** — only relevant for the three datasets published in two formats (e.g.
  DFC open interest as both PDF and XLS). Leave it blank to get both.
- **Re-download files already stored** — normally off. The tool skips files you already
  have, which is what makes it safe to re-run. Tick this only if you suspect a file on
  disk is corrupt.

### 2. Coverage

A colour-coded grid of dataset × month, so you can see your archive at a glance:

- 🟩 **green** — files downloaded and verified
- 🟫 **brown** — nothing published for those days (weekends, public holidays, or before
  that dataset existed). This is normal and expected.
- 🟥 **red** — something went wrong. Just re-run the same download; errors are retried
  automatically.

### 3. Other Downloads

PSX also publishes a handful of "latest only" files with no date in their filename —
the daily stock market report, index constituent lists, top gainers/losers, and so on.
Because there's no date in the URL, **these cannot be backfilled**: yesterday's version
is gone forever once replaced. The only way to build up a history is to take a snapshot
each day from now on. (This tab is where that will live; it's the next thing being
built.)

### 4. Settings

A read-only view of where your data is stored, how fast the tool is allowed to request
files, and the full dataset registry. To change anything, edit `config/settings.yaml`
and restart the app.

---

## Where your files go

Inside the project folder:

```
data/
  raw/
    nd_accepted/
      2024/
        2024-01-02.pdf
        2024-01-03.pdf
      2025/
        ...
    omts/
      2025/
        2025-03-14.csv
  manifest.db          a record of every file it has ever tried to get
  logs/
    2026-09-20.log     what happened today
```

One folder per dataset, then one per year, and files named by trade date. PSX's own
filenames are wildly inconsistent (`nd_acc_202617sep.pdf`), so the tool renames
everything to a clean `YYYY-MM-DD` so files sort correctly and scripts can find them.

**Want the archive on another drive?** Open `config/settings.yaml` and change
`data_dir`, e.g. `data_dir: D:\PSX Archive`.

**Backing up:** the `data` folder is the only thing that matters. The code can always
be downloaded again.

---

## Good things to know

**It's safe to stop and restart.** Close the window, lose power, or reboot mid-download
— the worst case is one incomplete file, and that file gets retried next time.

**It never downloads the same file twice.** Every attempt is recorded, so re-running a
download that you've already done takes seconds and makes almost no requests. Run it
daily and it only fetches what's new.

**It's deliberately slow, and that's on purpose.** The tool makes **one** request at a
time with a short pause between each, using a normal browser identity. It does not
hammer PSX's servers, and it never runs downloads in parallel. Please don't change that
— it's what keeps the tool (and you) welcome on their site. A large multi-year backfill
of every dataset is tens of thousands of files, so expect it to run for hours. Start it
in the evening, or do it in chunks, one section or one year at a time.

**Missing days are normal.** Weekends are skipped without even asking. Public holidays
and dates before a dataset existed come back as *missing*, get recorded, and are never
asked about again. A grid full of brown squares on Saturdays is the tool working
correctly.

**It won't save junk.** When a file doesn't exist, PSX answers with an HTML error page
rather than an honest error. The tool checks what it actually received and refuses
anything that isn't a real file — so you never end up with a 47 KB web page sitting in
your archive pretending to be a PDF.

**Today's files appear after the market closes**, usually in the evening PKT. If you ask
for today's data in the morning, it will be reported as missing. The tool's default end
date accounts for this: before 19:00 PKT it uses the previous trading day. Dates within
the last 3 days are automatically re-checked on later runs, so late-published files get
picked up.

---

## Questions and problems

**"python is not recognized" / "pip is not recognized"**
Python isn't on your PATH. Reinstall Python and tick **Add python.exe to PATH** on the
first screen (see [Step 1](#step-1--install-python)).

**"streamlit is not recognized"**
Use the full command — `python -m streamlit run app.py`, not just `streamlit run app.py`.

**The browser didn't open**
Go to [http://localhost:8501](http://localhost:8501) yourself.

**Everything comes back as "missing"**
Usually one of three things: the dates are weekends or holidays; the dataset didn't
exist that far back (try a recent date range to confirm the tool works, then walk
backwards); or your internet/firewall is blocking `dps.psx.com.pk`. Open
[dps.psx.com.pk/downloads](https://dps.psx.com.pk/downloads) in your browser to check.

**Lots of red / errors**
Check whether the PSX site itself is up. If ten errors happen in a row the job stops
itself deliberately rather than keep pounding a site that's having a bad day. Wait and
re-run — errors are automatically retried.

**I can't open a `.Z` file**
Install [7-Zip](https://www.7-zip.org/). Those are Unix `compress` archives.

**Can I get data older than what the tool finds?**
Not through this tool — it can only fetch what PSX still publishes. For older history
you'd need a data licence from PSX (marketdatarequest@psx.com.pk).

**How big will the archive get?**
Very roughly, a full multi-year backfill of all 24 datasets lands in the tens of
gigabytes, dominated by the PDF reports. Priority datasets only, over a couple of
years, is far smaller. Watch the `data` folder as you go.

**Can it run automatically every day?**
Yes — see [For command-line users](#for-command-line-users) below.

---

## For command-line users

The app is a friendly front-end. Everything it does is also a command, and both share
exactly the same download engine.

```powershell
# specific datasets over a date range
python cli.py fetch --datasets omts,nd_accepted --from 2024-01-01 --to 2026-09-17

# a whole section
python cli.py fetch --section "Off Market / NDM" --from 2025-01-01

# the last 5 trading days, every dataset
python cli.py fetch --all --last 5

# see the plan and time estimate without making a single request
python cli.py fetch --all --from 2016-01-01 --dry-run

# rebuild the record from whatever is actually on disk
python cli.py reconcile
```

Useful flags: `--ext pdf,xls` (multi-format datasets only), `--force` (re-download files
already stored), `--data-dir` (use a different archive), `--verbose` (log every file,
not just problems).

`--to` defaults to the last completed trading day.

**Scheduling a daily update (Windows):** open Task Scheduler → Create Basic Task → run
daily at about 19:15 PKT → Action: *Start a program* → Program `python`, Arguments
`cli.py fetch --all --last 3`, Start in: your project folder. That keeps the archive
current and catches anything PSX published late.

### Project layout

```
config/registry.yaml   the 24 datasets — adding one is a YAML edit, not a code change
config/settings.yaml   data folder, request delay, timeout, retries
psx/                   the engine: registry, calendar, store, manifest, fetcher, jobs
cli.py                 the command-line interface
app.py                 the Streamlit app
tests/                 203 tests, all offline
data/                  your archive (never committed to Git)
```

### Tests

```powershell
python -m pytest -q
```

All 203 tests run offline — HTTP is mocked, so the suite never touches the live PSX
site. Please keep it that way.

---

## How it works under the hood

Every daily file on the PSX portal has a predictable address:

```
https://dps.psx.com.pk/download/{dataset}/{YYYY-MM-DD}.{ext}
```

So no browser automation or screen-scraping is needed — the tool asks for the exact
file it wants and checks what came back. A file is accepted **only** if the server
answers HTTP 200 *and* says the content is a binary file; a 404 is recorded as
*missing*, and anything else is an *error* to retry later.

Each download is written to a temporary `.part` file and renamed only once it's complete,
so a half-written file never appears in your archive. Every attempt — success, missing,
or error — is written to a small SQLite database (`data/manifest.db`) with the file size
and a SHA-256 checksum. That record is what makes the tool resumable and what feeds the
Coverage grid. If the database and your actual files ever disagree, **the files win**:
`python cli.py reconcile` rebuilds the record by walking the folders.

The download logic lives in one place and both the app and the command line call into
it, so they can never drift apart in behaviour.

**Deliberately not included:** any parsing. This tool's one job is to build a faithful
archive of the original files. Turning those PDFs and CSVs into tables is a separate
concern, and mixing the two would mean re-downloading everything every time a parsing
rule changed.

---

## Please read: legal and fair use

This tool is for **personal, non-commercial research**.

PSX's Terms of Use restrict automated retrieval and **prohibit redistributing or
commercially using market data without a licence** from PSX
(marketdatarequest@psx.com.pk). The data belongs to PSX, not to you and not to this
project.

What that means in practice:

- ✅ Download PSX data for your own research, analysis, and trading decisions.
- ❌ Don't republish, resell, or share the archive you build.
- ❌ Don't host this tool on a public server or wrap it in a public API.
- ❌ Don't raise the request rate or add concurrency to make it faster.
- ➡️ If your use turns commercial, get a PSX data licence first.

**Licence:** the *code* is [MIT licensed](LICENSE) — use it, change it, share it
freely. The *data* it downloads is not covered by that licence and never becomes yours
to redistribute.

This project is not affiliated with, endorsed by, or connected to the Pakistan Stock
Exchange. It ships with no warranty — verify anything you rely on against PSX's own
published reports before trading on it.
