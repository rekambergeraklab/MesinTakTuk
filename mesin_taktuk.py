#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import numpy as np
import jack
import threading
import time
import os

NUM_CHANNELS = 32
AUTOSAVE_FILE = ".metro_lampung_autosave.json"

class MetroEngine:
    def __init__(self):
        try:
            self.client = jack.Client("METRO_LAMPUNG")
        except jack.JackError:
            messagebox.showerror("JACK Error", "JACK server is not running!\nPlease start QjackCtl or your DAW first.")
            exit(1)

        self.out_ports = [self.client.outports.register(f'out_{i+1}') for i in range(NUM_CHANNELS)]
        
        self.active = [False] * NUM_CHANNELS
        self.ticks = [np.zeros(0, dtype=np.float32)] * NUM_CHANNELS
        self.tick_lengths = [0] * NUM_CHANNELS
        self.samples_per_beat = [0.0] * NUM_CHANNELS
        self.frame_counter = 0
        self.channel_offsets = [0] * NUM_CHANNELS 
        
        self.channel_vols = [1.0] * NUM_CHANNELS
        self.group_vols = [1.0, 1.0, 1.0, 1.0] 
        self.group_mutes = [False, False, False, False] 
        
        self.master_vol = 1.0
        self.master_mute = False 

        self.fading_out = [False] * NUM_CHANNELS
        self.fade_samples_left = [0] * NUM_CHANNELS
        self.fade_total_samples = 1000 
        
        self.running = True 

        self.client.set_process_callback(self.process)
        self.client.activate()

    def process(self, frames):
        if not self.running:
            for port in self.out_ports:
                port.get_array().fill(0.0)
            return

        t_idx = self.frame_counter + np.arange(frames)

        for i, port in enumerate(self.out_ports):
            buf = port.get_array()
            buf.fill(0.0)

            if self.active[i] and self.tick_lengths[i] > 0:
                local_t_idx = t_idx - self.channel_offsets[i]
                
                beat_phase = np.fmod(local_t_idx, self.samples_per_beat[i])
                tick_idx = beat_phase.astype(int)
                
                mask = (local_t_idx >= 0) & (tick_idx >= 0) & (tick_idx < self.tick_lengths[i])
                
                temp_buf = np.zeros(frames, dtype=np.float32)
                temp_buf[mask] = self.ticks[i][tick_idx[mask]]
                
                if self.fading_out[i]:
                    left = self.fade_samples_left[i]
                    if left > 0:
                        ramp_len = min(frames, left)
                        start_vol = left / self.fade_total_samples
                        end_vol = (left - ramp_len) / self.fade_total_samples
                        
                        ramp = np.linspace(start_vol, end_vol, ramp_len, endpoint=False, dtype=np.float32)
                        temp_buf[:ramp_len] *= ramp
                        temp_buf[ramp_len:] = 0.0 
                        
                        self.fade_samples_left[i] -= ramp_len
                    else:
                        temp_buf[:] = 0.0
                        self.active[i] = False
                        self.fading_out[i] = False

                group_idx = i // 8
                g_vol = 0.0 if self.group_mutes[group_idx] else self.group_vols[group_idx]
                m_vol = 0.0 if self.master_mute else self.master_vol
                
                buf[:] = temp_buf * self.channel_vols[i] * g_vol * m_vol

        self.frame_counter += frames

    def update_channel_params(self, i, data):
        try:
            bpm = float(data['bpm'])
            freq = float(data['freq'])
            dur_ms = float(data['dur'])
            att_pct = float(data['att'])
            dec_pct = float(data['dec'])
        except ValueError:
            return

        if bpm <= 0 or dur_ms <= 0:
            return

        sr = self.client.samplerate
        new_spb = (60.0 / bpm) * sr

        dur_samples = int((dur_ms / 1000.0) * sr)
        if dur_samples <= 0:
            return

        t = np.arange(dur_samples) / sr
        wave = np.sin(2 * np.pi * freq * t)

        env = np.ones(dur_samples)
        att_samples = int((att_pct / 100.0) * dur_samples)
        dec_samples = int((dec_pct / 100.0) * dur_samples)

        if att_samples > 0:
            env[:att_samples] = np.linspace(0, 1, att_samples)
        if dec_samples > 0:
            env[-dec_samples:] = np.linspace(1, 0, dec_samples)

        new_ticks = (wave * env).astype(np.float32)

        self.tick_lengths[i] = 0 
        self.ticks[i] = new_ticks
        self.samples_per_beat[i] = new_spb
        self.tick_lengths[i] = dur_samples

    def start_channel(self, i, data, sync_mode=False):
        if not data['name'].strip():
            self.active[i] = False
            return

        if not sync_mode:
            self.channel_offsets[i] = self.frame_counter

        self.fading_out[i] = False
        self.fade_samples_left[i] = 0

        self.update_channel_params(i, data)
        self.active[i] = True

    def stop_channel(self, i):
        if self.active[i] and not self.fading_out[i]:
            self.fading_out[i] = True
            sr = self.client.samplerate
            self.fade_total_samples = int(sr * 0.03) 
            self.fade_samples_left[i] = self.fade_total_samples

    def stop_all(self):
        for i in range(NUM_CHANNELS):
            self.stop_channel(i)

    def close(self):
        self.running = False
        self.client.deactivate()
        self.client.close()


class MetronomeRow:
    def __init__(self, parent, global_index, app):
        self.app = app
        self.index = global_index 
        self.is_led_on = False 
        
        default_name = f"Ch_{self.index + 1}" if self.index < 4 else ""
        
        self.name_var = tk.StringVar(value=default_name)
        self.bpm_var = tk.StringVar(value="120")
        self.freq_var = tk.StringVar(value=str(440 + ((self.index % 8) * 110)))
        self.dur_var = tk.StringVar(value="100")
        self.att_var = tk.StringVar(value="1")
        self.dec_var = tk.StringVar(value="20")
        self.vol_var = tk.StringVar(value="100")
        
        self.bpm_var.trace_add("write", self.on_param_change)
        self.freq_var.trace_add("write", self.on_param_change)
        self.dur_var.trace_add("write", self.on_param_change)
        self.att_var.trace_add("write", self.on_param_change)
        self.dec_var.trace_add("write", self.on_param_change)
        self.vol_var.trace_add("write", self.on_vol_change)

        row_pos = (self.index % 8) + 1 

        ttk.Entry(parent, textvariable=self.name_var, width=14).grid(row=row_pos, column=0, padx=8, pady=5)
        ttk.Spinbox(parent, from_=1, to=999, textvariable=self.bpm_var, width=7).grid(row=row_pos, column=1, padx=8, pady=5)
        ttk.Spinbox(parent, from_=20, to=10000, textvariable=self.freq_var, width=7).grid(row=row_pos, column=2, padx=8, pady=5)
        ttk.Spinbox(parent, from_=1, to=5000, textvariable=self.dur_var, width=7).grid(row=row_pos, column=3, padx=8, pady=5)
        ttk.Spinbox(parent, from_=0, to=100, textvariable=self.att_var, width=7).grid(row=row_pos, column=4, padx=8, pady=5)
        ttk.Spinbox(parent, from_=0, to=100, textvariable=self.dec_var, width=7).grid(row=row_pos, column=5, padx=8, pady=5)
        ttk.Spinbox(parent, from_=0, to=100, textvariable=self.vol_var, width=6).grid(row=row_pos, column=6, padx=8, pady=5)

        self.btn = ttk.Button(parent, text="Start", command=self.toggle, width=8)
        self.btn.grid(row=row_pos, column=7, padx=10, pady=5)

        self.led_canvas = tk.Canvas(parent, width=22, height=22, highlightthickness=0)
        self.led_canvas.grid(row=row_pos, column=8, padx=10, pady=5)
        self.led_id = self.led_canvas.create_oval(2, 2, 20, 20, fill="gray30", outline="black", width=1.5)

    def set_led(self, state):
        if state != self.is_led_on:
            self.is_led_on = state
            color = "lime green" if state else "gray30"
            self.led_canvas.itemconfig(self.led_id, fill=color)

    def on_param_change(self, *args):
        if self.app.engine.active[self.index]:
            self.app.engine.update_channel_params(self.index, self.get_data())

    def on_vol_change(self, *args):
        try:
            val = float(self.vol_var.get())
            self.app.engine.channel_vols[self.index] = max(0.0, min(100.0, val)) / 100.0
        except ValueError:
            pass

    def get_data(self):
        return {
            "name": self.name_var.get(),
            "bpm": self.bpm_var.get(),
            "freq": self.freq_var.get(),
            "dur": self.dur_var.get(),
            "att": self.att_var.get(),
            "dec": self.dec_var.get(),
            "vol": self.vol_var.get()
        }

    def set_data(self, data):
        self.name_var.set(data.get("name", ""))
        self.bpm_var.set(data.get("bpm", "120"))
        self.freq_var.set(data.get("freq", "440"))
        self.dur_var.set(data.get("dur", "100"))
        self.att_var.set(data.get("att", "1"))
        self.dec_var.set(data.get("dec", "20"))
        self.vol_var.set(data.get("vol", "100"))

    def toggle(self):
        if self.app.engine.active[self.index] and not self.app.engine.fading_out[self.index]:
            self.stop()
        else:
            self.start(sync_mode=False)

    def start(self, sync_mode=False):
        if self.name_var.get().strip():
            self.app.engine.start_channel(self.index, self.get_data(), sync_mode)
            self.btn.config(text="Stop")
            threading.Thread(target=self.app.auto_connect_channel, args=(self.index,), daemon=True).start()

    def stop(self):
        self.app.engine.stop_channel(self.index)
        self.btn.config(text="Start")
        self.set_led(False) 


class PolyMetroApp:
    def __init__(self, root, engine):
        self.root = root
        self.engine = engine
        
        self.root.title("METRO LAMPUNG pulsa multikanal")
        # Mengatur ukuran ideal, tapi MENGIZINKAN resize jika layar OS Anda membutuhkan ruang lebih
        self.root.geometry("980x800") 
        self.root.minsize(950, 750) # Mencegah window dikecilkan terlalu parah
        
        self.rows = []
        self.group_vol_vars = []
        self.group_mute_btns = [] 
        
        self.global_leds = []
        self.global_led_states = [False] * NUM_CHANNELS

        # --- NATIVE MENU BAR ---
        self.menu_bar = tk.Menu(self.root)
        
        self.file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.file_menu.add_command(label="Load Preset...", command=self.load_preset, accelerator="Ctrl+O")
        self.file_menu.add_command(label="Save Preset...", command=self.save_preset, accelerator="Ctrl+S")
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_close, accelerator="Ctrl+Q")
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)
        
        self.help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.help_menu.add_command(label="About", command=self.show_about)
        self.menu_bar.add_cascade(label="Help", menu=self.help_menu)
        
        self.root.config(menu=self.menu_bar)
        
        self.root.bind('<Control-o>', lambda event: self.load_preset())
        self.root.bind('<Control-s>', lambda event: self.save_preset())
        self.root.bind('<Control-q>', lambda event: self.on_close())


        # --- HEADER / BRANDING ---
        header_frame = ttk.Frame(self.root)
        header_frame.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(15, 5))
        
        title_lbl = ttk.Label(header_frame, text="METRO LAMPUNG", font=("Arial", 22, "bold"))
        title_lbl.pack(side=tk.LEFT)
        
        subtitle_lbl = ttk.Label(header_frame, text="PULSA MULTIKANAL (32-CH)", font=("Arial", 11, "italic"), foreground="gray40")
        subtitle_lbl.pack(side=tk.LEFT, padx=(10, 0), pady=(8, 0))

        # --- GLOBAL METER BRIDGE ---
        monitor_frame = ttk.LabelFrame(self.root, text=" Global Pulse Monitor ", padding=5)
        monitor_frame.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(10, 5))
        
        monitor_center = ttk.Frame(monitor_frame)
        monitor_center.pack(anchor=tk.CENTER, pady=5)

        for g in range(4):
            grp_frame = ttk.Frame(monitor_center)
            grp_frame.pack(side=tk.LEFT, padx=20)
            
            ttk.Label(grp_frame, text=f"GRP {g+1} ", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
            
            for c in range(8):
                led_canvas = tk.Canvas(grp_frame, width=14, height=14, highlightthickness=0)
                led_canvas.pack(side=tk.LEFT, padx=2)
                led_id = led_canvas.create_oval(1, 1, 13, 13, fill="gray30", outline="black", width=1)
                self.global_leds.append((led_canvas, led_id))

        # --- TABBED NOTEBOOK ARCHITECTURE ---
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        for g in range(4):
            tab = ttk.Frame(self.notebook, padding=10)
            start_ch = (g * 8) + 1
            end_ch = start_ch + 7
            self.notebook.add(tab, text=f"   Group {g+1} (Ch {start_ch}-{end_ch})   ")

            # TOP SECTION: Grid of 8 Channels
            grid_frame = ttk.Frame(tab)
            grid_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

            headers = ["Port Name", "BPM", "Freq (Hz)", "Dur (ms)", "Attack (%)", "Decay (%)", "Vol (%)", "Control", "Pulse"]
            for col, text in enumerate(headers):
                ttk.Label(grid_frame, text=text, font=("Arial", 9, "bold")).grid(row=0, column=col, padx=8, pady=(0, 8))

            for i in range(8):
                global_idx = (g * 8) + i
                self.rows.append(MetronomeRow(grid_frame, global_idx, self))

            # BOTTOM SECTION: Group Master (Horizontal, Bottom Left)
            slider_frame = ttk.LabelFrame(tab, text=f" Group {g+1} Master ", padding=10)
            slider_frame.pack(side=tk.BOTTOM, anchor=tk.W, fill=tk.X, padx=8, pady=5)

            grp_mute_btn = ttk.Button(slider_frame, text="🔊 Mute", width=10)
            grp_mute_btn.config(command=lambda idx=g, b=grp_mute_btn: self.toggle_group_mute(idx, b))
            grp_mute_btn.pack(side=tk.LEFT, padx=(5, 15))
            self.group_mute_btns.append(grp_mute_btn)

            ttk.Label(slider_frame, text="Volume:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(10, 5))

            g_vol_var = tk.DoubleVar(value=100.0)
            self.group_vol_vars.append(g_vol_var)
            
            slider = ttk.Scale(
                slider_frame, 
                from_=0, to=100, 
                orient=tk.HORIZONTAL, 
                variable=g_vol_var,
                command=lambda val, idx=g: self.on_group_vol_change(val, idx)
            )
            slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
            
            readout = ttk.Label(slider_frame, text="100%", font=("Arial", 10, "bold"), width=5)
            readout.pack(side=tk.LEFT, padx=(5, 10))
            g_vol_var.trace_add("write", lambda *args, r=readout, v=g_vol_var: r.config(text=f"{int(v.get())}%"))


        # --- FOOTER SECTION ---
        # Mengatur agar box kontrol Start All selalu berada di ATAS tulisan credit
        footer_frame = ttk.Frame(self.root)
        footer_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 15), padx=20)

        # Main Control Bar (Packed TOP inside footer)
        master_frame = ttk.LabelFrame(footer_frame, text=" Global Transport & Main Out ", padding=10)
        master_frame.pack(side=tk.TOP, fill=tk.X)
        
        ttk.Button(master_frame, text="Load Preset", command=self.load_preset, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(master_frame, text="Save Preset", command=self.save_preset, width=12).pack(side=tk.LEFT, padx=5)
        
        separator = ttk.Frame(master_frame, width=2, relief=tk.SUNKEN)
        separator.pack(side=tk.LEFT, fill=tk.Y, padx=15, pady=2)
        
        ttk.Button(master_frame, text="▶ START ALL (SYNC)", command=self.start_all, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(master_frame, text="⏹ STOP ALL", command=self.stop_all, width=12).pack(side=tk.LEFT, padx=5)
        
        self.master_vol_var = tk.StringVar(value="100")
        self.master_vol_var.trace_add("write", self.on_master_vol_change)
        
        main_vol_frame = ttk.Frame(master_frame)
        main_vol_frame.pack(side=tk.RIGHT, padx=5)
        
        self.master_mute_btn = ttk.Button(main_vol_frame, text="🔊 Mute", width=10, command=self.toggle_master_mute)
        self.master_mute_btn.pack(side=tk.LEFT, padx=10)

        ttk.Label(main_vol_frame, text="MASTER OUT:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(5, 5))
        ttk.Spinbox(main_vol_frame, from_=0, to=100, textvariable=self.master_vol_var, width=6, font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=5)

        # Credit Line (Packed BOTTOM inside footer)
        credit_label = ttk.Label(footer_frame, text="dikembangkan oleh rekambergeraklab Yogyakarta-Indonesia", font=("Arial", 8, "italic"), foreground="gray50")
        credit_label.pack(side=tk.BOTTOM, anchor=tk.E, pady=(5, 0))


        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Auto Load & Init Polling
        self.load_autosave()
        self.update_leds()

    def show_about(self):
        about_text = (
            "METRO LAMPUNG\n"
            "Pulsa Multikanal (32-CH)\n\n"
            "Dikembangkan oleh:\n"
            "rekambergeraklab\n"
            "Yogyakarta - Indonesia"
        )
        messagebox.showinfo("About METRO LAMPUNG", about_text)

    def _get_current_state(self):
        return {
            "channels": [row.get_data() for row in self.rows],
            "group_vols": [var.get() for var in self.group_vol_vars],
            "group_mutes": self.engine.group_mutes,
            "master_mute": self.engine.master_mute
        }

    def _apply_state(self, data):
        self.stop_all()
        
        if isinstance(data, dict):
            channels_data = data.get("channels", [])
            group_data = data.get("group_vols", [100.0] * 4)
            for i, g_vol in enumerate(group_data):
                self.group_vol_vars[i].set(g_vol)
                
            g_mutes = data.get("group_mutes", [False] * 4)
            self.engine.group_mutes = g_mutes
            for i, btn in enumerate(self.group_mute_btns):
                btn.config(text="🔇 Unmute" if g_mutes[i] else "🔊 Mute")
                
            m_mute = data.get("master_mute", False)
            self.engine.master_mute = m_mute
            self.master_mute_btn.config(text="🔇 Unmute" if m_mute else "🔊 Mute")
            
        else:
            channels_data = data
            for var in self.group_vol_vars:
                var.set(100.0)
            self.engine.group_mutes = [False] * 4
            for btn in self.group_mute_btns:
                btn.config(text="🔊 Mute")
            self.engine.master_mute = False
            self.master_mute_btn.config(text="🔊 Mute")

        for i, row_data in enumerate(channels_data):
            if i < len(self.rows):
                self.rows[i].set_data(row_data)

    def toggle_group_mute(self, group_idx, btn):
        is_muted = not self.engine.group_mutes[group_idx]
        self.engine.group_mutes[group_idx] = is_muted
        btn.config(text="🔇 Unmute" if is_muted else "🔊 Mute")

    def toggle_master_mute(self):
        is_muted = not self.engine.master_mute
        self.engine.master_mute = is_muted
        self.master_mute_btn.config(text="🔇 Unmute" if is_muted else "🔊 Mute")

    def update_leds(self):
        current_frame = self.engine.frame_counter
        sr = self.engine.client.samplerate
        
        for i, row in enumerate(self.rows):
            is_on = False
            
            if self.engine.active[i] and not self.engine.fading_out[i] and self.engine.samples_per_beat[i] > 0:
                local_frame = current_frame - self.engine.channel_offsets[i]
                
                if local_frame >= 0:
                    phase = local_frame % self.engine.samples_per_beat[i]
                    visual_len = max(self.engine.tick_lengths[i], sr * 0.05)
                    
                    if phase < visual_len:
                        is_on = True

            row.set_led(is_on)
            
            if is_on != self.global_led_states[i]:
                self.global_led_states[i] = is_on
                color = "lime green" if is_on else "gray30"
                canvas, led_id = self.global_leds[i]
                canvas.itemconfig(led_id, fill=color)
                
        self.root.after(30, self.update_leds)

    def on_group_vol_change(self, val, group_idx):
        self.engine.group_vols[group_idx] = float(val) / 100.0

    def on_master_vol_change(self, *args):
        try:
            val = float(self.master_vol_var.get())
            self.engine.master_vol = max(0.0, min(100.0, val)) / 100.0
        except ValueError:
            pass

    def start_all(self):
        self.engine.frame_counter = 0 
        for i in range(NUM_CHANNELS):
            self.engine.channel_offsets[i] = 0
            
        for row in self.rows:
            if row.name_var.get().strip():
                row.start(sync_mode=True)

    def stop_all(self):
        for row in self.rows:
            row.stop()

    def auto_connect_channel(self, index):
        time.sleep(0.2)
        try:
            playback_ports = self.engine.client.get_ports(is_physical=True, is_input=True)
            if len(playback_ports) >= 2:
                port = self.engine.out_ports[index]
                self.engine.client.connect(port, playback_ports[0])
                self.engine.client.connect(port, playback_ports[1])
        except jack.JackError:
            pass

    def save_preset(self):
        filepath = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    json.dump(self._get_current_state(), f, indent=4)
                messagebox.showinfo("Success", "Preset saved!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save preset:\n{e}")

    def load_preset(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if filepath:
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                self._apply_state(data)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load preset:\n{e}")

    def load_autosave(self):
        if os.path.exists(AUTOSAVE_FILE):
            try:
                with open(AUTOSAVE_FILE, 'r') as f:
                    data = json.load(f)
                self._apply_state(data)
            except Exception:
                pass 

    def save_autosave(self):
        try:
            with open(AUTOSAVE_FILE, 'w') as f:
                json.dump(self._get_current_state(), f, indent=4)
        except Exception:
            pass 

    def on_close(self):
        self.save_autosave() 
        self.engine.close()
        self.root.destroy()


if __name__ == "__main__":
    engine = MetroEngine()
    
    root = tk.Tk()
    style = ttk.Style()
    style.theme_use('clam') 
    
    app = PolyMetroApp(root, engine)
    root.mainloop()
