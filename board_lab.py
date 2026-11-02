#!/usr/bin/env python3
"""ინტერაქტიული სატესტო დაფა — შაშის ძრავის ქცევის ხელით შესამოწმებლად.

გაშვება::

    python board_lab.py

რას აკეთებს:

* დაფაზე დაწკაპუნებით აწყობ პოზიციას (წითელი/შავი ქვა ან დამკა, წაშლა);
* ირჩევ ვისი სვლაა — წესები ყოველთვის JokerGame-ისაა (ერთი საერთო კონფიგურაცია);
* ხედავ ყველა ლეგალურ სვლას და ჭამის სრულ გზას ნაბიჯ-ნაბიჯ; ყველა დასრულებული
  ჭამის გზა ლეგალურია — ქვების რაოდენობა ფილტრი არ არის;
* ატარებ ძრავის ანალიზს (დროის ლიმიტით) და ადარებ ყველა საწყის სვლას ერთნაირ
  პირობებში (იგივე სიღრმე, იგივე დრო, არც ერთ სვლას წინასწარი უპირატესობა არ აქვს);
* პოზიციის ან სვლის მხარის შეცვლისას ძველი ანალიზი უქმდება — ეკრანზე აღარ რჩება
  სხვა პოზიციის შედეგი;
* ყოველ ანალიზს ინახავს ``test_board_logs/``-ში (JSON + ტექსტი), პოზიციებს კი
  ``test_positions/``-ში.

ლოკალური ძრავის ხელსაწყოა: საიტზე არაფერს აგზავნის და საიტის წესებს
დადასტურებულად არ აცხადებს - საიტზე ნანახი გზის ჩასაწერი ველები ამისთვისაა.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from checkersbot import lab
from checkersbot.board import BLACK, RED, square_name

CELL = 58
MARGIN = 34
GLYPH_UI = {1: "ქ", 2: "დ", -1: "ქ", -2: "დ"}
TOOLS = [
    ("r", "წითელი ქვა"),
    ("R", "წითელი დამკა"),
    ("b", "შავი ქვა"),
    ("B", "შავი დამკა"),
    ("erase", "წაშლა"),
]
TIME_CHOICES = ["0.5", "1", "2", "3", "5", "10", "20"]
DEPTH_CHOICES = ["4", "6", "8", "10", "12", "18", "60"]

#: პატარა, ხელით შემოწმებადი პოზიციები.  ``rules`` აღარ არსებობს: ყველა პოზიცია
#: ერთი და იმავე JokerGame კონფიგურაციით ითამაშება.
PRESETS: Dict[str, Dict[str, Any]] = {
    "საწყისი პოზიცია": {"rows": None, "turn": "red"},
    "ორი გზა, ერთი დასასრული (g1xb6)": {
        "rows": [
            ".....b..",
            "........",
            "........",
            "..b...b.",
            "........",
            "........",
            ".r...b..",
            "......R.",
        ],
        "turn": "red",
    },
    "ჭამა და დამკა ჭამის დროს (f6)": {
        "rows": [
            "........",
            "..b.b...",
            ".....r..",
            "........",
            "........",
            "........",
            "........",
            "........",
        ],
        "turn": "red",
    },
    "მფრინავი დამკა შორიდან (c3)": {
        "rows": [
            "........",
            "......b.",
            "........",
            "....b...",
            "........",
            "..R.....",
            "........",
            "........",
        ],
        "turn": "red",
    },
    # სურათის პოზიცია: b2 ჭამს c3-ს, შემდეგ ორი დასრულებული გზაა —
    # b2→d4→b6 (2 ქვა) და b2→d4→f2→h4 (3 ქვა).  ორივე ლეგალურია,
    # b2→d4 კი არა (გაგრძელება არსებობს).
    "ორი გზა, 2 და 3 ქვა (b2)": {
        "rows": [
            ".....b..",
            "......b.",
            ".....b.b",
            "..b...b.",
            "........",
            "..b.b.b.",
            ".r......",
            "........",
        ],
        "turn": "red",
    },
    "მფრინავი დამკა, 8 გზა (c3)": {
        "rows": [
            "........",
            "........",
            "........",
            "........",
            "...b....",
            "..R.....",
            ".b......",
            "........",
        ],
        "turn": "black",
    },
}


class BoardLab(tk.Tk):
    """მთავარი ფანჯარა: დაფა მარცხნივ, ხელსაწყოები მარჯვნივ."""

    def __init__(self) -> None:
        super().__init__()
        self.title("შაშის სატესტო დაფა — ძრავის ქცევის შემოწმება")
        self.geometry("1230x780")
        self.minsize(1080, 700)

        self.position = lab.LabPosition()
        self.tool = tk.StringVar(value="r")
        self.turn_var = tk.StringVar(value="red")
        self.mode_var = tk.StringVar(value="edit")      # edit | moves
        self.time_var = tk.StringVar(value="3")
        self.depth_var = tk.StringVar(value="10")
        self.compare_depth_var = tk.StringVar(value="8")
        self.compare_time_var = tk.StringVar(value="8")

        self.undo_stack: List[List[str]] = []
        self.move_rows: List[Dict[str, Any]] = []
        self.selected_piece: Optional[int] = None
        self.playback: Optional[Dict[str, Any]] = None
        self.highlight_targets: List[int] = []
        self.highlight_route: List[Dict[str, Any]] = []
        self.highlight_victims: List[int] = []

        self.worker: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.results: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        #: რომელი პოზიციის ანალიზია ეკრანზე.  პოზიციის/მხარის შეცვლისას იცვლება,
        #: ძველი შედეგი კი აღარასდროს ჩანს (არადა ეკრანზე რჩებოდა სხვა პოზიციის
        #: ანალიზი და ჩანდა „5 გზა“ იქ, სადაც 8 უნდა იყოს).
        self.analysis_key = ""

        self._build_layout()
        self.refresh_all()

    # ------------------------------------------------------------------ UI
    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)
        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, padding=(0, 0, 10, 0))
        self.canvas = tk.Canvas(left, width=8 * CELL + 2 * MARGIN,
                                height=8 * CELL + 2 * MARGIN, bg="#f2efe6",
                                highlightthickness=1, highlightbackground="#998")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.status = tk.StringVar(value="მზადაა")
        ttk.Label(left, textvariable=self.status, wraplength=8 * CELL,
                  foreground="#294").pack(fill="x", pady=(8, 0))
        self.problem_box = tk.Text(left, height=6, width=52, wrap="word",
                                   background="#fff8f0", relief="flat")
        self.problem_box.pack(fill="both", expand=False, pady=(6, 0))
        panes.add(left, weight=0)

        right = ttk.Frame(panes)
        panes.add(right, weight=1)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        self._build_editor_tab()
        self._build_moves_tab()
        self._build_analysis_tab()
        self._build_rules_tab()

    def _build_editor_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="1. პოზიცია")

        ttk.Label(tab, text="ხელსაწყო (დაფაზე დაწკაპუნებით დადებ/წაშლი):",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(tab)
        tools.grid(row=1, column=0, sticky="w", pady=(4, 8))
        for value, label in TOOLS:
            ttk.Radiobutton(tools, text=label, value=value, variable=self.tool,
                            command=self._draw_board).pack(side="left", padx=(0, 10))

        modes = ttk.LabelFrame(tab, text="რეჟიმი", padding=6)
        modes.grid(row=2, column=0, sticky="ew")
        ttk.Radiobutton(modes, text="რედაქტორი — ქვების დალაგება",
                        value="edit", variable=self.mode_var,
                        command=self.on_mode_change).pack(anchor="w")
        ttk.Radiobutton(modes, text="სვლები — ქვის არჩევა და სამიზნეების ჩვენება",
                        value="moves", variable=self.mode_var,
                        command=self.on_mode_change).pack(anchor="w")

        turn = ttk.LabelFrame(tab, text="სვლის მხარე", padding=6)
        turn.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Radiobutton(turn, text="წითელი (მოძრაობს ზემოთ, დამკდება 8-ე რიგზე)",
                        value="red", variable=self.turn_var,
                        command=self.on_turn_change).pack(anchor="w")
        ttk.Radiobutton(turn, text="შავი (მოძრაობს ქვემოთ, დამკდება 1-ლ რიგზე)",
                        value="black", variable=self.turn_var,
                        command=self.on_turn_change).pack(anchor="w")

        rules = ttk.LabelFrame(tab, text="წესები: JokerGame (ერთი საერთო კონფიგურაცია)", padding=6)
        rules.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.rules_text = tk.StringVar()
        ttk.Label(rules, textvariable=self.rules_text, wraplength=520,
                  foreground="#444").pack(anchor="w")

        buttons = ttk.Frame(tab)
        buttons.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        for text, command in (
            ("გასუფთავება", self.clear_board),
            ("საწყისი პოზიცია", self.reset_board),
            ("უკან დაბრუნება", self.undo),
            ("პოზიციის შენახვა", self.save_position),
            ("პოზიციის გახსნა", self.open_position),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 6))

        preset = ttk.LabelFrame(tab, text="მზა პოზიციები (სწრაფი გამოცდისთვის)",
                                padding=6)
        preset.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        self.preset_box = ttk.Combobox(preset, state="readonly", width=46,
                                       values=list(PRESETS))
        self.preset_box.pack(side="left")
        ttk.Button(preset, text="ჩატვირთვა",
                   command=self.load_preset).pack(side="left", padx=(6, 0))

    def _build_moves_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="2. სვლები და ჭამა")

        ttk.Label(tab, text="ლეგალური სვლები (აირჩიე ერთი და ნახე გზა ნაბიჯ-ნაბიჯ):",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.move_list = tk.Listbox(tab, height=9, width=64, activestyle="dotbox")
        self.move_list.grid(row=1, column=0, sticky="nsew", pady=(4, 6))
        self.move_list.bind("<<ListboxSelect>>", self.on_move_selected)
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        self.move_details = tk.Text(tab, height=9, width=64, wrap="word",
                                    background="#f7f7f2", relief="flat")
        self.move_details.grid(row=2, column=0, sticky="ew")

        controls = ttk.Frame(tab)
        controls.grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(controls, text="◀ უკან", command=lambda: self.step_playback(-1)
                   ).pack(side="left")
        ttk.Button(controls, text="წინ ▶", command=lambda: self.step_playback(1)
                   ).pack(side="left", padx=4)
        ttk.Button(controls, text="თავიდან", command=self.reset_playback
                   ).pack(side="left")
        ttk.Button(controls, text="სვლის დასრულება დაფაზე",
                   command=self.apply_playback_move).pack(side="left", padx=(12, 0))
        ttk.Button(controls, text="ლეგალური სვლების განახლება",
                   command=self.refresh_all).pack(side="left", padx=(12, 0))

        diag = ttk.LabelFrame(tab, text="ჭამის დიაგნოსტიკა (რეალური გენერატორიდან)",
                              padding=6)
        diag.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.diag_text = tk.Text(diag, height=7, width=64, wrap="word",
                                 background="#f4f8ff", relief="flat")
        self.diag_text.pack(fill="both", expand=True)
        self.raw_list = tk.Listbox(diag, height=4, width=64)
        self.raw_list.pack(fill="x", pady=(6, 0))
        ttk.Label(diag, text="ყველა დასრულებული ჭამის გზა ლეგალურია: ქვების "
                             "რაოდენობა ფილტრი არ არის. ჯაჭვის შუაში გაჩერება "
                             "აკრძალულია, სანამ გაგრძელება არსებობს.",
                  wraplength=520, foreground="#666").pack(anchor="w", pady=(4, 0))

    def _build_analysis_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="3. ბოტის ანალიზი")

        row = ttk.Frame(tab)
        row.pack(anchor="w")
        ttk.Label(row, text="დროის ლიმიტი (წმ):").pack(side="left")
        ttk.Combobox(row, values=TIME_CHOICES, textvariable=self.time_var,
                     width=5, state="readonly").pack(side="left", padx=(4, 12))
        ttk.Label(row, text="სიღრმის ჭერი:").pack(side="left")
        ttk.Combobox(row, values=DEPTH_CHOICES, textvariable=self.depth_var,
                     width=5, state="readonly").pack(side="left", padx=(4, 12))
        self.analyse_button = ttk.Button(row, text="იფიქრე", command=self.start_analysis)
        self.analyse_button.pack(side="left")
        self.stop_button = ttk.Button(row, text="შეჩერება", command=self.stop_work,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=(6, 0))

        self.analysis_text = tk.Text(tab, height=11, width=70, wrap="word",
                                     background="#f7f7f2", relief="flat")
        self.analysis_text.pack(fill="x", pady=(8, 6))

        compare = ttk.LabelFrame(tab, text="ყველა სვლის შედარება (ერთი სიღრმე, ერთი დრო)",
                                 padding=6)
        compare.pack(fill="both", expand=True)
        head = ttk.Frame(compare)
        head.pack(anchor="w")
        ttk.Label(head, text="მიზნობრივი სიღრმე:").pack(side="left")
        ttk.Combobox(head, values=DEPTH_CHOICES, textvariable=self.compare_depth_var,
                     width=5, state="readonly").pack(side="left", padx=(4, 12))
        ttk.Label(head, text="საერთო დრო (წმ):").pack(side="left")
        ttk.Combobox(head, values=TIME_CHOICES, textvariable=self.compare_time_var,
                     width=5, state="readonly").pack(side="left", padx=(4, 12))
        self.compare_button = ttk.Button(head, text="შეადარე ყველა სვლა",
                                         command=self.start_comparison)
        self.compare_button.pack(side="left")

        columns = ("move", "score", "depth", "exact", "proven", "nodes", "seconds",
                   "note")
        self.compare_table = ttk.Treeview(compare, columns=columns, height=8,
                                          show="headings")
        for column, title, width in (
            ("move", "სვლა", 90), ("score", "ქულა", 80),
            ("depth", "სიღრმე / მიზანი", 110), ("exact", "მნიშვნელობა ზუსტია", 120),
            ("proven", "დადასტურებული შედეგი", 190), ("nodes", "კვანძები", 80),
            ("seconds", "წმ", 55), ("note", "შენიშვნა", 150),
        ):
            self.compare_table.heading(column, text=title)
            self.compare_table.column(column, width=width, anchor="w")
        self.compare_table.pack(fill="both", expand=True, pady=(6, 0))
        ttk.Label(compare, text="სიღრმე ითვლება ნახევარსვლებით ამ პოზიციიდან, "
                                "საწყისი სვლის ჩათვლით (მთელი ჭამის ჯაჭვი = 1 სვლა). "
                                "„მნიშვნელობა ზუსტია“ = დასრულებული იტერაცია ამ "
                                "სიღრმეზე (არა alpha-beta საზღვარი). "
                                "„დადასტურებული“ = სრული გადალახვით მტკიცება.",
                  wraplength=760, foreground="#666").pack(anchor="w", pady=(4, 0))

    def _build_rules_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="4. წესები (JokerGame)")

        ttk.Label(tab, text="ერთადერთი წესების ნაკრები — JokerGame:",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.rules_table = tk.Text(tab, height=14, width=78, wrap="word",
                                   background="#f7f7f2", relief="flat")
        self.rules_table.pack(fill="x", pady=(4, 6))

        features = ttk.LabelFrame(tab, text="რა თვისებები ჩანს ამ პოზიციაში", padding=6)
        features.pack(fill="x")
        self.feature_text = tk.Text(features, height=7, width=78, wrap="word",
                                    background="#f4f8ff", relief="flat")
        self.feature_text.pack(fill="x")

        site = ttk.LabelFrame(tab, text="საიტზე ნანახი (ხელით ჩასაწერი, არ არის ავტომატური დადასტურება)",
                             padding=6)
        site.pack(fill="x", pady=(8, 0))
        ttk.Label(site, text="გზა, რომელიც საიტზე დაინახე:").pack(anchor="w")
        self.site_path_var = tk.StringVar()
        ttk.Entry(site, textvariable=self.site_path_var, width=70).pack(anchor="w")
        ttk.Label(site, text="შენიშვნა:").pack(anchor="w", pady=(4, 0))
        self.site_note_text = tk.Text(site, height=3, width=78, wrap="word")
        self.site_note_text.pack(anchor="w")

        buttons = ttk.Frame(tab)
        buttons.pack(anchor="w", pady=(8, 0))
        ttk.Button(buttons, text="შენიშვნის შენახვა პოზიციაში",
                   command=self.store_site_note).pack(side="left")
        ttk.Button(buttons, text="ლოგების საქაღალდე",
                   command=self.open_logs).pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------- drawing
    def _square_box(self, index: int):
        row, col = divmod(index, 8)
        x = MARGIN + col * CELL
        y = MARGIN + row * CELL
        return x, y

    def _draw_board(self) -> None:
        self.canvas.delete("all")
        for index in range(64):
            row, col = divmod(index, 8)
            x, y = self._square_box(index)
            playable = index in lab.PLAYABLE_SQUARES_SET
            fill = "#c8a97e" if playable else "#efe6d4"
            self.canvas.create_rectangle(x, y, x + CELL, y + CELL, fill=fill,
                                         outline="#b9ab92")
            # კოორდინატები: რიგი მარცხნივ, სვეტი ქვემოთ
            if playable:
                self.canvas.create_text(x + 4, y + 6, text=square_name(index),
                                        anchor="nw", fill="#8a7a60",
                                        font=("Segoe UI", 6))

        # სახელები კიდეებზე
        for col in range(8):
            x = MARGIN + col * CELL + CELL // 2
            self.canvas.create_text(x, MARGIN // 2, text="abcdefgh"[col],
                                    font=("Segoe UI", 9, "bold"), fill="#555")
            self.canvas.create_text(x, MARGIN + 8 * CELL + MARGIN // 2,
                                    text="abcdefgh"[col],
                                    font=("Segoe UI", 9, "bold"), fill="#555")
        for row in range(8):
            y = MARGIN + row * CELL + CELL // 2
            rank = 8 - row
            self.canvas.create_text(MARGIN // 2, y, text=str(rank),
                                    font=("Segoe UI", 9, "bold"), fill="#555")
            self.canvas.create_text(MARGIN + 8 * CELL + MARGIN // 2, y,
                                    text=str(rank),
                                    font=("Segoe UI", 9, "bold"), fill="#555")

        # მონიშვნები: ქვის არჩევა, სამიზნეები, მარშრუტი, აღებული ქვები
        for square in self.highlight_targets:
            x, y = self._square_box(square)
            self.canvas.create_rectangle(x + 3, y + 3, x + CELL - 3, y + CELL - 3,
                                         outline="#2a7", width=3)
        for square in self.highlight_victims:
            x, y = self._square_box(square)
            self.canvas.create_line(x + 8, y + 8, x + CELL - 8, y + CELL - 8,
                                    fill="#c33", width=3)
            self.canvas.create_line(x + CELL - 8, y + 8, x + 8, y + CELL - 8,
                                    fill="#c33", width=3)
        for hop in self.highlight_route:
            x, y = self._square_box(hop["to_index"])
            self.canvas.create_oval(x + CELL - 20, y + CELL - 20, x + CELL - 4,
                                    y + CELL - 4, fill="#2266cc", outline="")
            self.canvas.create_text(x + CELL - 12, y + CELL - 12, text=str(hop["index"]),
                                    fill="white", font=("Segoe UI", 7, "bold"))
        if self.selected_piece is not None:
            x, y = self._square_box(self.selected_piece)
            self.canvas.create_rectangle(x + 2, y + 2, x + CELL - 2, y + CELL - 2,
                                         outline="#c80", width=3)

        board = self.position.to_board()
        for index, piece in enumerate(board.cells):
            if piece == 0:
                continue
            x, y = self._square_box(index)
            colour = "#c0392b" if piece > 0 else "#2c3e50"
            inner = "#e74c3c" if piece > 0 else "#34495e"
            self.canvas.create_oval(x + 7, y + 7, x + CELL - 7, y + CELL - 7,
                                    fill=inner, outline=colour, width=2)
            self.canvas.create_text(x + CELL // 2, y + CELL // 2,
                                    text=GLYPH_UI[piece], fill="white",
                                    font=("Segoe UI", 11, "bold"))

        if self.playback is not None:
            hops = self.playback["move"]["hops"]
            step = self.playback["step"]
            landing = (self.playback["move"]["from_index"] if step == 0
                       else hops[step - 1]["to_index"])
            x, y = self._square_box(landing)
            self.canvas.create_oval(x + 5, y + 5, x + CELL - 5, y + CELL - 5,
                                    outline="#0a0", width=3)
            self.canvas.create_text(MARGIN + 4 * CELL, MARGIN - 14,
                                    text="ნაბიჯი %d/%d" % (step, len(hops)),
                                    fill="#063", font=("Segoe UI", 9, "bold"))

    # ---------------------------------------------------------------- events
    def _square_at(self, event) -> Optional[int]:
        col = (event.x - MARGIN) // CELL
        row = (event.y - MARGIN) // CELL
        if 0 <= row < 8 and 0 <= col < 8:
            return int(row * 8 + col)
        return None

    def _snapshot(self) -> None:
        # the side to move belongs to the snapshot: undoing a played move must
        # restore the turn as well, otherwise the board silently flips sides
        self.undo_stack.append((list(self.position.rows), self.position.turn))
        if len(self.undo_stack) > 200:
            self.undo_stack.pop(0)

    def on_click(self, event) -> None:
        index = self._square_at(event)
        if index is None:
            return
        if self.mode_var.get() == "edit":
            self._place(index)
        else:
            self._select_or_play(index)

    def on_right_click(self, event) -> None:
        index = self._square_at(event)
        if index is None:
            return
        self._snapshot()
        self._set_cell(index, ".")
        self.refresh_all()

    def _place(self, index: int) -> None:
        piece = self.tool.get()
        if piece != "erase" and index not in lab.PLAYABLE_SQUARES_SET:
            self.status.set("%s მსუბუქ უჯრაზე ვერ დადგება - ძრავა იქ ქვას ვერ ანძრევს"
                            % square_name(index))
            return
        self._snapshot()
        self._set_cell(index, piece if piece != "erase" else ".")
        self.refresh_all()

    def _set_cell(self, index: int, glyph: str) -> None:
        row, col = divmod(index, 8)
        rows = [list(row_text) for row_text in self.position.rows]
        rows[row][col] = "." if glyph == "erase" else glyph
        self.position.rows = ["".join(row_text) for row_text in rows]

    def _select_or_play(self, index: int) -> None:
        board = self.position.to_board()
        piece = board.cells[index]
        ours = piece > 0 if board.turn == RED else piece < 0
        if piece != 0 and ours:
            self.selected_piece = index
            self.playback = None
            self.highlight_targets = sorted(
                {row["to_index"] for row in self.move_rows
                 if row["from_index"] == index}
            )
            self.highlight_route = []
            self.highlight_victims = []
            forced = bool(self.move_rows) and all(row["is_capture"]
                                                 for row in self.move_rows)
            self.status.set("%s: %d ლეგალური დასასრული%s"
                            % (square_name(index), len(self.highlight_targets),
                               " (ჭამა სავალდებულოა)" if forced else ""))
            self._draw_board()
            return
        candidates = [row for row in self.move_rows
                      if row["from_index"] == self.selected_piece
                      and row["to_index"] == index]
        if not candidates:
            self.status.set("%s: ამ ქვისთვის ლეგალური დასასრული არ არის"
                            % square_name(index))
            return
        self._start_playback(candidates[0])

    def clear_board(self) -> None:
        self._snapshot()
        self.position.rows = ["." * 8 for _ in range(8)]
        self.refresh_all()

    def reset_board(self) -> None:
        self._snapshot()
        self.position = lab.LabPosition()
        self.turn_var.set("red")
        self.refresh_all()

    def load_preset(self) -> None:
        name = self.preset_box.get()
        preset = PRESETS.get(name)
        if preset is None:
            return
        self._snapshot()
        rows = preset["rows"] or lab.rows_of(lab.Board())
        self.position = lab.LabPosition(
            rows=list(rows),
            turn=RED if preset["turn"] == "red" else BLACK,
            title=name,
        )
        self.turn_var.set(preset["turn"])
        self.refresh_all()
        self.status.set("ჩატვირთულია: %s" % name)

    def undo(self) -> None:
        if not self.undo_stack:
            self.status.set("უკან დასაბრუნებელი აღარაფერია")
            return
        rows, turn = self.undo_stack.pop()
        self.position.rows = rows
        self.position.turn = turn
        self.turn_var.set("red" if turn == RED else "black")
        self.refresh_all()

    def save_position(self) -> None:
        self.store_site_note()
        path = filedialog.asksaveasfilename(
            title="პოზიციის შენახვა", initialdir=str(lab.POSITION_DIR),
            defaultextension=".json", filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        self.position.save(path)
        self.status.set("პოზიცია შენახულია: %s" % path)

    def open_position(self) -> None:
        path = filedialog.askopenfilename(
            title="პოზიციის გახსნა", initialdir=str(lab.POSITION_DIR),
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            position = lab.LabPosition.load(path)
        except (OSError, ValueError) as error:
            messagebox.showerror("ვერ გაიხსნა", str(error))
            return
        self._snapshot()
        self.position = position
        self.turn_var.set("red" if position.turn == RED else "black")
        self.site_path_var.set(position.site_path)
        self.site_note_text.delete("1.0", "end")
        self.site_note_text.insert("1.0", position.site_note)
        self.refresh_all()
        self.status.set("გახსნილია: %s" % path)

    # ------------------------------------------------------------ refreshers
    def on_turn_change(self) -> None:
        self.position.turn = RED if self.turn_var.get() == "red" else BLACK
        self.refresh_all()

    def on_mode_change(self) -> None:
        self.selected_piece = None
        self.highlight_targets = []
        self.highlight_route = []
        self.highlight_victims = []
        self.playback = None
        self._draw_board()
        self.status.set("რეჟიმი: " + ("რედაქტორი" if self.mode_var.get() == "edit"
                                      else "სვლები"))

    def _position_key(self) -> str:
        """Identity of the position on the board (rows + side to move + rules)."""
        return "|".join(list(self.position.rows) + [
            "red" if self.position.turn == RED else "black",
            self.position.rules,
        ])

    def invalidate_analysis(self, reason: str = "") -> None:
        """Forget any analysis on screen - it belongs to a different position.

        Without this the window kept showing the previous position's numbers (for
        example "5 გზა" after the position had 8), which looked like engine output
        for the new position.
        """
        self.analysis_text.delete("1.0", "end")
        for item in self.compare_table.get_children():
            self.compare_table.delete(item)
        message = "ანალიზი გაუქმებულია"
        if reason:
            message += ": " + reason
        self.analysis_text.insert("1.0", message + " — დააჭირე „იფიქრე“ ან "
                                             "„შეადარე ყველა სვლა“, რომ ახალი "
                                             "პოზიციის შედეგი მიიღო.")

    def refresh_all(self) -> None:
        board = self.position.to_board()
        self.position.turn = board.turn
        self.move_rows = lab.legal_moves(board)
        self.playback = None
        self.highlight_route = []
        self.highlight_victims = []
        if self.selected_piece is not None and not board.cells[self.selected_piece]:
            self.selected_piece = None
        key = self._position_key()
        if key != self.analysis_key:
            self.analysis_key = key
            self.invalidate_analysis("პოზიცია ან სვლის მხარე შეიცვალა")
        self._draw_board()
        self._fill_move_list()
        self._fill_diagnostics(board)
        self._fill_problems(board)
        self.rules_text.set(
            "JokerGame — %s; მოცილება: %s; ყველაზე გრძელი ჭამა სავალდებულო არ არის"
            % (lab.JOKERGAME.name, lab.JOKERGAME.removal)
        )
        self.refresh_rules(board)

    def _fill_move_list(self) -> None:
        self.move_list.delete(0, "end")
        for row in self.move_rows:
            route = "→".join(
                hop["to"] + ("x" + hop["victim"] if hop["victim"] else "")
                for hop in row["hops"]
            )
            tag = []
            if row["is_capture"]:
                tag.append("ჭამა %d" % row["captured_count"])
            if row["promotes"]:
                tag.append("დამკა")
            label = "%-12s %s%s" % (row["notation"], route,
                                    ("  [" + ", ".join(tag) + "]") if tag else "")
            self.move_list.insert("end", label)
        if not self.move_rows:
            self.move_list.insert("end", "— ლეგალური სვლა არ არის —")

    def _fill_diagnostics(self, board) -> None:
        diagnostics = lab.capture_diagnostics(board)
        lines = []
        if diagnostics["has_capture"]:
            lines.append(
                "ჭამა არსებობს (%d დასრულებული გზა). სვლა სავალდებულოა: მშვიდი სვლა "
                "ამ პოზიციაში საერთოდ არ არის ლეგალური."
                % diagnostics["path_count"]
            )
        else:
            lines.append(
                "ჭამა არ არსებობს (%d მშვიდი სვლა) - ამიტომ ჩვეულებრივი სვლები "
                "ლეგალურია." % diagnostics["quiet_moves"]
            )
        lines.append(
            "ქვების რაოდენობა ფილტრი არ არის: ყველა დასრულებული გზა ლეგალურია "
            "(ყველაზე გრძელი: %d ქვა)." % diagnostics["longest"]
        )
        if diagnostics["promotion_continues"]:
            lines.append("ჭამის დროს დამკად ქცევა: ჩართულია (joker) - დამკა აგრძელებს ჭამას.")
        self.diag_text.delete("1.0", "end")
        self.diag_text.insert("1.0", "\n".join(lines))
        self.raw_list.delete(0, "end")
        for path in diagnostics["paths"]:
            self.raw_list.insert(
                "end", "%-12s იღებს %d ქვას  %s"
                % (path["notation"], path["captured_count"],
                   " → ".join(hop["to"] for hop in path["hops"]))
            )

    def _fill_problems(self, board) -> None:
        problems = lab.position_problems(board)
        self.problem_box.delete("1.0", "end")
        if problems:
            self.problem_box.insert("1.0", "პოზიციის შენიშვნები:\n- "
                                    + "\n- ".join(problems))
        else:
            self.problem_box.insert("1.0", "პოზიცია ჩვეულებრივია (უცნაურობა არ ჩანს).")

    def refresh_rules(self, board=None) -> None:
        """Fill tab 4: the JokerGame rule text and what is visible in this position."""
        board = self.position.to_board() if board is None else board
        lines = ["JokerGame — ერთი საერთო კონფიგურაცია "
                 "(checkersbot/rules.py), რომელსაც იყენებს ძრავა, ბოტი, CLI და ეს დაფა:"]
        for item in lab.JOKERGAME_TEXT:
            lines.append("  • " + item)
        self.rules_table.delete("1.0", "end")
        self.rules_table.insert("1.0", "\n".join(lines))

        diagnostics = lab.capture_diagnostics(board)
        moves = self.move_rows
        captures = [row for row in moves if row["is_capture"]]
        feature_lines = [
            "ამ პოზიციაში:",
            "  ლეგალური სვლა: %d, მათგან ჭამა: %d, მშვიდი: %d"
            % (len(moves), len(captures), len(moves) - len(captures)),
            "  ჭამის დასრულებული გზა: %d (ყველაზე გრძელი იღებს %d ქვას; ეს "
            "სავალდებულო არ არის)"
            % (diagnostics["path_count"], diagnostics["longest"]),
            "  სხვადასხვა სიგრძის გზები ერთდროულად: %s"
            % ("დიახ" if len({row["captured_count"] for row in captures}) > 1
               else "არა"),
        ]
        if diagnostics["paths"]:
            for path in diagnostics["paths"]:
                feature_lines.append(
                    "    %-12s იღებს %d: %s → %s"
                    % (path["notation"], path["captured_count"],
                       " → ".join(hop["to"] for hop in path["hops"]),
                       ", ".join(path["captures"]) or "—")
                )
        for problem in lab.position_problems(board):
            feature_lines.append("  შენიშვნა: " + problem)
        self.feature_text.delete("1.0", "end")
        self.feature_text.insert("1.0", "\n".join(feature_lines))

    def store_site_note(self) -> None:
        self.position.site_path = self.site_path_var.get().strip()
        self.position.site_note = self.site_note_text.get("1.0", "end").strip()

    def open_logs(self) -> None:
        lab.LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(lab.LOG_DIR))                 # Windows
        except AttributeError:                              # pragma: no cover
            messagebox.showinfo("ლოგები", str(lab.LOG_DIR))

    # ------------------------------------------------------------- playback
    def on_move_selected(self, _event=None) -> None:
        selection = self.move_list.curselection()
        if not selection or not self.move_rows:
            return
        index = selection[0]
        if index >= len(self.move_rows):
            return
        self._start_playback(self.move_rows[index])

    def _start_playback(self, row: Dict[str, Any]) -> None:
        self.playback = {"move": row, "step": 0}
        self.selected_piece = row["from_index"]
        self.highlight_targets = []
        self.highlight_route = []
        self.highlight_victims = []
        self._show_move_details(row)
        self._draw_board()
        self.status.set("%s: ნაბიჯი 0/%d (ჯერ არაფერი შეცვლილა დაფაზე)"
                        % (row["notation"], len(row["hops"])))

    def _show_move_details(self, row: Dict[str, Any]) -> None:
        lines = ["სვლა: %s" % row["notation"],
                 "დასაწყისი: %s → დასასრული: %s" % (row["from"], row["to"])]
        if row["captures"]:
            lines.append("აღებული ქვები (%d): %s"
                         % (row["captured_count"], ", ".join(row["captures"])))
        else:
            lines.append("ჭამა არ არის")
        lines.append("დამკა: %s" % ("კი" if row["promotes"] else "არა"))
        lines.append("გზა:")
        for hop in row["hops"]:
            lines.append("  %d. %s → %s%s" % (
                hop["index"], hop["from"], hop["to"],
                ("   (იღებს %s)" % hop["victim"]) if hop["victim"] else ""))
        same = [group for group in lab.moves_with_same_ends(self.position.to_board())
                if any(item["key"] == row["key"] for item in group)]
        if same:
            lines.append("ამავე დასაწყისი/დასასრულის სხვა გზები:")
            for group in same:
                for item in group:
                    if item["key"] == row["key"]:
                        continue
                    lines.append("  • %s  (%s)" % (
                        item["notation"],
                        ", ".join("%s→%s" % (hop["from"], hop["to"])
                                  for hop in item["hops"])))
        self.move_details.delete("1.0", "end")
        self.move_details.insert("1.0", "\n".join(lines))

    def step_playback(self, delta: int) -> None:
        if self.playback is None:
            self.status.set("ჯერ აირჩიე სვლა სიიდან")
            return
        hops = self.playback["move"]["hops"]
        step = max(0, min(len(hops), self.playback["step"] + delta))
        self.playback["step"] = step
        self.highlight_route = hops[:step]
        self.highlight_victims = [hop["victim_index"] for hop in hops[:step]
                                  if hop["victim_index"] is not None]
        self.highlight_targets = []
        self._draw_board()
        if step == 0:
            self.status.set("ნაბიჯი 0/%d — დაფა არ შეცვლილა" % len(hops))
        else:
            hop = hops[step - 1]
            self.status.set("ნაბიჯი %d/%d: %s → %s%s (ნაბიჯი არ ითვლება სრულ სვლად)"
                            % (step, len(hops), hop["from"], hop["to"],
                               (" იღებს " + hop["victim"]) if hop["victim"] else ""))

    def reset_playback(self) -> None:
        if self.playback is None:
            return
        self.playback["step"] = 0
        self.highlight_route = []
        self.highlight_victims = []
        self._draw_board()
        self.status.set("დაბრუნდა სვლის დასაწყისში")

    def apply_playback_move(self) -> None:
        if self.playback is None:
            self.status.set("ჯერ აირჩიე სვლა")
            return
        row = self.playback["move"]
        board = self.position.to_board()
        chosen = next((move for move in board.generate_moves()
                       if (move.from_square, move.to_square, move.captured,
                           move.promotes) == row["key"]), None)
        if chosen is None:
            self.status.set("ეს სვლა აღარ არის ლეგალური - განაახლე სია")
            return
        self._snapshot()
        board.make_move(chosen)
        self.position = lab.LabPosition.from_board(
            board, title=self.position.title,
            site_path=self.position.site_path, site_note=self.position.site_note,
        )
        self.turn_var.set("red" if board.turn == RED else "black")
        self.selected_piece = None
        self.refresh_all()
        self.status.set("სრული სვლა შესრულდა დაფაზე: %s (ერთი ნაბიჯი უკან)"
                        % row["notation"])

    # ------------------------------------------------------------- analysis
    def _seconds(self, variable: tk.StringVar, fallback: float) -> float:
        try:
            return max(0.1, float(variable.get()))
        except (TypeError, ValueError):
            return fallback

    def _depth(self, variable: tk.StringVar, fallback: int) -> int:
        try:
            return max(1, int(float(variable.get())))
        except (TypeError, ValueError):
            return fallback

    def start_analysis(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        self.store_site_note()
        seconds = self._seconds(self.time_var, 3.0)
        depth = self._depth(self.depth_var, 10)
        position = lab.LabPosition.from_dict(self.position.to_dict())
        key = self._position_key()
        self.stop_event = threading.Event()
        self._set_busy(True, "ანალიზი მიდის... (%.1f წმ)" % seconds)

        def work() -> None:
            try:
                analysis = lab.analyze_position(
                    position, seconds=seconds, depth_cap=depth,
                    should_stop=self.stop_event.is_set,
                )
                analysis["_log"] = lab.write_analysis_log(position, analysis)
            except Exception as error:                    # noqa: BLE001
                analysis = {"error": "%s: %s" % (type(error).__name__, error)}
            self.results.put({"kind": "analysis", "data": analysis, "key": key})

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.after(120, self._poll_results)

    def start_comparison(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        self.store_site_note()
        depth = self._depth(self.compare_depth_var, 8)
        budget = self._seconds(self.compare_time_var, 8.0)
        position = lab.LabPosition.from_dict(self.position.to_dict())
        key = self._position_key()
        self.stop_event = threading.Event()
        self._set_busy(True, "ყველა სვლის შედარება მიდის... (სიღრმე %d, %.1f წმ)"
                       % (depth, budget))

        def work() -> None:
            try:
                comparison = lab.compare_root_moves(
                    position, depth=depth, total_seconds=budget,
                    should_stop=self.stop_event.is_set,
                )
                analysis = lab.analyze_position(
                    position, seconds=min(2.0, budget), depth_cap=depth,
                    should_stop=self.stop_event.is_set,
                )
                comparison["_log"] = lab.write_analysis_log(
                    position, analysis, comparison=comparison
                )
            except Exception as error:                    # noqa: BLE001
                comparison = {"error": "%s: %s" % (type(error).__name__, error)}
            self.results.put({"kind": "comparison", "data": comparison, "key": key})

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.after(120, self._poll_results)

    def stop_work(self) -> None:
        self.stop_event.set()
        self.status.set("შეჩერების მოთხოვნა გაიგზავნა - ძრავა ბოლო დასრულებულ "
                        "იტერაციას დააბრუნებს")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        state = "disabled" if busy else "normal"
        self.analyse_button.configure(state=state)
        self.compare_button.configure(state=state)
        self.stop_button.configure(state="normal" if busy else "disabled")
        if message:
            self.status.set(message)

    def _poll_results(self) -> None:
        try:
            item = self.results.get_nowait()
        except queue.Empty:
            if self.worker is not None and self.worker.is_alive():
                self.after(120, self._poll_results)
            else:
                self._set_busy(False)
            return
        self._set_busy(False)
        if item.get("key") is not None and item["key"] != self.analysis_key:
            self.status.set("მოძველებული შედეგი იგნორირებულია: პოზიცია შეიცვალა "
                            "ძიების დროს")
            self.invalidate_analysis("პოზიცია შეიცვალა ძიების დროს")
            return
        if item["kind"] == "analysis":
            self._show_analysis(item["data"])
        else:
            self._show_comparison(item["data"])

    def _show_analysis(self, analysis: Dict[str, Any]) -> None:
        lines: List[str] = []
        if analysis.get("error"):
            lines.append("შეცდომა: %s" % analysis["error"])
        elif not analysis.get("move"):
            lines.append("ლეგალური სვლა არ არის: %s" % (analysis.get("note") or ""))
        else:
            move = analysis["move"]
            lines.append("არჩეული სვლა: %s   (გზა: %s)"
                         % (move["notation"],
                            " → ".join(hop["to"] for hop in move["hops"])))
            score = analysis.get("score")
            if score is None:
                lines.append("ქულა: არ შეფასდა (ძიებამ ვერც ერთი იტერაცია ვერ "
                             "დაასრულა) — ეს არ არის 0")
            else:
                lines.append("ქულა: %s   (%s)" % (score, analysis["score_sign"]))
            lines.append("სიღრმე: დასრულდა %s / მიზანი %s — ერთეული: ნახევარსვლები "
                         "საწყისი სვლის ჩათვლით (ჯაჭვი = 1 სვლა); კვანძები: %s | "
                         "დრო: %.2f წმ"
                         % (analysis["depth"], analysis.get("target_depth"),
                            analysis["nodes"], analysis["seconds"]))
            lines.append("მნიშვნელობა ზუსტია ამ სიღრმეზე: %s — ეს არ ნიშნავს შედეგის "
                         "მტკიცებას"
                         % ("დიახ" if analysis.get("value_exact") else "არა"))
            if analysis.get("mate_in") is not None:
                lines.append("ძიებაში ნანახი მატი: %d ნახევარსვლაში (ამ ძიების "
                             "სიღრმეში; დამოუკიდებელი მტკიცება ქვემოთ)"
                             % analysis["mate_in"])
            lines.append("დადასტურებული შედეგი (სრული გადალახვა, დამოუკიდებელი "
                         "შემოწმებით): " + lab.proof_summary(analysis.get("proof")))
            if analysis.get("continuation"):
                lines.append("სავარაუდო გაგრძელება (%s): %s"
                             % (analysis.get("continuation_source"),
                                " ".join(analysis["continuation"])))
            lines.append("სტატიკური შეფასება (ცალკე ძიების ქულისგან): %s = %s"
                         % (analysis.get("static"), analysis.get("components")))
            if analysis.get("_log"):
                lines.append("ჩანაწერი: %s" % analysis["_log"]["text"])
        self.analysis_text.delete("1.0", "end")
        self.analysis_text.insert("1.0", "\n".join(lines))
        if analysis.get("move"):
            self.status.set("ანალიზი დასრულდა: %s" % analysis["move"]["notation"])

    def _show_comparison(self, comparison: Dict[str, Any]) -> None:
        for item in self.compare_table.get_children():
            self.compare_table.delete(item)
        if comparison.get("error"):
            self.status.set("შედარება ვერ შესრულდა: %s" % comparison["error"])
            return
        for row in comparison.get("rows", []):
            note = row["note"] or ""
            proof = row.get("proof") or {}
            proven = (lab.proof_summary(proof) if row.get("proven")
                      else "არ არის დადასტურებული")
            self.compare_table.insert("", "end", values=(
                row["move"]["notation"],
                "—" if row["score"] is None else row["score"],
                "%s / %s" % (row["depth"], row.get("requested_depth", "—")),
                "დიახ" if row["value_exact"] else "არა",
                proven,
                row["nodes"],
                "%.2f" % row["seconds"],
                note,
            ))
        exact = len(comparison.get("exact") or [])
        total = len(comparison.get("rows") or [])
        by_value = sum(1 for row in comparison.get("rows") or [] if row["value_exact"])
        proven = sum(1 for row in comparison.get("rows") or [] if row["proven"])
        message = ("შედარება: %d სვლა | მნიშვნელობა ზუსტი %d/%d | მოთხოვნილი სიღრმე "
                   "დასრულდა %d/%d | დადასტურებული %d/%d"
                   % (total, by_value, total, exact, total, proven, total))
        if comparison.get("best_by_search"):
            message += " | შეფასებით საუკეთესო: %s" % comparison["best_by_search"]
        if comparison.get("best_proven"):
            message += (" | დადასტურებული: %s (%d ნახევარსვლაში)"
                        % (comparison["best_proven"],
                           next(row["proof"]["plies"] for row in comparison["rows"]
                                if row["move"]["notation"] == comparison["best_proven"]
                                and row.get("proven"))))
        if comparison.get("_log"):
            message += " | ჩანაწერი: %s" % comparison["_log"]["text"]
        self.status.set(message)


def resolve_preset(name: str) -> Optional[str]:
    """The preset whose name matches ``name`` exactly or as a substring."""
    if name in PRESETS:
        return name
    lowered = name.lower()
    return next((key for key in PRESETS if lowered in key.lower()), None)


def main(argv: Optional[List[str]] = None) -> int:
    """Open the board; ``--preset NAME`` loads a preset position straight away."""
    if getattr(sys, "frozen", False):
        # inside CheckersBot.exe: keep the logs/positions next to the exe
        try:
            os.chdir(Path(sys.executable).resolve().parent)
        except OSError:                            # pragma: no cover - read-only dir
            pass
    parser = argparse.ArgumentParser(
        description="შაშის სატესტო დაფა — JokerGame წესებით.")
    parser.add_argument("--preset", metavar="NAME",
                        help="გახსნის მზა პოზიციას სახელით (იხ. --list-presets)")
    parser.add_argument("--list-presets", action="store_true",
                        help="დაბეჭდე მზა პოზიციების სახელები და გამორთე")
    parser.add_argument("--analyse", type=float, metavar="SECONDS",
                        help="გახსნის შემდეგ დაუყოვნებლივ გაუშვი ანალიზი")
    args = parser.parse_args(argv)

    if args.list_presets:
        for name in PRESETS:
            print(name)
        return 0

    app = BoardLab()
    if args.preset:
        name = resolve_preset(args.preset)
        if name is None:
            app.destroy()
            print("უცნობი პრესეტი: %s" % args.preset, file=sys.stderr)
            return 2
        app.preset_box.set(name)
        app.load_preset()
    if args.analyse:
        app.time_var.set(str(args.analyse))
        app.after(300, app.start_analysis)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
