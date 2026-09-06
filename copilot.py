import json
import os
import re
import subprocess
import threading
import time
from textwrap import dedent
from typing import Optional, Tuple
from collections import deque
from pathlib import Path
from serial_protocol import frame_command, parse_ack


def calculate_heat_index(temp_c: float, hum: float) -> float:
    """Calculates human apparent temperature (Heat Index) in Celsius."""
    t_f = temp_c * 1.8 + 32.0
    hi_f = 0.5 * (t_f + 61.0 + ((t_f - 68.0) * 1.2) + (hum * 0.094))
    if hi_f >= 80.0:
        hi_f = (-42.379 + 2.04901523 * t_f + 10.14333127 * hum
                - 0.22475541 * t_f * hum - 0.00683783 * (t_f ** 2)
                - 0.05481717 * (hum ** 2) + 0.00122874 * (t_f ** 2) * hum
                + 0.00085282 * t_f * (hum ** 2) - 0.00000199 * (t_f ** 2) * (hum ** 2))
    return (hi_f - 32.0) / 1.8


import serial
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

load_dotenv()
console = Console()


def call_llm(messages: list, timeout: float = 35.0) -> Optional[str]:
    """
    Direct OpenAI-compatible call to OpenRouter with automatic retries,
    fallback to Ollama, and clean error suppression (prevents terminal noise).
    """
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3:free")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    if openrouter_key:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=openrouter_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=2
            )
            resp = client.chat.completions.create(model=model, messages=messages)
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception:
            pass

    # Fallback to local Ollama if running
    try:
        from openai import OpenAI
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1"
        ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
        client = OpenAI(api_key="ollama", base_url=ollama_url, timeout=12.0)
        resp = client.chat.completions.create(model=ollama_model, messages=messages)
        if resp.choices and resp.choices[0].message.content:
            return resp.choices[0].message.content.strip()
    except Exception:
        pass

    return None


BASE_DIR = Path(__file__).resolve().parent
HARDWARE_FILE = str(BASE_DIR / "hardware_context.json")

# Permanent Environmental AI Agent Firmware (Protected & Immutable)
MAIN_SKETCH_DIR = str(BASE_DIR / "tmp")
MAIN_SKETCH_FILE = os.path.join(MAIN_SKETCH_DIR, "tmp.ino")
SKETCH_DIR = MAIN_SKETCH_DIR
SKETCH_FILE = MAIN_SKETCH_FILE

# Isolated Temporary Experiment Sandbox
SANDBOX_DIR = str(BASE_DIR / "sandbox")
SANDBOX_FILE = os.path.join(SANDBOX_DIR, "sandbox.ino")

# Global state tracking: "MAIN" (telemetry agent active) or "SANDBOX" (temporary experiment active)
ACTIVE_FIRMWARE_MODE = "MAIN"


def detect_arduino_port() -> Tuple[str, str]:
    """
    Detects the connected Arduino board and port using arduino-cli.
    Falls back to .env settings if detection fails.
    """
    default_port = os.getenv("ARDUINO_PORT", "/dev/cu.usbmodem1101")
    default_fqbn = os.getenv("ARDUINO_BOARD", "arduino:avr:uno")

    try:
        res = subprocess.run(["arduino-cli", "board", "list", "--format", "json"],
                             capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        detected_ports = data.get("detected_ports", [])
        for p in detected_ports:
            boards = p.get("matching_boards", [])
            if boards:
                port = p.get("port", {}).get("address", default_port)
                fqbn = boards[0].get("fqbn", default_fqbn)
                return port, fqbn
    except Exception:
        pass

    return default_port, default_fqbn


def load_hardware_context() -> dict:
    """Loads hardware components and pin configuration."""
    if os.path.exists(HARDWARE_FILE):
        try:
            with open(HARDWARE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"board": "Arduino UNO", "components": []}


def save_hardware_context(context: dict) -> None:
    with open(HARDWARE_FILE, "w") as f:
        json.dump(context, f, indent=2)


def format_hardware_prompt(context: dict) -> str:
    lines = [f"Target Board: {context.get('board', 'Arduino UNO')}"]
    lines.append("Available Connected Components & Pinout:")
    for comp in context.get("components", []):
        name = comp.get("name", "")
        pins = comp.get("pins", "")
        notes = comp.get("notes", "")
        entry = f"- {name}: {pins}"
        if notes:
            entry += f" ({notes})"
        lines.append(entry)
    return "\n".join(lines)


class SerialBridge:
    """
    Manages live bidirectional USB communication with the Arduino.
    Listens for JSON telemetry and transmits real-time LCD/actuator commands.
    """
    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self.serial_conn: Optional[serial.Serial] = None
        self.is_running = False
        self.reader_thread: Optional[threading.Thread] = None
        self.latest_telemetry: dict = {
            "temp": 26.0,
            "hum": 50.0,
            "ldr": 500,
            "light": 0,
            "ac_led": 0,
            "alert_led": 0,
            "auto_light": 1
        }
        self.lock = threading.Lock()
        self.command_counter = 0
        self.last_ack = None
        self.pending_commands = {}

    def connect(self) -> bool:
        if not os.path.exists(self.port):
            return False
        try:
            self.serial_conn = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(1.5)  # Allow Arduino reset after serial open
            self.is_running = True
            self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.reader_thread.start()
            # Trigger AI startup sound on the Arduino buzzer
            time.sleep(0.3)
            self.send_command("AI_WAKE")
            return True
        except Exception:
            self.serial_conn = None
            return False

    def disconnect(self):
        self.is_running = False
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass
            self.serial_conn = None

    def _read_loop(self):
        while self.is_running and self.serial_conn:
            try:
                if self.serial_conn.in_waiting > 0:
                    raw_line = self.serial_conn.readline().decode("utf-8", errors="ignore").strip()
                    ack_id = parse_ack(raw_line)
                    if ack_id is not None:
                        with self.lock:
                            self.last_ack = ack_id
                            self.pending_commands.pop(ack_id, None)
                    elif raw_line.startswith("NACK:"):
                        with self.lock:
                            self.last_ack = raw_line
                    elif raw_line.startswith("{") and raw_line.endswith("}"):
                        try:
                            data = json.loads(raw_line)
                            with self.lock:
                                self.latest_telemetry.update(data)
                        except json.JSONDecodeError:
                            pass
                else:
                    time.sleep(0.05)
            except Exception:
                break

    def get_telemetry(self) -> dict:
        with self.lock:
            return dict(self.latest_telemetry)

    def send_command(self, cmd: str) -> bool:
        if not self.serial_conn or not self.is_running:
            return False
        try:
            with self.lock:
                self.command_counter += 1
                command_id = str(self.command_counter)
                self.pending_commands[command_id] = cmd.strip()
            full_cmd = frame_command(command_id, cmd).encode("utf-8")
            self.serial_conn.write(full_cmd)
            self.serial_conn.flush()
            return True
        except Exception:
            return False

    def set_lcd(self, line0: str, line1: str, duration_ms: int = 12000) -> bool:
        clean0 = line0.replace("|", "/").strip()[:16]
        clean1 = line1.replace("|", "/").strip()[:16]
        return self.send_command(f"SET_LCD:{clean0}|{clean1}|{duration_ms}")

    def set_light(self, on: bool) -> bool:
        return self.send_command(f"SET_LIGHT:{'1' if on else '0'}")

    def set_ac_led(self, on: bool) -> bool:
        return self.send_command(f"SET_AC_LED:{'1' if on else '0'}")

    def set_alert_led(self, on: bool) -> bool:
        return self.send_command(f"SET_ALERT:{'1' if on else '0'}")

    def set_auto_light(self, enabled: bool) -> bool:
        return self.send_command(f"AUTO_LIGHT:{'1' if enabled else '0'}")

    def beep(self, freq: int = 2000, duration_ms: int = 200) -> bool:
        return self.send_command(f"BEEP:{freq}|{duration_ms}")

    def play_startup_sound(self) -> bool:
        return self.send_command("AI_WAKE")

    def show_full_message(self, message: str) -> bool:
        """
        Transmits full message to the Arduino. The Arduino's built-in paging engine
        word-wraps the text into 16-character lines and displays it page-by-page on the 16x2 LCD.
        """
        clean_msg = message.replace("\n", " ").replace("\r", " ").strip()
        return self.send_command(f"MSG:{clean_msg}")

    def clear_lcd(self) -> bool:
        """Immediately stops message paging and restores the live telemetry HUD."""
        return self.send_command("CLEAR_LCD")


def compute_lcd_pages(text: str) -> list:
    """
    Word-wraps full text into pairs of lines for 16x2 LCD pagination.
    Returns: list of (line0, line1) tuples.
    """
    import textwrap
    clean = text.replace("\n", " ").replace("\r", " ").strip()
    lines = textwrap.wrap(clean, width=16)
    if not lines:
        return [("                ", "                ")]
    pages = []
    for i in range(0, len(lines), 2):
        l0 = lines[i]
        l1 = lines[i+1] if i+1 < len(lines) else ""
        pages.append((l0[:16], l1[:16]))
    return pages


# Global bridge singleton
bridge = SerialBridge(detect_arduino_port()[0])


class AutonomousObserver:
    """
    Autonomous AI reasoning engine.
    Periodically ingests live sensor telemetry, tracks trends & heat index,
    reasons with the LLM when environmental transitions occur, and updates the LCD, indicators, and buzzer.
    Only sends text to the physical LCD ONCE per transition to prevent repeating messages.
    """
    def __init__(self, bridge_inst: SerialBridge, interval_sec: int = 20):
        self.bridge = bridge_inst
        self.interval_sec = interval_sec
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.latest_suggestion = "AI Brain Online. Monitoring temperature, humidity, and ambient light."
        self.latest_lcd = ("AI Brain Online ", "Sensors Active  ")
        self.latest_lcd_pages = [("AI Brain Online ", "Sensors Active  ")]
        self.last_ac_state = False
        self.last_sent_suggestion = None
        self.last_state_signature = None
        self.history = deque(maxlen=8)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False

    def _loop(self):
        time.sleep(3)
        while self.is_running:
            try:
                self.evaluate_environment(force_display=False)
            except Exception:
                pass
            time.sleep(self.interval_sec)

    def evaluate_environment(self, force_display: bool = False) -> dict:
        t = self.bridge.get_telemetry()
        temp = t.get("temp", 26.0)
        hum = t.get("hum", 50.0)
        ldr = t.get("ldr", 500)
        light_on = (t.get("light", 0) == 1)
        ac_led_on = (t.get("ac_led", 0) == 1)

        heat_index = calculate_heat_index(temp, hum)
        ac_rec = 1 if (heat_index >= 28.0 or (temp >= 26.5 and hum >= 65.0)) else 0
        is_dark = (ldr < 50)

        # Discrete signature: detects true physical transitions (comfort shift, darkness shift, or light toggling)
        state_signature = (ac_rec, is_dark, light_on)

        # If conditions have not changed and user didn't explicitly request an evaluation, stay silent!
        if not force_display and self.last_state_signature is not None and state_signature == self.last_state_signature:
            return {
                "suggestion": self.latest_suggestion,
                "lcd_pages": self.latest_lcd_pages,
                "ac_rec": ac_rec,
                "heat_index": heat_index
            }

        # Track trends
        trend_str = "Stable"
        if len(self.history) >= 3:
            temp_diff = temp - self.history[0]["temp"]
            if temp_diff >= 0.5:
                trend_str = f"Warming (+{temp_diff:.1f}°C)"
            elif temp_diff <= -0.5:
                trend_str = f"Cooling ({temp_diff:.1f}°C)"

        self.history.append({"temp": temp, "hum": hum, "ldr": ldr, "time": time.time()})

        system_prompt = dedent(f"""
        You are an Autonomous Edge AI Environmental Agent managing a physical room in real time.
        Live Telemetry:
        - Temperature: {temp:.1f}°C | Humidity: {hum:.1f}%
        - Heat Index ('Feels Like'): {heat_index:.1f}°C
        - Climate Trend: {trend_str}
        - Ambient Light (LDR A0): {ldr} (Threshold: < 50 is Pitch Dark, room is {'PITCH DARK (Auto Light ON)' if ldr < 50 else 'ILLUMINATED'})
        - Room Light (Pin 4): {'ON' if light_on else 'OFF'}
        - AC Status LED (Pin 6): {'ON' if ac_led_on else 'OFF'}

        Comfort & Ergonomic Rules:
        - Darkness: LDR < 50 is dark. Light on Pin 4 automatically turns ON.
        - Cooling: Recommend AC ON if Heat Index >= 28.0°C or (Temp >= 26.5°C and Humidity >= 65.0%).
        - Ventilation: If Humidity >= 70%, warn about high humidity.

        Instructions:
        1. Formulate a short, natural, actionable message for the occupant (max 70-80 chars so it fits cleanly across 2-3 LCD pages).
        2. Set AC: 1 if AC recommended, 0 if comfortable.
        3. Set CHIME: 1 if conditions just became uncomfortable, else 0.

        Output format:
        SUGGESTION: <Your concise recommendation>
        AC: <0 or 1>
        CHIME: <0 or 1>
        """)

        try:
            text = call_llm(messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Analyze current room conditions and advise."}
            ], timeout=25.0)

            chime_rec = 0
            if text:
                suggestion = f"Temp {temp:.1f}C (Feels {heat_index:.1f}C), Hum {hum:.0f}%. All good."
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("SUGGESTION:"):
                        suggestion = line[11:].strip()
                    elif line.startswith("AC:"):
                        val = line[3:].strip()
                        if val in ["0", "1"]:
                            ac_rec = int(val)
                    elif line.startswith("CHIME:"):
                        val = line[6:].strip()
                        if val in ["0", "1"]:
                            chime_rec = int(val)
            else:
                # Intelligent local fallback during temporary network pauses
                if ac_rec == 1:
                    suggestion = f"Warm conditions: {temp:.1f}C (Feels like {heat_index:.1f}C). Turn ON AC."
                elif ldr < 50:
                    suggestion = f"Room is pitch dark (LDR {ldr}). Light turned ON."
                else:
                    suggestion = f"Room climate is comfortable at {temp:.1f}C and {hum:.0f}% humidity."

            self.latest_suggestion = suggestion
            self.latest_lcd_pages = compute_lcd_pages(suggestion)
            self.latest_lcd = self.latest_lcd_pages[0] if self.latest_lcd_pages else ("AI: Status OK   ", "                ")

            # Send to LCD ONCE only if the suggestion is new or force_display requested
            if (suggestion != self.last_sent_suggestion) or force_display:
                self.bridge.show_full_message(suggestion)
                self.last_sent_suggestion = suggestion

            # The Arduino firmware remains the authority for physical AC
            # status. The AI may recommend cooling, but must not override the
            # local sensor-based safety heuristic.

            # Chime on AC status transition or urgent condition
            if (ac_rec == 1 and not self.last_ac_state) or (chime_rec == 1):
                self.bridge.beep(2200, 180)

            self.last_ac_state = (ac_rec == 1)
            self.last_state_signature = state_signature

            return {
                "suggestion": suggestion,
                "lcd_pages": self.latest_lcd_pages,
                "ac_rec": ac_rec,
                "heat_index": heat_index
            }
        except Exception:
            return {}



# Global autonomous observer
observer = AutonomousObserver(bridge, interval_sec=15)



def clean_sketch_code(raw_response: str) -> str:
    """Extracts plain C++ code from markdown code fences or raw text."""
    pattern = r"```(?:arduino|cpp|c\+\+|c)?\s*\n?(.*?)```"
    match = re.search(pattern, raw_response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw_response.strip()


def compile_sketch(sketch_dir_or_fqbn: str = MAIN_SKETCH_DIR, fqbn: Optional[str] = None) -> Tuple[bool, str]:
    if fqbn is None:
        sketch_dir = MAIN_SKETCH_DIR
        fqbn_val = sketch_dir_or_fqbn
    else:
        sketch_dir = sketch_dir_or_fqbn
        fqbn_val = fqbn

    os.makedirs(sketch_dir, exist_ok=True)
    compile_cmd = ["arduino-cli", "compile", "--fqbn", fqbn_val, sketch_dir]
    res = subprocess.run(compile_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        err = res.stderr or res.stdout
        return False, err.strip()
    return True, "Compilation successful."


def upload_sketch(port: str, fqbn: str, sketch_dir: str = MAIN_SKETCH_DIR) -> Tuple[bool, str]:
    if not os.path.exists(port):
        return False, f"Port '{port}' not found. Please connect your Arduino."
    upload_cmd = ["arduino-cli", "upload", "--port", port, "--fqbn", fqbn, sketch_dir]
    res = subprocess.run(upload_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        err = res.stderr or res.stdout
        return False, err.strip()
    return True, f"Code successfully uploaded to Arduino on {port}."


def restore_main_firmware(port: str, fqbn: str) -> Tuple[bool, str]:
    """
    Safely compiles and flashes the permanent Environmental AI Agent firmware from tmp/tmp.ino.
    Resets active mode to MAIN and resumes live sensor monitoring.
    """
    global ACTIVE_FIRMWARE_MODE
    observer.stop()
    bridge.disconnect()
    time.sleep(0.8)

    compile_ok, compile_err = compile_sketch(MAIN_SKETCH_DIR, fqbn)
    if not compile_ok:
        return False, f"Failed to compile main firmware: {compile_err}"

    upload_ok, upload_msg = upload_sketch(port, fqbn, sketch_dir=MAIN_SKETCH_DIR)
    if upload_ok:
        ACTIVE_FIRMWARE_MODE = "MAIN"
        time.sleep(1.5)
        bridge.port = port
        bridge.connect()
        observer.start()
        return True, "Main Environmental AI Agent restored & live telemetry active!"
    return False, upload_msg


def flash_sandbox_firmware(port: str, fqbn: str) -> Tuple[bool, str]:
    """
    Safely compiles and flashes the isolated sandbox experiment firmware from sandbox/sandbox.ino.
    Sets ACTIVE_FIRMWARE_MODE to SANDBOX and suspends the telemetry observer.
    """
    global ACTIVE_FIRMWARE_MODE
    observer.stop()
    bridge.disconnect()
    time.sleep(0.8)

    compile_ok, compile_err = compile_sketch(SANDBOX_DIR, fqbn)
    if not compile_ok:
        return False, f"Failed to compile sandbox firmware: {compile_err}"

    upload_ok, upload_msg = upload_sketch(port, fqbn, sketch_dir=SANDBOX_DIR)
    if upload_ok:
        ACTIVE_FIRMWARE_MODE = "SANDBOX"
        return True, "Sandbox experiment flashed & running on Arduino!"
    return False, upload_msg


def generate_arduino_sketch(prompt: str, hardware_ctx: dict, error_feedback: Optional[str] = None, previous_code: Optional[str] = None) -> str:
    hw_info = format_hardware_prompt(hardware_ctx)
    if error_feedback and previous_code:
        system_prompt = dedent(f"""
        You are an expert Arduino C++ developer writing a temporary experiment sketch for an isolated hardware sandbox.
        The previous sketch failed to compile with this error:
        ---
        {error_feedback}
        ---
        Previous code:
        ```cpp
        {previous_code}
        ```
        Fix the compilation error while strictly fulfilling the user's request: "{prompt}"
        Hardware reference:
        {hw_info}
        Provide ONLY the corrected complete Arduino C++ code enclosed in ```cpp ... ```.
        """)
    else:
        system_prompt = dedent(f"""
        You are an expert embedded Arduino developer.
        Write a complete, self-contained, working Arduino C++ sketch (.ino) to run in a temporary test sandbox for this user experiment:
        "{prompt}"

        {hw_info}

        IMPORTANT INSTRUCTIONS:
        1. This sketch runs in an isolated sandbox and will NOT modify the main system firmware.
        2. Make the requested components work directly and simply as requested by the user.
        3. Reference known pinouts:
           - 16x2 I2C LCD: 0x27 (A4/A5, <Wire.h>, <LiquidCrystal_I2C.h>)
           - DHT11: Digital Pin 7 (<DHT.h>, DHT dht(7, DHT11))
           - Room Light LED: Digital Pin 4
           - Alert LED: Digital Pin 5
           - AC Status LED: Digital Pin 6
           - Buzzer: Digital Pin 8 (tone(8, freq) or digitalWrite)
           - LDR Sensor: Analog Pin A0
        4. Include complete setup() and loop() functions.
        5. Output ONLY the raw C++ code enclosed in ```cpp ... ``` blocks without conversational text.
        """)

    user_message = f"User Request: {prompt}"
    raw_code = call_llm(messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ], timeout=45.0)
    if not raw_code:
        raw_code = previous_code or ""
    return clean_sketch_code(raw_code)


def classify_user_intent(prompt: str) -> str:
    """
    Determines if user prompt is a real-time question/display action (QUERY)
    or an explicit firmware modification that requires recompilation (CODE).
    Default is QUERY to protect active firmware from accidental overwrites.
    """
    p = prompt.strip().lower()

    # Explicit code & sandbox prefixes
    if (p == "sandbox" or p.startswith("code:") or p.startswith("sketch:") or p.startswith("reprogram:") or
        p.startswith("sandbox:") or p.startswith("sandbox ") or
        p.startswith("experiment:") or p.startswith("experiment ")):
        return "CODE"
    if p.startswith("query:") or p.startswith("ask:") or p.startswith("say:"):
        return "QUERY"

    # Explicit code modification and sandbox experiment requests
    explicit_code_phrases = [
        "rewrite code", "change code", "modify code", "reprogram", "write code",
        "update code", "edit code", "new sketch", "generate sketch", "rewrite sketch",
        "flash new code", "compile new", "upload sandbox", "run sandbox", "flash sandbox",
        "blink light", "blink led", "blink the light", "blink the led", "flash led",
        "pulse light", "strobe light", "cycle led", "morse code", "play tone", "play melody"
    ]
    for phrase in explicit_code_phrases:
        if phrase in p:
            return "CODE"

    # All conversational questions, sensor inquiries, and direct actuations are QUERY
    return "QUERY"


def answer_user_and_display_lcd(user_prompt: str, telemetry: dict) -> Tuple[str, str, list]:
    """
    Evaluates current telemetry and answers the user in terminal and on 16x2 LCD.
    Returns: (terminal_answer, lcd_message, pages)
    """
    temp = telemetry.get("temp", 26.0)
    hum = telemetry.get("hum", 50.0)
    ldr = telemetry.get("ldr", 500)
    light_on = (telemetry.get("light", 0) == 1)
    ac_on = (telemetry.get("ac_led", 0) == 1)
    heat_index = calculate_heat_index(temp, hum)

    system_prompt = dedent(f"""
    You are an intelligent Edge-AI Arduino Copilot.
    Live Environmental Telemetry:
    - Temperature: {temp:.1f}°C | Humidity: {hum:.1f}%
    - Perceived Heat Index (Feels Like): {heat_index:.1f}°C
    - Ambient Light (LDR A0): {ldr} (Threshold: < 50 is Pitch Dark, room is {'PITCH DARK (Light ON)' if ldr < 50 else 'LIT (Light OFF)'})
    - AC Status: {'Recommended ON (Hot/Humid)' if ac_on else 'Comfortable, AC not needed'}

    User Prompt: "{user_prompt}"

    Instructions:
    1. Provide a comprehensive, natural answer for the terminal.
    2. Provide an LCD_MESSAGE: The complete, natural response (1-2 sentences, max 100 characters) to be displayed on the physical 16x2 LCD screen. The Arduino will automatically word-wrap and page through this message so the user can read the entire reply!

    Output format:
    TERMINAL: <Your full answer to user>
    LCD_MESSAGE: <The complete readable message for the LCD screen>
    """)

    text = call_llm(messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ], timeout=35.0)

    terminal_ans = ""
    lcd_msg = ""

    if text:
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("TERMINAL:"):
                terminal_ans = line[9:].strip()
            elif line.startswith("LCD_MESSAGE:"):
                lcd_msg = line[12:].strip()
            elif line.startswith("LCD_MSG:"):
                lcd_msg = line[8:].strip()
            elif line.startswith("LCD0:") and not lcd_msg:
                lcd_msg = line[5:].strip()

        if not lcd_msg:
            lcd_msg = terminal_ans[:100]
    else:
        # Resilient local fallback during temporary network pauses
        terminal_ans = (
            f"Live Telemetry Assessment: Temperature is {temp:.1f}°C (Feels like {heat_index:.1f}°C), "
            f"Humidity is {hum:.1f}%, Light is {ldr}. "
            f"{'AC is recommended to relieve thermal discomfort.' if ac_on else 'Indoor climate is comfortable, AC not needed.'} "
            f"{'Room light is active due to darkness.' if light_on else 'Room is adequately illuminated.'}"
        )
        lcd_msg = f"T:{temp:.1f}C Feels:{heat_index:.1f}C. {'Turn ON AC!' if ac_on else 'Comfortable!'}"

    pages = compute_lcd_pages(lcd_msg)
    return terminal_ans, lcd_msg, pages


def process_user_command(command: str) -> bool:
    """
    Unified entrypoint:
    - Queries / questions / quick actions are answered instantly via Serial & LCD with full pagination.
    - Code changes are recompiled and uploaded.
    """
    port, fqbn = detect_arduino_port()
    hw_ctx = load_hardware_context()
    intent = classify_user_intent(command)

    if intent == "QUERY":
        console.print(f"\n[bold cyan]🤖 Answering Question & Paging LCD:[/bold cyan] {command}")
        
        if not bridge.serial_conn or not bridge.is_running:
            bridge.port = port
            bridge.connect()

        telemetry = bridge.get_telemetry()
        terminal_answer, lcd_msg, pages = answer_user_and_display_lcd(command, telemetry)

        # Direct hardware toggles
        cmd_lower = command.lower()
        if "turn on light" in cmd_lower or "light on" in cmd_lower:
            bridge.set_light(True)
        elif "turn off light" in cmd_lower or "light off" in cmd_lower:
            bridge.set_light(False)
        elif "beep" in cmd_lower or "buzzer" in cmd_lower:
            bridge.beep(2000, 250)

        # Transmit full message to physical Arduino LCD paging engine!
        bridge.show_full_message(lcd_msg)

        # Display terminal output + Multi-Page LCD Carousel Preview
        console.print(f"\n[bold green]AI Answer:[/bold green] {terminal_answer}\n")
        
        preview_lines = []
        for idx, (l0, l1) in enumerate(pages):
            preview_lines.append(f"[bold cyan]Page {idx+1}/{len(pages)}[/bold cyan]")
            preview_lines.append(f"┌────────────────┐\n│{l0:<16}│\n│{l1:<16}│\n└────────────────┘")
        
        full_preview = "\n".join(preview_lines)
        console.print(Panel(full_preview, title="📟 Live LCD 16x2 Paging Sequence (Playing on Physical Screen)", border_style="cyan"))
        return True

    else:
        # Re-programming mode -> Routed to isolated SANDBOX!
        global ACTIVE_FIRMWARE_MODE

        # Clean prefix from prompt if present
        clean_prompt = command.strip()
        for prefix in ["code:", "sketch:", "reprogram:", "sandbox:", "sandbox ", "experiment:", "experiment "]:
            if clean_prompt.lower().startswith(prefix):
                clean_prompt = clean_prompt[len(prefix):].strip()
                break

        # If user simply requested to upload/run existing sandbox
        if clean_prompt.lower() in ["", "sandbox", "run sandbox", "flash sandbox", "upload sandbox"]:
            ok, msg = flash_sandbox_firmware(port, fqbn)
            if ok:
                console.print(f"[bold green]🚀 {msg}[/bold green] ({port})")
                console.print(f"[bold cyan]ℹ️  Type [bold yellow]'restore'[/bold yellow] or [bold yellow]'main'[/bold yellow] whenever you want to switch back to your Environmental AI Agent.[/bold cyan]\n")
                return True
            else:
                console.print(f"[bold red]❌ Sandbox upload error:[/bold red] {msg}")
                return False

        console.print(Panel(
            f"[bold yellow]🧪 Running in Isolated Experiment Sandbox[/bold yellow]\n\n"
            f"[cyan]User Request:[/cyan] {clean_prompt}\n"
            f"[green]Target File:[/green] [bold]{SANDBOX_FILE}[/bold]\n\n"
            f"[dim]🛡️ Permanent Environmental AI Agent source code in [bold]tmp/tmp.ino[/bold] is SAFE & UNTOUCHED.[/dim]\n"
            f"[yellow]💡 Tip: Type [bold cyan]'restore'[/bold cyan] or [bold cyan]'main'[/bold cyan] anytime to return to your Environmental Agent.[/yellow]",
            title="🔬 Temporary Hardware Sandbox Execution",
            border_style="yellow"
        ))

        observer.stop()
        bridge.disconnect()
        time.sleep(0.8)

        sketch_code = ""
        compile_ok = False
        max_retries = 2
        err_msg = ""

        for attempt in range(max_retries + 1):
            if attempt == 0:
                with console.status("[bold green]Generating sandbox Arduino C++ code...", spinner="dots"):
                    sketch_code = generate_arduino_sketch(clean_prompt, hw_ctx)
            else:
                with console.status(f"[bold yellow]Auto-healing compiler error (attempt {attempt}/{max_retries})...", spinner="dots"):
                    sketch_code = generate_arduino_sketch(clean_prompt, hw_ctx, error_feedback=err_msg, previous_code=sketch_code)

            os.makedirs(SANDBOX_DIR, exist_ok=True)
            with open(SANDBOX_FILE, "w", encoding="utf-8") as f:
                f.write(sketch_code)

            with console.status("[bold blue]Compiling sandbox sketch with arduino-cli...", spinner="dots"):
                compile_ok, err_msg = compile_sketch(SANDBOX_DIR, fqbn)

            if compile_ok:
                break

        if not compile_ok:
            console.print(f"[bold red]❌ Sandbox compilation failed:[/bold red] {err_msg}")
            return False

        console.print("\n[bold green]✅ Sandbox code successfully compiled![/bold green]")
        with console.status("[bold magenta]Uploading sandbox sketch to Arduino board...", spinner="dots"):
            upload_ok, upload_msg = upload_sketch(port, fqbn, sketch_dir=SANDBOX_DIR)

        if upload_ok:
            ACTIVE_FIRMWARE_MODE = "SANDBOX"
            console.print(f"[bold green]🚀 Sandbox Experiment Flashed & Running on Arduino![/bold green] ({port})")
            console.print(f"[bold cyan]ℹ️  Type [bold yellow]'restore'[/bold yellow] or [bold yellow]'main'[/bold yellow] whenever you want to switch back to your Environmental AI Agent.[/bold cyan]\n")
            return True
        else:
            console.print(f"[yellow]⚠️  Upload notice:[/yellow] {upload_msg}")
            return False
