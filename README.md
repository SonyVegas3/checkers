# Checkers AI Engine (8x8: jokergame, Brazilian, Russian and English draughts rules)

A Python checkers engine built as the brain of an automated bot for the web game
on jokergame.cc. It is a negamax searcher with alpha-beta pruning, a
transposition table, iterative deepening, jump quiescence and a hand tuned
evaluation function, plus a Playwright bridge that plays the game in a real
browser and an iOS app that plays it inside a WKWebView.

```
.
├── checkersbot/             # the shared core (no browser library in here)
│   ├── board.py             # rules: 8x8 board, pieces, move generation (english/russian/
│   │                        #   brazilian/joker), forced jumps, kinging, flying kings
│   ├── ai.py                # Minimax + alpha-beta search, evaluation, dynamic depth
│   ├── site.py              # jokergame.cc protocol: SELECTORS, the two JS scripts,
│   │                        #   SiteState, site_legal_moves + the Site interface
│   └── bot.py               # CheckersBot: probe -> pick -> click -> verify
├── src/checkersbot_ios/     # the iPhone/iPad app (BeeWare Toga + WebView)
│   ├── app.py               # window: start/stop, depth, seconds, WebView, log
│   ├── site.py              # WebViewSite - the same Site, driven by toga.WebView JS
│   └── __main__.py          # Briefcase entry point
├── pyproject.toml           # Briefcase project (iOS build only, see "iOS app")
├── game.py                  # terminal CLI: play against the engine / engine self-play
├── web_bot.py               # Playwright bridge: reads jokergame.cc and clicks the moves
├── gui.py                   # tkinter control panel (the entry point of CheckersBot.exe)
├── build_exe.py             # PyInstaller build + smoke test for dist/CheckersBot.exe
├── requirements.txt         # playwright (the engine itself is stdlib only)
├── requirements-build.txt   # pyinstaller, only for building the exe
├── requirements-ios.txt     # toga + briefcase, only for building the iOS app
├── tests/
│   ├── test_engine.py       # unittest suite (rules + perft + search)
│   ├── test_web_bot.py      # unittest suite for the Playwright bridge
│   ├── test_ios_site.py     # unittest suite for the iOS WebView bridge
│   ├── test_gui.py          # unittest suite for the panel (settings -> command line)
│   └── mock_checkers.html   # local copy of the site's DOM/click protocol
├── scripts/
│   ├── build_ipa.sh         # macOS: device build + Payload/ -> dist/*.ipa
│   ├── push_to_github.ps1   # Windows: one command to push and get the CI build
│   └── push_to_github.cmd   # the same, for cmd.exe / double-click
├── .github/workflows/ios.yml  # builds the unsigned .ipa on a macOS runner
├── board.py / ai.py         # thin aliases of checkersbot.board / checkersbot.ai
└── .gitignore
```

## Quick start

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium        # browser for web_bot.py
python game.py
```

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python game.py
```

The engine and its tests only need the Python 3.10+ standard library; Playwright
is needed by `web_bot.py` only.

**Windows users who just want the bot:** run `dist\CheckersBot.exe` (built with
`python build_exe.py`) — the panel below lets you pick where the game starts
(sign-in → lobby / lobby / already open table), the stake, the engine settings and
shows the live log. No Python installation is needed for that.

## Playing in the terminal

```powershell
python game.py                  # you are RED (bottom rows), engine is BLACK
python game.py --ai-red         # engine plays RED and moves first
python game.py --depth 9 --time 5                          # joker rules = what the site plays (default)
python game.py --depth 9 --time 5 --rules english          # terminal only: American checkers
python game.py --selfplay 2 --depth 5 --time 1 --verbose   # automated smoke test
```

> `--rules` exists **only in `game.py`** (a developer sandbox used by the tests).
> The web bot, `CheckersBot.exe` and the iOS app always play the site's `joker`
> rules — there is nothing to pick there.

Inside the game you can type a move (`c3-d4` for a step, `c3xd5` for a jump,
multi-jumps may be written as `c3xe5xg7`) or one of the commands `moves`,
`board`, `help`, `quit`.

After every engine move the CLI prints the search statistics:

```
engine plays b6-c5   [move b6-c5 | depth 8 | score -2 | nodes 23574 | 742 ms]
```

## Windows GUI / `.exe`

`gui.py` is a small tkinter panel around the same `web_bot.run_bot()` code, and
`CheckersBot.exe` is that panel packaged with PyInstaller — no Python needed on
the target machine.

```
dist/CheckersBot.exe            # double-click, or run from a terminal
```

What the panel offers:

| group | what it does |
| --- | --- |
| **1. საიდან დაიწყოს თამაში** | `ავტორიზაცია → ლობი` (signs in with the e-mail/password, then walks the lobby), `ლობი` (the browser profile is already signed in) or `უკვე ღია დაფა` (no lobby at all — the bot only plays the table that is already open, `--no-lobby`) |
| **2. ავტორიზაცია** | e-mail/phone + password; the password stays in memory and is **never** written to disk |
| **3. მაგიდა და თანხა** | stake ladder exactly as the site renders it (`0` = 50, `1` = 5 000, `2` = 50 000, `3` = 200 000, `4` = 1 000 000, `5` = 10 000 000 coins), browser profile folder, browser (Playwright Chromium / installed Chrome / Edge) and log folder |
| **4. ძრავა** | depth, seconds per move, poll interval, max moves — the board rules are fixed to the site's `joker` rule set — plus headless / close-browser / board-trace / dry-run / probe toggles |
| **buttons** | `▶ დაწყება`, `■ შეჩერება` (cooperative, stops after the current move), `✔ წესების შემოწმება` (`--diagnose`), open log/profile folders, save settings |
| **5. ლოგი** | live output of the bot (same lines as the CLI), mirrored to a file with `--log-file` |

The settings (everything except the password) are stored in
`%APPDATA%\CheckersBot\settings.json`, so the panel reopens exactly as you left it.

### The browser stays open when the game ends

The browser is started as its **own process** and the bot attaches to it over the
DevTools protocol, so finishing a game (or pressing `■ შეჩერება`) only stops the
bot — the window keeps showing the finished game with its **Rematch** button, or
the lobby if you navigate back. Nothing is closed, nothing is reloaded.

* the debugging port is derived from the profile path, so the next run
  **reconnects to the same window** (`browser already running - reusing it`);
* with `--no-lobby` / "უკვე ღია დაფა" you can therefore press Rematch on the site
  and start the bot again on the new game immediately;
* the last line of the log says it explicitly:
  `the browser stays open - 'Rematch' or the lobby are one click away`;
* if you do want the old behaviour, tick "ბრაუზერის დახურვა დასრულების შემდეგ" in
  the panel or pass `--close-browser` — then the browser is launched by Playwright
  itself and closed with the bot.

### Building the exe yourself

```powershell
python -m pip install -r requirements-build.txt      # pyinstaller
python build_exe.py                                  # dist\CheckersBot.exe (one file)
python build_exe.py --onedir                         # folder + faster start
```

The build bundles the whole `playwright` package (node driver included) and then
runs a smoke test (`CheckersBot.exe --selftest`), which opens and closes the
window; the exe prints `OK: smoke test passed`.

A browser is still needed:

* Playwright's Chromium — `python -m playwright install chromium` (the panel uses
  it automatically when it exists), **or**
* an installed Google Chrome / Microsoft Edge — the panel detects it and passes
  `--channel chrome|msedge`; if Playwright's Chromium is missing at launch the bot
  falls back to Chrome/Edge by itself and says so in the log.

### Command line (the exe accepts the same flags as the panel)

```powershell
CheckersBot.exe                                   # panel with the saved settings
CheckersBot.exe --autostart                       # start immediately with them
CheckersBot.exe --log-file logs\gui.log           # mirror the log to a file
CheckersBot.exe --selftest                        # open the window and close it
```

## iOS app (iPhone / iPad)

The bot is not ported: **the same Python engine and the same bot loop run on the
phone.** The only thing that changes is the browser underneath.

```
Windows:  CheckersBot.exe  ->  Playwright + Chromium  ->  jokergame.cc
iOS:      CheckersBot.app  ->  toga.WebView (WKWebView) ->  jokergame.cc
                    \____________ both use checkersbot/bot.py ____________/
```

`web_bot.JokerGameSite` and `checkersbot_ios.site.WebViewSite` are two
implementations of one small interface (`checkersbot.site.Site`): read the board
with `READ_STATE_JS`, click a stone/square with `el.click()`, poll
`window.init_game.canAction`. Nothing else in the bot knows which browser it is
talking to, so probing, calibration, forced jumps, `site_legal_moves` and the
search behave exactly as on Windows.

Why Playwright cannot be used there: Playwright needs a Node driver plus a full
Chromium, and it only ships Windows/Linux/macOS builds. iOS has no Chromium at
all (every browser is WebKit) and no way to drive another app's page — so the
app brings its own WebView instead.

### Build the IPA

An `.ipa` can only be produced on macOS with Xcode. Two supported paths:

```bash
# 1) a Mac (or a rented cloud Mac / MacinCloud)
python -m venv .venv-ios && source .venv-ios/bin/activate
python -m pip install -r requirements-ios.txt
bash scripts/build_ipa.sh             # -> dist/CheckersBot-unsigned.ipa
```

```text
# 2) no Mac: push the repo and let GitHub build it
scripts\push_to_github.cmd https://github.com/<you>/<repo>.git
#    -> Actions -> "iOS app" -> Run workflow -> artifact CheckersBot-unsigned-ipa
```

`scripts/build_ipa.sh` runs `briefcase create iOS`, a simulator build (which
installs the Python requirements and proves the app compiles) and then the real
step Briefcase cannot do for you: `xcodebuild -sdk iphoneos` with code signing
switched off, the `.app` wrapped in `Payload/` and zipped into an IPA.

The IPA is **unsigned** (no Apple account needed to build it). Install it with
iLoader / Sideloadly / AltStore, which re-sign it with your own certificate —
exactly the same flow as any other sideloaded app on the phone.

Notes from getting this green in CI:

* the runner must be **`macos-26`**: Briefcase 0.4.6 refuses anything older
  ("Xcode 26.0 is required; 16.4 is installed"), and `macos-15` ships Xcode 16;
* Briefcase 0.4.x puts the project in `build/checkersbot_ios/ios/xcode/`, so the
  script searches for `*.xcodeproj` instead of assuming a path;
* the Python support package's *Process Python libraries* phase always runs
  `codesign --sign "$EXPANDED_CODE_SIGN_IDENTITY"` on the modules it turns into
  frameworks. With signing disabled that identity is empty and the build dies
  with `no identity found`, so `build_ipa.sh` passes the ad-hoc identity `-`
  (`EXPANDED_CODE_SIGN_IDENTITY="-"`) while the app itself stays unsigned;
* every run publishes `ci-build.log` and `ci-ipa-report.txt` (the unzipped
  payload listing + `Info.plist`) to the `ci-log` branch, which makes a failed
  build diagnosable without downloading artifacts.

### Use it

1. Open the app, sign in on jokergame.cc inside it (the WebView keeps the
   cookies; the app never stores a password), open a table.
2. Press **▶ დაწყება**. The engine plays depth 9 with 5 s per move (the `DEPTH` /
   `SECONDS` constants at the top of `src/checkersbot_ios/app.py`).
3. The bot waits for the board, probes your stones, plays the engine's move and
   repeats until the game ends; **■ გაჩერება** stops it after the current move.
   The log under the board shows the same lines as the desktop bot.
4. While the bot runs the app keeps the screen awake (`idleTimerDisabled`), so
   the game continues when nobody touches the phone. iOS suspends apps that go
   to the background, so **closing the app pauses the game** — a bot that has to
   keep playing while the phone is in your pocket needs to run on a PC/server.
   If no table is open the app gives up after 25 s and says so instead of
   waiting two minutes.

What the app deliberately does not do: walk the lobby, pick a stake, sign in
automatically, or take screenshots (there is no such API for an app's own
WebView). `enter_game()` / `sign_in()` only log a hint; the HTML dump still
works and is written to the app's `Logs` folder.

### Develop it on Windows

The app package is plain Python, so the parts that matter are testable here:
`tests/test_ios_site.py` runs `WebViewSite` against `tests/mock_checkers.html`
with Playwright's Chromium standing in for WKWebView — same JavaScript, same
clicks, only the transport is faked. That is how the iOS bridge was validated
without a Mac (11 tests, including a forced double jump and a full `bot.run()`).

## Web bot — playing on jokergame.cc

`web_bot.py` drives a real browser with Playwright, reads the board from the DOM,
asks `CheckersAI` for the best move and clicks it exactly like a human would.

The bridge was written against the site's own client code
(`ui/js/games/checkers/game.js` + `stones.js`), which gives a very clean contract:

| element | meaning |
| --- | --- |
| `.game-square[item-absolute-id]` | one square, with `item-x` / `item-y` (1-based, origin top-left) |
| `.game-square.active` | destination squares the server offers for the selected stone |
| `.game-stone[stone-absolute]` | one piece with `stone-id`, `stone-color`, `stone-king`, `stone-clickable` |
| `.game-stone.highlight` | stone the server wants us to move (forced jump) |
| `.game-finish-dialog` | the game is over |

A move is therefore: **click our stone → wait for `.game-square.active` → click
every landing square of the (possibly chained) jump**.

The bot also reads the site's own game object (`window.init_game`) for the board,
the turn (`game_data.player.action`) and the shot clock (`step_timeout`), so it
knows exactly when it is its turn and never races the client's AJAX calls. Fresh
sessions start in the lobby — the bot clicks **"Play a game"** itself and then
waits for the table (if the site opens a bet/opponent dialog it waits and says so
in the log instead of spending coins on its own).

### The site is the referee (forced jumps, orientation, rules)

The bot never trusts its own view of the rules alone — it asks the site first and
only plays what the server offers:

1. **Probe** – it clicks each of our pieces and records the squares the site
   highlights (`.game-square.active`). A piece the server refuses answers with
   `.game-stone.highlight` on the pieces that may move instead, which is exactly
   how mandatory captures are enforced. Clicks are only sent while the client's
   own `canAction` is `"yes"` (it refuses them, with a toast, otherwise).
2. **Calibration** – the same answers decide the orientation: the engine board is
   built twice (our stones as RED = moving up, as BLACK = moving down) and the
   variant whose legal moves match the site's offers wins. A flipped board
   (`item-y` counted from the other side), a wrong `--side` or a wrong
   `--site-color` therefore correct themselves. After the first calibration the
   side is never switched again — that would wreck a good board model.
   Probing is the slow part of a turn (a click plus a board read each), so the
   calibration stops after three answers, the pieces the server highlights
   (forced captures) are asked first, and a probe that stays silent for 1.5 s
   counts as "not offered". The first move of a game therefore no longer clicks
   every piece, which makes starting much quicker.
3. **Restriction** – the engine only gets to choose among moves whose *first
   landing* the site advertised (`site_legal_moves`), so a jump can never be
   replaced by a quiet move and a move into an unoffered square is impossible.
4. **Chained jumps** – each landing is clicked in order; if the site ends the
   chain earlier than the engine expected, the bot finishes the jump with the
   squares the site still offers so the turn is never left half-played. The
   Russian flying king lands at a distance, which is why `Move.landing_path` is
   recorded during generation instead of being derived from the captured squares.

If the site ever disagrees with the chosen move anyway, the bot re-probes every
piece and plays one of the moves from the site's own list
(`our move (site-synced)` in the log). `--no-probe` disables the probing (fewer
clicks, engine trusted blindly) — useful only for debugging. `--diagnose` prints
the site's offers next to our own move generator for every stone of the current
position, which is how the flying-king rules were confirmed.

### 1. Sign in once (the profile keeps the session)

```powershell
# either let the bot sign in for you (credentials are only used in memory)
$env:JOKER_EMAIL="you@example.com"; $env:JOKER_PASSWORD="…"
python web_bot.py --profile .browser-profile

# …or pass them for a single run
python web_bot.py --profile .browser-profile --email you@example.com --password "…"

# …or log in by hand once: the cookies stay in .browser-profile/
python web_bot.py --profile .browser-profile
```

The bot signs in through the site's own "Sign in" dialog
(`input[name="phone"]` + `input[name="password"]`), walks the lobby
(**Play a game → Random people → stake**) and starts playing when an opponent is
found. The stake is chosen with `--coins` (0 = 50 coins … 5 = 10 000 000); the
bot never picks a disabled/too-expensive option.

### 2. …or attach to a browser you already use

```powershell
# start Chrome with the debugging port open, log in, open the table
chrome.exe --remote-debugging-port=9222

# then let the bot use that window (no separate profile/login)
python web_bot.py --cdp http://localhost:9222
```

### 3. Check what the bot sees before letting it click

```powershell
python web_bot.py --discover          # prints the parsed board + writes logs/discover.png
python web_bot.py --dry-run           # reads the board, prints the move, never clicks
python web_bot.py --once              # play exactly one move and exit
```

### 4. Play

```powershell
python web_bot.py --time 3 --depth 7                  # ~3 s per move
python web_bot.py --site-color white --side red       # force our colours
python web_bot.py --headless --max-moves 20 --poll 0.5
```

| flag | default | meaning |
| --- | --- | --- |
| `--url` | `https://jokergame.cc/checkers` | page to open |
| `--profile` | `.browser-profile` | persistent Chromium profile (login) |
| `--cdp` | – | attach to an existing Chrome (`http://localhost:9222`) |
| `--site-color` | `auto` | our colour on the site (`white`/`black`); auto = read the clickable stones |
| `--side` | `auto` | our colour in the engine; auto = read the direction our men move |
| `--depth` / `--time` | `7` / `3.0` | engine search depth / seconds per move |
| `--poll` | `1.0` | seconds between board polls while waiting |
| `--coins` | `0` | lobby stake: `0` = 50, `1` = 5 000, `2` = 50 000, `3` = 200 000, `4` = 1 000 000, `5` = 10 000 000 coins |
| `--email` / `--password` | env `JOKER_EMAIL` / `JOKER_PASSWORD` | sign-in credentials (never written to disk) |
| `--window-size` | auto | force the browser window, e.g. `1200x760` |
| `--no-fit` | off | do not resize/nudge the page (keep the site's own layout) |
| `--max-moves` | `0` (∞) | stop after N moves |
| `--max-errors` | `5` | stop after N consecutive failures (screenshots are saved to `logs/`) |
| `--no-probe` | off | trust the engine without asking the site (debug only) |
| `--trace-board` | off | log the parsed board before every move |
| `--diagnose` | off | compare the site's legal moves with our rules stone by stone and exit |
| `--no-lobby` | off | never walk the lobby: only play a board that is already open |
| `--channel` | – | launch an installed browser instead of Playwright's Chromium (`chrome`, `msedge`) |
| `--discover-only-board` | off | with `--discover`: read the board but do not click stones |
| `--close-browser` | off | close the browser when the bot stops (by default the window **stays open** on the finished game / lobby) |
| `--once`, `--dry-run`, `--discover`, `--headless` | | modes described above |

### Keeping the whole board visible

The site centres a square board and hangs the two player panels slightly outside
the window (the opponent panel ~8 px above the top edge, your own panel ~26 px
below the bottom edge), and a default browser window is often taller than the
usable screen. Two things fix that:

* **the window is resized to the screen** via CDP (`--window-size` overrides it),
  so nothing sits outside the display;
* **`JokerGameSite.fit_view()`** nudges the panels inside and, if the board plus
  panels still does not fit, scales the board down around its centre. Only CSS
  transforms are touched — the game state and the click targets are unchanged.

The bot reports the result once per table, e.g.

```
view: 896x654 window, board 474px, board+panels 634px -> everything fits
```

### Troubleshooting

* **"the checkers board never appeared"** – the bot is not on a table page.
  Use `--discover` to confirm; if your client is in another language/state, take
  the screenshot in `logs/` and adjust the selectors in `SELECTORS` (top of
  `checkersbot/site.py`).
* **A move is never accepted** – the log line
  `! the site does not offer … (it offers: …)` shows both sides of the
  disagreement. The bot then re-probes and plays a site-legal move
  (`our move (site-synced) …`); with `--discover` you can see the same map
  (per-stone destinations) before letting it play.
* **Wrong colours** – normally impossible now, because the orientation is
  calibrated from the site's own answers. If you force it, `--site-color
  white|black` picks the stones and `--side red|black` picks the direction; the
  bot corrects either one when the site disagrees (see the log).
* **Lobby / stake dialog** – a fresh session starts in the lobby; the bot clicks
  "Play a game" → "Random people" → the stake chosen with `--coins` itself. If a
  dialog stays open the bot waits and logs
  `(a site dialog is open - confirm or close it)`; it never picks a disabled or
  more expensive stake on its own.
* **Sign in** – if the site asks for credentials, pass `--email/--password` or set
  `JOKER_EMAIL` / `JOKER_PASSWORD` (the bot types them into the site's own dialog;
  they are never logged or written to disk). Without credentials, log in by hand
  once with `--profile` — the cookies stay in the profile directory.
* **Resume one specific table** – pass its URL, e.g.
  `--url "https://jokergame.cc/checkers/?page=game&game_id=123456"`.
* **Login expired** – delete `.browser-profile/` and log in again, or use `--cdp`
  with your everyday browser.

### How it was validated

* **Live on jokergame.cc** (real account, 50-coin tables): the bot signs in through
  the site's dialog, walks the lobby (Play a game → Random people → stake 0),
  resizes the window so the whole board is visible
  (`view: 896x654 window, board 474px, board+panels 634px -> everything fits`),
  calibrates itself against the site (`site match red=7 black=0`) and plays turn
  after turn while the opponent answers (`step 0 → 2 → 4 → …`, 60 s clock).
  Sessions of 4, 6, 8 and 10 moves were played live, including captures and
  chained jumps (`d2 -> b4 -> d6`, `h2xf4`, `d4xb6`, `b2xd4`).
* **A full live game was won with the corrected rules** (`logs/game12.log`,
  25 moves): we were a man up by move 20 (+250), promoted a man (`g7-h8`),
  hunted the opponent down with the flying king (`h8-d4`, `d4-c3`, `c3-b2`,
  `b2xf2`, `f2xa7`) and reached a forced win (`score +99997`) with 3 pieces
  (king + 2 men) against one. Every probe in that game matched the site square
  for square, e.g.
  `probe d4 (king): site h8, g7, b6, f6, c5, e5, c3, e3, b2, f2, g1 | engine …`.
* **Offline** against `tests/mock_checkers.html` — a local page with the very same
  DOM contract, click protocol, forced captures and chained jumps:
  normal play, a forced double jump (`b2xd4xf6`, both landings clicked) and a
  180°-flipped board that the bot has to calibrate itself. The browser tests are
  skipped automatically when Chromium is missing.
* If the site ever disagrees (`! the site does not offer …`), the bot re-probes
  every piece and plays a move from the site's own list
  (`our move (site-synced) …`) — that path was exercised live as well.


```python
from ai import CheckersAI
from board import Board, BLACK

board = Board()                       # or Board.from_matrix(cells, turn=BLACK)
engine = CheckersAI(color=BLACK, max_depth=8, time_limit=3.0)

move = engine.choose_move(board)      # a board.copy() is searched, board is untouched
if move is not None:
    payload = move.to_dict()          # {"from": "b6", "to": "c5", "captures": [], ...}
    board.apply_move(move)
```

`choose_move` never mutates the board you pass in, so the same call works in a
request handler. `move.to_dict()` is ready to be JSON-serialised for the
front-end, and `Board.from_matrix` / `Board.from_rows` rebuild a position from
whatever the web client sends.

## Rules implemented

Four rule sets share one generator, chosen with `Board(..., rules=...)`.  **Only
`joker` is exposed to the bots**: `web_bot.py`, `CheckersBot.exe` and the iOS app
have no rules option at all — they always play the site's rule set.  The other
three exist for `game.py` (a developer sandbox) and for the tests that prove
which rule set the site follows.

### `joker` — what jokergame.cc plays (the only one the bot uses)

Everything from `brazilian` below, **plus Russian promotion**: when a man lands
on its far row *in the middle of a capture*, it is crowned on the spot and
**keeps capturing as a (flying) king in the same move**.  A single move can
therefore be "man jumps, is crowned, the new king jumps again", and the piece
finishes the move as a king (`Move.promotes` is set, the search and
`make_move()` both handle it).

This was found in live play: the site offers the continuation, so a bot that
stops at the crown row plays a move the server does not consider finished and
then has to resynchronise.  The web bot, the panel, the CLI defaults and the iOS
app all use `joker`; `brazilian` keeps the strict international behaviour.

### `brazilian` — 8x8 international draughts

* **kings fly**: a king slides any number of empty squares along a diagonal and
  may capture an enemy piece **from any distance**, landing on **any** free
  square behind it, so one king can sweep several pieces in a single move;
* **men capture backwards too**: a man jumps in all four diagonal directions
  (it still *moves* forwards only and is crowned when it lands on the far row);
* **the longest capture is compulsory**: `capture_moves()` keeps only the
  sequences that take the maximum number of pieces;
* captured pieces stay on the board until the move ends, so a piece cannot jump
  the same piece twice nor pass over a piece it already took;
* a chain must be played to the end; a man that lands on the far row is crowned
  and the move ends immediately (no further jumps as a king).

### `russian` — Russian draughts / *damka*

Same as above but men capture **forwards only** and any capture (not just the
longest) may be chosen.

### `english` — American checkers / English draughts

* men step one square diagonally forward, kings step one square in any of the
  four diagonal directions (no flying kings);
* captures jump over an adjacent enemy piece onto the empty square behind it;
  several jumps are chained into one move.

### How the site's rule set was identified

Not by guessing — by replaying live games:

1. A live game showed the site's king taking two men **from a distance** and
   landing far behind them (`c1xe7`, capturing `e3` and `f6`, landing `g5`
   then `e7`) — impossible with one-square kings;
2. another live game showed a man capturing **backwards** in a chain
   (`f6xe5xc5`, landing `d4` then `b6`) — impossible for English *and* Russian
   men;
3. a 30-move live game was then replayed board by board: **every one of our
   moves and every opponent reply is legal under `brazilian` rules and no other
   rule set** (`0 unexplained position(s)`), which is what the two unit-test
   positions above encode;
4. live probes of our kings now match the site square for square, e.g.
   `probe e5 (king): site b8, c7, g7, d6, f6, d4, f4, c3, g3, b2, a1 | engine …`
   (identical lists);
5. one more difference showed up in live play: a man that captures onto the
   promotion row must **keep capturing as a king** — the site offers the
   continuation, strict international rules do not.  That is the `joker` rule
   set, and it is what the bot plays by default now.

`web_bot.py --diagnose` prints that same stone-by-stone comparison for any live
position, so the rules can be re-verified at any time.

Common to all rule sets:

* 8x8 board, 12 men per side, red at the bottom moving up, black on top moving
  down, red moves first (`web_bot.py` calibrates which engine colour we are from
  the site's own answers);
* **capturing is mandatory**: `Board.generate_moves()` only returns capture
  sequences when at least one exists, so the search can never pick a quiet move
  while a jump is available;
* a side with no legal move loses;
* `game.py` additionally declares a draw after 40 moves per side without a
  capture or a promotion.

The English generator is validated by `perft` against the published node counts:
**7, 49, 302, 1469, 7361** for depths 1-5 (identical for the other rule sets in
the opening, because no king exists yet and men's *quiet* moves are the same).

## How the search works

| Component | What it does |
| --- | --- |
| Negamax + alpha-beta | full width minimax with pruning; beta cutoffs end a branch as soon as it cannot influence the result |
| Transposition table | `dict` keyed by `(tuple(cells), side)` storing depth / score / bound / best move, removes repeated work from move transpositions |
| Iterative deepening | searches depth 1, 2, 3 ... up to a time budget and always returns the best move of the last completed iteration |
| Dynamic depth | `CheckersAI.dynamic_depth()`: crowded boards use `max_depth`, endgames add up to `max_extra_depth` extra plies (e.g. 6 -> 12 with fewer than 7 pieces) |
| Jump quiescence | when the depth budget runs out while the side to move is forced to jump, the search keeps following the jumps, which removes the horizon effect |
| Move ordering | transposition move first, then captures by number of pieces, promotions and piece-square gain - this is where most of alpha-beta's speed comes from |

Evaluation (`ai.evaluate`, centipawns, from red's point of view):

* material - man `100`, king `180` (the dominant term on purpose);
* piece-square tables - small advancement bonus towards promotion, centre
  control, edge squares (impossible to jump over, therefore safe), home row
  defence for men, centre for kings (black tables are 180 degree mirrors);
* mobility - empty diagonal steps, weight 1.

Keeping the positional terms small matters: with large piece-square bonuses the
engine starts chasing space and loses material, with small ones it plays solid
tactical checkers and converts its material advantage.

## Repetition handling

`CheckersAI.choose_move(board, history=...)` accepts the list of
`board.position_key()` values seen earlier in the game. Such positions are
scored as a draw inside the search, so a winning engine keeps making progress
instead of shuffling a won game into a 40-move draw (a losing engine may still
repeat deliberately to draw). `game.py` maintains that history automatically.

## Measured performance

Single core, CPython 3.10, 2 second budget per move:

| Position | Depth reached | Nodes | Time |
| --- | --- | --- | --- |
| opening position | 10 plies | 121,856 | 2.0 s |
| mid-game, 9 v 9 pieces | 10 plies | 141,312 | 2.0 s |
| opening, fixed depth 8 | 8 plies | 16,061 | 0.26 s (~61k nodes/s) |

Strength checks (all with a 0.2 s budget per move):

* beats a 1-ply greedy opponent as RED in 45 plies and as BLACK in 58 plies;
* an engine-vs-engine game at depth 6 / 0.2 s is decided in 163 plies (~26 s)
  instead of shuffling into a draw.

## Tests

```powershell
python -m unittest discover -s tests -t . -v
```

81 tests. `tests/test_engine.py` (43) covers the starting position,
perft, mandatory captures, chained jumps, king jumps in every direction,
promotion (including "the move ends when a man is crowned"), make/unmake
round-trips, winner detection, move parsing, evaluation symmetry, the Russian
flying-king rules (slides, long captures, landing anywhere behind, no double
capture, men still capture forwards only), the Brazilian rules (backwards man
captures exactly as the site played them, compulsory longest capture), the
`joker` rules (a crowned man keeps capturing as a king, the same move ends as a
king, make/unmake of such a chain, the engine picks it) and the
search (legality, safe-jump choice, forced wins, time limit, repetition
avoidance, dynamic depth).
`tests/test_web_bot.py` (11) covers the coordinate mapping, jump-path and
first-landing reconstruction, site-move filtering (including forced captures),
DOM state parsing, and real Playwright runs against `tests/mock_checkers.html`:
normal play, a forced double jump (`b2xd4xf6`, two landings clicked) and a
180°-flipped board that the bot has to calibrate itself. The browser tests are
skipped automatically when Chromium is missing.
`tests/test_gui.py` (15) covers the panel's logic: the settings <-> command line
mapping for all three start modes, the stake ladder, the engine/browser options,
that the password is never written to the settings file, and the settings
round-trip.
`tests/test_ios_site.py` (11) covers the iOS bridge: the JSON transport
(`read_state`, `click_stone` / `click_square`, the `canAction` poll, a dead
WebView becoming a `SiteError`, the "never ask for a password" rules) and, with
Playwright standing in for WKWebView, the shared bot playing on
`tests/mock_checkers.html` through DOM-only clicks — including a forced double
jump and a full `bot.run(max_moves=1)`. Skipped automatically without Chromium.

## Notes / limitations

* "Unbeatable" in the practical sense: with the default 3 s budget the engine
  searches 8-10 plies plus complete capture sequences, it never misses a forced
  jump or a material win inside the horizon, and it beats tactical (1-ply)
  opponents from both colours. Perfect play against a strong human requires an
  endgame database (tablebase), which is deliberately out of scope for this step.
* Very long king-versus-king endgames can still end in a 40-move draw; the
  engine has no endgame tablebase, only search plus the heuristic evaluation.
* Draws are declared by the caller (`game.py` uses the 40 move rule); the
  search itself treats the game as a win/loss tree.
* The default rule set is `--rules joker`: flying kings, men capture backwards,
  the longest capture is compulsory **and** a man crowned in the middle of a
  capture keeps capturing as a king — that is what jokergame.cc plays, verified
  by replaying live games (see "Rules implemented"). `--rules brazilian` is the
  strict international variant (the move ends on the crown row),
  `--rules russian` (men capture forwards only, any capture) and
  `--rules english` (American checkers) are kept as well.
* `web_bot.py` automates a real browser session on a real account. Use it only
  where automated play is allowed by the site's rules, and keep the request rate
  sane (`--poll`, `--time`).
* The selectors mirror the current jokergame.cc client (`game.js` / `stones.js`).
  If the site changes its markup, run `--discover`, look at `logs/`, and update
  `SELECTORS` in `checkersbot/site.py` — neither the bot nor the iOS app depends
  on the DOM anywhere else.
* The browser bridge was validated against `tests/mock_checkers.html` and against
  the site's own client code, not against a logged-in live game (no account was
  used). Run `--discover` / `--dry-run` first on the real site.
* The iOS app needs macOS + Xcode to produce an `.ipa` (or the GitHub Actions
  workflow in `.github/workflows/ios.yml`). It is meant for the owner's own
  device via iLoader / TestFlight: an App Store build of a bot that plays a
  staked game on jokergame.cc would not pass review. It also does not walk the
  lobby — you open the table yourself — and iOS suspends the app when you leave
  it, so a game pauses in the background.
