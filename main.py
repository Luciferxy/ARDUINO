import sys
import os
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

from copilot import (
    process_user_command,
    detect_arduino_port,
    load_hardware_context,
    save_hardware_context,
    upload_sketch,
    restore_main_firmware,
    flash_sandbox_firmware,
    bridge,
    observer,
    MAIN_SKETCH_FILE,
    SANDBOX_FILE,
    SKETCH_FILE
)
import copilot
console = Console()


def print_banner(port: str, fqbn: str, hw_ctx: dict):
    model_name = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3:free")
    provider_name = "OpenRouter" if os.getenv("OPENROUTER_API_KEY") else "Ollama"

    console.print(Panel.fit(
        f"[bold cyan]⚡ Autonomous Environmental AI Agent & Live Edge Copilot ⚡[/bold cyan]\n"
        f"[dim]Continuous DHT11 + LDR sensing • Real-time AI climate advice • Live LCD dialogue[/dim]\n\n"
        f"[bold]Board:[/bold] [green]{fqbn}[/green] on [yellow]{port}[/yellow]\n"
        f"[bold]AI Brain:[/bold] [magenta]{provider_name}/{model_name}[/magenta]",
        border_style="cyan"
    ))


def show_hardware(hw_ctx: dict):
    table = Table(title="Connected Hardware Configuration", border_style="blue")
    table.add_column("Component", style="cyan", no_wrap=True)
    table.add_column("Pins / Connections", style="magenta")
    table.add_column("Notes / Function", style="dim")

    for comp in hw_ctx.get("components", []):
        table.add_row(comp.get("name", ""), comp.get("pins", ""), comp.get("notes", ""))

    console.print(table)


def show_live_status():
    t = bridge.get_telemetry()
    table = Table(title="Live Sensor Telemetry & Hardware Status", border_style="green")
    table.add_column("Sensor / Indicator", style="cyan")
    table.add_column("Current Reading / State", style="bold yellow")

    temp = t.get("temp", 25.0)
    hum = t.get("hum", 50.0)
    ldr = t.get("ldr", 500)
    light = t.get("light", 0)
    ac_led = t.get("ac_led", 0)
    auto_light = t.get("auto_light", 1)

    from copilot import calculate_heat_index
    heat_idx = calculate_heat_index(temp, hum)

    table.add_row("Temperature (DHT11)", f"{temp:.1f} °C")
    table.add_row("Feels Like (Heat Index)", f"{heat_idx:.1f} °C")
    table.add_row("Humidity (DHT11)", f"{hum:.1f} %")
    table.add_row("Ambient Light (LDR A0)", f"{ldr} ({'PITCH DARK 🌑 (<50)' if ldr < 50 else 'ILLUMINATED ☀️'})")
    table.add_row("Room Light (Pin 4)", "[green]ON 💡[/green]" if light == 1 else "[dim]OFF 🌑[/dim]")
    table.add_row("Auto-Lighting Mode", "[green]ENABLED[/green]" if auto_light == 1 else "[yellow]MANUAL OVERRIDE[/yellow]")
    table.add_row("AC Recommendation (Pin 6)", "[bold red]TURN ON AC! ❄️[/bold red]" if ac_led == 1 else "[bold green]Comfortable 😊[/bold green]")

    console.print(table)

    # Show latest autonomous AI suggestion
    if observer.latest_suggestion:
        console.print(Panel(
            f"[bold green]Latest AI Environmental Suggestion:[/bold green]\n{observer.latest_suggestion}\n\n"
            f"[cyan]Physical LCD Screen:[/cyan]\n"
            f"┌────────────────┐\n"
            f"│{observer.latest_lcd[0]:<16}│\n"
            f"│{observer.latest_lcd[1]:<16}│\n"
            f"└────────────────┘",
            title="🤖 Autonomous AI Decision",
            border_style="magenta"
        ))


def show_current_code(mode: str = "main"):
    target_file = SANDBOX_FILE if mode == "sandbox" else MAIN_SKETCH_FILE
    title = "Isolated Sandbox Firmware (sandbox/sandbox.ino)" if mode == "sandbox" else "Permanent System Firmware (tmp/tmp.ino)"
    border_color = "yellow" if mode == "sandbox" else "blue"

    if os.path.exists(target_file):
        with open(target_file, "r", encoding="utf-8") as f:
            code = f.read()
        from rich.syntax import Syntax
        console.print(Panel(Syntax(code, "cpp", theme="monokai", line_numbers=True), title=title, border_style=border_color))
        if mode == "sandbox":
            console.print("[dim]💡 Tip: Type [bold yellow]'sandbox'[/bold yellow] or [bold yellow]'upload sandbox'[/bold yellow] to flash this experiment to your Arduino.[/dim]")
    else:
        console.print(f"[yellow]No sketch file found at {target_file}.[/yellow]")


def interactive_session():
    port, fqbn = detect_arduino_port()
    hw_ctx = load_hardware_context()

    print_banner(port, fqbn, hw_ctx)

    # Connect serial bridge
    bridge.port = port
    if os.path.exists(port):
        with console.status("[bold blue]Connecting to Arduino live stream...", spinner="dots"):
            connected = bridge.connect()
        if connected:
            console.print(f"[bold green]Connected to Arduino on {port}![/bold green]")
            # Start background autonomous AI reasoning observer
            observer.start()
            console.print("[dim]⚡ Background Autonomous AI reasoning observer activated (evaluating every 15s)[/dim]")
        else:
            console.print(f"[yellow]Note: Port {port} available, but could not open serial stream yet.[/yellow]")
    else:
        console.print(f"[yellow]⚠️ Arduino not detected on USB. Plug it in and type 'upload' to flash.[/yellow]")

    console.print("\n[bold yellow]What you can do:[/bold yellow]")
    console.print("  • Ask questions: [italic]'Is the room too warm?', 'What is the humidity?', 'Should I turn on AC?'[/italic]")
    console.print("  • Direct actuation: [italic]'Turn on light', 'Turn off light', 'Beep', 'Say Good Morning on LCD'[/italic]")
    console.print("  • Sandbox experiments: [italic]'sandbox blink light twice'[/italic] or [italic]'sandbox'[/italic] [dim](runs in isolated sandbox)[/dim]")
    console.print("  • Commands: [bold]status[/bold] | [bold]suggest[/bold] | [bold yellow]sandbox[/bold yellow] | [bold green]restore[/bold green] [dim](main agent)[/dim] | [bold]code[/bold] [dim][main|sandbox][/dim] | [bold]clear[/bold] | [bold]upload[/bold] | [bold]exit[/bold]\n")

    while True:
        try:
            prompt_label = "[bold yellow]Arduino Sandbox[/bold yellow]" if copilot.ACTIVE_FIRMWARE_MODE == "SANDBOX" else "[bold cyan]Arduino Copilot[/bold cyan]"
            user_input = Prompt.ask(prompt_label).strip()
            if not user_input:
                continue

            cmd_lower = user_input.lower()
            if cmd_lower in ["exit", "quit", "q"]:
                observer.stop()
                bridge.disconnect()
                console.print("[green]Goodbye! Autonomous system halted cleanly.[/green]")
                break
            elif cmd_lower in ["restore", "main", "reset_main"]:
                port, fqbn = detect_arduino_port()
                with console.status("[bold green]Restoring permanent Environmental AI Agent to Arduino...", spinner="dots"):
                    ok, msg = restore_main_firmware(port, fqbn)
                if ok:
                    console.print(f"[bold green]🛡️  {msg}[/bold green] ({port})")
                else:
                    console.print(f"[bold red]❌ Restore error:[/bold red] {msg}")
                continue
            elif cmd_lower in ["sandbox", "run sandbox", "flash sandbox", "upload sandbox"]:
                port, fqbn = detect_arduino_port()
                with console.status("[bold yellow]Compiling and flashing Sandbox Experiment to Arduino...", spinner="dots"):
                    ok, msg = flash_sandbox_firmware(port, fqbn)
                if ok:
                    console.print(f"[bold green]🚀 {msg}[/bold green] ({port})")
                    console.print("[bold cyan]ℹ️  Type [bold yellow]'restore'[/bold yellow] or [bold yellow]'main'[/bold yellow] anytime to re-flash the permanent Environmental AI Agent.[/bold cyan]")
                else:
                    console.print(f"[bold red]❌ Sandbox flash error:[/bold red] {msg}")
                continue
            elif cmd_lower.startswith("sandbox "):
                experiment_prompt = user_input[8:].strip()
                process_user_command(f"code: {experiment_prompt}")
                continue
            elif cmd_lower in ["status", "telemetry", "data"]:
                show_live_status()
                continue
            elif cmd_lower in ["suggest", "ai", "eval"]:
                with console.status("[bold magenta]Autonomous AI evaluating current environment...", spinner="dots"):
                    res = observer.evaluate_environment(force_display=True)
                show_live_status()
                continue
            elif cmd_lower in ["clear", "cls", "hud", "stop"]:
                bridge.clear_lcd()
                console.print("[green]LCD message cleared. Restored live telemetry HUD.[/green]")
                continue
            elif cmd_lower in ["hw", "hardware"]:
                show_hardware(hw_ctx)
                continue
            elif cmd_lower.startswith("add "):
                new_comp_desc = user_input[4:].strip()
                hw_ctx["components"].append({"name": new_comp_desc, "pins": "User specified", "notes": ""})
                save_hardware_context(hw_ctx)
                console.print(f"[green]Added to hardware context:[/green] {new_comp_desc}")
                continue
            elif cmd_lower.startswith("code"):
                sub = cmd_lower.replace("code", "").strip()
                if "sandbox" in sub:
                    show_current_code(mode="sandbox")
                else:
                    show_current_code(mode="main")
                continue
            elif cmd_lower == "board":
                port, fqbn = detect_arduino_port()
                console.print(f"[green]Detected Board:[/green] {fqbn} on {port}")
                continue
            elif cmd_lower in ["upload", "upload main", "flash main"]:
                port, fqbn = detect_arduino_port()
                with console.status("[bold magenta]Flashing permanent Environmental Agent firmware...", spinner="dots"):
                    ok, msg = restore_main_firmware(port, fqbn)
                if ok:
                    console.print(f"[bold green]🚀 Main Environmental Agent running on Arduino![/bold green] ({port})")
                else:
                    console.print(f"[bold red]❌ Flash error:[/bold red] {msg}")
                continue

            # Process prompt
            process_user_command(user_input)
            console.print()

        except (KeyboardInterrupt, EOFError):
            observer.stop()
            bridge.disconnect()
            console.print("\n[green]Session ended.[/green]")
            break


def main():
    if len(sys.argv) > 1:
        command = " ".join(sys.argv[1:])
        process_user_command(command)
    else:
        interactive_session()


if __name__ == "__main__":
    main()
