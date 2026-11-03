# Checkers AI Engine (8x8, JokerGame rules)

A Python checkers engine built as the brain of an automated bot for the web game
on jokergame.cc. It is a negamax searcher with alpha-beta pruning, a
transposition table, iterative deepening, jump quiescence and a hand tuned
evaluation function, plus a Playwright bridge that plays the game in a real
browser and an iOS app that plays it inside a WKWebView.

There is **one** rule set in the whole project — `checkersbot/rules.py` — and every
component (engine, bot, GUI, CLI, iOS app, test board) uses it.

```
.
├── checkersbot/             # the shared core (no browser library in here)
│   ├── rules.py             # the one rule set (JOKERGAME) + where each rule comes from
│   ├── board.py             # 8x8 board, pieces, move generation, forced jumps,
│   │                        #   kinging, flying kings, immediate capture removal
│   ├── ai.py                # Minimax + alpha-beta search, evaluation, dynamic depth
│   ├── site.py              # jokergame.cc protocol: SELECTORS, the two JS scripts,
│   │                        #   SiteState, site_legal_moves + the Site interface
│   ├── lab.py               # logic behind board_lab.py (positions, diagnostics, logs)
│   ├── record.py            # JSON record of a played game
│   └── bot.py               # CheckersBot: probe -> pick -> click -> verify
├── src/checkersbot_ios/     # the iPhone/iPad app (BeeWare Toga + WebView)
│   ├── app.py               # window: start/stop, depth, seconds, WebView, log
│   ├── site.py              # WebViewSite - the same Site, driven by toga.WebView JS
│   └── __main__.py          # Briefcase entry point
├── pyproject.toml           # Briefcase project (iOS build only, see "iOS app")
├── game.py                  # terminal CLI: play against the engine / engine self-play
├── board_lab.py             # interactive test board: positions, paths, analysis, logs
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
python game.py --depth 9 --time 5                          # JokerGame rules (the only set)
python game.py --selfplay 2 --depth 5 --time 1 --verbose   # automated smoke test
```

There is no `--rules` option anywhere: the engine, the bot, `CheckersBot.exe`, the
iOS app and `board_lab.py` all play the same `JOKERGAME` configuration.

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

What the panel offers — two pages, like the iOS app:

* **🏠 ლობი — პარამეტრები**: everything is chosen here (where the run starts,
  sign-in, stake, browser, engine, behaviour switches, start/diagnose/test-board);
* **🎮 თამაში — live**: only what is needed while the bot plays — a status line,
  the four **live switches** (😞 მარცხი, 💰 ფსონის გაზრდაზე დათანხმება,
  🔁 ავტო-რემატჩი, ⏱ ადამიანური ფიქრი — they apply **immediately**, no restart),
  `■ შეჩერება`, `↻ თავიდან გაშვება` (stop + start with the same settings),
  the log folders, `← ლობი` and the live log.

| group | what it does |
| --- | --- |
| **1. საიდან დაიწყოს თამაში** | `ავტორიზაცია → ლობი` (signs in with the e-mail/password, then walks the lobby), `ლობი` (the browser profile is already signed in) or `უკვე ღია დაფა` (no lobby at all — the bot only plays the table that is already open, `--no-lobby`) |
| **2. ავტორიზაცია** | e-mail/phone + password; the password stays in memory and is **never** written to disk |
| **3. მაგიდა და თანხა** | stake ladder exactly as the site renders it (`0` = 50, `1` = 5 000, `2` = 50 000, `3` = 200 000, `4` = 1 000 000, `5` = 10 000 000 coins), browser profile folder, browser (Playwright Chromium / installed Chrome / Edge) and log folder |
| **4. ძრავა და ქცევა** | depth, seconds per move, poll interval, max moves, **max games**, **rematch wait**, **thinking times** (2,3,5,7), plus headless / close-browser / board-trace / dry-run / probe / **😞 მარცხი (ადამიანურად)** / **💰 ფსონის გაზრდაზე დათანხმება** / **🔁 ავტო-რემატჩი** / **⏱ ფიქრი** switches — all of them are also on the live page |
| **lobby buttons** | ▶ დაწყება, ✔ წესების შემოწმება (`--diagnose`), 🧪 სატესტო დაფა (`board_lab.py`; inside the exe the same executable restarts with `--test-board`), 💾 შენახვა, 📂 ლოგები, 📂 პროფილი |
| **live buttons** | ■ შეჩერება (stops after the current move), ↻ თავიდან გაშვება, 📂 folders, ← ლობი |
| **log** | live output of the bot (same lines as the CLI), mirrored to a file with `--log-file` |

Every switch in the panel maps to a real `web_bot.py` flag (`--headless`,
`--close-browser`, `--trace-board`, `--dry-run`, `--no-probe`, `--accept-stake`,
`--no-rematch`, `--no-think-delays`, `--no-fit`), and a test builds the panel's
command line with **all of them on** and parses it, so a dead control cannot
creep in: `tests/test_gui.py::test_every_toggle_maps_to_a_real_cli_flag`.

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

### Battery and heat on the phone

Everything the app does per move was measured, and the three hot spots were the
engine search, the JavaScript round trips and the log view:

| what | before | now |
| --- | --- | --- |
| engine CPU per move | **11.9 s** (hard 12 s, soft 9 s) | **4.7 s** (hard 6 s, **soft 3 s** - no new iteration starts after 3 s, a running one is never cut) |
| narrow positions (2-3 legal moves) | the full clock | **2 s hard / 1 s soft** (`NARROW_SECONDS`) - such a position has no long plan to find |
| board reads while the engine thinks | every 0.4 s (~30 per move) | **every 1 s** (~6 per move) - still well inside the site's ~10 s stake dialog |
| board polling (`poll`) | 1.0 s | 1.0 s (`BOARD_POLL_S`) |
| log view redraws | one per line | **at most 2 per second** (`LOG_REFRESH_S`) |

Measured on the same mid-game position: 11.86 s -> **4.68 s** of CPU per move
(-60 %), depth 12 -> 10.  The knobs live at the top of
`src/checkersbot_ios/app.py`: raise `SECONDS`/`SOFT_SECONDS` for more strength,
lower them for a cooler phone (the CLI has `--soft-time` / `--narrow-time`).

The screen is still kept awake while the bot runs (`idleTimerDisabled`): iOS
suspends a backgrounded app, so without it the game would stop.  The display is
the one heat source that cannot be removed - if the phone gets hot, play with the
screen brightness down or let the bot run on the PC instead.

### Use it

1. Open the app and sign in on jokergame.cc inside it (the WebView keeps the
   cookies; the app never stores a password). You do **not** have to open a
   table: the bot walks the lobby itself.
2. **Two pages: 🎮 Game and ⚙ Settings.** The JokerGame WebView owns the whole
   game page; a bottom tab bar switches between the two pages and nothing else:

   ```
   ┌────────────────────────────────────────┐   ┌──────────────────────────────┐
   │                                        │   │ ● მუშაობს ▶ ↻ ⟲ 📜 ⓘ        │
   │        JokerGame (full screen)         │   ├──────────────────────────────┤
   │                                        │   │ 🎚 ძრავა                     │
   │                                        │   │   სიძლიერე ▁▂▃▄▅ 12          │
   ├────────────────────────────────────────┤   │ 🎮 თამაში                    │
   │  ● მუშაობს                      ▶     │   │ 🧠 ქცევა  …  ↕ scroll        │
   ├────────────────────────────────────────┤   ├──────────────────────────────┤
   │      🎮 თამაში      ⚙ პარამეტრები     │   │   🎮 თამაში  ⚙ პარამეტრები  │
   └────────────────────────────────────────┘   └──────────────────────────────┘
   ```

   **Page switching (`ios-35`).**  Only **one** page is ever mounted: `_show_page()`
   removes the inactive page from the `pages` box and adds the wanted one.  This is
   deliberate - Toga 0.5.6 does **not** drop a `display="none"` widget from the
   layout (`PackLogic._hidden` only looks at `visibility`), so the earlier
   display-based switch left both pages sharing the height: the settings stayed
   visible under the game and the WebView was squeezed into the top half (measured
   402x305 in a 402x874 window).  `Widget.remove()` only detaches native views
   (`set_window()` is a no-op on iOS), so the WKWebView keeps its loaded match,
   cookies and zoom and is simply re-attached on the way back.

   The white **"CheckersBot" header** was Toga's own window chrome:
   `toga_iOS.window.MainWindow` builds a `NavigationContainer`, i.e. a real
   `UINavigationController` whose navigation bar draws the window title, and
   `MainWindow.content_native_layout()` adds that bar's height to
   `container.top_inset` so every widget is laid out below it.  `ios-35` hides the
   bar (`setNavigationBarHidden:animated:`) and keeps `top_inset` at the real
   **safe-area top** (59 pt on an iPhone 17 Pro), so the freed height goes to the
   game while the status bar / Dynamic Island space stays clear:

   ```
   overlay: header navigation bar hidden=True (was 44 pt tall), top inset 59 pt
   ```

   Every switch logs the mounted tree, the WebView frame and the load counter
   (`webview_loads` must stay 1):

   ```
   გვერდი: ⚙ პარამეტრები (WebView #1, იგივე)
   overlay: page=settings mounted=1 webview_loads=1
   overlay: webview 390x815 @ 0,59 | window 390x874
   overlay: pages: mounted tree
     Box display=pack flex=1 height=None frame=390x822@0,0
       Box display=pack flex=1 height=None frame=390x822@0,0
   ```

   **Settings page.** Every setting lives on its own scrollable page (a Toga
   `ScrollContainer`, `horizontal=False`) grouped into 🎚 ძრავა, 🎮 თამაში,
   🧠 ქცევა and 🔍 დიაგნოსტიკა.  Toga fills `UIScrollView.contentSize` in
   `ScrollContainer.content_refreshed()` from the *layout* height of the content
   box; if that value is too small the list silently refuses to scroll, so on every
   switch to the page `overlay.ensure_vertical_scroll()` re-measures the content
   (Toga layout **and** the native subviews, whichever is larger) and

   - raises `contentSize` (and the document frame) to the measured height,
   - sets `alwaysBounceVertical`, `isScrollEnabled`, `showsVerticalScrollIndicator`,
     `delaysContentTouches = False` (otherwise iOS swallows the first tap on a
     control inside a scroll view) and `contentInsetAdjustmentBehavior = never`
     (the page already sits inside the safe area, so no double inset),

   then logs the raw numbers:

   ```
   overlay: scroll ok - viewport 390x504, content 812 pt (contentSize 44->812, delaysContentTouches=False)
   scroll: frame=390x504 contentSize=390x812 offset=0,0 doc=390x812 enabled=True bounce=True delays=False inset=2
   ```

   **Header buttons (settings page):** **● status** (მუშაობს / გაჩერებული /
   ელოდება / შეცდომა, fed by the bot's own log lines), **▶ / ■** start-stop,
   **↻** restart the bot thread with the current settings, **⟲** reload the lobby
   page (the only action that navigates the WebView), **📜** show/hide the log
   section at the bottom of the page, **ⓘ** show the last log lines and the live
   native numbers in a `UIAlertController` (works even if the settings page had a
   problem).  The game page keeps only **● status** and **▶ / ■**.

   **Why the floating panel is gone.** Builds `ios-28` … `ios-33` tried to float a
   native panel over the WebView. Each attempt failed on the device in a different
   way (a half-drawn game from pinning the WebView; a panel that never appeared
   because rubicon-objc exposes `superview`/`window` as *properties*, not methods; a
   settings body laid out outside its 44 pt box). The panel code has been removed
   from the app: two plain Toga pages need no constraint surgery, and the only
   remaining UIKit helpers are read-only diagnostics plus the safe-area background
   paint (`overlay.read` / `describe_*` / `scroll_report` / `show_alert`).

   Diagnostics are still printed at startup, after the page loads and on every page
   switch (`overlay: diagnostics`, `overlay: page state`, `scroll: …`), and the
   **ⓘ** button shows the same numbers in a native alert.

3. The settings page holds every setting the bot has — **სიძლიერე (სიღრმე)**,
   **წამი/სვლა**, **რბილი ლიმიტი**, **poll**, **მაქს. თამაში / მაქს. სვლა**,
   **რემატჩის / ჩატის ლოდინი**, **ფსონი**, and the switches
   **⏱ ადამიანური ფიქრი · 😞 მარცხი · 💰 ფსონის გაზრდა · 🔁 ავტო-რემატჩი ·
   👁 dry-run · 📋 დაფის ტრეისი (opponent answers) · 🔎 probe**.
   Everything is applied to the **running** bot the moment it changes (the hint
   under the controls confirms it) — no restart, no page reload. The only
   exceptions are **მაქს. თამაში** and **მაქს. სვლა**, which are arguments of
   `bot.run()`: changing them shows “⚠ … შემდეგ გაშვებაზე შევა ძალაში”, and
   **↻ განახლება** restarts the bot thread (the game page keeps running) to pick
   them up.
4. It plays **depth 18 with 6 s per move** (`DEPTH` / `SECONDS` /
   `SOFT_SECONDS` in `src/checkersbot_ios/app.py` — raise them for more strength,
   lower them for a cooler phone) and keeps going: when a game ends it waits up
   to 30 s for the **opponent** to offer a rematch (it never offers one itself),
   accepts it if it comes, and otherwise leaves the table and looks for a new
   opponent — until you press **■**.
   the game continues when nobody touches the phone. iOS suspends apps that go
   to the background, so **closing the app pauses the game** — a bot that has to
   keep playing while the phone is in your pocket needs to run on a PC/server.
5. Signing in stays manual (the app never stores a password) and the HTML dump
   still works and is written to the app's `Logs` folder.

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

**Every capture is logged with its route and victims**: `our move: b2xb6   [... |
takes 2: c3, c5   route d4 → b6]`. Any completed capture path is legal — the
longest one is **not** forced (the user's observation of JokerGame) — so the log
shows which route the engine chose and what it took. After a
capture the bot also compares the site's stone count with the count its own move
predicted: if the site kept a piece the model thought was taken (a chain that
ended differently), it logs `! the site holds N stones after …, M were expected`
and saves `logs/post-move-mismatch-*.html/.png` once, so the position can be
reproduced. Tick **„დაფის ტრეისი ლოგში"** (`--trace-board`) to print the whole
board before every move — the fastest way to report a position that looks wrong.

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
python web_bot.py --time 3 --depth 9                  # 3 s per move (quick games)
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
| `--depth` / `--time` | `18` / `12.0` | engine depth cap / seconds per move — **more time is straight strength** (12 s reaches ~depth 12, 391k nodes) |
| `--poll` | `1.0` | seconds between board polls while waiting |
| `--coins` | `0` | lobby stake: `0` = 50, `1` = 5 000, `2` = 50 000, `3` = 200 000, `4` = 1 000 000, `5` = 10 000 000 coins |
| `--email` / `--password` | env `JOKER_EMAIL` / `JOKER_PASSWORD` | sign-in credentials (never written to disk) |
| `--window-size` | auto | force the browser window, e.g. `1200x760` |
| `--no-fit` | off | do not resize/nudge the page (keep the site's own layout) |
| `--max-moves` | `0` (∞) | stop after N moves |
| `--max-games` | `0` (∞) | stop after N games — the default plays on: rematch, then a new opponent |
| `--rematch-wait` | `30` | seconds to wait for the **opponent's** rematch offer after a game (the bot never asks itself) |
| `--chat-wait` | `5` | seconds a chat invitation is left alone before the bot declines it and plays on |
| `--think-delays` | `2,3,5,7` | human-like pauses when the position was hard: the first move of a game is quick, later moves wait one of these at random (`0` = no waiting) |
| `--no-think-delays` | off | no human pause at all: the bot moves as soon as its search is finished (the panel's **⏱ ფიქრი 2/3/5/7 (ცოცხლად)** switch does the same live) |
| `--quick-delays` | `1,2,3` | pauses used instead when the move was obvious |
| `--lose` | off | **human loss**: play average-player moves on purpose, nothing is handed over for free |
| `--accept-stake` | off | agree when the opponent raises the stake (off = the raise is declined, it costs coins) |
| `--no-rematch` | off | do not wait for a rematch: leave the table and search a new opponent at once |
| `--max-errors` | `5` | stop after N consecutive failures (screenshots are saved to `logs/`) |
| `--no-probe` | off | trust the engine without asking the site (debug only) |
| `--trace-board` | off | log the parsed board before every move |
| `--diagnose` | off | compare the site's legal moves with our rules stone by stone and exit |
| `--no-lobby` | off | never walk the lobby: only play a board that is already open |
| `--channel` | – | launch an installed browser instead of Playwright's Chromium (`chrome`, `msedge`) |
| `--discover-only-board` | off | with `--discover`: read the board but do not click stones |
| `--close-browser` | off | close the browser when the bot stops (by default the window **stays open** on the finished game / lobby) |
| `--once`, `--dry-run`, `--discover`, `--headless` | | modes described above |

### Automatic play — game after game

With the defaults (`--max-games 0`, `--max-moves 0`) the bot plays until you stop
it:

1. the lobby is walked once (**Play a game → Random people → stake**);
2. the game is played to the end;
3. the bot **does not ask for a rematch**. It keeps the finished table open for
   `--rematch-wait` seconds (30 by default, the site's own window) and watches the
   page for the **opponent's** offer: as soon as `init_users.rematch_opponent` /
   the site's `.rm-offer` banner appears, it accepts it with the same client call
   the banner's button makes (`init_play.rematch({opponent_id})`, or a click on
   that banner) — the log says
   `the opponent offered a rematch - accepted (accepted:123); waiting for the new game`;
4. once the page reloads with a new `game_id` the bot carries on (a rematch can swap
   the colours, so it calibrates itself again);
5. if no offer arrives within the wait, or a rematch is not wanted at all
   (**🔁 ოპონენტის რემატჩის ლოდინი** / `--no-rematch`), it leaves the table,
   reloads the lobby and searches a new opponent — then it is back at step 2. The
   log line is `no rematch offer from the opponent in 30s`.

`--rematch-wait` tunes step 3 and `--max-games` stops after N games; the panel has
the same two fields (`მაქს. თამაში`, `რემატჩის ლოდინი`). `■ შეჩერება` / Ctrl+C
stops between moves. The iOS app does exactly the same with `lobby_walk=True`
and `STAKE = 0`.

**Human pace.** The engine answers in milliseconds, which looks robotic, so the
bot waits like a player would: the **first move of a game is played quickly**
(0.4–1.2 s) and every later move waits one of `--think-delays` seconds at random
(default `2,3,5,7`) — **but only when the position deserved it**. The pause now
comes *after* the search, so the bot knows how hard the move was: a position with
one or two choices, a forced capture or a move that is clearly better than every
alternative (the engine reports the gap to the runner-up, ≥ 1.5 men counts as
obvious) waits one of the shorter `--quick-delays` (default `1,2,3`) instead of
seven seconds. The log says which one it used:
`thinking 3.0s ... (easy position)` / `thinking 7.0s ... (thinking)` /
`thinking 0.8s ... (opening)`.

The pauses can be switched **while the bot plays**: the panel's
**⏱ ფიქრი 2/3/5/7 (ცოცხლად)** checkbox is polled before every move — off means no
pause at all, the bot moves as soon as its search is finished (only the search time
remains). The same at start-up: `--no-think-delays` (or `--think-delays 0`, or an
empty panel field).

**Fast lobby.** Returning to the lobby used to wait up to 30 s *after every
click* (`Play a game` → `Random people` → stake are three separate dialogs, and
the board cannot appear before the last one). The bot now clicks through them as
fast as they render, keeps polling for the table, and — if the site's
"Searching in progress" dialog reports an error — presses its own **Cancel**
button and starts the search again instead of stalling. A healthy search is
allowed to run as long as it needs, and when no table comes back the bot **keeps
searching until you stop it** (it only logs `no table yet - searching again`).

### Mid-game dialogs (draw offer, bigger stake, chat)

Three dialogs can appear in the middle of a game — each of them freezes the board,
and the clock keeps running behind them:

* **the opponent offers a draw** — `game_data.opponent.draw` makes the client
  pop up „მოწინააღმდეგე გთავაზობთ ფრეს“ with **თანხმობა / უარი**. Ignoring it
  loses the game on time (the site's own endgames are usually dead draws anyway,
  e.g. two against two), so the bot **always agrees**: it clicks the dialog's
  accept button (`the opponent offers a draw - agreed (clicked)`), answers each
  offer exactly once, and only waits behind the dialog for a few seconds if the
  button cannot be found (then it saves `logs/draw-offer-*.html/.png` once);
* **the opponent offers a bigger stake** — the site sets
  `game_data.opponent.coins > 0` and shows a dialog that is **not cancelable** and
  only lives for ~10 s. **By default the bot refuses** (a raise costs coins): it
  presses the dialog's reject button when there is one
  (`the opponent offers to play for 5000 coins - declined (clicked), the stake
  stays as it is`) and otherwise lets the offer expire, then plays on. Tick
  **💰 ფსონის გაზრდაზე დათანხმება** in the panel - or the same switch in the iOS
  app, or `--accept-stake` on the CLI - and it agrees instead
  (`... - accepted (clicked)`); either way the switch is **polled before every
  offer**, so ticking it while the bot plays already applies to the next raise, and
  the page is polled during the "thinking" pause **and while the engine searches**
  (the search runs in a worker thread, so a mid-search offer is never missed) and
  answered exactly once;
* **the opponent wants to chat** — `game_data.opponent.chat == 'yes'` shows a
  prompt that covers the board. The bot leaves it alone for `--chat-wait`
  seconds, so you can accept it yourself while watching, and then declines it
  (the site's own "decline" is a client-side close, nothing is sent) and plays on:
  `the opponent asks to chat - declining in 5s` → `chat invitation declined - playing on`.
  The server keeps `opponent.chat == 'yes'` until the invitation is **accepted**,
  so the flag alone must never block the game: the dialog is dismissed once, it is
  re-hidden if it comes back, and the bot plays on (it only ever waits the
  `--chat-wait` seconds once per invitation).

Both are recognised by the site's own translated strings (`lang.text(281)` /
`lang.text(284)`), so they keep working in Georgian, English or any other
language; the buttons are clicked by their translated label too. If the page
language cannot be read at all, the stake dialog is still answered: it is the
topmost dialog while `opponent.coins > 0`, and its first button is the accept one
(the bot only ever asks while a bet offer is pending). When nothing can be
clicked the bot saves `logs/stake-offer-*.html/.png` once, so the markup can be
checked.

### Losing on purpose („ადამიანური მარცხი“)

Both UIs have **one** loss switch (the old „მარცხი“ and „მარცხი (ადამიანურად)“
labels are merged into this single control): the **😞 ადამიანური მარცხი** button
next to Start/Stop in the panel (it ticks the checkbox in group 4), the
**😞 ადამიანური მარცხი** switch in the iOS app, and `--lose` on the CLI.
It does **not** play like a beginner: it plays like an **average club player**,
so a loss looks like a bad day, not like a thrown game:

* the position is ranked with a **shallow** search (`CheckersAI.rank_moves`,
  depth 4, worst → best) instead of the full one;
* every move that hands a piece over is dropped — the picks stay within
  `MEDIUM_WINDOW = 80` evaluation units (0.8 of a man) of the best move, so
  nothing is left hanging and no piece is given away for free;
* the best move itself is skipped, so the game really does drift towards a loss
  (a missed continuation, a passive move) — measured against a full-strength
  engine it loses material slowly instead of collapsing: ~9 units (0.09 man) per
  move on average, never more than 0.8;
* if every alternative is much worse, it plays the best move and loses slowly;
* the bot keeps playing and searching for the next opponent afterwards.

The switch only affects the running game; it is cleared when the bot stops.

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

## The rule set (JokerGame)

There is exactly one rule set in the project: `checkersbot/rules.py` defines
`JOKERGAME`, and the engine, the web bot, `CheckersBot.exe`, the iOS app, `game.py`
and `board_lab.py` all use it. `Board(..., rules=...)` accepts the `Rules` object or
its name `"joker"`; `resolve()` raises on anything else, so there is no English,
Russian or Brazilian mode any more.

### What JokerGame does

* 8x8 board, 32 dark squares, 12 men per side; red sits at the bottom (rows 5-7)
  and moves up, black on top and moves down, red moves first;
* a man steps one square diagonally forward and is crowned on the far row;
* **kings fly**: a king slides any number of empty squares along a diagonal and
  takes an enemy piece **from any distance**, landing on **any** free square behind
  it, so one king can sweep several pieces in a single move;
* **men capture backwards too**: a man jumps in all four diagonal directions (it
  still *moves* forwards only);
* **capturing is compulsory**: `Board.generate_moves()` returns captures only when
  at least one exists, so a quiet move can never be picked while a jump is on;
* **a chosen chain must be finished**: the generator only returns paths that end
  where no continuation is left, so stopping after `b2→d4` (when `d4` can still
  capture) is not a legal move;
* **on a branch any legal continuation may be chosen**;
* **the longest capture is *not* compulsory**: every completed path is legal,
  whatever number of pieces it takes (the user's observation of JokerGame);
* **crowning inside a capture continues**: a man that lands on its far row in the
  middle of a capture is crowned on the spot and keeps capturing as a (flying)
  king in the same move (`Move.promotes` is set);
* **a taken piece leaves the board immediately**, so a king may land on a square it
  has just cleared and continue in the opposite direction (user's observation);
* a side with no legal move loses; `game.py` additionally declares a draw after 40
  moves per side without a capture or a promotion.

### Worked example: two capture paths of different length

The position below (red to move) is a regression test and a preset in
`board_lab.py` ("ორი გზა, 2 და 3 ქვა (b2)"):

```
8  .....b..
7  ......b.
6  .....b.b
5  ..b...b.
4  ........
3  ..b.b.b.
2  .r......
1  ........
   abcdefgh
```

`b2` takes `c3` and lands on `d4`, where two continuations exist, so the move is
not finished there. Both completed paths are legal:

| path | landing squares | pieces taken |
| --- | --- | --- |
| `b2xb6` | `d4`, `b6` | `c3`, `c5` |
| `b2xh4` | `d4`, `f2`, `h4` | `c3`, `e3`, `g3` |

`b2→d4` alone is rejected (a continuation exists), and the two-piece path is **not**
removed because a three-piece alternative exists — that is the rule that used to be
wrong. `tests/test_board_lab.py` asserts exactly this, and the comparison mode feeds
both paths to the search with the same depth and clock, without preferring either.

### How the rule set was identified

Not by guessing — by replaying live games, plus the user's observation for the two
points that a replay cannot settle (immediate removal and "longest is optional"):

1. A live game showed the site's king taking two men **from a distance** and
   landing far behind them (`c1xe7`, capturing `e3` and `f6`, landing `g5`
   then `e7`) — impossible with one-square kings;
2. another live game showed a man capturing **backwards** in a chain
   (`f6xe5xc5`, landing `d4` then `b6`) — impossible for English *and* Russian
   men;
3. a 30-move live game was then replayed board by board: **every one of our
   moves and every opponent reply is legal under our generator** (`0 unexplained
   position(s)`);
4. live probes of our kings now match the site square for square, e.g.
   `probe e5 (king): site b8, c7, g7, d6, f6, d4, f4, c3, g3, b2, a1 | engine …`
   (identical lists);
5. a man that captures onto the promotion row must **keep capturing as a king** —
   the site offers the continuation, strict international rules do not;
6. the **immediate removal** of a taken piece and the fact that **the longest
   capture is not compulsory** come from the user's observation of JokerGame; they
   are marked as such in `rules.py`, in the test board's rule tab and in every
   saved log (`rules_basis`), and they are not claimed to be verified by the code.

`web_bot.py --diagnose` prints that same stone-by-stone comparison for any live
position, so the rules can be re-verified at any time.

The move generator is validated by `perft` against the published English node
counts: **7, 49, 302, 1469, 7361** for depths 1-5 (the opening has no kings, and
men's *quiet* moves are the same, so these counts hold here too).

## How the search works

| Component | What it does |
| --- | --- |
| Negamax + alpha-beta | full width minimax with pruning; beta cutoffs end a branch as soon as it cannot influence the result |
| Transposition table | `dict` keyed by `(tuple(cells), side)` storing depth / score / bound / best move, removes repeated work from move transpositions |
| Iterative deepening | searches depth 1, 2, 3 ... up to a time budget and always returns the best move of the last completed iteration |
| Dynamic depth | `CheckersAI.dynamic_depth()`: crowded boards use `max_depth`, endgames add up to `max_extra_depth` extra plies (e.g. 6 -> 12 with fewer than 7 pieces) |
| Jump quiescence | when the depth budget runs out while the side to move is forced to jump, the search keeps following the jumps, which removes the horizon effect (and the very last quiescence node is evaluated without generating moves at all) |
| Move ordering | transposition move first, then captures by number of pieces, promotions and piece-square gain - this is where most of alpha-beta's speed comes from |
| Cached rule flags | `flying_kings` / `men_capture_backwards` / `promotion_continues` are read once from the shared `Rules` object instead of comparing rule strings in every node |
| `ROW_OF` table | promotion checks in the move generator index a tuple instead of calling `row_of()` millions of times |
| Capture preference | JokerGame allows **any** completed capture chain (the longest is not compulsory, see `checkersbot/rules.py`), so the engine plays the move that takes more pieces when its evaluation is within `CAPTURE_MARGIN` (60) of the best one - `take as much as you can` without throwing the position away |

Evaluation (`ai.evaluate`, centipawns, from red's point of view):

* material - man `100`, king `180` (the dominant term on purpose);
* piece-square tables - small advancement bonus towards promotion, centre
  control, edge squares (impossible to jump over, therefore safe), home row
  defence for men, centre for kings (black tables are 180 degree mirrors);
* mobility - empty diagonal steps, weight 1;
* ~~back-row defence~~ (`BACK_ROW_WEIGHT`) - a man left on our own back row was
  worth extra, but a measured match showed **45.8 %** against the same engine
  without it, and it made the bot keep men at home (passive play that looked
  "illogical"), so it ships **switched off**; the code stays as a knob for the
  match runner.

Deliberately small positional terms matter: with large piece-square bonuses the
engine starts chasing space and loses material, with small ones it plays solid
tactical checkers and converts its material advantage — material, advancement and
mobility are what the "logical" play comes from.

### Making it stronger

The two knobs that matter are **time per move** and **depth cap**; the engine
always uses the whole clock (iterative deepening), so the clock is what buys
strength:

| | panel field | CLI | iOS (`app.py`) |
| --- | --- | --- | --- |
| think time | `წამი/სვლა` (**12 s**) | `--time 12` | `SECONDS = 12.0` |
| depth cap | `სიღრმე` (**18**) | `--depth 18` | `DEPTH = 18` |

* 12 s with the depth cap at 18 reaches **depth 13 in the opening** (630k nodes)
  and 13-18 plies in the endgame - the cap almost never binds, the clock does;
* the human "thinking" pause is **skipped when the search itself took 6 s or
  more** (`--think-delays`), so a strong move costs ~12-13 s, not 12 + 7;
* the site's turn clock allows ~30 s, so `--time 20` or even `--time 25` is still
  safe if you want maximum strength over speed of play;
* endgames get up to `max_extra_depth` (6) extra plies automatically, so a
  6-piece endgame is searched 18-24 plies deep;
* `scripts/engine_match.py` measures any change objectively: it plays two engine
  modules against each other with swapped colours and random openings
  (`python scripts/engine_match.py --games 20 --seconds 0.3
  --a checkersbot.ai --b <reference module>`, or `--set-a TRADE_WEIGHT=2` to try
  a constant), so an evaluation or search tweak is only kept when it actually
  scores more than 50 %. Two examples from this tuning round:
  * back-row defence + the faster generator: **67.9 %** (5 W / 0 L / 9 D) - kept;
  * a "trade when ahead" term (`TRADE_WEIGHT = 2`): **53.6 %** (3 W / 2 L / 9 D) -
    too close to noise, so it ships switched **off** (the code stays as a knob);
* what is *not* in here: opening book, endgame databases and a native (C/Cython)
  move generator. The Python move generator is ~70 % of the search time, so a
  native port is the single biggest remaining gain; the databases would make
  the last 8-10 pieces perfect.

## Interactive test board (`board_lab.py`)

A Georgian-language board for checking, by hand, what the engine actually does::

    python board_lab.py

* **Position editor** — click squares to place a red man/king or a black man/king,
  or erase (`წაშლა`; right click also erases). Toolbar: clear, start position, undo,
  save/open a position (JSON in `test_positions/`), presets for the interesting
  cases (including "ორი გზა, 2 და 3 ქვა (b2)" — the two-path position above). The
  side to move is selected separately; the rules are always JokerGame and the
  preset list has no rule option.
* **Coordinates** are drawn on the board edges and inside every playable square
  (`a1 … h8`). Unusual positions are *reported* (pieces on light squares, more than
  12 pieces, no legal move, empty side) and never silently repaired.
* **Moves and captures** — all legal moves with their full route, target squares
  when a piece is selected, numbered hops with the piece taken on each hop, step
  forward/back, and a separate button that plays the *complete* move on the board
  (one undo step). A playback step never changes the position. Different jump
  chains with the same start and end square are listed separately.
* **Capture diagnostics** — the real generator is asked: does a capture exist, how
  many completed paths are there, how many pieces each one takes, when a man is
  crowned and keeps capturing. There is no "excluded path" list any more: every
  completed path is a legal move, whatever its length.
* **Bot analysis** — choose the time limit and depth cap; shows the chosen move and
  its whole route, the score (positive = good for the side to move), the completed
  depth, nodes, time, a probable continuation read from the transposition table,
  and the static evaluation split into its components (separate from the searched
  score). A score of `არ შეფასდა` means the search finished no iteration at all —
  it is never shown as `0`. The analysis runs in a worker thread, the window stays
  responsive and `შეჩერება` stops it after the last completed iteration.
  `შეადარე ყველა სვლა` scores **every** legal first move with one depth target and
  one shared clock, with no preference for any of them; only rows that finished that
  depth are marked `მნიშვნელობა ზუსტი`, and a "best move" is named from the search
  only when every row is exact **at the same depth**.
* **Depth, exactness and proof are three different things** — see
  "Analysis reliability" below: the depth is half-moves from the board (root move
  included, a jump chain is one ply), `მნიშვნელობა ზუსტია` only says the value is a
  finished iteration at that depth, and `დადასტურებული შედეგი` comes from a separate
  full-width proof search (`checkersbot/solver.py`).
* **Stale results** — changing the position or the side to move *invalidates* the
  analysis on screen: the text and the comparison table are cleared and a late
  worker result for the old position is discarded (it used to stay visible, which
  made an old "5 paths" answer look like the answer for the new position).
* **Rules tab** — the JokerGame rule text (`lab.JOKERGAME_TEXT`, the same words in
  the saved log), which of its properties are visible in the current position, the
  number of capture paths and their lengths. It states that the removal rule and
  "the longest capture is optional" come from the user's observation and are not
  verified independently. Free-text fields store the path seen on the site and a
  note, so a real example can be compared later.
* **Logs** — every analysis writes `test_board_logs/analysis_<stamp>.json` plus a
  readable `.txt`: the exact position, side, rules and `rules_basis`, engine
  version, the "history is unknown" note (a hand-built board cannot detect a
  repetition draw), all legal moves, all completed capture paths with their
  lengths, the chosen move, the alternatives, and the site note.
* **Launcher** — `open_test_board.bat` (double-click) starts the board on the
  reported position (`--preset b2`; the fragment is ASCII because a `.bat` is read
  in the console code page). From a shell: `python board_lab.py --preset b2`
  (`--list-presets` prints the names, `--analyse 3` runs an analysis at once).

> **Backgammon** has its own test board with the same idea:
> `open_backgammon_board.bat` (or `backgammon\board_lab.py`) opens
> `backgammon/board_lab.py` — arrange a position, ask the bot and see *why*, play a
> whole game against it (record / undo / save) and measure every decision against
> the strong Open Sage reference. See `backgammon/README.md` § 10a.

## Analysis reliability

Three questions are answered separately, because they are different questions:

| Question | Where it comes from | How to read it |
| --- | --- | --- |
| "how good is this move as far as the search looks?" | `ai.choose_move` (alpha-beta, depth limited) | `ქულა`, positive = good for the side to move; `None` = not evaluated, never `0` |
| "is that number exact?" | a **finished** iteration at that depth | `მნიშვნელობა ზუსტია` — exact *for that depth*, not a bound, and **not** a proof of the result |
| "who wins with perfect play, and in how many half-moves?" | `checkersbot/solver.py` (full width, no evaluation) | `დადასტურებული შედეგი`, `proof.plies`, `best_line` |

**Depth unit.** One ply = one half-move counted **from the position on the board,
including the root move**. A complete jump chain is one ply, because it is one move.
The engine's cap is a *base* depth: `dynamic_depth` adds up to 6 plies in endgames, so
the completed depth may exceed the cap (the panel shows cap, target and completed
depth). The comparison panel searches each child with `depth - 1` plies and rebases
the result (`1 + child depth`), so the two panels always use the same unit; the raw
child number is kept as `child_depth`.

**Score normalisation.** Every row of the comparison is negated into the perspective
of the side to move at the board, so all alternatives of one position and one side
carry the same sign convention (in the position below every row is negative: red is
lost). Mate scores are `MATE - ply`; the distance reported for a row is
`_mate_in(score) + 1`, because the child search counted plies from the child position.

**Proof, not a sample line.** `solver.solve()` walks *every* legal reply with two
predicates - `win in k` (one line that beats all replies) and `loss in k` (all of the
loser's moves lose) - iterating `k` upwards, so the first `k` that succeeds is exactly
the distance under best play. `solver.best_line()` returns that line and the tests walk
it move by move: every move legal, length equal to `plies`, the loser stuck at the end.
One nice continuation proves nothing: after `b2xh4` black has a winning move, yet the
careless `f6-e5` leads to a proven **win for red** - a test, not a claim.

**What the report of this position says** (the position above, red to move):

| move | ordinary score | depth (root unit) | exact at that depth | proven |
| --- | --- | --- | --- | --- |
| `b2xb6` | −99993 | 6 | yes | loss in **8** plies |
| `b2xh4` | −99999 | 2 | yes | loss in **2** plies |

Both paths lose; the proven best resistance is `b2xb6` with 8 half-moves, i.e. 6 more
than `b2xh4`. The proof line is
`b2xb6 f8-e7 b6-a7 e7-d6 a7-b8 g5-f4 b8xe5 f6xd4` - red's last man is taken on ply 8.
The ordinary depths differ (6 vs 2) because the search stops as soon as it sees a
mate, so **no winner is named from the search** here: the panel says that instead of
comparing unequal numbers, and reports the proven result on its own line.

## Measurement and tuning (2026-10-27)

Everything below is measured on this machine (i5-10300H, Windows 11, Python 3.10)
with frozen positions, so a change can be re-checked instead of believed.

* **Reference engine.** `checkersbot/ai_baseline.py` is a frozen copy of
  `checkersbot/ai.py` as it was at commit `b8b1d9f` (comment-only header, same
  code). Every comparison uses it: `--b checkersbot.ai_baseline`.
* **Benchmark.** `python scripts/engine_bench.py --engine checkersbot.ai --seconds 2`
  runs six frozen positions (`opening`, `midgame`, `endgame_4`, `tactical_chain`,
  `tactical_multi`, `king_double`) and records the reached depth, the nodes, the
  time and the peak memory in `logs/bench_*.json`. `--seconds 0 --depth 8`
  compares the *same work* (equal depth) instead of the clock.
* **Matches.** `python scripts/engine_match.py --games 16 --seconds 0.15 --a checkersbot.ai --b checkersbot.ai_baseline`
  plays the JokerGame rules (the only rule set - `engine_match.py` has no rules
  option), **a pair of games from the same random opening with the colours
  swapped**, and prints W/D/L. A 16-game match is noisy: with two *identical*
  engines the tool returned 53.1 % (6 W / 5 L / 5 D), i.e. ±12 % is inside noise -
  small differences need many more games before they mean anything.
* **Where the time goes** (cProfile, depth 8, midgame): move generation 63 %
  (`capture_moves` 1.25 s + `quiet_moves` 0.66 s of 3.12 s), the search itself 14 %,
  `evaluate` 11 %, move ordering 6 %. The move generator - pure Python - is the
  real bottleneck; a native port is the biggest single gain, and it is *not* done.
* **Time budget (the biggest measured gain).** `info.step_timeout` is a per-move
  clock of about 60 s (57-60 s at every sample of every logged game) and the bot
  now derives its budget from it (`checkersbot/time_manager.py`): clock minus 6 s
  of reading/probing/clicking minus 20 % safety, soft limit at 75 % of that. On the
  frozen positions that is 42 s instead of the old fixed 12 s:

  | position | 12 s | 42 s (same 60 s clock) |
  | --- | --- | --- |
  | opening | depth 11 | **depth 12** |
  | midgame | depth 12 | **depth 14** |
  | endgame_4 | depth 15 | **depth 19** |

  `--time N` is now a *cap* on top of that (the panel and the iOS app still pass
  their own value); `--time-overhead` changes the reserved seconds. Without a site
  clock the old 12 s default stays.
* **Search refinements, measured one by one** (`CheckersAI(use_killers=…,
  use_pvs=…, use_aspiration=…)`, all **off by default**):

  | change | nodes at equal depth (6 positions) | verdict |
  | --- | --- | --- |
  | PVS | 566 062 → 549 957 (−2.8 %) | neutral, kept as a knob |
  | aspiration windows | 566 457 (±0) | neutral, kept as a knob |
  | killer + history ordering | 1 292 465 (**+128 %**) | clearly worse here, off |

  At equal *time* the three of them together reached the same depth as the plain
  search on these positions, so no strength claim is made for them.
* **Game records.** Every game writes `logs/game_record_<stamp>.json`: the position
  the bot first saw, our side, every move with its captures, score, depth, nodes,
  seconds and the time budget it was given, site mismatches, and the result **as
  the site states it** (`gf-won` / `gf-lost` on the finish dialog). If the dialog
  does not say, the result stays `"unknown"` - the evaluation sign is not evidence.
  No names, e-mails, passwords or tokens are written.

## Repetition handling

`CheckersAI.choose_move(board, history=...)` accepts the list of
`board.position_key()` values seen earlier in the game. Such positions are
scored as a draw inside the search, so a winning engine keeps making progress
instead of shuffling a won game into a 40-move draw (a losing engine may still
repeat deliberately to draw). `game.py` maintains that history automatically.

## Measured performance

Single core, CPython 3.10, `scripts/bench`-style runs on the opening position:

| Position | Depth reached | Nodes | Time |
| --- | --- | --- | --- |
| opening, 12 s budget | 13 plies | 629,760 | 12.0 s |
| opening, fixed depth 8 | 8 plies | 16,061 | 0.29 s (~55k nodes/s) |
| endgame (8 pieces), 10 depth cap | 13-15 plies | ~400k | 6-15 s |

Strength checks (all with a 0.2 s budget per move):

* beats a 1-ply greedy opponent as RED in 45 plies and as BLACK in 58 plies;
* an engine-vs-engine game at depth 6 / 0.2 s is decided in 163 plies (~26 s)
  instead of shuffling into a draw;
* **`scripts/engine_match.py` vs the previous release** (faster move generator and
  rule flags against the engine that shipped before it, swapped colours, random
  4-ply openings, 0.15 s per move): **5 W / 0 L / 9 D = 67.9 %** over 14 games;
* the back-row evaluation term, measured the same way: **45.8 %** (1 W / 2 L /
  9 D) - it did not help, so it is switched off (see above).

## Tests

```powershell
python -m unittest discover -s tests -t . -v
```

227 tests. `tests/test_engine.py` (51) covers the starting position, perft
(7 / 49 / 302 / 1469 for the opening plies, 7482 at ply 5 where the first capture
appears and men may also take backwards), mandatory captures, chained jumps, king
jumps in every direction, promotion, make/unmake round-trips, winner detection,
move parsing, evaluation symmetry, the **only** rule set (flying kings that slide
and capture at a distance and land on any free square behind the taken piece, men
that capture backwards, a man crowned inside a capture that keeps capturing as a
king, immediate removal, no maximum-capture filter, the two capture paths of the
report position in both pieces and in both colours, the engine and the real bot
accepting either path, other rule names being refused) and the search (legality,
safe-jump choice, forced wins, time limit, repetition avoidance, dynamic depth,
`only move` reported as *not evaluated* instead of a fake 0).
`tests/test_board_lab.py` (61) covers what the test board shows: the displayed
moves are exactly the engine's, every completed capture path is legal and none is
ever excluded, the two paths of the report position (2 and 3 pieces) with
`b2→d4` refused because it can continue, the 8-king-route position in both
colours with the diagonal-segment check, the position file round-trip **and its
migration** (an old `brazilian`/`end` file is converted to JokerGame and the change
is recorded), the equal-conditions comparison of both paths, the log contents
(no "maximum capture filter" line any more, the JokerGame rule text instead) and
the window (no rule/removal selectors, the analysis is invalidated when the
position changes, a late result for the old position is discarded, the launcher
finds the preset). It also pins the **analysis reliability**: the solver's outcome
and distance on small positions, agreement with an independent full-width
reference search in both colours, the depth unit (`depth == child_depth + 1`, the
root move included), the score direction (all rows of one side share one sign),
the mate distance measured from the root (`mate_in == proof.plies`), that a proven
result is never turned into a search winner, that `best_line()` is legal and ends
at the stated distance, and that an unproven result is reported as
"ამ სიღრმეზე ... არ არის დადასტურებული · ეს შეფასებაა, არა მტკიცება".
`tests/test_web_bot.py` (11) covers the coordinate mapping, jump-path and
first-landing reconstruction, site-move filtering (including forced captures),
DOM state parsing, and real Playwright runs against `tests/mock_checkers.html`:
normal play, a forced double jump (`b2xd4xf6`, two landings clicked) and a
180°-flipped board that the bot has to calibrate itself. The browser tests are
skipped automatically when Chromium is missing.
`tests/test_gui.py` (21) covers the panel's logic: the settings <-> command line
mapping for all three start modes, the stake ladder, the engine/browser options,
the **live ⏱ thinking switch** (`--no-think-delays`), the command the
**🧪 სატესტო დაფა** button runs (the script, or the frozen exe with `--test-board`),
that there is exactly **one** „ადამიანური მარცხი“ control, that the password is
never written to the settings file, and the settings round-trip.
`tests/test_auto_loop.py` (30) drives ``CheckersBot._next_game`` through a fake
site: the bot **never offers** a rematch, it accepts the opponent's offer and
keeps the table, it leaves for a new opponent when no offer comes within
``rematch_wait`` (or the switch is off), the live ⏱ switch stops and restarts the
human pauses, plus the offer/answer loop (stake, draw, chat) and the
"lose like a human" move choice.
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
* The one rule set is JokerGame (`checkersbot/rules.py`): flying kings, men
  capture backwards, capturing is compulsory but **the longest capture is not**
  (every completed path may be played), a taken piece leaves the board at once,
  and a man crowned in the middle of a capture keeps capturing as a king. The
  board, the bot, the panel, the CLI, the iOS app and the test board all use it;
  there is no `--rules` switch anywhere.
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
