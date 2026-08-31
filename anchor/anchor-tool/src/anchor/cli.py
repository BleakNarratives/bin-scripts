# [DNA_TAG]
# ORIGIN: Crostini-Chromebook (auto-detected)
# PILLAR: unknown
# DEPS: click, rich, rich, rich, anchor
# ROLE: Component of UNPACKED
# AUTHOR: Auto-tagged by Buffy (DNA Sweeper)
# SESSION: 2026-08-22 ShipWreckD OS Builder
# TIER: Recruit (5)
# AKA: cli, anchor-module
# [/DNA_TAG]

import click
from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme
from anchor.engine import PuterEngine
from anchor.config import Config

# Custom theme based on user requirements
anchor_theme = Theme({
    "info": "cyan",
    "debug": "blue",
    "prompt": "bold magenta",
    "ok": "bold green",
    "warn": "bold yellow",
    "err": "bold red",
    "heading": "bold bright_white",
    "code": "bright_white",
    "path": "bright_white",
    "flag": "bold bright_yellow",
})

console = Console(theme=anchor_theme)

@click.group()
@click.pass_context
def cli(ctx):
    """[bold bright_white]Anchor[/bold bright_white]: Professional CLI for your Puter toolchain."""
    ctx.obj = {
        'config': Config(),
        'engine': PuterEngine(),
        'console': console
    }

@cli.command()
@click.argument('prompt')
@click.pass_context
def chat(ctx, prompt):
    """Chat with Puter."""
    engine = ctx.obj['engine']
    console = ctx.obj['console']
    
    console.print(f"[cyan]INFO[/cyan] Sending: [bright_cyan]{prompt}[/bright_cyan]")
    
    with console.status("[bold green]Querying Puter..."):
        response = engine.chat(prompt)
    
    console.print(Panel(response, title="[bold bright_white]Anchor Response[/bold bright_white]", border_style="green"))

@cli.command()
@click.pass_context
def models(ctx):
    """List available Puter models."""
    engine = ctx.obj['engine']
    models = engine.list_models()
    console.print(f"[bold cyan]Available Models:[/bold cyan] {models}")

def main():
    cli()
