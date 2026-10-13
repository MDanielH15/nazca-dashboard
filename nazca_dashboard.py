from __future__ import annotations

from collections import deque
from datetime import datetime
import queue
import re
import threading
import time
import tkinter as tk

import customtkinter as ctk
import serial
from serial.tools import list_ports


# ---------------- CONFIGURACIÓN ----------------

BAUDRATE = 9600
BOOT_DELAY = 2.0

BG = "#061C13"
CARD = "#0D2A20"
INNER = "#082219"
BORDER = "#234438"
GOLD = "#C49A45"
GOLD_HOVER = "#D4AF62"
WHITE = "#FFFFFF"
MUTED = "#A5BDB1"
RED = "#DA3C43"
RED_HOVER = "#F05058"
GREEN = "#78CEA2"

TARGET_LABELS = {
    "M1": "Motor 1",
    "M2": "Motor 2",
    "B": "Ambos motores",
}

ACK_PATTERN = re.compile(
    r"^(M1|M2|B)\s*:\s*([0-4])$",
    re.IGNORECASE,
)


def target_motors(target):
    """Identifica los motores afectados por una orden."""
    if target == "M1":
        return (1,)
    if target == "M2":
        return (2,)
    if target == "B":
        return (1, 2)
    raise ValueError("Destino inválido.")


def encode_level(level, target="B"):
    """Genera una línea como M1:3, M2:2 o B:0."""
    if type(level) is not int or not 0 <= level <= 4:
        raise ValueError("El nivel debe ser un entero entre 0 y 4.")

    target_motors(target)
    return f"{target}:{level}\n".encode("ascii")


# ---------------- COMUNICACIÓN SERIAL ----------------

class SerialWorker(threading.Thread):
    """Gestiona el puerto en un hilo independiente de la interfaz."""

    def __init__(self, port, events, session):
        super().__init__(daemon=True, name=f"Serial-{session}")
        self.port_name = port
        self.events = events
        self.session = session
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

        # Última orden pendiente de cada motor.
        self.pending = {}

    def emit(self, kind, data=None):
        self.events.put((self.session, kind, data))

    def request(self, level, target="B"):
        encode_level(level, target)
        with self.lock:
            for motor in target_motors(target):
                self.pending[motor] = level

    def stop(self):
        self.stop_event.set()

    def take_command(self):
        with self.lock:
            if not self.pending:
                return None

            # Combina órdenes iguales para ambos motores.
            if (
                len(self.pending) == 2
                and self.pending[1] == self.pending[2]
            ):
                level = self.pending[1]
                self.pending.clear()
                return "B", level

            motor = min(self.pending)
            return f"M{motor}", self.pending.pop(motor)

    @staticmethod
    def write_command(port, target, level):
        data = encode_level(level, target)
        if port.write(data) != len(data):
            raise serial.SerialTimeoutException(
                "Escritura serial incompleta."
            )

    def run(self):
        port = None
        stop_sent = False
        receive = bytearray()
        last_byte = time.monotonic()

        try:
            port = serial.Serial(
                port=self.port_name,
                baudrate=BAUDRATE,
                timeout=0.05,
                write_timeout=0.25,
            )
            self.emit("opening", self.port_name)

            # Algunas placas se reinician al abrir el puerto.
            if not self.stop_event.wait(BOOT_DELAY):
                self.write_command(port, "B", 0)
                self.emit("ready", self.port_name)
                self.emit("sent", ("B", 0))

                while not self.stop_event.is_set():
                    command = self.take_command()
                    if command is not None:
                        target, level = command
                        self.write_command(port, target, level)
                        self.emit("sent", command)

                    size = min(max(port.in_waiting, 1), 512)
                    data = port.read(size)
                    now = time.monotonic()

                    if data:
                        receive.extend(data)
                        last_byte = now

                        # Reconstruye mensajes divididos entre lecturas.
                        while b"\n" in receive:
                            line, _, rest = receive.partition(b"\n")
                            receive = bytearray(rest)
                            self.emit(
                                "rx",
                                line.decode(
                                    "utf-8", errors="replace"
                                ).strip(),
                            )

                        if len(receive) >= 4096:
                            self.emit(
                                "rx",
                                receive.decode(
                                    "utf-8", errors="replace"
                                ).strip(),
                            )
                            receive.clear()

                    elif receive and now - last_byte >= 0.4:
                        self.emit(
                            "rx",
                            receive.decode(
                                "utf-8", errors="replace"
                            ).strip(),
                        )
                        receive.clear()

        except (serial.SerialException, OSError, ValueError) as exc:
            self.emit("error", str(exc))

        finally:
            if port is not None:
                # Intenta parar ambos antes de cerrar.
                try:
                    self.write_command(port, "B", 0)
                    stop_sent = True
                except (serial.SerialException, OSError, ValueError):
                    pass

                try:
                    port.close()
                except (serial.SerialException, OSError):
                    pass

            self.emit("closed", stop_sent)


# ---------------- DASHBOARD ----------------

class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__()

        self.title("NAZCA Racing | Motor Control")
        self.geometry("1140x900")
        self.minsize(990, 760)
        self.configure(fg_color=BG)

        self.events = queue.Queue()
        self.worker = None
        self.session = 0
        self.connected = False
        self.closing = False
        self.scanning = False

        self.target = "B"
        self.levels = {1: 0, 2: 0}
        self.reported_levels = {1: None, 2: None}
        self.level = 0

        self.logs = deque(maxlen=250)
        self.port_map = {}

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Escape>", lambda event: self.emergency_stop())
        self.bind("<space>", self.space_stop)

        self.build_ui()
        self.enable_controls(False)
        self.log("SYS", "Panel listo. Selecciona el puerto del Arduino.")
        self.refresh_ports()

        self.after(40, self.poll_events)
        self.after(3000, self.auto_refresh)

    def label(
        self, parent, text, size=13, color=WHITE, bold=False, **kwargs
    ):
        return ctk.CTkLabel(
            parent,
            text=text,
            text_color=color,
            font=ctk.CTkFont(
                family="Roboto",
                size=size,
                weight="bold" if bold else "normal",
            ),
            **kwargs,
        )

    def card(self, parent):
        return ctk.CTkFrame(
            parent,
            fg_color=CARD,
            border_color=BORDER,
            border_width=1,
            corner_radius=18,
        )

    def build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Encabezado.
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(
            row=0, column=0, padx=30,
            pady=(25, 20), sticky="ew"
        )
        header.grid_columnconfigure(1, weight=1)

        emblem = tk.Canvas(
            header, width=64, height=55,
            bg=BG, highlightthickness=0
        )
        emblem.grid(
            row=0, column=0, rowspan=2, padx=(0, 18)
        )
        for x, y, width in (
            (27, 8, 34), (16, 23, 36), (5, 38, 38)
        ):
            emblem.create_polygon(
                x, y, x + width, y,
                x + width - 9, y + 9, x - 9, y + 9,
                fill=GOLD, outline=""
            )

        self.label(
            header, "NAZCA RACING", 28, bold=True
        ).grid(row=0, column=1, sticky="w")

        self.label(
            header, "FORMULA SAE ELECTRIC / MOTOR CONTROL",
            11, MUTED
        ).grid(row=1, column=1, sticky="w")

        self.status_badge = self.label(
            header, "● SIN CONEXIÓN", 12, MUTED, bold=True
        )
        self.status_badge.grid(row=0, column=2, rowspan=2)

        # Conexión.
        connection = self.card(self)
        connection.grid(
            row=1, column=0, padx=30,
            pady=(0, 18), sticky="ew"
        )
        connection.grid_columnconfigure(1, weight=1)

        self.label(
            connection, "01 / ENLACE SERIAL", 12, GOLD, True
        ).grid(
            row=0, column=0, columnspan=3,
            sticky="w", padx=22, pady=(16, 8)
        )

        self.header_stop = ctk.CTkButton(
            connection,
            text="E-STOP / AMBOS",
            width=148, height=28,
            fg_color=RED, hover_color=RED_HOVER,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self.emergency_stop,
        )
        self.header_stop.grid(
            row=0, column=3, padx=(0, 22), pady=(10, 8)
        )

        self.label(
            connection, "PUERTO", 11, MUTED, True
        ).grid(
            row=1, column=0,
            padx=(22, 14), pady=(0, 18)
        )

        self.port_combo = ctk.CTkComboBox(
            connection,
            values=["Buscando puertos…"],
            state="readonly", height=40,
            fg_color=INNER, border_color=BORDER,
            button_color=BORDER,
            button_hover_color="#355B49",
            dropdown_fg_color=INNER,
            dropdown_hover_color=BORDER,
            text_color=WHITE,
        )
        self.port_combo.grid(
            row=1, column=1, sticky="ew", pady=(0, 18)
        )

        self.refresh_button = ctk.CTkButton(
            connection,
            text="Actualizar", width=104, height=40,
            fg_color=BORDER, hover_color="#355B49",
            command=self.refresh_ports,
        )
        self.refresh_button.grid(
            row=1, column=2, padx=12, pady=(0, 18)
        )

        self.connect_button = ctk.CTkButton(
            connection,
            text="Conectar", width=148, height=40,
            fg_color=GOLD, hover_color=GOLD_HOVER,
            text_color=BG,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.toggle_connection,
        )
        self.connect_button.grid(
            row=1, column=3, padx=(0, 22), pady=(0, 18)
        )

        # Área central desplazable.
        body = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=GOLD,
        )
        body.grid(
            row=2, column=0, padx=30,
            pady=(0, 18), sticky="nsew"
        )
        body.grid_columnconfigure(0, weight=3, uniform="panels")
        body.grid_columnconfigure(1, weight=2, uniform="panels")
        body.grid_rowconfigure(0, weight=1)

        # Control de motores.
        control = self.card(body)
        control.grid(
            row=0, column=0, sticky="nsew", padx=(0, 16)
        )
        control.grid_columnconfigure(0, weight=1)

        self.label(
            control, "02 / CONTROL DE POTENCIA", 12, GOLD, True
        ).grid(
            row=0, column=0, sticky="w",
            padx=24, pady=(20, 8)
        )

        self.label(
            control, "Velocidad de dos motores.", 22, bold=True
        ).grid(row=1, column=0, sticky="w", padx=24)

        self.label(
            control, "Selecciona el motor y un nivel de 0 a 4",
            13, MUTED
        ).grid(
            row=2, column=0, sticky="w",
            padx=24, pady=(3, 0)
        )

        self.target_selector = ctk.CTkSegmentedButton(
            control,
            values=list(TARGET_LABELS.values()),
            height=36,
            fg_color=INNER,
            selected_color=GOLD,
            selected_hover_color=GOLD_HOVER,
            unselected_color=INNER,
            unselected_hover_color=BORDER,
            text_color=WHITE,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.select_target,
        )
        self.target_selector.set("Ambos motores")
        self.target_selector.grid(
            row=3, column=0, sticky="ew",
            padx=24, pady=(12, 4)
        )

        number = ctk.CTkFrame(control, fg_color="transparent")
        number.grid(
            row=4, column=0, sticky="ew",
            padx=24, pady=(6, 4)
        )

        self.level_label = self.label(
            number, "0", 52, GOLD, True
        )
        self.level_label.pack(side="left")

        self.label(
            number, "/ 4  NIVEL SOLICITADO", 12, MUTED, True
        ).pack(side="left", padx=16, pady=(30, 0))

        self.slider = ctk.CTkSlider(
            control,
            from_=0, to=4, number_of_steps=4,
            height=24,
            fg_color=BORDER,
            progress_color=GOLD,
            button_color=GOLD,
            button_hover_color=GOLD_HOVER,
            command=self.set_level,
        )
        self.slider.set(0)
        self.slider.grid(
            row=5, column=0, padx=28,
            pady=(6, 0), sticky="ew"
        )

        ticks = ctk.CTkFrame(control, fg_color="transparent")
        ticks.grid(
            row=6, column=0, padx=24,
            pady=(3, 10), sticky="ew"
        )
        for level in range(5):
            ticks.grid_columnconfigure(level, weight=1)
            self.label(
                ticks, f"{level * 25}%", 11, MUTED
            ).grid(row=0, column=level)

        quick = ctk.CTkFrame(control, fg_color="transparent")
        quick.grid(row=7, column=0, sticky="ew", padx=24)

        self.quick_buttons = []
        for level in range(5):
            quick.grid_columnconfigure(level, weight=1)

            button = ctk.CTkButton(
                quick,
                text="0\nPARAR" if level == 0 else str(level),
                width=50, height=44,
                corner_radius=10,
                fg_color=RED if level == 0 else INNER,
                hover_color=RED_HOVER if level == 0 else BORDER,
                border_width=1,
                border_color=RED if level == 0 else BORDER,
                font=ctk.CTkFont(
                    size=12 if level == 0 else 18,
                    weight="bold",
                ),
                command=lambda value=level: self.set_level(
                    value, force=True
                ),
            )
            button.grid(
                row=0, column=level, sticky="ew",
                padx=(0 if level == 0 else 6, 0)
            )
            self.quick_buttons.append(button)

        self.stop_button = ctk.CTkButton(
            control,
            text="E-STOP / PARAR AMBOS MOTORES",
            height=43,
            fg_color=RED, hover_color=RED_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.emergency_stop,
        )
        self.stop_button.grid(
            row=8, column=0, sticky="ew",
            padx=24, pady=(12, 6)
        )

        self.label(
            control,
            "0: para el destino elegido · ESC/espacio: para ambos",
            11, MUTED
        ).grid(
            row=9, column=0, sticky="w",
            padx=24, pady=(0, 12)
        )

        # Telemetría.
        telemetry = self.card(body)
        telemetry.grid(row=0, column=1, sticky="nsew")
        telemetry.grid_columnconfigure(0, weight=1)

        self.label(
            telemetry, "03 / ESTADO DEL SISTEMA", 12, GOLD, True
        ).grid(
            row=0, column=0, sticky="w",
            padx=24, pady=(20, 12)
        )

        self.power_caption = self.label(
            telemetry, "POTENCIA / AMBOS MOTORES",
            11, MUTED, True
        )
        self.power_caption.grid(
            row=1, column=0, sticky="w", padx=24
        )

        self.percent_label = self.label(
            telemetry, "0%", 58, WHITE, True
        )
        self.percent_label.grid(
            row=2, column=0, sticky="w",
            padx=24, pady=(0, 3)
        )

        self.power_bar = ctk.CTkProgressBar(
            telemetry,
            height=19, corner_radius=9,
            fg_color=BORDER, progress_color=GOLD,
        )
        self.power_bar.set(0)
        self.power_bar.grid(
            row=3, column=0, sticky="ew",
            padx=24, pady=(0, 13)
        )

        self.label(
            telemetry,
            "Escala del nivel seleccionado; no mide RPM ni PWM real.",
            11, MUTED, wraplength=300, justify="left"
        ).grid(
            row=4, column=0, sticky="w",
            padx=24, pady=(0, 16)
        )

        detail = ctk.CTkFrame(
            telemetry, fg_color=INNER, corner_radius=12
        )
        detail.grid(row=5, column=0, sticky="ew", padx=24)
        detail.grid_columnconfigure(1, weight=1)

        self.sent_label = self.detail_row(
            detail, 0, "ÚLTIMO ENVÍO", "—"
        )
        self.motor_labels = {
            1: self.detail_row(
                detail, 1, "M1 SOLICITADO / RX", "0 / —"
            ),
            2: self.detail_row(
                detail, 2, "M2 SOLICITADO / RX", "0 / —"
            ),
        }
        self.detail_row(detail, 3, "BAUDRATE", "9600 bps")

        self.feedback_label = self.label(
            telemetry,
            "Conecta el Arduino para comenzar.",
            12, MUTED, wraplength=300, justify="left"
        )
        self.feedback_label.grid(
            row=6, column=0, sticky="w",
            padx=24, pady=(8, 8)
        )

        # Consola.
        console_panel = self.card(self)
        console_panel.grid(
            row=3, column=0, padx=30,
            pady=(0, 13), sticky="ew"
        )
        console_panel.grid_columnconfigure(0, weight=1)

        self.label(
            console_panel, "04 / CONSOLA SERIAL", 12, GOLD, True
        ).grid(
            row=0, column=0, sticky="w",
            padx=20, pady=(12, 8)
        )

        ctk.CTkButton(
            console_panel,
            text="Limpiar", width=70, height=24,
            fg_color=BORDER, hover_color="#355B49",
            command=self.clear_log,
        ).grid(
            row=0, column=1, padx=20, pady=(10, 5)
        )

        self.console = ctk.CTkTextbox(
            console_panel,
            height=95,
            fg_color=INNER,
            text_color=MUTED,
            corner_radius=10,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
        )
        self.console.grid(
            row=1, column=0, columnspan=2,
            padx=16, pady=(0, 14), sticky="ew"
        )
        self.console.configure(state="disabled")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(
            row=4, column=0, sticky="ew",
            padx=32, pady=(0, 13)
        )
        footer.grid_columnconfigure(0, weight=1)

        self.label(
            footer, "UTEC / FORMULA SAE ELECTRIC", 10, MUTED
        ).grid(row=0, column=0, sticky="w")

        self.label(
            footer, "SERIAL 9600 · NIVELES 0–4", 10, GOLD
        ).grid(row=0, column=1, sticky="e")

    def detail_row(self, parent, row, name, value):
        self.label(
            parent, name, 10, MUTED, True
        ).grid(
            row=row, column=0, sticky="w", padx=14, pady=4
        )
        widget = self.label(parent, value, 13, WHITE, True)
        widget.grid(
            row=row, column=1, sticky="e", padx=14, pady=4
        )
        return widget

    def log(self, source, message):
        """Consola de solo lectura, limitada a 250 registros."""
        message = str(message).replace("\r", "").replace("\x00", "")
        if not message:
            return

        self.logs.append(
            f"{datetime.now():%H:%M:%S} [{source}] {message}"
        )
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.insert("end", "\n".join(self.logs) + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def clear_log(self):
        self.logs.clear()
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def refresh_ports(self):
        """Busca los puertos en segundo plano."""
        if self.scanning or self.closing:
            return
        self.scanning = True

        def scan():
            try:
                ports = sorted(
                    list_ports.comports(),
                    key=lambda port: port.device,
                )
                values = [
                    (
                        f"{port.device} · {port.description}",
                        port.device,
                    )
                    for port in ports
                ]
                self.events.put((None, "ports", values))
            except (serial.SerialException, OSError) as exc:
                self.events.put(
                    (None, "ports_error", str(exc))
                )

        threading.Thread(
            target=scan, daemon=True, name="PortScanner"
        ).start()

    def auto_refresh(self):
        if not self.closing:
            if self.worker is None:
                self.refresh_ports()
            self.after(3000, self.auto_refresh)

    def enable_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        self.slider.configure(state=state)
        self.stop_button.configure(state=state)
        self.header_stop.configure(state=state)
        self.target_selector.configure(state=state)
        for button in self.quick_buttons:
            button.configure(state=state)

    def render_level(self):
        """Muestra la consigna del destino seleccionado."""
        values = [
            self.levels[motor]
            for motor in target_motors(self.target)
        ]
        level = values[0] if len(set(values)) == 1 else None
        self.level = level

        self.slider.set(0 if level is None else level)
        self.level_label.configure(
            text="—" if level is None else str(level)
        )
        self.percent_label.configure(
            text="MIXTO" if level is None else f"{level * 25}%"
        )
        self.power_bar.set(
            0 if level is None else level / 4
        )

        caption = (
            "AMBOS / NIVELES DIFERENTES"
            if level is None
            else f"POTENCIA / {TARGET_LABELS[self.target].upper()}"
        )
        self.power_caption.configure(text=caption)

        for motor, label in self.motor_labels.items():
            received = self.reported_levels[motor]
            received_text = (
                "—" if received is None else str(received)
            )
            label.configure(
                text=f"{self.levels[motor]} / {received_text}"
            )

        for value, button in enumerate(self.quick_buttons):
            if value > 0:
                active = value == level
                button.configure(
                    fg_color=GOLD if active else INNER,
                    text_color=BG if active else WHITE,
                    border_color=GOLD if active else BORDER,
                )

    def select_target(self, label):
        """Cambiar de selector no envía órdenes."""
        self.target = next(
            target
            for target, name in TARGET_LABELS.items()
            if name == label
        )
        self.render_level()

        if self.level is None:
            message = (
                "M1 y M2 tienen niveles diferentes. "
                "Elige un nivel para igualarlos."
            )
        else:
            message = (
                f"{label}: nivel solicitado {self.level}. "
                "Cambiar destino no mueve los motores."
            )

        self.feedback_label.configure(
            text=message, text_color=MUTED
        )

    def set_level(self, value, force=False):
        """Aplica un nivel al motor o motores elegidos."""
        if (
            not self.connected
            or self.worker is None
            or self.closing
        ):
            return

        level = max(0, min(4, int(round(float(value)))))
        if level == self.level and not force:
            return

        for motor in target_motors(self.target):
            self.levels[motor] = level

        self.render_level()
        self.feedback_label.configure(
            text="Orden pendiente de envío…",
            text_color=GOLD,
        )
        self.worker.request(level, self.target)

    def emergency_stop(self):
        """La parada global siempre afecta a ambos motores."""
        if (
            self.connected
            and self.worker is not None
            and not self.closing
        ):
            self.levels = {1: 0, 2: 0}
            self.render_level()
            self.worker.request(0, "B")
            self.feedback_label.configure(
                text="Parada global pendiente de envío…",
                text_color=GOLD,
            )
            self.log("SYS", "Parada global solicitada: B:0.")

    def space_stop(self, event):
        self.emergency_stop()
        return "break"

    def toggle_connection(self):
        if self.worker is not None:
            self.disconnect()
            return

        port = self.port_map.get(self.port_combo.get())
        if not port:
            self.log("SYS", "Selecciona un puerto válido.")
            return

        self.session += 1
        self.levels = {1: 0, 2: 0}
        self.reported_levels = {1: None, 2: None}
        self.sent_label.configure(text="—")
        self.render_level()

        self.port_combo.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.connect_button.configure(
            text="Cancelar",
            fg_color=RED,
            hover_color=RED_HOVER,
            text_color=WHITE,
        )
        self.status_badge.configure(
            text="● CONECTANDO", text_color=GOLD
        )
        self.feedback_label.configure(
            text="Abriendo puerto…", text_color=MUTED
        )

        self.worker = SerialWorker(
            port, self.events, self.session
        )
        self.worker.start()

    def disconnect(self):
        self.connected = False
        self.enable_controls(False)
        self.connect_button.configure(
            text="Cerrando…", state="disabled"
        )
        self.status_badge.configure(
            text="● CERRANDO", text_color=GOLD
        )
        self.feedback_label.configure(
            text="Intentando detener ambos y cerrar el puerto…",
            text_color=MUTED,
        )
        if self.worker is not None:
            self.worker.stop()

    def handle_event(self, kind, data):
        if kind == "opening":
            self.log("SYS", f"{data} abierto a 9600 bps.")
            self.feedback_label.configure(
                text="Esperando inicio del Arduino…",
                text_color=GOLD,
            )

        elif kind == "ready":
            if (
                self.worker is None
                or self.worker.stop_event.is_set()
                or self.closing
            ):
                return

            self.connected = True
            self.enable_controls(True)
            self.connect_button.configure(
                text="Desconectar",
                fg_color=RED,
                hover_color=RED_HOVER,
                text_color=WHITE,
            )
            self.status_badge.configure(
                text="● CONECTADO", text_color=GREEN
            )
            self.log(
                "SYS",
                f"Enlace activo: {data}. Ambos motores inician en 0.",
            )

        elif kind == "sent":
            target, level = data
            self.sent_label.configure(text=f"{target}:{level}")
            self.feedback_label.configure(
                text=f"{target}:{level} enviado; esperando respuesta.",
                text_color=GOLD,
            )
            self.log(
                "TX",
                f"{target}:{level} ({TARGET_LABELS[target]})",
            )

        elif kind == "rx":
            self.log("RX", data)
            match = ACK_PATTERN.fullmatch(data.strip())

            if match:
                target = match.group(1).upper()
                level = int(match.group(2))

                for motor in target_motors(target):
                    self.reported_levels[motor] = level

                self.render_level()

                matches = all(
                    self.reported_levels[motor]
                    == self.levels[motor]
                    for motor in target_motors(self.target)
                )
                self.feedback_label.configure(
                    text=f"Arduino informó {target}:{level}.",
                    text_color=GREEN if matches else GOLD,
                )

        elif kind == "error":
            self.connected = False
            self.enable_controls(False)
            self.log("ERROR", data)
            self.status_badge.configure(
                text="● ERROR SERIAL", text_color=RED
            )
            self.feedback_label.configure(
                text="Comunicación perdida. Estado físico desconocido.",
                text_color=RED,
            )

        elif kind == "closed":
            self.connected = False
            self.worker = None
            self.enable_controls(False)
            self.port_combo.configure(state="readonly")
            self.refresh_button.configure(state="normal")
            self.connect_button.configure(
                text="Conectar",
                state="normal",
                fg_color=GOLD,
                hover_color=GOLD_HOVER,
                text_color=BG,
            )
            self.status_badge.configure(
                text="● SIN CONEXIÓN", text_color=MUTED
            )
            self.feedback_label.configure(
                text="Sin enlace. Estado físico desconocido.",
                text_color=MUTED,
            )

            message = (
                "Puerto cerrado; B:0 escrito antes del cierre, "
                "sin confirmar recepción."
                if data
                else "Puerto cerrado. No se pudo escribir B:0."
            )
            self.log("SYS", message)
            self.refresh_ports()

    def poll_events(self):
        """Actualiza los widgets únicamente desde el hilo Tk."""
        for _ in range(100):
            try:
                session, kind, data = self.events.get_nowait()
            except queue.Empty:
                break

            if kind in ("ports", "ports_error"):
                self.scanning = False

                if kind == "ports_error":
                    self.log(
                        "ERROR", f"Detección de puertos: {data}"
                    )

                elif self.worker is None:
                    selected_device = self.port_map.get(
                        self.port_combo.get()
                    )
                    self.port_map = dict(data)
                    values = (
                        list(self.port_map)
                        or ["Sin puertos disponibles"]
                    )
                    self.port_combo.configure(values=values)

                    selection = next(
                        (
                            label
                            for label, device in data
                            if device == selected_device
                        ),
                        values[0],
                    )
                    self.port_combo.set(selection)

            elif session == self.session:
                self.handle_event(kind, data)

        if self.closing and self.worker is None:
            self.destroy()
            return

        self.after(40, self.poll_events)

    def on_close(self):
        if self.closing:
            return
        self.closing = True

        if self.worker is not None:
            self.disconnect()
        else:
            self.destroy()


if __name__ == "__main__":
    App().mainloop()