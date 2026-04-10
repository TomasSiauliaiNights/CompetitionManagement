"""
Robotics Tournament Manager v4
- PostgreSQL as primary real-time data store
- Google Sheets as optional background sync backup
- Excel import for initial robot loading
- ESP32 serial for hardware timer
- OBS overlay + second screen display
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial, serial.tools.list_ports
import threading, time, json, os, copy, traceback

from db import DB, GSheetSync, parse_time

SD = os.path.dirname(os.path.abspath(__file__))
OD = os.path.join(SD, "overlay")
CF = os.path.join(SD, "tournament_config.json")
TEAL = "#00f5d4"

def fmt_ms(ms):
    if ms is None: return "--:--.---"
    ms = max(0, int(ms))
    m, s, ml = ms // 60000, (ms % 60000) // 1000, ms % 1000
    return f"{m}:{s:02d}.{ml:03d}" if m else f"{s}.{ml:03d}"

DEF = {
    "mode": "postgres",
    "pg_host": "localhost", "pg_port": "5432", "pg_db": "tournament",
    "pg_user": "postgres", "pg_pass": "",
    "excel_path": "", "gs_creds": "", "gs_id": "",
    "gs_sync_interval": 30,
    "lf": {"sheet": "Line following", "start": 7, "cn": "A", "cm": "B", "ct": "D", "nt": 5},
    "fs": {"sheet": "Ugnies Seses", "start": 4, "cn": "A", "cm": "B", "ct": "D", "nt": 5},
    "fr": {"sheet": "Folkrace", "ln": "P", "lm": "Q", "ls": 6,
           "gc": "B", "gn": "C", "gr": ["D","E","F"], "gt": "G"},
    "fr_cd": 180, "fs_cd": 180, "max_trials": 5,
}

def load_cfg():
    if os.path.exists(CF):
        try:
            with open(CF) as f: d = json.load(f)
            c = copy.deepcopy(DEF)
            for k, v in d.items():
                if k in c and isinstance(c[k], dict) and isinstance(v, dict): c[k].update(v)
                else: c[k] = v
            return c
        except: pass
    return copy.deepcopy(DEF)

def save_cfg(c):
    with open(CF, "w") as f: json.dump(c, f, indent=2)


class Ser:
    def __init__(self, cb):
        self.ser = None; self.cb = cb; self.connected = False; self._run = False
    def ports(self): return [(p.device, p.description) for p in serial.tools.list_ports.comports()]
    def connect(self, port):
        self.ser = serial.Serial(port, 115200, timeout=0.1)
        time.sleep(2); self.ser.reset_input_buffer()
        self.connected = True; self._run = True
        threading.Thread(target=self._lp, daemon=True).start()
        self.send("PING")
    def disconnect(self):
        self._run = False; self.connected = False
        if self.ser and self.ser.is_open: self.ser.close()
    def send(self, c):
        if self.ser and self.ser.is_open: self.ser.write((c + "\n").encode())
    def _lp(self):
        while self._run:
            try:
                if self.ser and self.ser.is_open and self.ser.in_waiting:
                    l = self.ser.readline().decode("utf-8", errors="ignore").strip()
                    if l: self.cb(l)
                else: time.sleep(0.005)
            except: time.sleep(0.01)


class Overlay:
    def __init__(self):
        os.makedirs(OD, exist_ok=True)
        self.path = os.path.join(OD, "overlay_data.js")
        self.d = {"category": "", "robot_number": "", "robot_name": "",
            "timer_text": "--:--.---", "timer_state": "idle",
            "best_time": "", "best_label": "Best", "status_text": "",
            "points": [], "folkrace_group": ""}
        self._w()
        print(f"[Overlay] Path: {self.path}")
    def update(self, **kw):
        self.d.update(kw); self._w()
    def _w(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write("var OVERLAY_DATA=" + json.dumps(self.d, ensure_ascii=False) + ";")
        except Exception as e:
            print(f"[Overlay ERROR] {e}")


class Screen2(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Tournament Display"); self.configure(bg="#0a0a0f"); self.geometry("1280x720")
        self.cat_l = tk.Label(self, font=("Consolas", 24, "bold"), fg=TEAL, bg="#0a0a0f")
        self.cat_l.pack(pady=(30, 5))
        f = tk.Frame(self, bg="#0a0a0f"); f.pack(pady=5)
        self.num_l = tk.Label(f, font=("Consolas", 32, "bold"), fg="#ff6b6b", bg="#0a0a0f")
        self.num_l.pack(side=tk.LEFT, padx=10)
        self.name_l = tk.Label(f, font=("Consolas", 36, "bold"), fg="#fff", bg="#0a0a0f")
        self.name_l.pack(side=tk.LEFT, padx=10)
        self.timer_l = tk.Label(self, text="--:--.---", font=("Consolas", 120, "bold"), fg=TEAL, bg="#0a0a0f")
        self.timer_l.pack(pady=10)
        self.best_l = tk.Label(self, font=("Consolas", 28), fg="#ffd93d", bg="#0a0a0f")
        self.best_l.pack(pady=3)
        self.stat_l = tk.Label(self, font=("Consolas", 24, "bold"), fg=TEAL, bg="#0a0a0f")
        self.stat_l.pack(pady=3)
        self.pts_f = tk.Frame(self, bg="#0a0a0f"); self.pts_f.pack(pady=5)
        self._pw = []
    def upd(self, **kw):
        self.cat_l.config(text=kw.get("cat", ""))
        n = kw.get("num", ""); self.num_l.config(text=f"#{n}" if n else "")
        self.name_l.config(text=kw.get("name", ""))
        self.timer_l.config(text=kw.get("tt", "--:--.---"), fg=kw.get("tc", TEAL))
        b = kw.get("best", ""); self.best_l.config(text=f"Best: {b}" if b else "")
        self.stat_l.config(text=kw.get("st", ""), fg=kw.get("sc", TEAL))
        for w in self._pw: w.destroy()
        self._pw.clear()
        for p in (kw.get("pts") or []):
            l = tk.Label(self.pts_f,
                text=f"#{p['num']}  {p['name']}  —  {p['pts']} pts",
                font=("Consolas", 20), fg="#ffd93d", bg="#0a0a0f")
            l.pack(); self._pw.append(l)
    def toggle_fs(self): self.attributes("-fullscreen", not self.attributes("-fullscreen"))


class SettingsWin(tk.Toplevel):
    def __init__(self, master, cfg, on_save):
        super().__init__(master)
        self.title("⚙ Settings"); self.geometry("750x750"); self.configure(bg="#12121a")
        self.cfg = copy.deepcopy(cfg); self.on_save = on_save; self._v = {}

        canvas = tk.Canvas(self, bg="#12121a", highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        sf = tk.Frame(canvas, bg="#12121a")
        sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        L = {"bg": "#12121a", "fg": "#e0e0e0", "font": ("Segoe UI", 11)}
        H = {"bg": "#12121a", "fg": TEAL, "font": ("Consolas", 13, "bold")}
        E = {"bg": "#252540", "fg": "#e0e0e0", "font": ("Segoe UI", 11),
             "insertbackground": "#e0e0e0", "relief": "flat", "bd": 2}

        # PostgreSQL
        tk.Label(sf, text="DATABASE (PostgreSQL)", **H).pack(anchor="w", padx=15, pady=(12,4))
        self._row(sf, "Host:", "pg_host", 20, L, E, default=cfg.get("pg_host","localhost"))
        self._row(sf, "Port:", "pg_port", 8, L, E, default=cfg.get("pg_port","5432"))
        self._row(sf, "Database:", "pg_db", 20, L, E, default=cfg.get("pg_db","tournament"))
        self._row(sf, "User:", "pg_user", 20, L, E, default=cfg.get("pg_user","postgres"))
        self._row(sf, "Password:", "pg_pass", 20, L, E, default=cfg.get("pg_pass",""))

        # Excel import
        tk.Label(sf, text="EXCEL IMPORT (one-time robot load)", **H).pack(anchor="w", padx=15, pady=(12,4))
        self._row(sf, "Excel File:", "excel_path", 45, L, E, browse="excel", default=cfg.get("excel_path",""))

        # Import settings
        for title, key in [("LINE FOLLOWING", "lf"), ("FIRE SISTER", "fs")]:
            tk.Label(sf, text=f"  {title} import columns", bg="#12121a", fg="#888", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(5,2))
            c = cfg.get(key, {})
            for fld, lab in [("sheet","Sheet"),("start","Start Row"),("cn","# Col"),("cm","Name Col"),("ct","Trials Col"),("nt","Max Trials")]:
                self._row(sf, f"    {lab}:", f"{key}.{fld}", 16, L, E, default=c.get(fld,""))

        tk.Label(sf, text="  FOLKRACE import columns", bg="#12121a", fg="#888", font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(5,2))
        fr = cfg.get("fr", {})
        for fld, lab in [("sheet","Sheet"),("ln","List # Col"),("lm","List Name Col"),("ls","List Start"),
                          ("gc","Group # Col"),("gn","Group Name Col"),("gt","Total Col")]:
            self._row(sf, f"    {lab}:", f"fr.{fld}", 16, L, E, default=fr.get(fld,""))
        self._row(sf, "    Round Cols:", "fr._rc", 16, L, E,
                  default=",".join(fr.get("gr", ["D","E","F"])))

        # Google Sheets backup
        tk.Label(sf, text="GOOGLE SHEETS BACKUP (optional)", **H).pack(anchor="w", padx=15, pady=(12,4))
        self._row(sf, "Creds JSON:", "gs_creds", 45, L, E, browse="json", default=cfg.get("gs_creds",""))
        self._row(sf, "Sheet URL/ID:", "gs_id", 50, L, E, default=cfg.get("gs_id",""))
        self._row(sf, "Sync interval (s):", "gs_sync_interval", 8, L, E, default=cfg.get("gs_sync_interval",30))
        tk.Label(sf, text="  Must be a native Google Sheet (not uploaded .xlsx)\n"
                          "  File → Save as Google Sheets if needed",
            bg="#12121a", fg="#555", font=("Segoe UI", 9), justify="left").pack(anchor="w", padx=25)

        # Timers
        tk.Label(sf, text="TIMERS", **H).pack(anchor="w", padx=15, pady=(10,4))
        self._row(sf, "Folkrace CD (s):", "fr_cd", 8, L, E, default=cfg.get("fr_cd", 180))
        self._row(sf, "Fire Sister CD (s):", "fs_cd", 8, L, E, default=cfg.get("fs_cd", 180))
        self._row(sf, "Max Trials:", "max_trials", 8, L, E, default=cfg.get("max_trials", 5))

        tk.Button(sf, text="💾  SAVE", command=self._save,
            bg="#1a4a1a", fg="#00ff88", font=("Segoe UI", 13, "bold"),
            relief="flat", padx=20, pady=8).pack(pady=15)

    def _row(self, parent, label, key, width, L, E, default="", browse=None):
        r = tk.Frame(parent, bg="#12121a"); r.pack(fill="x", padx=25, pady=2)
        tk.Label(r, text=label, **L).pack(side="left")
        var = tk.StringVar(value=str(default))
        tk.Entry(r, textvariable=var, width=width, **E).pack(side="left", padx=5)
        if browse:
            ft = [("Excel","*.xlsx")] if browse == "excel" else [("JSON","*.json")]
            tk.Button(r, text="…", command=lambda: self._br(var, ft),
                bg="#252540", fg="#e0e0e0", relief="flat", width=3).pack(side="left")
        self._v[key] = var

    def _br(self, var, ft):
        self.lift()
        p = filedialog.askopenfilename(filetypes=ft, parent=self)
        if p: var.set(p)
        self.lift()

    def _save(self):
        c = self.cfg
        for key, var in self._v.items():
            val = var.get()
            try: val = int(val)
            except: pass
            if "." in key:
                sec, fld = key.split(".", 1)
                if fld == "_rc":
                    c.setdefault(sec, {})["gr"] = [x.strip() for x in str(val).split(",")]
                    continue
                c.setdefault(sec, {})[fld] = val
            else:
                c[key] = val
        save_cfg(c); self.on_save(c)
        messagebox.showinfo("OK", "Settings saved.", parent=self); self.destroy()


# ═══════════════════ MAIN APP ═══════════════════

class App:
    def __init__(self, root):
        self.root = root
        root.title("Robotics Tournament Manager v4")
        root.geometry("1100x850"); root.configure(bg="#12121a"); root.minsize(1000, 750)

        self.cfg = load_cfg()
        self.ms = 0; self.final = None; self.running = False; self.ready = False
        self.sw_t0 = None; self.sw_on = False; self.cd_ms = 0; self.is_cd = False
        self.cat = "lf"; self.rnum = ""; self.rname = ""; self.best_str = ""
        self.robot_id = None  # current robot DB id

        self.ov = Overlay()
        self.ser = Ser(lambda m: root.after(0, self._ser_msg, m))
        self.db = DB()
        self.gs_sync = None
        self.s2 = None

        self._styles(); self._build()
        root.bind("<space>", self._space)
        root.bind("<F11>", lambda e: self.s2 and self.s2.winfo_exists() and self.s2.toggle_fs())
        self._tick()

    def _styles(self):
        s = ttk.Style(); s.theme_use("clam")
        BG, PNL, BTN = "#12121a", "#1a1a2e", "#252540"
        s.configure("TFrame", background=BG); s.configure("P.TFrame", background=PNL)
        s.configure("TLabel", background=BG, foreground="#e0e0e0", font=("Segoe UI", 11))
        s.configure("P.TLabel", background=PNL, foreground="#e0e0e0", font=("Segoe UI", 11))
        s.configure("Timer.TLabel", background=PNL, foreground=TEAL, font=("Consolas", 52, "bold"))
        s.configure("TButton", background=BTN, foreground="#e0e0e0", font=("Segoe UI", 11, "bold"), padding=(10,5))
        s.map("TButton", background=[("active", "#333355")])
        for n, bg, fg in [("Go","#1a4a1a","#00ff88"),("Stop","#4a1a1a","#ff6b6b"),
                           ("Cfm","#1a1a4a","#8888ff"),("Dnf","#4a3a1a","#ffd93d")]:
            s.configure(f"{n}.TButton", background=bg, foreground=fg)
            s.map(f"{n}.TButton", background=[("active", bg)])
        s.configure("TNotebook", background=BG)
        s.configure("TNotebook.Tab", background=BTN, foreground="#e0e0e0",
                    font=("Segoe UI", 12, "bold"), padding=(18, 7))
        s.map("TNotebook.Tab", background=[("selected", PNL)], foreground=[("selected", TEAL)])

    def _build(self):
        top = ttk.Frame(self.root); top.pack(fill=tk.X, padx=10, pady=(8,4))
        ttk.Label(top, text="Port:").pack(side=tk.LEFT)
        self.port_v = tk.StringVar()
        self.port_cb = ttk.Combobox(top, textvariable=self.port_v, width=18, state="readonly")
        self.port_cb.pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="⟳", width=3, command=self._ref_ports).pack(side=tk.LEFT, padx=2)
        self.conn_btn = ttk.Button(top, text="Connect", command=self._toggle_ser)
        self.conn_btn.pack(side=tk.LEFT, padx=3)
        self.conn_lbl = ttk.Label(top, text="● Disconnected", foreground="#ff6b6b")
        self.conn_lbl.pack(side=tk.LEFT, padx=(5, 10))

        ttk.Button(top, text="⚙", command=lambda: SettingsWin(self.root, self.cfg, self._on_cfg),
                   width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="🗄 Connect DB", command=self._connect_db).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="📥 Import Excel", command=self._import_excel).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="☁ Start GSheets Sync", command=self._start_gs_sync).pack(side=tk.LEFT, padx=3)
        self.db_lbl = ttk.Label(top, text="DB: Not connected", foreground="#666")
        self.db_lbl.pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="🖥", command=self._toggle_s2, width=3).pack(side=tk.RIGHT, padx=3)

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._build_lf(); self._build_fr(); self._build_fs()

        sf = ttk.Frame(self.root); sf.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.sbar = ttk.Label(sf, text="Space=start/stop | F11=fullscreen 2nd screen", foreground="#666")
        self.sbar.pack(side=tk.LEFT)
        self._ref_ports()

    def _build_lf(self):
        t = ttk.Frame(self.nb, style="P.TFrame"); self.nb.add(t, text="  LINE FOLLOWING  ")
        ef = ttk.Frame(t, style="P.TFrame"); ef.pack(fill=tk.X, padx=15, pady=(12,5))
        ttk.Label(ef, text="Robot #:", style="P.TLabel").pack(side=tk.LEFT)
        self.lf_entry = ttk.Entry(ef, width=8, font=("Consolas", 14))
        self.lf_entry.pack(side=tk.LEFT, padx=5)
        self.lf_entry.bind("<Return>", lambda e: self._lf_lookup())
        ttk.Button(ef, text="Lookup", command=self._lf_lookup).pack(side=tk.LEFT, padx=5)
        self.lf_name_l = ttk.Label(ef, style="P.TLabel", font=("Segoe UI", 15, "bold"), foreground="#fff")
        self.lf_name_l.pack(side=tk.LEFT, padx=15)
        self.lf_best_l = ttk.Label(ef, style="P.TLabel", foreground="#ffd93d", font=("Consolas", 13))
        self.lf_best_l.pack(side=tk.LEFT)

        tf = ttk.Frame(t, style="P.TFrame"); tf.pack(fill=tk.X, padx=15, pady=5)
        self.lf_timer = ttk.Label(tf, text="--:--.---", style="Timer.TLabel"); self.lf_timer.pack(pady=8)
        self.lf_status = ttk.Label(tf, text="IDLE", style="P.TLabel", font=("Consolas", 15, "bold"), foreground="#666")
        self.lf_status.pack()

        bf = ttk.Frame(t, style="P.TFrame"); bf.pack(fill=tk.X, padx=15, pady=8)
        ttk.Button(bf, text="▶  READY", style="Go.TButton", command=self._lf_ready).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="■  STOP", style="Stop.TButton", command=self._lf_stop).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="↺  RESET", command=self._lf_reset).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="✓  CONFIRM", style="Cfm.TButton", command=self._lf_confirm).pack(side=tk.RIGHT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="✗  DNF", style="Dnf.TButton", command=self._lf_dnf).pack(side=tk.RIGHT, padx=4, ipadx=8, ipady=4)

        ttk.Label(t, text="Trials", style="P.TLabel", font=("Consolas", 11, "bold"), foreground=TEAL).pack(anchor="w", padx=15, pady=(8,2))
        self.lf_trials_f = tk.Frame(t, bg="#1a1a2e"); self.lf_trials_f.pack(fill=tk.X, padx=15, pady=2)

        ttk.Label(t, text="Scoreboard", style="P.TLabel", font=("Consolas", 11, "bold"), foreground=TEAL).pack(anchor="w", padx=15, pady=(8,2))
        bf2 = tk.Frame(t, bg="#15152a"); bf2.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0,10))
        c = tk.Canvas(bf2, bg="#15152a", highlightthickness=0)
        sb = ttk.Scrollbar(bf2, orient="vertical", command=c.yview)
        self.lf_board = tk.Frame(c, bg="#15152a")
        self.lf_board.bind("<Configure>", lambda e: c.configure(scrollregion=c.bbox("all")))
        c.create_window((0,0), window=self.lf_board, anchor="nw")
        c.configure(yscrollcommand=sb.set)
        c.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")

    def _build_fr(self):
        t = ttk.Frame(self.nb, style="P.TFrame"); self.nb.add(t, text="  FOLKRACE  ")
        top = ttk.Frame(t, style="P.TFrame"); top.pack(fill=tk.X, padx=15, pady=(12,5))
        ttk.Label(top, text="Group:", style="P.TLabel").pack(side=tk.LEFT)
        self.fr_gvar = tk.StringVar()
        self.fr_gcb = ttk.Combobox(top, textvariable=self.fr_gvar, width=28, state="readonly")
        self.fr_gcb.pack(side=tk.LEFT, padx=5)
        self.fr_gcb.bind("<<ComboboxSelected>>", self._fr_sel_group)
        ttk.Button(top, text="⟳ Reload", command=self._fr_reload).pack(side=tk.LEFT, padx=5)
        ttk.Label(top, text="Timer:", style="P.TLabel").pack(side=tk.LEFT, padx=(20,3))
        self.fr_cd_var = tk.StringVar(value=str(self.cfg.get("fr_cd", 180)))
        ttk.Entry(top, textvariable=self.fr_cd_var, width=5, font=("Consolas", 12)).pack(side=tk.LEFT)
        ttk.Label(top, text="s", style="P.TLabel").pack(side=tk.LEFT)

        tf = ttk.Frame(t, style="P.TFrame"); tf.pack(fill=tk.X, padx=15, pady=5)
        self.fr_timer = ttk.Label(tf, text="3:00.000", style="Timer.TLabel"); self.fr_timer.pack(pady=5)
        self.fr_status = ttk.Label(tf, text="IDLE", style="P.TLabel", font=("Consolas", 15, "bold"), foreground="#666")
        self.fr_status.pack()

        bf = ttk.Frame(t, style="P.TFrame"); bf.pack(fill=tk.X, padx=15, pady=5)
        ttk.Button(bf, text="▶  START (3-2-1)", style="Go.TButton", command=self._fr_start).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="■  STOP", style="Stop.TButton", command=self._fr_stop).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="↺  RESET", command=self._fr_reset).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="✓  SAVE SCORES", style="Cfm.TButton", command=self._fr_save).pack(side=tk.RIGHT, padx=4, ipadx=8, ipady=4)

        self.fr_robots_f = tk.Frame(t, bg="#15152a")
        self.fr_robots_f.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5,10))
        self.fr_wlist = []; self.fr_groups = []; self.fr_gi = -1

    def _build_fs(self):
        t = ttk.Frame(self.nb, style="P.TFrame"); self.nb.add(t, text="  FIRE SISTER  ")
        ef = ttk.Frame(t, style="P.TFrame"); ef.pack(fill=tk.X, padx=15, pady=(12,5))
        ttk.Label(ef, text="Robot #:", style="P.TLabel").pack(side=tk.LEFT)
        self.fs_entry = ttk.Entry(ef, width=8, font=("Consolas", 14))
        self.fs_entry.pack(side=tk.LEFT, padx=5)
        self.fs_entry.bind("<Return>", lambda e: self._fs_lookup())
        ttk.Button(ef, text="Lookup", command=self._fs_lookup).pack(side=tk.LEFT, padx=5)
        self.fs_name_l = ttk.Label(ef, style="P.TLabel", font=("Segoe UI", 15, "bold"), foreground="#fff")
        self.fs_name_l.pack(side=tk.LEFT, padx=15)
        self.fs_best_l = ttk.Label(ef, style="P.TLabel", foreground="#ffd93d", font=("Consolas", 13))
        self.fs_best_l.pack(side=tk.LEFT)

        cf = ttk.Frame(t, style="P.TFrame"); cf.pack(fill=tk.X, padx=15, pady=3)
        ttk.Label(cf, text="Countdown:", style="P.TLabel").pack(side=tk.LEFT)
        self.fs_cd_var = tk.StringVar(value=str(self.cfg.get("fs_cd", 180)))
        ttk.Entry(cf, textvariable=self.fs_cd_var, width=5, font=("Consolas", 12)).pack(side=tk.LEFT, padx=5)
        ttk.Label(cf, text="s     Points:", style="P.TLabel", font=("Segoe UI", 13)).pack(side=tk.LEFT, padx=(15,3))
        self.fs_pts_e = ttk.Entry(cf, width=8, font=("Consolas", 14))
        self.fs_pts_e.pack(side=tk.LEFT, padx=5)

        tf = ttk.Frame(t, style="P.TFrame"); tf.pack(fill=tk.X, padx=15, pady=5)
        self.fs_timer = ttk.Label(tf, text="3:00.000", style="Timer.TLabel"); self.fs_timer.pack(pady=8)
        self.fs_status = ttk.Label(tf, text="IDLE", style="P.TLabel", font=("Consolas", 15, "bold"), foreground="#666")
        self.fs_status.pack()

        bf = ttk.Frame(t, style="P.TFrame"); bf.pack(fill=tk.X, padx=15, pady=5)
        ttk.Button(bf, text="▶  START (3-2-1)", style="Go.TButton", command=self._fs_start).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="■  STOP", style="Stop.TButton", command=self._fs_stop).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="↺  RESET", command=self._fs_reset).pack(side=tk.LEFT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="✓  CONFIRM POINTS", style="Cfm.TButton", command=self._fs_confirm).pack(side=tk.RIGHT, padx=4, ipadx=8, ipady=4)
        ttk.Button(bf, text="✗  DNF", style="Dnf.TButton", command=self._fs_dnf).pack(side=tk.RIGHT, padx=4, ipadx=8, ipady=4)

        ttk.Label(t, text="Trials", style="P.TLabel", font=("Consolas", 11, "bold"), foreground=TEAL).pack(anchor="w", padx=15, pady=(8,2))
        self.fs_trials_f = tk.Frame(t, bg="#1a1a2e"); self.fs_trials_f.pack(fill=tk.X, padx=15, pady=2)

        ttk.Label(t, text="Scoreboard", style="P.TLabel", font=("Consolas", 11, "bold"), foreground=TEAL).pack(anchor="w", padx=15, pady=(8,2))
        bf2 = tk.Frame(t, bg="#15152a"); bf2.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0,10))
        c = tk.Canvas(bf2, bg="#15152a", highlightthickness=0)
        sb = ttk.Scrollbar(bf2, orient="vertical", command=c.yview)
        self.fs_board = tk.Frame(c, bg="#15152a")
        self.fs_board.bind("<Configure>", lambda e: c.configure(scrollregion=c.bbox("all")))
        c.create_window((0,0), window=self.fs_board, anchor="nw")
        c.configure(yscrollcommand=sb.set)
        c.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")

    # ─── Serial ───
    def _ref_ports(self):
        ps = self.ser.ports(); self.port_cb["values"] = [f"{p[0]} - {p[1]}" for p in ps]
        if ps: self.port_cb.current(0)
    def _toggle_ser(self):
        if self.ser.connected:
            self.ser.disconnect(); self.conn_btn.config(text="Connect"); self.conn_lbl.config(text="● Disconnected", foreground="#ff6b6b")
        else:
            ps = self.port_v.get()
            if not ps: return
            try:
                self.ser.connect(ps.split(" - ")[0])
                self.conn_btn.config(text="Disconnect"); self.conn_lbl.config(text="● Connected", foreground=TEAL)
            except Exception as e: messagebox.showerror("Error", str(e))
    def _ser_msg(self, m):
        if m.startswith("TIME:"): 
            try: self.ms = int(m[5:]); self.running = True
            except: pass
        elif m.startswith("FINAL:"):
            try: self.final = int(m[6:]); self.ms = self.final; self.running = False; self.sw_on = False; self._sst("STOPPED — "+fmt_ms(self.final), TEAL)
            except: pass
        elif m == "TRIGGERED": self.running = True; self.ready = False; self.sw_t0 = time.perf_counter(); self.sw_on = True; self._sst("RUNNING", TEAL)
        elif m == "READY_ACK": self.ready = True; self._sst("READY — Waiting for sensor", "#ffd93d")
        elif m == "COUNTDOWN_END": self.running = False; self.sw_on = False; self._sst("TIME'S UP!", "#ff6b6b")
        elif m == "PONG": self.sbar.config(text="ESP32 OK")
        elif m.startswith("STATUS:"): self._sst(m[7:], "#ffd93d")
    def _sst(self, t, c):
        w = {"lf": self.lf_status, "fr": self.fr_status, "fs": self.fs_status}.get(self.cat)
        if w: w.config(text=t, foreground=c)
        self.ov.update(status_text=t)

    # ─── DB / Import / GSheets ───
    def _on_cfg(self, c): self.cfg = c
    def _connect_db(self):
        try:
            self.db.connect(
                host=self.cfg.get("pg_host", "localhost"),
                port=int(self.cfg.get("pg_port", 5432)),
                dbname=self.cfg.get("pg_db", "tournament"),
                user=self.cfg.get("pg_user", "postgres"),
                password=self.cfg.get("pg_pass", ""))
            self.db_lbl.config(text="DB ✓", foreground=TEAL)
            self.sbar.config(text="Database connected!")
            self._refresh_board("lf"); self._refresh_board("fs"); self._fr_reload()
        except Exception as e:
            messagebox.showerror("DB Error", f"{e}\n\n{traceback.format_exc()}")

    def _import_excel(self):
        if not self.db.ok: messagebox.showwarning("", "Connect to database first."); return
        p = self.cfg.get("excel_path", "")
        if not p:
            p = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
            if not p: return
        try:
            result = self.db.import_from_excel(p, self.cfg)
            self.sbar.config(text=f"Imported: {result['robots']} robots, {result['trials']} trials, {result['groups']} groups")
            self._refresh_board("lf"); self._refresh_board("fs"); self._fr_reload()
            messagebox.showinfo("Import", f"Done!\n{result['robots']} robots\n{result['trials']} trials\n{result['groups']} folkrace groups")
        except Exception as e:
            messagebox.showerror("Import Error", f"{e}\n\n{traceback.format_exc()}")

    def _start_gs_sync(self):
        cr = self.cfg.get("gs_creds", ""); sid = self.cfg.get("gs_id", "")
        if not cr or not sid:
            messagebox.showwarning("", "Set Google Sheets credentials in Settings first."); return
        if not self.db.ok: messagebox.showwarning("", "Connect to database first."); return
        interval = int(self.cfg.get("gs_sync_interval", 30))
        self.gs_sync = GSheetSync(self.db, cr, sid, interval)
        if self.gs_sync.error:
            messagebox.showerror("GSheets", f"Sync failed: {self.gs_sync.error}")
        else:
            self.sbar.config(text=f"GSheets sync started (every {interval}s)")

    # ─── Helpers ───
    def _nt(self): return int(self.cfg.get("max_trials", 5))

    def _refresh_trials(self, cat, robot_id):
        frame = self.lf_trials_f if cat == "lf" else self.fs_trials_f
        for w in frame.winfo_children(): w.destroy()
        if not self.db.ok or not robot_id: return
        vals = self.db.get_trial_values(robot_id, self._nt())
        for i, v in enumerate(vals):
            txt = str(v) if v is not None and str(v).strip() != "" else "—"
            fg = "#ff6b6b" if v is not None and str(v).strip().upper() == "DNF" else "#e0e0e0"
            tk.Label(frame, text=f"T{i+1}: {txt}", bg="#1a1a2e", fg=fg,
                     font=("Consolas", 12), padx=10, pady=3).pack(side=tk.LEFT, padx=2)

    def _refresh_board(self, cat):
        if not self.db.ok: return
        is_pts = (cat == "fs")
        board = self.db.scoreboard_fs() if is_pts else self.db.scoreboard_lf()
        inner = self.lf_board if cat == "lf" else self.fs_board
        for w in inner.winfo_children(): w.destroy()
        nt = self._nt()
        hdrs = ["#", "Name"] + [f"T{i+1}" for i in range(nt)] + ["Best"]
        for ci, h in enumerate(hdrs):
            tk.Label(inner, text=h, bg="#252540", fg=TEAL, font=("Consolas", 10, "bold"),
                     padx=6, pady=3).grid(row=0, column=ci, sticky="ew", padx=1, pady=1)
        for ri, r in enumerate(board):
            bg = "#15152a" if ri % 2 == 0 else "#1a1a30"
            hl = (r["num"] == self.rnum) and (cat == self.cat)
            if hl: bg = "#2a2a1a"
            tk.Label(inner, text=r["num"], bg=bg, fg="#ff6b6b" if hl else "#e0e0e0",
                     font=("Consolas", 10, "bold"), padx=6, pady=2).grid(row=ri+1, column=0, sticky="ew", padx=1)
            tk.Label(inner, text=r["name"], bg=bg, fg="#fff" if hl else "#e0e0e0",
                     font=("Segoe UI", 10), padx=6, pady=2).grid(row=ri+1, column=1, sticky="ew", padx=1)
            for ti, v in enumerate(r["trials"]):
                txt = str(v) if v is not None and str(v).strip() != "" else ""
                tk.Label(inner, text=txt, bg=bg, fg="#e0e0e0", font=("Consolas", 10),
                         padx=4, pady=2, anchor="center").grid(row=ri+1, column=2+ti, sticky="ew", padx=1)
            best = r["best"]
            bt = f"{best} pts" if is_pts and best is not None else (fmt_ms(best) if best and not is_pts else "")
            tk.Label(inner, text=bt, bg=bg, fg="#ffd93d", font=("Consolas", 10, "bold"),
                     padx=4, pady=2, anchor="center").grid(row=ri+1, column=2+nt, sticky="ew", padx=1)

    # ─── LINE FOLLOWING ───
    def _lf_lookup(self):
        n = self.lf_entry.get().strip()
        if not n: return
        self.rnum = n; self.rname = f"Robot {n}"; self.robot_id = None
        if self.db.ok:
            r = self.db.find_robot(n, "line_following")
            if r: self.rname = r["name"] or f"Robot {n}"; self.robot_id = r["id"]
            self._refresh_trials("lf", self.robot_id); self._refresh_board("lf")
        bs = ""
        if self.robot_id:
            b = self.db.best_time(self.robot_id)
            if b: bs = fmt_ms(b)
        self.best_str = bs
        self.lf_name_l.config(text=self.rname)
        self.lf_best_l.config(text=f"Best: {bs}" if bs else "Best: No record")
        self.ov.update(category="LINE FOLLOWING", robot_number=n, robot_name=self.rname, best_time=bs)

    def _lf_ready(self):
        self.ser.send("READY"); self.ms = 0; self.final = None
        self.running = False; self.ready = True; self.is_cd = False
        self._sst("READY — Waiting for sensor", "#ffd93d")
        self.ov.update(timer_text="0.000", timer_state="ready")

    def _lf_stop(self):
        self.ser.send("STOP")
        if self.sw_on:
            e = int((time.perf_counter() - self.sw_t0) * 1000)
            self.ms = e; self.final = e; self.sw_on = False; self.running = False
            self._sst(f"STOPPED — {fmt_ms(e)}", TEAL)

    def _lf_reset(self):
        self.ser.send("RESET"); self.ms = 0; self.final = None
        self.running = False; self.ready = False; self.sw_on = False
        self._sst("IDLE", "#666"); self.lf_timer.config(text="--:--.---")
        self.ov.update(timer_text="--:--.---", timer_state="idle")

    def _lf_confirm(self):
        if self.final is None: messagebox.showwarning("", "No time."); return
        if not self.robot_id: messagebox.showwarning("", "Lookup a robot first."); return
        ts = fmt_ms(self.final)
        tn = self.db.write_trial_time(self.robot_id, ts, self._nt())
        if tn:
            self.sbar.config(text=f"✓ T{tn}: {ts}")
            self._refresh_trials("lf", self.robot_id); self._refresh_board("lf")
            b = self.db.best_time(self.robot_id)
            bs = fmt_ms(b) if b else ""
            self.best_str = bs; self.lf_best_l.config(text=f"Best: {bs}")
            self.ov.update(best_time=bs, timer_state="finished")
        else:
            messagebox.showwarning("", "All trials full.")

    def _lf_dnf(self):
        if not self.robot_id: return
        self.ser.send("DNF"); self.running = False; self.sw_on = False; self.final = None
        self._sst("DNF", "#ff6b6b")
        self.db.write_trial_time(self.robot_id, "DNF", self._nt())
        self._refresh_trials("lf", self.robot_id); self._refresh_board("lf")
        self.ov.update(timer_text="DNF", timer_state="dnf")

    # ─── FOLKRACE ───
    def _fr_reload(self):
        if not self.db.ok: return
        self.fr_groups = self.db.get_folkrace_groups_full()
        self.fr_gcb["values"] = [g["name"] for g in self.fr_groups]
        if self.fr_groups: self.fr_gcb.current(0); self._fr_sel_group()

    def _fr_sel_group(self, e=None):
        idx = self.fr_gcb.current()
        if idx < 0 or idx >= len(self.fr_groups): return
        self.fr_gi = idx; g = self.fr_groups[idx]
        for w in self.fr_wlist: w.destroy()
        self.fr_wlist.clear()
        hdr = tk.Frame(self.fr_robots_f, bg="#252540"); hdr.pack(fill=tk.X, pady=(0,2))
        for txt, w in [("#", 6), ("Robot", 20), ("R1", 6), ("R2", 6), ("R3", 6), ("Total", 7)]:
            tk.Label(hdr, text=txt, bg="#252540", fg=TEAL, font=("Consolas", 10, "bold"),
                     width=w, anchor="center").pack(side=tk.LEFT, padx=2, pady=4)
        self.fr_wlist.append(hdr)
        for ri, ent in enumerate(g["robots"]):
            bg = "#15152a" if ri % 2 == 0 else "#1a1a30"
            row = tk.Frame(self.fr_robots_f, bg=bg); row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=ent["number"], bg=bg, fg="#ff6b6b",
                     font=("Consolas", 11, "bold"), width=6, anchor="center").pack(side=tk.LEFT, padx=2, pady=3)
            tk.Label(row, text=ent["name"], bg=bg, fg="#fff",
                     font=("Segoe UI", 11), width=20, anchor="w").pack(side=tk.LEFT, padx=2, pady=3)
            rvars = []
            for val in [ent["r1"], ent["r2"], ent["r3"]]:
                v = tk.StringVar(value=str(val))
                tk.Entry(row, textvariable=v, width=6, font=("Consolas", 11),
                         bg="#252540", fg="#e0e0e0", insertbackground="#e0e0e0",
                         relief="flat", justify="center").pack(side=tk.LEFT, padx=2, pady=3)
                rvars.append(v)
            tv = tk.StringVar(value=str(ent["total"]))
            tk.Label(row, textvariable=tv, bg=bg, fg="#ffd93d",
                     font=("Consolas", 11, "bold"), width=7, anchor="center").pack(side=tk.LEFT, padx=2, pady=3)
            ent["_rv"] = rvars; ent["_tv"] = tv
            self.fr_wlist.append(row)
        self.ov.update(category="FOLKRACE", folkrace_group=g["name"],
            robot_number="", robot_name="", best_time="",
            points=[{"num": e["number"], "name": e["name"], "number": e["number"], "pts": str(e["total"])}
                    for e in g["robots"]])

    def _fr_start(self):
        try: s = int(self.fr_cd_var.get())
        except: s = 180
        self.cd_ms = s * 1000; self.is_cd = True
        self.ser.send(f"COUNTDOWN:{s}"); time.sleep(0.1); self.ser.send("BEEP_START")
        self.ms = s * 1000; self.sw_t0 = time.perf_counter() + 3
        self.sw_on = True; self.running = True
        self._sst("3... 2... 1... GO!", "#ffd93d")
        self.ov.update(timer_text=fmt_ms(s*1000), timer_state="countdown")

    def _fr_stop(self):
        self.ser.send("STOP"); self.running = False; self.sw_on = False
        self._sst("STOPPED", "#ff6b6b")

    def _fr_reset(self):
        self.ser.send("RESET"); self.running = False; self.sw_on = False
        try: s = int(self.fr_cd_var.get())
        except: s = 180
        self.fr_timer.config(text=fmt_ms(s*1000)); self._sst("IDLE", "#666")

    def _fr_save(self):
        if self.fr_gi < 0: return
        g = self.fr_groups[self.fr_gi]
        pts_out = []
        for ent in g["robots"]:
            rvars = ent.get("_rv", [])
            try: r1 = int(rvars[0].get()) if len(rvars) > 0 else 0
            except: r1 = 0
            try: r2 = int(rvars[1].get()) if len(rvars) > 1 else 0
            except: r2 = 0
            try: r3 = int(rvars[2].get()) if len(rvars) > 2 else 0
            except: r3 = 0
            total = r1 + r2 + r3
            if self.db.ok:
                self.db.update_folkrace_entry(ent["id"], r1, r2, r3)
            ent["_tv"].set(str(total))
            pts_out.append({"num": ent["number"], "name": ent["name"], "number": ent["number"], "pts": str(total)})
        self.ov.update(points=pts_out, status_text="SCORES SAVED")
        self.sbar.config(text=f"✓ Scores saved for {g['name']}")

    # ─── FIRE SISTER ───
    def _fs_lookup(self):
        n = self.fs_entry.get().strip()
        if not n: return
        self.rnum = n; self.rname = f"Robot {n}"; self.robot_id = None
        if self.db.ok:
            r = self.db.find_robot(n, "fire_sister")
            if r: self.rname = r["name"] or f"Robot {n}"; self.robot_id = r["id"]
            self._refresh_trials("fs", self.robot_id); self._refresh_board("fs")
        bs = ""
        if self.robot_id:
            b = self.db.best_points(self.robot_id)
            if b is not None: bs = f"{b} pts"
        self.best_str = bs
        self.fs_name_l.config(text=self.rname)
        self.fs_best_l.config(text=f"Best: {bs}" if bs else "Best: No record")
        self.ov.update(category="FIRE SISTER", robot_number=n, robot_name=self.rname, best_time=bs)

    def _fs_start(self):
        try: s = int(self.fs_cd_var.get())
        except: s = 180
        self.cd_ms = s * 1000; self.is_cd = True
        self.ser.send(f"COUNTDOWN:{s}"); time.sleep(0.1); self.ser.send("BEEP_START")
        self.ms = s * 1000; self.sw_t0 = time.perf_counter() + 3
        self.sw_on = True; self.running = True
        self._sst("3... 2... 1... GO!", "#ffd93d")
        self.ov.update(timer_text=fmt_ms(s*1000), timer_state="countdown")

    def _fs_stop(self):
        self.ser.send("STOP"); self.running = False; self.sw_on = False
        self._sst("STOPPED", "#ff6b6b")

    def _fs_reset(self):
        self.ser.send("RESET"); self.running = False; self.sw_on = False
        try: s = int(self.fs_cd_var.get())
        except: s = 180
        self.fs_timer.config(text=fmt_ms(s*1000)); self._sst("IDLE", "#666")
        self.ov.update(timer_text=fmt_ms(s*1000), timer_state="idle")

    def _fs_confirm(self):
        if not self.robot_id: messagebox.showwarning("", "Lookup a robot first."); return
        pts = self.fs_pts_e.get().strip()
        if not pts: messagebox.showwarning("", "Enter points."); return
        try: pv = int(pts)
        except: messagebox.showwarning("", "Points must be a number."); return
        tn = self.db.write_trial_points(self.robot_id, pv, self._nt())
        if tn:
            self.sbar.config(text=f"✓ T{tn}: {pv} pts")
            self._refresh_trials("fs", self.robot_id); self._refresh_board("fs")
            b = self.db.best_points(self.robot_id)
            bs = f"{b} pts" if b is not None else ""
            self.best_str = bs; self.fs_best_l.config(text=f"Best: {bs}")
            self.ov.update(best_time=bs, timer_state="finished",
                points=[{"num": self.rnum, "name": self.rname, "number": self.rnum, "pts": str(pv)}])
        else:
            messagebox.showwarning("", "All trials full.")

    def _fs_dnf(self):
        if not self.robot_id: return
        self.ser.send("DNF"); self.running = False; self.sw_on = False
        self._sst("DNF", "#ff6b6b")
        self.db.write_trial_points(self.robot_id, "DNF", self._nt())
        self._refresh_trials("fs", self.robot_id); self._refresh_board("fs")
        self.ov.update(timer_text="DNF", timer_state="dnf")

    # ─── Spacebar ───
    def _space(self, e=None):
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry)): return
        if self.cat == "lf":
            if self.ready and not self.running:
                self.ser.send("START"); self.sw_t0 = time.perf_counter()
                self.sw_on = True; self.running = True; self.ready = False; self.is_cd = False
                self._sst("RUNNING (manual)", TEAL)
            elif self.running: self._lf_stop()
        elif self.cat == "fr":
            if not self.running: self._fr_start()
            else: self._fr_stop()
        elif self.cat == "fs":
            if not self.running: self._fs_start()
            else: self._fs_stop()

    # ─── Tab / Screen2 ───
    def _tab_changed(self, e=None):
        idx = self.nb.index(self.nb.select())
        cats = ["lf", "fr", "fs"]; names = ["LINE FOLLOWING", "FOLKRACE", "FIRE SISTER"]
        self.cat = cats[idx]; self.running = False; self.sw_on = False
        self.ov.update(category=names[idx], timer_text="--:--.---", timer_state="idle",
                       status_text="", points=[], robot_number="", robot_name="",
                       best_time="", folkrace_group="")

    def _toggle_s2(self):
        if self.s2 and self.s2.winfo_exists(): self.s2.destroy(); self.s2 = None
        else: self.s2 = Screen2(self.root)

    # ─── Tick ───
    def _tick(self):
        if self.sw_on and self.sw_t0:
            el = time.perf_counter() - self.sw_t0
            if el >= 0:
                if self.is_cd:
                    rem = self.cd_ms - int(el * 1000)
                    if rem <= 0: rem = 0; self.sw_on = False; self.running = False; self._sst("TIME'S UP!", "#ff6b6b")
                    if not self.ser.connected: self.ms = rem
                else:
                    if not self.ser.connected: self.ms = int(el * 1000)

        show = self.final if (self.final is not None and not self.running) else self.ms
        dt = fmt_ms(show); tc = TEAL; ts = "running" if self.running else "idle"

        if self.cat == "lf":
            self.lf_timer.config(text=dt)
            if self.final is not None and not self.running: ts = "finished"
        elif self.cat == "fr":
            self.fr_timer.config(text=dt); ts = "countdown"
            if show <= 10000 and self.running: tc = "#ff6b6b"; ts = "danger"
        elif self.cat == "fs":
            self.fs_timer.config(text=dt); ts = "countdown"
            if show <= 10000 and self.running: tc = "#ff6b6b"; ts = "danger"
            if not self.running and show == 0: ts = "finished"

        self.ov.update(timer_text=dt, timer_state=ts)

        if self.s2 and self.s2.winfo_exists():
            pts = None
            if self.cat == "fr" and 0 <= self.fr_gi < len(self.fr_groups):
                g = self.fr_groups[self.fr_gi]
                pts = [{"num": e["number"], "name": e["name"],
                        "pts": e["_tv"].get() if "_tv" in e else str(e["total"])}
                       for e in g["robots"]]
            stat = ""; w = {"lf": self.lf_status, "fr": self.fr_status, "fs": self.fs_status}.get(self.cat)
            if w: stat = w.cget("text")
            cat_name = {"lf": "LINE FOLLOWING", "fr": "FOLKRACE", "fs": "FIRE SISTER"}.get(self.cat, "")
            self.s2.upd(cat=cat_name, num=self.rnum, name=self.rname, tt=dt, tc=tc,
                        best=self.best_str, st=stat, sc=tc, pts=pts)

        self.root.after(33, self._tick)


if __name__ == "__main__":
    root = tk.Tk(); App(root); root.mainloop()
