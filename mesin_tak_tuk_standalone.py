#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import json
import numpy as np
import pygame

# Initialize pygame mixer for low-latency audio
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)

class MetronomeRow:
    def __init__(self, parent, row_index):
        self.playing = False
        self.parent = parent

        # Default Values
        self.name_var = tk.StringVar(value=f"Metro{row_index}")
        self.bpm_var = tk.StringVar(value="120")
        self.freq_var = tk.StringVar(value=str(440 + (row_index * 110)))
        self.dur_var = tk.StringVar(value="100")
        self.vol_var = tk.DoubleVar(value=0.5) # Added volume control

        # UI Elements
        ttk.Entry(parent, textvariable=self.name_var, width=10).grid(row=row_index, column=0, padx=5, pady=5)
        ttk.Entry(parent, textvariable=self.bpm_var, width=6).grid(row=row_index, column=1, padx=5, pady=5)
        ttk.Entry(parent, textvariable=self.freq_var, width=6).grid(row=row_index, column=2, padx=5, pady=5)
        ttk.Entry(parent, textvariable=self.dur_var, width=6).grid(row=row_index, column=3, padx=5, pady=5)
        
        # Volume Slider instead of Attack/Decay for simplicity in standalone
        ttk.Scale(parent, from_=0, to=1, variable=self.vol_var, orient='horizontal', length=60).grid(row=row_index, column=4, columnspan=2, padx=5)

        self.btn = ttk.Button(parent, text="Start", command=self.toggle)
        self.btn.grid(row=row_index, column=6, padx=5, pady=5)

    def generate_sound(self):
        """Generates a sine wave beep based on frequency and duration."""
        sample_rate = 44100
        freq = float(self.freq_var.get())
        dur = float(self.dur_var.get()) / 1000.0
        
        t = np.linspace(0, dur, int(sample_rate * dur), False)
        wave = np.sin(freq * t * 2 * np.pi) * self.vol_var.get()
        
        # Apply a quick fade out to prevent clicking sounds
        fade_len = int(len(wave) * 0.1)
        fade_out = np.linspace(1., 0., fade_len)
        wave[-fade_len:] *= fade_out

        audio = (wave * 32767).astype(np.int16)
        # Create stereo sound
        stereo_audio = np.column_stack((audio, audio))
        return pygame.sndarray.make_sound(stereo_audio)

    def toggle(self):
        if not self.playing:
            self.start()
        else:
            self.stop()

    def start(self):
        self.playing = True
        self.btn.config(text="Stop")
        threading.Thread(target=self.run_loop, daemon=True).start()

    def run_loop(self):
        sound = self.generate_sound()
        while self.playing:
            bpm = float(self.bpm_var.get())
            if bpm <= 0: break
            
            interval = 60.0 / bpm
            start_time = time.time()
            
            sound.play()
            
            # Precise sleep calculation
            while time.time() < start_time + interval:
                if not self.playing: return
                time.sleep(0.001)

    def stop(self):
        self.playing = False
        self.btn.config(text="Start")

    def get_data(self):
        return {"name": self.name_var.get(), "bpm": self.bpm_var.get(), "freq": self.freq_var.get(), "dur": self.dur_var.get()}

    def set_data(self, data):
        self.name_var.set(data.get("name", ""))
        self.bpm_var.set(data.get("bpm", "120"))
        self.freq_var.set(data.get("freq", "440"))
        self.dur_var.set(data.get("dur", "100"))

class PolyMetroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Mesin Tak Tuk (Standalone)")
        self.rows = []
        headers = ["Name", "BPM", "Freq (Hz)", "Dur (ms)", "Volume", "", "Control"]
        for col, text in enumerate(headers):
            ttk.Label(root, text=text, font=("Arial", 10, "bold")).grid(row=0, column=col, padx=5, pady=5)

        for i in range(1, 9):
            self.rows.append(MetronomeRow(root, i))

        btn_frame = ttk.Frame(root)
        btn_frame.grid(row=10, column=0, columnspan=7, pady=20)
        ttk.Button(btn_frame, text="Save Preset", command=self.save_preset).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Load Preset", command=self.load_preset).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="START ALL", command=self.start_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="STOP ALL", command=self.stop_all).pack(side=tk.LEFT, padx=5)

    def save_preset(self):
        data = [row.get_data() for row in self.rows]
        fp = filedialog.asksaveasfilename(defaultextension=".json")
        if fp:
            with open(fp, 'w') as f: json.dump(data, f)

    def load_preset(self):
        fp = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if fp:
            with open(fp, 'r') as f:
                data = json.load(f)
                for i, row_data in enumerate(data): self.rows[i].set_data(row_data)

    def start_all(self):
        for row in self.rows: row.start()

    def stop_all(self):
        for row in self.rows: row.stop()

if __name__ == "__main__":
    root = tk.Tk()
    app = PolyMetroApp(root)
    root.mainloop()